from __future__ import annotations

import asyncio

import pytest

from services.sticker import StickerDecisionContext, StickerDecisionProvider


async def _decide(*, rng=None, **kwargs):
    # Default rng=0.0 → any positive probability sends, so tests that only care
    # about "would it send at all" are deterministic. Tests that probe the rate
    # pass an explicit rng.
    return await StickerDecisionProvider().decide(
        StickerDecisionContext(**kwargs), rng=(rng if rng is not None else (lambda: 0.0))
    )


async def test_sticker_decision_no_candidates() -> None:
    decision = await _decide()

    assert decision.should_send is False
    assert decision.reason == "no_candidates"
    assert decision.candidate_pool == ()


# ---------------------------------------------------------------------------
# Bernoulli sampling: send_probability is a RATE, not a threshold. The same
# context sends sometimes and skips sometimes depending on the uniform draw.
# ---------------------------------------------------------------------------
async def test_sticker_decision_samples_below_rate_sends() -> None:
    # frequent + thinker_ran + suggested → base 0.7 × energy(0.6→0.8) = 0.56.
    decision = await _decide(
        frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=True, rng=lambda: 0.1
    )
    assert decision.send_probability == pytest.approx(0.56, abs=1e-3)
    assert decision.should_send is True
    assert decision.reason == "sampled_send"


async def test_sticker_decision_samples_above_rate_skips() -> None:
    decision = await _decide(
        frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=True, rng=lambda: 0.9
    )
    assert decision.send_probability == pytest.approx(0.56, abs=1e-3)
    assert decision.should_send is False
    assert decision.reason == "sampled_skip"


# ---------------------------------------------------------------------------
# thinker-led veto: thinker ran + said no → fallback paths obey (do not send).
# ---------------------------------------------------------------------------
async def test_sticker_decision_thinker_veto_blocks_frequent() -> None:
    decision = await _decide(
        frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=False, rng=lambda: 0.0
    )
    assert decision.should_send is False
    assert decision.reason == "thinker_veto"


async def test_sticker_decision_thinker_veto_does_not_block_tool_call() -> None:
    # An explicit send_sticker tool_call is the LLM's own action — thinker's
    # whether-to-decorate veto must not override it.
    decision = await _decide(
        tool_call_candidates=("s1",), thinker_ran=True, thinker_suggested=False, rng=lambda: 0.0
    )
    assert decision.should_send is True
    assert decision.trigger_source == "tool_call"


async def test_sticker_decision_thinker_not_run_does_not_veto() -> None:
    # force_reply / thinker disabled → thinker had no opinion. Must NOT be treated
    # as a veto; the fallback baseline still gives an occasional sticker.
    decision = await _decide(
        frequent_candidates=("s1",), thinker_ran=False, rng=lambda: 0.0
    )
    assert decision.should_send is True
    # thinker absent → modest baseline 0.4 × energy(0.6→0.8) = 0.32
    assert decision.send_probability == pytest.approx(0.32, abs=1e-3)


async def test_sticker_decision_thinker_ran_raises_rate_over_no_opinion() -> None:
    ran = await _decide(frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=True)
    absent = await _decide(frequent_candidates=("s1",), thinker_ran=False)
    assert ran.send_probability > absent.send_probability


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
    low = await _decide(
        mood_energy=0.0, frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=True
    )
    high = await _decide(
        mood_energy=1.0, frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=True
    )

    # floor 0.5: 0.7 × 0.5 = 0.35 > 0 (still a real probability)
    assert low.send_probability == pytest.approx(0.35, abs=1e-3)
    assert low.send_probability > 0.0
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
    decision = await _decide(
        mood_valence=-0.9, mood_energy=0.7, frequent_candidates=("s1",),
        thinker_ran=True, thinker_suggested=True,
    )

    assert decision.should_send is True
    assert decision.reason == "sampled_send"
    # sad → emotion rerank (empathetic class), still sends
    assert decision.rerank_strategy == "emotion"


