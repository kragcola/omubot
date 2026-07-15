"""RED ABI and runtime evidence propagation contracts."""

from __future__ import annotations

from dataclasses import fields
from typing import Any
from unittest.mock import AsyncMock, patch

from kernel.types import ReplyContext, TriggerContext
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.memory.short_term import ShortTermMemory
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


def test_reply_context_abi_exposes_source_message_id() -> None:
    field_names = {field.name for field in fields(ReplyContext)}
    assert "source_message_id" in field_names, (
        "ReplyContext must carry the source QQ message_id for evidence-backed adapters"
    )


async def _chat_and_capture(
    *,
    persona_runtime: Any,
    identity_snapshot: Any,
    trigger: TriggerContext | None,
) -> ReplyContext:
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        thinker_enabled=False,
    )
    bus = _CaptureBus()
    client._bus = bus
    result = {
        "text": "这条回复用于验证证据 message_id 的透传。",
        "tool_uses": [],
        "input_tokens": 20,
        "output_tokens": 12,
        "cache_read": 0,
        "cache_create": 0,
    }
    try:
        with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=result):
            reply = await client.chat(
                session_id="group_200",
                group_id="200",
                user_id="100",
                user_content="请回答这个证据链测试问题。",
                identity=identity_snapshot,
                force_reply=True,
                trigger=trigger,
            )
        assert reply
        assert len(bus.post_reply) == 1
        return bus.post_reply[0]
    finally:
        await client.close()


async def test_trigger_target_message_id_reaches_post_reply_context(
    persona_runtime: Any,
    identity_snapshot: Any,
) -> None:
    reply_ctx = await _chat_and_capture(
        persona_runtime=persona_runtime,
        identity_snapshot=identity_snapshot,
        trigger=TriggerContext(
            reason="有人@了你",
            mode="at_mention",
            target_message_id=7001,
            target_user_id="100",
        ),
    )

    assert getattr(reply_ctx, "source_message_id", None) == 7001, (
        "LLMClient must copy TriggerContext.target_message_id into ReplyContext.source_message_id"
    )


async def test_missing_trigger_does_not_fabricate_source_message_id(
    persona_runtime: Any,
    identity_snapshot: Any,
) -> None:
    reply_ctx = await _chat_and_capture(
        persona_runtime=persona_runtime,
        identity_snapshot=identity_snapshot,
        trigger=None,
    )

    assert hasattr(reply_ctx, "source_message_id"), (
        "ReplyContext ABI must expose source_message_id even when evidence is absent"
    )
    assert getattr(reply_ctx, "source_message_id", object()) is None
