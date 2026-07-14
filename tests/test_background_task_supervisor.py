from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from kernel.background_tasks import (
    BackgroundTaskSupervisor,
    RestartPolicy,
    ShutdownPolicy,
    TaskKind,
    TaskSpec,
)
from kernel.bus import PluginBus
from plugins.dream.plugin import DreamAgent
from services.health import collect_service_health
from services.humanization.health_guard import HumanizationHealthGuard


def _spec(
    name: str,
    *,
    owner: str = "tests",
    restart: RestartPolicy = RestartPolicy.NEVER,
    shutdown: ShutdownPolicy = ShutdownPolicy.CANCEL,
    max_restarts: int = 0,
) -> TaskSpec:
    return TaskSpec(
        name=name,
        owner=owner,
        kind=TaskKind.PERIODIC,
        restart=restart,
        shutdown=shutdown,
        max_restarts=max_restarts,
        backoff_seconds=0.0,
        max_backoff_seconds=0.0,
        shutdown_timeout_seconds=0.01,
    )


def test_task_kind_contract_is_closed_to_the_four_architecture_categories() -> None:
    assert {item.value for item in TaskKind} == {
        "inline",
        "deferred",
        "periodic",
        "heavy",
    }
    with pytest.raises(ValueError, match="unknown background task kind"):
        TaskSpec(name="unknown", owner="tests", kind=cast(Any, "free-form"))


async def test_snapshot_exposes_task_metadata_and_running_state() -> None:
    supervisor = BackgroundTaskSupervisor()
    started = asyncio.Event()

    async def worker() -> None:
        started.set()
        await asyncio.Event().wait()

    task = supervisor.spawn(_spec("ticker", owner="plugin_bus"), worker)
    await started.wait()

    [snapshot] = supervisor.snapshot()
    assert snapshot.name == "ticker"
    assert snapshot.owner == "plugin_bus"
    assert snapshot.kind is TaskKind.PERIODIC
    assert snapshot.restart is RestartPolicy.NEVER
    assert snapshot.shutdown is ShutdownPolicy.CANCEL
    assert snapshot.state == "running"
    assert snapshot.attempts == 1
    assert task.done() is False

    await supervisor.stop()


async def test_lifecycle_start_is_idempotent_but_cannot_reopen_after_stop() -> None:
    supervisor = BackgroundTaskSupervisor()

    await supervisor.start()
    await supervisor.start()
    await supervisor.stop()

    with pytest.raises(RuntimeError, match="stopped"):
        await supervisor.start()


async def test_duplicate_task_name_fails_closed() -> None:
    supervisor = BackgroundTaskSupervisor()

    async def worker() -> None:
        await asyncio.Event().wait()

    supervisor.spawn(_spec("same-name"), worker)
    with pytest.raises(ValueError, match="already registered"):
        supervisor.spawn(_spec("same-name", owner="other-owner"), worker)

    await supervisor.stop()


async def test_failure_is_observable_without_restart() -> None:
    supervisor = BackgroundTaskSupervisor()

    async def broken() -> None:
        raise RuntimeError("boom")

    task = supervisor.spawn(_spec("broken"), broken)
    await task

    [snapshot] = supervisor.snapshot()
    assert snapshot.state == "failed"
    assert snapshot.attempts == 1
    assert snapshot.restarts == 0
    assert snapshot.last_error == "RuntimeError: boom"

    await supervisor.stop()


async def test_restart_on_failure_is_bounded() -> None:
    supervisor = BackgroundTaskSupervisor()
    attempts = 0

    async def broken() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError(f"boom-{attempts}")

    task = supervisor.spawn(
        _spec(
            "bounded",
            restart=RestartPolicy.ON_FAILURE,
            max_restarts=2,
        ),
        broken,
    )
    await task

    [snapshot] = supervisor.snapshot()
    assert attempts == 3
    assert snapshot.state == "failed"
    assert snapshot.attempts == 3
    assert snapshot.restarts == 2
    assert snapshot.last_error == "RuntimeError: boom-3"

    await supervisor.stop()


async def test_failure_history_is_bounded_to_twenty_records() -> None:
    supervisor = BackgroundTaskSupervisor()
    attempts = 0

    async def broken() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError(f"history-{attempts}")

    task = supervisor.spawn(
        _spec(
            "failure-history",
            restart=RestartPolicy.ON_FAILURE,
            max_restarts=25,
        ),
        broken,
    )
    await task

    [snapshot] = supervisor.snapshot()
    assert attempts == 26
    assert len(snapshot.failure_history) == 20
    assert snapshot.failure_history[0].error == "RuntimeError: history-7"
    assert snapshot.failure_history[-1].error == "RuntimeError: history-26"

    await supervisor.stop()


async def test_stop_during_restart_backoff_records_cancellation() -> None:
    supervisor = BackgroundTaskSupervisor()
    failed = asyncio.Event()

    async def broken() -> None:
        failed.set()
        raise RuntimeError("retry later")

    task = supervisor.spawn(
        TaskSpec(
            name="backing-off",
            owner="tests",
            kind=TaskKind.PERIODIC,
            restart=RestartPolicy.ON_FAILURE,
            shutdown=ShutdownPolicy.CANCEL,
            max_restarts=2,
            backoff_seconds=60.0,
            max_backoff_seconds=60.0,
        ),
        broken,
    )
    await failed.wait()
    await asyncio.sleep(0)
    assert supervisor.snapshot()[0].state == "backoff"

    await supervisor.stop()

    assert task.cancelled()
    assert supervisor.snapshot()[0].state == "cancelled"


