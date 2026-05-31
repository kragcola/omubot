"""Event-boundary detectors for dream-time consolidation triggers."""

from __future__ import annotations

import time
from typing import Any


class EventBoundaryDetector:
    def __init__(self, *, cooldown_s: float = 1800.0) -> None:
        self._cooldown_s = max(0.0, float(cooldown_s))
        self._last_triggered: dict[str, float] = {}

    async def check_silence(self, group_id: str, message_log: Any, threshold_min: int = 30) -> bool:
        if message_log is None or not str(group_id or "").strip():
            return False
        query_recent = getattr(message_log, "query_recent", None)
        if not callable(query_recent):
            return False
        rows = await query_recent(str(group_id), limit=1)
        if not rows:
            return False
        try:
            last_created_at = float(rows[-1].get("created_at", 0.0))
        except (AttributeError, TypeError, ValueError):
            return False
        return last_created_at > 0 and (time.time() - last_created_at) >= max(1, int(threshold_min or 30)) * 60

    async def check_mood_reversal(
        self,
        mood_engine: Any,
        group_id: str,
        variance_threshold: float = 0.4,
    ) -> bool:
        if mood_engine is None or not str(group_id or "").strip():
            return False
        recent_profiles = getattr(mood_engine, "recent_profiles", None)
        if not callable(recent_profiles):
            return False
        profiles = recent_profiles(group_id=str(group_id), session_id=f"group_{group_id}", within_s=1800.0)
        if len(profiles) < 2:
            return False
        first = float(getattr(profiles[0], "valence", 0.0))
        last = float(getattr(profiles[-1], "valence", 0.0))
        return (first * last) < 0 and abs(last - first) >= max(0.0, float(variance_threshold or 0.0))

    async def detect(
        self,
        *,
        group_id: str,
        message_log: Any,
        mood_engine: Any,
        threshold_min: int = 30,
        variance_threshold: float = 0.4,
    ) -> tuple[bool, str]:
        if self._cooldown_active(group_id):
            return False, ""
        if await self.check_silence(group_id, message_log, threshold_min=threshold_min):
            self._mark_triggered(group_id)
            return True, "silence"
        if await self.check_mood_reversal(mood_engine, group_id, variance_threshold=variance_threshold):
            self._mark_triggered(group_id)
            return True, "mood_reversal"
        return False, ""

    def _cooldown_active(self, group_id: str) -> bool:
        last = self._last_triggered.get(str(group_id), 0.0)
        return (time.monotonic() - last) < self._cooldown_s

    def _mark_triggered(self, group_id: str) -> None:
        self._last_triggered[str(group_id)] = time.monotonic()
