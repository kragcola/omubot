"""Deprecated facade for the canonical calendar-context runtime provider."""

from __future__ import annotations

from datetime import datetime

from plugins.calendar_context.runtime import (
    get_calendar_self_names,
    set_calendar_self_names,
)
from plugins.calendar_context.runtime import (
    get_day_context as _get_day_context,
)
from plugins.calendar_context.service import BirthdayEntry, DayContext


def set_self_name(name: str) -> None:
    """Forward legacy identity updates to the canonical calendar domain."""
    set_calendar_self_names(name)


def get_self_name() -> str | None:
    """Return the first canonical identity name for legacy callers."""
    names = get_calendar_self_names()
    return names[0] if names else None


def get_day_context(dt: datetime) -> DayContext:
    """Delegate to the provider published by CalendarContextPlugin."""
    return _get_day_context(dt)


__all__ = [
    "BirthdayEntry",
    "DayContext",
    "get_day_context",
    "get_self_name",
    "set_self_name",
]
