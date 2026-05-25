from __future__ import annotations

import asyncio

import pytest

from services.llm.llm_request import LLMRequest
from services.reply_planner import BinaryPlanner, BinaryPlannerFeatures, NoReplyCounter, no_reply_threshold


def test_no_reply_threshold_boundaries() -> None:
    assert no_reply_threshold(0) == 1
    assert no_reply_threshold(2) == 1
    assert no_reply_threshold(3) == 2
    assert no_reply_threshold(4) == 2
    assert no_reply_threshold(5) == 3


def test_no_reply_counter_observes_and_resets() -> None:
    counter = NoReplyCounter()

    counter.observe("no_reply")
    counter.observe("no_reply")
    counter.observe("no_reply")
    assert counter.consecutive == 3
    assert no_reply_threshold(counter.consecutive) == 2

    counter.observe("reply")
    assert counter.consecutive == 0
    assert no_reply_threshold(counter.consecutive) == 1


async def test_binary_planner_updates_counter_but_cancel_does_not() -> None:
    counter = NoReplyCounter()

    async def no_reply_api(request: LLMRequest) -> dict:
        return {"text": '{"reasoning":"旁支","decision":"no_reply","confidence":0.8,"reason":"idle"}'}

    await BinaryPlanner(no_reply_counter=counter).plan(
        BinaryPlannerFeatures(current_text="闲聊"),
        api_call=no_reply_api,
    )
    assert counter.consecutive == 1

    async def cancel_api(request: LLMRequest) -> dict:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await BinaryPlanner(no_reply_counter=counter).plan(
            BinaryPlannerFeatures(current_text="闲聊"),
            api_call=cancel_api,
        )
    assert counter.consecutive == 1
