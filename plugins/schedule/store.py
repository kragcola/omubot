"""ScheduleStore — JSON file persistence + in-memory cache for daily schedules."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from plugins.schedule.types import Schedule, TimeSlot, normalize_activity_label

_L = logger.bind(channel="schedule")


@dataclass(frozen=True)
class ScheduleGovernanceSnapshot:
    """Read-only source view used by governed Worldbook commits.

    Unlike ``load()``, this view never attempts legacy repair or invalidation.
    A pending operator decision must not cause a source file to be modified.
    """

    schedule: Schedule
    governance_intent: dict[str, Any] | None
    source_sha256: str


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

    def load(self, date_str: str, *, update_current: bool = True) -> Schedule | None:
        """Load a schedule file from disk. Returns None if missing or malformed."""
        path = self._path(date_str)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            governed_source = isinstance(data, dict) and "worldbook_governance_intent" in data
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
                    if governed_source:
                        _L.error(
                            "governed schedule is invalid | path={} invalid_activity={!r} — preserving for recovery",
                            path,
                            legacy_value,
                        )
                    else:
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
            if update_current:
                self._current = schedule
            _L.info("schedule loaded | date={} theme={}", schedule.date, schedule.theme)
            return schedule
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            _L.error("schedule load failed | path={} error={}", path, e)
            return None

    def save(
        self,
        schedule: Schedule,
        *,
        governance_intent: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist a schedule to disk and set it as current."""
        path = self._path(schedule.date)
        data = self._source_payload(schedule)
        if governance_intent is not None:
            if not isinstance(governance_intent, Mapping):
                raise TypeError("governance_intent must be a mapping")
            data["worldbook_governance_intent"] = json.loads(
                json.dumps(
                    dict(governance_intent),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        self._assert_governed_source_unchanged(path, data)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        self._current = schedule
        _L.info("schedule saved | date={} theme={} slots={}", schedule.date, schedule.theme, len(schedule.slots))

    def load_governance_snapshot(
        self,
        date_str: str,
    ) -> ScheduleGovernanceSnapshot | None:
        """Load an immutable schedule source without legacy repair side effects."""
        path = self._path(date_str)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise TypeError("schedule root must be an object")
            schedule = self._schedule_from_data(data)
            if schedule.date != date_str:
                raise ValueError("schedule source filename does not match its date")
            raw_intent = data.get("worldbook_governance_intent")
            if raw_intent is None:
                intent = None
            elif isinstance(raw_intent, dict):
                intent = json.loads(
                    json.dumps(
                        raw_intent,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                )
            else:
                raise TypeError("worldbook governance intent must be an object")
            return ScheduleGovernanceSnapshot(
                schedule=schedule,
                governance_intent=intent,
                source_sha256=self.source_sha256(schedule),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            _L.error("governed schedule snapshot load failed | path={} error={}", path, exc)
            return None

    def source_exists(self, date_str: str) -> bool:
        """Return file existence without parsing or repairing its contents."""
        return self._path(date_str).is_file()

    @staticmethod
    def source_sha256(schedule: Schedule) -> str:
        payload = json.dumps(
            ScheduleStore._source_payload(schedule),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

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
    def _source_payload(schedule: Schedule) -> dict[str, Any]:
        return {
            "date": schedule.date,
            "day_narrative": schedule.day_narrative,
            "theme": schedule.theme,
            "generated_at": schedule.generated_at,
            "slots": [
                {
                    "time": slot.time,
                    "activity": slot.activity,
                    "description": slot.description,
                    "mood_hint": slot.mood_hint,
                    "location": slot.location,
                }
                for slot in schedule.slots
            ],
        }

    @staticmethod
    def _assert_governed_source_unchanged(
        path: Path,
        candidate: Mapping[str, Any],
    ) -> None:
        """Refuse any mutation that would alter a persisted governance intent."""
        if not path.is_file():
            return
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError("existing schedule source is unreadable") from exc
        try:
            existing = json.loads(raw)
        except json.JSONDecodeError as exc:
            if "worldbook_governance_intent" in raw:
                raise ValueError("governed schedule source is unreadable") from exc
            return
        if not isinstance(existing, dict):
            return
        if "worldbook_governance_intent" not in existing:
            return
        if existing != dict(candidate):
            raise ValueError("governed schedule source is immutable")

    @staticmethod
    def _schedule_from_data(data: Mapping[str, Any]) -> Schedule:
        slots_data = data["slots"]
        if not isinstance(slots_data, list):
            raise TypeError("slots must be a list")
        slots: list[TimeSlot] = []
        for raw_slot in slots_data:
            if not isinstance(raw_slot, dict):
                raise TypeError("slot must be an object")
            activity = normalize_activity_label(raw_slot.get("activity", ""))
            if not activity:
                raise ValueError("schedule has an invalid activity label")
            slots.append(TimeSlot(
                time=str(raw_slot.get("time", "") or ""),
                activity=activity,
                mood_hint=str(raw_slot.get("mood_hint", "") or ""),
                location=str(raw_slot.get("location", "") or ""),
                description=str(raw_slot.get("description", "") or ""),
            ))
        return Schedule(
            date=data["date"],
            day_narrative=data.get("day_narrative", ""),
            slots=slots,
            generated_at=data.get("generated_at", ""),
            theme=data.get("theme", ""),
        )

    @staticmethod
    def _invalidate_legacy_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except TypeError:
            if path.exists():
                path.unlink()
