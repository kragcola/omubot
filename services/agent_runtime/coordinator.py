"""Dark run coordination for Agent Runtime v2."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping
from string import Formatter
from typing import Any

from kernel.types import Tool, ToolConcurrency, ToolContext
from services.agent_runtime.executor import (
    EffectExecution,
    EffectExecutor,
    ExecutionGuard,
    TrustedToolContext,
)
from services.agent_runtime.ledger import (
    AgentRunRecord,
    AgentRuntimeLedger,
    ToolCallRecord,
)
from services.agent_runtime.policy import (
    ApprovalGrant,
    RuntimePrincipal,
    ToolPolicyRequest,
    canonical_args_digest,
)
from services.tools.registry import ToolRegistry


async def _await_required_cleanup(awaitable: Awaitable[Any]) -> None:
    task = asyncio.ensure_future(awaitable)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    task.result()


class RunCoordinator:
    """Own Run lifecycle and connect catalog snapshots to governed execution."""

    def __init__(
        self,
        *,
        ledger: AgentRuntimeLedger,
        registry: ToolRegistry,
        executor: EffectExecutor,
    ) -> None:
        self._ledger = ledger
        self._registry = registry
        self._executor = executor

    async def start_run(
        self,
        *,
        run_id: str,
        trigger_type: str,
        trigger_ref: str,
        principal: RuntimePrincipal,
        session_id: str = "",
        group_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        registry_generation, _tools = self._registry.snapshot_catalog()
        await self._ledger.create_run(
            run_id=run_id,
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
            principal_kind=principal.kind,
            principal_id=principal.principal_id,
            session_id=session_id,
            group_id=group_id,
            registry_generation=registry_generation,
            metadata=metadata,
        )
        return await self._ledger.transition_run(
            run_id,
            to_status="running",
            actor="coordinator",
        )

    async def execute_tool(
        self,
        *,
        run_id: str,
        call_id: str,
        step_id: str,
        tool_name: str,
        principal: RuntimePrincipal,
        arguments: Mapping[str, Any],
        trusted_context: TrustedToolContext,
        worker_id: str,
        target_ref: str = "",
        idempotency_key_digest: str = "",
        approval: ApprovalGrant | None = None,
        execution_guard: ExecutionGuard | None = None,
    ) -> EffectExecution:
        run = await self._ledger.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status != "running":
            raise ValueError(f"run is not executable from status: {run.status}")
        if (run.principal_kind, run.principal_id) != (
            principal.kind,
            principal.principal_id,
        ):
            raise ValueError("runtime principal does not match Agent run")

        registry_generation, tools = self._registry.snapshot_catalog()
        tool = next((item for item in tools if item.name == tool_name), None)
        if tool is None:
            raise KeyError(tool_name)
        spec = tool.spec
        args_digest = canonical_args_digest(arguments)
        durable_target_ref, concurrency_key = self._resolve_binding(
            tool=tool,
            trusted_context=trusted_context,
            principal=principal,
            run_id=run_id,
            step_id=step_id,
            call_id=call_id,
            registry_generation=registry_generation,
            arguments=arguments,
            caller_target_ref=target_ref,
        )
        approval_ref_digest = (
            approval.approval_ref_digest if approval is not None else ""
        )
        await self._ledger.create_tool_call(
            call_id=call_id,
            run_id=run_id,
            step_id=step_id,
            tool_name=spec.name,
            tool_version=spec.version,
            owner=spec.owner,
            effect=spec.effect.value,
            principal_kind=principal.kind,
            principal_id=principal.principal_id,
            target_ref=durable_target_ref,
            args_digest=args_digest,
            idempotency_mode=spec.idempotency.value,
            idempotency_key_digest=idempotency_key_digest,
            approval_ref_digest=approval_ref_digest,
            concurrency_mode=spec.concurrency.value,
            concurrency_key=concurrency_key,
        )
        execution = await self._execute_with_cancellation_projection(
            run_id=run_id,
            call_id=call_id,
            execution=self._executor.execute(
                call_id=call_id,
                tool=tool,
                request=ToolPolicyRequest(
                    spec=spec,
                    principal=principal,
                    args_digest=args_digest,
                    target_ref=durable_target_ref,
                    idempotency_key_digest=idempotency_key_digest,
                    approval=approval,
                    registry_generation=registry_generation,
                ),
                arguments=arguments,
                trusted_context=trusted_context,
                worker_id=worker_id,
                current_registry_generation=self._registry_generation,
                execution_guard=execution_guard,
            ),
        )
        if execution.decision.outcome == "require_approval":
            await self._ledger.transition_run(
                run_id,
                to_status="waiting_approval",
                actor="coordinator",
            )
        elif execution.result is not None:
            if execution.result.status == "failed_retryable":
                await self._ledger.transition_run(
                    run_id,
                    to_status="waiting_retry",
                    actor="coordinator",
                )
            elif execution.result.status == "unknown":
                await self._ledger.transition_run(
                    run_id,
                    to_status="waiting_external",
                    actor="coordinator",
                )
        return execution

    async def resume_approved_tool(
        self,
        *,
        run_id: str,
        call_id: str,
        principal: RuntimePrincipal,
        arguments: Mapping[str, Any],
        approval: ApprovalGrant,
        trusted_context: TrustedToolContext,
        worker_id: str,
        approval_actor: str,
        execution_guard: ExecutionGuard | None = None,
    ) -> EffectExecution:
        run = await self._ledger.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status != "waiting_approval":
            raise ValueError(f"run is not waiting for approval: {run.status}")
        if (run.principal_kind, run.principal_id) != (
            principal.kind,
            principal.principal_id,
        ):
            raise ValueError("runtime principal does not match Agent run")
        call = await self._ledger.get_tool_call(call_id)
        if call is None or call.run_id != run_id:
            raise KeyError(call_id)
        if call.status != "approval_pending":
            raise ValueError(f"tool call is not approval_pending: {call.status}")

        registry_generation, tools = self._registry.snapshot_catalog()
        if registry_generation != run.registry_generation:
            raise ValueError("registry generation changed while waiting for approval")
        tool = next((item for item in tools if item.name == call.tool_name), None)
        if tool is None:
            raise KeyError(call.tool_name)
        args_digest = canonical_args_digest(arguments)
        if args_digest != call.args_digest:
            raise ValueError("approved arguments do not match durable tool call")
        self._verify_resumed_binding(
            call=call,
            tool=tool,
            trusted_context=trusted_context,
            principal=principal,
            registry_generation=registry_generation,
            arguments=arguments,
        )
        await self._ledger.record_tool_call_approval(
            call_id,
            approval_ref_digest=approval.approval_ref_digest,
            actor=approval_actor,
        )
        await self._ledger.transition_run(
            run_id,
            to_status="running",
            actor="coordinator",
        )
        execution = await self._execute_with_cancellation_projection(
            run_id=run_id,
            call_id=call_id,
            execution=self._executor.execute(
                call_id=call_id,
                tool=tool,
                request=ToolPolicyRequest(
                    spec=tool.spec,
                    principal=principal,
                    args_digest=args_digest,
                    target_ref=call.target_ref,
                    idempotency_key_digest=call.idempotency_key_digest,
                    approval=approval,
                    registry_generation=registry_generation,
                ),
                arguments=arguments,
                trusted_context=trusted_context,
                worker_id=worker_id,
                current_registry_generation=self._registry_generation,
                execution_guard=execution_guard,
            ),
        )
        if execution.result is not None:
            if execution.result.status == "failed_retryable":
                await self._ledger.transition_run(
                    run_id,
                    to_status="waiting_retry",
                    actor="coordinator",
                )
            elif execution.result.status == "unknown":
                await self._ledger.transition_run(
                    run_id,
                    to_status="waiting_external",
                    actor="coordinator",
                )
        return execution

    async def resume_retryable_tool(
        self,
        *,
        run_id: str,
        call_id: str,
        principal: RuntimePrincipal,
        arguments: Mapping[str, Any],
        trusted_context: TrustedToolContext,
        worker_id: str,
        execution_guard: ExecutionGuard | None = None,
    ) -> EffectExecution:
        run = await self._ledger.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status != "waiting_retry":
            raise ValueError(f"run is not waiting for retry: {run.status}")
        if (run.principal_kind, run.principal_id) != (
            principal.kind,
            principal.principal_id,
        ):
            raise ValueError("runtime principal does not match Agent run")
        call = await self._ledger.get_tool_call(call_id)
        if call is None or call.run_id != run_id:
            raise KeyError(call_id)
        if call.status != "failed_retryable":
            raise ValueError(f"tool call is not failed_retryable: {call.status}")
        if call.effect in {"external_reversible", "external_irreversible"}:
            raise ValueError("external effect cannot use automatic retry")

        registry_generation, tools = self._registry.snapshot_catalog()
        if registry_generation != run.registry_generation:
            raise ValueError("registry generation changed while waiting for retry")
        tool = next((item for item in tools if item.name == call.tool_name), None)
        if tool is None:
            raise KeyError(call.tool_name)
        args_digest = canonical_args_digest(arguments)
        if args_digest != call.args_digest:
            raise ValueError("retry arguments do not match durable tool call")
        self._verify_resumed_binding(
            call=call,
            tool=tool,
            trusted_context=trusted_context,
            principal=principal,
            registry_generation=registry_generation,
            arguments=arguments,
        )
        await self._ledger.transition_run(
            run_id,
            to_status="running",
            actor="coordinator",
        )
        execution = await self._execute_with_cancellation_projection(
            run_id=run_id,
            call_id=call_id,
            execution=self._executor.execute(
                call_id=call_id,
                tool=tool,
                request=ToolPolicyRequest(
                    spec=tool.spec,
                    principal=principal,
                    args_digest=args_digest,
                    target_ref=call.target_ref,
                    idempotency_key_digest=call.idempotency_key_digest,
                    registry_generation=registry_generation,
                ),
                arguments=arguments,
                trusted_context=trusted_context,
                worker_id=worker_id,
                current_registry_generation=self._registry_generation,
                execution_guard=execution_guard,
            ),
        )
        if execution.result is not None:
            if execution.result.status == "failed_retryable":
                await self._ledger.transition_run(
                    run_id,
                    to_status="waiting_retry",
                    actor="coordinator",
                )
            elif execution.result.status == "unknown":
                await self._ledger.transition_run(
                    run_id,
                    to_status="waiting_external",
                    actor="coordinator",
                )
        return execution

    def _registry_generation(self) -> int:
        generation, _tools = self._registry.snapshot_catalog()
        return generation

    async def _execute_with_cancellation_projection(
        self,
        *,
        run_id: str,
        call_id: str,
        execution: Awaitable[EffectExecution],
    ) -> EffectExecution:
        try:
            return await execution
        except asyncio.CancelledError:
            await _await_required_cleanup(
                self._project_cancelled_run(
                    run_id=run_id,
                    call_id=call_id,
                )
            )
            raise

    async def _project_cancelled_run(
        self,
        *,
        run_id: str,
        call_id: str,
    ) -> None:
        call = await self._ledger.get_tool_call(call_id)
        run = await self._ledger.get_run(run_id)
        if (
            call is not None
            and call.status == "unknown"
            and run is not None
            and run.status == "running"
        ):
            await self._ledger.transition_run(
                run_id,
                to_status="waiting_external",
                actor="coordinator",
                metadata={"reason": "cancelled_tool_outcome_unknown"},
            )
        elif (
            call is not None
            and call.status == "cancelled"
            and run is not None
            and run.status == "running"
        ):
            await self._ledger.transition_run(
                run_id,
                to_status="cancelled",
                actor="coordinator",
                metadata={"reason": "cancelled_before_dispatch"},
            )

    def _verify_resumed_binding(
        self,
        *,
        call: ToolCallRecord,
        tool: Tool,
        trusted_context: TrustedToolContext,
        principal: RuntimePrincipal,
        registry_generation: int,
        arguments: Mapping[str, Any],
    ) -> None:
        if not tool.spec.binding_required:
            return
        try:
            target_ref, concurrency_key = self._resolve_binding(
                tool=tool,
                trusted_context=trusted_context,
                principal=principal,
                run_id=call.run_id,
                step_id=call.step_id,
                call_id=call.call_id,
                registry_generation=registry_generation,
                arguments=arguments,
                caller_target_ref="",
                durable_target_ref=call.target_ref,
            )
        except ValueError as exc:
            raise ValueError("tool binding changed while waiting") from exc
        if (
            target_ref != call.target_ref
            or concurrency_key != call.concurrency_key
        ):
            raise ValueError("tool binding changed while waiting")

    def _resolve_binding(
        self,
        *,
        tool: Tool,
        trusted_context: TrustedToolContext,
        principal: RuntimePrincipal,
        run_id: str,
        step_id: str,
        call_id: str,
        registry_generation: int,
        arguments: Mapping[str, Any],
        caller_target_ref: str,
        durable_target_ref: str = "",
    ) -> tuple[str, str]:
        """Derive durable target/key before ToolCall creation.

        When ``binding_required`` is true, only TrustedToolContext plus run
        metadata may feed ``bind_invocation``. A nonempty caller target is an
        assertion that must match the tool-derived target.
        """
        spec = tool.spec
        claimed_target = str(caller_target_ref or "").strip()
        if not spec.binding_required:
            concurrency_key = self._concurrency_key(
                spec.concurrency,
                spec.concurrency_key_template,
                arguments,
            )
            return claimed_target, concurrency_key

        bind_ctx = ToolContext(
            bot=trusted_context.bot,
            user_id=trusted_context.user_id,
            group_id=trusted_context.group_id,
            session_id=trusted_context.session_id,
            extra=dict(trusted_context.extra),
            principal_kind=principal.kind,
            principal_id=principal.principal_id,
            run_id=run_id,
            step_id=step_id,
            call_id=call_id,
            target_ref=str(durable_target_ref or "").strip(),
            auth_context_ref=trusted_context.auth_context_ref,
            approval_ref=trusted_context.approval_ref,
            idempotency_key=trusted_context.idempotency_key,
            trace_id=trusted_context.trace_id,
            registry_generation=registry_generation,
        )
        binding = tool.bind_invocation(bind_ctx, dict(arguments))
        target_ref = str(binding.target_ref or "").strip()
        concurrency_key = str(binding.concurrency_key or "").strip()
        if not target_ref:
            raise ValueError("binding_required tool produced empty target_ref")
        if (
            spec.concurrency is ToolConcurrency.KEYED_SERIAL
            and not concurrency_key
        ):
            raise ValueError(
                "binding_required keyed_serial tool produced empty concurrency_key"
            )
        if claimed_target and claimed_target != target_ref:
            raise ValueError("target_ref does not match tool-derived binding")
        if concurrency_key and len(concurrency_key) > 256:
            raise ValueError("unsafe concurrency key value")
        if target_ref and len(target_ref) > 256:
            raise ValueError("unsafe target_ref value")
        return target_ref, concurrency_key

    @staticmethod
    def _concurrency_key(
        concurrency: ToolConcurrency,
        template: str,
        arguments: Mapping[str, Any],
    ) -> str:
        if concurrency is not ToolConcurrency.KEYED_SERIAL:
            return ""
        try:
            parts: list[str] = []
            for literal, field_name, format_spec, conversion in Formatter().parse(
                template
            ):
                parts.append(literal)
                if field_name is None:
                    continue
                if (
                    not field_name.isidentifier()
                    or format_spec
                    or conversion is not None
                ):
                    raise ValueError("unsafe concurrency key template")
                value = arguments[field_name]
                if isinstance(value, bool) or not isinstance(value, (str, int)):
                    raise ValueError("unsafe concurrency key value")
                rendered = str(value).strip()
                if not rendered:
                    raise ValueError("unsafe concurrency key value")
                parts.append(rendered)
            concurrency_key = "".join(parts)
        except KeyError as exc:
            raise ValueError("cannot resolve keyed_serial concurrency key") from exc
        if not concurrency_key or len(concurrency_key) > 256:
            raise ValueError("unsafe concurrency key value")
        return concurrency_key
