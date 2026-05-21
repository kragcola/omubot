"""Shared helpers for lightweight sticker capture from OneBot image segments."""

from __future__ import annotations

from typing import Any

DEFAULT_STICKER_USAGE_HINT = "群友常用表情，适合轻松闲聊或回应同类情绪时使用"
_STICKER_SUMMARY_TOKENS = ("动画表情", "表情", "mface", "sticker")


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
