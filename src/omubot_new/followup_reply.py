"""Pure bounded legacy D decision over the current turn's resolved sources."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .types import Message, ModelRequest


@dataclass(frozen=True)
class FollowupDecision:
    should_extend: bool
    wait_seconds: float
    reasons: tuple[str, ...]
    unsupported: tuple[str, ...] = ()


def decide_followup(
    text: str, register: str | None, *, energy: float | None = None, heat: float | None = None,
) -> FollowupDecision:
    text = " ".join(text.split())
    label = {"affectionate": "playful", "distant": "polite_distant", "serious": "polite_distant"}.get(
        register or "", register
    )
    wait = 2.55 if label in {"quiet", "polite_distant"} else 2.0 if label in {"playful", "snark"} else 2.2
    unsupported = tuple(name for name, value in (("energy", energy), ("heat", heat)) if value is None)
    if energy is not None:
        wait += 0.25 if energy < 0.35 else -0.20 if energy > 0.75 else 0.0
    if heat is not None:
        wait += -0.55 if heat > 0.75 else 0.20 if heat < 0.25 else 0.0
    wait = round(max(1.2, min(3.0, wait)), 3)
    if not 8 <= len(text) <= 360:
        return FollowupDecision(False, wait, ("surface_length",), unsupported)
    score = 0.0
    reasons: list[str] = []
    if re.search(r"[?？]\s*$", text):
        score -= 0.45
        reasons.append("asks_user")
    stack: list[str] = []
    pairs = {"(": ")", "（": "）", "[": "]", "【": "】", "《": "》"}
    for char in text:
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
    if re.search(r"[,，;；:：、]\s*$", text) or stack:
        score += 0.42
        reasons.append("open_tail")
    if re.search(r"还有|另外|不过|但是|然后|顺便|补一句|等下|先别急|话说", text):
        score += 0.24
        reasons.append("continuation_cue")
    if text[-1] not in set("。.!！~～…)]）】》\"'"):
        score += 0.18
        reasons.append("unfinished_surface")
    elif not reasons:
        score -= 0.10
        reasons.append("closed_surface")
    if label in {"quiet", "polite_distant"}:
        score -= 0.20
        reasons.append("register_" + label)
    elif label in {"playful", "snark"}:
        score += 0.08
        reasons.append("register_" + label)
    if energy is not None:
        if energy < 0.35:
            score -= 0.18
            reasons.append("low_slot_energy")
        elif energy > 0.75:
            score += 0.04
            reasons.append("high_slot_energy")
    if heat is not None:
        if heat > 0.85:
            score -= 0.08
            reasons.append("hot_group")
        elif heat < 0.25:
            score += 0.06
            reasons.append("quiet_group")
    return FollowupDecision(score >= 0.55, wait, tuple(reasons), unsupported)


def followup_request(base: ModelRequest, visible: list[Message], ordinal: int) -> ModelRequest:
    return base.model_copy(
        update={
            "messages": [
                *base.messages,
                *visible,
                Message(
                    role="user",
                    content=(
                        f"追评第{ordinal}次：仅在已有回复的基础上补充一句自然的话，不重复、不提问、不使用工具。"
                    ),
                ),
            ],
            "tools": [],
            "current_images": (),
            "previous_tool": None,
            "tool_result": None,
            "continuation": [],
            "max_output_tokens": 128,
        }
    )
