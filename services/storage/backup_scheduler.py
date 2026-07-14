"""Backup scheduler — asyncio-based daily backup trigger.

Two coroutines run concurrently:

- daily backup loop: sleeps until `daily_time`, calls `BackupService.create`,
  prunes backups older than `keep_days`
- quick_check loop (Phase 2 corruption defense): every
  `quick_check_interval_minutes`, probes every SQLite DB in the registry via
  `PRAGMA quick_check` + `journal_mode`. Any non-"ok" result triggers a
  loguru error on `channel="backup"` (auto-routes to admin SSE +
  RuntimeErrorStore) **and** an immediate `pre-change` profile backup so
  the last known-clean state is captured before further corruption spreads.

Logging is unified through loguru `_L = logger.bind(channel="backup")` so
admin's events SSE picks up alarms without any extra wiring.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from loguru import logger

from kernel.background_tasks import (
    BackgroundTaskSupervisor,
    ShutdownPolicy,
    TaskKind,
    TaskSpec,
)
from services.storage.backup import BACKUP_REGISTRY, BackupService

_L = logger.bind(channel="backup")


@dataclass(frozen=True)
class QuickCheckResult:
    """Single SQLite probe outcome."""

    db_id: str
    path: str
    ok: bool
    quick_check: str
    journal_mode: str
    error: str | None = None


class BackupScheduler:
    def __init__(
        self,
        storage_dir: Path,
        repo_root: Path,
        daily_time: str = "04:30",
        keep_days: int = 7,
        default_profile: str = "daily",
        enabled: bool = True,
        quick_check_enabled: bool = True,
        quick_check_interval_minutes: int = 60,
        task_supervisor: BackgroundTaskSupervisor | None = None,
    ):
        self._storage_dir = Path(storage_dir).resolve()
        self._repo_root = Path(repo_root).resolve()
        self._service = BackupService(storage_dir=self._storage_dir, repo_root=self._repo_root)
        self._daily_time = self._parse_time(daily_time)
        self._keep_days = keep_days
        self._default_profile = default_profile
        self._enabled = enabled
        self._quick_check_enabled = quick_check_enabled
        self._quick_check_interval = max(15, int(quick_check_interval_minutes)) * 60
        self._task_supervisor = task_supervisor
        self._daily_task: asyncio.Task | None = None
        self._quick_check_task: asyncio.Task | None = None
        self._last_quick_check: list[QuickCheckResult] = []
        self._last_quick_check_at: datetime | None = None
        self._lifecycle_lock = asyncio.Lock()

    @staticmethod
    def _parse_time(s: str) -> time:
        parts = s.split(":")
        return time(int(parts[0]), int(parts[1]))

    def _seconds_until_next_run(self) -> float:
        now = datetime.now()
        target = now.replace(
            hour=self._daily_time.hour,
            minute=self._daily_time.minute,
            second=0,
            microsecond=0,
        )
        if target <= now:
            target += timedelta(days=1)
        delta = (target - now).total_seconds()
        return max(delta, 60.0)

    async def start(self) -> None:
        async with self._lifecycle_lock:
            await self._start_unlocked()

    @staticmethod
    def _task_is_active(task: asyncio.Task | None) -> bool:
        return task is not None and not task.done()

    async def _start_unlocked(self) -> None:
        if not self._enabled:
            _L.info("backup scheduler disabled by config")
            return
        daily_active = self._task_is_active(self._daily_task)
        quick_check_active = self._task_is_active(self._quick_check_task)
        if daily_active and (not self._quick_check_enabled or quick_check_active):
            _L.debug("backup scheduler already has all desired loops")
            return

        if not daily_active:
            self._daily_task = None
        if not quick_check_active:
            self._quick_check_task = None

        if self._task_supervisor is not None:
            created_daily = False
            try:
                if self._daily_task is None:
                    self._daily_task = self._task_supervisor.spawn(
                        TaskSpec(
                            name="backup.daily",
                            owner="services.storage.backup",
                            kind=TaskKind.PERIODIC,
                            shutdown=ShutdownPolicy.CANCEL,
                        ),
                        self._daily_loop,
                    )
                    created_daily = True
                if self._quick_check_enabled and self._quick_check_task is None:
                    self._quick_check_task = self._task_supervisor.spawn(
                        TaskSpec(
                            name="backup.quick_check",
                            owner="services.storage.backup",
                            kind=TaskKind.PERIODIC,
                            shutdown=ShutdownPolicy.CANCEL,
                        ),
                        self._quick_check_loop,
                    )
            except BaseException:
                if created_daily:
                    await self._task_supervisor.stop_owner("services.storage.backup")
                    self._daily_task = None
                    self._quick_check_task = None
                raise
            _L.info(f"backup scheduler started, daily_time={self._daily_time}")
            if self._quick_check_enabled and self._quick_check_task is not None:
                _L.info(f"quick_check loop started, interval={self._quick_check_interval}s")
            return

        daily_task = self._daily_task
        quick_check_task = self._quick_check_task
        created_tasks: list[asyncio.Task] = []
        try:
            if daily_task is None:
                daily_task = asyncio.create_task(self._daily_loop())
                created_tasks.append(daily_task)
            if self._quick_check_enabled and quick_check_task is None:
                quick_check_task = asyncio.create_task(self._quick_check_loop())
                created_tasks.append(quick_check_task)
        except BaseException:
            for task in created_tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            raise

        self._daily_task = daily_task
        self._quick_check_task = quick_check_task
        _L.info(f"backup scheduler started, daily_time={self._daily_time}")
        if quick_check_task is not None:
            _L.info(f"quick_check loop started, interval={self._quick_check_interval}s")

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_unlocked()

    async def _stop_unlocked(self) -> None:
        if self._task_supervisor is not None:
            await self._task_supervisor.stop_owner("services.storage.backup")
            self._daily_task = None
            self._quick_check_task = None
            _L.info("backup scheduler loops stopped")
            return
        for task_name, task in (("daily", self._daily_task), ("quick_check", self._quick_check_task)):
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            _L.info(f"backup {task_name} loop stopped")
        self._daily_task = None
        self._quick_check_task = None

    async def run_now(self, profile: str | None = None) -> dict:
        """Trigger an immediate backup (used by admin API)."""
        target_profile = profile or self._default_profile
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._service.create, target_profile, False)

    async def run_quick_check_now(self) -> list[QuickCheckResult]:
        """Trigger an immediate SQLite quick_check sweep (admin API)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._probe_all_sqlite)

    @property
    def last_quick_check(self) -> tuple[datetime | None, list[QuickCheckResult]]:
        return self._last_quick_check_at, list(self._last_quick_check)

    @property
    def settings(self) -> dict:
        return {
            "enabled": self._enabled,
            "daily_time": f"{self._daily_time.hour:02d}:{self._daily_time.minute:02d}",
            "keep_days": self._keep_days,
            "default_profile": self._default_profile,
            "quick_check_enabled": self._quick_check_enabled,
            "quick_check_interval_minutes": self._quick_check_interval // 60,
        }

    async def reload(
        self,
        daily_time: str,
        keep_days: int,
        default_profile: str,
        enabled: bool,
        *,
        quick_check_enabled: bool | None = None,
        quick_check_interval_minutes: int | None = None,
    ) -> None:
        """Hot-reload config without restarting the bot."""
        async with self._lifecycle_lock:
            transaction = asyncio.create_task(
                self._reload_unlocked(
                    daily_time=daily_time,
                    keep_days=keep_days,
                    default_profile=default_profile,
                    enabled=enabled,
                    quick_check_enabled=quick_check_enabled,
                    quick_check_interval_minutes=quick_check_interval_minutes,
                ),
                name="backup:reload",
            )
            try:
                await asyncio.shield(transaction)
            except asyncio.CancelledError:
                await transaction
                raise

    async def _reload_unlocked(
        self,
        *,
        daily_time: str,
        keep_days: int,
        default_profile: str,
        enabled: bool,
        quick_check_enabled: bool | None,
        quick_check_interval_minutes: int | None,
    ) -> None:
        parsed_daily_time = self._parse_time(daily_time)
        parsed_quick_check_interval = (
            None if quick_check_interval_minutes is None else max(15, int(quick_check_interval_minutes)) * 60
        )
        had_tasks = self._daily_task is not None or self._quick_check_task is not None
        if had_tasks:
            await self._stop_unlocked()

        self._daily_time = parsed_daily_time
        self._keep_days = keep_days
        self._default_profile = default_profile
        self._enabled = enabled
        if quick_check_enabled is not None:
            self._quick_check_enabled = quick_check_enabled
        if parsed_quick_check_interval is not None:
            self._quick_check_interval = parsed_quick_check_interval

        if enabled:
            await self._start_unlocked()
            _L.info(f"backup scheduler enabled by reload, daily_time={self._daily_time}")
        else:
            _L.info("backup scheduler disabled by reload")

    async def _daily_loop(self) -> None:
        while True:
            try:
                wait = self._seconds_until_next_run()
                _L.debug(f"backup scheduler sleeping {wait:.0f}s")
                await asyncio.sleep(wait)
                loop = asyncio.get_event_loop()
                manifest = await loop.run_in_executor(None, self._service.create, self._default_profile, False)
                trusted = manifest.get("summary", {}).get("trusted", False)
                if trusted:
                    _L.info(f"scheduled backup ok: {manifest['backup_id']}")
                else:
                    _L.warning(f"scheduled backup untrusted: {manifest.get('summary')}")
                self._service.prune(keep_days=self._keep_days)
            except asyncio.CancelledError:
                raise
            except Exception:
                _L.exception("backup scheduler error, retrying in 1h")
                await asyncio.sleep(3600)

    async def _quick_check_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._quick_check_interval)
                loop = asyncio.get_event_loop()
                results = await loop.run_in_executor(None, self._probe_all_sqlite)
                self._last_quick_check = results
                self._last_quick_check_at = datetime.now()
                bad = [r for r in results if not r.ok]
                if not bad:
                    _L.debug(f"quick_check ok: {len(results)} dbs probed")
                    continue
                names = ", ".join(f"{r.db_id}({r.quick_check})" for r in bad)
                _L.error(f"SQLite quick_check failed for {len(bad)}/{len(results)} db(s): {names}")
                # Trigger an emergency pre-change backup so we capture whatever
                # state remains before the corruption spreads further.
                try:
                    manifest = await loop.run_in_executor(None, self._service.create, "pre-change", False)
                    trusted = manifest.get("summary", {}).get("trusted", False)
                    if trusted:
                        _L.warning(
                            f"emergency pre-change backup written after quick_check alarm: {manifest.get('backup_id')}"
                        )
                    else:
                        _L.error(
                            "emergency pre-change backup rejected: db corrupt before backup "
                            f"({manifest.get('summary')})"
                        )
                except Exception:
                    _L.exception("emergency pre-change backup failed")
            except asyncio.CancelledError:
                raise
            except Exception:
                _L.exception("quick_check loop error, retrying in 5min")
                await asyncio.sleep(300)

    def _probe_all_sqlite(self) -> list[QuickCheckResult]:
        results: list[QuickCheckResult] = []
        for item in BACKUP_REGISTRY:
            if item.item_type != "sqlite":
                continue
            db_path = self._repo_root / item.path
            results.append(self._probe_sqlite(item.id, db_path, required=item.required))
        return results

    @staticmethod
    def _probe_sqlite(
        db_id: str,
        db_path: Path,
        *,
        required: bool = True,
    ) -> QuickCheckResult:
        if not db_path.exists():
            if not required:
                return QuickCheckResult(
                    db_id=db_id,
                    path=str(db_path),
                    ok=True,
                    quick_check="missing_optional",
                    journal_mode="",
                )
            return QuickCheckResult(
                db_id=db_id,
                path=str(db_path),
                ok=False,
                quick_check="missing",
                journal_mode="",
                error="db file does not exist",
            )
        try:
            with sqlite3.connect(str(db_path), timeout=2.0) as conn:
                qc = str(conn.execute("PRAGMA quick_check").fetchone()[0])
                jm = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
            return QuickCheckResult(
                db_id=db_id,
                path=str(db_path),
                ok=qc.lower() == "ok",
                quick_check=qc,
                journal_mode=jm,
            )
        except Exception as exc:
            return QuickCheckResult(
                db_id=db_id,
                path=str(db_path),
                ok=False,
                quick_check="error",
                journal_mode="",
                error=str(exc)[:200],
            )
