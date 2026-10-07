"""Finite plan/utter requests; Conversation owns calls, validity and delivery."""

from __future__ import annotations

import json
import re
from typing import cast

from .types import JsonValue, Message, ModelRequest


def plan_request(base: ModelRequest) -> ModelRequest:
    hint = json.dumps({
        "planned_reply": "plan",
        "instruction": "应用生成计划，不是群成员新发言。若需要已提供的工具，直接选择工具；"
                       "否则只输出JSON：{\"utterances\":[\"第一段要点\",\"第二段要点\"]}，"
                       "列出2到3个简短要点，不输出完整回复。",
    }, ensure_ascii=False)
    return base.model_copy(update={
        "messages": [*base.messages, Message(role="user", content=hint)],
        "max_output_tokens": 80,
    })


def parse_plan(text: str) -> tuple[str, ...] | None:
    try:
        value: JsonValue = cast(JsonValue, json.loads(text))
    except json.JSONDecodeError:
        value = [re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", line).strip()
                 for line in text.splitlines() if line.strip()]
    if isinstance(value, dict):
        value = value.get("utterances")
    if not isinstance(value, list) or not 2 <= len(value) <= 3:
        return None
    outlines: list[str] = []
    for line in value:
        if not isinstance(line, str) or not line.strip() or len(line) > 160:
            return None
        outlines.append(line.strip())
    return tuple(outlines) if len(set(outlines)) == len(outlines) else None


def utter_request(
    base: ModelRequest, outlines: tuple[str, ...], index: int, buffered: tuple[str, ...]
) -> ModelRequest:
    hint = json.dumps({
        "planned_reply": "utter", "ordinal": index + 1, "total": len(outlines),
        "outlines": outlines, "current_outline": outlines[index],
        "buffered_candidates": buffered,
        "instruction": "应用生成形态，不是群成员发言。候选均未发送；只自然地说当前这一段，"
                       "不要重复其他候选，不输出计划或工具。",
    }, ensure_ascii=False)
    return base.model_copy(update={
        "messages": [*base.messages, Message(role="user", content=hint)],
        "tools": [], "max_output_tokens": 150,
    })
