from __future__ import annotations

import asyncio

import pytest

from services.sticker import StickerDecisionContext, StickerDecisionProvider
from services.sticker.decision_provider import _SCORE_SOFT_BAND, compute_sticker_score


async def _decide(*, rng=None, threshold=0.5, **kwargs):
    # Default rng=0.0 → inside the soft band the linear ramp sends; outside it
    # the decision is deterministic regardless of rng. Tests that probe the band
    # pass an explicit rng.
    return await StickerDecisionProvider().decide(
        StickerDecisionContext(**kwargs),
        threshold=threshold,
        rng=(rng if rng is not None else (lambda: 0.0)),
    )


async def test_sticker_decision_no_candidates() -> None:
    decision = await _decide()

    assert decision.should_send is False
    assert decision.reason == "no_candidates"
    assert decision.candidate_pool == ()


# ---------------------------------------------------------------------------
# Deterministic score gate (2026-06-12): send_probability now holds the
# logit-linear *score* (sigmoid, [0,1]); the decision is `score >= threshold`,
# fully deterministic outside the narrow soft band. No more Bernoulli骰子.
# ---------------------------------------------------------------------------
async def test_sticker_decision_thinker_wants_sends_deterministically() -> None:
    # thinker ran + suggested → dominant +2.5 logit → score ~0.92, well above 0.5.
    # Deterministic: sends regardless of rng draw.
    for draw in (0.0, 0.5, 0.99):
        decision = await _decide(
            frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=True,
            mood_energy=0.46, rng=lambda d=draw: d,
        )
        assert decision.should_send is True, f"draw={draw}"
        assert decision.reason == "score_send"
    assert decision.send_probability > 0.85


async def test_sticker_decision_serious_reply_skips() -> None:
    # A serious reply: thinker ran and said no → veto (hard, score irrelevant).
    decision = await _decide(
        frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=False,
        rng=lambda: 0.0,
    )
    assert decision.should_send is False
    assert decision.reason == "thinker_veto"


# ---------------------------------------------------------------------------
# §3.5b narrow-band softening: only near the threshold does rng matter.
# thinker absent (force_reply/@) lands score ~0.57, inside [0.4, 0.6] → jitter.
# ---------------------------------------------------------------------------
async def test_sticker_decision_soft_band_jitter_when_uncertain() -> None:
    kw = dict(frequent_candidates=("s1",), thinker_ran=False, mood_energy=0.46)
    score = compute_sticker_score(StickerDecisionContext(**kw), "frequent")
    # Confirm this case sits inside the soft band around the default threshold.
    assert abs(score - 0.5) <= _SCORE_SOFT_BAND, score
    low_draw = await _decide(**kw, rng=lambda: 0.0)
    high_draw = await _decide(**kw, rng=lambda: 0.999)
    assert low_draw.should_send is True
    assert high_draw.should_send is False


async def test_sticker_decision_thinker_veto_does_not_block_tool_call() -> None:
    # An explicit send_sticker tool_call is the LLM's own action — thinker's
    # whether-to-decorate veto must not override it.
    decision = await _decide(
        tool_call_candidates=("s1",), thinker_ran=True, thinker_suggested=False, rng=lambda: 0.0
    )
    assert decision.should_send is True
    assert decision.trigger_source == "tool_call"


async def test_sticker_decision_thinker_wants_scores_over_no_opinion() -> None:
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
# energy axis: low energy lowers the score but NEVER zeroes it ("累 ≠ 不发")
# ---------------------------------------------------------------------------
async def test_sticker_decision_low_energy_reduces_but_does_not_block() -> None:
    low = await _decide(
        mood_energy=0.0, frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=True
    )
    high = await _decide(
        mood_energy=1.0, frequent_candidates=("s1",), thinker_ran=True, thinker_suggested=True
    )

    assert low.send_probability > 0.0
    assert high.send_probability > low.send_probability
    # thinker wants → even at zero energy the score clears the gate.
    assert low.should_send is True


async def test_sticker_decision_energy_is_monotonic() -> None:
    scores = [
        (await _decide(mood_energy=e, tool_call_candidates=("s1",))).send_probability
        for e in (0.0, 0.5, 1.0)
    ]
    assert scores == sorted(scores)


# ---------------------------------------------------------------------------
# valence axis: low valence (sad) does NOT block — selection class handled in
# client, the provider must keep sending ("难过 ≠ 不发").
# ---------------------------------------------------------------------------
async def test_sticker_decision_low_valence_still_sends() -> None:
    decision = await _decide(
        mood_valence=-0.9, mood_energy=0.7, frequent_candidates=("s1",),
        thinker_ran=True, thinker_suggested=True,
    )

    assert decision.should_send is True
    assert decision.reason == "score_send"
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


async def test_sticker_decision_threshold_is_configurable() -> None:
    # thinker absent → mid score ~0.57. A high threshold (0.9) skips it; a low
    # threshold (0.1) sends it — the config knob actually moves the gate.
    kw = dict(frequent_candidates=("s1",), thinker_ran=False, mood_energy=0.46)
    strict = await _decide(**kw, threshold=0.9, rng=lambda: 0.0)
    loose = await _decide(**kw, threshold=0.1, rng=lambda: 0.0)
    assert strict.should_send is False
    assert loose.should_send is True


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


# ---------------------------------------------------------------------------
# End-to-end monte-carlo: with the deterministic gate, "thinker wants a sticker"
# at production params lands near-certain (the old Bernoulli gate capped it at
# ~51%). thinker-veto stays 0%. This pins the headline behavior change.
# ---------------------------------------------------------------------------
async def test_sticker_decision_monte_carlo_send_rates() -> None:
    import random

    async def rate(n: int = 4000, **kw) -> float:
        provider = StickerDecisionProvider()
        sent = 0
        for _ in range(n):
            d = await provider.decide(
                StickerDecisionContext(frequent_candidates=("s1",), **kw),
                rng=random.random,
            )
            sent += int(d.should_send)
        return sent / n

    wants = await rate(thinker_ran=True, thinker_suggested=True, mood_energy=0.46)
    veto = await rate(thinker_ran=True, thinker_suggested=False, mood_energy=0.46)
    assert wants > 0.95, wants  # deterministic send (was ~51% under Bernoulli)
    assert veto == 0.0, veto

