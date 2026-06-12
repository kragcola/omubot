"""Single decision point for sticker send intent."""

from __future__ import annotations

import math
import random as _random
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
# client._select_post_reply_sticker), energy feeds the send score (here). Neither
# axis hard-blocks — "难过/累 ≠ 不发". The only near-block is affection=withdraw,
# which comes from the *relationship*, not mood.
# thinker-led (2026-06-10): for the post-reply *fallback* paths (frequent/thinker),
# the thinker's own sticker:false is a VETO — it owns "whether to decorate this
# reply". This restores LLM autonomy after the old ×0.8 demote made every reply
# carry a sticker. The veto never touches an explicit send_sticker tool_call or a
# kaomoji-enforced round; those are not the thinker's call to make.
_VETOABLE_SOURCES = frozenset({"frequent", "thinker"})
# Deterministic score (2026-06-12): replaces the Bernoulli probability gate that
# made even "thinker wants a sticker" only ~51% likely to send (two串联 gates
# multiplied to ~28% end-to-end). The score is a logit-linear weighting (mirrors
# scheduler RWS `compute_rws`), passed through a sigmoid, then a deterministic
# `score >= threshold` test — same inputs, same output, fully explainable.
_DEFAULT_SCORE_THRESHOLD = 0.5
# §3.5b narrow-band softening: keep a little human jitter ONLY in the "拿不准"
# band around the threshold; outside it the decision is deterministic. This drops
# the骰子-as-主导 of the old gate while preserving same-context变化 near the edge.
_SCORE_SOFT_BAND = 0.1

# Logit weights per feature (草案; calibrated via monte-carlo, see migration doc).
_W_BIAS = 0.0  # overall propensity knob
_W_THINKER_WANTS = 2.5  # thinker ran + suggested → dominant positive
_W_THINKER_ABSENT = 0.3  # thinker had no opinion (force_reply/@) → weak baseline
_W_SOURCE_TOOL_CALL = 3.0  # explicit send_sticker tool_call → near-certain
_W_SOURCE_KAOMOJI = 1.5  # kaomoji-enforce intent
_W_VALENCE_PLAYFUL = 0.6  # happy + energetic → wants decoration
_W_VALENCE_NEGATIVE = 0.2  # 难过≠不发: empathetic sticker, mild positive
_W_ENERGY = 0.4  # high energy lifts, low dips — never zeroes
_W_AFFECTION_CLOSE = 0.5  # intimacy → more casual stickers
_W_AFFECTION_STRANGER = -0.7  # strangers → contract
# base_frequency (web-configurable) as a logit offset rather than a multiplier.
_BASE_FREQUENCY_LOGIT = {"rarely": -0.7, "normal": 0.0, "frequently": 0.7, "off": 0.0}


@dataclass(frozen=True, slots=True)
class StickerDecisionContext:
    register_label: str = "neutral"
    mood_label: str = "neutral"  # retained for observability/logging only
    mood_energy: float = _DEFAULT_MOOD_ENERGY  # [0,1] satiety axis → probability scale
    mood_valence: float = _DEFAULT_MOOD_VALENCE  # [-1,1] pleasure axis → class only
    affection_stage: str = "acquaint"
    base_frequency: str = "normal"  # web-configurable baseline: rarely/normal/frequently/off
    # thinker-led semantics: thinker_ran distinguishes "thinker had no opinion"
    # (force_reply / thinker disabled — must NOT veto) from "thinker ran and said
    # no" (thinker_ran=True, thinker_suggested=False → veto the fallback paths).
    thinker_ran: bool = False
    thinker_suggested: bool = True  # thinker.sticker decision; only meaningful if thinker_ran
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
        threshold: float = _DEFAULT_SCORE_THRESHOLD,
        rng: Callable[[], float] | None = None,
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
        score = compute_sticker_score(context, source) if pool else 0.0
        strategy = _rerank_strategy(context, source, score)
        if context.cooldown_active:
            return _decision(False, pool, strategy, context, source, score, "cooldown_active")
        if not pool:
            return _decision(False, pool, "none", context, "none", 0.0, "no_candidates")
        if context.affection_stage == "withdraw":
            # Only near-block left, and it comes from the relationship, not mood.
            return _decision(False, pool, strategy, context, source, score, "affection_withdraw_gate")
        # thinker-led veto: if the thinker ran and decided this reply shouldn't be
        # decorated, the fallback paths (frequent/thinker) obey it. An explicit
        # send_sticker tool_call or a kaomoji-enforced round are not vetoable.
        if context.thinker_ran and not context.thinker_suggested and source in _VETOABLE_SOURCES:
            return _decision(False, pool, strategy, context, source, score, "thinker_veto")
        # Deterministic score gate (replaces Bernoulli). §3.5b: keep a little
        # jitter ONLY inside the soft band around the threshold; outside it the
        # decision is fixed (same context → same outcome).
        theta = max(0.0, min(1.0, float(threshold)))
        if abs(score - theta) <= _SCORE_SOFT_BAND:
            draw = (rng or _random.random)()
            should_send = draw < _band_send_fraction(score, theta)
        else:
            should_send = score >= theta
        decision = _decision(
            should_send, pool, strategy, context, source, score,
            "score_send" if should_send else "score_skip",
        )
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


