"""Shared M1 irritation signal math independent of the Schedule plugin."""

from __future__ import annotations

from typing import Any

_MENTION_TENSION = 0.03
_POKE_TENSION = 0.04
_BURST_BONUS = 0.01
_TENSION_CAP = 0.2


def compute_m1_irritation_tension_delta(
    *,
    mention_count: int = 0,
    poke_count: int = 0,
    m1_enabled: bool = False,
) -> float:
    """Convert @/poke burst counts into a bounded M1 tension delta."""
    if not m1_enabled:
        return 0.0
    mentions = max(0, int(mention_count or 0))
    pokes = max(0, int(poke_count or 0))
    total = mentions + pokes
    if total <= 0:
        return 0.0
    delta = (
        mentions * _MENTION_TENSION
        + pokes * _POKE_TENSION
        + max(0, total - 1) * _BURST_BONUS
    )
    return max(0.0, min(_TENSION_CAP, delta))


def register_m1_irritation_signal(
    mood_engine: Any,
    *,
    mention_count: int = 0,
    poke_count: int = 0,
    group_id: str | int | None = None,
    session_id: str = "",
    m1_enabled: bool = False,
) -> bool:
    """Register irritation through the mood-engine interaction port."""
    delta = compute_m1_irritation_tension_delta(
        mention_count=mention_count,
        poke_count=poke_count,
        m1_enabled=m1_enabled,
    )
    if delta <= 0.0 or mood_engine is None:
        return False
    mood_engine.register_interaction_signal(
        tension_d=delta,
        group_id=group_id,
        session_id=session_id,
        m1_tension_enabled=True,
    )
    return True
