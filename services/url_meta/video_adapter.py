"""Optional video metadata adapter for Bilibili and YouTube URLs."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import aiohttp

_URL_RE = re.compile(r"https?://[^\s<>'\"，。！？、）)]+", re.I)
_BILI_RE = re.compile(r"/video/(BV[0-9A-Za-z]{10}|av\d+)", re.I)
_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_TIMEOUT_S = 0.5


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    platform: str
    url: str
    video_id: str
    title: str


async def collect_video_metadata(
    text: str,
    *,
    enabled: bool = False,
    session: Any | None = None,
    timeout_s: float = _TIMEOUT_S,
    limit: int = 2,
) -> list[VideoMetadata]:
    if not enabled:
        return []
    refs = _extract_video_refs(text, limit=limit)
    if not refs:
        return []
    if session is not None:
        return [item for ref in refs if (item := await _fetch_metadata(session, ref, timeout_s))]
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "Omubot/1.0"}) as owned:
        return [item for ref in refs if (item := await _fetch_metadata(owned, ref, timeout_s))]


def _extract_video_refs(text: str, *, limit: int) -> list[tuple[str, str, str, str]]:
    seen: set[tuple[str, str]] = set()
    refs: list[tuple[str, str, str, str]] = []
    for match in _URL_RE.finditer(text):
        ref = _parse_video_url(match.group(0).rstrip(".,;:!?"))
        if ref is None or (ref[0], ref[2]) in seen:
            continue
        seen.add((ref[0], ref[2]))
        refs.append(ref)
        if len(refs) >= limit:
            break
    return refs


def _parse_video_url(url: str) -> tuple[str, str, str, str] | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "bilibili.com" or host.endswith(".bilibili.com"):
        match = _BILI_RE.search(parsed.path)
        if not match:
            return None
        raw = match.group(1)
        is_av = raw.lower().startswith("av")
        video_id = f"av{raw[2:]}" if is_av else f"BV{raw[2:]}"
        query = f"aid={raw[2:]}" if is_av else f"bvid={video_id}"
        endpoint = f"https://api.bilibili.com/x/web-interface/view?{query}"
        return ("bilibili", url, video_id, endpoint)
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif host in _YT_HOSTS:
        video_id = parse_qs(parsed.query).get("v", [""])[0] if parsed.path == "/watch" else ""
        if parsed.path.startswith("/shorts/"):
            video_id = parsed.path.removeprefix("/shorts/").split("/", 1)[0]
    else:
        return None
    if not _YT_ID_RE.fullmatch(video_id):
        return None
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    endpoint = f"https://www.youtube.com/oembed?format=json&url={quote(canonical, safe='')}"
    return ("youtube", url, video_id, endpoint)


async def _fetch_metadata(session: Any, ref: tuple[str, str, str, str], timeout_s: float) -> VideoMetadata | None:
    platform, url, video_id, endpoint = ref
    try:
        async with asyncio.timeout(timeout_s):
            async with session.get(endpoint, timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
                if getattr(resp, "status", 0) != 200:
                    return None
                data = await resp.json(content_type=None)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if platform == "bilibili":
        if data.get("code", 0) != 0:
            return None
        data = data.get("data")
        if not isinstance(data, dict):
            return None
    value = data.get("title")
    title = " ".join(value.split())[:160] if isinstance(value, str) else ""
    return VideoMetadata(platform, url, video_id, title) if title else None
