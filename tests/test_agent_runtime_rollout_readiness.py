"""Behavioral RED contracts for offline Agent Runtime rollout readiness."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from services.agent_runtime.ledger import AgentRuntimeLedger
from services.memory.governance_contracts import (
    CandidateEnvelopeV1,
    CardClaimV1,
    EvidenceAtomV1,
    ObservationV1,
    OwnerScope,
    ProducerKind,
    ProjectionProposalV1,
    SourceKind,
    TimeBasis,
    Visibility,
    sha256_text,
)
from services.memory.governance_store import MemoryGovernanceStore
from services.worldbook.governance_store import WorldbookGovernanceStore

CONTRACT_VERSION = "rollout_readiness.v1"
SCHEMA_VERSION = 1
RUNTIME_SOURCE_SCHEMA_VERSION = 2
MEMORY_SOURCE_SCHEMA_VERSION = 1
WORLDBOOK_SOURCE_SCHEMA_VERSION = 1
T0 = datetime(2026, 7, 22, 6, 0, tzinfo=UTC)

ACTIVATION_BLOCKERS = frozenset(
    {
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
    }
)
ROLLBACK_GATES = frozenset(
    {
        "dark_code_removal",
        "production_database_rollback",
        "production_migration_rollback",
        "production_runtime_wiring_rollback",
    }
)

SENSITIVE_KEY_FRAGMENTS = (
    "path",
    "principal",
    "target",
    "argument",
    "args",
    "payload",
    "evidence",
    "exception",
    "traceback",
    "error",
)
ACTION_KEY_FRAGMENTS = (
    "authoriz",
    "toggle",
    "execute",
    "mutate",
    "initializ",
    "reconcil",
)


def _readiness_type(monkeypatch: pytest.MonkeyPatch) -> type[Any]:
    """Resolve the proposed module while keeping pre-implementation RED collectible."""

    module_name = "services.agent_runtime.rollout_readiness"
    if importlib.util.find_spec(module_name) is None:
        placeholder = ModuleType(module_name)

        class RolloutReadinessV1:
            def __init__(self, runtime_source: Any, memory_source: Any) -> None:
                self._runtime_source = runtime_source
                self._memory_source = memory_source

            async def dark_readiness(self) -> dict[str, Any]:
                return {**_placeholder_envelope(), "status": "not_implemented", "sources": {}}

            async def activation_readiness(self) -> dict[str, Any]:
                return {
                    **_placeholder_envelope(),
                    "status": "not_implemented",
                    "blocking_gates": (),
                }

            async def rollback_readiness(self) -> dict[str, Any]:
                return {
                    **_placeholder_envelope(),
                    "status": "not_implemented",
                    "rollback": {},
                }

        placeholder.__dict__["RolloutReadinessV1"] = RolloutReadinessV1
        monkeypatch.setitem(sys.modules, module_name, placeholder)

    module = importlib.import_module(module_name)
    readiness_type = getattr(module, "RolloutReadinessV1", None)
    assert isinstance(readiness_type, type), (
        "services.agent_runtime.rollout_readiness.RolloutReadinessV1 is required"
    )
    return readiness_type


def _placeholder_envelope() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "mode": "offline_dark",
        "report_only": True,
        "activation_authorized": False,
    }


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
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return getattr(value, "value", value)


def _serialized(value: Any) -> str:
    return json.dumps(_json_tree(value), ensure_ascii=False, sort_keys=True, default=str)


def _recursive_keys(value: Any) -> set[str]:
    tree = _json_tree(value)
    if isinstance(tree, Mapping):
        return set(tree) | {
            key for item in tree.values() for key in _recursive_keys(item)
        }
    if isinstance(tree, list):
        return {key for item in tree for key in _recursive_keys(item)}
    return set()


def _assert_common_envelope(report: Mapping[str, Any]) -> None:
    assert report["contract_version"] == CONTRACT_VERSION
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["mode"] == "offline_dark"
    assert report["report_only"] is True
    assert report["activation_authorized"] is False


def _assert_deep_denylist(report: Mapping[str, Any]) -> None:
    for key in _recursive_keys(report):
        normalized = key.casefold().replace("-", "_")
        if normalized in {"activation_authorized", "evidence_at"} or normalized in (
            ACTIVATION_BLOCKERS | ROLLBACK_GATES
        ):
            continue
        assert not any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS), (
            f"readiness DTO leaks denied key {key!r}"
        )
        assert not any(fragment in normalized for fragment in ACTION_KEY_FRAGMENTS), (
            f"readiness DTO exposes action-like key {key!r}"
        )


def _source_check(
    report: Mapping[str, Any],
    source_name: str,
) -> Mapping[str, Any]:
    sources = _mapping(report["sources"])
    assert set(sources) == {"runtime", "memory", "worldbook"}
    return _mapping(sources[source_name])


def _available_source(schema_version: int) -> dict[str, Any]:
    return {
        "available": True,
        "schema_version": schema_version,
        "integrity": "ok",
    }


def _unavailable_source(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "schema_version": None,
        "integrity": "unknown",
        "reason": reason,
    }


def _memory_candidate() -> CandidateEnvelopeV1:
    quote = "EVIDENCE_QUOTE_CANARY_ROLLOUT_READINESS"
    evidence = EvidenceAtomV1(
        evidence_ref=(
            "message:onebot:private:42:EVIDENCE_REF_CANARY_ROLLOUT_READINESS"
        ),
        content_sha256=sha256_text(quote),
        quote=quote,
        actor_ref="service:PRINCIPAL_CANARY_ROLLOUT_READINESS",
        occurred_at=T0,
    )
    observation = ObservationV1(
        source_kind=SourceKind.USER_STATEMENT,
        producer_kind=ProducerKind.MEMO,
        producer_version="memo-v2",
        producer_run_id="run-redacted-readiness",
        subject_ref="user:qq:42",
        owner_scope=OwnerScope.USER,
        owner_id="42",
        visibility=Visibility.PRIVATE,
        origin_group_ref=None,
        claim=CardClaimV1(
            category="preference",
            content="PAYLOAD_CANARY_ROLLOUT_READINESS",
        ),
        evidence=(evidence,),
        observed_at=T0 + timedelta(seconds=1),
        source_occurred_at=T0,
        time_basis=TimeBasis.SOURCE_EVENT,
        valid_from=None,
        valid_to=None,
        confidence=0.8,
    )
    proposal = ProjectionProposalV1.create(
        projection_kind="card",
        operation="create",
        target_ref=None,
        payload={
            "category": "preference",
            "content": "PAYLOAD_CANARY_ROLLOUT_READINESS",
        },
    )
    return CandidateEnvelopeV1.create(
        observation=observation,
        proposal=proposal,
        producer_kind="memo",
        producer_run_id="run-redacted-readiness",
        producer_item_id="item-redacted-readiness",
        produced_at=T0 + timedelta(seconds=2),
        model_output="ARGS_CANARY_ROLLOUT_READINESS",
    )


def _assert_actionless_surface(readiness: Any) -> None:
    expected_methods = {
        "activation_readiness",
        "dark_readiness",
        "rollback_readiness",
    }
    public_callables = {
        name
        for name in dir(readiness)
        if not name.startswith("_") and callable(getattr(readiness, name))
    }
    assert public_callables == expected_methods
    for name in dir(readiness):
        if name.startswith("_"):
            continue
        normalized = name.casefold()
        assert not any(fragment in normalized for fragment in ACTION_KEY_FRAGMENTS)


@pytest.mark.asyncio
async def test_missing_and_uninitialized_sources_are_unavailable_without_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    readiness_type = _readiness_type(monkeypatch)

    missing = _mapping(await readiness_type(None, None).dark_readiness())
    _assert_common_envelope(missing)
    assert missing["status"] == "not_ready"
    assert _source_check(missing, "runtime") == _unavailable_source(
        "source_unavailable"
    )
    assert _source_check(missing, "memory") == _unavailable_source(
        "source_unavailable"
    )
    assert _source_check(missing, "worldbook") == _unavailable_source(
        "source_unavailable"
    )

    runtime_path = tmp_path / "runtime-must-not-exist.db"
    memory_path = tmp_path / "memory-must-not-exist.db"
    runtime = AgentRuntimeLedger(runtime_path)
    memory = MemoryGovernanceStore(memory_path)

    async def forbidden_init() -> None:
        pytest.fail("readiness must not initialize a caller-owned source")

    monkeypatch.setattr(runtime, "init", forbidden_init)
    monkeypatch.setattr(memory, "init", forbidden_init)
    uninitialized = _mapping(
        await readiness_type(runtime, memory).dark_readiness()
    )

    _assert_common_envelope(uninitialized)
    assert uninitialized["status"] == "not_ready"
    assert _source_check(uninitialized, "runtime") == _unavailable_source(
        "source_unavailable"
    )
    assert _source_check(uninitialized, "memory") == _unavailable_source(
        "source_unavailable"
    )
    assert _source_check(uninitialized, "worldbook") == _unavailable_source(
        "source_unavailable"
    )
    _assert_deep_denylist(uninitialized)
    assert tuple(tmp_path.iterdir()) == ()


@pytest.mark.asyncio
async def test_open_sources_report_only_sanitized_structural_dark_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_path = tmp_path / "runtime-private-path-canary.db"
    memory_path = tmp_path / "memory-private-path-canary.db"
    worldbook_path = tmp_path / "worldbook-private-path-canary.db"
    runtime = AgentRuntimeLedger(runtime_path)
    memory = MemoryGovernanceStore(memory_path)
    worldbook = WorldbookGovernanceStore(worldbook_path)
    await runtime.init()
    await memory.init()
    await worldbook.init()
    try:
        run = await runtime.create_run(
            run_id="run-safe-readiness-id",
            trigger_type="message",
            trigger_ref="TARGET_CANARY_ROLLOUT_READINESS",
            principal_kind="user",
            principal_id="PRINCIPAL_CANARY_ROLLOUT_READINESS",
            session_id="ARGS_CANARY_ROLLOUT_READINESS",
            group_id="PAYLOAD_CANARY_ROLLOUT_READINESS",
            metadata={
                "nested": {
                    "exception": "EXCEPTION_TEXT_CANARY_ROLLOUT_READINESS",
                }
            },
        )
        candidate = _memory_candidate()
        await memory.append_candidate(candidate)

        readiness = _readiness_type(monkeypatch)(
            runtime,
            memory,
            worldbook_source=worldbook,
        )
        report = _mapping(await readiness.dark_readiness())

        _assert_common_envelope(report)
        assert report["status"] == "ready"
        assert _source_check(report, "runtime") == _available_source(
            RUNTIME_SOURCE_SCHEMA_VERSION
        )
        assert _source_check(report, "memory") == _available_source(
            MEMORY_SOURCE_SCHEMA_VERSION
        )
        assert _source_check(report, "worldbook") == _available_source(
            WORLDBOOK_SOURCE_SCHEMA_VERSION
        )
        _assert_deep_denylist(report)
        serialized = _serialized(report)
        assert str(tmp_path) not in serialized
        assert "runtime-private-path-canary.db" not in serialized
        assert "memory-private-path-canary.db" not in serialized
        assert "worldbook-private-path-canary.db" not in serialized
        for canary in (
            "TARGET_CANARY_ROLLOUT_READINESS",
            "PRINCIPAL_CANARY_ROLLOUT_READINESS",
            "ARGS_CANARY_ROLLOUT_READINESS",
            "PAYLOAD_CANARY_ROLLOUT_READINESS",
            "EVIDENCE_REF_CANARY_ROLLOUT_READINESS",
            "EVIDENCE_QUOTE_CANARY_ROLLOUT_READINESS",
            "EXCEPTION_TEXT_CANARY_ROLLOUT_READINESS",
        ):
            assert canary not in serialized

        assert await runtime.get_run(run.run_id) == run
        assert await memory.get_candidate(candidate.candidate_id) == candidate
    finally:
        await runtime.close()
        await memory.close()
        await worldbook.close()


@pytest.mark.asyncio
async def test_closed_sources_fail_closed_without_exception_or_path_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = AgentRuntimeLedger(tmp_path / "closed-runtime-private.db")
    memory = MemoryGovernanceStore(tmp_path / "closed-memory-private.db")
    worldbook = WorldbookGovernanceStore(tmp_path / "closed-worldbook-private.db")
    await runtime.init()
    await memory.init()
    await worldbook.init()
    await runtime.close()
    await memory.close()
    await worldbook.close()

    report = _mapping(
        await _readiness_type(monkeypatch)(
            runtime,
            memory,
            worldbook_source=worldbook,
        ).dark_readiness()
    )

    _assert_common_envelope(report)
    assert report["status"] == "not_ready"
    assert _source_check(report, "runtime") == _unavailable_source(
        "source_unavailable"
    )
    assert _source_check(report, "memory") == _unavailable_source(
        "source_unavailable"
    )
    assert _source_check(report, "worldbook") == _unavailable_source(
        "source_unavailable"
    )
    _assert_deep_denylist(report)
    serialized = _serialized(report)
    assert "AgentRuntimeLedger is not initialized" not in serialized
    assert "MemoryGovernanceStore is not initialized" not in serialized
    assert str(tmp_path) not in serialized


class _FailingQueryConnection:
    def __init__(self, inner: Any, secret: str) -> None:
        self._inner = inner
        self._secret = secret

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(self._secret)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("execute"):
            return self.execute
        return getattr(self._inner, name)


@pytest.mark.asyncio
async def test_query_errors_are_generic_redacted_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = AgentRuntimeLedger(tmp_path / "runtime-query-failure.db")
    memory = MemoryGovernanceStore(tmp_path / "memory-query-healthy.db")
    worldbook = WorldbookGovernanceStore(tmp_path / "worldbook-query-healthy.db")
    await runtime.init()
    await memory.init()
    await worldbook.init()
    inner = runtime._db
    assert inner is not None
    runtime.__dict__["_db"] = _FailingQueryConnection(
        inner,
        "EXCEPTION_TEXT_QUERY_FAILURE_CANARY /private/source/path.db",
    )
    try:
        report = _mapping(
            await _readiness_type(monkeypatch)(
                runtime,
                memory,
                worldbook_source=worldbook,
            ).dark_readiness()
        )
    finally:
        runtime.__dict__["_db"] = inner
        await runtime.close()
        await memory.close()
        await worldbook.close()

    _assert_common_envelope(report)
    assert report["status"] == "not_ready"
    assert _source_check(report, "runtime") == _unavailable_source("query_failed")
    assert _source_check(report, "memory") == _available_source(
        MEMORY_SOURCE_SCHEMA_VERSION
    )
    assert _source_check(report, "worldbook") == _available_source(
        WORLDBOOK_SOURCE_SCHEMA_VERSION
    )
    _assert_deep_denylist(report)
    serialized = _serialized(report)
    assert "EXCEPTION_TEXT_QUERY_FAILURE_CANARY" not in serialized
    assert "/private/source/path.db" not in serialized


@pytest.mark.asyncio
async def test_activation_is_not_ready_until_every_production_gate_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = AgentRuntimeLedger(tmp_path / "activation-runtime.db")
    memory = MemoryGovernanceStore(tmp_path / "activation-memory.db")
    await runtime.init()
    await memory.init()
    try:
        readiness = _readiness_type(monkeypatch)(runtime, memory)
        report = _mapping(await readiness.activation_readiness())
    finally:
        await runtime.close()
        await memory.close()

    _assert_common_envelope(report)
    assert report["status"] == "not_ready"
    blockers = report["blocking_gates"]
    assert isinstance(blockers, Sequence) and not isinstance(blockers, (str, bytes))
    assert frozenset(blockers) == ACTIVATION_BLOCKERS
    assert len(blockers) == len(ACTIVATION_BLOCKERS)
    _assert_deep_denylist(report)


@pytest.mark.asyncio
async def test_rollback_report_is_descriptive_only_and_enables_no_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = AgentRuntimeLedger(tmp_path / "rollback-runtime.db")
    memory = MemoryGovernanceStore(tmp_path / "rollback-memory.db")
    await runtime.init()
    await memory.init()
    try:
        readiness = _readiness_type(monkeypatch)(runtime, memory)
        _assert_actionless_surface(readiness)
        report = _mapping(await readiness.rollback_readiness())
    finally:
        await runtime.close()
        await memory.close()

    _assert_common_envelope(report)
    assert report["status"] == "not_ready"
    assert "rollback" not in report
    gates = _mapping(report["gates"])
    assert set(gates) == ROLLBACK_GATES
    for gate in gates.values():
        assert _mapping(gate) == {
            "status": "not_assessed",
            "reason": "unknown",
            "evidence_at": None,
        }
    _assert_deep_denylist(report)
