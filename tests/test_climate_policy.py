"""Tests for Dialogue Climate M4 — ClimatePolicy synthesis (pure)."""

from __future__ import annotations

from services.dialogue_climate.policy import synthesize
from services.dialogue_climate.state import ClimateState


def test_neutral_state_full_bias_no_guidance():
    out = synthesize(ClimateState())
    assert out.reply_bias == "full"
    assert out.mood_label == "neutral"
    assert out.guidance == ""  # neutral → nothing to inject
    assert out.delay_multiplier == 1.0


def test_high_tension_shortens_and_speeds_up():
    out = synthesize(ClimateState(tension=0.7))
    assert out.reply_bias == "short"
    assert out.mood_label == "irritated"
    assert out.delay_multiplier < 1.0
    assert "对话气候" in out.guidance


def test_low_energy_shortens_and_slows():
    out = synthesize(ClimateState(energy=0.2))
    assert out.reply_bias == "short"
    assert out.delay_multiplier > 1.0


def test_high_openness_elaborates():
    out = synthesize(ClimateState(energy=0.6, openness=0.8))
    assert out.reply_bias == "elaborate"


def test_playful_mood():
    out = synthesize(ClimateState(valence=0.7, energy=0.7))
    assert out.mood_label == "playful"


def test_close_familiarity_hint_and_guidance():
    out = synthesize(ClimateState(familiarity=0.8, openness=0.75, energy=0.6))
    assert out.openness_hint == "more_open"
    assert "熟" in out.guidance


def test_deterministic_same_state_same_output():
    s = ClimateState(energy=0.4, valence=0.55, tension=0.2, familiarity=0.5)
    a = synthesize(s)
    b = synthesize(s)
    assert a == b


def test_tension_dominates_over_energy():
    # high tension AND low energy → still short (both point short, tension first)
    out = synthesize(ClimateState(tension=0.7, energy=0.2))
    assert out.reply_bias == "short"
    assert out.mood_label == "irritated"
