"""Report-only readiness checks for the dark Agent Runtime v2 rollout."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from services.agent_runtime.ledger import AgentRuntimeLedger
from services.memory.governance_store import MemoryGovernanceStore
from services.worldbook.governance_store import WorldbookGovernanceStore

_CONTRACT_VERSION = "rollout_readiness.v1"
_SCHEMA_VERSION = 1
_MODE = "offline_dark"
ACTIVATION_GATE_NAMES = (
    "production_principal_construction",
    "trusted_invocation_persistence",
    "trusted_invocation_reconstruction",
    "operator_identity",
    "operator_authentication",
    "production_runtime_source_path",
    "production_memory_source_path",
    "llm_client_wiring",
    "tool_registry_wiring",
    "bootstrap_wiring",
    "single_worker_gate",
    "exclusive_recovery_gate",
    "worldbook_governance_source_path",
    "worldbook_single_world_gate",
    "worldbook_authoritative_reread",
    "worldbook_reducer_verifier",
    "worldbook_no_dual_truth",
)
ROLLBACK_GATE_NAMES = (
    "dark_code_removal",
    "production_database_rollback",
    "production_migration_rollback",
    "production_runtime_wiring_rollback",
)
_GATE_STATUSES = frozenset({"ready", "not_ready", "not_assessed"})
_REASON_RE = re.compile(r"^[a-z0-9_]{1,120}$")
type ReadinessAttestor = Callable[[], Awaitable[Mapping[str, Any]]]


def _base() -> dict[str, Any]:
    return {
        "contract_version": _CONTRACT_VERSION,
        "schema_version": _SCHEMA_VERSION,
        "mode": _MODE,
        "report_only": True,
        "activation_authorized": False,
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "schema_version": None,
        "integrity": "unknown",
        "reason": reason,
    }


async def _check_source(source: object, *, schema_version: int) -> dict[str, Any]:
    db = getattr(source, "_db", None) if source is not None else None
    lock = getattr(source, "_write_lock", None) if source is not None else None
    if db is None or lock is None:
        return _unavailable("source_unavailable")
    try:
        async with lock:
            cursor = await db.execute("PRAGMA quick_check")
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
        integrity = str(row[0]) if row is not None else ""
    except Exception:
        return _unavailable("query_failed")
    if integrity != "ok":
        return _unavailable("query_failed")
    return {
        "available": True,
        "schema_version": schema_version,
        "integrity": "ok",
    }


class RolloutReadinessV1:
    """Inspect explicit dark sources without enabling or mutating the runtime."""

    def __init__(
        self,
        runtime_source: AgentRuntimeLedger | None,
        memory_source: MemoryGovernanceStore | None,
        *,
        activation_attestor: ReadinessAttestor | None = None,
        rollback_attestor: ReadinessAttestor | None = None,
        worldbook_source: WorldbookGovernanceStore | None = None,
    ) -> None:
        self._runtime_source = runtime_source
        self._memory_source = memory_source
        self._activation_attestor = activation_attestor
        self._rollback_attestor = rollback_attestor
        self._worldbook_source = worldbook_source

    async def dark_readiness(self) -> dict[str, Any]:
        runtime = await _check_source(self._runtime_source, schema_version=2)
        memory = await _check_source(self._memory_source, schema_version=1)
        worldbook = await _check_source(self._worldbook_source, schema_version=1)
        ready = all(
            source["available"] is True
            for source in (runtime, memory, worldbook)
        )
        return {
            **_base(),
            "status": "ready" if ready else "not_ready",
            "sources": {
                "runtime": runtime,
                "memory": memory,
                "worldbook": worldbook,
            },
        }

    async def activation_readiness(self) -> dict[str, Any]:
        gates = await _attested_gates(
            self._activation_attestor,
            ACTIVATION_GATE_NAMES,
        )
        ready = all(gate["status"] == "ready" for gate in gates.values())
        return {
            **_base(),
            "status": "ready" if ready else "not_ready",
            "activation_authorized": ready,
            "gates": gates,
            "blocking_gates": tuple(
                name for name, gate in gates.items() if gate["status"] != "ready"
            ),
        }

    async def rollback_readiness(self) -> dict[str, Any]:
        gates = await _attested_gates(
            self._rollback_attestor,
            ROLLBACK_GATE_NAMES,
        )
        ready = all(gate["status"] == "ready" for gate in gates.values())
        return {
            **_base(),
            "status": "ready" if ready else "not_ready",
            "gates": gates,
            "blocking_gates": tuple(
                name for name, gate in gates.items() if gate["status"] != "ready"
            ),
        }


def _unassessed_gates(
    names: tuple[str, ...],
    *,
    reason: str,
) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "status": "not_assessed",
            "reason": reason,
            "evidence_at": None,
        }
        for name in names
    }


async def _attested_gates(
    attestor: ReadinessAttestor | None,
    names: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    if attestor is None:
        return _unassessed_gates(names, reason="unknown")
    try:
        raw = await attestor()
    except Exception:
        return _unassessed_gates(names, reason="attestation_failed")
    try:
        return _validated_gates(raw, names)
    except (TypeError, ValueError):
        return _unassessed_gates(names, reason="attestation_invalid")


def _validated_gates(
    raw: Mapping[str, Any],
    names: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping) or set(raw) != set(names):
        raise ValueError("attestation gate set is invalid")
    result: dict[str, dict[str, Any]] = {}
    now = datetime.now(UTC)
    for name in names:
        value = raw.get(name)
        if not isinstance(value, Mapping) or set(value) != {
            "status",
            "reason",
            "evidence_at",
        }:
            raise ValueError("attestation gate payload is invalid")
        status = str(value.get("status") or "")
        reason = str(value.get("reason") or "")
        if status not in _GATE_STATUSES or _REASON_RE.fullmatch(reason) is None:
            raise ValueError("attestation gate value is invalid")
        evidence_at = value.get("evidence_at")
        if status == "not_assessed":
            if evidence_at is not None:
                raise ValueError("unassessed gate cannot claim evidence")
            normalized_time = None
        else:
            normalized = _aware_utc(evidence_at)
            if normalized > now:
                raise ValueError("attestation evidence cannot be in the future")
            normalized_time = normalized.isoformat().replace("+00:00", "Z")
        result[name] = {
            "status": status,
            "reason": reason,
            "evidence_at": normalized_time,
        }
    return result


def _aware_utc(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise TypeError("attestation evidence_at must be a timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("attestation evidence_at must be timezone-aware")
    return parsed.astimezone(UTC)


__all__ = [
    "ACTIVATION_GATE_NAMES",
    "ROLLBACK_GATE_NAMES",
    "ReadinessAttestor",
    "RolloutReadinessV1",
]
