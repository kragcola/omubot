"""Best-effort OpenGraph title extraction for recent group URLs."""

from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from html import unescape
from typing import Any, Final

import aiohttp

from services.url_meta.blacklist import is_blocked_url

_URL_RE: Final = re.compile(r"https?://[^\s<>'\"，。！？、）)]+", re.I)
_OG_RE: Final = re.compile(
    r"<meta\b(?=[^>]*(?:property|name)\s*=\s*['\"]og:title['\"])(?=[^>]*content\s*=\s*(['\"])(.*?)\1)[^>]*>",
    re.I | re.S,
)
_TITLE_RE: Final = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
_TTL_S: Final = 24 * 60 * 60
_MAX_CACHE: Final = 128
_DEFAULT_TIMEOUT_S: Final = 0.5
_CACHE: OrderedDict[str, tuple[float, str | None]] = OrderedDict()

@dataclass(frozen=True, slots=True)
class UrlTitle:
    url: str
    title: str


async def build_url_title_context(text: str, *, limit: int = 3) -> str:
    titles = await collect_url_titles(text, limit=limit)
    if not titles:
        return ""
    lines = ["【链接标题】"]
    lines.extend(f"- {item.title} ({item.url})" for item in titles)
    return "\n".join(lines)


async def collect_url_titles(
    text: str,
    *,
    session: Any | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    limit: int = 3,
) -> list[UrlTitle]:
    urls = _extract_urls(text, limit=limit)
    if not urls:
        return []
    if session is not None:
        return [item for item in [await _title_for_url(session, url, timeout_s) for url in urls] if item]
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "Omubot/1.0"}) as owned:
        return [item for item in [await _title_for_url(owned, url, timeout_s) for url in urls] if item]


def clear_url_title_cache() -> None:
    _CACHE.clear()


def _extract_urls(text: str, *, limit: int) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?")
        if url in seen or is_blocked_url(url):
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


async def _title_for_url(session: Any, url: str, timeout_s: float) -> UrlTitle | None:
    now = time.time()
    hit, cached = _cache_get(url, now)
    if hit:
        return UrlTitle(url, cached) if cached else None
    title = await _fetch_title(session, url, timeout_s)
    _cache_put(url, title, now)
    return UrlTitle(url, title) if title else None


async def _fetch_title(session: Any, url: str, timeout_s: float) -> str | None:
    try:
        async with asyncio.timeout(timeout_s):
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
                if getattr(resp, "status", 0) != 200:
                    return None
                content_type = str(getattr(resp, "headers", {}).get("content-type", "text/html")).lower()
                if "html" not in content_type:
                    return None
                html = await resp.text(errors="ignore")
    except Exception:
        return None
    return _extract_title(html)


def _extract_title(html: str) -> str | None:
    match = _OG_RE.search(html) or _TITLE_RE.search(html)
    if not match:
        return None
    raw = match.group(2 if match.re is _OG_RE else 1)
    title = " ".join(unescape(raw).split())
    return title[:120] or None


def _cache_get(url: str, now: float) -> tuple[bool, str | None]:
    item = _CACHE.get(url)
    if item is None:
        return False, None
    ts, title = item
    if now - ts > _TTL_S:
        _CACHE.pop(url, None)
        return False, None
    _CACHE.move_to_end(url)
    return True, title


def _cache_put(url: str, title: str | None, now: float) -> None:
    _CACHE[url] = (now, title)
    _CACHE.move_to_end(url)
    while len(_CACHE) > _MAX_CACHE:
        _CACHE.popitem(last=False)
