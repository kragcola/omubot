"""Transactional startup contracts for BackupScheduler task ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

import services.storage.backup_scheduler as backup_scheduler_module
from kernel.background_tasks import BackgroundTaskSupervisor, TaskKind
from services.storage.backup import BackupItem
from services.storage.backup_scheduler import BackupScheduler


class _ControlledTask:
    def __init__(self, coro: Coroutine[Any, Any, Any]) -> None:
        self._coro = coro
        self._closed = False
        self.cancel_calls = 0
        self.await_calls = 0
        self.cancel_requested = False

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.cancel_requested = True

    def __await__(self):
        self.await_calls += 1
        self.dispose()

        async def complete() -> None:
            if self.cancel_requested:
                raise asyncio.CancelledError

        return complete().__await__()

    def dispose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._coro.close()


@pytest.mark.asyncio
async def test_scheduler_loops_are_owned_by_process_supervisor(tmp_path: Path) -> None:
    supervisor = BackgroundTaskSupervisor()
    scheduler = BackupScheduler(
        storage_dir=tmp_path / "storage",
        repo_root=tmp_path,
        enabled=True,
        quick_check_enabled=True,
        task_supervisor=supervisor,
    )

    await scheduler.start()
    await asyncio.sleep(0)

    snapshots = {item.name: item for item in supervisor.snapshot()}
    assert set(snapshots) == {"backup.daily", "backup.quick_check"}
    assert all(item.owner == "services.storage.backup" for item in snapshots.values())
    assert all(item.kind is TaskKind.PERIODIC for item in snapshots.values())

    await scheduler.stop()
    assert all(item.state == "cancelled" for item in supervisor.snapshot())
    await supervisor.stop()


@pytest.mark.asyncio
async def test_concurrent_start_is_idempotent_for_live_supervised_tasks(
    tmp_path: Path,
) -> None:
    supervisor = BackgroundTaskSupervisor()
    scheduler = BackupScheduler(
        storage_dir=tmp_path / "storage",
        repo_root=tmp_path,
        enabled=True,
        quick_check_enabled=True,
        task_supervisor=supervisor,
    )

    try:
        results = await asyncio.gather(
            scheduler.start(),
            scheduler.start(),
            return_exceptions=True,
        )
        await asyncio.sleep(0)

        assert results == [None, None]
        assert scheduler._daily_task is not None
        assert scheduler._daily_task.done() is False
        assert scheduler._quick_check_task is not None
        assert scheduler._quick_check_task.done() is False
        snapshots = {item.name: item for item in supervisor.snapshot()}
        assert set(snapshots) == {"backup.daily", "backup.quick_check"}
        assert all(item.state == "running" for item in snapshots.values())
    finally:
        await scheduler.stop()
        await supervisor.stop()


@pytest.mark.asyncio
async def test_start_supplements_only_missing_desired_supervised_loop(
    tmp_path: Path,
) -> None:
    supervisor = BackgroundTaskSupervisor()
    scheduler = BackupScheduler(
        storage_dir=tmp_path / "storage",
        repo_root=tmp_path,
        enabled=True,
        quick_check_enabled=False,
        task_supervisor=supervisor,
    )
    await scheduler.start()
    await asyncio.sleep(0)
    original_daily = scheduler._daily_task
    assert original_daily is not None
    assert scheduler._quick_check_task is None

    scheduler._quick_check_enabled = True
    try:
        await scheduler.start()
        await asyncio.sleep(0)

        assert scheduler._daily_task is original_daily
        assert original_daily.done() is False
        quick_check_task = scheduler._quick_check_task
        assert quick_check_task is not None
        assert quick_check_task.done() is False
        snapshots = {item.name: item for item in supervisor.snapshot()}
        assert set(snapshots) == {"backup.daily", "backup.quick_check"}
        assert all(item.state == "running" for item in snapshots.values())
    finally:
        await scheduler.stop()
        await supervisor.stop()


@pytest.mark.asyncio
async def test_repeated_start_reuses_live_direct_tasks(tmp_path: Path) -> None:
    scheduler = BackupScheduler(
        storage_dir=tmp_path / "storage",
        repo_root=tmp_path,
        enabled=True,
        quick_check_enabled=True,
    )
    await scheduler.start()
    await asyncio.sleep(0)
    original_daily = scheduler._daily_task
    original_quick_check = scheduler._quick_check_task

    try:
        await scheduler.start()

        assert scheduler._daily_task is original_daily
        assert scheduler._quick_check_task is original_quick_check
        assert scheduler._daily_task is not None
        assert scheduler._daily_task.done() is False
        assert scheduler._quick_check_task is not None
        assert scheduler._quick_check_task.done() is False
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_reload_cancels_then_reregisters_stable_supervised_tasks(
    tmp_path: Path,
) -> None:
    supervisor = BackgroundTaskSupervisor()
    scheduler = BackupScheduler(
        storage_dir=tmp_path / "storage",
        repo_root=tmp_path,
        enabled=True,
        quick_check_enabled=True,
        task_supervisor=supervisor,
    )
    await scheduler.start()
    await asyncio.sleep(0)

    await scheduler.reload(
        daily_time="05:00",
        keep_days=9,
        default_profile="daily",
        enabled=False,
        quick_check_enabled=False,
        quick_check_interval_minutes=90,
    )
    assert all(item.state == "cancelled" for item in supervisor.snapshot())

    await scheduler.reload(
        daily_time="05:00",
        keep_days=9,
        default_profile="daily",
        enabled=True,
        quick_check_enabled=True,
        quick_check_interval_minutes=90,
    )
    await asyncio.sleep(0)
    snapshots = {item.name: item for item in supervisor.snapshot()}
    assert set(snapshots) == {"backup.daily", "backup.quick_check"}
    assert all(item.state == "running" for item in snapshots.values())

    await scheduler.stop()
    await supervisor.stop()


@pytest.mark.asyncio
async def test_concurrent_reload_keeps_supervised_tasks_active(
    tmp_path: Path,
) -> None:
    supervisor = BackgroundTaskSupervisor()
    scheduler = BackupScheduler(
        storage_dir=tmp_path / "storage",
        repo_root=tmp_path,
        enabled=True,
        quick_check_enabled=True,
        task_supervisor=supervisor,
    )
    await scheduler.start()
    await asyncio.sleep(0)

    try:
        results = await asyncio.gather(
            scheduler.reload(
                daily_time="05:15",
                keep_days=11,
                default_profile="daily",
                enabled=True,
                quick_check_enabled=True,
                quick_check_interval_minutes=75,
            ),
            scheduler.reload(
                daily_time="05:15",
                keep_days=11,
                default_profile="daily",
                enabled=True,
                quick_check_enabled=True,
                quick_check_interval_minutes=75,
            ),
            return_exceptions=True,
        )
        await asyncio.sleep(0)

        assert results == [None, None]
        snapshots = {item.name: item for item in supervisor.snapshot()}
        assert set(snapshots) == {"backup.daily", "backup.quick_check"}
        assert all(item.state == "running" for item in snapshots.values())
        assert scheduler._daily_task is not None
        assert scheduler._daily_task.done() is False
        assert scheduler._quick_check_task is not None
        assert scheduler._quick_check_task.done() is False
    finally:
        await scheduler.stop()
        await supervisor.stop()


@pytest.mark.parametrize(
    ("required", "expected_emergency_profiles"),
    ((False, []), (True, ["pre-change"])),
)
@pytest.mark.asyncio
async def test_missing_database_only_triggers_emergency_backup_when_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    required: bool,
    expected_emergency_profiles: list[str],
) -> None:
    scheduler = BackupScheduler(
        storage_dir=tmp_path / "storage",
        repo_root=tmp_path,
        enabled=True,
        quick_check_enabled=True,
    )
    monkeypatch.setattr(
        backup_scheduler_module,
        "BACKUP_REGISTRY",
        [
            BackupItem(
                "probe_target",
                "storage/probe_target.db",
                "sqlite",
                required=required,
            )
        ],
    )
    emergency_profiles: list[str] = []

    def create_backup(profile: str, _host_mode: bool) -> dict[str, Any]:
        emergency_profiles.append(profile)
        return {"backup_id": "emergency", "summary": {"trusted": True}}

    monkeypatch.setattr(scheduler._service, "create", create_backup)
    sleep_calls = 0

    async def run_one_iteration(_delay: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(backup_scheduler_module.asyncio, "sleep", run_one_iteration)

    with pytest.raises(asyncio.CancelledError):
        await scheduler._quick_check_loop()

    assert emergency_profiles == expected_emergency_profiles


@pytest.mark.asyncio
async def test_reload_cancelled_while_waiting_for_lifecycle_lock_keeps_tasks(
    tmp_path: Path,
) -> None:
    supervisor = BackgroundTaskSupervisor()
    scheduler = BackupScheduler(
        storage_dir=tmp_path / "storage",
        repo_root=tmp_path,
        enabled=True,
        quick_check_enabled=True,
        task_supervisor=supervisor,
    )
    await scheduler.start()
    await asyncio.sleep(0)
    original_settings = scheduler.settings
    await scheduler._lifecycle_lock.acquire()
    reload_task = asyncio.create_task(
        scheduler.reload(
            daily_time="06:45",
            keep_days=13,
            default_profile="daily",
            enabled=True,
            quick_check_enabled=False,
            quick_check_interval_minutes=120,
        )
    )
    await asyncio.sleep(0)

    try:
        reload_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reload_task
    finally:
        scheduler._lifecycle_lock.release()

    try:
        assert scheduler.settings == original_settings
        snapshots = {item.name: item for item in supervisor.snapshot()}
        assert set(snapshots) == {"backup.daily", "backup.quick_check"}
        assert all(item.state == "running" for item in snapshots.values())
    finally:
        await scheduler.stop()
        await supervisor.stop()


@pytest.mark.asyncio
async def test_reload_cancellation_during_stop_completes_consistent_new_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = BackgroundTaskSupervisor()
    scheduler = BackupScheduler(
        storage_dir=tmp_path / "storage",
        repo_root=tmp_path,
        enabled=True,
        quick_check_enabled=True,
        task_supervisor=supervisor,
    )
    await scheduler.start()
    await asyncio.sleep(0)
    old_daily = scheduler._daily_task
    old_quick_check = scheduler._quick_check_task
    stop_entered = asyncio.Event()
    allow_stop = asyncio.Event()
    original_stop_owner = supervisor.stop_owner

    async def blocked_stop_owner(owner: str) -> None:
        stop_entered.set()
        await allow_stop.wait()
        await original_stop_owner(owner)

    monkeypatch.setattr(supervisor, "stop_owner", blocked_stop_owner)
    reload_task = asyncio.create_task(
        scheduler.reload(
            daily_time="07:20",
            keep_days=15,
            default_profile="migration",
            enabled=True,
            quick_check_enabled=True,
            quick_check_interval_minutes=150,
        )
    )
    await stop_entered.wait()

    reload_task.cancel()
    await asyncio.sleep(0)
    allow_stop.set()
    with pytest.raises(asyncio.CancelledError):
        await reload_task
    await asyncio.sleep(0)

    try:
        assert scheduler.settings == {
            "enabled": True,
            "daily_time": "07:20",
            "keep_days": 15,
            "default_profile": "migration",
            "quick_check_enabled": True,
            "quick_check_interval_minutes": 150,
        }
        assert scheduler._daily_task is not old_daily
        assert scheduler._quick_check_task is not old_quick_check
        assert scheduler._daily_task is not None
        assert scheduler._daily_task.done() is False
        assert scheduler._quick_check_task is not None
        assert scheduler._quick_check_task.done() is False
        snapshots = {item.name: item for item in supervisor.snapshot()}
        assert set(snapshots) == {"backup.daily", "backup.quick_check"}
        assert all(item.state == "running" for item in snapshots.values())
    finally:
        await scheduler.stop()
        await supervisor.stop()


@pytest.mark.asyncio
async def test_start_rolls_back_daily_task_when_quick_check_task_creation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = BackupScheduler(
        storage_dir=tmp_path / "storage",
        repo_root=tmp_path,
        enabled=True,
        quick_check_enabled=True,
    )
    create_error = RuntimeError("quick-check task creation failed")
    daily_task: _ControlledTask | None = None
    create_calls = 0

    def create_task(coro: Coroutine[Any, Any, Any]) -> _ControlledTask:
        nonlocal create_calls, daily_task
        create_calls += 1
        if create_calls == 1:
            daily_task = _ControlledTask(coro)
            return daily_task
        coro.close()
        raise create_error

    monkeypatch.setattr(asyncio, "create_task", create_task)

    try:
        with pytest.raises(RuntimeError) as raised:
            await scheduler.start()

        assert raised.value is create_error
        assert create_calls == 2
        assert daily_task is not None
        assert daily_task.cancel_calls == 1, "failed startup must cancel the acquired daily task"
        assert daily_task.await_calls == 1, "failed startup must await daily task cancellation"
        assert scheduler._daily_task is None
        assert scheduler._quick_check_task is None

        await scheduler.stop()
        await scheduler.stop()

        assert daily_task.cancel_calls == 1
        assert daily_task.await_calls == 1
    finally:
        if daily_task is not None:
            daily_task.dispose()
