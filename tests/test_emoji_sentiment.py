"""Tests for QQ reaction emoji → sentiment mapping (Issue 17 Part 0, P0-1)."""

from __future__ import annotations

from services.humanization.emoji_sentiment import (
    EMOJI_SENTIMENT,
    classify_reaction_sentiment,
)


def test_known_positive_code() -> None:
    polarity, intensity = classify_reaction_sentiment("171")  # 点赞
    assert polarity == "positive"
    assert intensity == 0.9


def test_known_negative_code() -> None:
    polarity, intensity = classify_reaction_sentiment("322")  # 翻白眼
    assert polarity == "negative"
    assert intensity == 0.9


def test_known_neutral_code() -> None:
    polarity, intensity = classify_reaction_sentiment("32")  # 疑问
    assert polarity == "neutral"
    assert intensity == 0.0


def test_unknown_code_defaults_weak_positive() -> None:
    polarity, intensity = classify_reaction_sentiment("999999")
    assert polarity == "positive"
    assert intensity == 0.2


def test_empty_code_defaults_weak_positive() -> None:
    polarity, intensity = classify_reaction_sentiment("")
    assert polarity == "positive"
    assert intensity == 0.2


def test_intensity_within_range() -> None:
    for polarity, intensity, _label in EMOJI_SENTIMENT.values():
        assert polarity in ("positive", "negative", "neutral")
        assert 0.0 <= intensity <= 1.0


def test_neutral_entries_have_zero_intensity() -> None:
    for polarity, intensity, _label in EMOJI_SENTIMENT.values():
        if polarity == "neutral":
            assert intensity == 0.0