# ---------------------------------------------------------------------------
# base_frequency baseline (web-configurable) is monotonic; off short-circuits.
# ---------------------------------------------------------------------------
async def test_sticker_decision_base_frequency_monotonic() -> None:
    rarely = await _decide(
        base_frequency="rarely", frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=True
    )
    normal = await _decide(
        base_frequency="normal", frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=True
    )
    frequently = await _decide(
        base_frequency="frequently", frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=True
    )

    assert rarely.send_probability < normal.send_probability < frequently.send_probability


async def test_sticker_decision_base_frequency_off_zeroes_probability() -> None:
    decision = await _decide(base_frequency="off", tool_call_candidates=("s1",))

    assert decision.should_send is False
    assert decision.send_probability == 0.0


# ---------------------------------------------------------------------------
# affection axis (B3): real stage drives modulation
# ---------------------------------------------------------------------------
async def test_sticker_decision_affection_is_monotonic() -> None:
    stranger = await _decide(
        affection_stage="stranger", mood_energy=1.0, frequent_candidates=("s1",),
        thinker_ran=True, thinker_suggested=True,
    )
    acquaint = await _decide(
        affection_stage="acquaint", mood_energy=1.0, frequent_candidates=("s1",),
        thinker_ran=True, thinker_suggested=True,
    )
    close = await _decide(
        affection_stage="close", mood_energy=1.0, frequent_candidates=("s1",),
        thinker_ran=True, thinker_suggested=True,
    )

    assert stranger.send_probability < acquaint.send_probability < close.send_probability


async def test_sticker_decision_withdraw_affection_blocks() -> None:
    decision = await _decide(affection_stage="withdraw", frequent_candidates=("s1",))

    assert decision.should_send is False
    assert decision.reason == "affection_withdraw_gate"
    assert decision.send_probability <= 0.05


async def test_sticker_decision_close_affection_uses_persona_rerank() -> None:
    decision = await _decide(
        affection_stage="close", mood_energy=1.0, frequent_candidates=("s1",),
        thinker_ran=True, thinker_suggested=True,
    )

    assert decision.should_send is True
    assert decision.rerank_strategy == "persona"


# ---------------------------------------------------------------------------
# kaomoji gating: numeric playful (energy≥0.7 & valence≥0.4)
# ---------------------------------------------------------------------------
async def test_sticker_decision_kaomoji_suppressed_outside_playful() -> None:
    decision = await _decide(
        register_label="quiet", mood_energy=0.3, mood_valence=0.0,
        kaomoji_candidates=("k1",), rng=lambda: 0.5,
    )

    assert decision.should_send is False
    assert decision.send_probability == pytest.approx(0.2, abs=1e-3)


async def test_sticker_decision_kaomoji_playful_numeric_sends() -> None:
    decision = await _decide(
        register_label="playful", mood_energy=0.9, mood_valence=0.6, kaomoji_candidates=("k1",)
    )

    assert decision.should_send is True


# ---------------------------------------------------------------------------
# mood_label is log-only and must not affect the decision
# ---------------------------------------------------------------------------
async def test_sticker_decision_mood_label_does_not_gate() -> None:
    cold = await _decide(
        mood_label="困倦", mood_energy=0.6, frequent_candidates=("s1",),
        thinker_ran=True, thinker_suggested=True,
    )
    neutral = await _decide(
        mood_label="neutral", mood_energy=0.6, frequent_candidates=("s1",),
        thinker_ran=True, thinker_suggested=True,
    )

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
        StickerDecisionContext(frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=True),
        extra_candidates=loader,
        rng=lambda: 0.0,
    )

    assert decision.candidate_pool == ("s1", "extra")


async def test_sticker_decision_cancel_path_propagates() -> None:
    async def loader() -> tuple[str, ...]:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await StickerDecisionProvider().decide(StickerDecisionContext(), extra_candidates=loader)
