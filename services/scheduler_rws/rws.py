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
    time_mult: float = 1.0
    consecutive_skip: int = 0
    force_threshold: int = 5
    double_threshold: int = 3
    hawkes_rho: float = 0.0
    eot_probability: float = 0.5
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "terms": dict(self.terms),
            "logit": round(self.logit, 4),
            "score": round(self.score, 4),
            "theta": round(self.theta, 4),
            "decision": self.decision,
            "old_threshold": round(self.old_threshold, 4),
            "old_decision": self.old_decision,
        }


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
    mood_residual = _clamp(features.mood_mult - 1.0, low=-1.0, high=1.0)
    schedule_residual = _clamp(features.time_mult - 1.0, low=-1.0, high=1.0)
    old_probability = _clamp(features.old_threshold)

    terms = {
        "bias": weights.bias,
        "old_threshold": _logit(old_probability),
        "at": weights.at if mode == "at_mention" and features.addressee_self else 0.0,
        "directed_followup": weights.directed_followup if mode == "directed_followup" else 0.0,
        "video_always": weights.video_always if mode == "video_always" else 0.0,
        "addressee": weights.addressee if features.addressee_self else -weights.addressee,
        "eot": weights.eot * (_clamp(features.eot_probability) - 0.5) * 2.0,
        "info_gain": weights.info_gain * _clamp(features.info_gain),
        "hawkes": -weights.hawkes * _clamp(features.hawkes_rho),
        "skip_pressure": weights.skip_pressure * skip_pressure,
        "mood_residual": weights.mood_residual * mood_residual,
        "schedule_residual": weights.schedule_residual * schedule_residual,
    }
    logit = sum(terms.values())
    score = _sigmoid(logit)
    return RWSExplanation(
        terms=terms,
        logit=logit,
        score=score,
        theta=theta,
        decision=score >= theta,
        old_threshold=_clamp(features.old_threshold),
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
