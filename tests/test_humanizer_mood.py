from __future__ import annotations

import asyncio

import pytest

from services.humanizer import Humanizer
from services.llm.segmentation import ReplySegmentationConfig, inter_segment_delay, reply_segment_plan


async def _capture_delay(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return sleeps


async def test_humanizer_cold_mood_slows_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = await _capture_delay(monkeypatch)
    humanizer = Humanizer(enabled=True, min_delay=1.0, max_delay=1.0, char_delay=0.0)

    await humanizer.delay("abcd", mood="cold")

    assert sleeps == [1.3]


async def test_humanizer_playful_mood_speeds_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = await _capture_delay(monkeypatch)
    humanizer = Humanizer(enabled=True, min_delay=1.0, max_delay=1.0, char_delay=0.0)

    await humanizer.delay("abcd", mood="playful")

    assert sleeps == [0.8]


async def test_humanizer_reads_dict_mood_label(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = await _capture_delay(monkeypatch)
    humanizer = Humanizer(enabled=True, min_delay=1.0, max_delay=1.0, char_delay=0.0)

    await humanizer.delay("abcd", mood={"label": "high", "energy": 1.0})

    assert sleeps == [0.85]


def test_inter_segment_delay_cold_mood_slows() -> None:
    neutral = inter_segment_delay("这段文字稍微长一点", mood_label="neutral")
    cold = inter_segment_delay("这段文字稍微长一点", mood_label="cold")

    assert cold > neutral


def test_inter_segment_delay_playful_mood_speeds() -> None:
    neutral = inter_segment_delay("这段文字稍微长一点", mood_label="neutral")
    playful = inter_segment_delay("这段文字稍微长一点", mood_label="playful")

    assert playful < neutral


def test_reply_segment_plan_passes_mood_to_inter_segment_delay() -> None:
    cfg = ReplySegmentationConfig(enabled=True, natural_split_enabled=True, max_segment_chars=6)

    class _ConstRng:
        def __init__(self, value: float) -> None:
            self._value = value

        def random(self) -> float:
            return self._value

    neutral = reply_segment_plan(
        "第一句很长很长。第二句也很长很长。", cfg, mood_label="neutral", rng=_ConstRng(0.0),
    )
    cold = reply_segment_plan(
        "第一句很长很长。第二句也很长很长。", cfg, mood_label="cold", rng=_ConstRng(0.0),
    )

    assert cold.inter_segment_delays[0] > neutral.inter_segment_delays[0]
