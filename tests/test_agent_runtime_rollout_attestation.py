"""RED contracts for attested rollout and rollback readiness."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from services.agent_runtime.ledger import AgentRuntimeLedger
from services.agent_runtime.rollout_readiness import RolloutReadinessV1
from services.memory.governance_store import MemoryGovernanceStore

CONTRACT_VERSION = "rollout_readiness.v1"
EVIDENCE_AT = datetime(2026, 7, 21, 6, 30, tzinfo=UTC)
ACTIVATION_GATES = (
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
ROLLBACK_GATES = (
    "dark_code_removal",
    "production_database_rollback",
    "production_migration_rollback",
    "production_runtime_wiring_rollback",
)
GATE_STATUSES = frozenset({"ready", "not_ready", "not_assessed"})
LEGACY_UNATTESTED_CLAIMS = frozenset(
    {
        "dark_code_removable",
        "production_database_present",
        "production_migration_present",
        "production_runtime_wiring_present",
    }
)
SENSITIVE_CANARIES = (
    "ATTESTOR_SECRET_CANARY",
    "/private/rollout/source.db",
    "PRINCIPAL_ATTESTATION_CANARY",
    "TARGET_ATTESTATION_CANARY",
    "RAW_EVIDENCE_ATTESTATION_CANARY",
)


@pytest.fixture
async def opened_sources(
    tmp_path: Path,
) -> AsyncIterator[tuple[AgentRuntimeLedger, MemoryGovernanceStore]]:
    runtime = AgentRuntimeLedger(tmp_path / "runtime-attestation.db")
    memory = MemoryGovernanceStore(tmp_path / "memory-attestation.db")
    await runtime.init()
    await memory.init()
    try:
        yield runtime, memory
    finally:
        await runtime.close()
        await memory.close()


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        assert isinstance(dumped, Mapping)
        return dumped
    pytest.fail(f"expected a mapping-like DTO, got {type(value).__name__}")


def _json_tree(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_tree(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_tree(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_tree(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return str(value)
    return getattr(value, "value", value)


def _serialized(value: Any) -> str:
    return json.dumps(
        _json_tree(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _recursive_keys(value: Any) -> set[str]:
    tree = _json_tree(value)
    if isinstance(tree, Mapping):
        return set(tree) | {
            key for item in tree.values() for key in _recursive_keys(item)
        }
    if isinstance(tree, list):
        return {key for item in tree for key in _recursive_keys(item)}
    return set()


def _parse_timestamp(value: Any) -> datetime:
    assert isinstance(value, str) and value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None and parsed.utcoffset() is not None
    return parsed.astimezone(UTC)


def _assert_common(
    report: Mapping[str, Any],
    *,
    status: str,
    activation_authorized: bool,
) -> None:
    assert report["contract_version"] == CONTRACT_VERSION
    assert report["schema_version"] == 1
    assert report["mode"] == "offline_dark"
    assert report["report_only"] is True
    assert report["status"] == status
    assert report["activation_authorized"] is activation_authorized
    serialized = _serialized(report)
    assert all(canary not in serialized for canary in SENSITIVE_CANARIES)


def _assert_gate(
    value: Any,
    *,
    status: str,
    reason: str,
    evidence_at: datetime | None,
) -> None:
    gate = _mapping(value)
    assert set(gate) == {"status", "reason", "evidence_at"}
    assert gate["status"] == status
    assert gate["status"] in GATE_STATUSES
    assert gate["reason"] == reason
    assert isinstance(gate["reason"], str) and 1 <= len(gate["reason"]) <= 120
    assert gate["reason"].isascii()
    assert all(
        character.islower() or character.isdigit() or character == "_"
        for character in gate["reason"]
    )
    if evidence_at is None:
        assert gate["evidence_at"] is None
    else:
        assert _parse_timestamp(gate["evidence_at"]) == evidence_at


def _gate_map(
    report: Mapping[str, Any],
    expected_names: Sequence[str],
) -> Mapping[str, Any]:
    assert "gates" in report, "attested readiness must expose a gate map"
    gates = _mapping(report["gates"])
    assert tuple(gates) == tuple(expected_names)
    return gates


def _assert_all_unassessed(
    report: Mapping[str, Any],
    expected_names: Sequence[str],
    *,
    reason: str = "unknown",
) -> None:
    gates = _gate_map(report, expected_names)
    for gate in gates.values():
        _assert_gate(
            gate,
            status="not_assessed",
            reason=reason,
            evidence_at=None,
        )


def _attestation(
    gate_names: Sequence[str],
    *,
    statuses: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    overrides = dict(statuses or {})
    result: dict[str, dict[str, Any]] = {}
    for gate_name in gate_names:
        status = overrides.get(gate_name, "ready")
        result[gate_name] = {
            "status": status,
            "reason": {
                "ready": "verified",
                "not_ready": "missing_requirement",
                "not_assessed": "unknown",
            }[status],
            "evidence_at": None if status == "not_assessed" else EVIDENCE_AT,
        }
    return result


class _StaticAttestor:
    def __init__(
        self,
        result: Mapping[str, Any] | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    async def __call__(self) -> Mapping[str, Any]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _new_readiness(
    runtime: AgentRuntimeLedger,
    memory: MemoryGovernanceStore,
    *,
    activation_attestor: Any = None,
    rollback_attestor: Any = None,
    worldbook_source: Any = None,
) -> RolloutReadinessV1:
    readiness_type: Any = RolloutReadinessV1
    try:
        return readiness_type(
            runtime,
            memory,
            activation_attestor=activation_attestor,
            rollback_attestor=rollback_attestor,
            worldbook_source=worldbook_source,
        )
    except TypeError as exc:
        pytest.fail(
            "RolloutReadinessV1 must accept explicit optional activation_attestor, "
            "rollback_attestor, and worldbook_source keyword arguments: "
            f"{exc}"
        )


def test_constructor_exposes_only_explicit_optional_attestation_sources() -> None:
    parameters = inspect.signature(RolloutReadinessV1).parameters
    assert tuple(parameters) == (
        "runtime_source",
        "memory_source",
        "activation_attestor",
        "rollback_attestor",
        "worldbook_source",
    )
    for name in (
        "activation_attestor",
        "rollback_attestor",
        "worldbook_source",
    ):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is None


@pytest.mark.asyncio
async def test_missing_attestors_report_every_environment_gate_unknown(
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    readiness = RolloutReadinessV1(runtime, memory)

    activation = _mapping(await readiness.activation_readiness())
    _assert_common(
        activation,
        status="not_ready",
        activation_authorized=False,
    )
    _assert_all_unassessed(activation, ACTIVATION_GATES)

    rollback = _mapping(await readiness.rollback_readiness())
    _assert_common(
        rollback,
        status="not_ready",
        activation_authorized=False,
    )
    _assert_all_unassessed(rollback, ROLLBACK_GATES)
    assert LEGACY_UNATTESTED_CLAIMS.isdisjoint(_recursive_keys(rollback))
    serialized = _serialized((activation, rollback))
    assert '"status":false' not in serialized
    assert '"status":"absent"' not in serialized


@pytest.mark.asyncio
async def test_activation_attestation_folds_all_gates_and_authorization(
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    ready_attestor = _StaticAttestor(_attestation(ACTIVATION_GATES))
    ready = _mapping(
        await _new_readiness(
            runtime,
            memory,
            activation_attestor=ready_attestor,
        ).activation_readiness()
    )
    _assert_common(ready, status="ready", activation_authorized=True)
    ready_gates = _gate_map(ready, ACTIVATION_GATES)
    for gate in ready_gates.values():
        _assert_gate(
            gate,
            status="ready",
            reason="verified",
            evidence_at=EVIDENCE_AT,
        )
    assert ready_attestor.calls == 1

    blocked_name = ACTIVATION_GATES[-1]
    blocked_attestor = _StaticAttestor(
        _attestation(
            ACTIVATION_GATES,
            statuses={blocked_name: "not_ready"},
        )
    )
    blocked = _mapping(
        await _new_readiness(
            runtime,
            memory,
            activation_attestor=blocked_attestor,
        ).activation_readiness()
    )
    _assert_common(blocked, status="not_ready", activation_authorized=False)
    blocked_gates = _gate_map(blocked, ACTIVATION_GATES)
    _assert_gate(
        blocked_gates[blocked_name],
        status="not_ready",
        reason="missing_requirement",
        evidence_at=EVIDENCE_AT,
    )

    unknown_name = ACTIVATION_GATES[0]
    unknown_attestor = _StaticAttestor(
        _attestation(
            ACTIVATION_GATES,
            statuses={unknown_name: "not_assessed"},
        )
    )
    unknown = _mapping(
        await _new_readiness(
            runtime,
            memory,
            activation_attestor=unknown_attestor,
        ).activation_readiness()
    )
    _assert_common(unknown, status="not_ready", activation_authorized=False)
    unknown_gates = _gate_map(unknown, ACTIVATION_GATES)
    _assert_gate(
        unknown_gates[unknown_name],
        status="not_assessed",
        reason="unknown",
        evidence_at=None,
    )


@pytest.mark.asyncio
async def test_rollback_attestation_uses_the_same_closed_fold(
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    ready_attestor = _StaticAttestor(_attestation(ROLLBACK_GATES))
    ready = _mapping(
        await _new_readiness(
            runtime,
            memory,
            rollback_attestor=ready_attestor,
        ).rollback_readiness()
    )
    _assert_common(ready, status="ready", activation_authorized=False)
    gates = _gate_map(ready, ROLLBACK_GATES)
    for gate in gates.values():
        _assert_gate(
            gate,
            status="ready",
            reason="verified",
            evidence_at=EVIDENCE_AT,
        )
    assert LEGACY_UNATTESTED_CLAIMS.isdisjoint(_recursive_keys(ready))

    blocked_name = ROLLBACK_GATES[1]
    blocked = _mapping(
        await _new_readiness(
            runtime,
            memory,
            rollback_attestor=_StaticAttestor(
                _attestation(
                    ROLLBACK_GATES,
                    statuses={blocked_name: "not_ready"},
                )
            ),
        ).rollback_readiness()
    )
    _assert_common(blocked, status="not_ready", activation_authorized=False)
    _assert_gate(
        _gate_map(blocked, ROLLBACK_GATES)[blocked_name],
        status="not_ready",
        reason="missing_requirement",
        evidence_at=EVIDENCE_AT,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param(
            {
                **_attestation(ACTIVATION_GATES),
                "unexpected_gate": {
                    "status": "ready",
                    "reason": "verified",
                    "evidence_at": EVIDENCE_AT,
                },
            },
            id="extra-gate",
        ),
        pytest.param(
            {
                key: value
                for key, value in _attestation(ACTIVATION_GATES).items()
                if key != ACTIVATION_GATES[-1]
            },
            id="missing-gate",
        ),
        pytest.param(
            {
                **_attestation(ACTIVATION_GATES),
                ACTIVATION_GATES[0]: {
                    "status": "passed",
                    "reason": "verified",
                    "evidence_at": EVIDENCE_AT,
                },
            },
            id="open-status",
        ),
        pytest.param(
            {
                **_attestation(ACTIVATION_GATES),
                ACTIVATION_GATES[0]: {
                    "status": "ready",
                    "reason": "x" * 121 + "ATTESTOR_SECRET_CANARY",
                    "evidence_at": EVIDENCE_AT,
                },
            },
            id="unbounded-reason",
        ),
        pytest.param(
            {
                **_attestation(ACTIVATION_GATES),
                ACTIVATION_GATES[0]: {
                    "status": "ready",
                    "reason": "verified",
                    "evidence_at": "2026-07-22T06:30:00",
                },
            },
            id="naive-time",
        ),
        pytest.param(
            {
                **_attestation(ACTIVATION_GATES),
                ACTIVATION_GATES[0]: {
                    "status": "ready",
                    "reason": "verified",
                    "evidence_at": datetime.now(UTC) + timedelta(days=1),
                },
            },
            id="future-time",
        ),
    ],
)
async def test_malformed_activation_attestations_fail_closed_and_redacted(
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
    malformed: Mapping[str, Any],
) -> None:
    runtime, memory = opened_sources
    report = _mapping(
        await _new_readiness(
            runtime,
            memory,
            activation_attestor=_StaticAttestor(malformed),
        ).activation_readiness()
    )

    _assert_common(report, status="not_ready", activation_authorized=False)
    _assert_all_unassessed(
        report,
        ACTIVATION_GATES,
        reason="attestation_invalid",
    )
    assert "unexpected_gate" not in _serialized(report)


@pytest.mark.asyncio
async def test_malformed_rollback_attestation_uses_the_same_validation(
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    malformed = _attestation(ROLLBACK_GATES)
    malformed[ROLLBACK_GATES[0]] = {
        "status": "ready",
        "reason": "x" * 121 + "ATTESTOR_SECRET_CANARY",
        "evidence_at": EVIDENCE_AT,
    }
    report = _mapping(
        await _new_readiness(
            runtime,
            memory,
            rollback_attestor=_StaticAttestor(malformed),
        ).rollback_readiness()
    )

    _assert_common(report, status="not_ready", activation_authorized=False)
    _assert_all_unassessed(
        report,
        ROLLBACK_GATES,
        reason="attestation_invalid",
    )


@pytest.mark.asyncio
async def test_attestor_exceptions_are_generic_but_cancellation_propagates(
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    failing = _StaticAttestor(
        error=RuntimeError(
            "ATTESTOR_SECRET_CANARY /private/rollout/source.db "
            "PRINCIPAL_ATTESTATION_CANARY"
        )
    )
    report = _mapping(
        await _new_readiness(
            runtime,
            memory,
            rollback_attestor=failing,
        ).rollback_readiness()
    )
    _assert_common(report, status="not_ready", activation_authorized=False)
    _assert_all_unassessed(report, ROLLBACK_GATES, reason="attestation_failed")
    assert failing.calls == 1

    cancelled = _StaticAttestor(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await _new_readiness(
            runtime,
            memory,
            activation_attestor=cancelled,
        ).activation_readiness()


@pytest.mark.asyncio
async def test_dark_readiness_has_three_sources_and_missing_worldbook_fails_closed(
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    report = _mapping(await RolloutReadinessV1(runtime, memory).dark_readiness())

    _assert_common(report, status="not_ready", activation_authorized=False)
    sources = _mapping(report["sources"])
    assert tuple(sources) == ("runtime", "memory", "worldbook")
    assert _mapping(sources["runtime"])["available"] is True
    assert _mapping(sources["memory"])["available"] is True
    assert _mapping(sources["worldbook"]) == {
        "available": False,
        "schema_version": None,
        "integrity": "unknown",
        "reason": "source_unavailable",
    }
