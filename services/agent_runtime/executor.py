"""Dark governed effect execution for Agent Runtime v2."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from kernel.types import (
    Tool,
    ToolConcurrency,
    ToolContext,
    ToolEffect,
    ToolError,
    ToolExecutionError,
    ToolResult,
    ToolResultStatus,
    ToolRetryPolicy,
)
from services.agent_runtime.ledger import (
    AgentRuntimeLedger,
    ConcurrencyBusyError,
    ToolCallRecord,
)
from services.agent_runtime.policy import (
    PolicyDecision,
    PolicyGate,
    PolicyOutcome,
    PolicyReason,
    ToolPolicyRequest,
    canonical_args_digest,
)
from services.group.outbound_access_guard import GroupOutboundPolicyDeniedError

ExecutionGuardResult = AbstractAsyncContextManager[None] | Awaitable[bool]
ExecutionGuard = Callable[[int], ExecutionGuardResult] | Callable[[], ExecutionGuardResult]


class _ExecutionFenceError(RuntimeError):
    """A provider entry fence could not reserve current worker ownership."""


def _call_execution_guard(
    execution_guard: ExecutionGuard,
    *,
    timeout_ms: int,
) -> ExecutionGuardResult:
    """Call timeout-aware guards while preserving legacy zero-argument guards."""

    try:
        guard_signature = inspect.signature(execution_guard)
    except (TypeError, ValueError):
        return cast(Callable[[int], ExecutionGuardResult], execution_guard)(timeout_ms)
    try:
        guard_signature.bind(timeout_ms)
    except TypeError:
        try:
            guard_signature.bind()
        except TypeError:
            return cast(Callable[[int], ExecutionGuardResult], execution_guard)(
                timeout_ms
            )
        return cast(Callable[[], ExecutionGuardResult], execution_guard)()
    return cast(Callable[[int], ExecutionGuardResult], execution_guard)(timeout_ms)


@asynccontextmanager
async def _execution_guard_scope(
    execution_guard: ExecutionGuard,
    *,
    timeout_ms: int,
) -> AsyncIterator[None]:
    """Enter the worker fence before provider code can observe a call.

    Runtime v2 uses the context-manager form so the caller can keep a
    process-local ownership lock for the complete provider call.  Awaitable
    boolean guards remain accepted only for older direct executor callers;
    they have no reservation semantics and are fail-closed.
    """

    try:
        candidate = _call_execution_guard(execution_guard, timeout_ms=timeout_ms)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise _ExecutionFenceError("worker execution fence is unavailable") from exc

    if isinstance(candidate, AbstractAsyncContextManager):
        context = candidate
        try:
            await context.__aenter__()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _ExecutionFenceError("worker execution fence was rejected") from exc
        try:
            yield
        except BaseException as exc:
            try:
                suppress = await context.__aexit__(
                    type(exc),
                    exc,
                    exc.__traceback__,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exit_exc:
                raise _ExecutionFenceError(
                    "worker execution fence could not close"
                ) from exit_exc
            if suppress:
                raise _ExecutionFenceError(
                    "worker execution fence must not suppress provider failure"
                ) from exc
            raise
        else:
            try:
                await context.__aexit__(None, None, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise _ExecutionFenceError(
                    "worker execution fence could not close"
                ) from exc
        return

    try:
        allowed = await candidate
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise _ExecutionFenceError("worker execution fence is unavailable") from exc
    if allowed is not True:
        raise _ExecutionFenceError("worker execution fence was rejected")
    yield


@dataclass(frozen=True, slots=True)
class TrustedToolContext:
    """Caller identity reconstructed by the runtime, never by the model."""

    user_id: str
    bot: Any = None
    group_id: str | None = None
    session_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    auth_context_ref: str = ""
    approval_ref: str = ""
    idempotency_key: str = ""
    trace_id: str = ""


@dataclass(frozen=True, slots=True)
class EffectExecution:
    decision: PolicyDecision
    result: ToolResult | None


async def _await_required_cleanup(awaitable: Coroutine[Any, Any, Any]) -> None:
    """Keep repeated cancellation from detaching a required state write."""

    task = asyncio.create_task(awaitable, name="agent-runtime-executor-cleanup")
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    task.result()


class ExecutionConcurrencyGate:
    """Process-local serialization shared by one runtime coordinator."""

    def __init__(self) -> None:
        self._global_lock = asyncio.Lock()
        self._keyed_locks: dict[str, asyncio.Lock] = {}

    def lock_for(
        self,
        concurrency: ToolConcurrency,
        concurrency_key: str,
    ) -> asyncio.Lock | None:
        if concurrency is ToolConcurrency.PARALLEL:
            return None
        if concurrency is ToolConcurrency.GLOBAL_SERIAL:
            return self._global_lock
        key = str(concurrency_key or "").strip()
        if not key:
            raise ValueError("keyed_serial call requires a durable concurrency_key")
        return self._keyed_locks.setdefault(key, asyncio.Lock())


class EffectExecutor:
    """Mediate a proposed ToolCall before any tool code can run."""

    def __init__(
        self,
        *,
        ledger: AgentRuntimeLedger,
        policy_gate: PolicyGate,
        concurrency_gate: ExecutionConcurrencyGate | None = None,
    ) -> None:
        self._ledger = ledger
        self._policy_gate = policy_gate
        self._concurrency_gate = concurrency_gate or ExecutionConcurrencyGate()

    async def execute(
        self,
        *,
        call_id: str,
        tool: Tool,
        request: ToolPolicyRequest,
        arguments: Mapping[str, Any],
        trusted_context: TrustedToolContext,
        worker_id: str,
        current_registry_generation: int | Callable[[], int],
        execution_guard: ExecutionGuard | None = None,
    ) -> EffectExecution:
        call = await self._ledger.get_tool_call(call_id)
        if call is None:
            raise KeyError(call_id)
        run = await self._ledger.get_run(call.run_id)
        if run is None:
            raise RuntimeError(f"tool call run is missing: {call.run_id}")
        mismatched_fields = self._binding_mismatches(
            call=call,
            run_registry_generation=run.registry_generation,
            tool=tool,
            request=request,
            arguments=arguments,
        )
        if mismatched_fields:
            decision = PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason=PolicyReason.CALL_SNAPSHOT_MISMATCH,
            )
            metadata = decision.to_event_metadata(request)
            metadata["mismatched_fields"] = mismatched_fields
            await self._ledger.transition_tool_call(
                call_id,
                to_status="denied",
                actor="runtime",
                metadata=metadata,
                error_code=decision.reason.value,
            )
            return EffectExecution(decision=decision, result=None)

        decision = self._policy_gate.evaluate(
            request,
            current_registry_generation=self._current_generation(
                current_registry_generation
            ),
        )
        metadata = decision.to_event_metadata(request)
        if decision.outcome is PolicyOutcome.DENY:
            await self._ledger.transition_tool_call(
                call_id,
                to_status="denied",
                actor="policy",
                metadata=metadata,
                error_code=decision.reason.value,
            )
            return EffectExecution(decision=decision, result=None)

        validation_failure = self._input_validation_failure(request, arguments)
        if validation_failure is not None:
            reason, validation_metadata = validation_failure
            decision = PolicyDecision(outcome=PolicyOutcome.DENY, reason=reason)
            metadata = decision.to_event_metadata(request)
            metadata["validation"] = validation_metadata
            await self._ledger.transition_tool_call(
                call_id,
                to_status="denied",
                actor="runtime",
                metadata=metadata,
                error_code=decision.reason.value,
            )
            return EffectExecution(decision=decision, result=None)

        if decision.outcome is PolicyOutcome.REQUIRE_APPROVAL:
            await self._ledger.transition_tool_call(
                call_id,
                to_status="approval_pending",
                actor="policy",
                metadata=metadata,
            )
            return EffectExecution(decision=decision, result=None)

        lock = self._concurrency_gate.lock_for(
            request.spec.concurrency,
            call.concurrency_key,
        )
        try:
            if lock is None:
                return await self._dispatch(
                    call=call,
                    tool=tool,
                    request=request,
                    arguments=arguments,
                    trusted_context=trusted_context,
                    worker_id=worker_id,
                    decision=decision,
                    policy_metadata=metadata,
                    current_registry_generation=current_registry_generation,
                    execution_guard=execution_guard,
                )
            async with lock:
                return await self._dispatch(
                    call=call,
                    tool=tool,
                    request=request,
                    arguments=arguments,
                    trusted_context=trusted_context,
                    worker_id=worker_id,
                    decision=decision,
                    policy_metadata=metadata,
                    current_registry_generation=current_registry_generation,
                    execution_guard=execution_guard,
                )
        except asyncio.CancelledError:
            await _await_required_cleanup(
                self._cancel_open_call(
                    call_id=call.call_id,
                    worker_id=worker_id,
                )
            )
            raise

    async def _cancel_open_call(
        self,
        *,
        call_id: str,
        worker_id: str,
    ) -> None:
        current = await self._ledger.get_tool_call(call_id)
        if current is not None and current.status in {
            "proposed",
            "approval_pending",
            "ready",
            "claimed",
            "dispatching",
        }:
            await self._ledger.cancel_tool_call(
                call_id,
                actor=worker_id,
                reason="executor_cancelled",
                expected_lease_owner=worker_id,
            )

    async def _dispatch(
        self,
        *,
        call: ToolCallRecord,
        tool: Tool,
        request: ToolPolicyRequest,
        arguments: Mapping[str, Any],
        trusted_context: TrustedToolContext,
        worker_id: str,
        decision: PolicyDecision,
        policy_metadata: dict[str, Any],
        current_registry_generation: int | Callable[[], int],
        execution_guard: ExecutionGuard | None,
    ) -> EffectExecution:
        decision = self._policy_gate.evaluate(
            request,
            current_registry_generation=self._current_generation(
                current_registry_generation
            ),
        )
        policy_metadata = decision.to_event_metadata(request)
        if decision.outcome is not PolicyOutcome.ALLOW:
            to_status = (
                "approval_pending"
                if decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
                else "denied"
            )
            await self._ledger.transition_tool_call(
                call.call_id,
                to_status=to_status,
                actor="policy",
                metadata=policy_metadata,
                error_code=(
                    decision.reason.value if to_status == "denied" else None
                ),
            )
            return EffectExecution(decision=decision, result=None)
        await self._ledger.transition_tool_call(
            call.call_id,
            to_status="ready",
            actor="policy",
            metadata=policy_metadata,
        )
        lease_until = (
            datetime.now(UTC) + timedelta(milliseconds=request.spec.timeout_ms)
        ).isoformat()
        try:
            await self._ledger.claim_tool_call(
                call.call_id,
                lease_owner=worker_id,
                lease_until=lease_until,
                actor=worker_id,
            )
        except ConcurrencyBusyError:
            retryable = (
                not self._is_external(request)
                and request.spec.retry_policy is ToolRetryPolicy.SAFE_TRANSIENT
            )
            return await self._finish_failure(
                call_id=call.call_id,
                worker_id=worker_id,
                decision=decision,
                status=(
                    ToolResultStatus.FAILED_RETRYABLE
                    if retryable
                    else ToolResultStatus.FAILED_TERMINAL
                ),
                error_code="concurrency_busy",
                retryable=retryable,
                safe_message=(
                    "Tool is waiting for an execution slot"
                    if retryable
                    else "Tool could not acquire its execution slot"
                ),
                started_at=datetime.now(UTC).isoformat(),
            )
        except Exception as exc:
            current = await self._ledger.get_tool_call(call.call_id)
            if current is None:
                raise RuntimeError("tool call disappeared during claim") from exc
            claim_ambiguous = current.status in {"claimed", "dispatching", "unknown"}
            retryable = (
                not claim_ambiguous
                and not self._is_external(request)
                and request.spec.retry_policy is ToolRetryPolicy.SAFE_TRANSIENT
            )
            if claim_ambiguous:
                status = ToolResultStatus.UNKNOWN
                error_code = "claim_outcome_unknown"
                safe_message = "Tool claim outcome could not be durably confirmed"
            elif retryable:
                status = ToolResultStatus.FAILED_RETRYABLE
                error_code = "claim_failed"
                safe_message = "Tool claim failed before execution"
            else:
                status = ToolResultStatus.FAILED_TERMINAL
                error_code = "claim_failed"
                safe_message = "Tool claim failed before execution"
            return await self._finish_failure(
                call_id=call.call_id,
                worker_id=worker_id,
                decision=decision,
                status=status,
                error_code=error_code,
                retryable=retryable,
                safe_message=safe_message,
                started_at=datetime.now(UTC).isoformat(),
            )
        try:
            await self._ledger.transition_tool_call(
                call.call_id,
                to_status="dispatching",
                actor=worker_id,
                expected_lease_owner=worker_id,
            )
        except Exception as exc:
            current = await self._ledger.get_tool_call(call.call_id)
            if current is None:
                raise RuntimeError(
                    "tool call disappeared during dispatch marker write"
                ) from exc
            marker_ambiguous = current.status in {"dispatching", "unknown"}
            retryable = (
                not marker_ambiguous
                and not self._is_external(request)
                and request.spec.retry_policy is ToolRetryPolicy.SAFE_TRANSIENT
            )
            if marker_ambiguous:
                status = ToolResultStatus.UNKNOWN
                error_code = "dispatch_marker_outcome_unknown"
                safe_message = "Tool dispatch marker could not be durably confirmed"
            elif retryable:
                status = ToolResultStatus.FAILED_RETRYABLE
                error_code = "dispatch_marker_failed"
                safe_message = "Tool dispatch marker failed before execution"
            else:
                status = ToolResultStatus.FAILED_TERMINAL
                error_code = "dispatch_marker_failed"
                safe_message = "Tool dispatch marker failed before execution"
            return await self._finish_failure(
                call_id=call.call_id,
                worker_id=worker_id,
                decision=decision,
                status=status,
                error_code=error_code,
                retryable=retryable,
                safe_message=safe_message,
                started_at=datetime.now(UTC).isoformat(),
            )
        started_at = datetime.now(UTC).isoformat()
        try:
            if execution_guard is None:
                async with asyncio.timeout(request.spec.timeout_ms / 1000):
                    output = await tool.execute(
                        self._build_tool_context(call, request, trusted_context),
                        **dict(arguments),
                    )
            else:
                async with _execution_guard_scope(
                    execution_guard,
                    timeout_ms=request.spec.timeout_ms,
                ):
                    async with asyncio.timeout(request.spec.timeout_ms / 1000):
                        output = await tool.execute(
                            self._build_tool_context(call, request, trusted_context),
                            **dict(arguments),
                        )
        except _ExecutionFenceError:
            return await self._finish_failure(
                call_id=call.call_id,
                worker_id=worker_id,
                decision=decision,
                status=ToolResultStatus.FAILED_TERMINAL,
                error_code="worker_not_ready",
                retryable=False,
                safe_message="Runtime worker ownership is no longer current",
                started_at=started_at,
            )
        except asyncio.CancelledError:
            await _await_required_cleanup(
                self._ledger.cancel_tool_call(
                    call.call_id,
                    actor=worker_id,
                    reason="executor_cancelled",
                    expected_lease_owner=worker_id,
                )
            )
            raise
        except TimeoutError:
            externally_ambiguous = self._is_external(request)
            retryable = (
                not externally_ambiguous
                and request.spec.retry_policy is ToolRetryPolicy.SAFE_TRANSIENT
            )
            if externally_ambiguous:
                status = ToolResultStatus.UNKNOWN
                error_code = "tool_timeout_unknown"
                safe_message = "External delivery status is unknown"
            elif retryable:
                status = ToolResultStatus.FAILED_RETRYABLE
                error_code = "tool_timeout"
                safe_message = "Tool execution timed out"
            else:
                status = ToolResultStatus.FAILED_TERMINAL
                error_code = "tool_timeout"
                safe_message = "Tool execution timed out"
            return await self._finish_failure(
                call_id=call.call_id,
                worker_id=worker_id,
                decision=decision,
                status=status,
                error_code=error_code,
                retryable=retryable,
                safe_message=safe_message,
                started_at=started_at,
            )
        except ToolExecutionError as exc:
            externally_ambiguous = (
                self._is_external(request) and exc.external_effect_started
            )
            retryable = (
                not self._is_external(request)
                and exc.transient
                and request.spec.retry_policy is ToolRetryPolicy.SAFE_TRANSIENT
            )
            if externally_ambiguous:
                status = ToolResultStatus.UNKNOWN
                error_code = "tool_exception_unknown"
                safe_message = "External delivery status is unknown"
            elif retryable:
                status = ToolResultStatus.FAILED_RETRYABLE
                error_code = exc.code
                safe_message = exc.safe_message
            else:
                status = ToolResultStatus.FAILED_TERMINAL
                error_code = exc.code
                safe_message = exc.safe_message
            return await self._finish_failure(
                call_id=call.call_id,
                worker_id=worker_id,
                decision=decision,
                status=status,
                error_code=error_code,
                retryable=retryable,
                safe_message=safe_message,
                started_at=started_at,
                metadata={
                    "external_effect_started": exc.external_effect_started,
                },
            )
        except GroupOutboundPolicyDeniedError:
            return await self._finish_failure(
                call_id=call.call_id,
                worker_id=worker_id,
                decision=decision,
                status=ToolResultStatus.FAILED_TERMINAL,
                error_code="group_policy_denied",
                retryable=False,
                safe_message="Group policy denied this external action",
                started_at=started_at,
                metadata={"external_effect_started": False},
            )
        except Exception:
            externally_ambiguous = self._is_external(request)
            return await self._finish_failure(
                call_id=call.call_id,
                worker_id=worker_id,
                decision=decision,
                status=(
                    ToolResultStatus.UNKNOWN
                    if externally_ambiguous
                    else ToolResultStatus.FAILED_TERMINAL
                ),
                error_code=(
                    "tool_exception_unknown"
                    if externally_ambiguous
                    else "tool_execution_failed"
                ),
                retryable=False,
                safe_message=(
                    "External delivery status is unknown"
                    if externally_ambiguous
                    else "Tool execution failed"
                ),
                started_at=started_at,
            )

        if not self._is_model_json(output):
            externally_ambiguous = self._is_external(request)
            return await self._finish_failure(
                call_id=call.call_id,
                worker_id=worker_id,
                decision=decision,
                status=(
                    ToolResultStatus.UNKNOWN
                    if externally_ambiguous
                    else ToolResultStatus.FAILED_TERMINAL
                ),
                error_code=(
                    "output_not_json_unknown"
                    if externally_ambiguous
                    else "output_not_json"
                ),
                retryable=False,
                safe_message=(
                    "External delivery status is unknown"
                    if externally_ambiguous
                    else "Tool returned an invalid result"
                ),
                started_at=started_at,
            )
        output_validation = self._output_validation_failure(request, output)
        if output_validation is not None:
            externally_ambiguous = self._is_external(request)
            return await self._finish_failure(
                call_id=call.call_id,
                worker_id=worker_id,
                decision=decision,
                status=(
                    ToolResultStatus.UNKNOWN
                    if externally_ambiguous
                    else ToolResultStatus.FAILED_TERMINAL
                ),
                error_code=(
                    "output_schema_invalid_unknown"
                    if externally_ambiguous
                    else "output_schema_invalid"
                ),
                retryable=False,
                safe_message=(
                    "External delivery status is unknown"
                    if externally_ambiguous
                    else "Tool returned an invalid result"
                ),
                started_at=started_at,
                metadata={"validation": output_validation},
            )

        result = ToolResult(
            status=ToolResultStatus.SUCCEEDED,
            structured_output=output,
            started_at=started_at,
            ended_at=datetime.now(UTC).isoformat(),
        )
        result = await self._persist_terminal_result(
            call_id=call.call_id,
            worker_id=worker_id,
            intended_result=result,
            intended_status=ToolResultStatus.SUCCEEDED,
            started_at=started_at,
        )
        return EffectExecution(decision=decision, result=result)

    async def _finish_failure(
        self,
        *,
        call_id: str,
        worker_id: str,
        decision: PolicyDecision,
        status: ToolResultStatus,
        error_code: str,
        retryable: bool,
        safe_message: str,
        started_at: str,
        metadata: dict[str, Any] | None = None,
    ) -> EffectExecution:
        result = ToolResult(
            status=status,
            error=ToolError(
                code=error_code,
                retryable=retryable,
                safe_message=safe_message,
            ),
            started_at=started_at,
            ended_at=datetime.now(UTC).isoformat(),
        )
        result = await self._persist_terminal_result(
            call_id=call_id,
            worker_id=worker_id,
            intended_result=result,
            intended_status=status,
            started_at=started_at,
            metadata=metadata,
            error_code=error_code,
        )
        return EffectExecution(decision=decision, result=result)

    async def _persist_terminal_result(
        self,
        *,
        call_id: str,
        worker_id: str,
        intended_result: ToolResult,
        intended_status: ToolResultStatus,
        started_at: str,
        metadata: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> ToolResult:
        try:
            await self._ledger.transition_tool_call(
                call_id,
                to_status=intended_status.value,
                actor=worker_id,
                metadata=metadata,
                safe_result=intended_result.to_model_payload(),
                error_code=error_code,
                expected_lease_owner=worker_id,
            )
        except asyncio.CancelledError:
            await _await_required_cleanup(
                self._recover_terminal_write(
                    call_id=call_id,
                    worker_id=worker_id,
                    intended_result=intended_result,
                    intended_status=intended_status,
                    started_at=started_at,
                    metadata=metadata,
                    error_code=error_code,
                )
            )
            raise
        except Exception:
            return await self._recover_terminal_write(
                call_id=call_id,
                worker_id=worker_id,
                intended_result=intended_result,
                intended_status=intended_status,
                started_at=started_at,
                metadata=metadata,
                error_code=error_code,
            )
        return intended_result

    async def _recover_terminal_write(
        self,
        *,
        call_id: str,
        worker_id: str,
        intended_result: ToolResult,
        intended_status: ToolResultStatus,
        started_at: str,
        metadata: dict[str, Any] | None,
        error_code: str | None,
    ) -> ToolResult:
        current = await self._ledger.get_tool_call(call_id)
        if current is None:
            raise RuntimeError("tool call disappeared during result finalization")
        if current.status == intended_status.value:
            return intended_result
        if current.status in {"ready", "claimed"}:
            await self._ledger.transition_tool_call(
                call_id,
                to_status=intended_status.value,
                actor=worker_id,
                metadata=metadata,
                safe_result=intended_result.to_model_payload(),
                error_code=error_code,
                expected_lease_owner=worker_id,
            )
            return intended_result
        fallback = ToolResult(
            status=ToolResultStatus.UNKNOWN,
            error=ToolError(
                code="tool_result_persistence_unknown",
                retryable=False,
                safe_message="Tool completion could not be durably confirmed",
            ),
            started_at=started_at,
            ended_at=datetime.now(UTC).isoformat(),
        )
        if current.status == ToolResultStatus.UNKNOWN.value:
            return fallback
        if current.status != "dispatching":
            raise RuntimeError(
                "tool call left dispatching during result finalization"
            )
        await self._ledger.transition_tool_call(
            call_id,
            to_status=ToolResultStatus.UNKNOWN.value,
            actor=worker_id,
            metadata={"intended_status": intended_status.value},
            safe_result=fallback.to_model_payload(),
            error_code="tool_result_persistence_unknown",
            expected_lease_owner=worker_id,
        )
        return fallback

    @staticmethod
    def _build_tool_context(
        call: ToolCallRecord,
        request: ToolPolicyRequest,
        trusted_context: TrustedToolContext,
    ) -> ToolContext:
        return ToolContext(
            bot=trusted_context.bot,
            user_id=trusted_context.user_id,
            group_id=trusted_context.group_id,
            session_id=trusted_context.session_id,
            extra=dict(trusted_context.extra),
            principal_kind=request.principal.kind,
            principal_id=request.principal.principal_id,
            run_id=call.run_id,
            step_id=call.step_id,
            call_id=call.call_id,
            target_ref=call.target_ref,
            auth_context_ref=trusted_context.auth_context_ref,
            approval_ref=trusted_context.approval_ref,
            idempotency_key=trusted_context.idempotency_key,
            trace_id=trusted_context.trace_id,
            registry_generation=request.registry_generation,
        )

    @staticmethod
    def _is_external(request: ToolPolicyRequest) -> bool:
        return request.spec.effect in {
            ToolEffect.EXTERNAL_REVERSIBLE,
            ToolEffect.EXTERNAL_IRREVERSIBLE,
        }

    @staticmethod
    def _is_model_json(output: Any) -> bool:
        try:
            json.dumps(
                output,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _current_generation(source: int | Callable[[], int]) -> int:
        return int(source() if callable(source) else source)

    @staticmethod
    def _input_validation_failure(
        request: ToolPolicyRequest,
        arguments: Mapping[str, Any],
    ) -> tuple[PolicyReason, dict[str, Any]] | None:
        try:
            Draft202012Validator.check_schema(request.spec.input_schema)
        except SchemaError:
            return PolicyReason.TOOL_SCHEMA_INVALID, {
                "path": [],
                "validator": "schema",
            }
        validator = Draft202012Validator(request.spec.input_schema)
        error = next(iter(validator.iter_errors(dict(arguments))), None)
        if error is None:
            return None
        return PolicyReason.INPUT_SCHEMA_INVALID, {
            "path": list(error.absolute_path),
            "validator": str(error.validator or "unknown"),
        }

    @staticmethod
    def _output_validation_failure(
        request: ToolPolicyRequest,
        output: Any,
    ) -> dict[str, Any] | None:
        schema = request.spec.output_schema
        if schema is None:
            return None
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            return {"path": [], "validator": "schema"}
        error = next(iter(Draft202012Validator(schema).iter_errors(output)), None)
        if error is None:
            return None
        return {
            "path": list(error.absolute_path),
            "validator": str(error.validator or "unknown"),
        }

    @staticmethod
    def _binding_mismatches(
        *,
        call: ToolCallRecord,
        run_registry_generation: int,
        tool: Tool,
        request: ToolPolicyRequest,
        arguments: Mapping[str, Any],
    ) -> list[str]:
        approval_ref_digest = (
            request.approval.approval_ref_digest
            if request.approval is not None
            else ""
        )
        expected = {
            "tool_name": request.spec.name,
            "tool_version": request.spec.version,
            "owner": request.spec.owner,
            "effect": request.spec.effect.value,
            "principal_kind": request.principal.kind,
            "principal_id": request.principal.principal_id,
            "target_ref": request.target_ref,
            "args_digest": request.args_digest,
            "idempotency_mode": request.spec.idempotency.value,
            "idempotency_key_digest": request.idempotency_key_digest,
            "approval_ref_digest": approval_ref_digest,
            "concurrency_mode": request.spec.concurrency.value,
        }
        mismatches = [
            field_name
            for field_name, expected_value in expected.items()
            if getattr(call, field_name) != expected_value
        ]
        if canonical_args_digest(arguments) != request.args_digest:
            mismatches.append("arguments")
        tool_spec = tool.spec
        tool_fields = {
            "tool.name": (tool.name, request.spec.name),
            "tool_spec.name": (tool_spec.name, request.spec.name),
            "tool_spec.version": (tool_spec.version, request.spec.version),
            "tool_spec.owner": (tool_spec.owner, request.spec.owner),
            "tool_spec.effect": (tool_spec.effect, request.spec.effect),
            "tool_spec.idempotency": (
                tool_spec.idempotency,
                request.spec.idempotency,
            ),
        }
        mismatches.extend(
            field_name
            for field_name, (actual, expected_value) in tool_fields.items()
            if actual != expected_value
        )
        if tool_spec != request.spec:
            mismatches.append("tool_spec")
        if run_registry_generation != request.registry_generation:
            mismatches.append("registry_generation")
        return mismatches
