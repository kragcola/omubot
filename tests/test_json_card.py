"""Tests for services.json_card.extract_json_card_text (F-γ G1)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from kernel.router import _render_message
from services.json_card import extract_json_card_text


def test_extracts_prompt_and_title_desc() -> None:
    raw = json.dumps({
        "prompt": "[QQ小程序]摸摸小saki",
        "meta": {"detail_1": {"title": "摸摸小saki", "desc": "千早糖愛音 0:21"}},
    })
    out = extract_json_card_text(raw)
    assert "摸摸小saki" in out
    assert "千早糖愛音 0:21" in out


def test_dedups_repeated_fields() -> None:
    raw = json.dumps({
        "prompt": "摸摸小saki",
        "meta": {"detail_1": {"title": "摸摸小saki", "desc": "摸摸小saki"}},
    })
    # prompt and title/desc all identical → single token, not tripled.
    assert extract_json_card_text(raw) == "摸摸小saki"


def test_invalid_json_returns_empty() -> None:
    assert extract_json_card_text("not json {{{") == ""
    assert extract_json_card_text("") == ""


def test_card_without_known_fields_returns_empty() -> None:
    assert extract_json_card_text(json.dumps({"ver": "1.0", "config": {}})) == ""


def test_prompt_only_card() -> None:
    assert extract_json_card_text(json.dumps({"prompt": "[QQ小程序]但是张雪峰"})) == "[QQ小程序]但是张雪峰"


def test_bilibili_reexport_matches() -> None:
    """The bilibili plugin re-exports the same parser under its old name."""
    from plugins.bilibili.plugin import _extract_json_card_text

    raw = json.dumps({"meta": {"detail_1": {"title": "abc"}}})
    assert _extract_json_card_text(raw) == extract_json_card_text(raw) == "abc"


@pytest.mark.asyncio
async def test_render_message_quoted_json_card_has_body() -> None:
    """G1: a quoted B站视频 card renders with its title/desc inside QUOTED_MSG,
    not an empty shell. Regression for F-γ (§19)."""
    card_raw = json.dumps({
        "prompt": "[QQ小程序]摸摸小saki",
        "meta": {"detail_1": {"title": "摸摸小saki", "desc": "千早糖愛音"}},
    })
    reply = SimpleNamespace(
        sender=SimpleNamespace(user_id="2459515872", nickname="丛非凡"),
        message=Message([MessageSegment.json(card_raw)]),
    )
    # Current message: user @bot with no text (the real F-γ shape).
    rendered = await _render_message(
        Message([MessageSegment.at("384801062")]),
        reply=reply,
        self_id="384801062",
        vision_enabled=False,
    )
    text = rendered if isinstance(rendered, str) else " ".join(
        b.get("text", "") for b in rendered if isinstance(b, dict)
    )
    assert "QUOTED_MSG" in text
    assert "摸摸小saki" in text  # card content is now in the quote body

