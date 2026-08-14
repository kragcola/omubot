"""RED contracts for the bounded, redacted Memory governance admin query."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from services.memory.governance_contracts import (
    CandidateEnvelopeV1,
    CardClaimV1,
    ConflictV1,
    EvidenceAtomV1,
    ObservationV1,
    OwnerScope,
    ProducerKind,
    ProjectionProposalV1,
    PromotionEventV1,
    SourceKind,
    TimeBasis,
    Visibility,
    sha256_text,
)
from services.memory.governance_store import MemoryGovernanceStore

CONTRACT_VERSION = "memory_governance_admin_query.v1"
ADMIN_SCHEMA_VERSION = 1
SOURCE_SCHEMA_VERSION = 1
T0 = datetime(2026, 7, 22, 1, 0, tzinfo=UTC)


def _query_module() -> ModuleType:
    spec = importlib.util.find_spec("services.memory.governance_query")
    assert spec is not None, (
        "services.memory.governance_query.MemoryGovernanceAdminQueryV1 is required"
    )
    return importlib.import_module("services.memory.governance_query")


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
        return {
            field.name: _json_tree(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(key): _json_tree(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_tree(item) for item in value]
    return value


def _serialized(value: Any) -> str:
    return json.dumps(_json_tree(value), ensure_ascii=False, sort_keys=True)


def _recursive_keys(value: Any) -> set[str]:
    tree = _json_tree(value)
    if isinstance(tree, Mapping):
        return set(tree) | {
            key for item in tree.values() for key in _recursive_keys(item)
        }
    if isinstance(tree, list):
        return {key for item in tree for key in _recursive_keys(item)}
    return set()


def _assert_timestamp(value: Any) -> None:
    if isinstance(value, datetime):
        assert value.tzinfo is not None and value.utcoffset() is not None
        return
    assert isinstance(value, str) and value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None and parsed.utcoffset() is not None


def _assert_envelope(value: Mapping[str, Any], *, available: bool = True) -> None:
    assert value["available"] is available
    assert value["mode"] == "offline_dark"
    assert value["reason"] == ("" if available else "source_unavailable")
    assert value["contract_version"] == CONTRACT_VERSION
    assert value["admin_schema_version"] == ADMIN_SCHEMA_VERSION
    assert value["source_schema_version"] == SOURCE_SCHEMA_VERSION
    _assert_timestamp(value["snapshot_at"])
    assert "schema_version" not in value


def _assert_page(value: Mapping[str, Any], *, available: bool = True) -> None:
    _assert_envelope(value, available=available)
    assert isinstance(value["items"], Sequence)
    assert not isinstance(value["items"], (str, bytes, bytearray))
    assert value["next_cursor"] is None or isinstance(value["next_cursor"], str)


def _proposal_payload(kind: str, marker: str) -> dict[str, Any]:
    if kind == "card":
        return {"category": "preference", "content": marker}
    if kind == "style":
        return {"expression": marker, "situation": "quiet conversation"}
    raise AssertionError(f"unsupported test projection kind: {kind}")


def _candidate(
    *,
    item: str,
    projection_kind: str = "card",
    operation: str = "create",
    target_ref: str | None = None,
    subject_ref: str = "user:qq:42",
    owner_id: str = "42",
    origin_group_ref: str | None = None,
    evidence_ref: str | None = None,
    evidence_quote: str | None = None,
    claim_content: str | None = None,
    proposal_content: str | None = None,
    producer_version: str = "memo-v2",
    producer_run_id: str = "memo-run-admin-query",
    model_output: str | None = None,
    offset: int = 0,
) -> CandidateEnvelopeV1:
    quote = evidence_quote or f"raw evidence {item}"
    evidence = EvidenceAtomV1(
        evidence_ref=evidence_ref or f"message:onebot:private:42:msg-{item}",
        content_sha256=sha256_text(quote),
        quote=quote,
        actor_ref=subject_ref,
        occurred_at=T0 + timedelta(seconds=offset),
    )
    observation = ObservationV1(
        source_kind=SourceKind.USER_STATEMENT,
        producer_kind=ProducerKind.MEMO,
        producer_version=producer_version,
        producer_run_id=producer_run_id,
        subject_ref=subject_ref,
        owner_scope=OwnerScope.USER,
        owner_id=owner_id,
        visibility=(
            Visibility.SAME_GROUP if origin_group_ref else Visibility.PRIVATE
        ),
        origin_group_ref=origin_group_ref,
        claim=CardClaimV1(
            category="preference",
            content=claim_content or f"claim content {item}",
        ),
        evidence=(evidence,),
        observed_at=T0 + timedelta(seconds=offset + 1),
        source_occurred_at=T0 + timedelta(seconds=offset),
        time_basis=TimeBasis.SOURCE_EVENT,
        valid_from=None,
        valid_to=None,
        confidence=0.8,
    )
    proposal = ProjectionProposalV1.create(
        projection_kind=projection_kind,
        operation=operation,
        target_ref=target_ref,
        payload=_proposal_payload(
            projection_kind,
            proposal_content or f"proposal content {item}",
        ),
    )
    return CandidateEnvelopeV1.create(
        observation=observation,
        proposal=proposal,
        producer_kind="memo",
        producer_run_id=producer_run_id,
        producer_item_id=item,
        produced_at=T0 + timedelta(seconds=offset + 2),
        model_output=model_output or f'{{"item":"{item}"}}',
    )


def _conflict(
    first: CandidateEnvelopeV1,
    second: CandidateEnvelopeV1,
    *,
    suffix: str,
    detected_at: datetime | None = None,
) -> ConflictV1:
    return ConflictV1.create(
        kind="contradiction",
        subject_ref=first.observation.subject_ref,
        claim_key=f"claim-{suffix}",
        observation_ids=(
            first.observation.observation_id,
            second.observation.observation_id,
        ),
        candidate_ids=(first.candidate_id, second.candidate_id),
        existing_projection_refs=(f"card:legacy-{suffix}",),
        detected_at=detected_at or T0 + timedelta(minutes=2),
        detector="deterministic",
        basis_evidence_refs=tuple(
            atom.evidence_ref
            for candidate in (first, second)
            for atom in candidate.observation.evidence
        ),
    )


def _promotion_event(
    candidate: CandidateEnvelopeV1,
    event_kind: str,
    *,
    occurred_at: datetime,
    actor_kind: str,
    actor_ref: str,
    operator_note: str = "",
    conflict_ids: tuple[str, ...] = (),
) -> PromotionEventV1:
    return PromotionEventV1.create(
        candidate_id=candidate.candidate_id,
        candidate_sha256=candidate.candidate_sha256,
        event_kind=event_kind,
        actor_kind=actor_kind,
        actor_ref=actor_ref,
        occurred_at=occurred_at,
        reason_code="admin_query_test",
        operator_note=operator_note,
        conflict_ids=conflict_ids,
        projection_kind=candidate.proposal.projection_kind,
        operation=candidate.proposal.operation,
        projection_ref=None,
        receipt_ref=None,
    )


async def _append_candidates(
    store: MemoryGovernanceStore,
    count: int,
    *,
    prefix: str,
) -> list[CandidateEnvelopeV1]:
    values = [
        _candidate(item=f"{prefix}-{index}", offset=index * 10)
        for index in range(count)
    ]
    for candidate in values:
        await store.append_candidate(candidate)
    return values


async def test_missing_uninitialized_and_closed_sources_are_unavailable_without_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    before = tuple(tmp_path.iterdir())
    query_type = _query_module().MemoryGovernanceAdminQueryV1

    none_query = query_type(None)
    none_results = (
        _mapping(await none_query.summary()),
        _mapping(await none_query.list_observations()),
        _mapping(await none_query.list_candidates()),
        _mapping(await none_query.list_conflicts()),
        _mapping(await none_query.get_candidate("mcand_missing")),
    )
    assert tuple(tmp_path.iterdir()) == before

    explicit_path = tmp_path / "must-not-be-created.db"
    store = MemoryGovernanceStore(explicit_path)
    uninitialized_query = query_type(store)
    uninitialized_results = (
        _mapping(await uninitialized_query.summary()),
        _mapping(await uninitialized_query.list_observations()),
        _mapping(await uninitialized_query.list_candidates()),
        _mapping(await uninitialized_query.list_conflicts()),
        _mapping(await uninitialized_query.get_candidate("mcand_missing")),
    )
    assert not explicit_path.exists()

    for result in (*none_results, *uninitialized_results):
        _assert_envelope(result, available=False)
        assert "MemoryGovernanceStore is not initialized" not in _serialized(result)
    for page in (
        none_results[1],
        none_results[2],
        none_results[3],
        uninitialized_results[1],
        uninitialized_results[2],
        uninitialized_results[3],
    ):
        assert page["items"] == []
        assert page["next_cursor"] is None
    assert none_results[4]["item"] is None
    assert uninitialized_results[4]["item"] is None

    await store.init()
    await store.close()
    closed_before = {
        item.name: (item.stat().st_size, item.stat().st_mtime_ns)
        for item in tmp_path.iterdir()
    }
    closed_query = query_type(store)
    closed_results = (
        _mapping(await closed_query.summary()),
        _mapping(await closed_query.list_observations()),
        _mapping(await closed_query.list_candidates()),
        _mapping(await closed_query.list_conflicts()),
        _mapping(await closed_query.get_candidate("mcand_missing")),
    )
    closed_after = {
        item.name: (item.stat().st_size, item.stat().st_mtime_ns)
        for item in tmp_path.iterdir()
    }
    assert closed_after == closed_before
    for result in closed_results:
        _assert_envelope(result, available=False)


async def test_list_limits_require_exact_ints_inside_closed_admin_bound(
    tmp_path: Path,
) -> None:
    store = MemoryGovernanceStore(tmp_path / "limits.db")
    await store.init()
    try:
        query = _query_module().MemoryGovernanceAdminQueryV1(store)
        calls = (
            query.list_observations,
            query.list_candidates,
            query.list_conflicts,
        )
        for call in calls:
            for invalid in (True, False, 1.0, "1", 0, -1, 101, None):
                with pytest.raises((TypeError, ValueError), match="limit"):
                    await call(limit=invalid)
            _assert_page(_mapping(await call(limit=1)))
            _assert_page(_mapping(await call(limit=100)))
    finally:
        await store.close()


async def test_summary_and_bulk_pages_are_versioned_useful_and_deeply_redacted(
    tmp_path: Path,
) -> None:
    canaries = {
        "user:qq:991337991337",
        "991337991337",
        "group:qq:881337881337",
        "message:onebot:group:881337881337:EVIDENCE_REF_CANARY_MEMORY_ADMIN",
        "EXACT_EVIDENCE_QUOTE_CANARY_MEMORY_ADMIN",
        "CLAIM_PAYLOAD_CANARY_MEMORY_ADMIN",
        "PROPOSAL_PAYLOAD_CANARY_MEMORY_ADMIN",
        "PRODUCER_VERSION_CANARY_MEMORY_ADMIN",
        "memo-run-PRODUCER_CANARY_MEMORY_ADMIN",
        "MODEL_OUTPUT_CANARY_MEMORY_ADMIN",
        "policy:ACTOR_CANARY_MEMORY_ADMIN",
        "OPERATOR_NOTE_CANARY_MEMORY_ADMIN",
        "claim-CONFLICT_KEY_CANARY_MEMORY_ADMIN",
        "card:legacy-CONFLICT_KEY_CANARY_MEMORY_ADMIN",
    }
    path = tmp_path / "redaction.db"
    store = MemoryGovernanceStore(path)
    await store.init()
    try:
        sensitive = _candidate(
            item="sensitive",
            subject_ref="user:qq:991337991337",
            owner_id="991337991337",
            origin_group_ref="group:qq:881337881337",
            evidence_ref=(
                "message:onebot:group:881337881337:"
                "EVIDENCE_REF_CANARY_MEMORY_ADMIN"
            ),
            evidence_quote="EXACT_EVIDENCE_QUOTE_CANARY_MEMORY_ADMIN",
            claim_content="CLAIM_PAYLOAD_CANARY_MEMORY_ADMIN",
            proposal_content="PROPOSAL_PAYLOAD_CANARY_MEMORY_ADMIN",
            producer_version="PRODUCER_VERSION_CANARY_MEMORY_ADMIN",
            producer_run_id="memo-run-PRODUCER_CANARY_MEMORY_ADMIN",
            model_output="MODEL_OUTPUT_CANARY_MEMORY_ADMIN",
        )
        second = _candidate(
            item="second",
            subject_ref="user:qq:991337991337",
            owner_id="991337991337",
            origin_group_ref="group:qq:881337881337",
            offset=10,
        )
        await store.append_candidate(sensitive)
        await store.append_candidate(second)
        conflict = _conflict(
            sensitive,
            second,
            suffix="CONFLICT_KEY_CANARY_MEMORY_ADMIN",
        )
        await store.append_conflict(conflict)
        queued = _promotion_event(
            sensitive,
            "review_queued",
            occurred_at=T0 + timedelta(minutes=3),
            actor_kind="policy",
            actor_ref="policy:ACTOR_CANARY_MEMORY_ADMIN",
            operator_note="OPERATOR_NOTE_CANARY_MEMORY_ADMIN",
        )
        approved = _promotion_event(
            sensitive,
            "promotion_approved",
            occurred_at=T0 + timedelta(minutes=4),
            actor_kind="operator",
            actor_ref="operator:memory-admin-test",
            conflict_ids=(conflict.conflict_id,),
        )
        await store.append_promotion_event(queued)
        await store.append_promotion_event(approved)

        query = _query_module().MemoryGovernanceAdminQueryV1(store)
        summary = _mapping(await query.summary())
        observations = _mapping(await query.list_observations(limit=100))
        candidates = _mapping(await query.list_candidates(limit=100))
        conflicts = _mapping(await query.list_conflicts(limit=100))
    finally:
        await store.close()

    for envelope in (summary, observations, candidates, conflicts):
        _assert_envelope(envelope)
    assert summary["observation_count"] == 2
    assert summary["candidate_count"] == 2
    assert summary["conflict_count"] == 1
    assert summary["promotion_event_count"] == 2

    observation_by_id = {
        _mapping(item)["observation_id"]: _mapping(item)
        for item in observations["items"]
    }
    observation = observation_by_id[sensitive.observation.observation_id]
    assert observation["contract_version"] == "memory.observation.v1"
    assert observation["observation_sha256"] == sensitive.observation.observation_sha256
    assert observation["source_kind"] == "user_statement"
    assert observation["producer_kind"] == "memo"
    assert observation["evidence_count"] == 1
    _assert_timestamp(observation["observed_at"])

    candidate_by_id = {
        _mapping(item)["candidate_id"]: _mapping(item)
        for item in candidates["items"]
    }
    candidate = candidate_by_id[sensitive.candidate_id]
    assert candidate["contract_version"] == "memory.candidate.v1"
    assert candidate["candidate_sha256"] == sensitive.candidate_sha256
    assert candidate["observation_id"] == sensitive.observation.observation_id
    assert candidate["projection_kind"] == "card"
    assert candidate["operation"] == "create"
    assert candidate["fold_status"] == "approved"
    assert candidate["conflict_count"] == 1
    assert candidate["promotion_event_count"] == 2
    _assert_timestamp(candidate["produced_at"])

    assert len(conflicts["items"]) == 1
    conflict_item = _mapping(conflicts["items"][0])
    assert conflict_item["conflict_id"] == conflict.conflict_id
    assert conflict_item["contract_version"] == "memory.conflict.v1"
    assert conflict_item["kind"] == "contradiction"
    assert conflict_item["observation_count"] == 2
    assert conflict_item["candidate_count"] == 2
    _assert_timestamp(conflict_item["detected_at"])

    forbidden_keys = {
        "actor_ref",
        "basis_evidence_refs",
        "claim",
        "claim_key",
        "evidence",
        "evidence_ref",
        "existing_projection_refs",
        "operator_note",
        "origin_group_ref",
        "owner_id",
        "owner_scope",
        "payload",
        "producer_item_id",
        "producer_run_id",
        "quote",
        "subject_ref",
        "target_ref",
    }
    bulk = (summary, observations, candidates, conflicts)
    assert forbidden_keys.isdisjoint(_recursive_keys(bulk))
    serialized = _serialized(bulk)
    assert all(canary not in serialized for canary in canaries)


async def test_candidate_cursor_is_opaque_tamper_evident_and_filter_bound(
    tmp_path: Path,
) -> None:
    store = MemoryGovernanceStore(tmp_path / "cursor.db")
    await store.init()
    try:
        card_creates = await _append_candidates(store, 3, prefix="card-create")
        await store.append_candidate(
            _candidate(
                item="card-update",
                operation="reinforce",
                target_ref="card:existing-card",
                offset=40,
            )
        )
        await store.append_candidate(
            _candidate(item="style-create", projection_kind="style", offset=50)
        )

        query = _query_module().MemoryGovernanceAdminQueryV1(store)
        first = _mapping(
            await query.list_candidates(
                limit=1,
                projection_kind="card",
                operation="create",
            )
        )
        cursor = first["next_cursor"]
        assert isinstance(cursor, str) and cursor
        assert all(
            plaintext not in cursor
            for plaintext in (
                "card",
                "create",
                *(candidate.candidate_id for candidate in card_creates),
            )
        )

        index = len(cursor) // 2
        replacement = "A" if cursor[index] != "A" else "B"
        tampered = f"{cursor[:index]}{replacement}{cursor[index + 1:]}"
        with pytest.raises(ValueError, match="cursor"):
            await query.list_candidates(
                limit=1,
                cursor=tampered,
                projection_kind="card",
                operation="create",
            )
        for projection_kind, operation in (
            (None, None),
            ("card", None),
            ("card", "reinforce"),
            ("style", "create"),
        ):
            with pytest.raises(ValueError, match="cursor"):
                await query.list_candidates(
                    limit=1,
                    cursor=cursor,
                    projection_kind=projection_kind,
                    operation=operation,
                )
    finally:
        await store.close()


@pytest.mark.parametrize("surface", ["observations", "candidates", "conflicts"])
async def test_list_keysets_do_not_duplicate_skip_or_admit_later_appends(
    tmp_path: Path,
    surface: str,
) -> None:
    store = MemoryGovernanceStore(tmp_path / f"keyset-{surface}.db")
    await store.init()
    try:
        query = _query_module().MemoryGovernanceAdminQueryV1(store)
        first_candidate: CandidateEnvelopeV1 | None = None
        second_candidate: CandidateEnvelopeV1 | None = None
        if surface == "conflicts":
            first_candidate, second_candidate = await _append_candidates(
                store,
                2,
                prefix="conflict-participant",
            )
            originals = [
                _conflict(first_candidate, second_candidate, suffix=f"original-{index}")
                for index in range(4)
            ]
            for conflict in originals:
                await store.append_conflict(conflict)
            list_page = query.list_conflicts
            id_key = "conflict_id"
        else:
            originals = await _append_candidates(store, 4, prefix="original")
            list_page = (
                query.list_observations
                if surface == "observations"
                else query.list_candidates
            )
            id_key = "observation_id" if surface == "observations" else "candidate_id"

        baseline = _mapping(await list_page(limit=100))
        baseline_ids = [_mapping(item)[id_key] for item in baseline["items"]]
        first = _mapping(await list_page(limit=2))
        cursor = first["next_cursor"]
        assert isinstance(cursor, str) and cursor
        index = len(cursor) // 2
        replacement = "A" if cursor[index] != "A" else "B"
        tampered = f"{cursor[:index]}{replacement}{cursor[index + 1:]}"
        with pytest.raises(ValueError, match="cursor"):
            await list_page(limit=2, cursor=tampered)

        if surface == "conflicts":
            assert first_candidate is not None
            assert second_candidate is not None
            await store.append_conflict(
                _conflict(
                    first_candidate,
                    second_candidate,
                    suffix="appended-between-pages",
                )
            )
        else:
            await store.append_candidate(
                _candidate(item="appended-between-pages", offset=100)
            )
        second = _mapping(await list_page(limit=100, cursor=cursor))
    finally:
        await store.close()

    paged_ids = [
        *[_mapping(item)[id_key] for item in first["items"]],
        *[_mapping(item)[id_key] for item in second["items"]],
    ]
    assert paged_ids == baseline_ids
    assert len(paged_ids) == len(set(paged_ids))


async def test_candidate_detail_is_redacted_folded_and_closed_source_safe(
    tmp_path: Path,
) -> None:
    canaries = {
        "DETAIL_EVIDENCE_QUOTE_CANARY_MEMORY_ADMIN",
        "DETAIL_CLAIM_CANARY_MEMORY_ADMIN",
        "DETAIL_PROPOSAL_CANARY_MEMORY_ADMIN",
        "operator:DETAIL_ACTOR_CANARY_MEMORY_ADMIN",
        "DETAIL_OPERATOR_NOTE_CANARY_MEMORY_ADMIN",
    }
    store = MemoryGovernanceStore(tmp_path / "detail.db")
    await store.init()
    try:
        candidate = _candidate(
            item="detail",
            evidence_quote="DETAIL_EVIDENCE_QUOTE_CANARY_MEMORY_ADMIN",
            claim_content="DETAIL_CLAIM_CANARY_MEMORY_ADMIN",
            proposal_content="DETAIL_PROPOSAL_CANARY_MEMORY_ADMIN",
        )
        other = _candidate(item="detail-other", offset=10)
        await store.append_candidate(candidate)
        await store.append_candidate(other)
        conflict = _conflict(candidate, other, suffix="detail")
        await store.append_conflict(conflict)
        await store.append_promotion_event(
            _promotion_event(
                candidate,
                "review_queued",
                occurred_at=T0 + timedelta(minutes=3),
                actor_kind="operator",
                actor_ref="operator:DETAIL_ACTOR_CANARY_MEMORY_ADMIN",
                operator_note="DETAIL_OPERATOR_NOTE_CANARY_MEMORY_ADMIN",
            )
        )

        query = _query_module().MemoryGovernanceAdminQueryV1(store)
        detail = _mapping(await query.get_candidate(candidate.candidate_id))
        missing = _mapping(await query.get_candidate("mcand_does-not-exist"))
        await store.close()
        unavailable = _mapping(await query.get_candidate(candidate.candidate_id))
    finally:
        await store.close()

    _assert_envelope(detail)
    item = _mapping(detail["item"])
    assert item["candidate_id"] == candidate.candidate_id
    assert item["observation_id"] == candidate.observation.observation_id
    assert item["projection_kind"] == "card"
    assert item["operation"] == "create"
    assert item["fold_status"] == "review_queued"
    assert item["conflict_count"] == 1
    assert item["promotion_event_count"] == 1
    assert item["resolved_conflict_count"] == 0
    assert item["unresolved_conflict_count"] == 1
    assert {
        "actor_ref",
        "claim",
        "evidence",
        "operator_note",
        "owner_id",
        "owner_scope",
        "payload",
        "quote",
        "subject_ref",
    }.isdisjoint(_recursive_keys(detail))
    serialized = _serialized(detail)
    assert all(canary not in serialized for canary in canaries)

    _assert_envelope(missing)
    assert missing["item"] is None
    _assert_envelope(unavailable, available=False)
    assert unavailable["item"] is None


async def test_queries_do_not_mutate_the_explicit_governance_ledger(
    tmp_path: Path,
) -> None:
    path = tmp_path / "read-only.db"
    store = MemoryGovernanceStore(path)
    await store.init()
    try:
        candidate = _candidate(item="read-only")
        await store.append_candidate(candidate)
        before_files = tuple(sorted(item.name for item in tmp_path.iterdir()))
        before_digest = hashlib.sha256(path.read_bytes()).digest()

        query = _query_module().MemoryGovernanceAdminQueryV1(store)
        await query.summary()
        await query.list_observations(limit=100)
        await query.list_candidates(limit=100)
        await query.list_conflicts(limit=100)
        await query.get_candidate(candidate.candidate_id)

        after_digest = hashlib.sha256(path.read_bytes()).digest()
        after_files = tuple(sorted(item.name for item in tmp_path.iterdir()))
        assert after_digest == before_digest
        assert after_files == before_files
        assert await store.get_candidate(candidate.candidate_id) == candidate
    finally:
        await store.close()
