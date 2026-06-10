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
_MAX_CANDIDATES = 10
_DEFAULT_COOLDOWN_MS = 45_000
_MOOD_TTL_S = 300
_FEEDBACK_STICKER_DENSITY_CAP = 0.3
_DEFAULT_MOOD_ENERGY = 0.6
_DEFAULT_MOOD_VALENCE = 0.0

# Mood two-axis (2026-06-10): valence picks *which class* of sticker (handled in
# client._select_post_reply_sticker), energy scales *probability* (here). Neither
# axis hard-blocks — "难过/累 ≠ 不发". The only near-block is affection=withdraw,
# which comes from the *relationship*, not mood.
_BASE_FREQUENCY_MULT = {"rarely": 0.5, "normal": 1.0, "frequently": 1.4, "off": 0.0}
# D1=(a): thinker sticker:false demotes the post-reply path, it does not veto it.
_THINKER_FALSE_MULT = 0.6


@dataclass(frozen=True, slots=True)
class StickerDecisionContext:
    register_label: str = "neutral"
    mood_label: str = "neutral"  # retained for observability/logging only
    mood_energy: float = _DEFAULT_MOOD_ENERGY  # [0,1] satiety axis → probability scale
    mood_valence: float = _DEFAULT_MOOD_VALENCE  # [-1,1] pleasure axis → class only
    affection_stage: str = "acquaint"
    base_frequency: str = "normal"  # web-configurable baseline: rarely/normal/frequently/off
    thinker_suggested: bool = True  # thinker.sticker decision; False demotes (×0.6), never vetoes
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
        if context.affection_stage == "withdraw":
            # Only near-block left, and it comes from the relationship, not mood.
            return _decision(False, pool, strategy, context, source, probability, "affection_withdraw_gate")
        if source == "thinker" and probability < 0.5:
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


def _mood_energy_multiplier(energy: float) -> float:
    """Low energy → 话少 → fewer stickers, but never zero ("累 ≠ 不发").

    energy 1.0 → ×1.0; 0.5 → ×0.75; 0.0 → ×0.5. Linear, floor 0.5.
    """
    return 0.5 + 0.5 * max(0.0, min(1.0, energy))


def _is_playful(context: StickerDecisionContext) -> bool:
    """Numeric replacement for the old (Chinese-mismatched) _PLAYFUL_MOODS set."""
    return context.mood_energy >= 0.7 and context.mood_valence >= 0.4


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
    # web-configurable baseline frequency (rarely/normal/frequently/off)
    base *= _BASE_FREQUENCY_MULT.get(context.base_frequency, 1.0)
    # energy axis: tired → scale down (multiplier, never to zero)
    base *= _mood_energy_multiplier(context.mood_energy)
    # thinker:false demotes the post-reply path rather than vetoing it (D1=a)
    if not context.thinker_suggested:
        base *= _THINKER_FALSE_MULT
    # affection axis: intimacy amplifies, strangers contract, withdraw near-mutes
    affection = context.affection_stage
    if affection == "close":
        base = min(0.95, base + 0.1)
    elif affection == "stranger":
        base = max(0.0, base - 0.15)
    elif affection == "withdraw":
        base = min(base, 0.05)
    # kaomoji outside a playful register/mood stays suppressed
    if source == "kaomoji" and context.register_label != "playful" and not _is_playful(context):
        base = min(base, 0.2)
    return max(0.0, min(1.0, base))


def _rerank_strategy(
    context: StickerDecisionContext,
    source: StickerTrigger,
    probability: float,
) -> StickerRerankStrategy:
    if probability <= 0:
        return "none"
    if context.affection_stage == "close":
        return "persona"
    # emotion-driven rerank when the mood is strongly polarised on either axis
    if _is_playful(context) or context.mood_valence <= -0.3:
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
