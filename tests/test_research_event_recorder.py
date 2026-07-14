"""Behavior contract for the bounded async research event recorder."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest


def _load_api() -> tuple[type[Any], type[Any]]:
    """Defer the expected import so a missing recorder is an explicit RED."""
    try:
        from services.group.research_event_store import (
            ResearchEvent,
            ResearchEventRecorder,
        )
    except ImportError as exc:
        if "ResearchEventRecorder" in str(exc):
            pytest.fail(
                "missing expected ResearchEventRecorder bounded async writer",
                pytrace=False,
            )
        raise
    return ResearchEvent, ResearchEventRecorder


def _event(event_type: type[Any], suffix: str = "1") -> Any:
    return event_type(
        event_uid=f"onebot:group:g-42:{suffix}",
        run_id="run-20260712-recorder",
        event_time=datetime(2026, 7, 12, 10, 30, 15, tzinfo=UTC),
        direction="inbound",
        actor_type="human",
        actor_id="actor-pseudo-7f3a",
        source="live",
        group_id="g-42",
        message_id=int(suffix),
        reply_to_message_id=None,
        at_user_ids=(),
        text=f"event-{suffix}",
        content_type="text",
    )


class _RecordingStore:
    def __init__(self) -> None:
        self.events: list[Any] = []
        self.persisted = 0
        self.duplicate = 0
        self._event_uids: set[str] = set()

    async def append(self, event: Any) -> Any:
        if event.event_uid in self._event_uids:
            self.duplicate += 1
            return SimpleNamespace(status="duplicate")
        self._event_uids.add(event.event_uid)
        self.events.append(event)
        self.persisted += 1
        return SimpleNamespace(status="persisted")

    def metrics_snapshot(self) -> Any:
        return SimpleNamespace(persisted=self.persisted, duplicate=self.duplicate)


class _BlockingStore:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.persisted = 0

    async def append(self, event: Any) -> Any:
        del event
        self.started.set()
        await self.release.wait()
        self.persisted += 1
        return SimpleNamespace(status="persisted")

    def metrics_snapshot(self) -> Any:
        return SimpleNamespace(persisted=self.persisted, duplicate=0)


class _FailingStore:
    async def append(self, event: Any) -> Any:
        del event
        raise RuntimeError("injected write failure")

    def metrics_snapshot(self) -> Any:
        return SimpleNamespace(persisted=0, duplicate=0)


async def test_close_drains_all_accepted_events() -> None:
    event_type, recorder_type = _load_api()
    store = _RecordingStore()
    recorder = recorder_type(
        store,
        max_queue_size=8,
        batch_size=4,
        flush_interval_seconds=60.0,
    )
    await recorder.start()

    assert recorder.enqueue(_event(event_type, "1")) is True
    assert recorder.enqueue(_event(event_type, "2")) is True

    await recorder.close()

    recorder_metrics = recorder.metrics_snapshot()
    store_metrics = store.metrics_snapshot()
    assert [event.event_uid for event in store.events] == [
        "onebot:group:g-42:1",
        "onebot:group:g-42:2",
    ]
    assert recorder_metrics.received == 2
    assert recorder_metrics.enqueued == 2
    assert recorder_metrics.dropped_queue_full == 0
    assert recorder_metrics.write_error == 0
    assert recorder_metrics.pending == 0
    assert store_metrics.persisted == 2
    assert store_metrics.duplicate == 0


async def test_queue_full_enqueue_returns_immediately_and_counts_drop() -> None:
    event_type, recorder_type = _load_api()
    store = _BlockingStore()
    recorder = recorder_type(
        store,
        max_queue_size=1,
        batch_size=1,
        flush_interval_seconds=60.0,
    )
    await recorder.start()

    try:
        assert recorder.enqueue(_event(event_type, "1")) is True
        await asyncio.wait_for(store.started.wait(), timeout=1.0)
        assert recorder.enqueue(_event(event_type, "2")) is True

        loop = asyncio.get_running_loop()
        started_at = loop.time()
        accepted = recorder.enqueue(_event(event_type, "3"))
        elapsed = loop.time() - started_at

        metrics = recorder.metrics_snapshot()
        assert accepted is False
        assert elapsed < 0.05
        assert metrics.received == 3
        assert metrics.enqueued == 2
        assert metrics.dropped_queue_full == 1
        assert metrics.write_error == 0
        assert metrics.pending >= 1
    finally:
        store.release.set()
        await recorder.close()

    assert recorder.metrics_snapshot().pending == 0


async def test_writer_error_is_counted_and_close_does_not_raise() -> None:
    event_type, recorder_type = _load_api()
    recorder = recorder_type(
        _FailingStore(),
        max_queue_size=2,
        batch_size=1,
        flush_interval_seconds=60.0,
    )
    await recorder.start()

    assert recorder.enqueue(_event(event_type)) is True
    await recorder.close()

    metrics = recorder.metrics_snapshot()
    assert metrics.received == 1
    assert metrics.enqueued == 1
    assert metrics.dropped_queue_full == 0
    assert metrics.write_error == 1
    assert metrics.pending == 0
