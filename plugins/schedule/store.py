"""ScheduleStore — JSON file persistence + in-memory cache for daily schedules."""

from __future__ import annotations

import json
import os
from pathlib import Path

from loguru import logger

from plugins.schedule.types import Schedule, TimeSlot, normalize_activity_label

_L = logger.bind(channel="schedule")


class ScheduleStore:
    """Read/write Schedule JSON files. Cache the current day's schedule in memory."""

    def __init__(self, storage_dir: str = "storage/schedule") -> None:
        self._dir = Path(storage_dir)
        self._current: Schedule | None = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def current(self) -> Schedule | None:
        return self._current

    async def startup(self) -> None:
        """Ensure storage dir exists and load today's schedule if present."""
        self._dir.mkdir(parents=True, exist_ok=True)

    def load(self, date_str: str) -> Schedule | None:
        """Load a schedule file from disk. Returns None if missing or malformed."""
        path = self._path(date_str)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            slots_data = data["slots"]
            if not isinstance(slots_data, list):
                raise TypeError("slots must be a list")
            slots: list[TimeSlot] = []
            for raw_slot in slots_data:
                if not isinstance(raw_slot, dict):
                    raise TypeError("slot must be an object")
                raw_activity = raw_slot.get("activity", "")
                activity = normalize_activity_label(raw_activity)
                if not activity:
                    legacy_value = str(raw_activity or "").strip()
                    _L.warning(
                        "legacy schedule detected | path={} invalid_activity={!r} — deleting for regenerate",
                        path,
                        legacy_value,
                    )
                    self._invalidate_legacy_file(path)
                    if self._current is not None and self._current.date == date_str:
                        self._current = None
                    return None
                slots.append(TimeSlot(
                    time=str(raw_slot.get("time", "") or ""),
                    activity=activity,
                    mood_hint=str(raw_slot.get("mood_hint", "") or ""),
                    location=str(raw_slot.get("location", "") or ""),
                    description=str(raw_slot.get("description", "") or ""),
                ))
            schedule = Schedule(
                date=data["date"],
                day_narrative=data.get("day_narrative", ""),
                slots=slots,
                generated_at=data.get("generated_at", ""),
                theme=data.get("theme", ""),
            )
            self._current = schedule
            _L.info("schedule loaded | date={} theme={}", schedule.date, schedule.theme)
            return schedule
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            _L.error("schedule load failed | path={} error={}", path, e)
            return None

    def save(self, schedule: Schedule) -> None:
        """Persist a schedule to disk and set it as current."""
        path = self._path(schedule.date)
        data = {
            "date": schedule.date,
            "day_narrative": schedule.day_narrative,
            "theme": schedule.theme,
            "generated_at": schedule.generated_at,
            "slots": [
                {
                    "time": s.time,
                    "activity": s.activity,
                    "description": s.description,
                    "mood_hint": s.mood_hint,
                    "location": s.location,
                }
                for s in schedule.slots
            ],
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        self._current = schedule
        _L.info("schedule saved | date={} theme={} slots={}", schedule.date, schedule.theme, len(schedule.slots))

    def list_files(self) -> list[str]:
        """List available schedule date strings, newest first."""
        files: list[str] = []
        for f in sorted(self._dir.glob("*.json"), reverse=True):
            name = f.stem
            if len(name) == 10 and name[4] == "-" and name[7] == "-":
                files.append(name)
        return files

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _path(self, date_str: str) -> Path:
        return self._dir / f"{date_str}.json"

    @staticmethod
    def _invalidate_legacy_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except TypeError:
            if path.exists():
                path.unlink()
