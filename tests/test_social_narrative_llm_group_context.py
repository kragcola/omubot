"""RED contract for factual user text without duplicate group provider input."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from kernel.types import TriggerContext
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.tools.registry import ToolRegistry


class _CaptureBus:
    def __init__(self) -> None:
        self.post_reply: list[Any] = []

    async def fire_on_pre_prompt(self, prompt_ctx: Any) -> None:
        del prompt_ctx

    async def fire_on_post_reply(self, reply_ctx: Any) -> None:
        self.post_reply.append(reply_ctx)

    async def fire_on_thinker_decision(self, thinker_ctx: Any) -> None:
        del thinker_ctx


async def test_group_anchor_text_reaches_post_reply_without_transient_provider_duplicate(
    persona_runtime: Any,
    identity_snapshot: Any,
) -> None:
    anchor_text = "我们刚才一起把排练节奏理顺了"
    timeline = GroupTimeline()
    timeline.add(
        "200",
        role="user",
        speaker="当前成员(100)",
        content=anchor_text,
        message_id=7001,
    )
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        group_timeline=timeline,
        thinker_enabled=False,
    )
    bus = _CaptureBus()
    client._bus = bus
    provider_messages: list[dict[str, Any]] = []
    result = {
        "text": "先从最容易乱掉的那一段开始。",
        "tool_uses": [],
        "input_tokens": 20,
        "output_tokens": 12,
        "cache_read": 0,
        "cache_create": 0,
    }

    async def capture_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        provider_messages.extend(dict(message) for message in args[5])
        return result

    try:
        with patch(
            "services.llm.client.call_api",
            new_callable=AsyncMock,
            side_effect=capture_call,
        ):
            reply = await client.chat(
                session_id="group_200",
                group_id="200",
                user_id="100",
                user_content=anchor_text,
                identity=identity_snapshot,
                force_reply=True,
                trigger=TriggerContext(
                    reason="有人@了你",
                    mode="at_mention",
                    target_message_id=7001,
                    target_user_id="100",
                ),
            )
    finally:
        await client.close()

    assert reply
    assert len(bus.post_reply) == 1
    assert bus.post_reply[0].user_msg == anchor_text
    assert any(anchor_text in str(message.get("content", "")) for message in provider_messages)
    exact_transient_copies = [
        message
        for message in provider_messages
        if message.get("role") == "user" and message.get("content") == anchor_text
    ]
    assert exact_transient_copies == [], (
        "the pending timeline already carries the anchor text; group provider input "
        "must not append the same text again as a transient user message"
    )
