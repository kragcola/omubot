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
    # frequent 0.7 × normal 1.0 × energy(0.6→0.8) = 0.56
    assert decision.send_probability == pytest.approx(0.56, abs=1e-3)


async def test_sticker_decision_tool_call_wins_priority() -> None:
    decision = await _decide(
        tool_call_candidates=("tool",),
        frequent_candidates=("freq",),
        thinker_candidates=("hint",),
    )

    assert decision.should_send is True
    assert decision.trigger_source == "tool_call"
    assert decision.candidate_pool == ("tool", "freq", "hint")


# ---------------------------------------------------------------------------
# energy axis: low energy lowers probability but NEVER to zero ("累 ≠ 不发")
# ---------------------------------------------------------------------------
async def test_sticker_decision_low_energy_reduces_but_does_not_block() -> None:
    low = await _decide(mood_energy=0.0, frequent_candidates=("s1",))
    high = await _decide(mood_energy=1.0, frequent_candidates=("s1",))

    # floor 0.5: frequent 0.7 × 0.5 = 0.35 > 0 (still a real probability)
    assert low.send_probability == pytest.approx(0.35, abs=1e-3)
    assert low.send_probability > 0.0
    # high energy ×1.0 → 0.7, strictly higher than low energy
    assert high.send_probability > low.send_probability


async def test_sticker_decision_energy_is_monotonic() -> None:
    probs = [
        (await _decide(mood_energy=e, tool_call_candidates=("s1",))).send_probability
        for e in (0.0, 0.5, 1.0)
    ]
    assert probs == sorted(probs)


# ---------------------------------------------------------------------------
# valence axis: low valence (sad) does NOT block — selection class handled in
# client, the provider must keep sending.
# ---------------------------------------------------------------------------
async def test_sticker_decision_low_valence_still_sends() -> None:
    decision = await _decide(mood_valence=-0.9, mood_energy=0.7, frequent_candidates=("s1",))

    assert decision.should_send is True
    assert decision.reason == "single_decision"
    # sad → emotion rerank (empathetic class), still sends
    assert decision.rerank_strategy == "emotion"


# ---------------------------------------------------------------------------
# base_frequency baseline (web-configurable) is monotonic; off short-circuits
# at the caller (here it just zeroes the probability).
# ---------------------------------------------------------------------------
async def test_sticker_decision_base_frequency_monotonic() -> None:
    rarely = await _decide(base_frequency="rarely", frequent_candidates=("s1",))
    normal = await _decide(base_frequency="normal", frequent_candidates=("s1",))
    frequently = await _decide(base_frequency="frequently", frequent_candidates=("s1",))

    assert rarely.send_probability < normal.send_probability < frequently.send_probability
    # frequently 1.4 multiplier should lift a frequent source over the 0.5 line
    assert frequently.should_send is True


async def test_sticker_decision_base_frequency_off_zeroes_probability() -> None:
    decision = await _decide(base_frequency="off", tool_call_candidates=("s1",))

    assert decision.should_send is False
    assert decision.send_probability == 0.0


# ---------------------------------------------------------------------------
# thinker:false demotes (×0.6) but does not veto (D1 = a)
# ---------------------------------------------------------------------------
async def test_sticker_decision_thinker_false_demotes_not_vetoes() -> None:
    on = await _decide(thinker_suggested=True, mood_energy=1.0, tool_call_candidates=("s1",))
    off = await _decide(thinker_suggested=False, mood_energy=1.0, tool_call_candidates=("s1",))

    assert off.send_probability < on.send_probability
    # tool_call 0.85 × 0.6 = 0.51 ≥ 0.5 → still can send despite thinker:false
    assert off.send_probability == pytest.approx(0.51, abs=1e-3)
    assert off.should_send is True


async def test_sticker_decision_thinker_source_hint_only_below_threshold() -> None:
    # thinker source base 0.45 × energy(0.6→0.8) = 0.36 < 0.5 → hint only
    decision = await _decide(thinker_candidates=("t1",))

    assert decision.should_send is False
    assert decision.trigger_source == "thinker"
    assert decision.reason == "thinker_hint_only"


# ---------------------------------------------------------------------------
# affection axis (B3): real stage now drives modulation
# ---------------------------------------------------------------------------
async def test_sticker_decision_affection_is_monotonic() -> None:
    stranger = await _decide(affection_stage="stranger", mood_energy=1.0, frequent_candidates=("s1",))
    acquaint = await _decide(affection_stage="acquaint", mood_energy=1.0, frequent_candidates=("s1",))
    close = await _decide(affection_stage="close", mood_energy=1.0, frequent_candidates=("s1",))

    assert stranger.send_probability < acquaint.send_probability < close.send_probability


async def test_sticker_decision_withdraw_affection_blocks() -> None:
    decision = await _decide(affection_stage="withdraw", frequent_candidates=("s1",))

    assert decision.should_send is False
    assert decision.reason == "affection_withdraw_gate"
    assert decision.send_probability <= 0.05


async def test_sticker_decision_close_affection_uses_persona_rerank() -> None:
    decision = await _decide(affection_stage="close", mood_energy=1.0, frequent_candidates=("s1",))

    assert decision.should_send is True
    assert decision.rerank_strategy == "persona"


# ---------------------------------------------------------------------------
# kaomoji gating: numeric playful (energy≥0.7 & valence≥0.4) replaces the old
# Chinese-mismatched _PLAYFUL_MOODS set.
# ---------------------------------------------------------------------------
async def test_sticker_decision_kaomoji_suppressed_outside_playful() -> None:
    decision = await _decide(
        register_label="quiet", mood_energy=0.3, mood_valence=0.0, kaomoji_candidates=("k1",)
    )

    assert decision.should_send is False
    assert decision.send_probability == pytest.approx(0.2, abs=1e-3)


async def test_sticker_decision_kaomoji_playful_numeric_sends() -> None:
    decision = await _decide(
        register_label="playful", mood_energy=0.9, mood_valence=0.6, kaomoji_candidates=("k1",)
    )

    assert decision.should_send is True


# ---------------------------------------------------------------------------
# mood_label is now log-only and must not affect the decision
# ---------------------------------------------------------------------------
async def test_sticker_decision_mood_label_does_not_gate() -> None:
    # Old behaviour blocked "cold"/"tired"; now only the numeric axes matter.
    cold = await _decide(mood_label="困倦", mood_energy=0.6, frequent_candidates=("s1",))
    neutral = await _decide(mood_label="neutral", mood_energy=0.6, frequent_candidates=("s1",))

    assert cold.send_probability == neutral.send_probability
    assert cold.should_send == neutral.should_send


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
