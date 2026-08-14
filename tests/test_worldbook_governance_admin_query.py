"""RED contracts for bounded, deeply redacted Worldbook admin queries."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from plugins.schedule.story_arc import StoryArc
from services.social_narrative import SocialExperience
from services.worldbook.governed_adapters import (
    build_schedule_event_proposal,
    build_social_event_proposal,
    verify_committed_world_event,
)
from services.worldbook.reducer import EventReducer

CONTRACT_VERSION = "worldbook_governance_admin_query.v1"
ADMIN_SCHEMA_VERSION = 1
SOURCE_SCHEMA_VERSION = 1
T0 = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)


def _store_module() -> ModuleType:
    spec = importlib.util.find_spec("services.worldbook.governance_store")
    assert spec is not None, "services.worldbook.governance_store is required"
    return importlib.import_module("services.worldbook.governance_store")


def _query_module() -> ModuleType:
    spec = importlib.util.find_spec("services.worldbook.governance_query")
    assert spec is not None, "services.worldbook.governance_query.WorldbookGovernanceAdminQueryV1 is required"
    return importlib.import_module("services.worldbook.governance_query")


def _contracts() -> ModuleType:
    return importlib.import_module("services.worldbook.governance_contracts")


def _store(path: Path) -> Any:
    return _store_module().WorldbookGovernanceStore(str(path))


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        result = model_dump(mode="json")
        assert isinstance(result, Mapping)
        return result
    pytest.fail(f"expected mapping-like DTO, got {type(value).__name__}")


def _json_tree(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_tree(getattr(value, field.name)) for field in fields(value)}
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
        return set(tree) | {key for item in tree.values() for key in _recursive_keys(item)}
    if isinstance(tree, list):
        return {key for item in tree for key in _recursive_keys(item)}
    return set()


def _assert_timestamp(value: Any) -> None:
    if isinstance(value, datetime):
        assert value.tzinfo is not None and value.utcoffset() is not None
        return
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    assert parsed.tzinfo is not None and parsed.utcoffset() is not None


def _assert_envelope(value: Mapping[str, Any], *, available: bool = True) -> None:
    assert value["available"] is available
    assert value["mode"] == "offline_dark"
    assert value["reason"] == ("" if available else "source_unavailable")
    assert value["contract_version"] == CONTRACT_VERSION
    assert value["admin_schema_version"] == ADMIN_SCHEMA_VERSION
    assert value["source_schema_version"] == SOURCE_SCHEMA_VERSION
    _assert_timestamp(value["snapshot_at"])


def _assert_page(value: Mapping[str, Any], *, available: bool = True) -> None:
    _assert_envelope(value, available=available)
    assert isinstance(value["items"], Sequence)
    assert not isinstance(value["items"], (str, bytes, bytearray))
    assert value["next_cursor"] is None or isinstance(value["next_cursor"], str)


def _schedule(*, item: int, world_id: str = "omubot.default") -> Any:
    return build_schedule_event_proposal(
        world_id=world_id,
        target_arc_id=f"arc.query.{item}",
        schedule_date=f"2026-09-{item + 1:02d}",
        summary=f"SCHEDULE_EVENT_PAYLOAD_CANARY_{item}",
        proposed_at=T0 + timedelta(seconds=item),
    )


def _social() -> Any:
    record = SocialExperience(
        experience_id="social.exp.ADMIN_EXPERIENCE_CANARY",
        group_id="881337881337",
        user_id="991337991337",
        entity_kind="factual",
        evidence_message_id="ADMIN_MESSAGE_ID_CANARY",
        evidence_time="2026-07-22T11:30:00+08:00",
        evidence_source="ADMIN_EVIDENCE_SOURCE_CANARY",
        user_text="Alice SecretName said RAW_SOCIAL_TEXT_CANARY",
        bot_reply="RAW_SOCIAL_REPLY_CANARY",
        status="active",
        created_at="2026-07-22T11:31:00+08:00",
    )
    return build_social_event_proposal(
        record=record,
        world_id="world.social",
        target_arc_id="arc.social.query",
        current_group_id="881337881337",
        current_user_id="991337991337",
        proposed_at=T0 + timedelta(minutes=1),
    )


def _decision(proposal: Any, decision: str = "approve") -> Any:
    return _contracts().WorldbookOperatorDecisionV1.create(
        proposal_id=proposal.proposal_id,
        decision=decision,
        reason_code="ADMIN_REASON_CANARY",
        operator_ref="operator:ADMIN_OPERATOR_REF_CANARY",
        decided_at=T0 + timedelta(minutes=2),
    )


def _receipt(proposal: Any) -> Any:
    committed_input = replace(
        proposal.event,
        status="committed",
        committed_at="2026-07-22T12:05:00+08:00",
        step=7,
    )
    arc = StoryArc(arc_id=proposal.world_ref.arc_id, revision=3)
    committed = EventReducer().apply(arc, committed_input, now_step=7)
    return verify_committed_world_event(
        proposal,
        committed_event=committed,
        committed_arc_snapshot=arc.to_dict(),
        committed_event_ids=tuple(arc.event_budget["committed_event_ids"]),
        persisted_revision=arc.revision,
    )


async def test_missing_uninitialized_and_closed_sources_are_generic_unavailable_without_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    query_type = _query_module().WorldbookGovernanceAdminQueryV1
    none_query = query_type(None)
    none_results = (
        _mapping(await none_query.summary()),
        _mapping(await none_query.list_proposals()),
        _mapping(await none_query.get_proposal("wprop_missing")),
    )
    assert list(tmp_path.iterdir()) == []

    path = tmp_path / "must-not-be-created.db"
    store = _store(path)
    uninitialized_query = query_type(store)
    uninitialized_results = (
        _mapping(await uninitialized_query.summary()),
        _mapping(await uninitialized_query.list_proposals()),
        _mapping(await uninitialized_query.get_proposal("wprop_missing")),
    )
    assert not path.exists()

    for result in (*none_results, *uninitialized_results):
        _assert_envelope(result, available=False)
        assert str(path) not in _serialized(result)
        assert "not initialized" not in _serialized(result).lower()
    for result in (none_results[1], uninitialized_results[1]):
        assert result["items"] == [] and result["next_cursor"] is None
    assert none_results[2]["item"] is None
    assert uninitialized_results[2]["item"] is None

    await store.init()
    await store.close()
    closed = _mapping(await query_type(store).summary())
    _assert_envelope(closed, available=False)


async def test_list_limit_requires_exact_int_inside_closed_bound(tmp_path: Path) -> None:
    store = _store(tmp_path / "limits.db")
    await store.init()
    try:
        query = _query_module().WorldbookGovernanceAdminQueryV1(store)
        for invalid in (True, False, 1.0, "1", 0, -1, 101, None):
            with pytest.raises((TypeError, ValueError), match="limit"):
                await query.list_proposals(limit=invalid)
        _assert_page(_mapping(await query.list_proposals(limit=1)))
        _assert_page(_mapping(await query.list_proposals(limit=100)))
    finally:
        await store.close()


async def test_summary_list_and_detail_are_useful_bounded_and_deeply_redacted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "DO_NOT_LEAK_DB_PATH_CANARY.db"
    store = _store(path)
    await store.init()
    try:
        pending = _schedule(item=0)
        approved = _schedule(item=1)
        rejected = _social()
        committed = _schedule(item=2)
        for proposal in (pending, approved, rejected, committed):
            await store.append_proposal(proposal)
        await store.append_operator_decision(_decision(approved))
        await store.append_operator_decision(_decision(rejected, "reject"))
        await store.append_operator_decision(_decision(committed))
        await store.append_commit_receipt(_receipt(committed))

        query = _query_module().WorldbookGovernanceAdminQueryV1(store)
        summary = _mapping(await query.summary())
        page = _mapping(await query.list_proposals(limit=100))
        detail = _mapping(await query.get_proposal(rejected.proposal_id))
        missing = _mapping(await query.get_proposal("wprop_does_not_exist"))
    finally:
        await store.close()

    for value in (summary, page, detail, missing):
        _assert_envelope(value)
    assert summary["proposal_count"] == 4
    assert summary["pending_count"] == 1
    assert summary["approved_count"] == 1
    assert summary["rejected_count"] == 1
    assert summary["committed_count"] == 1
    assert len(page["items"]) == 4
    assert missing["item"] is None

    by_id = {_mapping(item)["proposal_id"]: _mapping(item) for item in page["items"]}
    social = by_id[rejected.proposal_id]
    assert social["event_id"] == rejected.event.event_id
    assert social["world_id"] == "world.social"
    assert social["source_kind"] == "social_evidence"
    assert social["status"] == "rejected"
    assert social["decision_present"] is True
    assert social["receipt_present"] is False
    assert social["proposal_sha256"] == rejected.proposal_sha256
    assert len(social["provenance"]["source_binding_sha256"]) == 64
    assert social["provenance"]["evidence_count"] == len(rejected.evidence)
    _assert_timestamp(social["occurred_at"])
    _assert_timestamp(social["proposed_at"])
    assert _mapping(detail["item"]) == social

    committed_item = by_id[committed.proposal_id]
    assert committed_item["status"] == "committed"
    assert committed_item["decision_present"] is True
    assert committed_item["receipt_present"] is True

    forbidden_keys = {
        "arc_id",
        "consequences",
        "db_path",
        "event",
        "event_payload",
        "evidence",
        "evidence_message_id",
        "evidence_ref",
        "group_id",
        "name",
        "operator_ref",
        "quote",
        "reason_code",
        "source_binding",
        "source_ref",
        "summary",
        "user_id",
        "variable_deltas",
    }
    assert forbidden_keys.isdisjoint(_recursive_keys((summary, page, detail)))
    forbidden_values = {
        str(path),
        "881337881337",
        "991337991337",
        "ADMIN_MESSAGE_ID_CANARY",
        "ADMIN_EVIDENCE_SOURCE_CANARY",
        "ADMIN_EXPERIENCE_CANARY",
        "ADMIN_OPERATOR_REF_CANARY",
        "ADMIN_REASON_CANARY",
        rejected.event.summary,
        *[f"SCHEDULE_EVENT_PAYLOAD_CANARY_{index}" for index in range(3)],
    }
    serialized = _serialized((summary, page, detail))
    assert all(value not in serialized for value in forbidden_values)


async def test_filters_are_exact_and_cursor_is_opaque_tamper_evident_and_bound(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "cursor.db")
    await store.init()
    try:
        selected = tuple(_schedule(item=index) for index in range(3))
        other_world = _schedule(item=3, world_id="world.other")
        social = _social()
        for proposal in (*selected, other_world, social):
            await store.append_proposal(proposal)
        await store.append_operator_decision(_decision(selected[0]))

        query = _query_module().WorldbookGovernanceAdminQueryV1(store)
        first = _mapping(
            await query.list_proposals(
                limit=1,
                world_id="omubot.default",
                source_kind="schedule",
                status="pending",
            )
        )
        cursor = first["next_cursor"]
        assert isinstance(cursor, str) and cursor
        assert all(
            plaintext not in cursor
            for plaintext in (
                "omubot.default",
                "schedule",
                "pending",
                *(proposal.proposal_id for proposal in selected),
            )
        )

        index = len(cursor) // 2
        tampered = cursor[:index] + ("A" if cursor[index] != "A" else "B") + cursor[index + 1 :]
        with pytest.raises(ValueError, match="cursor"):
            await query.list_proposals(
                limit=1,
                cursor=tampered,
                world_id="omubot.default",
                source_kind="schedule",
                status="pending",
            )
        for filters in (
            {},
            {"world_id": "world.other", "source_kind": "schedule", "status": "pending"},
            {"world_id": "omubot.default", "source_kind": "social_evidence", "status": "pending"},
            {"world_id": "omubot.default", "source_kind": "schedule", "status": "approved"},
        ):
            with pytest.raises(ValueError, match="cursor"):
                await query.list_proposals(limit=1, cursor=cursor, **filters)
    finally:
        await store.close()


async def test_keyset_snapshot_has_no_duplicates_skips_or_later_appends(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "keyset.db")
    await store.init()
    try:
        originals = tuple(_schedule(item=index) for index in range(4))
        for proposal in originals:
            await store.append_proposal(proposal)
        query = _query_module().WorldbookGovernanceAdminQueryV1(store)
        baseline = _mapping(await query.list_proposals(limit=100))
        first = _mapping(await query.list_proposals(limit=2))
        assert isinstance(first["next_cursor"], str)

        await store.append_proposal(_schedule(item=5))
        second = _mapping(await query.list_proposals(limit=100, cursor=first["next_cursor"]))
    finally:
        await store.close()

    baseline_ids = [_mapping(item)["proposal_id"] for item in baseline["items"]]
    paged_ids = [
        *[_mapping(item)["proposal_id"] for item in first["items"]],
        *[_mapping(item)["proposal_id"] for item in second["items"]],
    ]
    assert paged_ids == baseline_ids
    assert len(paged_ids) == len(set(paged_ids))


async def test_status_filtered_cursor_freezes_unseen_decisions_receipts_and_dtos(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "status-snapshot.db")
    await store.init()
    try:
        originals = tuple(_schedule(item=index) for index in range(4))
        for proposal in originals:
            await store.append_proposal(proposal)
        query = _query_module().WorldbookGovernanceAdminQueryV1(store)
        baseline = _mapping(await query.list_proposals(limit=100, status="pending"))
        first = _mapping(await query.list_proposals(limit=1, status="pending"))
        assert isinstance(first["next_cursor"], str)

        await store.append_operator_decision(_decision(originals[1]))
        await store.append_operator_decision(_decision(originals[2]))
        await store.append_commit_receipt(_receipt(originals[2]))

        pages = [first]
        cursor = first["next_cursor"]
        while cursor is not None:
            page = _mapping(
                await query.list_proposals(
                    limit=1,
                    cursor=str(cursor),
                    status="pending",
                )
            )
            pages.append(page)
            cursor = page["next_cursor"]
    finally:
        await store.close()

    expected = [_mapping(item) for item in baseline["items"]]
    paged = [_mapping(item) for page in pages for item in page["items"]]
    assert paged == expected
    assert len({item["proposal_id"] for item in paged}) == len(paged)
    assert all(item["status"] == "pending" for item in paged)
    assert all(item["decision_present"] is False for item in paged)
    assert all(item["receipt_present"] is False for item in paged)


async def test_query_failure_is_generic_unavailable_and_never_leaks_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path / "failure-path-canary.db")
    await store.init()
    try:
        proposal = _schedule(item=0)
        await store.append_proposal(proposal)

        async def failed_get(_proposal_id: str) -> Any:
            raise RuntimeError("SECRET_QUERY_FAILURE_CANARY at /private/live/worldbook.db")

        monkeypatch.setattr(store, "get_proposal", failed_get)
        query = _query_module().WorldbookGovernanceAdminQueryV1(store)
        result = _mapping(await query.get_proposal(proposal.proposal_id))
    finally:
        await store.close()

    _assert_envelope(result, available=False)
    assert result["item"] is None
    assert "SECRET_QUERY_FAILURE_CANARY" not in _serialized(result)
    assert "/private/live/worldbook.db" not in _serialized(result)


async def test_queries_use_caller_opened_source_without_mutating_ledger(
    tmp_path: Path,
) -> None:
    path = tmp_path / "read-only.db"
    store = _store(path)
    await store.init()
    try:
        proposal = _schedule(item=0)
        await store.append_proposal(proposal)
        before_names = tuple(sorted(item.name for item in tmp_path.iterdir()))
        before_digest = hashlib.sha256(path.read_bytes()).digest()

        query = _query_module().WorldbookGovernanceAdminQueryV1(store)
        await query.summary()
        await query.list_proposals(limit=100)
        await query.get_proposal(proposal.proposal_id)

        assert hashlib.sha256(path.read_bytes()).digest() == before_digest
        assert tuple(sorted(item.name for item in tmp_path.iterdir())) == before_names
        assert await store.get_proposal(proposal.proposal_id) == proposal
    finally:
        await store.close()
