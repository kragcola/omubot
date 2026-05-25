from __future__ import annotations

import asyncio

import pytest

from services.llm.llm_request import LLMRequest
from services.reply_planner.binary_planner import (
    BinaryPlanDecision,
    BinaryPlanner,
    BinaryPlannerFeatures,
    build_binary_planner_request,
    parse_binary_planner_output,
)


def test_parse_binary_planner_reply_json() -> None:
    decision = parse_binary_planner_output(
        '{"reasoning":"明确问 bot","decision":"reply","confidence":0.82,"reason":"directed"}'
    )

    assert decision.action == "reply"
    assert decision.confidence == 0.82
    assert decision.reasoning == "明确问 bot"


def test_parse_binary_planner_no_reply_json() -> None:
    decision = parse_binary_planner_output(
        '{"reasoning":"在和别人闲聊","decision":"no_reply","confidence":0.7,"reason":"other_user"}'
    )

    assert decision.action == "no_reply"
    assert decision.confidence == 0.7


def test_parse_binary_planner_fenced_json() -> None:
    decision = parse_binary_planner_output(
        '```json\n{"reasoning":"引用 bot","decision":"reply","confidence":0.91,"reason":"quote"}\n```'
    )

    assert decision.action == "reply"
    assert decision.parse_mode == "direct"


def test_parse_binary_planner_embedded_json() -> None:
    decision = parse_binary_planner_output(
        '好的 {"reasoning":"没有接话价值","decision":"no_reply","confidence":0.64,"reason":"idle"}'
    )

    assert decision.action == "no_reply"
    assert decision.parse_mode == "embedded"


def test_parse_binary_planner_invalid_decision_fails_open() -> None:
    raw = '{"decision":"wait","confidence":0.99}'
    decision = parse_binary_planner_output(raw)

    assert decision == BinaryPlanDecision.fail_open("invalid_decision", raw_text=raw)


def test_parse_binary_planner_clamps_confidence_and_reason() -> None:
    decision = parse_binary_planner_output(
        '{"reasoning":"很长 很长 很长 很长 很长 很长 很长 很长 很长 很长","decision":"reply",'
        '"confidence":2,"reason":"ok"}'
    )

    assert decision.confidence == 1.0
    assert len(decision.reasoning) <= 80


def test_build_binary_planner_request_uses_llm_request_spine() -> None:
    request = build_binary_planner_request(
        BinaryPlannerFeatures(
            current_text="继续说",
            current_user_id="1001",
            group_id="g1",
            register_label="playful",
            context="上下文",
            addressee_id="bot",
            bot_id="bot",
            reply_to_bot=True,
            recent_assistant_text="上一条 bot 回复",
        )
    )

    assert isinstance(request, LLMRequest)
    assert request.task == "reply_gate"
    assert request.user_id == "1001"
    assert request.group_id == "g1"
    assert request.requires_capabilities == ("chat", "json")
    payload = request.user_messages[0]["content"]
    assert '"register_label": "playful"' in payload
    assert '"context": "上下文"' in payload


def test_build_binary_planner_request_truncates_context() -> None:
    request = build_binary_planner_request(BinaryPlannerFeatures(current_text="x" * 200, context="y" * 400))
    payload = request.user_messages[0]["content"]

    assert "xxx..." in payload
    assert "yyy..." in payload


async def test_binary_planner_success_calls_api() -> None:
    captured: list[LLMRequest] = []

    async def api_call(request: LLMRequest) -> dict:
        captured.append(request)
        return {"text": '{"reasoning":"可接","decision":"reply","confidence":0.8,"reason":"directed"}'}

    decision = await BinaryPlanner().plan(BinaryPlannerFeatures(current_text="@bot 在吗"), api_call=api_call)

    assert decision.action == "reply"
    assert captured[0].task == "reply_gate"


async def test_binary_planner_call_failure_fails_open() -> None:
    async def api_call(request: LLMRequest) -> dict:
        raise RuntimeError("boom")

    decision = await BinaryPlanner().plan(BinaryPlannerFeatures(current_text="hi"), api_call=api_call)

    assert decision.action == "reply"
    assert decision.reason == "planner_call_failed"


async def test_binary_planner_timeout_fails_open() -> None:
    async def api_call(request: LLMRequest) -> dict:
        await asyncio.sleep(0.05)
        return {"text": "{}"}

    decision = await BinaryPlanner(timeout_ms=1).plan(BinaryPlannerFeatures(current_text="hi"), api_call=api_call)

    assert decision.action == "reply"
    assert decision.reason == "planner_timeout"


async def test_binary_planner_cancel_path_propagates() -> None:
    async def api_call(request: LLMRequest) -> dict:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await BinaryPlanner().plan(BinaryPlannerFeatures(current_text="hi"), api_call=api_call)
