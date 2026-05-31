"""Shared parser for QQ mini-program JSON cards (B站视频卡片等).

A QQ mini-program share arrives as a ``[json:data={...}]`` segment whose
``extract_plain_text()`` is empty — the human-readable title/desc live inside
the JSON. Both the bilibili plugin (to detect shared videos) and the reply
renderer (so a quoted video card has actual content to respond to, not an
empty ``[QUOTED_MSG]`` shell — see F-γ in
docs/tracking/fix-prob-fire-stale-topic-sticker-2026-05-30.md §19) need this.
"""

from __future__ import annotations

import json


def extract_json_card_text(raw: str) -> str:
    """Extract low-noise human-readable fields from a QQ mini-program JSON card.

    Returns ``prompt`` + ``meta.detail_1.title``/``desc`` joined and de-duped,
    or ``""`` when the input is not a parseable card.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""

    parts: list[str] = []
    prompt = data.get("prompt", "")
    if isinstance(prompt, str) and prompt.strip():
        parts.append(prompt.strip())

    meta = data.get("meta", {})
    if isinstance(meta, dict):
        detail = meta.get("detail_1", {})
        if isinstance(detail, dict):
            for key in ("title", "desc"):
                value = detail.get(key, "")
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())

    deduped: list[str] = []
    for part in parts:
        if part not in deduped:
            deduped.append(part)
    return " ".join(deduped)
