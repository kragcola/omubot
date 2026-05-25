"""Single decision point for sticker send intent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

StickerTrigger = Literal["none", "tool_call", "kaomoji", "frequent", "thinker"]
StickerRerankStrategy = Literal["none", "emotion", "intent", "persona"]
_COLD_MOODS = {"cold", "tired"}
_PLAYFUL_MOODS = {"playful", "high"}
_MAX_CANDIDATES = 10
_DEFAULT_COOLDOWN_MS = 45_000


@dataclass(frozen=True, slots=True)
class StickerDecisionContext:
    register_label: str = "neutral"
    mood_label: str = "neutral"
    affection_stage: str = "acquaint"
    cooldown_active: bool = False
    cooldown_ms: int = _DEFAULT_COOLDOWN_MS
    frequent_candidates: Sequence[str] = field(default_factory=tuple)
    kaomoji_candidates: Sequence[str] = field(default_factory=tuple)
    thinker_candidates: Sequence[str] = field(default_factory=tuple)
    tool_call_candidates: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class StickerDecision:
    should_send: bool
    candidate_pool: tuple[str, ...] = ()
    rerank_strategy: StickerRerankStrategy = "none"
    cooldown_ms: int = _DEFAULT_COOLDOWN_MS
    trigger_source: StickerTrigger = "none"
    send_probability: float = 0.0
    reason: str = ""


class StickerDecisionProvider:
    async def decide(
        self,
        context: StickerDecisionContext,
        *,
        extra_candidates: Callable[[], Awaitable[Sequence[str]]] | None = None,
    ) -> StickerDecision:
        extras = tuple(await extra_candidates()) if extra_candidates is not None else ()
        pool = _dedupe([
            *context.tool_call_candidates,
            *context.kaomoji_candidates,
            *context.frequent_candidates,
            *context.thinker_candidates,
            *extras,
        ])
        source = _trigger_source(context)
        probability = _send_probability(context, source, bool(pool))
        strategy = _rerank_strategy(context, source, probability)
        if context.cooldown_active:
            return _decision(False, pool, strategy, context, source, probability, "cooldown_active")
        if not pool:
            return _decision(False, pool, "none", context, "none", 0.0, "no_candidates")
        if _blocked_by_mood(context):
            return _decision(False, pool, strategy, context, source, probability, "mood_or_affection_gate")
        if source == "thinker" and probability < 0.7:
            return _decision(False, pool, strategy, context, source, probability, "thinker_hint_only")
        return _decision(probability >= 0.5, pool, strategy, context, source, probability, "single_decision")


def _decision(
    should_send: bool,
    pool: tuple[str, ...],
    strategy: StickerRerankStrategy,
    context: StickerDecisionContext,
    source: StickerTrigger,
    probability: float,
    reason: str,
) -> StickerDecision:
    return StickerDecision(
        should_send=should_send,
        candidate_pool=pool[:_MAX_CANDIDATES],
        rerank_strategy=strategy if should_send else "none",
        cooldown_ms=max(0, int(context.cooldown_ms)),
        trigger_source=source if pool else "none",
        send_probability=round(max(0.0, min(1.0, probability)), 3),
        reason=reason,
    )


def _trigger_source(context: StickerDecisionContext) -> StickerTrigger:
    if context.tool_call_candidates:
        return "tool_call"
    if context.kaomoji_candidates:
        return "kaomoji"
    if context.frequent_candidates:
        return "frequent"
    if context.thinker_candidates:
        return "thinker"
    return "none"


def _send_probability(context: StickerDecisionContext, source: StickerTrigger, has_pool: bool) -> float:
    if not has_pool:
        return 0.0
    base = {
        "tool_call": 0.85,
        "kaomoji": 0.65,
        "frequent": 0.7,
        "thinker": 0.45,
        "none": 0.0,
    }[source]
    mood = context.mood_label
    affection = context.affection_stage
    if mood in _PLAYFUL_MOODS:
        base = max(base, 0.7)
    if mood in _COLD_MOODS:
        base = min(base, 0.1)
    if affection == "close":
        base = min(0.95, base + 0.1)
    elif affection == "stranger":
        base = max(0.0, base - 0.15)
    elif affection == "withdraw":
        base = min(base, 0.05)
    if source == "kaomoji" and context.register_label != "playful" and mood not in _PLAYFUL_MOODS:
        base = min(base, 0.2)
    return base


def _blocked_by_mood(context: StickerDecisionContext) -> bool:
    return context.mood_label in _COLD_MOODS or context.affection_stage == "withdraw"


def _rerank_strategy(
    context: StickerDecisionContext,
    source: StickerTrigger,
    probability: float,
) -> StickerRerankStrategy:
    if probability <= 0:
        return "none"
    if context.affection_stage == "close":
        return "persona"
    if context.mood_label in _PLAYFUL_MOODS or context.mood_label in _COLD_MOODS:
        return "emotion"
    if source in {"tool_call", "kaomoji"}:
        return "intent"
    return "emotion" if source == "thinker" else "intent"


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        sticker_id = str(value).strip()
        if not sticker_id or sticker_id in seen:
            continue
        seen.add(sticker_id)
        out.append(sticker_id)
        if len(out) >= _MAX_CANDIDATES:
            break
    return tuple(out)
