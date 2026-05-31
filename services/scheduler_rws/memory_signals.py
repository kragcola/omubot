"""Async readers for memory/relationship signals used by scheduler RWS."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

_POSITIVE_MARKERS: tuple[str, ...] = ("笑", "好", "顺利", "愿意", "回应", "配合", "继续", "喜欢", "开心", "成功")
_NEGATIVE_MARKERS: tuple[str, ...] = ("冷", "无视", "拒绝", "尴尬", "生气", "沉默", "不回", "失败", "反感", "打断")
_WILLINGNESS_PHASE_SCORES: dict[str, float] = {
    "stranger": 0.2,
    "acquaint": 0.4,
    "familiar": 0.6,
    "close": 0.8,
    "withdraw": 0.1,
}


async def recent_outcome_ratio(episode_store: Any, group_id: str, hours: int = 24) -> float:
    """Return the recent positive outcome ratio in [0, 1]."""
    if episode_store is None or not str(group_id or "").strip():
        return 0.5
    list_episodes: Any = getattr(episode_store, "list_episodes", None)
    if callable(list_episodes):
        episodes = await cast(Any, list_episodes)(group_id=str(group_id), limit=80)
    else:
        list_for_recall: Any = getattr(episode_store, "list_for_recall", None)
        if not callable(list_for_recall):
            return 0.5
        episodes = await cast(Any, list_for_recall)(group_id=str(group_id), limit=80, include_decayed=True)
    cutoff = datetime.now() - timedelta(hours=max(1, int(hours or 24)))
    positive = 0
    labeled = 0
    for episode in episodes:
        timestamp = _coerce_episode_ts(getattr(episode, "updated_at", "") or getattr(episode, "created_at", ""))
        if timestamp is not None and timestamp < cutoff:
            continue
        polarity = _outcome_polarity(str(getattr(episode, "outcome_signal", "") or ""))
        if polarity == "neutral":
            continue
        labeled += 1
        if polarity == "positive":
            positive += 1
    if labeled <= 0:
        return 0.5
    return _clamp01(positive / labeled)


async def familiarity_score(card_store: Any, target_id: str, cap: int = 50) -> float:
    """Return target-related active card density in [0, 1]."""
    normalized_target = str(target_id or "").strip()
    if card_store is None or not normalized_target:
        return 0.0
    list_cards: Any = getattr(card_store, "list_cards", None)
    if callable(list_cards):
        cards = await cast(Any, list_cards)(scope_id=normalized_target, status="active", limit=max(1, int(cap or 50)))
    else:
        get_entity_cards: Any = getattr(card_store, "get_entity_cards", None)
        if not callable(get_entity_cards):
            return 0.0
        cards = await cast(Any, get_entity_cards)("user", normalized_target)
    return _clamp01(len(cards) / max(1, int(cap or 50)))


async def willingness_phase_score(stage: str) -> float:
    """Map willingness stage names onto [0, 1] scheduler features."""
    return _clamp01(_WILLINGNESS_PHASE_SCORES.get(str(stage or "").strip().lower(), 0.5))


async def mood_trend(mood_engine: Any, group_id: str) -> float:
    """Return the recent 30-minute mood valence trend, normalized to [0, 1]."""
    if mood_engine is None or not str(group_id or "").strip():
        return 0.5
    recent_profiles: Any = getattr(mood_engine, "recent_profiles", None)
    if not callable(recent_profiles):
        return 0.5
    profiles = cast(Any, recent_profiles)(group_id=str(group_id), session_id=f"group_{group_id}", within_s=1800.0)
    if len(profiles) < 2:
        return 0.5
    first = float(getattr(profiles[0], "valence", 0.0))
    last = float(getattr(profiles[-1], "valence", 0.0))
    delta = max(-1.0, min(1.0, last - first))
    return _clamp01((delta + 1.0) / 2.0)


def _coerce_episode_ts(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _outcome_polarity(text: str) -> str:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return "neutral"
    if any(marker in normalized for marker in _NEGATIVE_MARKERS):
        return "negative"
    if any(marker in normalized for marker in _POSITIVE_MARKERS):
        return "positive"
    return "neutral"


def _clamp01(value: object) -> float:
    try:
        raw = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raw = 0.0
    return max(0.0, min(1.0, raw))
