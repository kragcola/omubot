"""Canonical runtime handle for the active calendar-context provider."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from plugins.calendar_context.service import DayContext


class CalendarContextProvider(Protocol):
    def get_day_context(self, dt: datetime) -> DayContext: ...

    def set_self_names(self, *names: str) -> None: ...


_active_service: CalendarContextProvider | None = None
_self_names: tuple[str, ...] = ()


def publish_calendar_service(service: CalendarContextProvider) -> None:
    """Publish the provider owned by CalendarContextPlugin."""
    global _active_service
    _active_service = service
    if _self_names:
        service.set_self_names(*_self_names)


def unpublish_calendar_service(service: object) -> None:
    """Withdraw only the provider currently owned by the caller."""
    global _active_service
    if _active_service is service:
        _active_service = None


def set_calendar_self_names(*names: str) -> None:
    """Keep compatibility identity state in the canonical calendar domain."""
    global _self_names
    _self_names = tuple(str(name).strip() for name in names if str(name or "").strip())
    if _active_service is not None:
        _active_service.set_self_names(*_self_names)


def get_calendar_self_names() -> tuple[str, ...]:
    if _active_service is not None:
        active_names = getattr(_active_service, "self_names", ())
        names = tuple(str(name) for name in (active_names or ()) if str(name))
        if names:
            return names
    return _self_names


def get_day_context(dt: datetime) -> DayContext:
    """Read the active provider or return an explicit data-free fallback."""
    if _active_service is not None:
        return _active_service.get_day_context(dt)
    return DayContext(
        date=dt.strftime("%Y-%m-%d"),
        weekday=dt.weekday(),
        day_type="school_day" if dt.weekday() < 5 else "weekend",
    )


__all__ = [
    "CalendarContextProvider",
    "get_calendar_self_names",
    "get_day_context",
    "publish_calendar_service",
    "set_calendar_self_names",
    "unpublish_calendar_service",
]
