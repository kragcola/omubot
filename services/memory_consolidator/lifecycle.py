"""Independent lifecycle owner for automatic memory consolidation."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from loguru import logger

from kernel.background_tasks import (
    BackgroundTaskSupervisor,
    RestartPolicy,
    ShutdownPolicy,
    TaskKind,
    TaskSpec,
)
from services.memory_consolidator.event_boundary import EventBoundaryDetector

_L = logger.bind(channel="memory_consolidator")
_TASK_NAME = "memory_consolidator.lifecycle"
_TASK_OWNER = "services.memory_consolidator"
_TICK_INTERVAL_SECONDS = 60.0


class MemoryConsolidatorLifecycle:
    """Schedule periodic and event-boundary consolidation independently of Dream."""

    def __init__(
        self,
        *,
        ctx: Any,
        settings_loader: Callable[[Any], Mapping[str, Any]],
        monotonic: Callable[[], float],
        event_boundary_enabled: Callable[[], bool],
        task_supervisor: BackgroundTaskSupervisor | None = None,
    ) -> None:
        self._ctx = ctx
        self._settings_loader = settings_loader
        self._monotonic = monotonic
        self._event_boundary_enabled = event_boundary_enabled
        self._task_supervisor = task_supervisor
        self._event_boundary_detector = EventBoundaryDetector()
        self._last_periodic_monotonic = 0.0
        self._task: asyncio.Task[None] | None = None
        self._stop_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._stopped = False

    async def start(self) -> None:
        """Start the independent tick loop once."""
        if self._stopping or self._stopped:
            raise RuntimeError("memory consolidator lifecycle is stopped")
        if self._task is not None and not self._task.done():
            return
        if self._task_supervisor is None:
            self._task = asyncio.create_task(
                self._loop(),
                name=_TASK_NAME,
            )
        else:
            self._task = self._task_supervisor.spawn(
                TaskSpec(
                    name=_TASK_NAME,
                    owner=_TASK_OWNER,
                    kind=TaskKind.PERIODIC,
                    restart=RestartPolicy.ON_FAILURE,
                    shutdown=ShutdownPolicy.CANCEL,
                    max_restarts=3,
                    backoff_seconds=1.0,
                    max_backoff_seconds=30.0,
                ),
                self._loop,
            )

    async def stop(self) -> None:
        """Stop the tick loop without abandoning cancellation if the caller is cancelled."""
        if self._stopped:
            return
        if self._stop_task is None:
            self._stopping = True
            self._stop_task = asyncio.create_task(
                self._stop_loop(),
                name=f"{_TASK_NAME}.stop",
            )
        await asyncio.shield(self._stop_task)

    async def tick_once(self) -> None:
        """Run enabled event-boundary and due periodic consolidation work once."""
        settings = self._settings_loader(getattr(self._ctx, "storage_dir", "storage"))
        consolidator_config = settings.get("consolidator", {})
        if not isinstance(consolidator_config, Mapping):
            return
        if not consolidator_config.get("auto_enabled", False):
            return

        consolidator = getattr(self._ctx, "memory_consolidator", None)
        message_log = getattr(self._ctx, "msg_log", None)
        list_group_ids = getattr(message_log, "list_group_ids", None)
        if consolidator is None or not callable(list_group_ids):
            return

        list_group_ids_call = cast(Callable[[], Awaitable[list[Any]]], list_group_ids)
        group_ids = list(await list_group_ids_call())[:5]
        if self._event_boundary_enabled():
            event_count = await self._run_event_boundaries(group_ids, message_log, consolidator)
            if event_count:
                _L.info(
                    "consolidator event-boundary tick completed | groups={}",
                    event_count,
                )

        interval_seconds = max(
            0,
            int(consolidator_config.get("interval_minutes", 360)),
        ) * 60
        now = float(self._monotonic())
        if now - self._last_periodic_monotonic < interval_seconds:
            return

        for group_id in group_ids:
            await consolidator.run_once(
                group_id=str(group_id),
                triggered_by="periodic_tick",
                max_batches=1,
                batch_size=30,
            )
        self._last_periodic_monotonic = now
        _L.info(
            "consolidator periodic tick completed | groups={}",
            len(group_ids),
        )

    async def _run_event_boundaries(
        self,
        group_ids: list[Any],
        message_log: Any,
        consolidator: Any,
    ) -> int:
        event_count = 0
        mood_engine = getattr(self._ctx, "mood_engine", None)
        for group_id in group_ids:
            triggered, reason = await self._event_boundary_detector.detect(
                group_id=str(group_id),
                message_log=message_log,
                mood_engine=mood_engine,
            )
            if not triggered:
                continue
            await consolidator.run_once(
                group_id=str(group_id),
                triggered_by=f"event_boundary:{reason}",
                max_batches=1,
                batch_size=30,
            )
            event_count += 1
        return event_count

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _L.warning("consolidator lifecycle tick failed | err={}", exc)
            await asyncio.sleep(_TICK_INTERVAL_SECONDS)

    async def _stop_loop(self) -> None:
        try:
            task = self._task
            if task is not None and not task.done():
                if self._task_supervisor is not None:
                    await self._task_supervisor.stop_owner(_TASK_OWNER)
                else:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        finally:
            self._task = None
            self._stopping = False
            self._stopped = True
