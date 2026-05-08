from __future__ import annotations

from services.llm.thinker import parse_think_output


def test_parse_think_output_accepts_plain_json() -> None:
    decision = parse_think_output('{"action":"reply","thought":"直接回答","sticker":true,"tone":"认真"}')

    assert decision is not None
    assert decision.action == "reply"
    assert decision.thought == "直接回答"
    assert decision.sticker is True
    assert decision.tone == "认真"


def test_parse_think_output_accepts_fenced_json() -> None:
    decision = parse_think_output(
        '```json\n{"action":"wait","thought":"先等一下","sticker":false,"tone":"日常"}\n```'
    )

    assert decision is not None
    assert decision.action == "wait"
    assert decision.thought == "先等一下"
    assert decision.sticker is False
    assert decision.tone == "日常"


def test_parse_think_output_recovers_embedded_json() -> None:
    decision = parse_think_output(
        '我先这样判断：{"action":"search","thought":"查一下今天日期","sticker":"0","tone":"认真"} 然后再说。'
    )

    assert decision is not None
    assert decision.action == "search"
    assert decision.thought == "查一下今天日期"
    assert decision.sticker is False
    assert decision.tone == "认真"


def test_parse_think_output_uses_heuristic_reply_fallback() -> None:
    decision = parse_think_output("哇这个话题我有话想接，简单回一下就好。")

    assert decision is not None
    assert decision.action == "reply"
    assert "parse error" not in decision.thought
    assert decision.thought
    assert decision.sticker is False
    assert decision.tone == "日常"


def test_parse_think_output_uses_heuristic_wait_fallback() -> None:
    decision = parse_think_output("这段我先不回，等一下再说。")

    assert decision is not None
    assert decision.action == "wait"
    assert "先不回" in decision.thought or "等一下" in decision.thought


def test_parse_think_output_empty_text_returns_none() -> None:
    assert parse_think_output("   ") is None
