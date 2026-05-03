"""Hot-reloading time-slot talk multiplier from config/talk_schedule.json."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CST = ZoneInfo("Asia/Shanghai")

_DEFAULT_SCHEDULE: list[dict] = []
_DEFAULT_GLOBAL = 1.0


def _parse_time(t: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


class TalkSchedule:
    """Loads talk_schedule.json and resolves the time-of-day multiplier."""

    def __init__(self, path: str = "config/talk_schedule.json") -> None:
        self._path = path
        self._mtime: float = 0.0
        self._schedule: list[dict] = _DEFAULT_SCHEDULE
        self._global_mult: float = _DEFAULT_GLOBAL

    def get_time_multiplier(self) -> float:
        """Return the time-slot multiplier for the current CST time."""
        self._reload_if_changed()
        now = datetime.now(CST)
        now_minutes = now.hour * 60 + now.minute
        for slot in self._schedule:
            start = _parse_time(slot["start"])
            end = _parse_time(slot["end"])
            if start <= now_minutes < end:
                return self._global_mult * float(slot["multiplier"])
        # Handle overnight ranges (e.g. 22:00–02:00)
        for slot in self._schedule:
            start = _parse_time(slot["start"])
            end = _parse_time(slot["end"])
            if start > end and (now_minutes >= start or now_minutes < end):
                return self._global_mult * float(slot["multiplier"])
        return self._global_mult

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reload_if_changed(self) -> None:
        try:
            mtime = os.path.getmtime(self._path)
            if mtime == self._mtime:
                return
            self._mtime = mtime
        except OSError:
            return
        try:
            raw = Path(self._path).read_text(encoding="utf-8")
            data = json.loads(raw)
            self._global_mult = float(data.get("global_multiplier", _DEFAULT_GLOBAL))
            self._schedule = data.get("schedule", _DEFAULT_SCHEDULE)
        except (json.JSONDecodeError, ValueError, KeyError):
            pass  # keep current values on parse failure
