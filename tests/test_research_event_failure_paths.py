"""Failure-path contracts for research event capture and shutdown."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ALLOWED_GROUP = "raw-group-allowed"
BLOCKED_GROUP = "raw-group-blocked"
SECRET = "test-only-failure-path-secret"


def _load_api() -> tuple[type[Any], type[Any], type[Any]]:
    try:
        from services.group.research_event_store import (
            ResearchEvent,
            ResearchEventCapture,
            ResearchEventRecorder,
        )
    except ImportError as exc:
        pytest.fail("missing expected research event failure-path API", pytrace=False)
        raise AssertionError("unreachable") from exc
    return ResearchEvent, ResearchEventCapture, ResearchEventRecorder


def _load_store_type() -> type[Any]:
    try:
        from services.group.research_event_store import ResearchEventStore
    except ImportError as exc:
        pytest.fail("missing expected ResearchEventStore init API", pytrace=False)
        raise AssertionError("unreachable") from exc
    return ResearchEventStore


def _new_capture(capture_type: type[Any], recorder: Any, *, run_id: str) -> Any:
    try:
        return capture_type(
            recorder=recorder,
            run_id=run_id,
            pseudonymization_secret=SECRET,
            group_allowlist=[ALLOWED_GROUP],
        )
    except TypeError as exc:
        if "group_allowlist" in str(exc):
            pytest.fail(
                "ResearchEventCapture must own the raw group_allowlist",
                pytrace=False,
            )
        if "pseudonymization_secret" in str(exc):
            pytest.fail(
                "ResearchEventCapture must require pseudonymization_secret",
                pytrace=False,
            )
        raise


def _event(event_type: type[Any], suffix: int) -> Any:
    return event_type(
        event_uid=f"onebot:group:pseudo-group:{suffix}",
        run_id="run-failure-path",
        event_time=datetime(2026, 7, 12, 14, 30, 15, tzinfo=UTC),
        direction="inbound",
        actor_type="human",
        actor_id="actor-pseudo-7f3a",
        source="live",
        group_id="group-pseudo-6b2c",
        message_id=suffix,
        reply_to_message_id=None,
        at_user_ids=(),
        text=f"event-{suffix}",
        content_type="text",
    )


def _capture_inbound(capture: Any, group_id: str, message_id: int) -> bool:
    method = getattr(capture, "capture_inbound", None)
    assert callable(method), "ResearchEventCapture must expose capture_inbound"
    return method(
        event_time=datetime(2026, 7, 12, 14, 30, 15, tzinfo=UTC),
        source="live",
        group_id=group_id,
        actor_id="raw-actor-100",
        message_id=message_id,
        reply_to_message_id=None,
        at_targets=("raw-at-200",),
        text="inbound",
        content_type="text",
    )


def _capture_outbound(capture: Any, group_id: str, message_id: int) -> bool:
    method = getattr(capture, "capture_outbound", None)
    assert callable(method), "ResearchEventCapture must expose capture_outbound"
    return method(
        event_time=datetime(2026, 7, 12, 14, 30, 16, tzinfo=UTC),
        source="live",
        group_id=group_id,
        actor_id="raw-bot-999",
        message_id=message_id,
        reply_to_message_id=None,
        at_targets=(),
        text="outbound",
        content_type="text",
    )


class _ObservableStore:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    def metrics_snapshot(self) -> Any:
        return SimpleNamespace(persisted=5, duplicate=2, error=1)


class _CaptureRecorder:
    def __init__(self) -> None:
        self.store = _ObservableStore()
        self.events: list[Any] = []

    def enqueue(self, event: Any) -> bool:
        self.events.append(event)
        return True

    def metrics_snapshot(self) -> Any:
        return SimpleNamespace(
            received=9,
            enqueued=7,
            dropped_queue_full=2,
            write_error=1,
            pending=3,
        )


class _BlockingBatchStore:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.persisted = 0

    async def append_many(self, events: list[Any] | tuple[Any, ...]) -> list[Any]:
        batch = tuple(events)
        self.started.set()
        await self.release.wait()
        self.persisted += len(batch)
        return [SimpleNamespace(status="persisted") for _ in batch]

    async def append(self, event: Any) -> Any:
        results = await self.append_many([event])
        return results[0]

    def metrics_snapshot(self) -> Any:
        return SimpleNamespace(persisted=self.persisted, duplicate=0, error=0)


class _CancelableOwnedRecorder(_CaptureRecorder):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def close(self) -> None:
        self.close_started.set()
        await self.release.wait()
        self.closed = True


class _CloseTrackingStore:
    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.close_error = close_error
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class _CloseTrackingRecorder:
    def __init__(
        self,
        store: _CloseTrackingStore,
        *,
        close_errors: tuple[Exception, ...] = (),
    ) -> None:
        self.store = store
        self.close_errors = close_errors
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        error_index = self.close_calls - 1
        if error_index < len(self.close_errors):
            raise self.close_errors[error_index]


def test_capture_enforces_allowlist_for_inbound_and_outbound() -> None:
    _, capture_type, _ = _load_api()
    recorder = _CaptureRecorder()
    capture = _new_capture(capture_type, recorder, run_id="run-allowlist")

    assert _capture_inbound(capture, BLOCKED_GROUP, 1) is False
    assert _capture_outbound(capture, BLOCKED_GROUP, 2) is False
    assert recorder.events == []

    assert _capture_inbound(capture, ALLOWED_GROUP, 3) is True
    assert _capture_outbound(capture, ALLOWED_GROUP, 4) is True
    assert len(recorder.events) == 2


async def test_recorder_close_can_be_retried_after_cancellation_and_drains() -> None:
    event_type, _, recorder_type = _load_api()
    store = _BlockingBatchStore()
    recorder = recorder_type(
        store,
        max_queue_size=1,
        batch_size=1,
        flush_interval_seconds=60.0,
    )
    await recorder.start()
    assert recorder.enqueue(_event(event_type, 1)) is True
    await asyncio.wait_for(store.started.wait(), timeout=1.0)
    assert recorder.enqueue(_event(event_type, 2)) is True
    assert recorder.enqueue(_event(event_type, 3)) is False

    first_close = asyncio.create_task(recorder.close())
    await asyncio.sleep(0)
    first_close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_close

    store.release.set()
    try:
        await asyncio.wait_for(recorder.close(), timeout=1.0)
    except TimeoutError:
        pytest.fail(
            "recorder.close must remain retryable after caller cancellation",
            pytrace=False,
        )

    metrics = recorder.metrics_snapshot()
    assert store.persisted == 2
    assert metrics.dropped_queue_full == 1
    assert metrics.pending == 0


async def test_capture_close_can_be_retried_and_eventually_closes_store() -> None:
    _, capture_type, _ = _load_api()
    recorder = _CancelableOwnedRecorder()
    capture = _new_capture(capture_type, recorder, run_id="run-capture-close")
    close_method = getattr(capture, "close", None)
    assert callable(close_method), "ResearchEventCapture must expose async close"

    first_close = asyncio.create_task(close_method())
    await asyncio.wait_for(recorder.close_started.wait(), timeout=1.0)
    first_close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_close

    recorder.release.set()
    try:
        await asyncio.wait_for(close_method(), timeout=1.0)
    except TimeoutError:
        pytest.fail(
            "ResearchEventCapture.close must remain retryable after cancellation",
            pytrace=False,
        )

    assert recorder.closed is True
    assert recorder.store.closed is True


async def test_capture_close_still_closes_store_and_preserves_recorder_error() -> None:
    _, capture_type, _ = _load_api()
    recorder_error = RuntimeError("injected recorder close failure")
    store = _CloseTrackingStore()
    recorder = _CloseTrackingRecorder(store, close_errors=(recorder_error,))
    capture = _new_capture(capture_type, recorder, run_id="run-recorder-close-failure")

    with pytest.raises(RuntimeError) as raised:
        await capture.close()

    assert raised.value is recorder_error
    assert recorder.close_calls == 1
    assert store.close_calls == 1


async def test_capture_close_aggregates_recorder_and_store_errors() -> None:
    _, capture_type, _ = _load_api()
    recorder_error = RuntimeError("injected recorder close failure")
    store_error = OSError("injected store close failure")
    store = _CloseTrackingStore(close_error=store_error)
    recorder = _CloseTrackingRecorder(store, close_errors=(recorder_error,))
    capture = _new_capture(capture_type, recorder, run_id="run-both-close-failures")

    try:
        await capture.close()
    except BaseException as exc:
        raised = exc
    else:
        pytest.fail("capture.close must propagate both cleanup failures")

    assert isinstance(raised, ExceptionGroup), (
        "capture.close must aggregate recorder and store cleanup failures"
    )
    assert raised.exceptions == (recorder_error, store_error)
    assert recorder.close_calls == 1
    assert store.close_calls == 1


async def test_capture_close_retries_only_incomplete_cleanup() -> None:
    _, capture_type, _ = _load_api()
    recorder_error = RuntimeError("transient recorder close failure")
    store = _CloseTrackingStore()
    recorder = _CloseTrackingRecorder(store, close_errors=(recorder_error,))
    capture = _new_capture(capture_type, recorder, run_id="run-close-idempotence")

    with pytest.raises(RuntimeError) as raised:
        await capture.close()
    assert raised.value is recorder_error

    retry_errors: list[BaseException] = []
    for _ in range(2):
        try:
            await capture.close()
        except BaseException as exc:
            retry_errors.append(exc)

    assert retry_errors == [], "capture.close must retry incomplete cleanup"
    assert recorder.close_calls == 2
    assert store.close_calls == 1


def test_capture_metrics_snapshot_combines_recorder_and_store_counters() -> None:
    _, capture_type, _ = _load_api()
    capture = _new_capture(capture_type, _CaptureRecorder(), run_id="run-metrics")
    snapshot_method = getattr(capture, "metrics_snapshot", None)
    assert callable(snapshot_method), "ResearchEventCapture must expose metrics_snapshot"

    metrics = snapshot_method()
    assert metrics.received == 9
    assert metrics.enqueued == 7
    assert metrics.dropped_queue_full == 2
    assert metrics.write_error == 1
    assert metrics.pending == 3
    assert metrics.persisted == 5
    assert metrics.duplicate == 2
    assert metrics.error == 1


async def test_store_init_chmod_failure_closes_connection_and_resets_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_type = _load_store_type()
    db_path = tmp_path / "research-events.db"
    store = store_type(str(db_path))
    original_chmod = Path.chmod
    opened_connections: list[Any] = []

    def fail_database_chmod(path: Path, mode: int, **kwargs: Any) -> None:
        if path == db_path:
            opened_connections.extend(
                value
                for value in vars(store).values()
                if callable(getattr(value, "execute", None)) and callable(getattr(value, "close", None))
            )
            raise OSError("injected chmod failure")
        original_chmod(path, mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", fail_database_chmod)

    with pytest.raises(OSError, match="injected chmod failure"):
        await store.init()

    try:
        assert opened_connections, "init must have opened a connection before chmod"
        store_state = tuple(vars(store).values())
        cleanup_failures: list[str] = []
        for connection in opened_connections:
            if any(value is connection for value in store_state):
                cleanup_failures.append("store still references the opened connection")
            try:
                cursor = await connection.execute("SELECT 1")
            except ValueError:
                pass
            else:
                await cursor.close()
                cleanup_failures.append("opened SQLite connection still accepts queries")
        assert cleanup_failures == [], (
            "failed init must close its connection and restore uninitialized state: "
            + "; ".join(cleanup_failures)
        )
    finally:
        monkeypatch.setattr(Path, "chmod", original_chmod)
        await store.close()

    await store.init()
    await store.close()
