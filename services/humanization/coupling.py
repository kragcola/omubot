"""Mood/addressee/topic coupling lookup table."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

ReplyBias = Literal["default", "suppress", "short", "elaborate", "continue_old_topic"]


@dataclass(frozen=True, slots=True)
class CouplingFeatures:
    mood_label: str = "neutral"
    register_label: str = "neutral"
    affection_stage: str = "acquaint"
    addressee_self: bool = False
    topic_drift_score: float = 0.0
    topic_is_new: bool = False


@dataclass(frozen=True, slots=True)
class CouplingPolicy:
    reply_bias: ReplyBias = "default"
    register_label: str | None = None
    max_segments: int | None = None
    typing_multiplier: float = 1.0
    delay_multiplier: float = 1.0
    sticker_probability: float | None = None
    sticker_multiplier: float = 1.0
    reasons: tuple[str, ...] = ()


def lookup_coupling(features: CouplingFeatures) -> CouplingPolicy:
    mood = _norm(features.mood_label)
    register = _norm(features.register_label)
    affection = _norm(features.affection_stage)
    policy = CouplingPolicy(register_label=register or None)

    if mood == "cold" and not features.addressee_self:
        policy = _patch(policy, reply_bias="suppress", sticker_probability=0.1, reason="cold_non_self")
    elif mood == "cold" and features.addressee_self:
        policy = _patch(
            policy,
            reply_bias="short",
            max_segments=1,
            typing_multiplier=1.3,
            sticker_probability=0.1,
            reason="cold_self",
        )
    elif mood == "tired" and features.topic_is_new:
        policy = _patch(policy, reply_bias="continue_old_topic", sticker_probability=0.1, reason="tired_new_topic")
    elif mood == "tired":
        policy = _patch(policy, sticker_probability=0.1, reason="tired_low_sticker")
    elif mood == "playful" and features.topic_drift_score > 0.6:
        policy = _patch(policy, reply_bias="elaborate", reason="playful_topic_drift")

    if mood == "playful" and register == "playful":
        policy = _patch(policy, sticker_probability=0.7, reason="playful_register_sticker")

    if affection == "stranger":
        policy = _patch(policy, register_label="neutral", reason="stranger_neutral")
    elif affection == "close" and features.addressee_self:
        policy = _patch(policy, register_label="playful", sticker_multiplier=1.2, reason="close_self_playful")
    elif affection == "withdraw":
        policy = _patch(
            policy,
            delay_multiplier=1.3,
            sticker_probability=min(policy.sticker_probability or 0.05, 0.05),
            reason="withdraw_slow_low_sticker",
        )
    return policy


def _patch(policy: CouplingPolicy, *, reason: str, **changes: object) -> CouplingPolicy:
    return replace(policy, reasons=(*policy.reasons, reason), **changes)  # type: ignore[arg-type]


def _norm(value: object) -> str:
    return str(value or "").strip().lower()
