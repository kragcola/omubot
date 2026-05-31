from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from services.scheduler_rws.memory_signals import (
    familiarity_score,
    mood_trend,
    recent_outcome_ratio,
    willingness_phase_score,
)


@pytest.mark.asyncio
async def test_recent_outcome_ratio_uses_labeled_recent_episodes() -> None:
    now = datetime.now()

    class _Episode:
        def __init__(self, outcome_signal: str, updated_at: str) -> None:
            self.outcome_signal = outcome_signal
            self.updated_at = updated_at

    class _Store:
        async def list_episodes(self, **_kwargs):
            return [
                _Episode("用户后来愿意继续聊", now.isoformat()),
                _Episode("最后大家都笑了", now.isoformat()),
                _Episode("用户拒绝继续", now.isoformat()),
                _Episode("太久之前的结果", (now - timedelta(days=2)).isoformat()),
            ]

    ratio = await recent_outcome_ratio(_Store(), "g1", hours=24)

    assert ratio == pytest.approx(2 / 3, abs=0.01)


@pytest.mark.asyncio
async def test_familiarity_score_caps_to_unit_interval() -> None:
    class _Store:
        async def list_cards(self, **_kwargs):
            return [object()] * 80

    score = await familiarity_score(_Store(), "u1", cap=50)

    assert score == 1.0


@pytest.mark.asyncio
async def test_willingness_phase_score_maps_known_stage() -> None:
    assert await willingness_phase_score("close") == 0.8
    assert await willingness_phase_score("unknown") == 0.5


@pytest.mark.asyncio
async def test_mood_trend_normalizes_recent_valence_delta() -> None:
    class _Profile:
        def __init__(self, valence: float) -> None:
            self.valence = valence

    class _MoodEngine:
        def recent_profiles(self, **_kwargs):
            return [_Profile(-0.6), _Profile(0.4)]

    trend = await mood_trend(_MoodEngine(), "g1")

    assert trend == 1.0
