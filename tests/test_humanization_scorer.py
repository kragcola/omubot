from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services.humanization import (
    LAST_METRICS_SLOT,
    HumanizationScore,
    StylometricScorer,
    create_humanization_state_bus,
)
from services.system_module import Scope


def _scope() -> Scope:
    return Scope(session_id="group_100", group_id="100", user_id="u1", turn_id="turn-1")


def test_scorer_good_reply_scores_high() -> None:
    result = StylometricScorer().score("懂，我先按这个方向接一下。", register="neutral")

    assert isinstance(result, HumanizationScore)
    assert result.total > 0.9
    assert result.issues == []
    assert set(result.axes) == {"content", "register", "mood", "surface", "sticker_reuse"}


def test_content_empty_reply_is_penalized() -> None:
    result = StylometricScorer().score("")

    assert result.axes["content"] == 0.2
    assert "content.empty" in result.issues


def test_content_too_similar_to_reference_is_penalized() -> None:
    text = "我知道了，先这样处理。"
    result = StylometricScorer().score(text, references=[text])

    assert result.axes["content"] <= 0.58
    assert "content.too_similar_reference" in result.issues


def test_register_quiet_loud_reply_is_penalized() -> None:
    result = StylometricScorer().score("哈哈哈太棒了！！", register={"label": "quiet"})

    assert result.axes["register"] <= 0.55
    assert "register.quiet_too_loud" in result.issues


def test_register_distant_overfamiliar_reply_is_penalized() -> None:
    result = StylometricScorer().score("宝贝我来啦", register="distant")

    assert result.axes["register"] <= 0.45
    assert "register.distant_overfamiliar" in result.issues


def test_register_serious_decorative_reply_is_penalized() -> None:
    result = StylometricScorer().score("可以的☆ 这个问题要先看日志", register="serious")

    assert result.axes["register"] <= 0.55
    assert "register.serious_too_decorative" in result.issues


def test_mood_low_energy_overexcited_reply_is_penalized() -> None:
    mood = SimpleNamespace(energy=0.2, valence=0.5)
    result = StylometricScorer().score("冲冲冲！！", mood=mood)

    assert result.axes["mood"] == 0.5
    assert "mood.low_energy_overexcited" in result.issues


def test_mood_high_energy_too_cold_reply_is_penalized() -> None:
    result = StylometricScorer().score("嗯", mood={"energy": 0.9, "valence": 0.8})

    assert result.axes["mood"] == 0.65
    assert "mood.high_energy_too_cold" in result.issues


def test_surface_em_dash_and_template_phrase_are_penalized() -> None:
    result = StylometricScorer().score("作为一个AI，我会尽力解释——以下是答案。")

    assert result.axes["surface"] <= 0.55
    assert "surface.em_dash" in result.issues
    assert "surface.template_phrase" in result.issues


def test_surface_decorative_symbols_are_penalized() -> None:
    result = StylometricScorer().score("好呀☆")

    assert result.axes["surface"] <= 0.7
    assert "surface.decorative_symbol" in result.issues


def test_sticker_reuse_axis_detects_recent_sticker() -> None:
    result = StylometricScorer().score("«表情包:stk_abc12345»", recent_sticker_ids=["stk_abc12345"])

    assert result.axes["sticker_reuse"] == 0.4
    assert "sticker.reuse_recent" in result.issues


def test_scorer_writes_last_metrics_when_bus_and_scope_are_provided() -> None:
    bus = create_humanization_state_bus()
    result = StylometricScorer().score("先这样接一下。", bus=bus, scope=_scope())

    snapshot = bus.get(LAST_METRICS_SLOT, scope=_scope())
    assert snapshot is not None
    assert snapshot.value["score"] == result.total
    assert snapshot.value["axes"] == result.axes


@pytest.mark.asyncio
async def test_scorer_cancel_path_does_not_dirty_write() -> None:
    bus = create_humanization_state_bus()
    scorer = StylometricScorer()

    task = asyncio.create_task(scorer.score_async("先这样接一下。", bus=bus, scope=_scope()))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert bus.get(LAST_METRICS_SLOT, scope=_scope()) is None
