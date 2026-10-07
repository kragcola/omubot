"""Pure five-stage advice from complete, caller-authorized observations.

Callers must obtain actual reply timing, register consistency and interaction
counts from their source owners. A successful send, Affection score or Climate
dimension cannot stand in for an absent measurement. This module has no state,
permission grants or participation effects.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

WillingnessStage = Literal["stranger", "acquaint", "familiar", "close", "withdraw"]
WillingnessField = Literal[
    "recent_reply_delay_s", "register_consistency", "interaction_count", "consecutive_no_reply"]


@dataclass(frozen=True, slots=True)
class GroupWindowTurn:
    event_id: str
    role: Literal["user", "assistant"]
    observed_at: float


@dataclass(frozen=True, slots=True)
class WillingnessInputs:
    """None means no current observation; zero must be an actual measurement."""

    recent_reply_delay_s: float | None = None
    register_consistency: float | None = None
    interaction_count: int | None = None
    consecutive_no_reply: int | None = None

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("recent_reply_delay_s", self.recent_reply_delay_s, None),
            ("register_consistency", self.register_consistency, 1.0),
        ):
            if value is None:
                continue
            if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
                    or (maximum is not None and value > maximum)):
                raise ValueError(f"invalid {name}")
        for name, count in (
            ("interaction_count", self.interaction_count),
            ("consecutive_no_reply", self.consecutive_no_reply),
        ):
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError(f"invalid {name}")


@dataclass(frozen=True, slots=True)
class WillingnessRecommendation:
    status: Literal["available", "missing"]
    stage: WillingnessStage | None
    confidence: float | None
    reason: str
    missing_fields: tuple[WillingnessField, ...] = ()


_WILLINGNESS_PHASE_SCORES: dict[WillingnessStage, float] = {
    "stranger": .2,
    "acquaint": .4,
    "familiar": .6,
    "close": .8,
    "withdraw": .1,
}


def group_window_inputs(
    turns: tuple[GroupWindowTurn, ...], *, now: float, register_confidence: float | None,
) -> WillingnessInputs:
    """Build observations from a caller-authorized, ordered group-window snapshot."""
    if not turns:
        return WillingnessInputs()
    if not math.isfinite(now):
        raise ValueError("invalid now")

    event_ids: set[str] = set()
    previous_at = -math.inf
    latest_assistant_at: float | None = None
    for turn in turns:
        if turn.event_id in event_ids:
            raise ValueError("duplicate event_id")
        if not math.isfinite(turn.observed_at) or turn.observed_at > now:
            raise ValueError("invalid observed_at")
        if turn.observed_at < previous_at:
            raise ValueError("turns must be chronological")
        event_ids.add(turn.event_id)
        previous_at = turn.observed_at
        if turn.role == "assistant":
            latest_assistant_at = turn.observed_at

    delay = None if latest_assistant_at is None else now - latest_assistant_at
    no_reply = None if latest_assistant_at is None else sum(
        turn.role == "user" and turn.observed_at > latest_assistant_at for turn in turns)
    return WillingnessInputs(delay, register_confidence, len(turns), no_reply)


def willingness_stage(inputs: WillingnessInputs) -> WillingnessRecommendation:
    """Recommend a stage only when all four observed inputs are present."""
    delay, consistency = inputs.recent_reply_delay_s, inputs.register_consistency
    count, silence = inputs.interaction_count, inputs.consecutive_no_reply
    if delay is None or consistency is None or count is None or silence is None:
        observations: tuple[tuple[WillingnessField, float | int | None], ...] = (
            ("recent_reply_delay_s", delay), ("register_consistency", consistency),
            ("interaction_count", count), ("consecutive_no_reply", silence))
        missing: tuple[WillingnessField, ...] = tuple(name for name, value in observations if value is None)
        return WillingnessRecommendation("missing", None, None, "missing required observations", missing)
    if silence >= 3 or (delay >= 300 and count > 0):
        return WillingnessRecommendation(
            "available", "withdraw", .82, "repeated silence or long reply delay")
    if count <= 1 and consistency < .4:
        return WillingnessRecommendation(
            "available", "stranger", .7, "cold start with little register evidence")
    if count >= 20 and consistency >= .75 and delay <= 45:
        return WillingnessRecommendation(
            "available", "close", .78, "frequent stable interaction")
    if count >= 8 and consistency >= .55 and delay <= 120:
        return WillingnessRecommendation(
            "available", "familiar", .72, "enough recent interaction and stable register")
    return WillingnessRecommendation(
        "available", "acquaint", .6, "weak but usable interaction evidence")


def willingness_phase(recommendation: WillingnessRecommendation) -> float | None:
    """Return the established scheduler phase feature, or None when unavailable."""
    if recommendation.status != "available" or recommendation.stage is None:
        return None
    return _WILLINGNESS_PHASE_SCORES.get(recommendation.stage)


_STAGE_ORDER: tuple[WillingnessStage, ...] = ("withdraw", "stranger", "acquaint", "familiar", "close")
_POSITIVE_OUTCOMES = ("笑", "好", "顺利", "愿意", "回应", "配合", "继续", "喜欢", "开心", "成功")
_NEGATIVE_OUTCOMES = ("冷", "无视", "拒绝", "尴尬", "生气", "沉默", "不回", "失败", "反感", "打断")


def outcome_polarity(text: str) -> Literal["positive", "negative", "neutral"]:
    text = text.strip().lower()
    if any(marker in text for marker in _NEGATIVE_OUTCOMES):
        return "negative"
    return "positive" if any(marker in text for marker in _POSITIVE_OUTCOMES) else "neutral"


def episodic_outcome_ratio(outcomes: tuple[str, ...]) -> float | None:
    labels = tuple(label for text in outcomes if (label := outcome_polarity(text)) != "neutral")
    return sum(label == "positive" for label in labels) / len(labels) if labels else None


def apply_episode_outcomes(
    recommendation: WillingnessRecommendation, outcomes: tuple[str, ...],
) -> WillingnessRecommendation:
    ratio = episodic_outcome_ratio(outcomes)
    if recommendation.stage is None or recommendation.confidence is None or ratio is None:
        return recommendation
    shift = 1 if ratio > .6 else -1 if ratio < .4 else 0
    stage = _STAGE_ORDER[max(0, min(4, _STAGE_ORDER.index(recommendation.stage) + shift))]
    if stage == recommendation.stage:
        return recommendation
    direction = "positive" if shift > 0 else "negative"
    return WillingnessRecommendation("available", stage, min(1, round(recommendation.confidence + .04, 2)),
                                    recommendation.reason + f"; episodic_{direction}_bias")


def _situation_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).strip().lower()
    text = re.sub(r"https?://\S+|\[[^\]]+\]\([^)]+\)", "", text)
    return re.sub(r"[\s`*_~#>\[\](){}《》<>:：,，。.!！?？;；\"'“”‘’|/\\]+", "", text)


def rank_episode_situations(query: str, situations: tuple[str, ...]) -> tuple[int, ...]:
    """Legacy deterministic ngram/meaningful-overlap lookup, selecting at most three."""
    left = _situation_key(query)
    ranked: list[tuple[float, int]] = []
    for index, text in enumerate(situations):
        right = _situation_key(text)
        if not left or not right:
            continue
        if left == right:
            score = 1.0
        elif left in right or right in left:
            score = .82
        else:
            n = 2 if len(left) > 2 and len(right) > 2 else 1
            a = {left[i:i+n] for i in range(max(1, len(left)-n+1))}
            b = {right[i:i+n] for i in range(max(1, len(right)-n+1))}
            score = len(a & b) / len(a | b)
            shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
            if score < .5 and any(shorter[i:i+4] in longer for i in range(len(shorter)-3)):
                score = .65
        if score >= .5:
            ranked.append((score, index))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return tuple(index for _, index in ranked[:3])
