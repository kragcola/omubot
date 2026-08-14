"""RED contracts for the bounded, redacted Agent Runtime admin query."""

from __future__ import annotations

import importlib
import importlib.util
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from services.agent_runtime.ledger import AgentRuntimeLedger

CONTRACT_VERSION = "runtime_admin_query.v1"
ADMIN_SCHEMA_VERSION = 1
SOURCE_SCHEMA_VERSION = 2


def _query_module() -> ModuleType:
    spec = importlib.util.find_spec("services.agent_runtime.admin_query")
    assert spec is not None, (
        "services.agent_runtime.admin_query.RuntimeAdminQueryV1 is required"
    )
    return importlib.import_module("services.agent_runtime.admin_query")


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
    return value


def _serialized(value: Any) -> str:
    return json.dumps(_json_tree(value), ensure_ascii=False, sort_keys=True)


def _recursive_keys(value: Any) -> set[str]:
    tree = _json_tree(value)
    if isinstance(tree, Mapping):
        return set(tree) | {
            key
            for item in tree.values()
            for key in _recursive_keys(item)
        }
    if isinstance(tree, list):
        return {key for item in tree for key in _recursive_keys(item)}
    return set()


def _assert_page_contract(page: Mapping[str, Any]) -> None:
    assert page["available"] is True
    assert page["mode"] == "offline_dark"
    assert page["contract_version"] == CONTRACT_VERSION
    assert page["admin_schema_version"] == ADMIN_SCHEMA_VERSION
    assert page["source_schema_version"] == SOURCE_SCHEMA_VERSION
    assert "schema_version" not in page


async def test_none_source_is_unavailable_and_creates_no_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    before = tuple(tmp_path.iterdir())

    query = _query_module().RuntimeAdminQueryV1(None)
    summary = _mapping(await query.summary())

    assert summary["available"] is False
    assert summary["mode"] == "offline_dark"
    assert summary["contract_version"] == CONTRACT_VERSION
    assert summary["admin_schema_version"] == ADMIN_SCHEMA_VERSION
    assert tuple(tmp_path.iterdir()) == before


