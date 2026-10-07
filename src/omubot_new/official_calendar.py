"""Fetch and cache officially published Chinese annual holiday arrangements."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from time import monotonic
from typing import cast
from urllib.parse import urlsplit

import httpx

_LOG = logging.getLogger(__name__)

_POLICY_SEARCH_URL = "https://sousuo.www.gov.cn/search-gov/data"
_POLICY_SEARCH_TYPE = "zhengcelibrary_gw"
_POLICY_CATEGORY = "gwyzcwjk"
_ANNOUNCEMENT_URLS = {
    2025: "https://www.gov.cn/zhengce/zhengceku/202411/content_6986383.htm",
    2026: "https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm",
}
_OFFICIAL_PATH = re.compile(r"^/zhengce/zhengceku/\d{6}/content_\d+\.htm$")
_HOLIDAY_NAMES = ("元旦", "春节", "清明节", "劳动节", "端午节", "中秋节", "国庆节")
_HEADER = re.compile(
    r"(?P<ordinal>[一二三四五六七])、(?P<names>"
    + "|".join(_HOLIDAY_NAMES)
    + r"(?:、(?:"
    + "|".join(_HOLIDAY_NAMES)
    + r"))*)(?:：|:)"
)
_DATE_SPAN = re.compile(
    r"(?P<start_month>\d{1,2})月(?P<start_day>\d{1,2})日"
    r"(?:（[^）]*）|\([^)]*\))?"
    r"(?:至(?:(?P<end_month>\d{1,2})月)?(?P<end_day>\d{1,2})日"
    r"(?:（[^）]*）|\([^)]*\))?)?"
)
_CACHE_SCHEMA = 1
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_HOLIDAY_DAYS = 40
_FAILURE_RETRY_SECONDS = 6 * 60 * 60


class OfficialCalendarError(ValueError):
    """Official calendar data could not be identified or parsed safely."""


@dataclass(frozen=True, slots=True)
class CalendarDay:
    date: date
    holiday_name: str | None
    makeup_workday: bool
    source_url: str

    @property
    def fingerprint(self) -> str:
        """Body-free identity for this verified, managed calendar fact."""
        material = (self.date.isoformat(), self.holiday_name, self.makeup_workday, self.source_url)
        return hashlib.sha256(json.dumps(material, ensure_ascii=False,
            separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class _YearCalendar:
    year: int
    holidays: dict[str, str]
    makeup_days: frozenset[str]
    source_url: str


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript"}:
            self._suppressed += 1
        elif not self._suppressed and tag in {"br", "p", "div", "li", "h1", "h2"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._suppressed:
            self._suppressed -= 1
        elif not self._suppressed and tag in {"p", "div", "li", "h1", "h2"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._suppressed:
            self.parts.append(data)


def _visible_text(html: str) -> str:
    parser = _VisibleText()
    parser.feed(html)
    parser.close()
    return re.sub(r"\s+", "", "".join(parser.parts))


def _validate_official_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise OfficialCalendarError("annual notice URL is malformed") from exc
    if (
        parts.scheme != "https"
        or parts.hostname != "www.gov.cn"
        or parts.username is not None
        or parts.password is not None
        or port is not None
        or parts.query
        or parts.fragment
        or _OFFICIAL_PATH.fullmatch(parts.path) is None
    ):
        raise OfficialCalendarError("annual notice URL is outside the verified gov.cn policy path")
    return url


def _year_title(year: int) -> str:
    if type(year) is not int or not 2000 <= year <= 9999:
        raise OfficialCalendarError("calendar year is invalid")
    return f"国务院办公厅关于{year}年部分节假日安排的通知"


def parse_announcement(year: int, html: str, source_url: str) -> _YearCalendar:
    """Parse the seven annual arrangement sections and explicit makeup workdays."""

    source_url = _validate_official_url(source_url)
    title = _year_title(year)
    if type(html) is not str or not html:
        raise OfficialCalendarError("annual notice body is empty")

    text = _visible_text(html)
    if title not in text or "发文机关：国务院办公厅" not in text:
        raise OfficialCalendarError("annual notice title or issuer does not match")

    headers = list(_HEADER.finditer(text))
    if not headers:
        raise OfficialCalendarError("annual notice has no holiday sections")

    found_names: list[str] = []
    ordinals: list[int] = []
    holidays: dict[str, str] = {}
    makeup_days: set[str] = set()
    for index, header in enumerate(headers):
        ordinal = "一二三四五六七".index(header.group("ordinal")) + 1
        ordinals.append(ordinal)
        names = header.group("names").split("、")
        found_names.extend(names)
        body_end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        body = text[header.end() : body_end]
        footer = re.search(r"国务院办公厅\d{4}年\d{1,2}月\d{1,2}日", body)
        if footer is not None:
            body = body[: footer.start()]

        for sentence in re.split(r"[。；;]", body):
            spans = list(_DATE_SPAN.finditer(sentence))
            if "放假" in sentence:
                for span in spans:
                    start, end = _span_dates(year, span)
                    current = start
                    while current <= end:
                        if current.year == year:
                            holidays[current.isoformat()] = "、".join(names)
                        current += timedelta(days=1)
            if "上班" in sentence:
                for span in spans:
                    start, end = _span_dates(year, span)
                    current = start
                    while current <= end:
                        if current.year == year:
                            makeup_days.add(current.isoformat())
                        current += timedelta(days=1)

    if (
        ordinals != list(range(1, len(ordinals) + 1))
        or set(found_names) != set(_HOLIDAY_NAMES)
        or len(found_names) != len(set(found_names))
        or not holidays
        or len(holidays) > _MAX_HOLIDAY_DAYS
        or holidays.keys() & makeup_days
    ):
        raise OfficialCalendarError("annual notice sections are incomplete or contradictory")

    return _YearCalendar(year, holidays, frozenset(makeup_days), source_url)


def _span_dates(year: int, match: re.Match[str]) -> tuple[date, date]:
    start_month = int(match.group("start_month"))
    start_day = int(match.group("start_day"))
    end_month = int(match.group("end_month") or start_month)
    end_day = int(match.group("end_day") or start_day)
    end_year = year + 1 if end_month < start_month else year
    if match.group("end_month") is None and end_day < start_day:
        raise OfficialCalendarError("date range omits its ending month")
    try:
        start = date(year, start_month, start_day)
        end = date(end_year, end_month, end_day)
    except ValueError as exc:
        raise OfficialCalendarError("annual notice contains an invalid date") from exc
    if (end - start).days > 31:
        raise OfficialCalendarError("annual notice date range is too long")
    return start, end


def _plain_title(value: object) -> str:
    if type(value) is not str:
        return ""
    return _visible_text(value)


async def _request(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str | int] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    try:
        async with client.stream("GET", url, params=params, headers=headers) as response:
            if response.status_code != 200:
                raise OfficialCalendarError("official calendar endpoint returned a non-200 status")
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                    raise OfficialCalendarError("official calendar response exceeded its size limit")
                body.extend(chunk)
            # aiter_bytes has already decoded Content-Encoding. Retain the
            # content type for text decoding without decoding the body twice.
            return httpx.Response(
                response.status_code,
                content=bytes(body),
                headers={"content-type": response.headers.get("content-type", "")},
                request=response.request,
            )
    except httpx.HTTPError as exc:
        raise OfficialCalendarError("official calendar request failed") from exc


async def _discover_url(year: int, client: httpx.AsyncClient) -> str:
    title = _year_title(year)
    params = {
        "t": _POLICY_SEARCH_TYPE,
        "q": title,
        "searchfield": "title",
        "p": 1,
        "n": 5,
        "type": _POLICY_CATEGORY,
    }
    response = await _request(client, _POLICY_SEARCH_URL, params=params)
    try:
        raw_payload: object = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise OfficialCalendarError("official policy search returned invalid JSON") from exc
    if not isinstance(raw_payload, dict):
        raise OfficialCalendarError("official policy search did not succeed")
    payload = cast(dict[str, object], raw_payload)
    if payload.get("code") != 200:
        raise OfficialCalendarError("official policy search did not succeed")
    raw_search_vo = payload.get("searchVO")
    if not isinstance(raw_search_vo, dict):
        raise OfficialCalendarError("official policy search result shape is invalid")
    search_vo = cast(dict[str, object], raw_search_vo)
    raw_results = search_vo.get("listVO")
    total_count = search_vo.get("totalCount")
    if not isinstance(raw_results, list) or type(total_count) is not int:
        raise OfficialCalendarError("official policy search result shape is invalid")
    results = cast(list[object], raw_results)
    if total_count != len(results):
        raise OfficialCalendarError("official policy search result shape is invalid")

    hits: list[dict[str, object]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        result = cast(dict[str, object], item)
        if _plain_title(result.get("title")) == title and result.get("puborg") == "国务院办公厅":
            hits.append(result)
    if len(hits) != 1:
        raise OfficialCalendarError("official annual notice search was not unique")
    url = hits[0].get("url")
    if type(url) is not str:
        raise OfficialCalendarError("official annual notice search result has no URL")
    return _validate_official_url(url)


def _load_cache(path: Path, year: int) -> _YearCalendar | None:
    try:
        raw_value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw_value, dict):
        return None
    value = cast(dict[str, object], raw_value)
    if value.get("schema") != _CACHE_SCHEMA or value.get("year") != year:
        return None
    try:
        raw_source_url = value["source_url"]
        raw_holidays = value["holidays"]
        raw_makeup = value["makeup_days"]
        if (
            not isinstance(raw_source_url, str)
            or not isinstance(raw_holidays, dict)
            or not isinstance(raw_makeup, list)
        ):
            return None
        source_url = _validate_official_url(raw_source_url)
        holidays_value = cast(dict[object, object], raw_holidays)
        makeup_value = cast(list[object], raw_makeup)
        holidays: dict[str, str] = {}
        makeup_days: set[str] = set()
        for raw_key, raw_label in holidays_value.items():
            if not isinstance(raw_key, str) or not isinstance(raw_label, str):
                return None
            key, label = raw_key, raw_label
            day = date.fromisoformat(key)
            if day.year != year or not label:
                return None
            holidays[key] = label
        for raw_key in makeup_value:
            if not isinstance(raw_key, str):
                return None
            key = raw_key
            day = date.fromisoformat(key)
            if day.year != year:
                return None
            makeup_days.add(key)
        if not holidays or len(holidays) > _MAX_HOLIDAY_DAYS:
            return None
        if holidays.keys() & makeup_days:
            return None
        return _YearCalendar(year, holidays, frozenset(makeup_days), source_url)
    except (KeyError, TypeError, ValueError):
        return None


def _write_cache(path: Path, dataset: _YearCalendar) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": _CACHE_SCHEMA,
        "year": dataset.year,
        "source_url": dataset.source_url,
        "holidays": dict(sorted(dataset.holidays.items())),
        "makeup_days": sorted(dataset.makeup_days),
    }
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(payload, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class OfficialCalendar:
    """Fetch official annual arrangements once per process and cache by year."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)
        self._years: dict[int, _YearCalendar] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._retry_after: dict[int, float] = {}

    def known_cached_day(self, day: date) -> CalendarDay | None:
        """Read managed current evidence during dispatch, without fetching it."""
        year_data = self._years.get(day.year)
        if year_data is None:
            return None
        key = day.isoformat()
        return CalendarDay(day, year_data.holidays.get(key), key in year_data.makeup_days,
                           year_data.source_url)

    async def get_day(self, day: date) -> CalendarDay | None:
        if type(day) is not date:
            raise TypeError("day must be a date")
        year_data = await self._get_year(day.year)
        if year_data is None:
            return None
        key = day.isoformat()
        return CalendarDay(
            date=day,
            holiday_name=year_data.holidays.get(key),
            makeup_workday=key in year_data.makeup_days,
            source_url=year_data.source_url,
        )

    async def _get_year(self, year: int) -> _YearCalendar | None:
        cached = self._years.get(year)
        if cached is not None:
            return cached
        lock = self._locks.setdefault(year, asyncio.Lock())
        async with lock:
            cached = self._years.get(year)
            if cached is not None:
                return cached
            cache_path = self._cache_dir / f"china-holidays-{year}.json"
            cached = _load_cache(cache_path, year)
            if cached is not None:
                self._years[year] = cached
                return cached
            if monotonic() < self._retry_after.get(year, 0.0):
                return None
            try:
                dataset = await self._fetch_year(year)
            except OfficialCalendarError as exc:
                self._retry_after[year] = monotonic() + _FAILURE_RETRY_SECONDS
                _LOG.warning("official calendar unavailable for %s: %s", year, exc)
                return None
            try:
                _write_cache(cache_path, dataset)
            except OSError as exc:
                _LOG.warning("official calendar cache write failed for %s: %s", year, exc)
            self._years[year] = dataset
            return dataset

    async def _fetch_year(self, year: int) -> _YearCalendar:
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            url = _ANNOUNCEMENT_URLS.get(year)
            if url is None:
                url = await _discover_url(year, client)
            url = _validate_official_url(url)
            response = await _request(
                client,
                url,
                headers={"User-Agent": "Omubot-new official holiday calendar/1.0"},
            )
            if "text/html" not in response.headers.get("content-type", "").lower():
                raise OfficialCalendarError("official annual notice is not HTML")
            return parse_announcement(year, response.text, url)
