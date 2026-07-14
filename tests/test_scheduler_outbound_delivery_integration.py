"""Scheduler ownership contract around the single-attempt delivery stage."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from kernel.config import GroupConfig
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot
from services.scheduler import GroupChatScheduler
from services.scheduler_pipeline.outbound_delivery import DeliveryStatus, OutboundDeliveryResult


class _Runtime:
    def identity_snapshot(self) -> IdentitySnapshot:
        return IdentitySnapshot(id="bot", name="bot", personality="p", proactive="on")


class _RecordingDelivery:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def deliver(self, request: Any) -> OutboundDeliveryResult:
        self.requests.append(request)
        return OutboundDeliveryResult(
            status=DeliveryStatus.SENT,
            elapsed_s=1.25,
            message_id=701,
        )


@pytest.mark.asyncio
async def test_scheduler_delegates_one_attempt_and_owns_sent_event() -> None:
    scheduler = GroupChatScheduler(
        llm=cast(Any, SimpleNamespace()),
        timeline=GroupTimeline(),
        persona_runtime=cast(Any, _Runtime()),
        group_config=GroupConfig(talk_value=1.0, planner_smooth=0.0),
    )
    bot = SimpleNamespace(self_id="1", send_group_msg=AsyncMock())
    scheduler.set_bot(cast(Any, bot))
    delivery = _RecordingDelivery()
    cast(Any, scheduler)._outbound_delivery = delivery
    sent_event = asyncio.Event()

    elapsed = await scheduler._send_to_group(
        "100",
        "typed payload",
        humanize="skip",
        target_user_id="200",
        sent_event=sent_event,
    )

    assert len(delivery.requests) == 1
    request = delivery.requests[0]
    assert request.group_id == "100"
    assert request.text == "typed payload"
    assert request.humanize == "skip"
    assert request.target_user_id == "200"
    assert request.actor_id == "1"
    assert request.humanization.group_id == "100"
    assert elapsed == 1.25
    assert sent_event.is_set() is True
    bot.send_group_msg.assert_not_awaited()
    await scheduler.close()
