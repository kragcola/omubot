"""Detect and dampen unsolicited schedule oversharing in visible replies."""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.llm.sentinel_registry import (
    GuardrailContext,
    GuardrailHit,
    GuardrailResult,
    register_rule,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])|(?<=\n)")

_DEFAULT_BYPASS_PATTERNS: tuple[str, ...] = (
    "几点",
    "什么时候",
    "日程",
    "安排",
    "忙不忙",
    "在干嘛",
    "在做什么",
    "干啥呢",
)
_DEFAULT_LEAK_PATTERNS: tuple[str, ...] = (
    r"\d{1,2}[：:]\d{2}",
    "上午",
    "下午",
    "晚上",
    "排练",
    "吃饭",
    "休息",
    "上课",
    "午饭",
    "晚饭",
)


@dataclass(frozen=True, slots=True)
class OvershareDetectResult:
    hit: bool
    reason: str = ""
    dampened_text: str = ""
    matched_sentences: tuple[str, ...] = ()


def _pattern_values(config: object | None, field: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
    overshare = getattr(config, "schedule_overshare", config)
    raw = getattr(overshare, field, defaults)
    if not isinstance(raw, list | tuple):
        return defaults
    values = tuple(str(item).strip() for item in raw if str(item).strip())
    return values or defaults


def _compiled_patterns(config: object | None, field: str, defaults: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in _pattern_values(config, field, defaults))


def _contains_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _dampen_text(bot_reply: str, patterns: tuple[re.Pattern[str], ...]) -> tuple[str, tuple[str, ...]]:
    parts = _SENTENCE_SPLIT_RE.split(bot_reply)
    kept: list[str] = []
    removed: list[str] = []
    for part in parts:
        sentence = part.strip()
        if not sentence:
            continue
        if _contains_any(sentence, patterns):
            removed.append(sentence)
            continue
        kept.append(sentence)
    return "".join(kept).strip(), tuple(removed)


def detect(
    bot_reply: str,
    user_message: str,
    *,
    session_count: int = 0,
    cumulative_threshold: int = 2,
    bypass_patterns: tuple[re.Pattern[str], ...] | None = None,
    leak_patterns: tuple[re.Pattern[str], ...] | None = None,
) -> OvershareDetectResult:
    bypass = bypass_patterns or tuple(re.compile(pattern) for pattern in _DEFAULT_BYPASS_PATTERNS)
    leaks = leak_patterns or tuple(re.compile(pattern, re.IGNORECASE) for pattern in _DEFAULT_LEAK_PATTERNS)
    if _contains_any(user_message, bypass):
        return OvershareDetectResult(hit=False)
    if not _contains_any(bot_reply, leaks):
        return OvershareDetectResult(hit=False)
    dampened_text, removed = _dampen_text(bot_reply, leaks)
    reason = (
        "cumulative_threshold"
        if session_count >= max(0, cumulative_threshold)
        else "unsolicited_time_mention"
    )
    return OvershareDetectResult(
        hit=True,
        reason=reason,
        dampened_text=dampened_text,
        matched_sentences=removed,
    )


def _enabled(config: object | None) -> bool:
    overshare = getattr(config, "schedule_overshare", config)
    return bool(getattr(overshare, "enabled", False))


def _cumulative_threshold(config: object | None) -> int:
    overshare = getattr(config, "schedule_overshare", config)
    try:
        return max(0, int(getattr(overshare, "cumulative_threshold", 2)))
    except (TypeError, ValueError):
        return 2


def schedule_overshare_rule(text: str, ctx: GuardrailContext) -> GuardrailResult:
    if not _enabled(ctx.config):
        return GuardrailResult(passed=True, text=text)
    bypass_patterns = _compiled_patterns(ctx.config, "bypass_patterns", _DEFAULT_BYPASS_PATTERNS)
    leak_patterns = _compiled_patterns(ctx.config, "leak_patterns", _DEFAULT_LEAK_PATTERNS)
    result = detect(
        text,
        ctx.user_message,
        session_count=ctx.session_count,
        cumulative_threshold=_cumulative_threshold(ctx.config),
        bypass_patterns=bypass_patterns,
        leak_patterns=leak_patterns,
    )
    if not result.hit:
        return GuardrailResult(passed=True, text=text)
    hit = GuardrailHit(
        name="schedule_overshare",
        severity="low",
        action="rewrite",
        metadata={
            "reason": result.reason,
            "matched_sentences": list(result.matched_sentences),
        },
    )
    return GuardrailResult(
        passed=bool(result.dampened_text.strip()),
        text=result.dampened_text,
        hits=(hit,),
        metadata={
            "schedule_overshare_reason": result.reason,
            "schedule_overshare_dampened_empty": not bool(result.dampened_text.strip()),
        },
    )


register_rule(schedule_overshare_rule)
