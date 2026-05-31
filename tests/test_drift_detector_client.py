from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.memory.short_term import ShortTermMemory
from services.persona import IdentitySnapshot, PersonaRuntime
from services.tools.registry import ToolRegistry


def _result(text: str) -> dict[str, Any]:
    return {
        "text": text,
        "tool_uses": [],
        "input_tokens": 64,
        "output_tokens": 16,
        "cache_read": 0,
        "cache_create": 0,
    }


def _normalize_reply(text: str | None) -> str:
    return str(text or "").replace("\n", "").replace(" ", "").replace("，", "").rstrip("。")


async def _client(persona_runtime: PersonaRuntime) -> LLMClient:
    return LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        thinker_enabled=False,
        persona_drift_config=SimpleNamespace(
            enabled=True,
            lambda_ewma=0.3,
            theta_repair=0.6,
            theta_block=0.95,
            repair_max_retries=1,
        ),
    )


@pytest.mark.asyncio
async def test_chat_repairs_persona_drift_before_visible_guardrails(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    client = await _client(persona_runtime)
    call_count = 0

    async def _fake_call_api(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _result("我是凤笑梦，WxS 的成员，现在向你说明设定。")
        return _result("别这么正式啦，我直接说重点。")

    try:
        with patch("services.llm.client.call_api", new=_fake_call_api):
            reply = await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="hello",
                identity=identity_snapshot,
            )
    finally:
        await client.close()

    assert call_count == 2
    assert _normalize_reply(reply) == "别这么正式啦我直接说重点"