def _is_playful(context: StickerDecisionContext) -> bool:
    """Numeric replacement for the old (Chinese-mismatched) _PLAYFUL_MOODS set."""
    return context.mood_energy >= 0.7 and context.mood_valence >= 0.4


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _band_send_fraction(score: float, theta: float) -> float:
    """§3.5b: inside the soft band the send fraction ramps linearly from 0 at
    (theta - band) to 1 at (theta + band), so the jitter is centred on the
    threshold rather than a flat coin-flip. Outside the band callers don't use
    this (the decision is deterministic)."""
    lo = theta - _SCORE_SOFT_BAND
    span = max(1e-6, 2.0 * _SCORE_SOFT_BAND)
    return max(0.0, min(1.0, (score - lo) / span))


def compute_sticker_score(context: StickerDecisionContext, source: StickerTrigger) -> float:
    """Logit-linear sticker propensity → sigmoid → [0,1] score (mirrors the
    scheduler RWS `compute_rws` pattern). Replaces the old multiplicative
    Bernoulli rate: signals are *added* in logit space and the decision is a
    deterministic `score >= threshold` test, so the same context always yields
    the same score — explainable and tunable via weights, not magic products."""
    if source == "none":
        return 0.0
    logit = _W_BIAS
    logit += _BASE_FREQUENCY_LOGIT.get(context.base_frequency, 0.0)

    if source == "tool_call":
        logit += _W_SOURCE_TOOL_CALL
    elif source == "kaomoji":
        logit += _W_SOURCE_KAOMOJI
        # kaomoji outside a playful register/mood stays suppressed
        if context.register_label != "playful" and not _is_playful(context):
            logit -= 1.5
    else:  # frequent / thinker fallback paths — thinker-led
        # ran + suggested here (ran + False is vetoed before scoring); thinker
        # absent (force_reply / disabled) → modest baseline.
        logit += _W_THINKER_WANTS if context.thinker_ran else _W_THINKER_ABSENT

    # mood valence: 难过≠不发 (empathetic), 开心活泼 wants decoration
    if _is_playful(context):
        logit += _W_VALENCE_PLAYFUL
    elif context.mood_valence <= -0.4:
        logit += _W_VALENCE_NEGATIVE
    # energy axis: high lifts, low dips — symmetric around 0.5, never zeroes
    logit += _W_ENERGY * ((max(0.0, min(1.0, context.mood_energy)) - 0.5) * 2.0)
    # affection axis: intimacy amplifies, strangers contract
    if context.affection_stage == "close":
        logit += _W_AFFECTION_CLOSE
    elif context.affection_stage == "stranger":
        logit += _W_AFFECTION_STRANGER

    return _sigmoid(logit)


def _rerank_strategy(
    context: StickerDecisionContext,
    source: StickerTrigger,
    score: float,
) -> StickerRerankStrategy:
    if score <= 0:
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
