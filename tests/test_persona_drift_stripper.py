from __future__ import annotations

from types import SimpleNamespace

from services.llm.persona_drift_stripper import persona_drift_rule, strip_declarations
from services.llm.sentinel_registry import GuardrailContext


def test_strip_declarations_removes_identity_sentence() -> None:
    cleaned, matched = strip_declarations("今天天气真好。我是凤笑梦，WxS的成员。", bot_name="凤笑梦")

    assert cleaned == "今天天气真好。"
    assert matched == ["我是凤笑梦，WxS的成员。"]


def test_strip_declarations_rewrites_leading_wxs_member_clause() -> None:
    cleaned, matched = strip_declarations("作为W×S成员我觉得这首歌很好听")

    assert cleaned == "我觉得这首歌很好听"
    assert matched == ["作为W×S成员我觉得这首歌很好听"]


def test_strip_declarations_rewrites_ai_prefix() -> None:
    cleaned, matched = strip_declarations("我是AI所以我不会累")

    assert cleaned == "所以我不会累"
    assert matched == ["我是AI所以我不会累"]


def test_strip_declarations_keeps_non_declaration_phrase() -> None:
    cleaned, matched = strip_declarations("我是说这个很好吃", bot_name="凤笑梦")

    assert cleaned == "我是说这个很好吃"
    assert matched == []


def test_strip_declarations_fails_closed_on_lone_name_claim() -> None:
    # Policy (2026-06-14): a bare self-name claim IS persona drift. When stripping
    # collapses the whole reply, return empty so the rule reports passed=False and
    # the caller substitutes a fallback — never leak the declaration.
    cleaned, matched = strip_declarations("我是凤笑梦", bot_name="凤笑梦")

    assert cleaned == ""
    assert matched == ["我是凤笑梦"]


def test_strip_declarations_fails_closed_on_lone_ai_declaration() -> None:
    # Regression for the audit's #1 miss: a lone "我是一个AI" used to pass through
    # unchanged (the most important thing to suppress was leaked).
    cleaned, matched = strip_declarations("我是一个AI")

    assert cleaned == ""
    assert matched == ["我是一个AI"]


def test_strip_declarations_fails_closed_on_lone_setting_leak() -> None:
    cleaned, matched = strip_declarations("我的人设是温柔")

    assert cleaned == ""
    assert matched == ["我的人设是温柔"]


def test_strip_declarations_drops_residual_ai_when_name_stripped() -> None:
    # Regression for the audit's #3 wrong-direction strip: "我是{name}，我是一个AI"
    # used to keep the AI declaration after removing the legitimate name. Now the
    # residual hard declaration is dropped and the reply fails closed.
    cleaned, matched = strip_declarations("我是凤笑梦，我是一个AI", bot_name="凤笑梦")

    assert cleaned == ""
    assert matched == ["我是凤笑梦，我是一个AI"]


def test_strip_declarations_keeps_ordinary_self_description() -> None:
    # The broad "我是X" heuristic fires here, but with no real declaration the
    # reply must NOT be suppressed — keep the original (soft match, fail open).
    for text in ("我是个吃货", "我是来帮忙的"):
        cleaned, matched = strip_declarations(text)
        assert cleaned == text
        assert matched == [text]


def test_strip_declarations_fails_closed_on_name_with_membership() -> None:
    cleaned, matched = strip_declarations("我是凤笑梦，WxS的一员", bot_name="凤笑梦")

    assert cleaned == ""
    assert matched == ["我是凤笑梦，WxS的一员"]


def test_persona_drift_rule_fails_closed_on_lone_ai_declaration() -> None:
    ctx = GuardrailContext(
        bot_name="凤笑梦",
        config=SimpleNamespace(persona_drift=SimpleNamespace(enabled=True)),
    )

    result = persona_drift_rule("我是一个AI", ctx)

    assert result.passed is False
    assert result.text == ""
    assert [hit.name for hit in result.hits] == ["persona_drift"]


def test_strip_declarations_leaves_safe_text_unchanged() -> None:
    cleaned, matched = strip_declarations("我觉得这首歌很好听")

    assert cleaned == "我觉得这首歌很好听"
    assert matched == []


def test_persona_drift_rule_respects_enabled_flag_and_bot_name() -> None:
    ctx = GuardrailContext(
        bot_name="凤笑梦",
        config=SimpleNamespace(persona_drift=SimpleNamespace(enabled=True)),
    )

    result = persona_drift_rule("今天天气真好。我是凤笑梦，WxS的成员。", ctx)

    assert result.passed is True
    assert result.text == "今天天气真好。"
    assert [hit.name for hit in result.hits] == ["persona_drift"]

