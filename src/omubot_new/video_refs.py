"""Finite video references retained at the authenticated rich-message boundary.

This parser does no IO. A platform identifier recognises a card shape; neither
it nor a canonical URL grants permission to fetch or upload the card content.
"""

from __future__ import annotations

import json
import re
from typing import cast
from urllib.parse import parse_qs, urlsplit

from pydantic import JsonValue

from .rich_messages import VideoRef

_BILI_HOSTS = frozenset({"bilibili.com", "www.bilibili.com", "m.bilibili.com"})
_BILI_PATH = re.compile(r"/video/(BV[0-9A-Za-z]{10}|av[0-9]+)/?\Z")


def parse_bilibili_card(raw: str) -> tuple[VideoRef, ...]:
    """Recognise the legacy mini-program shape without keeping its raw JSON.

    Generic/malformed JSON cards keep their existing opaque card projection.
    Only finite direct video IDs are supported; redirects and title search are
    separate capabilities and are never inferred from this source metadata.
    """
    try:
        card = cast(JsonValue, json.loads(raw))
    except (json.JSONDecodeError, RecursionError):
        return ()
    if not isinstance(card, dict):
        return ()
    meta = card.get("meta")
    if not isinstance(meta, dict):
        return ()
    detail = meta.get("detail_1")
    if not isinstance(detail, dict) or str(detail.get("appid", "")) != "1109937557":
        return ()
    raw_title = detail.get("desc") or detail.get("title")
    title = " ".join(raw_title.split())[:160] if isinstance(raw_title, str) else ""
    refs: dict[str, VideoRef] = {}
    for location in (detail, meta, card):
        for key in ("url", "qqdocurl", "share_url"):
            value = location.get(key)
            if not isinstance(value, str) or len(value) > 2048:
                continue
            try:
                parsed = urlsplit(value.strip())
                port = parsed.port
            except ValueError:
                continue
            if (parsed.scheme not in {"http", "https"} or parsed.hostname not in _BILI_HOSTS
                    or parsed.username is not None or parsed.password is not None
                    or port not in (None, 443)):
                continue
            match = _BILI_PATH.fullmatch(parsed.path)
            if match is None:
                continue
            video_id = match.group(1)
            refs.setdefault(video_id, VideoRef(
                platform="bilibili", video_id=video_id,
                url=f"https://www.bilibili.com/video/{video_id}", title=title,
            ))
            if len(refs) == 2:
                return tuple(refs.values())
    return tuple(refs.values())


_YOUTUBE_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
})
_YOUTUBE_ID = re.compile(r"[-_A-Za-z0-9]{6,64}\Z")
_TEXT_REFERENCES = re.compile(r"https?://[^\s<>]+|\bBV[0-9A-Za-z]{10}\b|\bav[0-9]+\b")


def parse_video_url(value: str) -> VideoRef | None:
    """Recognise a finite direct URL, without resolving redirects or other hosts."""
    if len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme not in {"http", "https"} or parsed.username is not None
            or parsed.password is not None or port not in (None, 443)):
        return None
    if parsed.hostname in _BILI_HOSTS:
        match = _BILI_PATH.fullmatch(parsed.path)
        if match is not None:
            video_id = match.group(1)
            return VideoRef("bilibili", video_id,
                            f"https://www.bilibili.com/video/{video_id}", "")
        return None
    if parsed.hostname not in _YOUTUBE_HOSTS:
        return None
    video_id = ""
    if parsed.hostname == "youtu.be":
        video_id = parsed.path.removeprefix("/")
    elif parsed.path == "/watch":
        values = parse_qs(parsed.query).get("v", [])
        if len(values) == 1:
            video_id = values[0]
    elif parsed.path.startswith("/shorts/"):
        video_id = parsed.path.removeprefix("/shorts/")
    if _YOUTUBE_ID.fullmatch(video_id) is None:
        return None
    return VideoRef("youtube", video_id, f"https://www.youtube.com/watch?v={video_id}", "")


def parse_video_text(text: str) -> tuple[VideoRef, ...]:
    """Keep at most two direct references in current plaintext order."""
    refs: dict[tuple[str, str], VideoRef] = {}
    for match in _TEXT_REFERENCES.finditer(text):
        value = match.group().rstrip("。，、！？.,!?)］】")
        if value.startswith(("http://", "https://")):
            ref = parse_video_url(value)
        else:
            ref = VideoRef("bilibili", value, f"https://www.bilibili.com/video/{value}", "")
        if ref is not None:
            refs.setdefault((ref.platform, ref.video_id), ref)
            if len(refs) == 2:
                break
    return tuple(refs.values())
