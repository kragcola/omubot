"""Compatibility exports for the Bilibili directory plugin."""

from plugins.bilibili.plugin import (
    BilibiliConfig,
    BilibiliPlugin,
    _parse_duration,
    _VideoId,
    evaluate_interest,
    extract_video_id,
    format_video_summary,
    has_bilibili_link,
)

__all__ = [
    "BilibiliConfig",
    "BilibiliPlugin",
    "_VideoId",
    "_parse_duration",
    "evaluate_interest",
    "extract_video_id",
    "format_video_summary",
    "has_bilibili_link",
]
