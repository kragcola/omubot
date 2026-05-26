from __future__ import annotations

import pytest

from services.humanization import CouplingFeatures, lookup_coupling


@pytest.mark.parametrize(
    ("features", "expected_reason"),
    [
        (CouplingFeatures(mood_label="cold", addressee_self=False), "cold_non_self"),
        (CouplingFeatures(mood_label="cold", addressee_self=True), "cold_self"),
        (CouplingFeatures(mood_label="playful", topic_drift_score=0.7), "playful_topic_drift"),
        (CouplingFeatures(mood_label="tired", topic_is_new=True), "tired_new_topic"),
        (CouplingFeatures(mood_label="playful", register_label="playful"), "playful_register_sticker"),
        (CouplingFeatures(mood_label="tired"), "tired_low_sticker"),
        (CouplingFeatures(affection_stage="stranger", register_label="playful"), "stranger_neutral"),
        (CouplingFeatures(affection_stage="close", addressee_self=True), "close_self_playful"),
        (CouplingFeatures(affection_stage="withdraw"), "withdraw_slow_low_sticker"),
    ],
)
def test_lookup_coupling_covers_research_table_rows(
    features: CouplingFeatures,
    expected_reason: str,
) -> None:
    assert expected_reason in lookup_coupling(features).reasons


def test_cold_non_self_suppresses_reply_even_with_active_register() -> None:
    policy = lookup_coupling(CouplingFeatures(mood_label="cold", register_label="active"))

    assert policy.reply_bias == "suppress"
    assert policy.sticker_probability == 0.1


def test_cold_self_is_short_with_typing_multiplier() -> None:
    policy = lookup_coupling(CouplingFeatures(mood_label="cold", addressee_self=True))

    assert policy.reply_bias == "short"
    assert policy.max_segments == 1
    assert policy.typing_multiplier == 1.3


def test_playful_topic_drift_prefers_elaboration() -> None:
    policy = lookup_coupling(CouplingFeatures(mood_label="playful", topic_drift_score=0.61))

    assert policy.reply_bias == "elaborate"


def test_affection_priority_overrides_mood_and_register() -> None:
    policy = lookup_coupling(
        CouplingFeatures(
            mood_label="playful",
            register_label="playful",
            affection_stage="withdraw",
            topic_drift_score=0.9,
        )
    )

    assert policy.reply_bias == "elaborate"
    assert policy.sticker_probability == 0.05
    assert policy.delay_multiplier == 1.3
    assert policy.reasons[-1] == "withdraw_slow_low_sticker"


def test_stranger_forces_neutral_register() -> None:
    policy = lookup_coupling(CouplingFeatures(register_label="playful", affection_stage="stranger"))

    assert policy.register_label == "neutral"


def test_default_policy_is_noop() -> None:
    policy = lookup_coupling(CouplingFeatures())

    assert policy.reply_bias == "default"
    assert policy.sticker_probability is None
    assert policy.reasons == ()
