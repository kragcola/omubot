"""Behavior contracts for process-level application lifecycle ownership."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from bootstrap.application import ApplicationRuntime


class _LifecycleProbe:
    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        start_error: BaseException | None = None,
        stop_error: BaseException | None = None,
    ) -> None:
        self.name = name
        self.calls = calls
        self.start_error = start_error
        self.stop_error = stop_error

    async def start(self) -> None:
        self.calls.append(f"{self.name}.start")
        if self.start_error is not None:
            raise self.start_error

    async def stop(self) -> None:
        self.calls.append(f"{self.name}.stop")
        if self.stop_error is not None:
            raise self.stop_error


class _PartiallyAcquiringProbe:
    def __init__(
        self,
        name: str,
        calls: list[str],
        start_error: BaseException,
    ) -> None:
        self.name = name
        self.calls = calls
        self.start_error = start_error
        self.resource_acquired = False

    async def start(self) -> None:
        self.calls.append(f"{self.name}.start")
        self.resource_acquired = True
        raise self.start_error

    async def stop(self) -> None:
        self.calls.append(f"{self.name}.stop")
        self.resource_acquired = False


class _BackupLikeProbe:
    def __init__(self, calls: list[str], start_error: BaseException) -> None:
        self.calls = calls
        self.start_error = start_error
        self.task_handle: object | None = None

    async def start(self) -> None:
        self.calls.append("backup.start")
        self.task_handle = object()
        raise self.start_error

    async def stop(self) -> None:
        self.calls.append("backup.stop")
        self.task_handle = None


class _BlockingCleanupProbe:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls
        self.stop_entered = asyncio.Event()
        self.allow_stop = asyncio.Event()
        self.resource_acquired = False
        self.stop_calls = 0

    async def start(self) -> None:
        self.calls.append(f"{self.name}.start")
        self.resource_acquired = True

    async def stop(self) -> None:
        self.calls.append(f"{self.name}.stop")
        self.stop_calls += 1
        self.stop_entered.set()
        await self.allow_stop.wait()
        self.resource_acquired = False


class _ControlledStartProbe:
    def __init__(self, name: str, calls: list[str], *, blocked: bool) -> None:
        self.name = name
        self.calls = calls
        self.start_entered = asyncio.Event()
        self.allow_start = asyncio.Event()
        if not blocked:
            self.allow_start.set()
        self.start_calls = 0
        self.stop_calls = 0
        self.resource_acquired = False

    async def start(self) -> None:
        self.calls.append(f"{self.name}.start")
        self.start_calls += 1
        self.resource_acquired = True
        self.start_entered.set()
        await self.allow_start.wait()
        self.calls.append(f"{self.name}.start.done")

    async def stop(self) -> None:
        self.calls.append(f"{self.name}.stop")
        self.stop_calls += 1
        self.resource_acquired = False


@pytest.mark.asyncio
async def test_application_runtime_starts_components_in_declared_order() -> None:
    calls: list[str] = []
    runtime = ApplicationRuntime([
        _LifecycleProbe("first", calls),
        _LifecycleProbe("second", calls),
        _LifecycleProbe("third", calls),
    ])

    await runtime.start()

    assert calls == ["first.start", "second.start", "third.start"]


@pytest.mark.asyncio
async def test_application_runtime_start_failure_compensates_acquired_components_in_reverse() -> None:
    calls: list[str] = []
    startup_error = RuntimeError("third startup failed")
    runtime = ApplicationRuntime([
        _LifecycleProbe(
            "first",
            calls,
            stop_error=RuntimeError("first cleanup failed"),
        ),
        _LifecycleProbe("second", calls),
        _LifecycleProbe("third", calls, start_error=startup_error),
        _LifecycleProbe("never_started", calls),
    ])

    with pytest.raises(RuntimeError) as raised:
        await runtime.start()

    assert raised.value is startup_error
    assert calls == [
        "first.start",
        "second.start",
        "third.start",
        "third.stop",
        "second.stop",
        "first.stop",
    ]


@pytest.mark.asyncio
async def test_cancelled_partial_start_stops_failing_component_and_preserves_cancel() -> None:
    calls: list[str] = []
    startup_cancel = asyncio.CancelledError()
    partial = _PartiallyAcquiringProbe("partial", calls, startup_cancel)
    runtime = ApplicationRuntime([
        _LifecycleProbe("first", calls),
        partial,
        _LifecycleProbe("never_started", calls),
    ])

    with pytest.raises(asyncio.CancelledError) as raised:
        await runtime.start()

    assert raised.value is startup_cancel
    assert calls == [
        "first.start",
        "partial.start",
        "partial.stop",
        "first.stop",
    ]
    assert partial.resource_acquired is False


@pytest.mark.asyncio
async def test_backup_like_partial_start_releases_acquired_task_handle() -> None:
    calls: list[str] = []
    startup_error = RuntimeError("backup task creation failed")
    backup = _BackupLikeProbe(calls, startup_error)
    runtime = ApplicationRuntime([backup])

    with pytest.raises(RuntimeError) as raised:
        await runtime.start()

    assert raised.value is startup_error
    assert calls == ["backup.start", "backup.stop"]
    assert backup.task_handle is None


@pytest.mark.asyncio
async def test_application_runtime_stop_failure_does_not_block_remaining_cleanup() -> None:
    calls: list[str] = []
    runtime = ApplicationRuntime([
        _LifecycleProbe("first", calls),
        _LifecycleProbe(
            "second",
            calls,
            stop_error=RuntimeError("second stop failed"),
        ),
        _LifecycleProbe("third", calls),
    ])
    await runtime.start()
    calls.clear()

    with contextlib.suppress(RuntimeError, ExceptionGroup):
        await runtime.stop()

    assert calls == ["third.stop", "second.stop", "first.stop"]


@pytest.mark.asyncio
async def test_application_runtime_stop_is_idempotent() -> None:
    calls: list[str] = []
    runtime = ApplicationRuntime([_LifecycleProbe("only", calls)])
    await runtime.start()
    calls.clear()

    await runtime.stop()
    await runtime.stop()

    assert calls == ["only.stop"]


@pytest.mark.asyncio
async def test_cancelled_stop_is_shared_and_retry_completes_each_cleanup_once() -> None:
    calls: list[str] = []
    first = _LifecycleProbe("first", calls)
    blocking = _BlockingCleanupProbe("blocking", calls)
    runtime = ApplicationRuntime([first, blocking])
    await runtime.start()
    calls.clear()

    first_stop = asyncio.create_task(runtime.stop())
    await asyncio.wait_for(blocking.stop_entered.wait(), timeout=1)
    first_stop.cancel()

    with pytest.raises(asyncio.CancelledError):
        await first_stop

    second_stop = asyncio.create_task(runtime.stop())
    await asyncio.sleep(0)
    second_stop_waited_for_shared_cleanup = not second_stop.done()
    blocking.allow_stop.set()
    await asyncio.wait_for(second_stop, timeout=1)

    assert second_stop_waited_for_shared_cleanup is True
    assert calls == ["blocking.stop", "first.stop"]
    assert blocking.stop_calls == 1
    assert blocking.resource_acquired is False


@pytest.mark.asyncio
async def test_concurrent_start_calls_start_each_component_exactly_once() -> None:
    calls: list[str] = []
    first = _ControlledStartProbe("first", calls, blocked=True)
    second = _ControlledStartProbe("second", calls, blocked=False)
    runtime = ApplicationRuntime([first, second])
    first_start = asyncio.create_task(runtime.start())
    await first.start_entered.wait()
    second_caller_entered = asyncio.Event()

    async def start_again() -> None:
        second_caller_entered.set()
        await runtime.start()

    second_start = asyncio.create_task(start_again())
    await second_caller_entered.wait()
    first.allow_start.set()
    await asyncio.gather(first_start, second_start)
    await runtime.stop()

    assert first.start_calls == 1
    assert second.start_calls == 1
    assert first.stop_calls == 1
    assert second.stop_calls == 1
    assert first.resource_acquired is False
    assert second.resource_acquired is False


@pytest.mark.asyncio
async def test_stop_during_startup_prevents_post_stop_starts_and_cleans_ledger() -> None:
    calls: list[str] = []
    first = _ControlledStartProbe("first", calls, blocked=True)
    second = _ControlledStartProbe("second", calls, blocked=False)
    runtime = ApplicationRuntime([first, second])
    startup = asyncio.create_task(runtime.start())
    await first.start_entered.wait()
    stop_called = asyncio.Event()

    async def stop_runtime() -> None:
        stop_called.set()
        await runtime.stop()
        calls.append("runtime.stop.done")

    stopping = asyncio.create_task(stop_runtime())
    await stop_called.wait()
    first.allow_start.set()
    _startup_result, stop_result = await asyncio.gather(
        startup,
        stopping,
        return_exceptions=True,
    )
    cleanup_counts = (first.stop_calls, second.stop_calls)
    await runtime.stop()

    assert stop_result is None
    stop_done_index = calls.index("runtime.stop.done")
    assert not any(
        call.endswith(".start")
        for call in calls[stop_done_index + 1:]
    )
    assert first.start_calls == first.stop_calls == 1
    assert second.start_calls == second.stop_calls
    assert second.start_calls <= 1
    assert first.resource_acquired is False
    assert second.resource_acquired is False
    assert (first.stop_calls, second.stop_calls) == cleanup_counts
