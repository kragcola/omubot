"""Offline Hawkes-cache refresher for scheduler heat signals."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, cast

from loguru import logger

from services.scheduler_hawkes.cache import HawkesCache, snapshot_from_times

_L = logger.bind(channel="scheduler")


class HawkesOfflineRefresher:
    def __init__(
        self,
        *,
        message_log: Any,
        cache: HawkesCache | None = None,
        interval_s: float = 600.0,
        window_s: float = 3600.0,
        limit_per_group: int = 500,
    ) -> None:
        self._message_log = message_log
        self._cache = cache or HawkesCache()
        self._interval_s = max(10.0, float(interval_s))
        self._window_s = max(60.0, float(window_s))
        self._limit_per_group = max(10, int(limit_per_group))
        self._task: asyncio.Task[None] | None = None

    @property
    def cache(self) -> HawkesCache:
        return self._cache

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def run_once(self, *, now: float | None = None) -> int:
        if self._message_log is None:
            return 0
        list_group_ids = getattr(self._message_log, "list_group_ids", None)
        query_recent = getattr(self._message_log, "query_recent", None)
        if not callable(list_group_ids) or not callable(query_recent):
            return 0
        list_group_ids_call = cast(Callable[[], Awaitable[list[Any]]], list_group_ids)
        query_recent_call = cast(Callable[..., Awaitable[list[dict[str, Any]]]], query_recent)

        current = float(time.time() if now is None else now)
        count = 0
        for group_id in await list_group_ids_call():
            rows = await query_recent_call(str(group_id), limit=self._limit_per_group)
            times = [
                float(row.get("created_at", 0.0) or 0.0)
                for row in rows
                if str(row.get("role", "user") or "user") == "user"
            ]
            snapshot = snapshot_from_times(str(group_id), times, now=current, window_s=self._window_s)
            self._cache.upsert(snapshot)
            count += 1
        return count

    async def _loop(self) -> None:
        while True:
            try:
                updated = await self.run_once()
                _L.debug("scheduler_hawkes_refreshed | groups={}", updated)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _L.warning("scheduler_hawkes_refresh_failed | err={}", exc)
            await asyncio.sleep(self._interval_s)
