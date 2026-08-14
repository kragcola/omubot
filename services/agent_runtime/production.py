"""Fail-closed production composition for Agent Runtime v2.

This module deliberately assembles only explicit, preflight-verified sources.
It does not start a worker: the dispatcher remains closed until the separate
single-worker ownership gate is attested and acquired.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.agent_runtime.activation import ProductionActivationProfileV1
from services.agent_runtime.admin_operator import AdminOperatorActionsFactoryV1
from services.agent_runtime.coordinator import RunCoordinator
from services.agent_runtime.executor import EffectExecutor
from services.agent_runtime.host_ingress import AuthoritativeHostTriggerIngressV1
from services.agent_runtime.invocation_store import (
    TrustedInvocationStoreV1,
    WorkerLeaseV1,
)
from services.agent_runtime.ledger import AgentRuntimeLedger
from services.agent_runtime.operator_auth import OperatorAuthorizationStoreV1
from services.agent_runtime.policy import PolicyGate, PolicyOutcome
from services.agent_runtime.reconciliation import OneBotManualAttestationAdapter
from services.agent_runtime.rollout_attestation import ProfileBoundRolloutAttestorV1
from services.agent_runtime.rollout_readiness import (
    ReadinessAttestor,
    RolloutReadinessV1,
)
from services.memory.governance_store import MemoryGovernanceStore
from services.tools.registry import ToolRegistry
from services.worldbook.governance_store import WorldbookGovernanceStore


class ProductionActivationNotReadyError(RuntimeError):
    """Raised when preflight evidence is insufficient to open the composition."""


class WorkerOwnershipError(RuntimeError):
    """Raised when this process cannot prove exclusive Runtime v2 ownership."""


def _rejected(code: str) -> str:
    return json.dumps(
        {"status": "rejected", "code": code},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _model_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _derived_id(prefix: str, *parts: str) -> str:
    material = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(material).hexdigest()[:32]}"


def _safe_tool_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > 160
        or not text.isascii()
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in text)
    ):
        raise ValueError(f"{field} is invalid")
    return text


def _ready_report(
    report: object,
    *,
    require_activation_authorization: bool = False,
) -> bool:
    if not isinstance(report, Mapping) or report.get("status") != "ready":
        return False
    return (
        not require_activation_authorization
        or report.get("activation_authorized") is True
    )


async def _await_required_cleanup(awaitable: Any) -> None:
    task = asyncio.ensure_future(awaitable)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
            continue
    task.result()
    if cancellation is not None:
        raise cancellation


async def _close_sources(sources: tuple[Any, ...]) -> None:
    cancellation: asyncio.CancelledError | None = None
    for source in reversed(sources):
        try:
            await _await_required_cleanup(source.close())
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
    if cancellation is not None:
        raise cancellation


class LLMToolDispatcherV1:
    """The sole model-tool entrypoint once production composition is selected.

    The initial composition keeps this dispatcher closed. A later ownership
    lease is responsible for making it executable, so no partially composed
    runtime can silently use the legacy registry path.
    """

    def __init__(
        self,
        *,
        profile: ProductionActivationProfileV1,
        registry: ToolRegistry,
        coordinator: RunCoordinator,
        runtime: AgentRuntimeLedger,
        invocations: TrustedInvocationStoreV1,
    ) -> None:
        self._profile = profile
        self._registry = registry
        self._coordinator = coordinator
        self._runtime = runtime
        self._invocations = invocations
        self._worker_lease: WorkerLeaseV1 | None = None

    def activate(self, lease: WorkerLeaseV1) -> None:
        if not isinstance(lease, WorkerLeaseV1):
            raise TypeError("worker lease is required")
        if lease.owner_id != self._profile.worker_id:
            raise ValueError("worker lease owner does not match activation profile")
        self._worker_lease = lease

    def deactivate(self) -> None:
        self._worker_lease = None

    async def _current_worker_lease(self) -> WorkerLeaseV1 | None:
        """Return only the exact lease that still authorizes this dispatch."""

        lease = self._worker_lease
        if lease is not None and await self._invocations.has_worker_lease(lease):
            return lease
        # A concurrent renewal may have published a different valid token.
        if self._worker_lease == lease:
            self.deactivate()
        return None

    async def dispatch_tool_uses(
        self,
        *,
        invocation_id: str | None,
        tool_uses: tuple[dict[str, Any], ...],
        bot: Any,
    ) -> list[str]:
        if not invocation_id:
            return [_rejected("trusted_invocation_required") for _ in tool_uses]
        if await self._current_worker_lease() is None:
            return [_rejected("worker_not_ready") for _ in tool_uses]
        try:
            reconstructed = await self._invocations.reconstruct(invocation_id, bot=bot)
        except (KeyError, RuntimeError, ValueError):
            return [_rejected("trusted_invocation_unavailable") for _ in tool_uses]
        if not set(reconstructed.record.granted_scopes).issubset(
            self._profile.principal_scopes
        ) or not set(reconstructed.record.allowed_target_refs).issubset(
            self._profile.allowed_target_refs
        ):
            return [_rejected("invocation_policy_mismatch") for _ in tool_uses]
        registry_generation, _tools = self._registry.snapshot_catalog()
        if registry_generation != reconstructed.record.registry_generation:
            return [_rejected("registry_generation_mismatch") for _ in tool_uses]
        try:
            run_id = await self._ensure_run(reconstructed, registry_generation)
        except (RuntimeError, ValueError):
            return [_rejected("run_unavailable") for _ in tool_uses]

        results: list[str] = []
        for index, raw_tool_use in enumerate(tool_uses):
            lease = await self._current_worker_lease()
            if lease is None:
                results.append(_rejected("worker_not_ready"))
                continue
            try:
                tool_use_id, tool_name, arguments = _normalized_tool_use(raw_tool_use)
                call_id = _derived_id(
                    "call",
                    reconstructed.record.invocation_id,
                    tool_use_id,
                    str(index),
                )
                if await self._runtime.get_tool_call(call_id) is not None:
                    raise ValueError("duplicate tool use")
                execution = await self._coordinator.execute_tool(
                    run_id=run_id,
                    call_id=call_id,
                    step_id=f"step_{index}",
                    tool_name=tool_name,
                    principal=reconstructed.principal,
                    arguments=arguments,
                    trusted_context=reconstructed.trusted_context,
                    worker_id=lease.owner_id,
                )
            except KeyError:
                results.append(_rejected("tool_unavailable"))
                continue
            except ValueError:
                results.append(_rejected("tool_request_invalid"))
                continue
            except RuntimeError:
                results.append(_rejected("governed_dispatch_failed"))
                continue

            if execution.result is not None:
                results.append(_model_payload(execution.result.to_model_payload()))
            elif execution.decision.outcome is PolicyOutcome.REQUIRE_APPROVAL:
                results.append(
                    _model_payload(
                        {
                            "status": "approval_pending",
                            "reason": execution.decision.reason.value,
                        }
                    )
                )
            else:
                results.append(
                    _model_payload(
                        {
                            "status": "denied",
                            "reason": execution.decision.reason.value,
                        }
                    )
                )
        return results

    async def _ensure_run(self, reconstructed: Any, registry_generation: int) -> str:
        record = reconstructed.record
        run_id = _derived_id("run", record.invocation_id)
        existing = await self._runtime.get_run(run_id)
        if existing is None:
            existing = await self._coordinator.start_run(
                run_id=run_id,
                trigger_type=record.trigger_type,
                trigger_ref=record.trigger_ref,
                principal=reconstructed.principal,
                session_id=record.session_id,
                group_id=record.group_id,
                metadata={"invocation_id": record.invocation_id},
            )
        if (
            existing.status != "running"
            or existing.registry_generation != registry_generation
            or (existing.principal_kind, existing.principal_id)
            != (reconstructed.principal.kind, reconstructed.principal.principal_id)
            or existing.trigger_ref != record.trigger_ref
        ):
            raise ValueError("persisted run does not match trusted invocation")
        return run_id


def _normalized_tool_use(raw_tool_use: object) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(raw_tool_use, Mapping):
        raise ValueError("tool use is invalid")
    tool_use_id = _safe_tool_text(raw_tool_use.get("id"), field="tool use id")
    tool_name = _safe_tool_text(raw_tool_use.get("name"), field="tool name")
    arguments = raw_tool_use.get("arguments")
    if not isinstance(arguments, Mapping):
        raise ValueError("tool arguments are invalid")
    return tool_use_id, tool_name, dict(arguments)


@dataclass(slots=True)
class ProductionRuntimeAssemblyV1:
    """Opened explicit sources and their still-disabled dispatch boundary."""

    profile: ProductionActivationProfileV1
    runtime: AgentRuntimeLedger
    memory: MemoryGovernanceStore
    worldbook: WorldbookGovernanceStore
    operators: OperatorAuthorizationStoreV1
    invocations: TrustedInvocationStoreV1
    registry: ToolRegistry
    coordinator: RunCoordinator
    executor: EffectExecutor
    dispatcher: LLMToolDispatcherV1
    readiness: RolloutReadinessV1
    _worker_lease: WorkerLeaseV1 | None = field(default=None, init=False, repr=False)
    _worker_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    async def _require_worker_start_readiness(self) -> None:
        """Reject startup unless all report-only rollout checks are explicit and ready."""

        try:
            dark = await self.readiness.dark_readiness()
            activation = await self.readiness.activation_readiness()
            rollback = await self.readiness.rollback_readiness()
        except Exception:
            raise ProductionActivationNotReadyError(
                "production rollout readiness is unavailable"
            ) from None

        if not (
            _ready_report(dark)
            and _ready_report(activation, require_activation_authorization=True)
            and _ready_report(rollback)
        ):
            raise ProductionActivationNotReadyError(
                "production rollout readiness is not ready"
            )

    async def _require_worker_start_preflight(self) -> None:
        """Recheck caller-owned source and backup integrity immediately before lease."""

        try:
            report = await self.profile.preflight()
        except Exception:
            raise ProductionActivationNotReadyError(
                "production source preflight is unavailable"
            ) from None
        if not _ready_report(report):
            raise ProductionActivationNotReadyError(
                "production source preflight is not ready"
            )

    async def start_worker(
        self,
        *,
        lease_ttl_seconds: float = 30.0,
        now: Any = None,
        exclusive_recovery: bool = True,
    ) -> list[Any]:
        """Acquire ownership, perform exclusive recovery, then enable dispatch."""

        if exclusive_recovery is not True:
            raise ValueError("worker startup requires exclusive recovery")
        async with self._worker_lock:
            if self._closed:
                raise RuntimeError("production composition is closed")
            current = self._worker_lease
            if current is not None:
                if await self.invocations.has_worker_lease(current, now=now):
                    return []
                self._worker_lease = None
                self.dispatcher.deactivate()
            await self._require_worker_start_readiness()
            await self._require_worker_start_preflight()
            lease = await self.invocations.acquire_worker_lease(
                worker_id=self.profile.worker_id,
                lease_ttl_seconds=lease_ttl_seconds,
                now=now,
            )
            if lease is None:
                raise WorkerOwnershipError("production worker lease is unavailable")
            try:
                recovered = await self.runtime.recover_incomplete_tool_calls(
                    actor=lease.owner_id,
                    exclusive_startup=True,
                )
            except BaseException:
                await _await_required_cleanup(
                    self.invocations.release_worker_lease(lease)
                )
                raise
            self._worker_lease = lease
            self.dispatcher.activate(lease)
            return recovered

    async def renew_worker(
        self,
        *,
        lease_ttl_seconds: float = 30.0,
        now: Any = None,
    ) -> WorkerLeaseV1:
        """Renew the active lease or fail closed before the next dispatch."""

        async with self._worker_lock:
            lease = self._worker_lease
            if lease is None:
                raise WorkerOwnershipError("production worker lease is unavailable")
            renewed = await self.invocations.renew_worker_lease(
                lease,
                lease_ttl_seconds=lease_ttl_seconds,
                now=now,
            )
            if renewed is None:
                self._worker_lease = None
                self.dispatcher.deactivate()
                raise WorkerOwnershipError("production worker lease was lost")
            self._worker_lease = renewed
            self.dispatcher.activate(renewed)
            return renewed

    async def stop_worker(self) -> None:
        """Close dispatch before releasing ownership so a stale worker cannot run."""

        async with self._worker_lock:
            lease = self._worker_lease
            self._worker_lease = None
            self.dispatcher.deactivate()
            if lease is not None:
                await _await_required_cleanup(
                    self.invocations.release_worker_lease(lease)
                )

    def create_admin_operator_actions_factory(self) -> AdminOperatorActionsFactoryV1:
        """Expose Admin actions only from this already-attested composition."""

        if self._closed:
            raise RuntimeError("production composition is closed")
        return AdminOperatorActionsFactoryV1(
            runtime_source=self.runtime,
            memory_source=self.memory,
            operator_source=self.operators,
            reconciliation_adapters=(OneBotManualAttestationAdapter(),),
        )

    def create_host_trigger_ingress(self) -> AuthoritativeHostTriggerIngressV1:
        """Expose the OneBot ingress only from an attested composition."""

        if self._closed:
            raise RuntimeError("production composition is closed")
        return AuthoritativeHostTriggerIngressV1(
            invocations=self.invocations,
            registry=self.registry,
            granted_scopes=self.profile.principal_scopes,
            allowed_target_refs=self.profile.allowed_target_refs,
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.stop_worker()
        finally:
            await _close_sources(
                (
                    self.runtime,
                    self.memory,
                    self.worldbook,
                    self.operators,
                    self.invocations,
                )
            )


async def compose_production_runtime_from_settings(
    settings: Any,
    *,
    repo_root: str | Path,
    registry: ToolRegistry,
    llm_client: Any | None = None,
    activation_attestor: ReadinessAttestor | None = None,
    rollback_attestor: ReadinessAttestor | None = None,
) -> ProductionRuntimeAssemblyV1 | None:
    """Build disabled-safe production composition from the explicit config."""

    profile = ProductionActivationProfileV1.from_settings(
        settings,
        repo_root=repo_root,
    )
    if profile is None:
        return None
    return await compose_production_runtime(
        profile,
        registry=registry,
        llm_client=llm_client,
        activation_attestor=activation_attestor,
        rollback_attestor=rollback_attestor,
    )


async def compose_production_runtime(
    profile: ProductionActivationProfileV1,
    *,
    registry: ToolRegistry,
    llm_client: Any | None = None,
    activation_attestor: ReadinessAttestor | None = None,
    rollback_attestor: ReadinessAttestor | None = None,
) -> ProductionRuntimeAssemblyV1:
    """Open only preflight-ready caller-owned sources without starting work."""

    if not isinstance(profile, ProductionActivationProfileV1):
        raise TypeError("profile must be a ProductionActivationProfileV1")
    if not isinstance(registry, ToolRegistry):
        raise TypeError("registry must be a ToolRegistry")
    if activation_attestor is not None or rollback_attestor is not None:
        raise ValueError(
            "production rollout attestation cannot be overridden by callbacks"
        )
    if profile.attestation is None:
        raise ValueError(
            "production activation requires a digest-pinned attestation manifest"
        )
    attestor = ProfileBoundRolloutAttestorV1(profile)
    activation_attestor = attestor.activation_attestation
    rollback_attestor = attestor.rollback_attestation

    report = await profile.preflight()
    if report["status"] != "ready":
        raise ProductionActivationNotReadyError("production source preflight is not ready")

    runtime = AgentRuntimeLedger(profile.source("runtime").database_path)
    memory = MemoryGovernanceStore(profile.source("memory").database_path)
    worldbook = WorldbookGovernanceStore(profile.source("worldbook").database_path)
    operators = OperatorAuthorizationStoreV1(profile.source("operator").database_path)
    invocations = TrustedInvocationStoreV1(
        profile.source("invocation").database_path
    )
    sources = (runtime, memory, worldbook, operators, invocations)

    try:
        for source in sources:
            await source.init()
        executor = EffectExecutor(ledger=runtime, policy_gate=PolicyGate())
        coordinator = RunCoordinator(
            ledger=runtime,
            registry=registry,
            executor=executor,
        )
        dispatcher = LLMToolDispatcherV1(
            profile=profile,
            registry=registry,
            coordinator=coordinator,
            runtime=runtime,
            invocations=invocations,
        )
        readiness = RolloutReadinessV1(
            runtime,
            memory,
            activation_attestor=activation_attestor,
            rollback_attestor=rollback_attestor,
            worldbook_source=worldbook,
        )
        assembly = ProductionRuntimeAssemblyV1(
            profile=profile,
            runtime=runtime,
            memory=memory,
            worldbook=worldbook,
            operators=operators,
            invocations=invocations,
            registry=registry,
            coordinator=coordinator,
            executor=executor,
            dispatcher=dispatcher,
            readiness=readiness,
        )
        if llm_client is not None:
            setter = getattr(llm_client, "set_runtime_tool_dispatcher", None)
            if not callable(setter):
                raise TypeError("llm_client must support runtime tool dispatcher wiring")
            setter(dispatcher)
        return assembly
    except BaseException:
        await _close_sources(sources)
        raise


__all__ = [
    "LLMToolDispatcherV1",
    "ProductionActivationNotReadyError",
    "ProductionRuntimeAssemblyV1",
    "WorkerOwnershipError",
    "compose_production_runtime",
    "compose_production_runtime_from_settings",
]
