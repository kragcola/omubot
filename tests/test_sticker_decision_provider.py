from __future__ import annotations

import asyncio

import pytest

from services.sticker import StickerDecisionContext, StickerDecisionProvider


async def _decide(**kwargs):
    return await StickerDecisionProvider().decide(StickerDecisionContext(**kwargs))


async def test_sticker_decision_no_candidates() -> None:
    decision = await _decide()

    assert decision.should_send is False
    assert decision.reason == "no_candidates"
    assert decision.candidate_pool == ()


async def test_sticker_decision_frequent_sends_with_soft_probability() -> None:
    decision = await _decide(frequent_candidates=("s1",))

    assert decision.should_send is True
    assert decision.trigger_source == "frequent"
    assert decision.send_probability == 0.7


async def test_sticker_decision_tool_call_wins_priority() -> None:
    decision = await _decide(
        tool_call_candidates=("tool",),
        frequent_candidates=("freq",),
        thinker_candidates=("hint",),
    )

    assert decision.should_send is True
    assert decision.trigger_source == "tool_call"
    assert decision.candidate_pool == ("tool", "freq", "hint")


async def test_sticker_decision_kaomoji_requires_playful_gate() -> None:
    decision = await _decide(register_label="quiet", mood_label="neutral", kaomoji_candidates=("k1",))

    assert decision.should_send is False
    assert decision.send_probability == 0.2


async def test_sticker_decision_kaomoji_playful_sends() -> None:
    decision = await _decide(register_label="playful", mood_label="playful", kaomoji_candidates=("k1",))

    assert decision.should_send is True
    assert decision.rerank_strategy == "emotion"


async def test_sticker_decision_thinker_hint_only_does_not_self_decide() -> None:
    decision = await _decide(thinker_candidates=("t1",))

    assert decision.should_send is False
    assert decision.trigger_source == "thinker"
    assert decision.reason == "thinker_hint_only"


async def test_sticker_decision_thinker_hint_can_send_when_mood_elevates() -> None:
    decision = await _decide(mood_label="high", thinker_candidates=("t1",))

    assert decision.should_send is True
    assert decision.send_probability == 0.7


async def test_sticker_decision_cold_mood_blocks_overuse() -> None:
    decision = await _decide(mood_label="cold", frequent_candidates=("s1",))

    assert decision.should_send is False
    assert decision.send_probability == 0.1
    assert decision.reason == "mood_or_affection_gate"


async def test_sticker_decision_tired_mood_blocks_overuse() -> None:
    decision = await _decide(mood_label="tired", tool_call_candidates=("s1",))

    assert decision.should_send is False
    assert decision.send_probability == 0.1


async def test_sticker_decision_withdraw_affection_blocks() -> None:
    decision = await _decide(affection_stage="withdraw", frequent_candidates=("s1",))

    assert decision.should_send is False
    assert decision.send_probability == 0.05


async def test_sticker_decision_close_affection_uses_persona_rerank() -> None:
    decision = await _decide(affection_stage="close", frequent_candidates=("s1",))

    assert decision.should_send is True
    assert decision.rerank_strategy == "persona"
    assert decision.send_probability == 0.8


async def test_sticker_decision_cooldown_blocks_single_point() -> None:
    decision = await _decide(cooldown_active=True, frequent_candidates=("s1",), cooldown_ms=1234)

    assert decision.should_send is False
    assert decision.reason == "cooldown_active"
    assert decision.cooldown_ms == 1234


async def test_sticker_decision_dedupes_and_caps_candidates() -> None:
    candidates = tuple(["s1", "s1", *[f"s{i}" for i in range(2, 14)]])

    decision = await _decide(tool_call_candidates=candidates)

    assert len(decision.candidate_pool) == 10
    assert decision.candidate_pool[:3] == ("s1", "s2", "s3")


async def test_sticker_decision_extra_candidates_join_pool() -> None:
    async def loader() -> tuple[str, ...]:
        return ("extra",)

    decision = await StickerDecisionProvider().decide(
        StickerDecisionContext(frequent_candidates=("s1",)),
        extra_candidates=loader,
    )

    assert decision.candidate_pool == ("s1", "extra")


async def test_sticker_decision_cancel_path_propagates() -> None:
    async def loader() -> tuple[str, ...]:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await StickerDecisionProvider().decide(StickerDecisionContext(), extra_candidates=loader)
