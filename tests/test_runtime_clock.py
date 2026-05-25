from __future__ import annotations

from datetime import UTC, datetime

from plugins.schedule.calendar import DayContext
from plugins.schedule.types import Schedule, TimeSlot
from services.runtime_clock import (
    CST,
    format_cn_datetime,
    is_holiday,
    is_weekend,
    now_cst,
    slot_features,
    today_key,
    weekday_cn,
)


def test_now_cst_uses_shanghai_timezone() -> None:
    current = now_cst()

    assert current.tzinfo is CST
    assert current.utcoffset().total_seconds() == 8 * 3600


def test_today_key_converts_aware_datetime_to_cst() -> None:
    utc_evening = datetime(2026, 5, 24, 18, 30, tzinfo=UTC)

    assert today_key(utc_evening) == "2026-05-25"


def test_format_cn_datetime_includes_chinese_weekday() -> None:
    value = datetime(2026, 5, 25, 2, 30, tzinfo=CST)

    assert format_cn_datetime(value) == "2026年05月25日 02:30 周一"
    assert weekday_cn(value) == "周一"


def test_is_weekend_distinguishes_weekend_and_workday() -> None:
    sunday = datetime(2026, 5, 24, 12, 0, tzinfo=CST)
    monday = datetime(2026, 5, 25, 12, 0, tzinfo=CST)

    assert is_weekend(sunday) is True
    assert is_weekend(monday) is False


def test_is_holiday_accepts_calendar_stub() -> None:
    holiday = DayContext(date="2026-10-01", weekday=3, day_type="holiday", holiday_name="国庆节")
    normal = DayContext(date="2026-05-25", weekday=0, day_type="school_day")

    assert is_holiday(holiday) is True
    assert is_holiday(normal) is False


def test_slot_features_include_current_schedule_slot() -> None:
    now = datetime(2026, 5, 25, 12, 30, tzinfo=CST)
    schedule = Schedule(
        date="2026-05-25",
        day_narrative="test day",
        theme="测试日",
        slots=[
            TimeSlot(time="08:00", activity="起床", mood_hint="困倦"),
            TimeSlot(time="12:00", activity="午饭", mood_hint="放松"),
        ],
    )
    day_context = DayContext(date="2026-05-25", weekday=0, day_type="school_day")

    features = slot_features(now=now, schedule=schedule, day_context=day_context)

    assert features["date"] == today_key(now)
    assert features["weekday_cn"] == "周一"
    assert features["slot_time"] == "12:00"
    assert features["slot_activity"] == "午饭"
    assert features["slot_mood_hint"] == "放松"
    assert features["is_holiday"] is False
