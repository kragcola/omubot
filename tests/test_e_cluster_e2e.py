from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.block_trace.store import BlockTraceStore
from services.humanization import create_humanization_state_bus
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.name_registry import NameVariationRegistry
from services.persona import IdentitySnapshot, PersonaRuntime
from services.tools.registry import ToolRegistry


def _prompt(persona_runtime: PersonaRuntime) -> PromptBuilder:
    return PromptBuilder(persona_runtime=persona_runtime)


def _result(text: str) -> dict[str, object]:
    return {
        "text": text,
        "tool_uses": [],
        "input_tokens": 120,
        "output_tokens": 20,
        "cache_read": 0,
        "cache_create": 0,
    }


@pytest.mark.asyncio
async def test_e_cluster_addressee_hint_and_mention_post_processor(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
    tmp_path,
) -> None:
    timeline = GroupTimeline()
    timeline.add("100", role="user", content="hello", speaker="小明(1)")
    trace_store = BlockTraceStore(tmp_path / "trace-e.db")
    await trace_store.init()
    registry = NameVariationRegistry()
    registry.update_from_event("100", 1, "小明", "")
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        group_timeline=timeline,
        thinker_enabled=True,
        runtime_state=create_humanization_state_bus(),
        budget_manager=SimpleNamespace(_store=trace_store),
        addressee_hint_config=SimpleNamespace(enabled=True),
        mention_post_processor_config=SimpleNamespace(enabled=True, recent_speaker_limit=20),
        name_registry=registry,
    )
    client._bot_self_id = "999"
    try:
        captured_requests: list[object] = []
        hint = client._build_addressee_hint(
            group_id="100",
            trigger=SimpleNamespace(mode="at_mention", target_user_id="1", extra={}),
            fallback_user_id="1",
        )

        async def fake_call(request: object) -> dict[str, object]:
            captured_requests.append(request)
            return _result("嗨 @小明 你好")

        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch("services.llm.client.call_api", new_callable=AsyncMock),
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                topic_intent_label="闲聊",
                retrieve_mode="skip",
                rewritten_query="",
                thought="接一下",
                sticker=False,
                tone="日常",
                usage={},
            )
            client._call = fake_call  # type: ignore[method-assign]
            reply = await client.chat(
                session_id="group_100",
                group_id="100",
                user_id="1",
                user_content="继续",
                identity=identity_snapshot,
                trigger=SimpleNamespace(mode="at_mention", target_user_id="1", extra={}),
            )
    finally:
        await client.close()
        await trace_store.close()

    assert "[CQ:at,qq=1]" in str(reply or "")
    assert hint == "[当前你在回复：小明（QQ: 1）]"
    assert captured_requests
