"""Pure willingness stage classifier for short-term group interaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

WillingnessStage = Literal["stranger", "acquaint", "familiar", "close", "withdraw"]


@dataclass(frozen=True, slots=True)
class Willingness:
    stage: WillingnessStage
    confidence: float
    reason: str

    def to_state_value(self) -> dict[str, object]:
        return {
            "willingness_stage": self.stage,
            "confidence": self.confidence,
            "reason": self.reason,
        }


def willingness_stage(
    *,
    recent_reply_delay_s: float = 0.0,
    register_consistency: float = 0.0,
    interaction_count: int = 0,
    consecutive_no_reply: int = 0,
) -> Willingness:
    """Return a 5-stage willingness label without persisting anything."""
    delay = max(0.0, float(recent_reply_delay_s))
    consistency = _clamp(register_consistency)
    count = max(0, int(interaction_count))
    silence = max(0, int(consecutive_no_reply))
    if silence >= 3 or (delay >= 300 and count > 0):
        return Willingness("withdraw", 0.82, "repeated silence or long reply delay")
    if count <= 1 and consistency < 0.4:
        return Willingness("stranger", 0.7, "cold start with little register evidence")
    if count >= 20 and consistency >= 0.75 and delay <= 45:
        return Willingness("close", 0.78, "frequent stable interaction")
    if count >= 8 and consistency >= 0.55 and delay <= 120:
        return Willingness("familiar", 0.72, "enough recent interaction and stable register")
    return Willingness("acquaint", 0.6, "weak but usable interaction evidence")


def _clamp(value: float) -> float:
    try:
        number = float(value)
    except Exception:
        return 0.0
    return min(1.0, max(0.0, number))
