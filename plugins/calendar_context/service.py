"""Calendar context service: holidays, special days, birthdays, self matching."""

from __future__ import annotations

import asyncio
import calendar
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup
from bs4.exceptions import FeatureNotFound
from loguru import logger

try:
    import chinese_calendar as chinese_holiday_lib
except Exception:  # pragma: no cover - optional runtime fallback
    chinese_holiday_lib = None

try:
    from lunardate import LunarDate
except Exception:  # pragma: no cover - optional runtime fallback
    LunarDate = None

_L = logger.bind(channel="calendar_context")

_FALLBACK_HOLIDAY_LABELS = {
    "New Year's Day": "元旦",
    "Spring Festival": "春节",
    "Tomb-sweeping Day": "清明节",
    "Labour Day": "劳动节",
    "Dragon Boat Festival": "端午节",
    "Mid-autumn Festival": "中秋节",
    "National Day": "国庆节",
}

_OFFICIAL_HOLIDAY_NAMES = ("元旦", "春节", "清明节", "劳动节", "端午节", "中秋节", "国庆节")
_OFFICIAL_SECTION_PATTERN = re.compile(
    rf"(?P<num>[一二三四五六七八九十]+)、(?P<name>{'|'.join(_OFFICIAL_HOLIDAY_NAMES)})：(?P<body>.*?)(?=(?:[一二三四五六七八九十]+)、(?:{'|'.join(_OFFICIAL_HOLIDAY_NAMES)})：|国务院办公厅|$)",
    re.S,
)
_OFFICIAL_DATE_PATTERN = re.compile(
    r"(?P<start_month>\d{1,2})月(?P<start_day>\d{1,2})日(?:（[^）]*）)?(?:至(?:(?P<end_month>\d{1,2})月)?(?P<end_day>\d{1,2})日(?:（[^）]*）)?)?"
)


@dataclass
class BirthdayEntry:
    """A birthday entry with optional aliases and tags."""

    name_cn: str
    name_jp: str
    group: str
    is_wxs_member: bool = False
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class YearDataset:
    """A single year's holiday/makeup/special-day data."""

    year: int
    holidays: dict[str, str] = field(default_factory=dict)
    makeup_days: set[str] = field(default_factory=set)
    special_days: dict[str, list[str]] = field(default_factory=dict)
    source: str = "builtin"


@dataclass
class CalendarRuntimeMeta:
    """Runtime diagnostics for Admin/plugin inspection."""

    loaded_years: list[int] = field(default_factory=list)
    current_year: int = 0
    current_year_available: bool = False
    data_source: str = ""
    last_fetch_at: float = 0.0
    last_fetch_status: str = "idle"
    last_fetch_error: str = ""
    storage_year_path: str = ""
    plugin_year_path: str = ""


@dataclass
class SpecialDayRule:
    """A recurring special day defined by nth weekday in month."""

    month: int
    weekday: int
    nth: int
    names: list[str] = field(default_factory=list)


@dataclass
class LunarSpecialDayRule:
    """A recurring special day defined by chinese lunar calendar."""

    names: list[str] = field(default_factory=list)
    month: int | None = None
    day: int | None = None
    kind: str = "fixed"
    leap_month: bool = False


