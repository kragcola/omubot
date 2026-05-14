"""Tests for calendar context service — holidays, birthdays, special days."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from bs4.exceptions import FeatureNotFound

from plugins.calendar_context.service import CalendarContextService, YearDataset

CST = ZoneInfo("Asia/Shanghai")
DATA_ROOT = Path("plugins/calendar_context/data")


def _dt(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=CST)


def _service(
    *,
    cache_dir: Path | None = None,
    write_back_plugin_data: bool = False,
    official_source_enabled: bool = False,
    fallback_local_holiday_lib: bool = False,
    auto_fetch_missing_year: bool = False,
    extra_year_paths: list[Path] | None = None,
    extra_birthdays_paths: list[Path] | None = None,
) -> CalendarContextService:
    service = CalendarContextService(
        cache_dir=cache_dir,
        plugin_data_dir=DATA_ROOT,
        write_back_plugin_data=write_back_plugin_data,
        official_source_enabled=official_source_enabled,
        fallback_local_holiday_lib=fallback_local_holiday_lib,
        auto_fetch_missing_year=auto_fetch_missing_year,
    )
    service.load_dataset(
        birthdays_path=DATA_ROOT / "birthdays.json",
        special_days_path=DATA_ROOT / "special_days.json",
        builtin_years_dir=DATA_ROOT / "years",
        extra_year_paths=extra_year_paths or [],
        extra_birthdays_paths=extra_birthdays_paths or [],
    )
    return service


class TestDayType:
    def test_school_day_monday(self) -> None:
        ctx = _service().get_day_context(_dt("2026-04-27"))
        assert ctx.day_type == "school_day"
        assert ctx.is_school_day
        assert not ctx.is_weekend

    def test_holiday_priority(self) -> None:
        ctx = _service().get_day_context(_dt("2026-05-01"))
        assert ctx.day_type == "holiday"
        assert ctx.holiday_name == "劳动节"

    def test_makeup_day(self) -> None:
        ctx = _service().get_day_context(_dt("2026-02-14"))
        assert ctx.day_type == "makeup_day"
        assert ctx.is_makeup_day


class TestHolidayCoverage:
    def test_default_holiday_hits(self) -> None:
        service = _service()
        assert service.get_day_context(_dt("2026-02-18")).holiday_name == "春节"
        assert service.get_day_context(_dt("2026-10-01")).holiday_name == "国庆节"

    def test_corrected_2025_weekend_is_not_holiday(self) -> None:
        service = _service()
        ctx = service.get_day_context(_dt("2025-01-04"))
        assert ctx.day_type == "weekend"
        assert not ctx.is_holiday
        assert ctx.holiday_name == ""


class TestConfigContract:
    def test_schema_matches_default_config_fields(self) -> None:
        defaults = json.loads((DATA_ROOT.parent / "config.default.json").read_text(encoding="utf-8"))["values"]
        schema = json.loads((DATA_ROOT.parent / "config.schema.json").read_text(encoding="utf-8"))["properties"]

        assert "builtin_dataset" not in schema
        assert "extra_data_files" not in schema
        assert set(defaults).issubset(set(schema))
        assert "builtin_birthdays_file" in schema
        assert "extra_year_files" in schema
        assert defaults["write_back_plugin_data"] is False

    def test_missing_year_degrades_to_weekday_and_birthdays(self) -> None:
        service = _service()
        ctx = service.get_day_context(datetime(2027, 9, 9, 12, 0, tzinfo=CST))
        assert ctx.has_birthday
        assert ctx.birthdays[0].name_cn == "凤笑梦"
        assert ctx.holiday_name == ""
        assert ctx.special_day == ""


class TestSpecialDays:
    def test_default_special_day_hits(self) -> None:
        service = _service()
        assert service.get_day_context(_dt("2026-08-19")).special_day == "七夕"
        assert service.get_day_context(_dt("2026-12-25")).special_day == "圣诞节"

    def test_large_fixed_special_days_hit(self) -> None:
        service = _service()
        assert service.get_day_context(_dt("2026-09-10")).special_days == ["教师节"]
        assert service.get_day_context(_dt("2026-11-11")).special_days == ["双十一", "光棍节"]

    def test_special_days_can_stack_on_same_date(self) -> None:
        service = _service()
        ctx = service.get_day_context(_dt("2026-06-01"))
        assert ctx.special_days == ["儿童节", "国际牛奶日"]
        assert ctx.special_day == "儿童节 / 国际牛奶日"

    def test_recurring_special_days_work_across_years(self) -> None:
        service = _service()
        ctx = service.get_day_context(_dt("2025-06-01"))
        assert ctx.special_days == ["儿童节", "国际牛奶日"]

    def test_lunar_special_days_work_across_years(self) -> None:
        service = _service()
        assert service.get_day_context(_dt("2025-02-12")).special_days == ["元宵节"]
        assert service.get_day_context(_dt("2025-08-29")).special_days == ["七夕", "初音未来周年纪念日"]
        assert service.get_day_context(_dt("2026-03-03")).special_days == ["女儿节", "元宵节"]
        assert service.get_day_context(_dt("2026-10-18")).special_days == ["重阳节"]
        assert service.get_day_context(_dt("2027-02-05")).special_days == ["除夕"]

    def test_mothers_day_uses_second_sunday_rule(self) -> None:
        service = _service()
        assert "母亲节" not in service.get_day_context(_dt("2025-05-10")).special_days
        assert service.get_day_context(_dt("2025-05-11")).special_days == ["母亲节"]

    def test_fathers_day_uses_third_sunday_rule(self) -> None:
        service = _service()
        assert "父亲节" not in service.get_day_context(_dt("2025-06-21")).special_days
        assert service.get_day_context(_dt("2025-06-15")).special_days == ["父亲节"]

    def test_thanksgiving_uses_fourth_thursday_rule(self) -> None:
        service = _service()
        assert service.get_day_context(_dt("2025-11-27")).special_days == ["感恩节"]

    def test_year_specific_large_special_days_hit(self) -> None:
        service = _service()
        assert service.get_day_context(_dt("2025-02-12")).special_days == ["元宵节"]
        assert service.get_day_context(_dt("2025-08-29")).special_days == ["七夕", "初音未来周年纪念日"]
        assert service.get_day_context(_dt("2026-02-02")).special_days == ["春季节分"]


class TestBirthdays:
    def test_default_birthday_hits(self) -> None:
        ctx = _service().get_day_context(_dt("2026-09-09"))
        assert ctx.has_birthday
        assert len(ctx.birthdays) == 1
        assert ctx.birthdays[0].name_cn == "凤笑梦"
        assert ctx.birthdays[0].is_wxs_member

    def test_total_birthdays_kept(self) -> None:
        total = sum(len(v) for v in _service().birthdays_mmdd.values())
        assert total == 26

    def test_self_birthday_matches_plain_name(self) -> None:
        service = _service()
        service.set_self_names("凤笑梦")
        ctx = service.get_day_context(_dt("2026-09-09"))
        assert ctx.is_self_birthday

    def test_self_birthday_matches_identity_alias(self) -> None:
        service = _service()
        service.set_self_names("凤笑梦 (Emu Otori)")
        ctx = service.get_day_context(_dt("2026-09-09"))
        assert ctx.is_self_birthday

    def test_alias_match_works(self) -> None:
        service = _service()
        service.set_self_names("Emu Otori")
        ctx = service.get_day_context(_dt("2026-09-09"))
        assert ctx.is_self_birthday

    def test_birthdays_work_across_years(self) -> None:
        ctx = _service().get_day_context(datetime(2027, 9, 9, 12, 0, tzinfo=CST))
        assert ctx.has_birthday
        assert ctx.birthdays[0].name_cn == "凤笑梦"


class TestBirthdayHelpers:
    def test_wxs_birthdays_filter(self) -> None:
        ctx = _service().get_day_context(_dt("2026-12-27"))
        assert ctx.wxs_birthdays() == []
        assert len(ctx.other_birthdays()) == 2

    def test_wxs_birthdays_returns_wxs_only(self) -> None:
        ctx = _service().get_day_context(_dt("2026-09-09"))
        assert len(ctx.wxs_birthdays()) == 1
        assert len(ctx.other_birthdays()) == 0


class TestExtraDatasetMerge:
    def test_extra_dataset_can_override_and_append(self, tmp_path: Path) -> None:
        extra_year = tmp_path / "2026.extra.json"
        extra_year.write_text(
            json.dumps(
                {
                    "holidays": {"2026-04-27": "测试假日"},
                    "special_days": {"2026-04-29": ["测试纪念日"]},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        extra_birthdays = tmp_path / "birthdays.extra.json"
        extra_birthdays.write_text(
            json.dumps(
                {
                    "birthdays": [
                        {
                            "date_mmdd": "09-09",
                            "name_cn": "测试角色",
                            "name_jp": "テスト",
                            "group": "Test",
                            "is_wxs_member": False,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        service = _service(extra_year_paths=[extra_year], extra_birthdays_paths=[extra_birthdays])
        assert service.get_day_context(_dt("2026-04-27")).holiday_name == "测试假日"
        assert service.get_day_context(_dt("2026-04-29")).special_day == "测试纪念日"
        names = {b.name_cn for b in service.get_day_context(_dt("2026-09-09")).birthdays}
        assert "凤笑梦" in names
        assert "测试角色" in names


class TestAutoFetch:
    async def test_missing_year_can_be_fetched_and_cached(self, tmp_path: Path) -> None:
        service = _service(
            cache_dir=tmp_path,
            auto_fetch_missing_year=True,
            official_source_enabled=False,
            fallback_local_holiday_lib=False,
        )

        async def fake_fetch(year: int) -> YearDataset | None:
            return YearDataset(
                year=year,
                holidays={f"{year}-01-01": "元旦"},
                makeup_days={f"{year}-01-04"},
                special_days={},
                source="test_fetch",
            )

        service._fetch_year_dataset = fake_fetch  # type: ignore[method-assign]
        ok = await service.ensure_year_loaded(2027)
        assert ok
        assert service.get_day_context(datetime(2027, 1, 1, 12, 0, tzinfo=CST)).holiday_name == "元旦"
        assert (tmp_path / "years" / "2027.json").is_file()
        payload = json.loads((tmp_path / "years" / "2027.json").read_text(encoding="utf-8"))
        assert payload["special_days"]["2027-02-20"] == ["元宵节"]
        assert payload["special_days"]["2027-05-09"] == ["母亲节"]

    async def test_official_fetch_success_writes_plugin_data(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "plugin_data"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        service = CalendarContextService(
            cache_dir=tmp_path / "cache",
            plugin_data_dir=plugin_dir,
            write_back_plugin_data=True,
            auto_fetch_missing_year=True,
        )
        service.load_dataset(
            birthdays_path=DATA_ROOT / "birthdays.json",
            special_days_path=DATA_ROOT / "special_days.json",
            builtin_years_dir=DATA_ROOT / "years",
        )

        async def fake_fetch(year: int) -> YearDataset | None:
            return YearDataset(
                year=year,
                holidays={f"{year}-05-01": "劳动节"},
                makeup_days=set(),
                special_days={},
                source="official",
            )

        service._fetch_year_dataset = fake_fetch  # type: ignore[method-assign]
        ok = await service.ensure_year_loaded(2028)
        assert ok
        assert (tmp_path / "cache" / "years" / "2028.json").is_file()
        assert (plugin_dir / "years" / "2028.json").is_file()

    async def test_default_write_back_plugin_data_false_only_writes_cache(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "plugin_data"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        service = CalendarContextService(
            cache_dir=tmp_path / "cache",
            plugin_data_dir=plugin_dir,
            auto_fetch_missing_year=True,
        )
        service.load_dataset(
            birthdays_path=DATA_ROOT / "birthdays.json",
            special_days_path=DATA_ROOT / "special_days.json",
            builtin_years_dir=DATA_ROOT / "years",
        )

        async def fake_fetch(year: int) -> YearDataset | None:
            return YearDataset(year=year, holidays={f"{year}-01-01": "元旦"}, source="test_fetch")

        service._fetch_year_dataset = fake_fetch  # type: ignore[method-assign]
        ok = await service.ensure_year_loaded(2029)
        assert ok
        assert (tmp_path / "cache" / "years" / "2029.json").is_file()
        assert not (plugin_dir / "years" / "2029.json").exists()

    async def test_concurrent_missing_year_fetches_once(self, tmp_path: Path) -> None:
        service = _service(
            cache_dir=tmp_path,
            auto_fetch_missing_year=True,
            official_source_enabled=False,
            fallback_local_holiday_lib=False,
        )
        calls = 0

        async def fake_fetch(year: int) -> YearDataset | None:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return YearDataset(year=year, holidays={f"{year}-01-01": "元旦"}, source="test_fetch")

        service._fetch_year_dataset = fake_fetch  # type: ignore[method-assign]
        results = await asyncio.gather(service.ensure_year_loaded(2030), service.ensure_year_loaded(2030))

        assert results == [True, True]
        assert calls == 1

    async def test_fetch_failure_keeps_birthdays(self, tmp_path: Path) -> None:
        service = _service(
            cache_dir=tmp_path,
            auto_fetch_missing_year=True,
            official_source_enabled=False,
            fallback_local_holiday_lib=False,
        )

        async def fake_fetch(year: int) -> YearDataset | None:
            return None

        service._fetch_year_dataset = fake_fetch  # type: ignore[method-assign]
        ok = await service.ensure_year_loaded(2027)
        assert not ok
        ctx = service.get_day_context(datetime(2027, 9, 9, 12, 0, tzinfo=CST))
        assert ctx.has_birthday
        assert ctx.holiday_name == ""

    async def test_fallback_local_library_used_when_official_fails(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        service = _service(
            cache_dir=tmp_path,
            auto_fetch_missing_year=True,
            official_source_enabled=True,
            fallback_local_holiday_lib=True,
        )

        async def fake_official(_year: int) -> YearDataset | None:
            raise RuntimeError("official failed")

        monkeypatch.setattr(service, "_fetch_from_official", fake_official)
        monkeypatch.setattr(
            service,
            "_build_from_local_lib",
            lambda year: YearDataset(
                year=year,
                holidays={f"{year}-10-01": "国庆节"},
                makeup_days={f"{year}-10-10"},
                special_days={},
                source="fallback_local",
            ),
        )
        ok = await service.ensure_year_loaded(2027)
        assert ok
        assert service.runtime_meta["data_source"] == "fallback_local"

    async def test_fallback_local_excludes_plain_weekends_and_keeps_makeup_days(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _service(
            auto_fetch_missing_year=False,
            official_source_enabled=False,
            fallback_local_holiday_lib=True,
        )

        class _FakeHolidayLib:
            @staticmethod
            def get_holiday_detail(date_value):
                mapping = {
                    "2025-01-01": (True, "New Year's Day"),
                    "2025-01-04": (True, None),
                    "2025-01-05": (True, None),
                    "2025-02-01": (True, "Spring Festival"),
                    "2025-04-04": (True, "Tomb-sweeping Day"),
                    "2025-05-01": (True, "Labour Day"),
                    "2025-05-31": (True, "Dragon Boat Festival"),
                    "2025-10-01": (True, "National Day"),
                    "2025-10-06": (True, "Mid-autumn Festival"),
                }
                return mapping.get(date_value.isoformat(), (False, None))

            @staticmethod
            def is_workday(date_value):
                return date_value.isoformat() in {"2025-04-27", "2025-10-11"}

        monkeypatch.setattr("plugins.calendar_context.service.chinese_holiday_lib", _FakeHolidayLib)
        dataset = service._build_from_local_lib(2025)

        assert dataset is not None
        assert dataset.source == "fallback_local"
        assert dataset.holidays["2025-01-01"] == "元旦"
        assert dataset.holidays["2025-02-01"] == "春节"
        assert dataset.holidays["2025-04-04"] == "清明节"
        assert dataset.holidays["2025-05-01"] == "劳动节"
        assert dataset.holidays["2025-05-31"] == "端午节"
        assert dataset.holidays["2025-10-01"] == "国庆节"
        assert dataset.holidays["2025-10-06"] == "中秋节"
        assert "2025-01-04" not in dataset.holidays
        assert "2025-01-05" not in dataset.holidays
        assert "节假日" not in set(dataset.holidays.values())
        assert dataset.makeup_days == {"2025-04-27", "2025-10-11"}

    def test_official_html_parser_extracts_spans_and_makeup_days(self) -> None:
        service = _service()
        html = """
        <html><body>
        <p>国务院办公厅关于2027年部分节假日安排的通知</p>
        <p>一、元旦：1月1日（周五）至1月3日（周日）放假调休，共3天。</p>
        <p>二、春节：2月9日（周二）至2月15日（周一）放假调休，共7天。2月6日（周六）、2月20日（周六）上班。</p>
        <p>三、国庆节：10月1日（周五）至10月7日（周四）放假调休，共7天。9月26日（周日）、10月9日（周六）上班。</p>
        </body></html>
        """
        dataset = service._parse_official_html(2027, html)
        assert dataset is not None
        assert dataset.holidays["2027-01-01"] == "元旦"
        assert dataset.holidays["2027-01-03"] == "元旦"
        assert dataset.holidays["2027-02-09"] == "春节"
        assert dataset.holidays["2027-02-15"] == "春节"
        assert dataset.holidays["2027-10-07"] == "国庆节"
        assert dataset.makeup_days == {"2027-02-06", "2027-02-20", "2027-09-26", "2027-10-09"}

    def test_official_html_parser_falls_back_to_stdlib_parser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plugins.calendar_context import service as service_module

        original_beautiful_soup = service_module.BeautifulSoup

        def fake_beautiful_soup(html: str, parser: str):
            if parser == "lxml":
                raise FeatureNotFound("lxml unavailable")
            return original_beautiful_soup(html, parser)

        monkeypatch.setattr(service_module, "BeautifulSoup", fake_beautiful_soup)
        service = _service()
        html = """
        <html><body>
        <p>国务院办公厅关于2027年部分节假日安排的通知</p>
        <p>一、元旦：1月1日（周五）至1月3日（周日）放假调休，共3天。</p>
        </body></html>
        """

        dataset = service._parse_official_html(2027, html)

        assert dataset is not None
        assert dataset.holidays["2027-01-01"] == "元旦"
        assert dataset.holidays["2027-01-03"] == "元旦"


class TestLegacyCalendarShim:
    def test_import_does_not_load_dataset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import plugins.calendar_context.service as service_module

        calls = []

        def fake_load_dataset(self, **kwargs):  # noqa: ANN001
            calls.append(kwargs)

        monkeypatch.setattr(service_module.CalendarContextService, "load_dataset", fake_load_dataset)
        sys.modules.pop("plugins.schedule.calendar", None)

        importlib.import_module("plugins.schedule.calendar")

        assert calls == []

    def test_legacy_functions_still_work(self) -> None:
        import plugins.schedule.calendar as legacy_calendar

        legacy_calendar = importlib.reload(legacy_calendar)
        legacy_calendar.set_self_name("凤笑梦")

        assert legacy_calendar.get_self_name() == "凤笑梦"
        ctx = legacy_calendar.get_day_context(_dt("2026-09-09"))
        assert ctx.has_birthday
        assert ctx.is_self_birthday
        assert "2026-02-14" in legacy_calendar._MAKEUP_DAYS
        assert "09-09" in legacy_calendar._BIRTHDAYS_MMDD