async def test_stop_cancels_and_awaits_worker_with_cancellation_propagation() -> None:
    supervisor = BackgroundTaskSupervisor()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def worker() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = supervisor.spawn(_spec("cancel-me"), worker)
    await started.wait()
    await supervisor.stop()

    assert cancelled.is_set()
    assert task.done()
    assert task.cancelled()
    [snapshot] = supervisor.snapshot()
    assert snapshot.state == "cancelled"
    assert snapshot.cancelled is True


async def test_stop_owner_only_stops_owned_tasks() -> None:
    supervisor = BackgroundTaskSupervisor()
    alpha_started = asyncio.Event()
    beta_started = asyncio.Event()

    async def alpha() -> None:
        alpha_started.set()
        await asyncio.Event().wait()

    async def beta() -> None:
        beta_started.set()
        await asyncio.Event().wait()

    alpha_task = supervisor.spawn(_spec("alpha", owner="owner-a"), alpha)
    beta_task = supervisor.spawn(_spec("beta", owner="owner-b"), beta)
    await asyncio.gather(alpha_started.wait(), beta_started.wait())

    await supervisor.stop_owner("owner-a")

    assert alpha_task.cancelled()
    assert beta_task.done() is False
    snapshots = {item.name: item for item in supervisor.snapshot()}
    assert snapshots["alpha"].state == "cancelled"
    assert snapshots["beta"].state == "running"

    await supervisor.stop()


async def test_wait_shutdown_allows_graceful_completion() -> None:
    supervisor = BackgroundTaskSupervisor()
    release = asyncio.Event()

    async def worker() -> None:
        await release.wait()

    task = supervisor.spawn(_spec("graceful", shutdown=ShutdownPolicy.WAIT), worker)
    release.set()
    await supervisor.stop_owner("tests")

    assert task.done()
    assert task.cancelled() is False
    [snapshot] = supervisor.snapshot()
    assert snapshot.state == "completed"

    await supervisor.stop()


async def test_concurrent_stop_is_idempotent_and_spawn_after_stop_is_refused() -> None:
    supervisor = BackgroundTaskSupervisor()
    started = asyncio.Event()
    cancellation_count = 0

    async def worker() -> None:
        nonlocal cancellation_count
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_count += 1
            raise

    supervisor.spawn(_spec("one-stop"), worker)
    await started.wait()
    await asyncio.gather(supervisor.stop(), supervisor.stop(), supervisor.stop())

    assert cancellation_count == 1
    with pytest.raises(RuntimeError, match="stopped"):
        supervisor.spawn(_spec("too-late"), worker)


async def test_cancelling_stop_caller_does_not_abandon_shared_shutdown() -> None:
    supervisor = BackgroundTaskSupervisor()
    worker_cancelled = asyncio.Event()

    async def worker() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            worker_cancelled.set()
            await asyncio.sleep(0)
            raise

    supervisor.spawn(_spec("shielded-stop"), worker)
    stop_caller = asyncio.create_task(supervisor.stop())
    await asyncio.sleep(0)
    stop_caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop_caller

    await supervisor.stop()
    assert worker_cancelled.is_set()


async def test_plugin_bus_tick_loop_is_owned_by_supervisor() -> None:
    supervisor = BackgroundTaskSupervisor()
    bus = PluginBus(task_supervisor=supervisor)

    bus.start_tick_loop(cast(Any, object()), interval=3600.0)
    await asyncio.sleep(0)

    [snapshot] = supervisor.snapshot()
    assert snapshot.name == "plugin_bus.tick"
    assert snapshot.owner == "kernel.plugin_bus"
    assert snapshot.kind is TaskKind.PERIODIC
    await bus.stop_tick_loop()
    assert supervisor.snapshot()[0].state == "cancelled"

    await supervisor.stop()


async def test_humanization_health_guard_loop_is_owned_by_supervisor(tmp_path) -> None:
    supervisor = BackgroundTaskSupervisor()
    guard = HumanizationHealthGuard(
        db_path=tmp_path / "missing.db",
        interval_s=3600.0,
        task_supervisor=supervisor,
    )

    guard.start()
    await asyncio.sleep(0)

    [snapshot] = supervisor.snapshot()
    assert snapshot.name == "humanization.health_guard"
    assert snapshot.owner == "services.humanization"
    await guard.stop()
    assert supervisor.snapshot()[0].state == "cancelled"

    await supervisor.stop()


async def test_dream_loop_is_owned_by_supervisor() -> None:
    supervisor = BackgroundTaskSupervisor()
    agent = DreamAgent(
        store=cast(Any, object()),
        task_supervisor=supervisor,
    )
    started = asyncio.Event()

    async def loop(_api_call: Any) -> None:
        started.set()
        await asyncio.Event().wait()

    agent._loop = loop  # type: ignore[method-assign]
    agent.start(cast(Any, object()))
    await started.wait()

    [snapshot] = supervisor.snapshot()
    assert snapshot.name == "dream.loop"
    assert snapshot.owner == "plugins.dream"
    await agent.stop()
    assert supervisor.snapshot()[0].state == "cancelled"

    await supervisor.stop()


async def test_failed_background_task_is_visible_in_admin_health(tmp_path) -> None:
    supervisor = BackgroundTaskSupervisor()

    async def broken() -> None:
        raise RuntimeError("health-visible")

    task = supervisor.spawn(_spec("health-failure", owner="tests.health"), broken)
    await task
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()

    payload = await collect_service_health(
        ctx=SimpleNamespace(
            storage_dir=storage_dir,
            background_task_supervisor=supervisor,
        ),
    )
    service = next(
        item for item in payload["services"] if item["id"] == "background_tasks"
    )

    assert service["status"] == "error"
    assert service["meta"]["failed_count"] == 1
    assert service["meta"]["tasks"][0]["last_error"] == "RuntimeError: health-visible"

    await supervisor.stop()
