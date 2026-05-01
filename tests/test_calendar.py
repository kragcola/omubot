"""Tests for calendar module — DayContext, holidays, birthdays, special days."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from plugins.schedule.calendar import get_day_context, set_self_name

CST = ZoneInfo("Asia/Shanghai")


def _dt(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=CST)


# ---------------------------------------------------------------------------
# Day type classification
# ---------------------------------------------------------------------------


class TestDayType:
    def test_school_day_monday(self) -> None:
        ctx = get_day_context(_dt("2026-04-27"))  # Monday
        assert ctx.day_type == "school_day"
        assert ctx.is_school_day
        assert not ctx.is_weekend

    def test_school_day_friday(self) -> None:
        ctx = get_day_context(_dt("2026-05-01"))  # Friday, but it's 劳动节
        # May 1 is 劳动节 holiday, so not a school day
        assert ctx.day_type == "holiday"

    def test_weekend_saturday(self) -> None:
        ctx = get_day_context(_dt("2026-05-02"))  # Saturday in 劳动节
        assert ctx.day_type == "holiday"  # holiday takes priority

    def test_weekend_sunday(self) -> None:
        ctx = get_day_context(_dt("2026-04-26"))  # Sunday, no holiday
        assert ctx.day_type == "weekend"
        assert ctx.is_weekend

    def test_holiday_spring_festival(self) -> None:
        ctx = get_day_context(_dt("2026-02-18"))  # 春节
        assert ctx.day_type == "holiday"
        assert ctx.is_holiday
        assert ctx.holiday_name == "春节"

    def test_holiday_national_day(self) -> None:
        ctx = get_day_context(_dt("2026-10-01"))  # 国庆节
        assert ctx.day_type == "holiday"
        assert ctx.holiday_name == "国庆节"

    def test_makeup_day(self) -> None:
        ctx = get_day_context(_dt("2026-02-14"))  # Sat, 春节调休
        assert ctx.day_type == "makeup_day"
        assert ctx.is_makeup_day


# ---------------------------------------------------------------------------
# Holiday coverage — verify all 7 holidays
# ---------------------------------------------------------------------------


class TestHolidayCoverage:
    def test_new_year(self) -> None:
        for d in ["2026-01-01", "2026-01-02", "2026-01-03"]:
            assert get_day_context(_dt(d)).holiday_name == "元旦"

    def test_spring_festival(self) -> None:
        for d in ["2026-02-15", "2026-02-20", "2026-02-23"]:
            assert get_day_context(_dt(d)).holiday_name == "春节"

    def test_qingming(self) -> None:
        for d in ["2026-04-04", "2026-04-05", "2026-04-06"]:
            assert get_day_context(_dt(d)).holiday_name == "清明节"

    def test_labor_day(self) -> None:
        for d in ["2026-05-01", "2026-05-03", "2026-05-05"]:
            assert get_day_context(_dt(d)).holiday_name == "劳动节"

    def test_dragon_boat(self) -> None:
        for d in ["2026-06-19", "2026-06-20", "2026-06-21"]:
            assert get_day_context(_dt(d)).holiday_name == "端午节"

    def test_mid_autumn(self) -> None:
        for d in ["2026-09-25", "2026-09-26", "2026-09-27"]:
            assert get_day_context(_dt(d)).holiday_name == "中秋节"

    def test_national_day(self) -> None:
        for d in ["2026-10-01", "2026-10-04", "2026-10-07"]:
            assert get_day_context(_dt(d)).holiday_name == "国庆节"


# ---------------------------------------------------------------------------
# Makeup days
# ---------------------------------------------------------------------------


class TestMakeupDays:
    def test_all_makeup_days_are_makeup(self) -> None:
        from plugins.schedule.calendar import _MAKEUP_DAYS_2026

        for d in _MAKEUP_DAYS_2026:
            ctx = get_day_context(_dt(d))
            assert ctx.is_makeup_day, f"{d} should be makeup_day, got {ctx.day_type}"

    def test_makeup_days_count(self) -> None:
        from plugins.schedule.calendar import _MAKEUP_DAYS_2026

        assert len(_MAKEUP_DAYS_2026) == 6


# ---------------------------------------------------------------------------
# Special days (non-holiday festivals)
# ---------------------------------------------------------------------------


class TestSpecialDays:
    def test_valentines_day(self) -> None:
        ctx = get_day_context(_dt("2026-02-14"))
        assert ctx.special_day == "情人节"

    def test_qixi(self) -> None:
        ctx = get_day_context(_dt("2026-07-07"))
        assert ctx.special_day == "七夕"

    def test_christmas(self) -> None:
        ctx = get_day_context(_dt("2026-12-25"))
        assert ctx.special_day == "圣诞节"

    def test_halloween(self) -> None:
        ctx = get_day_context(_dt("2026-10-31"))
        assert ctx.special_day == "万圣节"

    def test_normal_day_has_no_special(self) -> None:
        ctx = get_day_context(_dt("2026-04-29"))
        assert ctx.special_day == ""


# ---------------------------------------------------------------------------
# Birthdays
# ---------------------------------------------------------------------------


class TestBirthdays:
    def test_self_birthday(self) -> None:
        set_self_name("凤笑梦")
        ctx = get_day_context(_dt("2026-09-09"))  # 凤笑梦
        assert ctx.has_birthday
        assert ctx.is_self_birthday
        assert len(ctx.birthdays) == 1
        assert ctx.birthdays[0].name_cn == "凤笑梦"
        assert ctx.birthdays[0].is_wxs_member

    def test_tsukasa_birthday(self) -> None:
        ctx = get_day_context(_dt("2026-05-17"))  # 天马司
        assert ctx.has_birthday
        assert not ctx.is_self_birthday
        assert ctx.birthdays[0].name_cn == "天马司"
        assert ctx.birthdays[0].is_wxs_member

    def test_rui_birthday(self) -> None:
        ctx = get_day_context(_dt("2026-06-24"))  # 神代类
        assert ctx.birthdays[0].name_cn == "神代类"
        assert ctx.birthdays[0].is_wxs_member

    def test_nene_birthday(self) -> None:
        ctx = get_day_context(_dt("2026-07-20"))  # 草薙宁宁
        assert ctx.birthdays[0].name_cn == "草薙宁宁"
        assert ctx.birthdays[0].is_wxs_member

    def test_miku_birthday(self) -> None:
        ctx = get_day_context(_dt("2026-08-31"))  # 初音未来
        assert ctx.has_birthday
        assert ctx.birthdays[0].name_cn == "初音未来"
        assert not ctx.birthdays[0].is_wxs_member

    def test_twin_birthday(self) -> None:
        ctx = get_day_context(_dt("2026-12-27"))  # 镜音铃 + 镜音连
        assert len(ctx.birthdays) == 2
        names = {b.name_cn for b in ctx.birthdays}
        assert names == {"镜音铃", "镜音连"}

    def test_no_birthday_on_normal_day(self) -> None:
        ctx = get_day_context(_dt("2026-04-29"))
        assert not ctx.has_birthday
        assert len(ctx.birthdays) == 0

    def test_total_birthday_dates(self) -> None:
        from plugins.schedule.calendar import _BIRTHDAYS_MMDD

        total_characters = sum(len(v) for v in _BIRTHDAYS_MMDD.values())
        assert total_characters == 26, f"expected 26 characters, got {total_characters}"


# ---------------------------------------------------------------------------
# WxS / other birthday helpers
# ---------------------------------------------------------------------------


class TestBirthdayHelpers:
    def test_wxs_birthdays_filter(self) -> None:
        ctx = get_day_context(_dt("2026-12-27"))  # twins, neither WxS
        assert ctx.wxs_birthdays() == []
        assert len(ctx.other_birthdays()) == 2

    def test_wxs_birthdays_returns_wxs_only(self) -> None:
        ctx = get_day_context(_dt("2026-09-09"))  # 凤笑梦 (WxS)
        assert len(ctx.wxs_birthdays()) == 1
        assert len(ctx.other_birthdays()) == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_holiday_overrides_weekend(self) -> None:
        ctx = get_day_context(_dt("2026-02-22"))  # Sunday during 春节
        assert ctx.day_type == "holiday"
        assert not ctx.is_weekend

    def test_makeup_overrides_weekend(self) -> None:
        ctx = get_day_context(_dt("2026-02-14"))  # Saturday makeup for 春节
        assert ctx.day_type == "makeup_day"
        assert not ctx.is_weekend

    def test_weekday_properties(self) -> None:
        ctx = get_day_context(_dt("2026-04-27"))  # Monday, school
        assert ctx.weekday == 0
        assert ctx.date == "2026-04-27"

    def test_date_string_format(self) -> None:
        ctx = get_day_context(_dt("2026-12-25"))
        assert ctx.date == "2026-12-25"
