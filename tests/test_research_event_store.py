"""Behavior contract for the append-only raw research event store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


def _load_api() -> tuple[type[Any], type[Any]]:
    """Defer the expected production import so RED is an assertion failure."""
    try:
        from services.group.research_event_store import ResearchEvent, ResearchEventStore
    except ModuleNotFoundError as exc:
        pytest.fail(
            "missing expected services.group.research_event_store API (ResearchEvent, ResearchEventStore)",
            pytrace=False,
        )
        raise AssertionError("unreachable") from exc
    return ResearchEvent, ResearchEventStore


def _event(event_type: type[Any], **overrides: Any) -> Any:
    values = {
        "event_uid": "onebot:group:g-42:1001",
        "run_id": "run-20260712-a",
        "event_time": datetime(2026, 7, 12, 9, 30, 15, tzinfo=UTC),
        "direction": "inbound",
        "actor_type": "human",
        "actor_id": "actor-pseudo-7f3a",
        "source": "live",
        "group_id": "g-42",
        "message_id": 1001,
        "reply_to_message_id": 998,
        "at_user_ids": ("u-9", "u-8"),
        "text": "原始消息",
        "content_type": "text",
    }
    values.update(overrides)
    try:
        return event_type(**values)
    except TypeError as exc:
        if "unexpected keyword argument 'actor_id'" in str(exc):
            pytest.fail(
                "ResearchEvent must accept the pseudonymous actor_id field",
                pytrace=False,
            )
        raise


async def _new_store(db_path: Path) -> Any:
    _, store_type = _load_api()
    store = store_type(str(db_path))
    await store.init()
    return store


async def test_inbound_event_round_trips_all_observable_fields(tmp_path: Path) -> None:
    event_type, _ = _load_api()
    event = _event(event_type, text=None, content_type="image")
    store = await _new_store(tmp_path / "research-events.db")

    try:
        result = await store.append(event)
        saved = await store.get_event(event.event_uid)

        assert result.status == "persisted"
        assert saved is not None
        assert saved.event_uid == event.event_uid
        assert saved.run_id == "run-20260712-a"
        assert saved.event_time == datetime(2026, 7, 12, 9, 30, 15, tzinfo=UTC)
        assert saved.ingested_at.tzinfo is not None
        assert saved.direction == "inbound"
        assert saved.actor_type == "human"
        assert saved.actor_id == "actor-pseudo-7f3a"
        assert saved.source == "live"
        assert saved.group_id == "g-42"
        assert saved.message_id == 1001
        assert saved.reply_to_message_id == 998
        assert saved.at_user_ids == ("u-9", "u-8")
        assert saved.text is None
        assert saved.content_type == "image"
    finally:
        await store.close()


async def test_successful_ai_outbound_event_is_persisted(tmp_path: Path) -> None:
    event_type, _ = _load_api()
    event = _event(
        event_type,
        event_uid="onebot:group:g-42:bot:2001",
        direction="outbound",
        actor_type="ai",
        actor_id="actor-bot-self",
        message_id=2001,
        reply_to_message_id=1001,
        at_user_ids=(),
        text="我在。",
    )
    store = await _new_store(tmp_path / "research-events.db")

    try:
        result = await store.append(event)
        saved = await store.get_event(event.event_uid)

        assert result.status == "persisted"
        assert saved is not None
        assert saved.direction == "outbound"
        assert saved.actor_type == "ai"
        assert saved.actor_id == "actor-bot-self"
        assert saved.message_id == 2001
        assert saved.reply_to_message_id == 1001
        assert saved.text == "我在。"
        assert saved.content_type == "text"
    finally:
        await store.close()


async def test_event_can_be_read_after_store_is_closed_and_reopened(tmp_path: Path) -> None:
    event_type, _ = _load_api()
    db_path = tmp_path / "research-events.db"
    event = _event(
        event_type,
        event_uid="onebot:history:g-42:900",
        source="history",
        message_id=900,
    )

    first_store = await _new_store(db_path)
    try:
        assert (await first_store.append(event)).status == "persisted"
    finally:
        await first_store.close()

    reopened_store = await _new_store(db_path)
    try:
        saved = await reopened_store.get_event(event.event_uid)
        assert saved is not None
        assert saved.event_uid == "onebot:history:g-42:900"
        assert saved.source == "history"
        assert saved.message_id == 900
    finally:
        await reopened_store.close()


async def test_duplicate_event_uid_keeps_first_event_and_reports_duplicate(tmp_path: Path) -> None:
    event_type, _ = _load_api()
    first = _event(event_type)
    conflicting_duplicate = _event(
        event_type,
        run_id="run-20260712-b",
        text="不应覆盖首条记录",
    )
    store = await _new_store(tmp_path / "research-events.db")

    try:
        first_result = await store.append(first)
        duplicate_result = await store.append(conflicting_duplicate)
        saved = await store.get_event(first.event_uid)

        assert first_result.status == "persisted"
        assert duplicate_result.status == "duplicate"
        assert saved is not None
        assert saved.run_id == "run-20260712-a"
        assert saved.text == "原始消息"
    finally:
        await store.close()


async def test_metrics_snapshot_counts_received_persisted_and_duplicate(tmp_path: Path) -> None:
    event_type, _ = _load_api()
    event = _event(event_type)
    store = await _new_store(tmp_path / "research-events.db")

    try:
        await store.append(event)
        await store.append(event)
        metrics = store.metrics_snapshot()

        assert metrics.received == 2
        assert metrics.persisted == 1
        assert metrics.duplicate == 1
        assert metrics.error == 0
    finally:
        await store.close()
