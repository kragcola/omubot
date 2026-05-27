from __future__ import annotations

import asyncio
from itertools import repeat

import pytest

from services.llm import segmentation
from services.llm.segmentation import (
    ReplySegmentationConfig,
    inter_segment_delay,
    natural_split,
    reply_segment_plan,
)


class SequenceRng:
    def __init__(self, *values: float, fallback: float = 0.0) -> None:
        self._values = list(values)
        self._fallback = fallback

    def random(self) -> float:
        if self._values:
            return self._values.pop(0)
        return self._fallback


def _rng(value: float) -> SequenceRng:
    return SequenceRng(*repeat(value, 200), fallback=value)


def test_flag_off_returns_single_segment_plan() -> None:
    text = "第一句很长很长。第二句很长很长。第三句很长很长。"
    cfg = ReplySegmentationConfig(natural_split_enabled=False)

    plan = reply_segment_plan(text, cfg)

    assert plan.segments == [text]
    assert plan.raw_count == 1
    assert plan.limit_status == "none"
    assert plan.inter_segment_delays == []


def test_disabled_config_overrides_natural_flag() -> None:
    text = "第一句！第二句！第三句！"
    cfg = ReplySegmentationConfig(enabled=False, natural_split_enabled=True)

    plan = reply_segment_plan(text, cfg, rng=_rng(0.0))

    assert plan.segments == [text]
    assert plan.raw_count == 1
    assert plan.limit_status == "none"
    assert plan.inter_segment_delays == []


def test_natural_flag_uses_natural_split_and_dynamic_delays() -> None:
    text = "第一段内容稍微长一点！第二段内容稍微长一点！第三段内容稍微长一点！"
    cfg = ReplySegmentationConfig(natural_split_enabled=True, inter_segment_delay_s=9.9)

    plan = reply_segment_plan(text, cfg, register="playful", rng=_rng(0.0))

    assert plan.segments == natural_split(text, register="playful", rng=_rng(0.0))
    assert plan.raw_count == len(plan.segments)
    assert plan.limit_status == "none"
    assert plan.inter_segment_delays == [
        inter_segment_delay(segment, register="playful")
        for segment in plan.segments[:-1]
    ]
    assert plan.inter_segment_delays != [9.9] * len(plan.inter_segment_delays)


def test_inter_delay_array_has_one_item_per_gap_and_lower_bound() -> None:
    text = "短！这是一段更长一点的内容！末尾。"
    cfg = ReplySegmentationConfig(natural_split_enabled=True)

    plan = reply_segment_plan(text, cfg, rng=_rng(0.0))

    assert len(plan.inter_segment_delays) == max(0, len(plan.segments) - 1)
    assert all(delay >= 0.5 for delay in plan.inter_segment_delays)


def test_register_missing_falls_back_to_neutral() -> None:
    text = "第一段内容稍微长一点！第二段内容稍微长一点！第三段内容稍微长一点！"
    cfg = ReplySegmentationConfig(natural_split_enabled=True)

    missing = reply_segment_plan(text, cfg, register=None, rng=_rng(0.8))
    neutral = reply_segment_plan(text, cfg, register="neutral_default", rng=_rng(0.8))

    assert missing == neutral


def test_register_state_dict_is_honored_for_dynamic_delay() -> None:
    text = "这段文字稍微长一点！第二段。"
    cfg = ReplySegmentationConfig(natural_split_enabled=True)

    neutral = reply_segment_plan(text, cfg, register={"label": "neutral_default"}, rng=_rng(0.0))
    quiet = reply_segment_plan(text, cfg, register={"label": "quiet"}, rng=_rng(0.0))

    assert quiet.segments == neutral.segments
    assert quiet.inter_segment_delays[0] > neutral.inter_segment_delays[0]


def test_cancel_during_natural_path_does_not_dirty_write(monkeypatch: pytest.MonkeyPatch) -> None:
    state: list[str] = []
    cfg = ReplySegmentationConfig(natural_split_enabled=True)

    def _raise_cancel(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(segmentation, "natural_split", _raise_cancel)

    with pytest.raises(asyncio.CancelledError):
        reply_segment_plan("这段文字会进入自然分段路径。", cfg, rng=_rng(0.0))

    assert state == []