@dataclass
class DayContext:
    """Everything special about a given date."""

    date: str
    weekday: int
    day_type: str
    holiday_name: str = ""
    birthdays: list[BirthdayEntry] = field(default_factory=list)
    special_day: str = ""
    special_day_names: list[str] = field(default_factory=list)
    self_names: set[str] = field(default_factory=set)

    @property
    def is_school_day(self) -> bool:
        return self.day_type == "school_day"

    @property
    def is_weekend(self) -> bool:
        return self.day_type == "weekend"

    @property
    def is_holiday(self) -> bool:
        return self.day_type == "holiday"

    @property
    def is_makeup_day(self) -> bool:
        return self.day_type == "makeup_day"

    @property
    def has_birthday(self) -> bool:
        return len(self.birthdays) > 0

    @property
    def holiday_names(self) -> list[str]:
        return [self.holiday_name] if self.holiday_name else []

    @property
    def special_days(self) -> list[str]:
        if self.special_day_names:
            return list(self.special_day_names)
        if not self.special_day:
            return []
        return [item.strip() for item in self.special_day.split(" / ") if item.strip()]

    @property
    def is_self_birthday(self) -> bool:
        if not self.self_names:
            return False
        self_tokens = {normalize_identity_name(name) for name in self.self_names if normalize_identity_name(name)}
        for birthday in self.birthdays:
            candidates = {normalize_identity_name(birthday.name_cn), normalize_identity_name(birthday.name_jp)}
            candidates.update(normalize_identity_name(alias) for alias in birthday.aliases)
            candidates = {candidate for candidate in candidates if candidate}
            if self_tokens & candidates:
                return True
        return False

    def wxs_birthdays(self) -> list[BirthdayEntry]:
        return [b for b in self.birthdays if b.is_wxs_member]

    def other_birthdays(self) -> list[BirthdayEntry]:
        return [b for b in self.birthdays if not b.is_wxs_member]


def normalize_identity_name(name: str) -> str:
    """Normalize a display name for birthday matching."""
    value = re.sub(r"\s+", " ", str(name or "").strip())
    if not value:
        return ""
    value = re.sub(r"\s*[\(\（][^\)\）]*[\)\）]\s*$", "", value).strip()
    return value.casefold()


