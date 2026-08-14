"""Behavior contracts for the dark governed effect executor."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import pytest

import services.agent_runtime.executor as executor_module
from kernel.types import (
    Tool,
    ToolApproval,
    ToolConcurrency,
    ToolContext,
    ToolEffect,
    ToolExecutionError,
    ToolRetryPolicy,
    ToolSpec,
)
from services.agent_runtime.ledger import AgentRuntimeLedger, ConcurrencyBusyError
from services.agent_runtime.policy import (
    ApprovalGrant,
    PolicyGate,
    RuntimePrincipal,
    ToolPolicyRequest,
    canonical_args_digest,
)


class _RecordingTool(Tool):
    def __init__(self, spec: ToolSpec) -> None:
        self._spec = spec
        self.executed = False
        self.context: ToolContext | None = None

    @property
    def name(self) -> str:
        return self._spec.name

    @property
    def description(self) -> str:
        return self._spec.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._spec.input_schema

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.executed = True
        self.context = ctx
        return "executed"


class _FailingTool(_RecordingTool):
    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.executed = True
        self.context = ctx
        raise RuntimeError("provider-secret-detail")


class _PreDispatchFailingTool(_RecordingTool):
    def __init__(self, spec: ToolSpec, *, external_effect_started: bool) -> None:
        super().__init__(spec)
        self._external_effect_started = external_effect_started

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.executed = True
        self.context = ctx
        raise ToolExecutionError(
            code="local_gate_rejected",
            safe_message="The operation was rejected before dispatch",
            external_effect_started=self._external_effect_started,
        )


class _SlowTool(_RecordingTool):
    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.executed = True
        self.context = ctx
        await asyncio.sleep(0.03)
        return "too late"


class _BlockingTool(_RecordingTool):
    def __init__(self, spec: ToolSpec) -> None:
        super().__init__(spec)
        self.started = asyncio.Event()

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.executed = True
        self.context = ctx
        self.started.set()
        await asyncio.Event().wait()
        return "unreachable"


class _StructuredTool(_RecordingTool):
    async def execute(self, ctx: ToolContext, **kwargs: Any) -> Any:
        self.executed = True
        self.context = ctx
        return {"value": "invalid-output-secret"}


class _NonJsonTool(_RecordingTool):
    async def execute(self, ctx: ToolContext, **kwargs: Any) -> Any:
        self.executed = True
        self.context = ctx
        return object()


class _SequencedTool(_RecordingTool):
    def __init__(self, spec: ToolSpec) -> None:
        super().__init__(spec)
        self.call_count = 0
        self.first_started = asyncio.Event()
        self.second_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.executed = True
        self.context = ctx
        self.call_count += 1
        if self.call_count == 1:
            self.first_started.set()
            await self.release_first.wait()
        else:
            self.second_started.set()
        return "executed"


class _ReleasableTool(_RecordingTool):
    def __init__(self, spec: ToolSpec) -> None:
        super().__init__(spec)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        self.executed = True
        self.context = ctx
        self.started.set()
        await self.release.wait()
        return "executed"


class _ObservedLock(asyncio.Lock):
    def __init__(self) -> None:
        super().__init__()
        self.acquire_attempted = asyncio.Event()

    async def acquire(self) -> Literal[True]:
        self.acquire_attempted.set()
        await super().acquire()
        return True


class _ObservedConcurrencyGate(executor_module.ExecutionConcurrencyGate):
    def __init__(self) -> None:
        self.lock = _ObservedLock()

    def lock_for(
        self,
        concurrency: ToolConcurrency,
        concurrency_key: str,
    ) -> asyncio.Lock | None:
        return self.lock


async def test_execution_guard_scope_supports_legacy_zero_argument_boolean_guard() -> None:
    guard_calls = 0

    async def legacy_guard() -> bool:
        nonlocal guard_calls
        guard_calls += 1
        return True

    async with executor_module._execution_guard_scope(
        legacy_guard,
        timeout_ms=1_000,
    ):
        assert guard_calls == 1

    assert guard_calls == 1


async def test_execution_guard_scope_rejects_guard_that_suppresses_provider_failure() -> None:
    class _SuppressingGuard:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *args: Any) -> bool:
            return True

    def suppressing_guard(_timeout_ms: int) -> _SuppressingGuard:
        return _SuppressingGuard()

    with pytest.raises(executor_module._ExecutionFenceError):
        async with executor_module._execution_guard_scope(
            suppressing_guard,
            timeout_ms=1_000,
        ):
            raise RuntimeError("provider failed")


async def _create_call(
    ledger: AgentRuntimeLedger,
    *,
    call_id: str,
    spec: ToolSpec,
    principal: RuntimePrincipal,
    args_digest: str,
    target_ref: str,
    approval_ref_digest: str = "",
    concurrency_key: str = "",
) -> None:
    await ledger.create_tool_call(
        call_id=call_id,
        run_id="run-executor",
        step_id=call_id,
        tool_name=spec.name,
        tool_version=spec.version,
        owner=spec.owner,
        effect=spec.effect.value,
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        target_ref=target_ref,
        args_digest=args_digest,
        idempotency_mode=spec.idempotency.value,
        approval_ref_digest=approval_ref_digest,
        concurrency_mode=spec.concurrency.value,
        concurrency_key=concurrency_key,
    )


async def test_executor_persists_deny_and_approval_without_calling_tool(
    tmp_path,
) -> None:
    executor_type = getattr(executor_module, "EffectExecutor", None)
    trusted_context_type = getattr(executor_module, "TrustedToolContext", None)
    assert executor_type is not None
    assert trusted_context_type is not None

    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(
        kind="user",
        principal_id="user-executor",
        granted_scopes=("external:write",),
        allowed_target_refs=("external:user-executor",),
    )
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="message",
        trigger_ref="qq:message:executor",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=3,
    )
    arguments = {"value": 1}
    args_digest = canonical_args_digest(arguments)
    denied_spec = ToolSpec(
        name="local_write",
        owner="test",
        description="local write",
        input_schema={"type": "object"},
        effect=ToolEffect.WRITE_LOCAL,
        required_scopes=("local:write",),
    )
    approval_spec = ToolSpec(
        name="external_write",
        owner="test",
        description="external write",
        input_schema={"type": "object"},
        effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
        required_scopes=("external:write",),
    )
    denied_tool = _RecordingTool(denied_spec)
    approval_tool = _RecordingTool(approval_spec)
    await _create_call(
        ledger,
        call_id="call-denied",
        spec=denied_spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="",
    )
    await _create_call(
        ledger,
        call_id="call-approval",
        spec=approval_spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="external:user-executor",
    )
    executor = executor_type(ledger=ledger, policy_gate=PolicyGate())
    trusted_context = trusted_context_type(
        user_id="user-executor",
        session_id="group:1",
    )

    denied = await executor.execute(
        call_id="call-denied",
        tool=denied_tool,
        request=ToolPolicyRequest(
            spec=denied_spec,
            principal=principal,
            args_digest=args_digest,
            registry_generation=3,
        ),
        arguments=arguments,
        trusted_context=trusted_context,
        worker_id="worker-1",
        current_registry_generation=3,
    )
    approval = await executor.execute(
        call_id="call-approval",
        tool=approval_tool,
        request=ToolPolicyRequest(
            spec=approval_spec,
            principal=principal,
            args_digest=args_digest,
            target_ref="external:user-executor",
            registry_generation=3,
        ),
        arguments=arguments,
        trusted_context=trusted_context,
        worker_id="worker-1",
        current_registry_generation=3,
    )
    denied_call = await ledger.get_tool_call("call-denied")
    approval_call = await ledger.get_tool_call("call-approval")
    events = await ledger.list_events(run_id="run-executor")
    await ledger.close()

    assert denied.decision.outcome == "deny"
    assert denied.result is None
    assert denied_call is not None and denied_call.status == "denied"
    assert denied_tool.executed is False
    assert approval.decision.outcome == "require_approval"
    assert approval.result is None
    assert approval_call is not None and approval_call.status == "approval_pending"
    assert approval_tool.executed is False
    policy_events = [event for event in events if event.actor == "policy"]
    assert [event.metadata["reason"] for event in policy_events] == [
        "missing_scope",
        "approval_required",
    ]


async def test_executor_runs_allowed_tool_with_rebuilt_trusted_context(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(kind="service", principal_id="runtime-worker")
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="recovery",
        trigger_ref="runtime:test",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=4,
    )
    arguments = {"value": 7}
    args_digest = canonical_args_digest(arguments)
    spec = ToolSpec(
        name="read_state",
        owner="test",
        description="read state",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        effect=ToolEffect.READ,
    )
    tool = _RecordingTool(spec)
    await _create_call(
        ledger,
        call_id="call-allowed",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="",
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    execution = await executor.execute(
        call_id="call-allowed",
        tool=tool,
        request=ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest=args_digest,
            registry_generation=4,
        ),
        arguments=arguments,
        trusted_context=executor_module.TrustedToolContext(
            user_id="runtime-user",
            group_id="runtime-group",
            session_id="runtime-session",
            extra={"trusted": True},
            auth_context_ref="auth:runtime-secret",
            approval_ref="approval:runtime-secret",
            idempotency_key="idempotency:runtime-secret",
            trace_id="trace-allowed",
        ),
        worker_id="worker-allow",
        current_registry_generation=4,
    )
    call = await ledger.get_tool_call("call-allowed")
    events = await ledger.list_events(run_id="run-executor")
    await ledger.close()

    assert execution.decision.outcome == "allow"
    assert execution.result is not None
    assert execution.result.status == "succeeded"
    assert execution.result.to_model_payload() == {
        "status": "succeeded",
        "output": "executed",
    }
    assert call is not None
    assert call.status == "succeeded"
    assert call.attempt_count == 1
    assert call.safe_result == execution.result.to_model_payload()
    assert tool.context is not None
    assert tool.context.user_id == "runtime-user"
    assert tool.context.group_id == "runtime-group"
    assert tool.context.session_id == "runtime-session"
    assert tool.context.principal_kind == "service"
    assert tool.context.principal_id == "runtime-worker"
    assert tool.context.run_id == "run-executor"
    assert tool.context.step_id == "call-allowed"
    assert tool.context.call_id == "call-allowed"
    assert tool.context.auth_context_ref == "auth:runtime-secret"
    assert tool.context.approval_ref == "approval:runtime-secret"
    assert tool.context.idempotency_key == "idempotency:runtime-secret"
    assert tool.context.trace_id == "trace-allowed"
    assert tool.context.registry_generation == 4
    assert tool.context.extra == {"trusted": True}
    assert [
        (event.from_status, event.to_status)
        for event in events
        if event.call_id == "call-allowed"
    ] == [
        ("", "proposed"),
        ("proposed", "ready"),
        ("ready", "claimed"),
        ("claimed", "dispatching"),
        ("dispatching", "succeeded"),
    ]


async def test_executor_denies_request_that_does_not_match_durable_call(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(kind="service", principal_id="runtime-worker")
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="recovery",
        trigger_ref="runtime:binding-test",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=5,
    )
    arguments = {"value": 9}
    args_digest = canonical_args_digest(arguments)
    durable_spec = ToolSpec(
        name="durable_read",
        owner="test",
        description="durable read",
        input_schema={"type": "object"},
        effect=ToolEffect.READ,
    )
    substituted_spec = ToolSpec(
        name="substituted_read",
        owner="test",
        description="substituted read",
        input_schema={"type": "object"},
        effect=ToolEffect.READ,
    )
    substituted_tool = _RecordingTool(substituted_spec)
    await _create_call(
        ledger,
        call_id="call-binding",
        spec=durable_spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="",
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    execution = await executor.execute(
        call_id="call-binding",
        tool=substituted_tool,
        request=ToolPolicyRequest(
            spec=substituted_spec,
            principal=principal,
            args_digest=args_digest,
            registry_generation=5,
        ),
        arguments=arguments,
        trusted_context=executor_module.TrustedToolContext(user_id="runtime-user"),
        worker_id="worker-binding",
        current_registry_generation=5,
    )
    call = await ledger.get_tool_call("call-binding")
    events = await ledger.list_events(run_id="run-executor")
    await ledger.close()

    assert execution.decision.outcome == "deny"
    assert execution.decision.reason == "call_snapshot_mismatch"
    assert execution.result is None
    assert substituted_tool.executed is False
    assert call is not None and call.status == "denied"
    assert call.error_code == "call_snapshot_mismatch"
    assert events[-1].actor == "runtime"
    assert events[-1].metadata["mismatched_fields"] == ["tool_name"]


async def test_executor_denies_downgraded_policy_spec_for_same_tool_identity(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(
        kind="service",
        principal_id="runtime-worker",
        allowed_target_refs=("local:protected-state",),
    )
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="recovery",
        trigger_ref="runtime:policy-downgrade-test",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=5,
    )
    arguments = {"value": 9}
    args_digest = canonical_args_digest(arguments)
    live_spec = ToolSpec(
        name="protected_write",
        owner="test",
        description="protected write",
        input_schema={"type": "object"},
        effect=ToolEffect.WRITE_LOCAL,
        required_scopes=("protected:write",),
        approval=ToolApproval.ALWAYS,
    )
    downgraded_spec = ToolSpec(
        name=live_spec.name,
        version=live_spec.version,
        owner=live_spec.owner,
        description=live_spec.description,
        input_schema=dict(live_spec.input_schema),
        effect=live_spec.effect,
        required_scopes=(),
        approval=ToolApproval.POLICY,
    )
    tool = _RecordingTool(live_spec)
    await _create_call(
        ledger,
        call_id="call-policy-downgrade",
        spec=live_spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="local:protected-state",
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    execution = await executor.execute(
        call_id="call-policy-downgrade",
        tool=tool,
        request=ToolPolicyRequest(
            spec=downgraded_spec,
            principal=principal,
            args_digest=args_digest,
            target_ref="local:protected-state",
            registry_generation=5,
        ),
        arguments=arguments,
        trusted_context=executor_module.TrustedToolContext(
            user_id="runtime-user"
        ),
        worker_id="worker-policy-downgrade",
        current_registry_generation=5,
    )
    call = await ledger.get_tool_call("call-policy-downgrade")
    events = await ledger.list_events(run_id="run-executor")
    await ledger.close()

    assert execution.decision.outcome == "deny"
    assert execution.decision.reason == "call_snapshot_mismatch"
    assert execution.result is None
    assert tool.executed is False
    assert call is not None and call.status == "denied"
    assert "tool_spec" in events[-1].metadata["mismatched_fields"]


async def test_executor_denies_arguments_that_fail_input_schema(tmp_path) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(kind="service", principal_id="runtime-worker")
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="recovery",
        trigger_ref="runtime:schema-test",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=6,
    )
    arguments = {"value": "not-an-integer"}
    args_digest = canonical_args_digest(arguments)
    spec = ToolSpec(
        name="schema_read",
        owner="test",
        description="schema read",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        effect=ToolEffect.READ,
    )
    tool = _RecordingTool(spec)
    await _create_call(
        ledger,
        call_id="call-schema",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="",
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    execution = await executor.execute(
        call_id="call-schema",
        tool=tool,
        request=ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest=args_digest,
            registry_generation=6,
        ),
        arguments=arguments,
        trusted_context=executor_module.TrustedToolContext(user_id="runtime-user"),
        worker_id="worker-schema",
        current_registry_generation=6,
    )
    call = await ledger.get_tool_call("call-schema")
    events = await ledger.list_events(run_id="run-executor")
    await ledger.close()

    assert execution.decision.outcome == "deny"
    assert execution.decision.reason == "input_schema_invalid"
    assert execution.result is None
    assert tool.executed is False
    assert call is not None and call.status == "denied"
    assert call.error_code == "input_schema_invalid"
    assert events[-1].metadata["validation"] == {
        "path": ["value"],
        "validator": "type",
    }
    assert "not-an-integer" not in str(events[-1].metadata)


async def test_executor_persists_terminal_failure_without_leaking_exception(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(kind="service", principal_id="runtime-worker")
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="recovery",
        trigger_ref="runtime:failure-test",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=7,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    spec = ToolSpec(
        name="failing_read",
        owner="test",
        description="failing read",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.READ,
    )
    tool = _FailingTool(spec)
    await _create_call(
        ledger,
        call_id="call-failure",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="",
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    try:
        execution = await executor.execute(
            call_id="call-failure",
            tool=tool,
            request=ToolPolicyRequest(
                spec=spec,
                principal=principal,
                args_digest=args_digest,
                registry_generation=7,
            ),
            arguments=arguments,
            trusted_context=executor_module.TrustedToolContext(
                user_id="runtime-user"
            ),
            worker_id="worker-failure",
            current_registry_generation=7,
        )
    finally:
        call = await ledger.get_tool_call("call-failure")
        await ledger.close()

    assert execution.decision.outcome == "allow"
    assert execution.result is not None
    assert execution.result.status == "failed_terminal"
    assert execution.result.error is not None
    assert execution.result.error.code == "tool_execution_failed"
    assert execution.result.error.retryable is False
    assert "provider-secret-detail" not in str(execution.result.to_model_payload())
    assert call is not None and call.status == "failed_terminal"
    assert call.safe_result == execution.result.to_model_payload()
    assert call.error_code == "tool_execution_failed"


async def test_executor_enforces_timeout_and_marks_safe_transient_retry(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(kind="service", principal_id="runtime-worker")
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="recovery",
        trigger_ref="runtime:timeout-test",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=8,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    spec = ToolSpec(
        name="slow_read",
        owner="test",
        description="slow read",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.READ,
        retry_policy=ToolRetryPolicy.SAFE_TRANSIENT,
        timeout_ms=1,
    )
    tool = _SlowTool(spec)
    await _create_call(
        ledger,
        call_id="call-timeout",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="",
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    execution = await executor.execute(
        call_id="call-timeout",
        tool=tool,
        request=ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest=args_digest,
            registry_generation=8,
        ),
        arguments=arguments,
        trusted_context=executor_module.TrustedToolContext(user_id="runtime-user"),
        worker_id="worker-timeout",
        current_registry_generation=8,
    )
    call = await ledger.get_tool_call("call-timeout")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "failed_retryable"
    assert execution.result.error is not None
    assert execution.result.error.code == "tool_timeout"
    assert execution.result.error.retryable is True
    assert call is not None and call.status == "failed_retryable"
    assert call.error_code == "tool_timeout"
    assert call.safe_result == execution.result.to_model_payload()


async def test_executor_marks_external_timeout_unknown_and_never_retryable(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(
        kind="user",
        principal_id="external-user",
        allowed_target_refs=("external:external-user",),
    )
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="message",
        trigger_ref="qq:message:external-timeout",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=9,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    approval_ref_digest = "sha256:approved-external-call"
    spec = ToolSpec(
        name="external_slow_write",
        owner="test",
        description="external slow write",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
        retry_policy=ToolRetryPolicy.SAFE_TRANSIENT,
        timeout_ms=1,
    )
    approval = ApprovalGrant(
        approval_ref_digest=approval_ref_digest,
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=spec.name,
        tool_version=spec.version,
        effect=spec.effect,
        args_digest=args_digest,
        target_ref="external:external-user",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    tool = _SlowTool(spec)
    await _create_call(
        ledger,
        call_id="call-external-timeout",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="external:external-user",
        approval_ref_digest=approval_ref_digest,
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    execution = await executor.execute(
        call_id="call-external-timeout",
        tool=tool,
        request=ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest=args_digest,
            target_ref="external:external-user",
            approval=approval,
            registry_generation=9,
        ),
        arguments=arguments,
        trusted_context=executor_module.TrustedToolContext(
            user_id="external-user",
            approval_ref="approval:runtime-secret",
        ),
        worker_id="worker-external-timeout",
        current_registry_generation=9,
    )
    call = await ledger.get_tool_call("call-external-timeout")
    await ledger.close()

    assert execution.decision.outcome == "allow"
    assert execution.result is not None
    assert execution.result.status == "unknown"
    assert execution.result.error is not None
    assert execution.result.error.retryable is False
    assert call is not None and call.status == "unknown"
    assert call.error_code == "tool_timeout_unknown"


async def test_executor_never_retries_external_concurrency_contention(
    tmp_path,
    monkeypatch,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(
        kind="user",
        principal_id="contention-user",
        allowed_target_refs=("external:contention-user",),
    )
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="message",
        trigger_ref="qq:message:external-contention",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=9,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    approval_ref_digest = "sha256:approved-contention-call"
    spec = ToolSpec(
        name="external_contended_write",
        owner="test",
        description="external contended write",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
        retry_policy=ToolRetryPolicy.NEVER,
    )
    approval = ApprovalGrant(
        approval_ref_digest=approval_ref_digest,
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=spec.name,
        tool_version=spec.version,
        effect=spec.effect,
        args_digest=args_digest,
        target_ref="external:contention-user",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    tool = _RecordingTool(spec)
    await _create_call(
        ledger,
        call_id="call-external-contention",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="external:contention-user",
        approval_ref_digest=approval_ref_digest,
    )

    async def raise_contention(*args: Any, **kwargs: Any) -> None:
        raise ConcurrencyBusyError(
            call_id="call-external-contention",
            blocking_call_id="call-existing",
            concurrency_mode="keyed_serial",
            concurrency_key="external:contention-user",
        )

    monkeypatch.setattr(ledger, "claim_tool_call", raise_contention)
    original_transition = ledger.transition_tool_call
    failed_finalization = False

    async def fail_first_terminal_transition(*args: Any, **kwargs: Any):
        nonlocal failed_finalization
        if kwargs.get("to_status") == "failed_terminal" and not failed_finalization:
            failed_finalization = True
            raise OSError("simulated ready finalization failure")
        return await original_transition(*args, **kwargs)

    monkeypatch.setattr(
        ledger,
        "transition_tool_call",
        fail_first_terminal_transition,
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    execution = await executor.execute(
        call_id="call-external-contention",
        tool=tool,
        request=ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest=args_digest,
            target_ref="external:contention-user",
            approval=approval,
            registry_generation=9,
        ),
        arguments=arguments,
        trusted_context=executor_module.TrustedToolContext(
            user_id="contention-user"
        ),
        worker_id="worker-external-contention",
        current_registry_generation=9,
    )
    call = await ledger.get_tool_call("call-external-contention")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "failed_terminal"
    assert execution.result.error is not None
    assert execution.result.error.code == "concurrency_busy"
    assert execution.result.error.retryable is False
    assert call is not None and call.status == "failed_terminal"
    assert tool.executed is False


async def test_executor_claim_commit_then_error_becomes_unknown(
    tmp_path,
    monkeypatch,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(
        kind="user",
        principal_id="claim-ambiguity-user",
        allowed_target_refs=("external:claim-ambiguity-user",),
    )
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="message",
        trigger_ref="qq:message:claim-ambiguity",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=9,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    approval_ref_digest = "sha256:approved-claim-ambiguity"
    spec = ToolSpec(
        name="external_claim_ambiguity",
        owner="test",
        description="external claim ambiguity",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
    )
    approval = ApprovalGrant(
        approval_ref_digest=approval_ref_digest,
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=spec.name,
        tool_version=spec.version,
        effect=spec.effect,
        args_digest=args_digest,
        target_ref="external:claim-ambiguity-user",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    tool = _RecordingTool(spec)
    await _create_call(
        ledger,
        call_id="call-claim-ambiguity",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="external:claim-ambiguity-user",
        approval_ref_digest=approval_ref_digest,
    )
    original_claim = ledger.claim_tool_call

    async def claim_then_raise(*args: Any, **kwargs: Any) -> None:
        await original_claim(*args, **kwargs)
        raise OSError("simulated post-commit claim failure")

    monkeypatch.setattr(ledger, "claim_tool_call", claim_then_raise)
    original_transition = ledger.transition_tool_call
    failed_finalization = False

    async def fail_first_unknown_transition(*args: Any, **kwargs: Any):
        nonlocal failed_finalization
        if kwargs.get("to_status") == "unknown" and not failed_finalization:
            failed_finalization = True
            raise OSError("simulated claimed finalization failure")
        return await original_transition(*args, **kwargs)

    monkeypatch.setattr(
        ledger,
        "transition_tool_call",
        fail_first_unknown_transition,
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    try:
        execution = await executor.execute(
            call_id="call-claim-ambiguity",
            tool=tool,
            request=ToolPolicyRequest(
                spec=spec,
                principal=principal,
                args_digest=args_digest,
                target_ref="external:claim-ambiguity-user",
                approval=approval,
                registry_generation=9,
            ),
            arguments=arguments,
            trusted_context=executor_module.TrustedToolContext(
                user_id="claim-ambiguity-user"
            ),
            worker_id="worker-claim-ambiguity",
            current_registry_generation=9,
        )
    except BaseException:
        monkeypatch.undo()
        await ledger.close()
        raise
    call = await ledger.get_tool_call("call-claim-ambiguity")
    await ledger.close()

    assert tool.executed is False
    assert execution.result is not None
    assert execution.result.status == "unknown"
    assert execution.result.error is not None
    assert execution.result.error.code == "claim_outcome_unknown"
    assert call is not None and call.status == "unknown"


async def test_executor_dispatch_marker_commit_then_error_becomes_unknown(
    tmp_path,
    monkeypatch,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(
        kind="user",
        principal_id="dispatch-marker-user",
        allowed_target_refs=("external:dispatch-marker-user",),
    )
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="message",
        trigger_ref="qq:message:dispatch-marker-ambiguity",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=9,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    approval_ref_digest = "sha256:approved-dispatch-marker"
    spec = ToolSpec(
        name="external_dispatch_marker_ambiguity",
        owner="test",
        description="external dispatch marker ambiguity",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
    )
    approval = ApprovalGrant(
        approval_ref_digest=approval_ref_digest,
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=spec.name,
        tool_version=spec.version,
        effect=spec.effect,
        args_digest=args_digest,
        target_ref="external:dispatch-marker-user",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    tool = _RecordingTool(spec)
    await _create_call(
        ledger,
        call_id="call-dispatch-marker-ambiguity",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="external:dispatch-marker-user",
        approval_ref_digest=approval_ref_digest,
    )
    original_transition = ledger.transition_tool_call
    failed = False

    async def dispatch_then_raise(*args: Any, **kwargs: Any):
        nonlocal failed
        result = await original_transition(*args, **kwargs)
        if kwargs.get("to_status") == "dispatching" and not failed:
            failed = True
            raise OSError("simulated post-commit dispatch marker failure")
        return result

    monkeypatch.setattr(ledger, "transition_tool_call", dispatch_then_raise)
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    try:
        execution = await executor.execute(
            call_id="call-dispatch-marker-ambiguity",
            tool=tool,
            request=ToolPolicyRequest(
                spec=spec,
                principal=principal,
                args_digest=args_digest,
                target_ref="external:dispatch-marker-user",
                approval=approval,
                registry_generation=9,
            ),
            arguments=arguments,
            trusted_context=executor_module.TrustedToolContext(
                user_id="dispatch-marker-user"
            ),
            worker_id="worker-dispatch-marker",
            current_registry_generation=9,
        )
    except BaseException:
        monkeypatch.undo()
        await ledger.close()
        raise
    call = await ledger.get_tool_call("call-dispatch-marker-ambiguity")
    await ledger.close()

    assert tool.executed is False
    assert execution.result is not None
    assert execution.result.status == "unknown"
    assert execution.result.error is not None
    assert execution.result.error.code == "dispatch_marker_outcome_unknown"
    assert call is not None and call.status == "unknown"


async def test_executor_cancellation_after_dispatch_persists_unknown(
    tmp_path,
    monkeypatch,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(
        kind="user",
        principal_id="cancel-user",
        allowed_target_refs=("external:cancel-user",),
    )
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="message",
        trigger_ref="qq:message:cancel",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=10,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    approval_ref_digest = "sha256:approved-cancel-call"
    spec = ToolSpec(
        name="external_blocking_write",
        owner="test",
        description="external blocking write",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
        timeout_ms=30_000,
    )
    approval = ApprovalGrant(
        approval_ref_digest=approval_ref_digest,
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=spec.name,
        tool_version=spec.version,
        effect=spec.effect,
        args_digest=args_digest,
        target_ref="external:cancel-user",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    tool = _BlockingTool(spec)
    await _create_call(
        ledger,
        call_id="call-cancel",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="external:cancel-user",
        approval_ref_digest=approval_ref_digest,
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    original_cancel = ledger.cancel_tool_call

    async def delayed_cancel(*args: Any, **kwargs: Any):
        cleanup_started.set()
        await release_cleanup.wait()
        return await original_cancel(*args, **kwargs)

    monkeypatch.setattr(ledger, "cancel_tool_call", delayed_cancel)
    task = asyncio.create_task(
        executor.execute(
            call_id="call-cancel",
            tool=tool,
            request=ToolPolicyRequest(
                spec=spec,
                principal=principal,
                args_digest=args_digest,
                target_ref="external:cancel-user",
                approval=approval,
                registry_generation=10,
            ),
            arguments=arguments,
            trusted_context=executor_module.TrustedToolContext(user_id="cancel-user"),
            worker_id="worker-cancel",
            current_registry_generation=10,
        )
    )
    await tool.started.wait()

    task.cancel()
    await cleanup_started.wait()
    task.cancel()
    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.undo()
    call = await ledger.get_tool_call("call-cancel")
    await ledger.close()

    assert call is not None and call.status == "unknown"
    assert call.error_code == "executor_cancelled_post_dispatch_unknown"


async def test_executor_parallel_setup_cancellation_closes_open_call(
    tmp_path,
    monkeypatch,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(kind="service", principal_id="parallel-cancel")
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="recovery",
        trigger_ref="runtime:parallel-setup-cancel",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=10,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    spec = ToolSpec(
        name="parallel_cancelled_read",
        owner="test",
        description="parallel cancelled read",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.READ,
        concurrency=ToolConcurrency.PARALLEL,
    )
    tool = _RecordingTool(spec)
    await _create_call(
        ledger,
        call_id="call-parallel-setup-cancel",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="",
    )
    transition_started = asyncio.Event()
    cleanup_lookup_started = asyncio.Event()
    release_cleanup_lookup = asyncio.Event()
    original_transition = ledger.transition_tool_call
    original_get_tool_call = ledger.get_tool_call
    get_tool_call_count = 0

    async def block_cleanup_lookup(call_id: str):
        nonlocal get_tool_call_count
        get_tool_call_count += 1
        if get_tool_call_count == 2:
            cleanup_lookup_started.set()
            await release_cleanup_lookup.wait()
        return await original_get_tool_call(call_id)

    async def block_ready_transition(*args: Any, **kwargs: Any):
        if kwargs.get("to_status") == "ready":
            transition_started.set()
            await asyncio.Event().wait()
        return await original_transition(*args, **kwargs)

    monkeypatch.setattr(ledger, "get_tool_call", block_cleanup_lookup)
    monkeypatch.setattr(ledger, "transition_tool_call", block_ready_transition)
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )
    task = asyncio.create_task(
        executor.execute(
            call_id="call-parallel-setup-cancel",
            tool=tool,
            request=ToolPolicyRequest(
                spec=spec,
                principal=principal,
                args_digest=args_digest,
                registry_generation=10,
            ),
            arguments=arguments,
            trusted_context=executor_module.TrustedToolContext(
                user_id="parallel-cancel"
            ),
            worker_id="worker-parallel-setup-cancel",
            current_registry_generation=10,
        )
    )
    await transition_started.wait()

    task.cancel()
    await cleanup_lookup_started.wait()
    task.cancel()
    task.cancel()
    release_cleanup_lookup.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.undo()
    call = await ledger.get_tool_call("call-parallel-setup-cancel")
    await ledger.close()

    assert tool.executed is False
    assert call is not None and call.status == "cancelled"
    assert call.error_code == "executor_cancelled"


async def test_executor_rejects_non_external_output_that_fails_schema(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(kind="service", principal_id="runtime-worker")
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="recovery",
        trigger_ref="runtime:output-schema-test",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=11,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    spec = ToolSpec(
        name="structured_read",
        owner="test",
        description="structured read",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        effect=ToolEffect.READ,
    )
    tool = _StructuredTool(spec)
    await _create_call(
        ledger,
        call_id="call-output-schema",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="",
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    execution = await executor.execute(
        call_id="call-output-schema",
        tool=tool,
        request=ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest=args_digest,
            registry_generation=11,
        ),
        arguments=arguments,
        trusted_context=executor_module.TrustedToolContext(user_id="runtime-user"),
        worker_id="worker-output-schema",
        current_registry_generation=11,
    )
    call = await ledger.get_tool_call("call-output-schema")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "failed_terminal"
    assert execution.result.error is not None
    assert execution.result.error.code == "output_schema_invalid"
    assert call is not None and call.status == "failed_terminal"
    assert "invalid-output-secret" not in str(call.safe_result)


async def test_executor_recovers_external_exception_finalization_as_unknown(
    tmp_path,
    monkeypatch,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(
        kind="user",
        principal_id="exception-user",
        allowed_target_refs=("external:exception-user",),
    )
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="message",
        trigger_ref="qq:message:external-exception",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=12,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    approval_ref_digest = "sha256:approved-exception-call"
    spec = ToolSpec(
        name="external_failing_write",
        owner="test",
        description="external failing write",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
    )
    approval = ApprovalGrant(
        approval_ref_digest=approval_ref_digest,
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=spec.name,
        tool_version=spec.version,
        effect=spec.effect,
        args_digest=args_digest,
        target_ref="external:exception-user",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    tool = _FailingTool(spec)
    await _create_call(
        ledger,
        call_id="call-external-exception",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="external:exception-user",
        approval_ref_digest=approval_ref_digest,
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )
    original_transition = ledger.transition_tool_call
    failed_finalization = False

    async def fail_first_unknown_transition(*args: Any, **kwargs: Any):
        nonlocal failed_finalization
        if kwargs.get("to_status") == "unknown" and not failed_finalization:
            failed_finalization = True
            raise OSError("simulated unknown finalization failure")
        return await original_transition(*args, **kwargs)

    monkeypatch.setattr(
        ledger,
        "transition_tool_call",
        fail_first_unknown_transition,
    )

    execution = await executor.execute(
        call_id="call-external-exception",
        tool=tool,
        request=ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest=args_digest,
            target_ref="external:exception-user",
            approval=approval,
            registry_generation=12,
        ),
        arguments=arguments,
        trusted_context=executor_module.TrustedToolContext(user_id="exception-user"),
        worker_id="worker-external-exception",
        current_registry_generation=12,
    )
    call = await ledger.get_tool_call("call-external-exception")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "unknown"
    assert execution.result.error is not None
    assert execution.result.error.code == "tool_result_persistence_unknown"
    assert execution.result.error.retryable is False
    assert call is not None and call.status == "unknown"
    assert "provider-secret-detail" not in str(call.safe_result)


async def test_executor_finalization_write_failure_falls_back_to_unknown(
    tmp_path,
    monkeypatch,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(
        kind="user",
        principal_id="finalization-user",
        allowed_target_refs=("external:finalization-user",),
    )
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="message",
        trigger_ref="qq:message:finalization-failure",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=12,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    approval_ref_digest = "sha256:approved-finalization-call"
    spec = ToolSpec(
        name="external_finalization_write",
        owner="test",
        description="external write with finalization failure",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
    )
    approval = ApprovalGrant(
        approval_ref_digest=approval_ref_digest,
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=spec.name,
        tool_version=spec.version,
        effect=spec.effect,
        args_digest=args_digest,
        target_ref="external:finalization-user",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    tool = _RecordingTool(spec)
    await _create_call(
        ledger,
        call_id="call-finalization-failure",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="external:finalization-user",
        approval_ref_digest=approval_ref_digest,
    )
    original_transition = ledger.transition_tool_call
    failed = False

    async def fail_succeeded_transition(*args: Any, **kwargs: Any):
        nonlocal failed
        if kwargs.get("to_status") == "succeeded" and not failed:
            failed = True
            raise OSError("simulated ledger finalization failure")
        return await original_transition(*args, **kwargs)

    monkeypatch.setattr(ledger, "transition_tool_call", fail_succeeded_transition)
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    try:
        execution = await executor.execute(
            call_id="call-finalization-failure",
            tool=tool,
            request=ToolPolicyRequest(
                spec=spec,
                principal=principal,
                args_digest=args_digest,
                target_ref="external:finalization-user",
                approval=approval,
                registry_generation=12,
            ),
            arguments=arguments,
            trusted_context=executor_module.TrustedToolContext(
                user_id="finalization-user"
            ),
            worker_id="worker-finalization-failure",
            current_registry_generation=12,
        )
    except BaseException:
        monkeypatch.undo()
        await ledger.close()
        raise
    call = await ledger.get_tool_call("call-finalization-failure")
    await ledger.close()

    assert tool.executed is True
    assert execution.result is not None
    assert execution.result.status == "unknown"
    assert execution.result.error is not None
    assert execution.result.error.code == "tool_result_persistence_unknown"
    assert call is not None and call.status == "unknown"
    assert call.error_code == "tool_result_persistence_unknown"


async def test_executor_cancellation_during_success_finalization_is_unknown(
    tmp_path,
    monkeypatch,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(
        kind="user",
        principal_id="finalization-cancel-user",
        allowed_target_refs=("external:finalization-cancel-user",),
    )
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="message",
        trigger_ref="qq:message:finalization-cancel",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=12,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    approval_ref_digest = "sha256:approved-finalization-cancel"
    spec = ToolSpec(
        name="external_finalization_cancel",
        owner="test",
        description="external write cancelled during finalization",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
    )
    approval = ApprovalGrant(
        approval_ref_digest=approval_ref_digest,
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=spec.name,
        tool_version=spec.version,
        effect=spec.effect,
        args_digest=args_digest,
        target_ref="external:finalization-cancel-user",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    tool = _RecordingTool(spec)
    await _create_call(
        ledger,
        call_id="call-finalization-cancel",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="external:finalization-cancel-user",
        approval_ref_digest=approval_ref_digest,
    )
    transition_started = asyncio.Event()
    original_transition = ledger.transition_tool_call

    async def block_succeeded_transition(*args: Any, **kwargs: Any):
        if kwargs.get("to_status") == "succeeded":
            transition_started.set()
            await asyncio.Event().wait()
        return await original_transition(*args, **kwargs)

    monkeypatch.setattr(ledger, "transition_tool_call", block_succeeded_transition)
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )
    task = asyncio.create_task(
        executor.execute(
            call_id="call-finalization-cancel",
            tool=tool,
            request=ToolPolicyRequest(
                spec=spec,
                principal=principal,
                args_digest=args_digest,
                target_ref="external:finalization-cancel-user",
                approval=approval,
                registry_generation=12,
            ),
            arguments=arguments,
            trusted_context=executor_module.TrustedToolContext(
                user_id="finalization-cancel-user"
            ),
            worker_id="worker-finalization-cancel",
            current_registry_generation=12,
        )
    )
    await transition_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.undo()
    call = await ledger.get_tool_call("call-finalization-cancel")
    await ledger.close()

    assert tool.executed is True
    assert call is not None and call.status == "unknown"
    assert call.error_code == "tool_result_persistence_unknown"


@pytest.mark.parametrize(
    ("external_effect_started", "expected_status", "expected_code"),
    [
        (False, "failed_terminal", "local_gate_rejected"),
        (True, "unknown", "tool_exception_unknown"),
    ],
)
async def test_executor_honors_only_proven_pre_dispatch_external_failure(
    tmp_path,
    external_effect_started: bool,
    expected_status: str,
    expected_code: str,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(
        kind="user",
        principal_id="pre-dispatch-user",
        allowed_target_refs=("external:pre-dispatch-user",),
    )
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="message",
        trigger_ref="qq:message:pre-dispatch-failure",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=12,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    approval_ref_digest = "sha256:approved-pre-dispatch-call"
    spec = ToolSpec(
        name="external_pre_dispatch_failure",
        owner="test",
        description="external write with a local gate",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
    )
    approval = ApprovalGrant(
        approval_ref_digest=approval_ref_digest,
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=spec.name,
        tool_version=spec.version,
        effect=spec.effect,
        args_digest=args_digest,
        target_ref="external:pre-dispatch-user",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    tool = _PreDispatchFailingTool(
        spec,
        external_effect_started=external_effect_started,
    )
    await _create_call(
        ledger,
        call_id="call-pre-dispatch-failure",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="external:pre-dispatch-user",
        approval_ref_digest=approval_ref_digest,
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    execution = await executor.execute(
        call_id="call-pre-dispatch-failure",
        tool=tool,
        request=ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest=args_digest,
            target_ref="external:pre-dispatch-user",
            approval=approval,
            registry_generation=12,
        ),
        arguments=arguments,
        trusted_context=executor_module.TrustedToolContext(
            user_id="pre-dispatch-user"
        ),
        worker_id="worker-pre-dispatch-failure",
        current_registry_generation=12,
    )
    call = await ledger.get_tool_call("call-pre-dispatch-failure")
    events = await ledger.list_events(run_id="run-executor")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == expected_status
    assert execution.result.error is not None
    assert execution.result.error.code == expected_code
    assert execution.result.error.retryable is False
    assert call is not None and call.status == expected_status
    terminal_event = next(
        event for event in events if event.to_status == expected_status
    )
    assert terminal_event.metadata["external_effect_started"] is external_effect_started


async def test_executor_globally_serializes_calls(tmp_path) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(kind="service", principal_id="runtime-worker")
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="recovery",
        trigger_ref="runtime:global-serial-test",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=13,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    spec = ToolSpec(
        name="global_serial_read",
        owner="test",
        description="global serial read",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.READ,
        concurrency=ToolConcurrency.GLOBAL_SERIAL,
        timeout_ms=1_000,
    )
    tool = _SequencedTool(spec)
    for call_id in ("call-global-1", "call-global-2"):
        await _create_call(
            ledger,
            call_id=call_id,
            spec=spec,
            principal=principal,
            args_digest=args_digest,
            target_ref="",
        )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    async def execute(call_id: str, worker_id: str):
        return await executor.execute(
            call_id=call_id,
            tool=tool,
            request=ToolPolicyRequest(
                spec=spec,
                principal=principal,
                args_digest=args_digest,
                registry_generation=13,
            ),
            arguments=arguments,
            trusted_context=executor_module.TrustedToolContext(
                user_id="runtime-user"
            ),
            worker_id=worker_id,
            current_registry_generation=13,
        )

    first = asyncio.create_task(execute("call-global-1", "worker-global-1"))
    await tool.first_started.wait()
    second = asyncio.create_task(execute("call-global-2", "worker-global-2"))
    try:
        await asyncio.wait_for(tool.second_started.wait(), timeout=0.02)
        overlapped = True
    except TimeoutError:
        overlapped = False
    tool.release_first.set()
    results = await asyncio.gather(first, second)
    calls = [
        await ledger.get_tool_call("call-global-1"),
        await ledger.get_tool_call("call-global-2"),
    ]
    await ledger.close()

    assert overlapped is False
    assert [result.result.status for result in results if result.result] == [
        "succeeded",
        "succeeded",
    ]
    assert [call.status for call in calls if call] == ["succeeded", "succeeded"]


async def test_executor_keyed_serializes_only_matching_keys(tmp_path) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(kind="service", principal_id="runtime-worker")
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="recovery",
        trigger_ref="runtime:keyed-serial-test",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=14,
    )
    arguments = {"resource_id": "same"}
    args_digest = canonical_args_digest(arguments)
    spec = ToolSpec(
        name="keyed_serial_read",
        owner="test",
        description="keyed serial read",
        input_schema={
            "type": "object",
            "properties": {"resource_id": {"type": "string"}},
            "required": ["resource_id"],
            "additionalProperties": False,
        },
        effect=ToolEffect.READ,
        concurrency=ToolConcurrency.KEYED_SERIAL,
        concurrency_key_template="resource:{resource_id}",
        timeout_ms=1_000,
    )
    tool = _SequencedTool(spec)
    for call_id in ("call-keyed-1", "call-keyed-2"):
        await _create_call(
            ledger,
            call_id=call_id,
            spec=spec,
            principal=principal,
            args_digest=args_digest,
            target_ref="",
            concurrency_key="resource:same",
        )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    async def execute(call_id: str, worker_id: str):
        return await executor.execute(
            call_id=call_id,
            tool=tool,
            request=ToolPolicyRequest(
                spec=spec,
                principal=principal,
                args_digest=args_digest,
                registry_generation=14,
            ),
            arguments=arguments,
            trusted_context=executor_module.TrustedToolContext(
                user_id="runtime-user"
            ),
            worker_id=worker_id,
            current_registry_generation=14,
        )

    first = asyncio.create_task(execute("call-keyed-1", "worker-keyed-1"))
    await tool.first_started.wait()
    second = asyncio.create_task(execute("call-keyed-2", "worker-keyed-2"))
    try:
        await asyncio.wait_for(tool.second_started.wait(), timeout=0.02)
        overlapped = True
    except TimeoutError:
        overlapped = False
    tool.release_first.set()
    await asyncio.gather(first, second)
    await ledger.close()

    assert overlapped is False


async def test_executor_keyed_allows_different_keys_in_parallel(tmp_path) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(kind="service", principal_id="runtime-worker")
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="recovery",
        trigger_ref="runtime:keyed-parallel-test",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=15,
    )
    spec = ToolSpec(
        name="keyed_parallel_read",
        owner="test",
        description="keyed parallel read",
        input_schema={
            "type": "object",
            "properties": {"resource_id": {"type": "string"}},
            "required": ["resource_id"],
            "additionalProperties": False,
        },
        effect=ToolEffect.READ,
        concurrency=ToolConcurrency.KEYED_SERIAL,
        concurrency_key_template="resource:{resource_id}",
        timeout_ms=1_000,
    )
    cases = []
    for suffix in ("a", "b"):
        arguments = {"resource_id": suffix}
        args_digest = canonical_args_digest(arguments)
        tool = _ReleasableTool(spec)
        call_id = f"call-keyed-{suffix}"
        await _create_call(
            ledger,
            call_id=call_id,
            spec=spec,
            principal=principal,
            args_digest=args_digest,
            target_ref="",
            concurrency_key=f"resource:{suffix}",
        )
        cases.append((call_id, arguments, args_digest, tool))
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    async def execute(case):
        call_id, arguments, args_digest, tool = case
        return await executor.execute(
            call_id=call_id,
            tool=tool,
            request=ToolPolicyRequest(
                spec=spec,
                principal=principal,
                args_digest=args_digest,
                registry_generation=15,
            ),
            arguments=arguments,
            trusted_context=executor_module.TrustedToolContext(
                user_id="runtime-user"
            ),
            worker_id=f"worker-{call_id}",
            current_registry_generation=15,
        )

    tasks = [asyncio.create_task(execute(case)) for case in cases]
    try:
        await asyncio.wait_for(
            asyncio.gather(*(case[3].started.wait() for case in cases)),
            timeout=0.03,
        )
        started_together = True
    except TimeoutError:
        started_together = False
    for case in cases:
        case[3].release.set()
    await asyncio.gather(*tasks)
    await ledger.close()

    assert started_together is True


async def test_executor_rechecks_approval_after_waiting_for_concurrency(
    tmp_path,
) -> None:
    now = [datetime(2026, 7, 21, 0, 0, tzinfo=UTC)]
    gate = _ObservedConcurrencyGate()
    await gate.lock.acquire()
    gate.lock.acquire_attempted.clear()
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(
        kind="user",
        principal_id="approval-user",
        allowed_target_refs=("external:approval-user",),
    )
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="message",
        trigger_ref="qq:message:approval-expiry",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=16,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    approval_ref_digest = "sha256:expiring-approval"
    spec = ToolSpec(
        name="expiring_external_write",
        owner="test",
        description="expiring external write",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
    )
    approval = ApprovalGrant(
        approval_ref_digest=approval_ref_digest,
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=spec.name,
        tool_version=spec.version,
        effect=spec.effect,
        args_digest=args_digest,
        target_ref="external:approval-user",
        expires_at=(now[0] + timedelta(minutes=1)).isoformat(),
    )
    tool = _RecordingTool(spec)
    await _create_call(
        ledger,
        call_id="call-expiring-approval",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="external:approval-user",
        approval_ref_digest=approval_ref_digest,
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(clock=lambda: now[0]),
        concurrency_gate=gate,
    )
    task = asyncio.create_task(
        executor.execute(
            call_id="call-expiring-approval",
            tool=tool,
            request=ToolPolicyRequest(
                spec=spec,
                principal=principal,
                args_digest=args_digest,
                target_ref="external:approval-user",
                approval=approval,
                registry_generation=16,
            ),
            arguments=arguments,
            trusted_context=executor_module.TrustedToolContext(
                user_id="approval-user"
            ),
            worker_id="worker-expiring-approval",
            current_registry_generation=16,
        )
    )
    await gate.lock.acquire_attempted.wait()
    now[0] += timedelta(minutes=2)
    gate.lock.release()

    execution = await task
    call = await ledger.get_tool_call("call-expiring-approval")
    await ledger.close()

    assert execution.decision.outcome == "deny"
    assert execution.decision.reason == "approval_expired"
    assert execution.result is None
    assert tool.executed is False
    assert call is not None and call.status == "denied"


async def test_executor_rejects_non_json_model_output_before_ledger_write(
    tmp_path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    principal = RuntimePrincipal(kind="service", principal_id="runtime-worker")
    await ledger.create_run(
        run_id="run-executor",
        trigger_type="recovery",
        trigger_ref="runtime:non-json-output",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        registry_generation=17,
    )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    spec = ToolSpec(
        name="non_json_read",
        owner="test",
        description="non-json read",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.READ,
    )
    tool = _NonJsonTool(spec)
    await _create_call(
        ledger,
        call_id="call-non-json-output",
        spec=spec,
        principal=principal,
        args_digest=args_digest,
        target_ref="",
    )
    executor = executor_module.EffectExecutor(
        ledger=ledger,
        policy_gate=PolicyGate(),
    )

    try:
        execution = await executor.execute(
            call_id="call-non-json-output",
            tool=tool,
            request=ToolPolicyRequest(
                spec=spec,
                principal=principal,
                args_digest=args_digest,
                registry_generation=17,
            ),
            arguments=arguments,
            trusted_context=executor_module.TrustedToolContext(
                user_id="runtime-user"
            ),
            worker_id="worker-non-json-output",
            current_registry_generation=17,
        )
    finally:
        call = await ledger.get_tool_call("call-non-json-output")
        await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "failed_terminal"
    assert execution.result.error is not None
    assert execution.result.error.code == "output_not_json"
    assert call is not None and call.status == "failed_terminal"
    assert call.safe_result == execution.result.to_model_payload()


async def test_executor_returns_retryable_result_for_cross_process_busy_scope(
    tmp_path,
) -> None:
    db_path = tmp_path / "agent-runtime.db"
    first_ledger = AgentRuntimeLedger(db_path)
    second_ledger = AgentRuntimeLedger(db_path)
    await first_ledger.init()
    await second_ledger.init()
    principal = RuntimePrincipal(kind="service", principal_id="runtime-worker")
    for run_id in ("run-busy-1", "run-busy-2"):
        await first_ledger.create_run(
            run_id=run_id,
            trigger_type="recovery",
            trigger_ref=f"runtime:{run_id}",
            principal_kind=principal.kind,
            principal_id=principal.principal_id,
            registry_generation=18,
        )
    arguments: dict[str, Any] = {}
    args_digest = canonical_args_digest(arguments)
    spec = ToolSpec(
        name="global_busy_read",
        owner="test",
        description="global busy read",
        input_schema={"type": "object", "additionalProperties": False},
        effect=ToolEffect.READ,
        retry_policy=ToolRetryPolicy.SAFE_TRANSIENT,
        concurrency=ToolConcurrency.GLOBAL_SERIAL,
        timeout_ms=1_000,
    )
    for ledger, run_id, call_id in (
        (first_ledger, "run-busy-1", "call-busy-1"),
        (second_ledger, "run-busy-2", "call-busy-2"),
    ):
        await ledger.create_tool_call(
            call_id=call_id,
            run_id=run_id,
            step_id=call_id,
            tool_name=spec.name,
            tool_version=spec.version,
            owner=spec.owner,
            effect=spec.effect.value,
            principal_kind=principal.kind,
            principal_id=principal.principal_id,
            args_digest=args_digest,
            idempotency_mode=spec.idempotency.value,
            concurrency_mode=spec.concurrency.value,
        )
    first_tool = _ReleasableTool(spec)
    second_tool = _RecordingTool(spec)
    first_executor = executor_module.EffectExecutor(
        ledger=first_ledger,
        policy_gate=PolicyGate(),
    )
    second_executor = executor_module.EffectExecutor(
        ledger=second_ledger,
        policy_gate=PolicyGate(),
    )

    first_task = asyncio.create_task(
        first_executor.execute(
            call_id="call-busy-1",
            tool=first_tool,
            request=ToolPolicyRequest(
                spec=spec,
                principal=principal,
                args_digest=args_digest,
                registry_generation=18,
            ),
            arguments=arguments,
            trusted_context=executor_module.TrustedToolContext(user_id="runtime"),
            worker_id="worker-busy-1",
            current_registry_generation=18,
        )
    )
    await first_tool.started.wait()
    try:
        try:
            second_execution = await second_executor.execute(
                call_id="call-busy-2",
                tool=second_tool,
                request=ToolPolicyRequest(
                    spec=spec,
                    principal=principal,
                    args_digest=args_digest,
                    registry_generation=18,
                ),
                arguments=arguments,
                trusted_context=executor_module.TrustedToolContext(
                    user_id="runtime"
                ),
                worker_id="worker-busy-2",
                current_registry_generation=18,
            )
        finally:
            first_tool.release.set()
            await first_task
    finally:
        second_call = await second_ledger.get_tool_call("call-busy-2")
        await second_ledger.close()
        await first_ledger.close()

    assert second_execution.result is not None
    assert second_execution.result.status == "failed_retryable"
    assert second_execution.result.error is not None
    assert second_execution.result.error.code == "concurrency_busy"
    assert second_tool.executed is False
    assert second_call is not None and second_call.status == "failed_retryable"
    assert second_call.attempt_count == 0
