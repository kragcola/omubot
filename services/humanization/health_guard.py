from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from loguru import logger

_DEGRADED_GROUPS: dict[str, float] = {}
_HEALTHY_SINCE: dict[str, float] = {}

class CacheHitSample(NamedTuple):
    group_id: str
    hit_rate: float

def is_group_degraded(group_id: int | str | None) -> bool:
    return str(group_id or "").strip() in _DEGRADED_GROUPS

def clear_degraded_groups() -> None:
    _DEGRADED_GROUPS.clear()
    _HEALTHY_SINCE.clear()

def degraded_group_ids() -> list[str]:
    return sorted(_DEGRADED_GROUPS)

class HumanizationHealthGuard:
    def __init__(
        self,
        db_path: str | Path = "storage/usage.db",
        *,
        interval_s: float = 60.0,
        degrade_threshold: float = 0.80,
        recover_threshold: float = 0.85,
        recover_s: float = 600.0,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.interval_s = interval_s
        self.degrade_threshold = degrade_threshold
        self.recover_threshold = recover_threshold
        self.recover_s = recover_s
        self._now = now or time.time
        self._task: asyncio.Task[None] | None = None
    def poll_once(self) -> list[CacheHitSample]:
        if not self.db_path.is_file():
            return []
        try:
            with sqlite3.connect(self.db_path) as con:
                rows = con.execute(
                    "SELECT group_id, SUM(prompt_cache_hit_tokens), "
                    "SUM(prompt_cache_miss_tokens) FROM llm_calls "
                    "WHERE ts >= datetime('now', '-1 hour') "
                    "AND group_id IS NOT NULL AND group_id != '' GROUP BY group_id"
                ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("humanization health guard db read failed | err={}", exc)
            return []
        now = float(self._now())
        samples: list[CacheHitSample] = []
        for group_id, hit, miss in rows:
            total = int(hit or 0) + int(miss or 0)
            if total <= 0:
                continue
            sample = CacheHitSample(str(group_id), int(hit or 0) / total)
            samples.append(sample)
            self._apply_sample(sample, now)
        return samples
    def _apply_sample(self, sample: CacheHitSample, now: float) -> None:
        gid = sample.group_id
        if sample.hit_rate < self.degrade_threshold:
            _DEGRADED_GROUPS[gid] = now
            _HEALTHY_SINCE.pop(gid, None)
        elif sample.hit_rate < self.recover_threshold:
            _HEALTHY_SINCE.pop(gid, None)
        elif gid in _DEGRADED_GROUPS:
            healthy_since = _HEALTHY_SINCE.setdefault(gid, now)
            if now - healthy_since >= self.recover_s:
                _DEGRADED_GROUPS.pop(gid, None)
                _HEALTHY_SINCE.pop(gid, None)
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.to_thread(self.poll_once)
            await asyncio.sleep(self.interval_s)
