"""Pure willingness stage classifier for short-term group interaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from services.similarity import NgramSimilarityProvider, normalize_text_key

WillingnessStage = Literal["stranger", "acquaint", "familiar", "close", "withdraw"]
_STAGE_ORDER: tuple[WillingnessStage, ...] = ("withdraw", "stranger", "acquaint", "familiar", "close")
_POSITIVE_MARKERS: tuple[str, ...] = ("笑", "好", "顺利", "愿意", "回应", "配合", "继续", "喜欢", "开心", "成功")
_NEGATIVE_MARKERS: tuple[str, ...] = ("冷", "无视", "拒绝", "尴尬", "生气", "沉默", "不回", "失败", "反感", "打断")


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
    recent_outcomes: list[str] | None = None,
) -> Willingness:
    """Return a 5-stage willingness label without persisting anything."""
    delay = max(0.0, float(recent_reply_delay_s))
    consistency = _clamp(register_consistency)
    count = max(0, int(interaction_count))
    silence = max(0, int(consecutive_no_reply))
    if silence >= 3 or (delay >= 300 and count > 0):
        base = Willingness("withdraw", 0.82, "repeated silence or long reply delay")
    elif count <= 1 and consistency < 0.4:
        base = Willingness("stranger", 0.7, "cold start with little register evidence")
    elif count >= 20 and consistency >= 0.75 and delay <= 45:
        base = Willingness("close", 0.78, "frequent stable interaction")
    elif count >= 8 and consistency >= 0.55 and delay <= 120:
        base = Willingness("familiar", 0.72, "enough recent interaction and stable register")
    else:
        base = Willingness("acquaint", 0.6, "weak but usable interaction evidence")
    shift = _episodic_outcome_shift(recent_outcomes or [])
    if shift == 0:
        return base
    shifted_stage = _shift_stage(base.stage, shift)
    if shifted_stage == base.stage:
        return base
    direction = "positive" if shift > 0 else "negative"
    return Willingness(
        shifted_stage,
        min(1.0, round(base.confidence + 0.04, 2)),
        f"{base.reason}; episodic_{direction}_bias",
    )


async def episodic_situation_lookup(
    episode_store: Any,
    group_id: str,
    situation_text: str,
    *,
    limit: int = 3,
    window: int = 50,
    similarity_threshold: float = 0.5,
) -> list[Any]:
    """Find similar recent episodes with non-empty outcomes for willingness biasing."""
    if episode_store is None or not group_id or not str(situation_text or "").strip():
        return []
    list_for_recall = getattr(episode_store, "list_for_recall", None)
    if not callable(list_for_recall):
        return []
    episodes = await list_for_recall(
        group_id=str(group_id),
        limit=max(int(limit or 0), int(window or 0), 3),
        include_decayed=True,
    )
    similarity = NgramSimilarityProvider()
    ranked: list[tuple[float, Any]] = []
    for episode in episodes:
        outcome = str(getattr(episode, "outcome_signal", "") or "").strip()
        if not outcome:
            continue
        situation = str(getattr(episode, "situation", "") or "").strip()
        observed = str(getattr(episode, "observed_context", "") or "").strip()
        candidate_text = " ".join(part for part in (situation, observed) if part)
        score = similarity.similarity(str(situation_text), candidate_text)
        if score < similarity_threshold and _has_meaningful_overlap(str(situation_text), candidate_text):
            score = 0.65
        if score < similarity_threshold:
            continue
        ranked.append((score, episode))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [episode for _, episode in ranked[: max(1, int(limit or 3))]]


def _clamp(value: float) -> float:
    try:
        number = float(value)
    except Exception:
        return 0.0
    return min(1.0, max(0.0, number))


def _episodic_outcome_shift(outcomes: list[str]) -> int:
    labeled = [_outcome_polarity(text) for text in outcomes if str(text or "").strip()]
    labeled = [label for label in labeled if label != "neutral"]
    if not labeled:
        return 0
    positive = sum(1 for label in labeled if label == "positive")
    negative = sum(1 for label in labeled if label == "negative")
    total = len(labeled)
    if total <= 0:
        return 0
    if negative / total > 0.6:
        return -1
    if positive / total > 0.6:
        return 1
    return 0


def _outcome_polarity(text: str) -> Literal["positive", "negative", "neutral"]:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return "neutral"
    if any(marker in normalized for marker in _NEGATIVE_MARKERS):
        return "negative"
    if any(marker in normalized for marker in _POSITIVE_MARKERS):
        return "positive"
    return "neutral"


def _shift_stage(stage: WillingnessStage, shift: int) -> WillingnessStage:
    try:
        index = _STAGE_ORDER.index(stage)
    except ValueError:
        return stage
    shifted = max(0, min(len(_STAGE_ORDER) - 1, index + shift))
    return _STAGE_ORDER[shifted]


def _has_meaningful_overlap(left: str, right: str, *, min_len: int = 4) -> bool:
    left_key = normalize_text_key(left)
    right_key = normalize_text_key(right)
    if not left_key or not right_key:
        return False
    shorter, longer = (left_key, right_key) if len(left_key) <= len(right_key) else (right_key, left_key)
    for size in range(len(shorter), min_len - 1, -1):
        for start in range(0, len(shorter) - size + 1):
            if shorter[start:start + size] in longer:
                return True
    return False
