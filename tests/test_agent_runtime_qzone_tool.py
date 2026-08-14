"""Governed Agent Runtime adapter contracts for QZone journal publish."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from importlib import import_module
from typing import Any, cast

import pytest

from kernel.types import (
    ToolApproval,
    ToolConcurrency,
    ToolContext,
    ToolEffect,
    ToolExecutionError,
    ToolIdempotency,
    ToolRetryPolicy,
)
from plugins.qzone_journal.delivery import DeliveryConfig, JournalDelivery
from plugins.qzone_journal.store import InvalidDraftTransitionError
from plugins.qzone_journal.transport import WireProfile
from services.agent_runtime.coordinator import RunCoordinator
from services.agent_runtime.executor import EffectExecutor, TrustedToolContext
from services.agent_runtime.ledger import AgentRuntimeLedger
from services.agent_runtime.policy import (
    ApprovalGrant,
    PolicyGate,
    RuntimePrincipal,
    canonical_args_digest,
)
from services.agent_runtime.reconciliation import ReconciliationCoordinator
from services.tools.qzone_journal import QZonePublishDraftTool
from services.tools.registry import ToolRegistry

_DRAFT_ID = "qzd_0123456789abcdef01234567"
_TARGET = f"qzone:draft:{_DRAFT_ID}:publish"


def _tool_type() -> type[Any]:
    try:
        module = import_module("services.tools.qzone_journal")
    except ModuleNotFoundError as exc:
        if exc.name != "services.tools.qzone_journal":
            raise
        module = None
    assert module is not None, "the governed QZone journal adapter is missing"
    tool_type = getattr(module, "QZonePublishDraftTool", None)
    assert tool_type is not None, "QZonePublishDraftTool is missing"
    return tool_type


def _reconciliation_adapter_type() -> type[Any]:
    module = import_module("services.tools.qzone_journal")
    adapter_type = getattr(module, "QZoneReconciliationAdapter", None)
    assert adapter_type is not None, "QZone reconciliation adapter is missing"
    return adapter_type


class _LiveDeliveryStub:
    live_publish_configured = True

    async def deliver(self, draft_id: str) -> Any:
        raise AssertionError(f"delivery must not run during binding: {draft_id}")


@dataclass(frozen=True, slots=True)
class _Profile:
    profile_id: str = "validated-qzone-test-v1"
    validated: bool = True


@dataclass(frozen=True, slots=True)
class _Draft:
    draft_id: str
    content: str
    status: str
    approval_scope: str = "live"
    source: str = "event_replan"
    subject_kind: str = "self"
    privacy: str = "public"
    source_summary: str = "今天整理了自己的项目进展。"


class _Store:
    def __init__(
        self,
        *,
        status: str = "approved",
        finalization_error: BaseException | None = None,
        claim_error: BaseException | None = None,
    ) -> None:
        self.status = status
        self.events: list[str] = []
        self.unknown_reason = ""
        self.finalization_error = finalization_error
        self.claim_error = claim_error

    def _draft(self) -> _Draft:
        return _Draft(
            draft_id=_DRAFT_ID,
            content="今天整理了自己的项目进展。",
            status=self.status,
        )

    async def get(self, draft_id: str) -> _Draft | None:
        assert draft_id == _DRAFT_ID
        self.events.append("store.get")
        return self._draft()

    async def is_lineage_tip(self, draft_id: str) -> bool:
        assert draft_id == _DRAFT_ID
        self.events.append("store.is_lineage_tip")
        return True

    async def claim(self, draft_id: str) -> _Draft:
        assert draft_id == _DRAFT_ID
        self.events.append("store.claim")
        self.status = "dispatching"
        if self.claim_error is not None:
            raise self.claim_error
        return self._draft()

    async def mark_unknown(self, draft_id: str, *, reason: str) -> _Draft:
        assert draft_id == _DRAFT_ID
        self.events.append("store.mark_unknown")
        self.status = "unknown"
        self.unknown_reason = reason
        return self._draft()

    async def mark_published(
        self,
        draft_id: str,
        *,
        external_post_id: str,
    ) -> _Draft:
        assert draft_id == _DRAFT_ID
        assert external_post_id
        self.events.append("store.mark_published")
        if self.finalization_error is not None:
            raise self.finalization_error
        self.status = "published"
        return self._draft()

    async def confirm_published(
        self,
        draft_id: str,
        *,
        note: str,
        external_post_id: str | None = None,
    ) -> _Draft:
        assert draft_id == _DRAFT_ID
        assert self.status in {"unknown", "published"}
        assert note.strip()
        assert external_post_id
        self.events.append("store.confirm_published")
        self.status = "published"
        return self._draft()

    async def confirm_not_published(
        self,
        draft_id: str,
        *,
        note: str,
    ) -> _Draft:
        assert draft_id == _DRAFT_ID
        assert self.status in {"unknown", "approved"}
        assert note.strip()
        self.events.append("store.confirm_not_published")
        self.status = "approved"
        return self._draft()


class _CredentialSource:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self) -> dict[str, str]:
        self.calls += 1
        return {"uin": "384801062"}


class _Transport:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.publish_calls = 0

    async def publish(self, **kwargs: Any) -> dict[str, str]:
        self.publish_calls += 1
        if self.error is not None:
            raise self.error
        return {"status": "published", "remote_id": "remote-fixture"}


class _BlockingTransport(_Transport):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def publish(self, **kwargs: Any) -> dict[str, str]:
        self.publish_calls += 1
        self.started.set()
        await asyncio.Event().wait()
        return {"status": "published", "remote_id": "unreachable"}


def _delivery(
    *,
    store: _Store,
    credentials: _CredentialSource,
    transport: _Transport,
) -> JournalDelivery:
    return JournalDelivery(
        config=DeliveryConfig(
            enabled=True,
            dry_run=False,
            allow_live_publish=True,
            allowed_live_uins=("384801062",),
        ),
        store=store,
        credential_source=credentials,
        transport=transport,
        profile=cast(WireProfile, _Profile()),
    )


async def _coordinator(
    tmp_path: Any,
    tool: QZonePublishDraftTool,
) -> tuple[AgentRuntimeLedger, RunCoordinator, RuntimePrincipal]:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    registry = ToolRegistry()
    registry.register(tool)
    principal = RuntimePrincipal(
        kind="service",
        principal_id="qzone-runtime",
        granted_scopes=("qzone:journal:publish",),
        allowed_target_refs=(_TARGET,),
    )
    coordinator = RunCoordinator(
        ledger=ledger,
        registry=registry,
        executor=EffectExecutor(ledger=ledger, policy_gate=PolicyGate()),
    )
    return ledger, coordinator, principal


def _approval(
    *,
    principal: RuntimePrincipal,
    tool: QZonePublishDraftTool,
    arguments: dict[str, str],
    digest: str,
) -> ApprovalGrant:
    return ApprovalGrant(
        approval_ref_digest=digest,
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=tool.spec.name,
        tool_version=tool.spec.version,
        effect=tool.spec.effect,
        args_digest=canonical_args_digest(arguments),
        target_ref=_TARGET,
        expires_at="2099-01-01T00:00:00+00:00",
    )


def test_qzone_publish_tool_has_irreversible_spec_and_trusted_draft_binding() -> None:
    tool = _tool_type()(_LiveDeliveryStub())
    ctx = ToolContext(
        user_id="operator",
        extra={"qzone_draft_ids": [_DRAFT_ID]},
    )

    spec = tool.spec
    binding = tool.bind_invocation(ctx, {"draft_id": _DRAFT_ID})

    assert tool.name == "qzone_publish_draft"
    assert spec.owner == "qzone_journal"
    assert spec.effect is ToolEffect.EXTERNAL_IRREVERSIBLE
    assert spec.required_scopes == ("qzone:journal:publish",)
    assert spec.approval is ToolApproval.ALWAYS
    assert spec.idempotency is ToolIdempotency.RECONCILE_ONLY
    assert spec.retry_policy is ToolRetryPolicy.NEVER
    assert spec.concurrency is ToolConcurrency.KEYED_SERIAL
    assert spec.binding_required is True
    assert spec.data_classification == ("qzone_journal_public_post",)
    assert (binding.target_ref, binding.concurrency_key) == (_TARGET, _TARGET)


@pytest.mark.parametrize(
    ("trusted_ids", "claimed_id"),
    [
        ([], _DRAFT_ID),
        (["qzd_aaaaaaaaaaaaaaaaaaaaaaaa"], _DRAFT_ID),
        ([_DRAFT_ID], "qzd_aaaaaaaaaaaaaaaaaaaaaaaa"),
        ([_DRAFT_ID], "../approved-draft"),
    ],
)
def test_qzone_publish_binding_rejects_untrusted_or_invalid_draft_ids(
    trusted_ids: list[str],
    claimed_id: str,
) -> None:
    tool = _tool_type()(_LiveDeliveryStub())

    with pytest.raises(ValueError, match="draft"):
        tool.bind_invocation(
            ToolContext(
                user_id="operator",
                extra={"qzone_draft_ids": trusted_ids},
            ),
            {"draft_id": claimed_id},
        )


async def test_qzone_gate_rejection_after_runtime_approval_is_terminal(
    tmp_path: Any,
) -> None:
    store = _Store(status="pending_review")
    credentials = _CredentialSource()
    transport = _Transport()
    tool = QZonePublishDraftTool(_delivery(store=store, credentials=credentials, transport=transport))
    ledger, coordinator, principal = await _coordinator(tmp_path, tool)
    arguments = {"draft_id": _DRAFT_ID}
    trusted_context = TrustedToolContext(
        user_id="operator",
        extra={"qzone_draft_ids": [_DRAFT_ID]},
    )
    await coordinator.start_run(
        run_id="run-qzone-gate",
        trigger_type="domain_event",
        trigger_ref="qzone:draft-ready",
        principal=principal,
    )

    waiting = await coordinator.execute_tool(
        run_id="run-qzone-gate",
        call_id="call-qzone-gate",
        step_id="step-qzone-gate",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        trusted_context=trusted_context,
        worker_id="worker-qzone-gate",
    )
    assert waiting.decision.outcome == "require_approval"
    assert credentials.calls == 0
    assert transport.publish_calls == 0

    approval = ApprovalGrant(
        approval_ref_digest="sha256:qzone-gate-approval",
        principal_kind=principal.kind,
        principal_id=principal.principal_id,
        tool_name=tool.spec.name,
        tool_version=tool.spec.version,
        effect=tool.spec.effect,
        args_digest=canonical_args_digest(arguments),
        target_ref=_TARGET,
        expires_at="2099-01-01T00:00:00+00:00",
    )
    execution = await coordinator.resume_approved_tool(
        run_id="run-qzone-gate",
        call_id="call-qzone-gate",
        principal=principal,
        arguments=arguments,
        approval=approval,
        trusted_context=trusted_context,
        worker_id="worker-qzone-gate-resume",
        approval_actor="operator",
    )
    call = await ledger.get_tool_call("call-qzone-gate")
    run = await ledger.get_run("run-qzone-gate")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "failed_terminal"
    assert execution.result.error is not None
    assert execution.result.error.code == "qzone_publish_precondition_failed"
    assert call is not None and call.status == "failed_terminal"
    assert run is not None and run.status == "running"
    assert store.status == "pending_review"
    assert credentials.calls == 0
    assert transport.publish_calls == 0


async def test_qzone_publish_tool_rejects_legacy_direct_execution() -> None:
    store = _Store(status="approved")
    credentials = _CredentialSource()
    transport = _Transport()
    tool = QZonePublishDraftTool(_delivery(store=store, credentials=credentials, transport=transport))

    with pytest.raises(ToolExecutionError, match="governed") as raised:
        await tool.execute(
            ToolContext(
                user_id="operator",
                extra={"qzone_draft_ids": [_DRAFT_ID]},
            ),
            draft_id=_DRAFT_ID,
        )

    assert raised.value.external_effect_started is False
    assert raised.value.code == "governed_runtime_required"
    assert store.status == "approved"
    assert credentials.calls == 0
    assert transport.publish_calls == 0


async def test_qzone_dual_approval_publishes_exactly_once(tmp_path: Any) -> None:
    store = _Store(status="approved")
    credentials = _CredentialSource()
    transport = _Transport()
    tool = QZonePublishDraftTool(_delivery(store=store, credentials=credentials, transport=transport))
    ledger, coordinator, principal = await _coordinator(tmp_path, tool)
    arguments = {"draft_id": _DRAFT_ID}
    await coordinator.start_run(
        run_id="run-qzone-success",
        trigger_type="domain_event",
        trigger_ref="qzone:draft-approved",
        principal=principal,
    )

    execution = await coordinator.execute_tool(
        run_id="run-qzone-success",
        call_id="call-qzone-success",
        step_id="step-qzone-success",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        approval=_approval(
            principal=principal,
            tool=tool,
            arguments=arguments,
            digest="sha256:qzone-success-approval",
        ),
        trusted_context=TrustedToolContext(
            user_id="operator",
            extra={"qzone_draft_ids": [_DRAFT_ID]},
        ),
        worker_id="worker-qzone-success",
    )
    call = await ledger.get_tool_call("call-qzone-success")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "succeeded"
    assert execution.result.structured_output == ('{"draft_id":"qzd_0123456789abcdef01234567","status":"published"}')
    assert call is not None and call.status == "succeeded"
    assert store.status == "published"
    assert credentials.calls == 1
    assert transport.publish_calls == 1
    assert store.events == [
        "store.get",
        "store.is_lineage_tip",
        "store.claim",
        "store.mark_published",
    ]


async def test_qzone_provider_exception_is_unknown_in_both_ledgers(
    tmp_path: Any,
) -> None:
    store = _Store(status="approved")
    credentials = _CredentialSource()
    transport = _Transport(error=OSError("read timeout after dispatch"))
    tool = QZonePublishDraftTool(_delivery(store=store, credentials=credentials, transport=transport))
    ledger, coordinator, principal = await _coordinator(tmp_path, tool)
    arguments = {"draft_id": _DRAFT_ID}
    await coordinator.start_run(
        run_id="run-qzone-unknown",
        trigger_type="domain_event",
        trigger_ref="qzone:draft-approved",
        principal=principal,
    )

    execution = await coordinator.execute_tool(
        run_id="run-qzone-unknown",
        call_id="call-qzone-unknown",
        step_id="step-qzone-unknown",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        approval=_approval(
            principal=principal,
            tool=tool,
            arguments=arguments,
            digest="sha256:qzone-unknown-approval",
        ),
        trusted_context=TrustedToolContext(
            user_id="operator",
            extra={"qzone_draft_ids": [_DRAFT_ID]},
        ),
        worker_id="worker-qzone-unknown",
    )
    call = await ledger.get_tool_call("call-qzone-unknown")
    run = await ledger.get_run("run-qzone-unknown")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "unknown"
    assert execution.result.error is not None
    assert execution.result.error.code == "tool_exception_unknown"
    assert "read timeout" not in str(execution.result.to_model_payload())
    assert call is not None and call.status == "unknown"
    assert run is not None and run.status == "waiting_external"
    assert store.status == "unknown"
    assert store.unknown_reason.startswith("OSError")
    assert credentials.calls == 1
    assert transport.publish_calls == 1
    assert store.events[-1] == "store.mark_unknown"


@pytest.mark.parametrize(
    ("decision", "external_id", "expected_status", "expected_event"),
    [
        (
            "confirmed_succeeded",
            "qzone-post-2001",
            "published",
            "store.confirm_published",
        ),
        (
            "confirmed_not_applied",
            "",
            "approved",
            "store.confirm_not_published",
        ),
    ],
)
async def test_qzone_manual_reconciliation_updates_domain_before_runtime(
    tmp_path: Any,
    decision: str,
    external_id: str,
    expected_status: str,
    expected_event: str,
) -> None:
    store = _Store(status="approved")
    credentials = _CredentialSource()
    transport = _Transport(error=OSError("provider outcome unknown"))
    tool = QZonePublishDraftTool(
        _delivery(store=store, credentials=credentials, transport=transport)
    )
    ledger, coordinator, principal = await _coordinator(tmp_path, tool)
    arguments = {"draft_id": _DRAFT_ID}
    await coordinator.start_run(
        run_id="run-qzone-reconcile",
        trigger_type="domain_event",
        trigger_ref="qzone:draft-approved",
        principal=principal,
    )
    unknown = await coordinator.execute_tool(
        run_id="run-qzone-reconcile",
        call_id="call-qzone-reconcile",
        step_id="step-qzone-reconcile",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        approval=_approval(
            principal=principal,
            tool=tool,
            arguments=arguments,
            digest="sha256:qzone-reconcile-approval",
        ),
        trusted_context=TrustedToolContext(
            user_id="operator",
            extra={"qzone_draft_ids": [_DRAFT_ID]},
        ),
        worker_id="worker-qzone-reconcile",
    )
    adapter = _reconciliation_adapter_type()(store)
    resolver = ReconciliationCoordinator(ledger=ledger)
    resolution = await resolver.resolve_unknown(
        call_id="call-qzone-reconcile",
        decision=decision,
        principal=RuntimePrincipal(
            kind="service",
            principal_id="qzone-operator",
            granted_scopes=("runtime:tool:reconcile",),
            allowed_target_refs=(_TARGET,),
        ),
        evidence_ref="qzone:capture:attested-2001",
        operator_note="attested capture confirms publication",
        external_id=external_id,
        adapter=adapter,
    )
    call = await ledger.get_tool_call("call-qzone-reconcile")
    run = await ledger.get_run("run-qzone-reconcile")
    events = await ledger.list_events(run_id="run-qzone-reconcile")
    await ledger.close()

    assert unknown.result is not None and unknown.result.status == "unknown"
    assert store.status == expected_status
    assert store.events[-1] == expected_event
    assert call is not None and call.status == "unknown"
    assert run is not None and run.status == "running"
    assert resolution.adapter_id == "qzone-journal-store-v1"
    assert resolution.external_id == external_id
    assert [event.event_type for event in events].count(
        "reconciliation_resolved"
    ) == 1


async def test_qzone_post_dispatch_transition_error_cannot_be_downgraded_terminal(
    tmp_path: Any,
) -> None:
    store = _Store(
        status="approved",
        finalization_error=InvalidDraftTransitionError("stale local state"),
    )
    credentials = _CredentialSource()
    transport = _Transport()
    tool = QZonePublishDraftTool(_delivery(store=store, credentials=credentials, transport=transport))
    ledger, coordinator, principal = await _coordinator(tmp_path, tool)
    arguments = {"draft_id": _DRAFT_ID}
    await coordinator.start_run(
        run_id="run-qzone-finalize-unknown",
        trigger_type="domain_event",
        trigger_ref="qzone:draft-approved",
        principal=principal,
    )

    execution = await coordinator.execute_tool(
        run_id="run-qzone-finalize-unknown",
        call_id="call-qzone-finalize-unknown",
        step_id="step-qzone-finalize-unknown",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        approval=_approval(
            principal=principal,
            tool=tool,
            arguments=arguments,
            digest="sha256:qzone-finalize-unknown-approval",
        ),
        trusted_context=TrustedToolContext(
            user_id="operator",
            extra={"qzone_draft_ids": [_DRAFT_ID]},
        ),
        worker_id="worker-qzone-finalize-unknown",
    )
    call = await ledger.get_tool_call("call-qzone-finalize-unknown")
    run = await ledger.get_run("run-qzone-finalize-unknown")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "unknown"
    assert call is not None and call.status == "unknown"
    assert run is not None and run.status == "waiting_external"
    assert store.status == "unknown"
    assert store.events[-2:] == ["store.mark_published", "store.mark_unknown"]


async def test_qzone_committed_claim_error_requires_reconciliation(
    tmp_path: Any,
) -> None:
    store = _Store(
        status="approved",
        claim_error=RuntimeError("post-commit read failed"),
    )
    credentials = _CredentialSource()
    transport = _Transport()
    tool = QZonePublishDraftTool(
        _delivery(store=store, credentials=credentials, transport=transport)
    )
    ledger, coordinator, principal = await _coordinator(tmp_path, tool)
    arguments = {"draft_id": _DRAFT_ID}
    await coordinator.start_run(
        run_id="run-qzone-claim-unknown",
        trigger_type="domain_event",
        trigger_ref="qzone:draft-approved",
        principal=principal,
    )

    execution = await coordinator.execute_tool(
        run_id="run-qzone-claim-unknown",
        call_id="call-qzone-claim-unknown",
        step_id="step-qzone-claim-unknown",
        tool_name=tool.name,
        principal=principal,
        arguments=arguments,
        approval=_approval(
            principal=principal,
            tool=tool,
            arguments=arguments,
            digest="sha256:qzone-claim-unknown-approval",
        ),
        trusted_context=TrustedToolContext(
            user_id="operator",
            extra={"qzone_draft_ids": [_DRAFT_ID]},
        ),
        worker_id="worker-qzone-claim-unknown",
    )
    call = await ledger.get_tool_call("call-qzone-claim-unknown")
    run = await ledger.get_run("run-qzone-claim-unknown")
    await ledger.close()

    assert execution.result is not None
    assert execution.result.status == "unknown"
    assert call is not None and call.status == "unknown"
    assert run is not None and run.status == "waiting_external"
    assert store.status == "unknown"
    assert store.events[-3:] == [
        "store.claim",
        "store.get",
        "store.mark_unknown",
    ]
    assert transport.publish_calls == 0


async def test_qzone_repeated_cancellation_preserves_both_unknown_states(
    tmp_path: Any,
) -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    class _BlockingUnknownStore(_Store):
        async def mark_unknown(self, draft_id: str, *, reason: str) -> _Draft:
            cleanup_started.set()
            await release_cleanup.wait()
            return await super().mark_unknown(draft_id, reason=reason)

    store = _BlockingUnknownStore(status="approved")
    credentials = _CredentialSource()
    transport = _BlockingTransport()
    tool = QZonePublishDraftTool(
        _delivery(store=store, credentials=credentials, transport=transport)
    )
    ledger, coordinator, principal = await _coordinator(tmp_path, tool)
    arguments = {"draft_id": _DRAFT_ID}
    await coordinator.start_run(
        run_id="run-qzone-cancel",
        trigger_type="domain_event",
        trigger_ref="qzone:draft-approved",
        principal=principal,
    )
    task = asyncio.create_task(
        coordinator.execute_tool(
            run_id="run-qzone-cancel",
            call_id="call-qzone-cancel",
            step_id="step-qzone-cancel",
            tool_name=tool.name,
            principal=principal,
            arguments=arguments,
            approval=_approval(
                principal=principal,
                tool=tool,
                arguments=arguments,
                digest="sha256:qzone-cancel-approval",
            ),
            trusted_context=TrustedToolContext(
                user_id="operator",
                extra={"qzone_draft_ids": [_DRAFT_ID]},
            ),
            worker_id="worker-qzone-cancel",
        )
    )
    await transport.started.wait()

    task.cancel()
    await cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    call = await ledger.get_tool_call("call-qzone-cancel")
    run = await ledger.get_run("run-qzone-cancel")
    await ledger.close()

    assert transport.publish_calls == 1
    assert store.status == "unknown"
    assert call is not None and call.status == "unknown"
    assert run is not None and run.status == "waiting_external"


async def test_qzone_cleanup_cancellation_after_provider_error_propagates(
    tmp_path: Any,
) -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    class _BlockingUnknownStore(_Store):
        async def mark_unknown(self, draft_id: str, *, reason: str) -> _Draft:
            cleanup_started.set()
            await release_cleanup.wait()
            return await super().mark_unknown(draft_id, reason=reason)

    store = _BlockingUnknownStore(status="approved")
    credentials = _CredentialSource()
    transport = _Transport(error=OSError("provider failed after dispatch"))
    tool = QZonePublishDraftTool(
        _delivery(store=store, credentials=credentials, transport=transport)
    )
    ledger, coordinator, principal = await _coordinator(tmp_path, tool)
    arguments = {"draft_id": _DRAFT_ID}
    await coordinator.start_run(
        run_id="run-qzone-cleanup-cancel",
        trigger_type="domain_event",
        trigger_ref="qzone:draft-approved",
        principal=principal,
    )
    task = asyncio.create_task(
        coordinator.execute_tool(
            run_id="run-qzone-cleanup-cancel",
            call_id="call-qzone-cleanup-cancel",
            step_id="step-qzone-cleanup-cancel",
            tool_name=tool.name,
            principal=principal,
            arguments=arguments,
            approval=_approval(
                principal=principal,
                tool=tool,
                arguments=arguments,
                digest="sha256:qzone-cleanup-cancel-approval",
            ),
            trusted_context=TrustedToolContext(
                user_id="operator",
                extra={"qzone_draft_ids": [_DRAFT_ID]},
            ),
            worker_id="worker-qzone-cleanup-cancel",
        )
    )
    await cleanup_started.wait()

    task.cancel()
    task.cancel()
    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    call = await ledger.get_tool_call("call-qzone-cleanup-cancel")
    run = await ledger.get_run("run-qzone-cleanup-cancel")
    await ledger.close()

    assert store.status == "unknown"
    assert call is not None and call.status == "unknown"
    assert run is not None and run.status == "waiting_external"


async def test_qzone_claim_inspection_survives_repeated_cancellation(
    tmp_path: Any,
) -> None:
    inspection_started = asyncio.Event()
    release_inspection = asyncio.Event()

    class _ClaimCancellationStore(_Store):
        def __init__(self) -> None:
            super().__init__(status="approved")
            self.get_calls = 0

        async def get(self, draft_id: str) -> _Draft | None:
            self.get_calls += 1
            if self.get_calls == 2:
                inspection_started.set()
                await release_inspection.wait()
            return await super().get(draft_id)

        async def claim(self, draft_id: str) -> _Draft:
            assert draft_id == _DRAFT_ID
            self.events.append("store.claim")
            self.status = "dispatching"
            raise asyncio.CancelledError()

    store = _ClaimCancellationStore()
    credentials = _CredentialSource()
    transport = _Transport()
    tool = QZonePublishDraftTool(
        _delivery(store=store, credentials=credentials, transport=transport)
    )
    ledger, coordinator, principal = await _coordinator(tmp_path, tool)
    arguments = {"draft_id": _DRAFT_ID}
    await coordinator.start_run(
        run_id="run-qzone-claim-cancel",
        trigger_type="domain_event",
        trigger_ref="qzone:draft-approved",
        principal=principal,
    )
    task = asyncio.create_task(
        coordinator.execute_tool(
            run_id="run-qzone-claim-cancel",
            call_id="call-qzone-claim-cancel",
            step_id="step-qzone-claim-cancel",
            tool_name=tool.name,
            principal=principal,
            arguments=arguments,
            approval=_approval(
                principal=principal,
                tool=tool,
                arguments=arguments,
                digest="sha256:qzone-claim-cancel-approval",
            ),
            trusted_context=TrustedToolContext(
                user_id="operator",
                extra={"qzone_draft_ids": [_DRAFT_ID]},
            ),
            worker_id="worker-qzone-claim-cancel",
        )
    )
    await inspection_started.wait()

    task.cancel()
    task.cancel()
    release_inspection.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    call = await ledger.get_tool_call("call-qzone-claim-cancel")
    run = await ledger.get_run("run-qzone-claim-cancel")
    await ledger.close()

    assert store.status == "unknown"
    assert transport.publish_calls == 0
    assert call is not None and call.status == "unknown"
    assert run is not None and run.status == "waiting_external"
