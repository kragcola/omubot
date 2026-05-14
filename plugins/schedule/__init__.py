"""Schedule system — daily simulated routine + mood engine."""

from __future__ import annotations

from plugins.schedule.generator import ScheduleGenerator
from plugins.schedule.mood import MoodEngine
from plugins.schedule.plugin import ScheduleConfig, SchedulePlugin
from plugins.schedule.store import ScheduleStore
from plugins.schedule.types import MoodProfile, Schedule, TimeSlot

__all__ = [
    "MoodEngine",
    "MoodProfile",
    "Schedule",
    "ScheduleConfig",
    "ScheduleGenerator",
    "SchedulePlugin",
    "ScheduleStore",
    "TimeSlot",
]
