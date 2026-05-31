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


def test_strip_declarations_does_not_delete_entire_reply() -> None:
    cleaned, matched = strip_declarations("我是凤笑梦", bot_name="凤笑梦")

    assert cleaned == "我是凤笑梦"
    assert matched == ["我是凤笑梦"]


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

