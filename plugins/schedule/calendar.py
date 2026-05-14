"""Compatibility shim for legacy schedule calendar imports.

New code should consume ``ctx.calendar_service`` instead of importing this
module. The default dataset is loaded lazily to avoid import-time side effects.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Set as AbstractSet
from datetime import datetime
from pathlib import Path

from plugins.calendar_context.service import BirthdayEntry, CalendarContextService, DayContext

_legacy_service: CalendarContextService | None = None


def _get_legacy_service() -> CalendarContextService:
    global _legacy_service
    if _legacy_service is None:
        service = CalendarContextService()
        data_root = Path(__file__).resolve().parent.parent / "calendar_context" / "data"
        service.load_dataset(
            birthdays_path=data_root / "birthdays.json",
            special_days_path=data_root / "special_days.json",
            builtin_years_dir=data_root / "years",
        )
        _legacy_service = service
    return _legacy_service


def set_self_name(name: str) -> None:
    """Backward-compatible setter for legacy callers."""
    _get_legacy_service().set_self_names(name)


def get_self_name() -> str | None:
    """Return one configured legacy self name if available."""
    names = sorted(getattr(_get_legacy_service(), "_self_names", set()))
    return names[0] if names else None


def get_day_context(dt: datetime) -> DayContext:
    """Build day context via the shared default dataset."""
    return _get_legacy_service().get_day_context(dt)


def get_makeup_days() -> set[str]:
    """Return all known makeup days from the lazily loaded default dataset."""
    return _get_legacy_service().makeup_days


def get_birthdays_mmdd() -> dict[str, list[BirthdayEntry]]:
    """Return birthday entries keyed by MM-DD from the default dataset."""
    return _get_legacy_service().birthdays_mmdd


class _LazyMakeupDays(AbstractSet[str]):
    def __contains__(self, value: object) -> bool:
        return value in get_makeup_days()

    def __iter__(self) -> Iterator[str]:
        return iter(get_makeup_days())

    def __len__(self) -> int:
        return len(get_makeup_days())

    def copy(self) -> set[str]:
        return set(get_makeup_days())

    def __repr__(self) -> str:
        return repr(get_makeup_days())


class _LazyBirthdaysMmdd(Mapping[str, list[BirthdayEntry]]):
    def __getitem__(self, key: str) -> list[BirthdayEntry]:
        return get_birthdays_mmdd()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(get_birthdays_mmdd())

    def __len__(self) -> int:
        return len(get_birthdays_mmdd())

    def __repr__(self) -> str:
        return repr(get_birthdays_mmdd())


_MAKEUP_DAYS = _LazyMakeupDays()
_BIRTHDAYS_MMDD = _LazyBirthdaysMmdd()
