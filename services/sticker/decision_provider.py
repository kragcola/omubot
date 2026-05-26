"""Single decision point for sticker send intent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from services.humanization import MOOD_CURRENT_SLOT, humanization_source
from services.sticker.fairmatch import fairmatch_rerank
from services.system_module import RuntimeStateBus, Scope

StickerTrigger = Literal["none", "tool_call", "kaomoji", "frequent", "thinker"]
StickerRerankStrategy = Literal["none", "emotion", "intent", "persona"]
_COLD_MOODS = {"cold", "tired"}
_PLAYFUL_MOODS = {"playful", "high"}
_MAX_CANDIDATES = 10
_DEFAULT_COOLDOWN_MS = 45_000
_MOOD_TTL_S = 300
_FEEDBACK_STICKER_DENSITY_CAP = 0.3


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
        runtime_state: RuntimeStateBus | None = None,
        scope: Scope | None = None,
        usage_counts: Mapping[str, int] | None = None,
    ) -> StickerDecision:
        extras = tuple(await extra_candidates()) if extra_candidates is not None else ()
        pool = fairmatch_rerank(_dedupe([
            *context.tool_call_candidates,
            *context.kaomoji_candidates,
            *context.frequent_candidates,
            *context.thinker_candidates,
            *extras,
        ]), usage_counts)
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
        decision = _decision(probability >= 0.5, pool, strategy, context, source, probability, "single_decision")
        if decision.should_send:
            _write_density_feedback(runtime_state, scope)
        return decision


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


def _write_density_feedback(runtime_state: RuntimeStateBus | None, scope: Scope | None) -> None:
    if runtime_state is None or scope is None:
        return
    snapshot = runtime_state.get(MOOD_CURRENT_SLOT, scope=scope)
    value = dict(snapshot.value) if snapshot is not None and isinstance(snapshot.value, dict) else {}
    signals = dict(value.get("signals") or {})
    signals["feedback_sticker_density"] = _FEEDBACK_STICKER_DENSITY_CAP
    value["signals"] = signals
    value.setdefault("label", "neutral")
    value.setdefault("confidence", 0.0)
    value.setdefault("reason", "sticker_density_feedback")
    value.setdefault("ttl_s", _MOOD_TTL_S)
    try:
        confidence = max(0.0, min(1.0, float(value.get("confidence", 0.0) or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    decay_at = snapshot.decay_at if snapshot is not None else None
    try:
        runtime_state.set(
            MOOD_CURRENT_SLOT,
            value,
            scope=scope,
            source=humanization_source("sticker_decision_provider:feedback"),
            confidence=confidence,
            decay_at=decay_at or (datetime.now() + timedelta(seconds=_MOOD_TTL_S)),
        )
    except Exception:
        return
