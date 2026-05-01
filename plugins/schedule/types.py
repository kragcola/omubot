"""Schedule system data types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class TimeSlot:
    time: str  # "HH:MM" in Asia/Shanghai
    activity: str
    mood_hint: str
    location: str = ""


@dataclass
class Schedule:
    date: str  # "YYYY-MM-DD"
    day_narrative: str
    slots: list[TimeSlot]

    generated_at: str = ""  # ISO timestamp
    theme: str = ""  # e.g. "排练日", "休息日", "演出日"

    def current_slot(self, now: datetime) -> TimeSlot | None:
        """Find the slot whose time range contains `now`, or the nearest past slot."""
        slot_times: list[tuple[TimeSlot, int]] = []
        for s in self.slots:
            minutes = _time_to_minutes(s.time)
            if minutes is not None:
                slot_times.append((s, minutes))
        if not slot_times:
            return None

        current = now.hour * 60 + now.minute
        slot_times.sort(key=lambda x: x[1])

        # Find the slot whose time range starts at or before now
        best: TimeSlot | None = None
        for _i, (s, start) in enumerate(slot_times):
            if current >= start:
                best = s
        return best


@dataclass
class MoodProfile:
    energy: float  # 0.0 (枯竭) ~ 1.0 (满电)
    valence: float  # -1.0 (负面) ~ 1.0 (正面)
    openness: float  # 0.0 (封闭) ~ 1.0 (开放话多)
    tension: float  # 0.0 (放松) ~ 1.0 (紧张/烦躁)

    label: str = ""  # e.g. "疲惫", "兴奋", "放松"
    anomaly_reason: str = ""  # explanation if mood is anomalous

    def clamp(self) -> MoodProfile:
        """Ensure all values stay in valid ranges."""
        self.energy = max(0.0, min(1.0, self.energy))
        self.valence = max(-1.0, min(1.0, self.valence))
        self.openness = max(0.0, min(1.0, self.openness))
        self.tension = max(0.0, min(1.0, self.tension))
        return self


def _time_to_minutes(t: str) -> int | None:
    """Convert 'HH:MM' to total minutes. Returns None on parse failure."""
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, TypeError):
        return None
