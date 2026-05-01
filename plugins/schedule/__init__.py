"""Schedule system — daily simulated routine + mood engine + real-date awareness."""

from __future__ import annotations

from plugins.schedule.calendar import BirthdayEntry, DayContext, get_day_context
from plugins.schedule.generator import ScheduleGenerator
from plugins.schedule.mood import MoodEngine
from plugins.schedule.store import ScheduleStore
from plugins.schedule.types import MoodProfile, Schedule, TimeSlot

__all__ = [
    "BirthdayEntry",
    "DayContext",
    "MoodEngine",
    "MoodProfile",
    "Schedule",
    "ScheduleGenerator",
    "ScheduleStore",
    "TimeSlot",
    "get_day_context",
]
