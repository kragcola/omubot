"""Shared helpers for lightweight sticker capture from OneBot image segments."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

DEFAULT_STICKER_USAGE_HINT = "群友常用表情，适合轻松闲聊或回应同类情绪时使用"
_STICKER_SUMMARY_TOKENS = ("动画表情", "表情", "mface", "sticker")
_EMOTION_TAG_PROMPT = (
    "请只输出一句简短中文，概括这张表情包最适合在什么情绪或聊天场景下发送。"
    "像给 sticker 写 usage_hint，一句话就够，不要解释分析，不要编号。"
)
_MEDIA_TYPE_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_MAX_EMOTION_TAG_LEN = 32

_L = logger.bind(channel="system")


def segment_value(seg: Any, key: str) -> str:
    """Return a normalized string value from a OneBot segment-like object."""
    data = getattr(seg, "data", None)
    if isinstance(data, dict):
        return str(data.get(key) or "").strip()
    if isinstance(seg, dict):
        data = seg.get("data")
        if isinstance(data, dict):
            return str(data.get(key) or "").strip()
        return str(seg.get(key) or "").strip()
    return ""


def is_sticker_like_segment(seg: Any) -> bool:
    """Return True when a segment is explicitly marked as a sticker-like image."""
    seg_type = getattr(seg, "type", "")
    if isinstance(seg, dict):
        seg_type = str(seg.get("type") or "")
    if seg_type != "image":
        return False

    sub_type = segment_value(seg, "sub_type")
    if sub_type in {"1", "7"}:
        return True

    summary = segment_value(seg, "summary")
    normalized_summary = summary.strip("[]").lower()
    return any(token in normalized_summary for token in _STICKER_SUMMARY_TOKENS)


def sticker_description_from_segment(seg: Any) -> str:
    """Build a conservative default description for an auto-captured sticker."""
    summary = segment_value(seg, "summary").strip()
    if summary:
        return f"群友发送的表情：{summary.strip('[]')}"

    sub_type = segment_value(seg, "sub_type")
    if sub_type == "1":
        return "群友发送的动画表情"
    if sub_type == "7":
        return "群友发送的表情包"
    return "群友发送的表情"


def sticker_media_type(path: str | Path | None) -> str:
    suffix = Path(path).suffix.lower() if path else ""
    return _MEDIA_TYPE_BY_SUFFIX.get(suffix, "image/jpeg")


def normalize_emotion_tag(text: str | None) -> str:
    compact = " ".join(str(text or "").replace("\n", " ").split()).strip(" \t，。！？；：,.!?;:")
    if not compact:
        return ""
    if len(compact) <= _MAX_EMOTION_TAG_LEN:
        return compact
    return compact[:_MAX_EMOTION_TAG_LEN].rstrip(" \t，。！？；：,.!?;:")


async def emit_emotion_tag(
    sticker_store: Any | None,
    sticker_id: str,
    *,
    image_data: bytes,
    vision_client: Any | None,
    media_type: str = "image/jpeg",
    fallback: str = DEFAULT_STICKER_USAGE_HINT,
    overwrite: bool = False,
    dry_run: bool = False,
) -> str:
    """Best-effort usage_hint enrichment for auto-captured stickers."""
    if sticker_store is None:
        return fallback
    entry = sticker_store.get(sticker_id)
    if entry is None:
        return fallback

    current_hint = str(entry.get("usage_hint") or "").strip()
    if current_hint and current_hint != fallback and not overwrite:
        return current_hint
    if vision_client is None:
        return current_hint or fallback

    try:
        raw_tag = await vision_client.describe_image(
            image_data,
            media_type=media_type,
            prompt=_EMOTION_TAG_PROMPT,
        )
    except Exception as exc:
        _L.debug("sticker emotion tag skipped | sticker_id={} reason={}", sticker_id, exc)
        return current_hint or fallback

    tag = normalize_emotion_tag(raw_tag)
    if not tag:
        return current_hint or fallback
    if dry_run:
        return tag
    if tag == current_hint:
        return tag
    try:
        updated = bool(sticker_store.update(sticker_id, usage_hint=tag))
    except Exception as exc:
        _L.warning("sticker emotion tag update failed | sticker_id={} err={}", sticker_id, exc)
        return current_hint or fallback
    if updated:
        _L.debug("sticker emotion tag updated | sticker_id={} tag={!r}", sticker_id, tag)
        return tag
    return current_hint or fallback
