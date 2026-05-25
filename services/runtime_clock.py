"""Runtime clock helpers shared by mood, debug, and humanization flows."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

CST = ZoneInfo("Asia/Shanghai")
_WEEKDAYS_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def now_cst() -> datetime:
    return datetime.now(CST)


def today_key(now: datetime | None = None) -> str:
    return _as_cst(now).strftime("%Y-%m-%d")


def weekday_cn(value: datetime | int | None = None) -> str:
    weekday = _as_cst(value).weekday() if value is None or isinstance(value, datetime) else int(value)
    return _WEEKDAYS_CN[max(0, min(6, weekday))]


def format_cn_datetime(now: datetime | None = None) -> str:
    current = _as_cst(now)
    return f"{current.strftime('%Y年%m月%d日 %H:%M')} {weekday_cn(current)}"


def is_weekend(now: datetime | None = None) -> bool:
    return _as_cst(now).weekday() >= 5


def is_holiday(day_context: object | None = None) -> bool:
    if day_context is None:
        return False
    return bool(getattr(day_context, "is_holiday", False) or getattr(day_context, "holiday_name", ""))


def slot_features(
    *,
    now: datetime | None = None,
    schedule: object | None = None,
    day_context: object | None = None,
) -> dict[str, Any]:
    current = _as_cst(now)
    slot = None
    if schedule is not None and hasattr(schedule, "current_slot"):
        slot = schedule.current_slot(current)
    return {
        "date": today_key(current),
        "hour": current.hour,
        "minute": current.minute,
        "weekday": current.weekday(),
        "weekday_cn": weekday_cn(current),
        "is_weekend": is_weekend(current),
        "is_holiday": is_holiday(day_context),
        "slot_time": str(getattr(slot, "time", "") or ""),
        "slot_activity": str(getattr(slot, "activity", "") or ""),
        "slot_mood_hint": str(getattr(slot, "mood_hint", "") or ""),
    }


def _as_cst(value: datetime | int | None = None) -> datetime:
    if value is None:
        return now_cst()
    if isinstance(value, datetime):
        return value.astimezone(CST) if value.tzinfo is not None else value.replace(tzinfo=CST)
    raise TypeError("expected datetime or None")
