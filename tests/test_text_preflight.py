from __future__ import annotations

from types import SimpleNamespace

from services.text_preflight import preflight


def _cfg(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(
        skip_punctuation_only=True,
        skip_single_emoji=True,
        skip_single_char=True,
        skip_repetition=True,
        min_repetition_count=3,
        bypass_on_reply_to_bot=True,
        bypass_on_at_bot=True,
        **kwargs,
    )


def test_preflight_skips_punctuation_only() -> None:
    result = preflight("？？？", config=_cfg())
    assert result.should_skip is True
    assert result.reason == "punctuation_only"


def test_preflight_skips_single_emoji() -> None:
    result = preflight("😂", config=_cfg())
    assert result.should_skip is True
    assert result.reason == "single_emoji"


def test_preflight_skips_single_char() -> None:
    result = preflight("嗯", config=_cfg())
    assert result.should_skip is True
    assert result.reason == "single_char"


def test_preflight_reply_to_bot_bypasses_skip() -> None:
    result = preflight("嗯", is_reply_to_bot=True, config=_cfg())
    assert result.should_skip is False


def test_preflight_at_bot_bypasses_skip() -> None:
    result = preflight("。", is_at_bot=True, config=_cfg())
    assert result.should_skip is False


def test_preflight_skips_repetition() -> None:
    result = preflight("哈哈哈哈哈", config=_cfg())
    assert result.should_skip is True
    assert result.reason == "repetition"


def test_preflight_keeps_normal_text() -> None:
    result = preflight("今天天气不错", config=_cfg())
    assert result.should_skip is False
    assert result.reason == ""

