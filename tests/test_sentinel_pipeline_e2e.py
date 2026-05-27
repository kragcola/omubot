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
from services.persona import IdentitySnapshot, PersonaRuntime
from services.tools.registry import ToolRegistry


def _prompt(persona_runtime: PersonaRuntime) -> PromptBuilder:
    return PromptBuilder(persona_runtime=persona_runtime)


def _normalize_visible(text: str | None) -> str:
    return str(text or "").replace("\n", "").replace(" ", "").rstrip("。")


def _result(text: str) -> dict[str, object]:
    return {
        "text": text,
        "tool_uses": [],
        "input_tokens": 120,
        "output_tokens": 20,
        "cache_read": 0,
        "cache_create": 0,
    }


async def _client(
    persona_runtime: PersonaRuntime,
    timeline: GroupTimeline,
    trace_store: BlockTraceStore,
    *,
    guardrail_enabled: bool,
) -> LLMClient:
    return LLMClient(
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
        sentinel_guardrail_config=SimpleNamespace(
            enabled=guardrail_enabled,
            dedup_ngram=2,
            dedup_threshold=0.2,
            dedup_action="rewrite",
            thinker_phrase_ngram=2,
            thinker_phrase_threshold=0.2,
            thinker_phrase_action="rewrite",
        ),
    )


@pytest.mark.asyncio
async def test_guardrail_pipeline_collects_all_a_cluster_hits_and_persists_metrics(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
    tmp_path,
) -> None:
    timeline = GroupTimeline()
    timeline.add("100", role="user", content="上一轮问题", speaker="user(100)")
    timeline.add("100", role="assistant", content="顺着这个问题轻轻接一下")
    trace_store = BlockTraceStore(tmp_path / "trace.db")
    await trace_store.init()
    client = await _client(persona_runtime, timeline, trace_store, guardrail_enabled=True)
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                return_value=_result("«img:1» 顺着这个问题轻轻接一下"),
            ),
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                retrieve_mode="skip",
                rewritten_query="",
                thought="顺着这个问题轻轻接一下",
                sticker=False,
                tone="日常",
                usage={},
            )
            reply = await client.chat(
                session_id="group_100",
                group_id="100",
                user_id="100",
                user_content="继续说",
                identity=identity_snapshot,
            )
            rows = await trace_store.list_humanization_metrics(limit=10)
            stats = await trace_store.humanization_metric_stats(group_id="100")
    finally:
        await client.close()
        await trace_store.close()

    assert _normalize_visible(reply) == "先不重复上一句啦"
    turns = list(timeline.get_turns("100"))
    assert turns[-1]["role"] == "assistant"
    assert _normalize_visible(str(turns[-1]["content"])) == "先不重复上一句啦"
    assert len(rows) == 1
    assert rows[0]["metadata"]["near_duplicate_hits"] == 1
    assert rows[0]["metadata"]["near_duplicate_rewritten"] == 1
    assert rows[0]["metadata"]["thinker_phrase_hits"] == 1
    assert rows[0]["metadata"]["sentinel_strip_hits"] == 1
    assert stats["near_duplicate_hits"] == 1
    assert stats["thinker_phrase_hits"] == 1
    assert stats["sentinel_strip_hits"] == 1


@pytest.mark.asyncio
async def test_guardrail_pipeline_disabled_short_circuits_and_keeps_original_reply(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
    tmp_path,
) -> None:
    timeline = GroupTimeline()
    timeline.add("100", role="user", content="上一轮问题", speaker="user(100)")
    timeline.add("100", role="assistant", content="顺着这个问题轻轻接一下")
    trace_store = BlockTraceStore(tmp_path / "trace-disabled.db")
    await trace_store.init()
    client = await _client(persona_runtime, timeline, trace_store, guardrail_enabled=False)
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                return_value=_result("«img:1» 顺着这个问题轻轻接一下"),
            ),
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                retrieve_mode="skip",
                rewritten_query="",
                thought="顺着这个问题轻轻接一下",
                sticker=False,
                tone="日常",
                usage={},
            )
            reply = await client.chat(
                session_id="group_100",
                group_id="100",
                user_id="100",
                user_content="继续说",
                identity=identity_snapshot,
            )
            rows = await trace_store.list_humanization_metrics(limit=10)
    finally:
        await client.close()
        await trace_store.close()

    assert _normalize_visible(reply) == "«img:1»顺着这个问题轻轻接一下"
    assert rows == []
