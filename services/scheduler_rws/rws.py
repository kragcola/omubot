"""Reply Worthiness Score.

This module is intentionally pure: the hot scheduler path can compute or
shadow a decision without touching network, storage, or runtime state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from services.scheduler_rws.weights import DEFAULT_RWS_WEIGHTS, RWSWeights


@dataclass(frozen=True, slots=True)
class RWSFeatures:
    mode: str = "none"
    addressee_self: bool = True
    old_threshold: float = 0.0
    mood_mult: float = 1.0
    mood_trend: float | None = None
    time_mult: float = 1.0
    consecutive_skip: int = 0
    force_threshold: int = 5
    double_threshold: int = 3
    hawkes_rho: float = 0.0
    eot_probability: float = 0.5
    outcome_ratio: float = 0.5
    familiarity: float = 0.0
    willingness_phase: float = 0.5
    info_gain: float = 0.0


@dataclass(frozen=True, slots=True)
class RWSExplanation:
    terms: dict[str, float]
    logit: float
    score: float
    theta: float
    decision: bool
    old_threshold: float
    old_decision: bool | None = None
    # P5 dual-threshold (Inner Thoughts style): the combined ``score`` above is
    # kept for rollback/observability, but the decision can also be split into
    # "do I have something worth saying" (im) vs "is now an OK moment / will I
    # interrupt" (interrupt). Both are always computed (cheap); the scheduler
    # decides whether to gate on them (``rws_dual_threshold`` flag).
    im_score: float = 0.0
    interrupt_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "terms": dict(self.terms),
            "logit": round(self.logit, 4),
            "score": round(self.score, 4),
            "theta": round(self.theta, 4),
            "decision": self.decision,
            "old_threshold": round(self.old_threshold, 4),
            "old_decision": self.old_decision,
            "im_score": round(self.im_score, 4),
            "interrupt_score": round(self.interrupt_score, 4),
        }


# P5: which terms describe *timing / appropriateness of interrupting* (vs.
# *intent / worth of speaking*). eot ≈ turn is ending (good moment), hawkes ≈
# group is busy (bad moment, negative weight), skip_pressure ≈ been quiet (ok to
# speak), schedule_residual ≈ time-of-day activity. Everything else (mode,
# addressee, relationship, info, baseline probability) feeds the intent score.
_INTERRUPT_TERMS: frozenset[str] = frozenset(
    {"eot", "hawkes", "skip_pressure", "schedule_residual"},
)


def dual_decision(
    explanation: RWSExplanation,
    *,
    im_threshold: float,
    interrupt_threshold: float,
) -> bool:
    """P5 fire test: speak only when BOTH gates pass — there is something worth
    saying (im) AND now is an acceptable moment (interrupt). The interrupt gate
    is set higher for proactive interjection than for an addressed reply, so the
    bot stays quiet in a busy/ongoing turn even when it has something to add."""
    return (
        explanation.im_score >= im_threshold
        and explanation.interrupt_score >= interrupt_threshold
    )


def compute_rws(
    features: RWSFeatures,
    *,
    weights: RWSWeights = DEFAULT_RWS_WEIGHTS,
    theta: float = 0.5,
) -> RWSExplanation:
    theta = _clamp(theta)
    mode = features.mode
    skip_denominator = max(1, int(features.force_threshold or 1))
    skip_pressure = _clamp(features.consecutive_skip / skip_denominator)
    mood_residual = (
        _clamp((features.mood_trend - 0.5) * 2.0, low=-1.0, high=1.0)
        if features.mood_trend is not None
        else _clamp(features.mood_mult - 1.0, low=-1.0, high=1.0)
    )
    schedule_residual = _clamp(features.time_mult - 1.0, low=-1.0, high=1.0)
    old_probability = _clamp(features.old_threshold)
    outcome_residual = (_clamp(features.outcome_ratio) - 0.5) * 2.0
    willingness_residual = (_clamp(features.willingness_phase) - 0.5) * 2.0

    terms = {
        "bias": weights.bias,
        "old_threshold": _logit(old_probability),
        "at": weights.at if mode == "at_mention" and features.addressee_self else 0.0,
        "directed_followup": weights.directed_followup if mode == "directed_followup" else 0.0,
        "video_always": weights.video_always if mode == "video_always" else 0.0,
        "qq_interaction": weights.qq_interaction if mode == "qq_interaction" else 0.0,
        "addressee": weights.addressee if features.addressee_self else -weights.addressee,
        "eot": weights.eot * (_clamp(features.eot_probability) - 0.5) * 2.0,
        "outcome": weights.outcome * outcome_residual,
        "familiarity": weights.familiarity * _clamp(features.familiarity),
        "willingness": weights.willingness * willingness_residual,
        "info_gain": weights.info_gain * _clamp(features.info_gain),
        "hawkes": -weights.hawkes * _clamp(features.hawkes_rho),
        "skip_pressure": weights.skip_pressure * skip_pressure,
        "mood_residual": weights.mood_residual * mood_residual,
        "schedule_residual": weights.schedule_residual * schedule_residual,
    }
    logit = sum(terms.values())
    score = _sigmoid(logit)
    # P5: partition the same terms into intent (im) vs timing (interrupt) logits.
    # The bias is shared (baseline propensity affects both). interrupt_logit gets
    # a fixed +bias so a neutral moment (no eot/hawkes signal) scores ~0.5 rather
    # than collapsing to 0 when those features are off.
    interrupt_logit = sum(v for k, v in terms.items() if k in _INTERRUPT_TERMS)
    im_logit = logit - interrupt_logit
    return RWSExplanation(
        terms=terms,
        logit=logit,
        score=score,
        theta=theta,
        decision=score >= theta,
        old_threshold=_clamp(features.old_threshold),
        im_score=_sigmoid(im_logit),
        interrupt_score=_sigmoid(interrupt_logit),
    )


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _logit(value: float) -> float:
    clamped = max(0.001, min(0.999, value))
    return math.log(clamped / (1.0 - clamped))


def _clamp(value: object, *, low: float = 0.0, high: float = 1.0) -> float:
    try:
        raw = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raw = low
    return max(low, min(high, raw))
