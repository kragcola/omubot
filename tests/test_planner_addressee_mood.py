from __future__ import annotations

import asyncio

import pytest

from services.group import AddresseeResult, addressee_gate
from services.llm.llm_request import LLMRequest
from services.reply_planner import BinaryPlanner, BinaryPlannerFeatures, NoReplyCounter, build_binary_planner_request


def test_addressee_gate_cold_other_suppresses() -> None:
    result = addressee_gate(AddresseeResult("100", 0.9, "at"), bot_ids=("42",), mood_label="cold")

    assert result is True


def test_addressee_gate_cold_self_passes() -> None:
    result = addressee_gate(AddresseeResult("42", 0.9, "at"), bot_ids=("42",), mood_label="cold")

    assert result is False


def test_addressee_gate_reply_to_bot_passes_without_target() -> None:
    result = addressee_gate(AddresseeResult(None, 0.0, "none"), bot_ids=("42",), mood_label="cold", reply_to_bot=True)

    assert result is False


async def test_cold_non_self_short_circuits_no_reply_without_api() -> None:
    called = False

    async def api_call(request: LLMRequest) -> dict:
        nonlocal called
        called = True
        return {"text": '{"decision":"reply"}'}

    decision = await BinaryPlanner().plan(
        BinaryPlannerFeatures(current_text="你们聊", addressee_id="100", bot_id="42", mood_label="cold"),
        api_call=api_call,
    )

    assert decision.action == "no_reply"
    assert decision.parse_mode == "gate"
    assert decision.reason == "cold_not_self"
    assert called is False


async def test_cold_unknown_addressee_short_circuits_no_reply() -> None:
    counter = NoReplyCounter()

    async def api_call(request: LLMRequest) -> dict:
        return {"text": '{"decision":"reply"}'}

    decision = await BinaryPlanner(no_reply_counter=counter).plan(
        BinaryPlannerFeatures(current_text="大家看看", bot_id="42", mood_label="cold"),
        api_call=api_call,
    )

    assert decision.action == "no_reply"
    assert counter.consecutive == 1


async def test_cold_self_calls_api() -> None:
    captured: list[LLMRequest] = []

    async def api_call(request: LLMRequest) -> dict:
        captured.append(request)
        return {"text": '{"reasoning":"明确叫 bot","decision":"reply","confidence":0.8,"reason":"self"}'}

    decision = await BinaryPlanner().plan(
        BinaryPlannerFeatures(current_text="@bot 在吗", addressee_id="42", bot_id="42", mood_label="cold"),
        api_call=api_call,
    )

    assert decision.action == "reply"
    assert captured[0].task == "reply_gate"


def test_stranger_normalizes_register_to_neutral_in_request() -> None:
    request = build_binary_planner_request(
        BinaryPlannerFeatures(current_text="你好", register_label="playful", affection_stage="stranger")
    )

    payload = request.user_messages[0]["content"]
    assert '"register_label": "neutral"' in payload
    assert '"affection_stage"' not in payload
    assert '"mood_label"' not in payload


async def test_cold_self_cancel_path_propagates_without_counter_update() -> None:
    counter = NoReplyCounter()

    async def api_call(request: LLMRequest) -> dict:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await BinaryPlanner(no_reply_counter=counter).plan(
            BinaryPlannerFeatures(current_text="@bot", addressee_id="42", bot_id="42", mood_label="cold"),
            api_call=api_call,
        )

    assert counter.consecutive == 0
