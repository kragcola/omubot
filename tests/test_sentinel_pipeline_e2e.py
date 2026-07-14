from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from services.block_trace.store import BlockTraceStore
from services.humanization import create_humanization_state_bus
from services.llm.client import MAX_TOOL_ROUNDS, LLMClient, ToolUse
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
    overshare_enabled: bool = False,
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
        schedule_overshare_config=SimpleNamespace(
            enabled=overshare_enabled,
            cumulative_threshold=2,
            bypass_patterns=["几点", "什么时候", "日程", "安排", "忙不忙", "在干嘛", "在做什么", "干啥呢"],
            leak_patterns=[
                r"\d{1,2}[：:]\d{2}",
                "上午",
                "下午",
                "晚上",
                "排练",
                "吃饭",
                "休息",
                "上课",
                "午饭",
                "晚饭",
            ],
        ),
    )


class _RecordingVisibleReplyGuardrailStage:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def run(self, stage_input: Any) -> SimpleNamespace:
        self.calls.append(stage_input)
        return SimpleNamespace(
            reply="stage-cleaned",
            hits=(),
            metadata={"guardrail_stage": "recording-fake"},
            blocked=False,
        )


@pytest.mark.asyncio
async def test_normal_terminal_delegates_visible_reply_to_guardrail_stage(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
    tmp_path,
) -> None:
    timeline = GroupTimeline()
    timeline.add("100", role="user", content="previous user message", speaker="user(100)")
    timeline.add("100", role="assistant", content="previous assistant reply")
    trace_store = BlockTraceStore(tmp_path / "trace-stage-delegation.db")
    await trace_store.init()
    client = await _client(persona_runtime, timeline, trace_store, guardrail_enabled=True)
    stage = _RecordingVisibleReplyGuardrailStage()
    cast(Any, client)._visible_reply_guardrail_stage = stage

    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                return_value=_result("raw visible reply"),
            ),
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                topic_intent_label="闲聊",
                retrieve_mode="skip",
                rewritten_query="",
                thought="private thinker thought",
                unknown_terms=[],
                sticker=False,
                tone="日常",
                instruction_signal="none",
                light_kind="",
                reply_necessity="high",
                usage={},
            )
            reply = await client.chat(
                session_id="group_100",
                group_id="100",
                user_id="100",
                user_content="current user message",
                identity=identity_snapshot,
            )
    finally:
        await client.close()
        await trace_store.close()

    assert len(stage.calls) == 1
    stage_input = stage.calls[0]
    assert stage_input.reply == "raw visible reply"
    assert stage_input.enabled is True
    assert stage_input.thinker_thought == "private thinker thought"
    assert stage_input.last_assistant_text == "previous assistant reply"
    assert stage_input.user_message == "current user message"
    assert stage_input.session_count == 0
    assert stage_input.bot_name == persona_runtime.identity_snapshot().name
    assert reply == "stage-cleaned"


@pytest.mark.asyncio
async def test_tool_exhausted_terminal_delegates_visible_reply_to_guardrail_stage(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
    tmp_path,
) -> None:
    timeline = GroupTimeline()
    timeline.add("100", role="user", content="previous user message", speaker="user(100)")
    timeline.add("100", role="assistant", content="previous unrelated assistant text")
    trace_store = BlockTraceStore(tmp_path / "trace-stage-tool-exhausted.db")
    await trace_store.init()
    client = await _client(persona_runtime, timeline, trace_store, guardrail_enabled=True)
    stage = _RecordingVisibleReplyGuardrailStage()
    cast(Any, client)._visible_reply_guardrail_stage = stage
    tool_round_results = [
        {
            **_result(""),
            "tool_uses": [
                ToolUse(
                    id=f"tool-{round_index}",
                    name="unregistered_test_tool",
                    input={"round": round_index},
                )
            ],
        }
        for round_index in range(MAX_TOOL_ROUNDS)
    ]
    provider_results = [
        *tool_round_results,
        {
            **_result("raw tool-exhausted visible reply"),
            "tool_uses": [
                ToolUse(
                    id="tool-final",
                    name="unregistered_test_tool",
                    input={"round": "final"},
                )
            ],
        },
    ]

    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                side_effect=provider_results,
            ) as mock_call_api,
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                topic_intent_label="闲聊",
                retrieve_mode="skip",
                rewritten_query="",
                thought="private tool-exhausted thinker thought",
                unknown_terms=[],
                sticker=False,
                tone="日常",
                instruction_signal="none",
                light_kind="",
                reply_necessity="high",
                usage={},
            )
            reply = await client.chat(
                session_id="group_100",
                group_id="100",
                user_id="100",
                user_content="current tool-exhausted user message",
                identity=identity_snapshot,
            )
    finally:
        await client.close()
        await trace_store.close()

    assert mock_call_api.await_count == MAX_TOOL_ROUNDS + 1
    assert len(stage.calls) == 1
    stage_input = stage.calls[0]
    assert stage_input.reply == "raw tool-exhausted visible reply"
    assert stage_input.enabled is True
    assert stage_input.thinker_thought == "private tool-exhausted thinker thought"
    assert stage_input.last_assistant_text == "previous unrelated assistant text"
    assert stage_input.user_message == "current tool-exhausted user message"
    assert stage_input.session_count == 0
    assert stage_input.bot_name == persona_runtime.identity_snapshot().name
    assert reply == "stage-cleaned"


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


@pytest.mark.asyncio
async def test_guardrail_pipeline_records_schedule_overshare_metrics_and_rewrites_reply(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
    tmp_path,
) -> None:
    timeline = GroupTimeline()
    timeline.add("100", role="user", content="哈哈好搞笑", speaker="user(100)")
    trace_store = BlockTraceStore(tmp_path / "trace-overshare.db")
    await trace_store.init()
    client = await _client(
        persona_runtime,
        timeline,
        trace_store,
        guardrail_enabled=False,
        overshare_enabled=True,
    )
    try:
        with (
            patch("services.llm.thinker.think", new_callable=AsyncMock) as mock_think,
            patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                return_value=_result("对吧哈哈，我下午3:00还要排练呢。先聊这个。"),
            ),
        ):
            mock_think.return_value = SimpleNamespace(
                action="reply",
                retrieve_mode="skip",
                rewritten_query="",
                thought="",
                sticker=False,
                tone="日常",
                usage={},
            )
            reply = await client.chat(
                session_id="group_100",
                group_id="100",
                user_id="100",
                user_content="哈哈好搞笑",
                identity=identity_snapshot,
            )
            rows = await trace_store.list_humanization_metrics(limit=10)
            stats = await trace_store.humanization_metric_stats(group_id="100")
    finally:
        await client.close()
        await trace_store.close()

    assert _normalize_visible(reply) == "先聊这个"
    assert len(rows) == 1
    assert rows[0]["metadata"]["schedule_overshare_hits"] == 1
    assert rows[0]["metadata"]["schedule_overshare_rewritten"] == 1
    assert rows[0]["metadata"]["schedule_overshare_reason"] == "unsolicited_time_mention"
    assert stats["schedule_overshare_hits"] == 1
    assert stats["schedule_overshare_rewritten"] == 1