async def test_query_never_initializes_or_opens_an_explicit_source(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "must-not-be-created.db"
    ledger = AgentRuntimeLedger(db_path)

    summary = _mapping(await _query_module().RuntimeAdminQueryV1(ledger).summary())

    assert summary["available"] is False
    assert summary["mode"] == "offline_dark"
    assert summary["reason"] == "source_unavailable"
    assert "AgentRuntimeLedger is not initialized" not in _serialized(summary)
    assert not db_path.exists()


@pytest.mark.parametrize("limit", [0, 101])
async def test_run_page_rejects_limits_outside_closed_admin_bound(
    tmp_path: Path,
    limit: int,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    try:
        query = _query_module().RuntimeAdminQueryV1(ledger)
        with pytest.raises(ValueError, match=r"limit.*1.*100"):
            await query.list_runs(limit=limit)
    finally:
        await ledger.close()


async def test_run_page_is_bounded_versioned_and_deeply_redacted(
    tmp_path: Path,
) -> None:
    secrets = {
        "PRINCIPAL_CANARY_RUNTIME_ADMIN",
        "SESSION_CANARY_RUNTIME_ADMIN",
        "GROUP_CANARY_RUNTIME_ADMIN",
        "TRIGGER_CANARY_RUNTIME_ADMIN",
        "TARGET_CANARY_RUNTIME_ADMIN",
        "ARGS_CANARY_RUNTIME_ADMIN",
        "RESULT_CANARY_RUNTIME_ADMIN",
        "ERROR_CANARY_RUNTIME_ADMIN",
        "EVENT_METADATA_CANARY_RUNTIME_ADMIN",
    }
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    try:
        await ledger.create_run(
            run_id="run-admin-safe-id",
            trigger_type="message",
            trigger_ref="TRIGGER_CANARY_RUNTIME_ADMIN",
            principal_kind="user",
            principal_id="PRINCIPAL_CANARY_RUNTIME_ADMIN",
            session_id="SESSION_CANARY_RUNTIME_ADMIN",
            group_id="GROUP_CANARY_RUNTIME_ADMIN",
            registry_generation=7,
            metadata={
                "target": "TARGET_CANARY_RUNTIME_ADMIN",
                "args": {"secret": "ARGS_CANARY_RUNTIME_ADMIN"},
                "result": "RESULT_CANARY_RUNTIME_ADMIN",
                "error": "ERROR_CANARY_RUNTIME_ADMIN",
                "event": {"metadata": "EVENT_METADATA_CANARY_RUNTIME_ADMIN"},
            },
        )

        query = _query_module().RuntimeAdminQueryV1(ledger)
        page = _mapping(await query.list_runs(limit=1))
    finally:
        await ledger.close()

    _assert_page_contract(page)
    items = page["items"]
    assert isinstance(items, Sequence) and not isinstance(items, (str, bytes))
    assert len(items) == 1
    assert page["next_cursor"] is None

    run = _mapping(items[0])
    assert run["run_id"] == "run-admin-safe-id"
    assert run["trigger_type"] == "message"
    assert run["status"] == "queued"
    assert all(run[name] for name in ("created_at", "updated_at"))
    assert run["terminal_at"] == ""
    assert {
        "trigger_ref",
        "principal_id",
        "session_id",
        "group_id",
        "metadata",
    }.isdisjoint(run)

    serialized = _serialized(page)
    assert all(secret not in serialized for secret in secrets)


async def test_run_cursor_is_opaque_tamper_evident_and_filter_bound(
    tmp_path: Path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    try:
        for suffix in ("one", "two", "three"):
            await ledger.create_run(
                run_id=f"run-cursor-{suffix}",
                trigger_type="recovery",
                trigger_ref=f"runtime:cursor:{suffix}",
                principal_kind="service",
                principal_id="runtime",
            )

        query = _query_module().RuntimeAdminQueryV1(ledger)
        first = _mapping(await query.list_runs(limit=1, status="queued"))
        cursor = first["next_cursor"]
        assert isinstance(cursor, str) and cursor
        assert all(
            plaintext not in cursor
            for plaintext in ("run-cursor-one", "run-cursor-two", "run-cursor-three", "queued")
        )

        index = len(cursor) // 2
        replacement = "A" if cursor[index] != "A" else "B"
        tampered = f"{cursor[:index]}{replacement}{cursor[index + 1:]}"
        with pytest.raises(ValueError, match="cursor"):
            await query.list_runs(limit=1, cursor=tampered, status="queued")
        with pytest.raises(ValueError, match="cursor"):
            await query.list_runs(limit=1, cursor=cursor, status=None)
    finally:
        await ledger.close()


async def test_run_keyset_does_not_duplicate_or_skip_when_a_run_is_appended(
    tmp_path: Path,
) -> None:
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    try:
        for suffix in ("one", "two", "three", "four"):
            await ledger.create_run(
                run_id=f"run-original-{suffix}",
                trigger_type="tick",
                trigger_ref=f"tick:{suffix}",
                principal_kind="service",
                principal_id="scheduler",
            )

        query = _query_module().RuntimeAdminQueryV1(ledger)
        original = _mapping(await query.list_runs(limit=100))
        original_ids = [_mapping(item)["run_id"] for item in original["items"]]

        first = _mapping(await query.list_runs(limit=2))
        cursor = first["next_cursor"]
        assert isinstance(cursor, str) and cursor

        await ledger.create_run(
            run_id="run-appended-between-pages",
            trigger_type="tick",
            trigger_ref="tick:appended",
            principal_kind="service",
            principal_id="scheduler",
        )
        second = _mapping(await query.list_runs(limit=100, cursor=cursor))
    finally:
        await ledger.close()

    paged_ids = [
        *[_mapping(item)["run_id"] for item in first["items"]],
        *[_mapping(item)["run_id"] for item in second["items"]],
    ]
    assert paged_ids == original_ids
    assert len(paged_ids) == len(set(paged_ids))
    assert "run-appended-between-pages" not in paged_ids


async def test_tool_page_exposes_exact_approval_times_and_omits_sensitive_data(
    tmp_path: Path,
) -> None:
    secrets = {
        "TOOL_PRINCIPAL_CANARY_RUNTIME_ADMIN",
        "TOOL_TARGET_CANARY_RUNTIME_ADMIN",
        "TOOL_ARGS_CANARY_RUNTIME_ADMIN",
        "TOOL_IDEMPOTENCY_CANARY_RUNTIME_ADMIN",
        "TOOL_APPROVAL_CANARY_RUNTIME_ADMIN",
        "TOOL_CONCURRENCY_CANARY_RUNTIME_ADMIN",
        "TOOL_METADATA_CANARY_RUNTIME_ADMIN",
        "TOOL_POLICY_ACTOR_CANARY_RUNTIME_ADMIN",
        "TOOL_APPROVER_CANARY_RUNTIME_ADMIN",
    }
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    try:
        await ledger.create_run(
            run_id="run-tool-admin-safe-id",
            trigger_type="message",
            trigger_ref="message:tool-admin",
            principal_kind="user",
            principal_id="TOOL_PRINCIPAL_CANARY_RUNTIME_ADMIN",
        )
        await ledger.create_tool_call(
            call_id="call-admin-safe-id",
            run_id="run-tool-admin-safe-id",
            step_id="step-admin-safe-id",
            tool_name="publish_safe_tool",
            tool_version="tool-contract-7",
            owner="test-owner",
            effect="external_irreversible",
            principal_kind="user",
            principal_id="TOOL_PRINCIPAL_CANARY_RUNTIME_ADMIN",
            target_ref="TOOL_TARGET_CANARY_RUNTIME_ADMIN",
            args_digest="TOOL_ARGS_CANARY_RUNTIME_ADMIN",
            idempotency_mode="required",
            idempotency_key_digest="TOOL_IDEMPOTENCY_CANARY_RUNTIME_ADMIN",
            concurrency_mode="keyed_serial",
            concurrency_key="TOOL_CONCURRENCY_CANARY_RUNTIME_ADMIN",
            metadata={"secret": "TOOL_METADATA_CANARY_RUNTIME_ADMIN"},
        )
        await ledger.transition_tool_call(
            "call-admin-safe-id",
            to_status="approval_pending",
            actor="TOOL_POLICY_ACTOR_CANARY_RUNTIME_ADMIN",
            metadata={"secret": "TOOL_METADATA_CANARY_RUNTIME_ADMIN"},
        )
        await ledger.record_tool_call_approval(
            "call-admin-safe-id",
            approval_ref_digest="TOOL_APPROVAL_CANARY_RUNTIME_ADMIN",
            actor="TOOL_APPROVER_CANARY_RUNTIME_ADMIN",
        )
        events = await ledger.list_events(run_id="run-tool-admin-safe-id")
        requested_at = next(
            event.event_at
            for event in events
            if event.entity_kind == "tool_call"
            and event.from_status == "proposed"
            and event.to_status == "approval_pending"
        )
        granted_at = next(
            event.event_at for event in events if event.event_type == "approval_granted"
        )

        query = _query_module().RuntimeAdminQueryV1(ledger)
        page = _mapping(await query.list_tool_calls(limit=1))
    finally:
        await ledger.close()

    _assert_page_contract(page)
    assert page["next_cursor"] is None
    assert len(page["items"]) == 1
    call = _mapping(page["items"][0])
    assert call["call_id"] == "call-admin-safe-id"
    assert call["run_id"] == "run-tool-admin-safe-id"
    assert call["tool_name"] == "publish_safe_tool"
    assert call["tool_version"] == "tool-contract-7"
    assert call["effect"] == "external_irreversible"
    assert call["status"] == "approval_pending"
    assert call["approval_requested_at"] == requested_at
    assert call["approval_granted_at"] == granted_at
    assert {
        "principal_kind",
        "principal_id",
        "target_ref",
        "args_digest",
        "idempotency_key_digest",
        "approval_ref_digest",
        "concurrency_key",
        "safe_result",
        "error_code",
        "external_id",
    }.isdisjoint(call)
    serialized = _serialized(page)
    assert all(secret not in serialized for secret in secrets)


async def test_event_page_is_bounded_run_bound_and_omits_actor_and_metadata(
    tmp_path: Path,
) -> None:
    secrets = {
        "EVENT_TRIGGER_CANARY_RUNTIME_ADMIN",
        "EVENT_CREATED_METADATA_CANARY_RUNTIME_ADMIN",
        "EVENT_ACTOR_CANARY_RUNTIME_ADMIN",
        "EVENT_TRANSITION_METADATA_CANARY_RUNTIME_ADMIN",
    }
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    try:
        await ledger.create_run(
            run_id="run-events-admin-safe-id",
            trigger_type="recovery",
            trigger_ref="EVENT_TRIGGER_CANARY_RUNTIME_ADMIN",
            principal_kind="service",
            principal_id="runtime",
            metadata={"secret": "EVENT_CREATED_METADATA_CANARY_RUNTIME_ADMIN"},
        )
        await ledger.transition_run(
            "run-events-admin-safe-id",
            to_status="running",
            actor="EVENT_ACTOR_CANARY_RUNTIME_ADMIN",
            metadata={"secret": "EVENT_TRANSITION_METADATA_CANARY_RUNTIME_ADMIN"},
        )
        await ledger.transition_run(
            "run-events-admin-safe-id",
            to_status="succeeded",
            actor="EVENT_ACTOR_CANARY_RUNTIME_ADMIN",
            metadata={"secret": "EVENT_TRANSITION_METADATA_CANARY_RUNTIME_ADMIN"},
        )
        await ledger.create_run(
            run_id="run-events-other",
            trigger_type="recovery",
            trigger_ref="runtime:events:other",
            principal_kind="service",
            principal_id="runtime",
        )

        query = _query_module().RuntimeAdminQueryV1(ledger)
        for invalid_limit in (0, 101):
            with pytest.raises(ValueError, match=r"limit.*1.*100"):
                await query.list_events(
                    "run-events-admin-safe-id",
                    limit=invalid_limit,
                )

        first = _mapping(
            await query.list_events("run-events-admin-safe-id", limit=2)
        )
        cursor = first["next_cursor"]
        assert isinstance(cursor, str) and cursor
        second = _mapping(
            await query.list_events(
                "run-events-admin-safe-id",
                limit=2,
                cursor=cursor,
            )
        )
        with pytest.raises(ValueError, match="cursor"):
            await query.list_events("run-events-other", limit=2, cursor=cursor)
    finally:
        await ledger.close()

    _assert_page_contract(first)
    _assert_page_contract(second)
    events = [*first["items"], *second["items"]]
    assert len(events) == 3
    event_ids = [_mapping(event)["event_id"] for event in events]
    assert len(event_ids) == len(set(event_ids))
    for value in events:
        event = _mapping(value)
        assert event["run_id"] == "run-events-admin-safe-id"
        assert event["entity_kind"] == "run"
        assert event["event_type"] in {"created", "status_changed"}
        assert event["from_status"] in {"", "queued", "running"}
        assert event["to_status"] in {"queued", "running", "succeeded"}
        assert event["event_at"]
        assert {"actor", "metadata"}.isdisjoint(event)
    serialized = _serialized([first, second])
    assert all(secret not in serialized for secret in secrets)


async def test_get_run_is_redacted_unknown_safe_and_closed_source_safe(
    tmp_path: Path,
) -> None:
    secrets = {
        "DETAIL_TRIGGER_CANARY_RUNTIME_ADMIN",
        "DETAIL_PRINCIPAL_CANARY_RUNTIME_ADMIN",
        "DETAIL_SESSION_CANARY_RUNTIME_ADMIN",
        "DETAIL_GROUP_CANARY_RUNTIME_ADMIN",
        "DETAIL_TARGET_CANARY_RUNTIME_ADMIN",
        "DETAIL_ARGS_CANARY_RUNTIME_ADMIN",
        "DETAIL_APPROVAL_CANARY_RUNTIME_ADMIN",
        "DETAIL_METADATA_CANARY_RUNTIME_ADMIN",
    }
    ledger = AgentRuntimeLedger(tmp_path / "agent-runtime.db")
    await ledger.init()
    try:
        await ledger.create_run(
            run_id="run-detail-admin-safe-id",
            trigger_type="message",
            trigger_ref="DETAIL_TRIGGER_CANARY_RUNTIME_ADMIN",
            principal_kind="user",
            principal_id="DETAIL_PRINCIPAL_CANARY_RUNTIME_ADMIN",
            session_id="DETAIL_SESSION_CANARY_RUNTIME_ADMIN",
            group_id="DETAIL_GROUP_CANARY_RUNTIME_ADMIN",
            metadata={"secret": "DETAIL_METADATA_CANARY_RUNTIME_ADMIN"},
        )
        await ledger.create_tool_call(
            call_id="call-detail-admin-safe-id",
            run_id="run-detail-admin-safe-id",
            step_id="step-detail-admin-safe-id",
            tool_name="detail_safe_tool",
            tool_version="3",
            owner="test-owner",
            effect="write_local",
            principal_kind="user",
            principal_id="DETAIL_PRINCIPAL_CANARY_RUNTIME_ADMIN",
            target_ref="DETAIL_TARGET_CANARY_RUNTIME_ADMIN",
            args_digest="DETAIL_ARGS_CANARY_RUNTIME_ADMIN",
            idempotency_mode="required",
            idempotency_key_digest="DETAIL_ARGS_CANARY_RUNTIME_ADMIN",
            approval_ref_digest="DETAIL_APPROVAL_CANARY_RUNTIME_ADMIN",
            metadata={"secret": "DETAIL_METADATA_CANARY_RUNTIME_ADMIN"},
        )

        query = _query_module().RuntimeAdminQueryV1(ledger)
        detail = _mapping(await query.get_run("run-detail-admin-safe-id"))
        missing = _mapping(await query.get_run("run-detail-does-not-exist"))

        await ledger.close()
        unavailable = _mapping(await query.get_run("run-detail-admin-safe-id"))
    finally:
        await ledger.close()

    _assert_page_contract(detail)
    run = _mapping(detail["item"])
    assert run["run_id"] == "run-detail-admin-safe-id"
    assert run["trigger_type"] == "message"
    assert run["status"] == "queued"
    assert {
        "invocation",
        "invocation_envelope",
        "arguments",
        "args",
        "result",
        "error",
        "principal_id",
        "session_id",
        "group_id",
        "trigger_ref",
        "target_ref",
        "metadata",
    }.isdisjoint(_recursive_keys(detail))
    serialized = _serialized(detail)
    assert all(secret not in serialized for secret in secrets)

    _assert_page_contract(missing)
    assert missing["item"] is None

    assert unavailable["available"] is False
    assert unavailable["mode"] == "offline_dark"
    assert unavailable["reason"] == "source_unavailable"
    assert "AgentRuntimeLedger is not initialized" not in _serialized(unavailable)
