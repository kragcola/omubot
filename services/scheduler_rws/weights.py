"""Weights for the Reply Worthiness Score scheduler layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RWSWeights:
    at: float = 6.0
    directed_followup: float = 6.0
    video_always: float = 6.0
    addressee: float = 0.0
    eot: float = 1.0
    info_gain: float = 0.0
    hawkes: float = 1.3
    skip_pressure: float = 0.0
    mood_residual: float = 0.0
    schedule_residual: float = 0.0
    bias: float = 0.0


DEFAULT_RWS_WEIGHTS = RWSWeights()
