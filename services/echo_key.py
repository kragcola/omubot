"""Stable OneBot message keys shared by Router and the Echo plugin."""

from __future__ import annotations

import json
from collections.abc import Iterable


def build_echo_key(segments: Iterable[object]) -> str:
    """Build a stable key from OneBot message segments for echo detection."""
    parts: list[str] = []
    for segment in segments:
        segment_type: str = getattr(segment, "type", "")
        data: dict = getattr(segment, "data", {}) or {}
        if segment_type == "text":
            parts.append(data.get("text", ""))
        elif segment_type == "image":
            subtype = str(data.get("sub_type", "0"))
            file_hash = data.get("file", "")
            parts.append(f"[image:{subtype}:{file_hash}]")
        elif segment_type == "face":
            parts.append(f"[face:{data.get('id', '')}]")
        elif segment_type == "at":
            parts.append(f"[at:{data.get('qq', '')}]")
        elif segment_type == "json":
            raw = data.get("data", "")
            prompt = ""
            if isinstance(raw, str) and raw:
                try:
                    payload = json.loads(raw)
                    prompt = payload.get("prompt", "") or payload.get("desc", "")
                except (json.JSONDecodeError, ValueError):
                    pass
            parts.append(f"[json:{prompt}]")
        else:
            parts.append(f"[{segment_type}]")
    return "".join(parts).strip()
