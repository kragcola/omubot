"""Prelaunch hardening contracts for research event capture."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Message

import kernel.router as router
from tests.test_research_event_capture_wiring import (
    _call_payload,
    _CaptureFake,
    _ctx,
    _group_event,
    _invoke_router_capture,
    _research_policy,
    _scheduler,
)


def _load_store_api() -> tuple[type[Any], type[Any], type[Any]]:
    from services.group.research_event_store import (
        ResearchEvent,
        ResearchEventRecorder,
        ResearchEventStore,
    )

    return ResearchEvent, ResearchEventRecorder, ResearchEventStore


def _event(event_type: type[Any], message_id: int) -> Any:
    return event_type(
        event_uid=f"onebot:group:pseudo-group:{message_id}",
        run_id="run-prelaunch-batch",
        event_time=datetime(2026, 7, 12, 13, 30, 15, tzinfo=UTC),
        direction="inbound",
        actor_type="human",
        actor_id="actor-pseudo-7f3a",
        source="live",
        group_id="group-pseudo-6b2c",
        message_id=message_id,
        reply_to_message_id=None,
        at_user_ids=(),
        text=f"event-{message_id}",
        content_type="text",
    )


class _BatchOnlySink:
    def __init__(self) -> None:
        self.batches: list[tuple[Any, ...]] = []
        self.append_calls = 0

    async def append_many(self, events: list[Any] | tuple[Any, ...]) -> list[Any]:
        batch = tuple(events)
        self.batches.append(batch)
        return [SimpleNamespace(status="persisted") for _ in batch]

    async def append(self, event: Any) -> Any:
        del event
        self.append_calls += 1
        raise AssertionError("recorder must use append_many for a batch-capable sink")

    def metrics_snapshot(self) -> Any:
        persisted = sum(len(batch) for batch in self.batches)
        return SimpleNamespace(persisted=persisted, duplicate=0)


async def test_router_capture_uses_platform_event_time_in_utc() -> None:
    platform_timestamp = 1_720_000_123
    expected_event_time = datetime.fromtimestamp(platform_timestamp, tz=UTC)
    capture_fn = getattr(router, "_capture_research_group_event", None)
    assert callable(capture_fn), "kernel.router must expose _capture_research_group_event"
    recorder = _CaptureFake()
    ctx = _ctx(_research_policy(enabled=True, group_allowlist=["100"]), recorder)
    event = _group_event(Message("hello"), message_id=501)
    event.time = platform_timestamp

    await _invoke_router_capture(ctx, capture_fn, event)

    assert len(recorder.calls) == 1
    payload = _call_payload(recorder.calls[0])
    assert payload.get("event_time") == expected_event_time, (
        "router capture must preserve event.time as a timezone-aware UTC datetime"
    )


async def test_recorder_uses_one_append_many_call_per_batch() -> None:
    event_type, recorder_type, store_type = _load_store_api()
    assert callable(getattr(store_type, "append_many", None)), "ResearchEventStore must expose append_many"
    sink = _BatchOnlySink()
    recorder = recorder_type(
        sink,
        max_queue_size=8,
        batch_size=3,
        flush_interval_seconds=60.0,
    )
    await recorder.start()

    assert recorder.enqueue(_event(event_type, 1)) is True
    assert recorder.enqueue(_event(event_type, 2)) is True
    assert recorder.enqueue(_event(event_type, 3)) is True
    await recorder.close()

    assert sink.append_calls == 0
    assert len(sink.batches) == 1
    assert [event.message_id for event in sink.batches[0]] == [1, 2, 3]


async def test_scheduler_captures_success_without_returned_message_id() -> None:
    capture = _CaptureFake()
    bot = SimpleNamespace(self_id="1", send_group_msg=AsyncMock(return_value={}))
    scheduler = _scheduler(capture, bot=bot)

    try:
        await scheduler._send_to_group("100", "hello")

        assert bot.send_group_msg.await_count == 1
        assert len(capture.calls) == 1, "a successful send must be captured even when message_id is unavailable"
        payload = _call_payload(capture.calls[0])
        assert "message_id" in payload
        assert payload["message_id"] is None
    finally:
        await scheduler.close()