class CalendarContextService:
    """Runtime date context provider backed by local JSON plus optional fetch."""

    def __init__(
        self,
        *,
        timezone: str = "Asia/Shanghai",
        match_identity_aliases: bool = True,
        cache_dir: Path | None = None,
        plugin_data_dir: Path | None = None,
        auto_fetch_missing_year: bool = True,
        auto_refresh_future_years: bool = False,
        official_source_enabled: bool = True,
        fallback_local_holiday_lib: bool = True,
        write_back_plugin_data: bool = False,
        official_fetch_timeout_s: float = 10.0,
        official_url_template: str = "https://www.gov.cn/zhengce/zhengceku/{year}-11/12/content_holidays.htm",
    ) -> None:
        self.timezone = timezone
        self.match_identity_aliases = match_identity_aliases
        self.cache_dir = cache_dir
        self.plugin_data_dir = plugin_data_dir
        self.auto_fetch_missing_year = auto_fetch_missing_year
        self.auto_refresh_future_years = auto_refresh_future_years
        self.official_source_enabled = official_source_enabled
        self.fallback_local_holiday_lib = fallback_local_holiday_lib
        self.write_back_plugin_data = write_back_plugin_data
        self.official_fetch_timeout_s = official_fetch_timeout_s
        self.official_url_template = official_url_template
        self._year_datasets: dict[int, YearDataset] = {}
        self._birthdays_mmdd: dict[str, list[BirthdayEntry]] = {}
        self._special_days_mmdd: dict[str, list[str]] = {}
        self._special_day_rules: list[SpecialDayRule] = []
        self._lunar_special_day_rules: list[LunarSpecialDayRule] = []
        self._resolved_special_day_rules: dict[int, dict[str, list[str]]] = {}
        self._resolved_lunar_special_day_rules: dict[int, dict[str, list[str]]] = {}
        self._self_names: set[str] = set()
        self._runtime = CalendarRuntimeMeta()
        self._year_locks: dict[int, asyncio.Lock] = {}

    def set_self_names(self, *names: str) -> None:
        tokens = {name.strip() for name in names if str(name or "").strip()}
        expanded: set[str] = set(tokens)
        if self.match_identity_aliases:
            for name in list(tokens):
                normalized = normalize_identity_name(name)
                if normalized and normalized != name.casefold():
                    expanded.add(re.sub(r"\s*[\(\（][^\)\）]*[\)\）]\s*$", "", name).strip())
                m = re.search(r"[\(\（]([^\)\）]+)[\)\）]\s*$", name)
                if m:
                    inner = m.group(1).strip()
                    if inner:
                        expanded.add(inner)
        self._self_names = {name for name in expanded if name}

    def load_dataset(
        self,
        *,
        birthdays_path: Path | None = None,
        builtin_years_dir: Path | None = None,
        extra_birthdays_paths: list[Path] | None = None,
        extra_year_paths: list[Path] | None = None,
        special_days_path: Path | None = None,
        extra_special_days_paths: list[Path] | None = None,
        builtin_path: Path | None = None,
        extra_paths: list[Path] | None = None,
    ) -> None:
        if builtin_path is not None and (birthdays_path is None or builtin_years_dir is None):
            root = builtin_path.parent
            birthdays_path = root / "birthdays.json"
            builtin_years_dir = root / "years"
            special_days_path = root / "special_days.json"
            extra_year_paths = list(extra_year_paths or []) + list(extra_paths or [])
        if birthdays_path is None or builtin_years_dir is None:
            raise ValueError("birthdays_path and builtin_years_dir are required")
        self._birthdays_mmdd = self._load_birthdays([birthdays_path, *(extra_birthdays_paths or [])])
        self._special_days_mmdd, self._special_day_rules, self._lunar_special_day_rules = self._load_special_days(
            [path for path in [special_days_path, *(extra_special_days_paths or [])] if path is not None]
        )
        self._resolved_special_day_rules = {}
        self._resolved_lunar_special_day_rules = {}
        self._year_datasets = {}

        year_sources = [
            *self._collect_year_files(self.cache_dir / "years" if self.cache_dir else None, source="storage"),
            *self._collect_year_files(builtin_years_dir, source="builtin"),
        ]
        for extra_path in extra_year_paths or []:
            year = self._extract_year_from_path(extra_path)
            if year is None:
                continue
            year_sources.append((year, extra_path, "extra"))

        year_buckets: dict[int, list[tuple[Path, str]]] = {}
        for year, path, source in year_sources:
            year_buckets.setdefault(year, []).append((path, source))

        for year, entries in sorted(year_buckets.items()):
            merged = YearDataset(year=year)
            for path, source in entries:
                payload = self._read_json(path)
                merged.holidays.update(self._parse_string_map(payload.get("holidays")))
                merged.makeup_days.update(self._parse_makeup_days(payload.get("makeup_days")))
                for date_str, names in self._parse_string_list_map(payload.get("special_days")).items():
                    bucket = merged.special_days.setdefault(date_str, [])
                    for name in names:
                        if name not in bucket:
                            bucket.append(name)
                merged.source = source
            self._year_datasets[year] = merged

        self._runtime.loaded_years = sorted(self._year_datasets.keys())

    async def ensure_year_loaded(self, year: int) -> bool:
        self._runtime.current_year = year
        if year in self._year_datasets:
            self._runtime.current_year_available = True
            self._runtime.data_source = self._year_datasets[year].source
            self._runtime.plugin_year_path = str(self._plugin_year_path(year)) if self.plugin_data_dir else ""
            self._runtime.storage_year_path = str(self._storage_year_path(year)) if self.cache_dir else ""
            return True

        lock = self._year_locks.setdefault(year, asyncio.Lock())
        async with lock:
            return await self._ensure_year_loaded_locked(year)

    async def _ensure_year_loaded_locked(self, year: int) -> bool:
        self._runtime.current_year = year
        if year in self._year_datasets:
            self._runtime.current_year_available = True
            self._runtime.data_source = self._year_datasets[year].source
            self._runtime.plugin_year_path = str(self._plugin_year_path(year)) if self.plugin_data_dir else ""
            self._runtime.storage_year_path = str(self._storage_year_path(year)) if self.cache_dir else ""
            return True

        self._runtime.current_year_available = False
        self._runtime.data_source = ""
        self._runtime.plugin_year_path = str(self._plugin_year_path(year)) if self.plugin_data_dir else ""
        self._runtime.storage_year_path = str(self._storage_year_path(year)) if self.cache_dir else ""

        if not self.auto_fetch_missing_year:
            _L.warning("calendar_context missing year dataset | year={}", year)
            return False

        _L.info("calendar_context fetch started | year={}", year)
        self._runtime.last_fetch_at = time.time()
        self._runtime.last_fetch_status = "fetching"
        self._runtime.last_fetch_error = ""

        dataset = await self._fetch_year_dataset(year)
        if dataset is None:
            self._runtime.last_fetch_status = "failed"
            self._runtime.current_year_available = False
            _L.warning(
                "calendar_context fetch failed | year={} error={}",
                year,
                self._runtime.last_fetch_error or "unknown",
            )
            return False

        dataset.special_days = self._build_materialized_special_days(year, dataset.special_days)
        self._year_datasets[year] = dataset
        self._runtime.loaded_years = sorted(self._year_datasets.keys())
        self._runtime.current_year_available = True
        self._runtime.data_source = dataset.source
        self._runtime.last_fetch_status = "success"

        self._write_year_dataset(dataset)
        return True

    def get_day_context(self, dt: datetime) -> DayContext:
        date_str = dt.strftime("%Y-%m-%d")
        mmdd = dt.strftime("%m-%d")
        wd = dt.weekday()
        year = dt.year

        year_data = self._year_datasets.get(year)
        holiday_name = year_data.holidays.get(date_str, "") if year_data else ""
        is_makeup = date_str in year_data.makeup_days if year_data else False
        special_names = list(year_data.special_days.get(date_str, [])) if year_data else []
        special_names.extend(name for name in self._special_days_mmdd.get(mmdd, []) if name not in special_names)
        for name in self._resolved_special_days_for_year(year).get(date_str, []):
            if name not in special_names:
                special_names.append(name)
        for name in self._resolved_lunar_special_days_for_year(year).get(date_str, []):
            if name not in special_names:
                special_names.append(name)
        if holiday_name:
            special_names = [name for name in special_names if name != holiday_name]
        special_day = " / ".join(special_names)
        birthdays = list(self._birthdays_mmdd.get(mmdd, []))

        if holiday_name:
            day_type = "holiday"
        elif is_makeup:
            day_type = "makeup_day"
        elif wd < 5:
            day_type = "school_day"
        else:
            day_type = "weekend"

        return DayContext(
            date=date_str,
            weekday=wd,
            day_type=day_type,
            holiday_name=holiday_name,
            birthdays=birthdays,
            special_day=special_day,
            special_day_names=list(special_names),
            self_names=set(self._self_names),
        )

    @property
    def makeup_days(self) -> set[str]:
        days: set[str] = set()
        for dataset in self._year_datasets.values():
            days.update(dataset.makeup_days)
        return days

    @property
    def birthdays_mmdd(self) -> dict[str, list[BirthdayEntry]]:
        return {key: list(value) for key, value in self._birthdays_mmdd.items()}

    @property
    def runtime_meta(self) -> dict[str, Any]:
        return asdict(self._runtime)

    def year_dataset(self, year: int) -> YearDataset | None:
        return self._year_datasets.get(year)

    async def _fetch_year_dataset(self, year: int) -> YearDataset | None:
        if self.official_source_enabled:
            try:
                dataset = await self._fetch_from_official(year)
            except Exception as exc:
                dataset = None
                self._runtime.last_fetch_error = str(exc)[:500]
                _L.warning(
                    "calendar_context fetch official failed | year={} error={}",
                    year,
                    self._runtime.last_fetch_error,
                )
            if dataset is not None:
                _L.info("calendar_context fetch official success | year={}", year)
                return dataset

        if self.fallback_local_holiday_lib:
            dataset = self._build_from_local_lib(year)
            if dataset is not None:
                _L.info("calendar_context fetch fallback_local success | year={}", year)
                return dataset

        if not self._runtime.last_fetch_error:
            self._runtime.last_fetch_error = "official and fallback failed"
        return None

    async def _fetch_from_official(self, year: int) -> YearDataset | None:
        url = self.official_url_template.format(year=year)
        async with httpx.AsyncClient(timeout=self.official_fetch_timeout_s, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Omubot calendar_context/1.0"})
            if resp.status_code >= 400:
                raise RuntimeError(f"official source returned {resp.status_code}")
            return self._parse_official_html(year, resp.text)

    def _parse_official_html(self, year: int, html: str) -> YearDataset | None:
        try:
            soup = BeautifulSoup(html, "lxml")
        except FeatureNotFound:
            soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        if "节假日安排" not in text:
            raise RuntimeError("official page missing holiday arrangement text")

        holidays: dict[str, str] = {}
        makeup_days: set[str] = set()
        for match in _OFFICIAL_SECTION_PATTERN.finditer(text):
            holiday_name = match.group("name")
            body = match.group("body").strip()
            for sentence in self._split_official_sentences(body):
                if "放假" in sentence:
                    for start_dt, end_dt in self._parse_official_date_spans(year, sentence):
                        current = start_dt
                        while current <= end_dt:
                            holidays[current.strftime("%Y-%m-%d")] = holiday_name
                            current += timedelta(days=1)
                if "上班" in sentence:
                    for work_dt, _ in self._parse_official_date_spans(year, sentence):
                        if work_dt.weekday() >= 5:
                            makeup_days.add(work_dt.strftime("%Y-%m-%d"))

        if not holidays:
            raise RuntimeError("official page parse yielded no holidays")

        return YearDataset(
            year=year,
            holidays=holidays,
            makeup_days=makeup_days,
            special_days={},
            source="official",
        )

    def _build_from_local_lib(self, year: int) -> YearDataset | None:
        if chinese_holiday_lib is None:
            self._runtime.last_fetch_error = "chinese_calendar unavailable"
            return None

        holidays: dict[str, str] = {}
        makeup_days: set[str] = set()
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        current = start
        try:
            while current < end:
                date_value = current.date()
                date_str = current.strftime("%Y-%m-%d")
                is_holiday, label = chinese_holiday_lib.get_holiday_detail(date_value)
                if is_holiday and label:
                    label_text = str(label).strip()
                    if label_text:
                        holidays[date_str] = self._normalize_fallback_holiday_label(label_text)
                if chinese_holiday_lib.is_workday(date_value) and current.weekday() >= 5:
                    makeup_days.add(date_str)
                current += timedelta(days=1)
        except NotImplementedError as exc:
            self._runtime.last_fetch_error = str(exc)[:500]
            return None

        if not holidays and not makeup_days:
            self._runtime.last_fetch_error = "local holiday lib yielded no data"
            return None

        return YearDataset(
            year=year,
            holidays=holidays,
            makeup_days=makeup_days,
            special_days={},
            source="fallback_local",
        )

    @staticmethod
    def _normalize_fallback_holiday_label(label: str) -> str:
        return _FALLBACK_HOLIDAY_LABELS.get(label, label)

    def _write_year_dataset(self, dataset: YearDataset) -> None:
        payload = {
            "holidays": dict(sorted(dataset.holidays.items())),
            "makeup_days": sorted(dataset.makeup_days),
            "special_days": dict(sorted((key, list(value)) for key, value in dataset.special_days.items())),
        }
        storage_path = self._storage_year_path(dataset.year)
        plugin_path = self._plugin_year_path(dataset.year)

        if storage_path is not None:
            try:
                self._write_json(storage_path, payload)
                self._runtime.storage_year_path = str(storage_path)
                _L.info("calendar_context write_cache success | path={}", storage_path)
            except Exception as exc:
                _L.warning("calendar_context write_cache failed | path={} error={}", storage_path, exc)

        if self.write_back_plugin_data and plugin_path is not None:
            try:
                self._write_json(plugin_path, payload)
                self._runtime.plugin_year_path = str(plugin_path)
                _L.info("calendar_context write_plugin_data success | path={}", plugin_path)
            except Exception as exc:
                _L.warning("calendar_context write_plugin_data failed | path={} error={}", plugin_path, exc)

    def _load_birthdays(self, paths: list[Path]) -> dict[str, list[BirthdayEntry]]:
        birthdays_raw: dict[str, dict[str, BirthdayEntry]] = {}
        for path in paths:
            payload = self._read_json(path)
            for date_mmdd, entries in self._parse_birthdays(payload.get("birthdays")).items():
                bucket = birthdays_raw.setdefault(date_mmdd, {})
                for entry in entries:
                    bucket[entry.name_cn] = entry
        return {mmdd: list(entries.values()) for mmdd, entries in birthdays_raw.items()}

    def _load_special_days(
        self, paths: list[Path]
    ) -> tuple[dict[str, list[str]], list[SpecialDayRule], list[LunarSpecialDayRule]]:
        result: dict[str, list[str]] = {}
        rules: list[SpecialDayRule] = []
        lunar_rules: list[LunarSpecialDayRule] = []
        for path in paths:
            payload = self._read_json(path)
            for mmdd, names in self._parse_string_list_map(payload.get("special_days")).items():
                bucket = result.setdefault(mmdd, [])
                for name in names:
                    if name not in bucket:
                        bucket.append(name)
            rules.extend(self._parse_special_day_rules(payload.get("special_day_rules")))
            lunar_rules.extend(self._parse_lunar_special_day_rules(payload.get("lunar_special_day_rules")))
        return result, rules, lunar_rules

    def _resolved_special_days_for_year(self, year: int) -> dict[str, list[str]]:
        cached = self._resolved_special_day_rules.get(year)
        if cached is not None:
            return cached

        resolved: dict[str, list[str]] = {}
        for rule in self._special_day_rules:
            date_str = self._resolve_special_day_rule(rule, year)
            if not date_str:
                continue
            bucket = resolved.setdefault(date_str, [])
            for name in rule.names:
                if name not in bucket:
                    bucket.append(name)
        self._resolved_special_day_rules[year] = resolved
        return resolved

    def _resolved_lunar_special_days_for_year(self, year: int) -> dict[str, list[str]]:
        cached = self._resolved_lunar_special_day_rules.get(year)
        if cached is not None:
            return cached

        resolved: dict[str, list[str]] = {}
        if LunarDate is None:
            self._resolved_lunar_special_day_rules[year] = resolved
            return resolved

        current = date(year, 1, 1)
        end = date(year + 1, 1, 1)
        while current < end:
            try:
                lunar = LunarDate.fromSolarDate(current.year, current.month, current.day)
            except Exception:
                current += timedelta(days=1)
                continue
            next_day = current + timedelta(days=1)
            try:
                next_lunar = LunarDate.fromSolarDate(next_day.year, next_day.month, next_day.day)
            except Exception:
                next_lunar = None

            matched_names: list[str] = []
            for rule in self._lunar_special_day_rules:
                if self._matches_lunar_rule(rule, lunar, next_lunar):
                    matched_names.extend(rule.names)
            if matched_names:
                bucket = resolved.setdefault(current.strftime("%Y-%m-%d"), [])
                for name in matched_names:
                    if name not in bucket:
                        bucket.append(name)
            current = next_day

        self._resolved_lunar_special_day_rules[year] = resolved
        return resolved

    def _build_materialized_special_days(
        self, year: int, existing: dict[str, list[str]] | None = None
    ) -> dict[str, list[str]]:
        merged: dict[str, list[str]] = {}
        sources = (
            existing or {},
            self._resolved_special_days_for_year(year),
            self._resolved_lunar_special_days_for_year(year),
        )
        for source in sources:
            for date_str, names in source.items():
                bucket = merged.setdefault(date_str, [])
                for name in names:
                    if name not in bucket:
                        bucket.append(name)
        return merged

    def _collect_year_files(self, root: Path | None, *, source: str) -> list[tuple[int, Path, str]]:
        if root is None or not root.is_dir():
            return []
        entries: list[tuple[int, Path, str]] = []
        for path in sorted(root.glob("*.json")):
            if path.name.startswith(".") or path.name.startswith("._"):
                continue
            year = self._extract_year_from_path(path)
            if year is not None:
                entries.append((year, path, source))
        return entries

    @staticmethod
    def _extract_year_from_path(path: Path) -> int | None:
        match = re.search(r"(\d{4})", path.stem)
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    def _storage_year_path(self, year: int) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / "years" / f"{year}.json"

    def _plugin_year_path(self, year: int) -> Path | None:
        if self.plugin_data_dir is None:
            return None
        return self.plugin_data_dir / "years" / f"{year}.json"

    def _parse_birthdays(self, value: Any) -> dict[str, list[BirthdayEntry]]:
        if not isinstance(value, list):
            return {}
        result: dict[str, list[BirthdayEntry]] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            date_mmdd = str(item.get("date_mmdd") or "").strip()
            name_cn = str(item.get("name_cn") or "").strip()
            name_jp = str(item.get("name_jp") or "").strip()
            group = str(item.get("group") or "").strip()
            if not date_mmdd or not name_cn or not name_jp or not group:
                continue
            entry = BirthdayEntry(
                name_cn=name_cn,
                name_jp=name_jp,
                group=group,
                is_wxs_member=bool(item.get("is_wxs_member", False)),
                aliases=[str(alias).strip() for alias in item.get("aliases", []) if str(alias).strip()],
                tags=[str(tag).strip() for tag in item.get("tags", []) if str(tag).strip()],
            )
            result.setdefault(date_mmdd, []).append(entry)
        return result

    @staticmethod
    def _parse_string_map(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {
            str(key).strip(): str(item).strip()
            for key, item in value.items()
            if str(key).strip() and str(item).strip()
        }

    @staticmethod
    def _parse_string_list_map(value: Any) -> dict[str, list[str]]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, list[str]] = {}
        for key, item in value.items():
            date_key = str(key).strip()
            if not date_key:
                continue
            values: list[str] = []
            if isinstance(item, list):
                values = [str(part).strip() for part in item if str(part).strip()]
            elif str(item).strip():
                values = [str(item).strip()]
            if values:
                result[date_key] = values
        return result

    @staticmethod
    def _parse_special_day_rules(value: Any) -> list[SpecialDayRule]:
        if not isinstance(value, list):
            return []

        rules: list[SpecialDayRule] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            month = CalendarContextService._coerce_int(item.get("month"))
            nth = CalendarContextService._coerce_int(item.get("nth"))
            weekday = CalendarContextService._parse_weekday(item.get("weekday"))
            names = CalendarContextService._coerce_names(item.get("names"), item.get("name"))
            if month is None or not 1 <= month <= 12:
                continue
            if nth is None or nth == 0:
                continue
            if weekday is None or not names:
                continue
            rules.append(SpecialDayRule(month=month, weekday=weekday, nth=nth, names=names))
        return rules

    @staticmethod
    def _parse_lunar_special_day_rules(value: Any) -> list[LunarSpecialDayRule]:
        if not isinstance(value, list):
            return []

        rules: list[LunarSpecialDayRule] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            names = CalendarContextService._coerce_names(item.get("names"), item.get("name"))
            if not names:
                continue
            kind = str(item.get("kind") or "fixed").strip().casefold()
            leap_month = bool(item.get("leap_month", False))
            if kind == "year_eve":
                rules.append(LunarSpecialDayRule(names=names, kind="year_eve", leap_month=leap_month))
                continue

            month = CalendarContextService._coerce_int(item.get("month"))
            day = CalendarContextService._coerce_int(item.get("day"))
            if month is None or not 1 <= month <= 12:
                continue
            if day is None or not 1 <= day <= 30:
                continue
            rules.append(
                LunarSpecialDayRule(
                    names=names,
                    month=month,
                    day=day,
                    kind="fixed",
                    leap_month=leap_month,
                )
            )
        return rules

    @staticmethod
    def _matches_lunar_rule(rule: LunarSpecialDayRule, lunar: Any, next_lunar: Any) -> bool:
        is_leap = bool(getattr(lunar, "isLeapMonth", False))
        if rule.kind == "year_eve":
            return (
                bool(next_lunar)
                and getattr(next_lunar, "month", None) == 1
                and getattr(next_lunar, "day", None) == 1
            )
        return (
            getattr(lunar, "month", None) == rule.month
            and getattr(lunar, "day", None) == rule.day
            and is_leap == rule.leap_month
        )

    @staticmethod
    def _resolve_special_day_rule(rule: SpecialDayRule, year: int) -> str | None:
        days_in_month = calendar.monthrange(year, rule.month)[1]
        if rule.nth > 0:
            first_day = datetime(year, rule.month, 1)
            offset = (rule.weekday - first_day.weekday()) % 7
            day = 1 + offset + 7 * (rule.nth - 1)
            if day > days_in_month:
                return None
        else:
            last_day = datetime(year, rule.month, days_in_month)
            offset = (last_day.weekday() - rule.weekday) % 7
            day = days_in_month - offset + 7 * (rule.nth + 1)
            if day < 1:
                return None
        return f"{year}-{rule.month:02d}-{day:02d}"

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        try:
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _coerce_names(names_value: Any, name_value: Any) -> list[str]:
        if isinstance(names_value, list):
            return [str(name).strip() for name in names_value if str(name).strip()]
        if str(name_value or "").strip():
            return [str(name_value).strip()]
        return []

    @staticmethod
    def _parse_weekday(value: Any) -> int | None:
        if isinstance(value, int):
            return value if 0 <= value <= 6 else None
        text = str(value or "").strip().casefold()
        mapping = {
            "0": 0,
            "monday": 0,
            "mon": 0,
            "周一": 0,
            "星期一": 0,
            "礼拜一": 0,
            "1": 1,
            "tuesday": 1,
            "tue": 1,
            "周二": 1,
            "星期二": 1,
            "礼拜二": 1,
            "2": 2,
            "wednesday": 2,
            "wed": 2,
            "周三": 2,
            "星期三": 2,
            "礼拜三": 2,
            "3": 3,
            "thursday": 3,
            "thu": 3,
            "thur": 3,
            "周四": 3,
            "星期四": 3,
            "礼拜四": 3,
            "4": 4,
            "friday": 4,
            "fri": 4,
            "周五": 4,
            "星期五": 4,
            "礼拜五": 4,
            "5": 5,
            "saturday": 5,
            "sat": 5,
            "周六": 5,
            "星期六": 5,
            "礼拜六": 5,
            "6": 6,
            "sunday": 6,
            "sun": 6,
            "周日": 6,
            "周天": 6,
            "星期日": 6,
            "星期天": 6,
            "礼拜日": 6,
            "礼拜天": 6,
        }
        return mapping.get(text)

    @staticmethod
    def _split_official_sentences(text: str) -> list[str]:
        parts = re.split(r"[。；;\n]+", text)
        return [part.strip() for part in parts if part.strip()]

    @staticmethod
    def _parse_official_date_spans(year: int, text: str) -> list[tuple[date, date]]:
        spans: list[tuple[date, date]] = []
        for match in _OFFICIAL_DATE_PATTERN.finditer(text):
            start_month = CalendarContextService._coerce_int(match.group("start_month"))
            start_day = CalendarContextService._coerce_int(match.group("start_day"))
            end_month = CalendarContextService._coerce_int(match.group("end_month")) or start_month
            end_day = CalendarContextService._coerce_int(match.group("end_day")) or start_day
            if start_month is None or start_day is None or end_month is None or end_day is None:
                continue
            try:
                start_dt = date(year, start_month, start_day)
                end_dt = date(year, end_month, end_day)
            except ValueError:
                continue
            if end_dt < start_dt:
                continue
            spans.append((start_dt, end_dt))
        return spans

    @staticmethod
    def _parse_makeup_days(value: Any) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {str(item).strip() for item in value if str(item).strip()}

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if path.name.startswith(".") or path.name.startswith("._"):
            return {}
        try:
            is_file = path.is_file()
        except OSError:
            return {}
        if not is_file:
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
