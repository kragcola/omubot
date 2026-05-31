from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from kernel.config import ResolvedHumanization
from services.block_trace.store import BlockTraceStore
from services.llm.client import LLMClient
from services.llm.plan_then_utter import PlanThenUtter
from services.llm.prompt_builder import PromptBuilder
from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot, PersonaRuntime
from services.tools.base import Tool
from services.tools.context import ToolContext
from services.tools.registry import ToolRegistry


def _prompt(persona_runtime: PersonaRuntime) -> PromptBuilder:
    return PromptBuilder(persona_runtime=persona_runtime)


def _result(text: str, *, input_tokens: int = 120, output_tokens: int = 20) -> dict[str, Any]:
    return {
        "text": text,
        "tool_uses": [],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read": 0,
        "cache_create": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": input_tokens,
        "reasoning_replay_tokens": 0,
    }


async def _make_client(
    timeline: GroupTimeline,
    persona_runtime: PersonaRuntime,
    *,
    humanization: ResolvedHumanization | None = None,
    tools: ToolRegistry | None = None,
    budget_manager: object | None = None,
) -> LLMClient:
    return LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=tools or ToolRegistry(),
        group_timeline=timeline,
        budget_manager=budget_manager,
        thinker_enabled=False,
        humanization_resolver=lambda group_id: humanization or ResolvedHumanization(),
    )


class _StaticTool(Tool):
    @property
    def name(self) -> str:
        return "lookup_cards"

    @property
    def description(self) -> str:
        return "lookup cards"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        return "ok"

    def to_openai_tool(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def test_parse_plan_accepts_json_dedupes_and_caps_to_three() -> None:
    planner = PlanThenUtter()

    outlines = planner.parse_plan('{"utterances":["先接住","再解释","再解释","最后补一句"]}')

    assert outlines == ("先接住", "再解释", "最后补一句")


def test_parse_plan_accepts_bullets_and_requires_at_least_two() -> None:
    planner = PlanThenUtter()

    assert planner.parse_plan("1. 先回应\n2. 再补充") == ("先回应", "再补充")
    assert planner.parse_plan("只有一条") == ()


def test_build_requests_keep_p6_9_token_caps_and_no_tools() -> None:
    planner = PlanThenUtter()
    plan = planner.build_plan_request(system_blocks=[], messages=[], user_id="1", group_id="2")
    utter = planner.build_utter_request(
        system_blocks=[],
        messages=[],
        user_id="1",
        group_id="2",
        plan_text="plan",
        outline="outline",
        utter_index=0,
        total_utters=2,
    )

    assert plan.max_tokens == 80
    assert utter.max_tokens == 150
    assert plan.tools is None
    assert utter.tools is None
    plan_hint = plan.static_blocks[-1]
    utter_hint = utter.static_blocks[-1]
    assert isinstance(plan_hint, dict)
    assert isinstance(utter_hint, dict)
    assert "plan_then_utter" in str(plan_hint["text"])
    assert "plan_then_utter" in str(utter_hint["text"])


async def test_chat_plan_then_utter_default_off_uses_main_path(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    sent: list[str] = []
    client = await _make_client(timeline, persona_runtime)

    async def _on_segment(segment: str) -> bool:
        sent.append(segment)
        return True

    try:
        with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_result("普通回复")) as call:
            reply = await client.chat(
                session_id="group_123",
                user_id="111",
                user_content="hello",
                identity=identity_snapshot,
                group_id="123",
                on_segment=_on_segment,
            )
    finally:
        await client.close()

    assert reply == "普通回复"
    assert sent == []
    assert call.await_count == 1


async def test_chat_plan_then_utter_sends_utterances_and_records_usage(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    sent: list[str] = []
    usage_rows: list[dict[str, Any]] = []
    client = await _make_client(
        timeline,
        persona_runtime,
        humanization=ResolvedHumanization(plan_then_utter_enabled=True, disable_natural_split=True),
    )

    async def _on_segment(segment: str) -> bool:
        sent.append(segment)
        return True

    def _record_usage(**kwargs: Any) -> None:
        usage_rows.append(kwargs)

    try:
        client._record_usage = _record_usage  # type: ignore[method-assign]
        with (
            patch("services.llm.client.asyncio.sleep", new_callable=AsyncMock, return_value=None),
            patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                side_effect=[
                    _result('{"utterances":["先接住","再补一句"]}', input_tokens=80, output_tokens=12),
                    _result("先接住这个点。", input_tokens=90, output_tokens=18),
                    _result("再补一句就够。", input_tokens=95, output_tokens=16),
                ],
            ) as call,
        ):
            reply = await client.chat(
                session_id="group_123",
                user_id="111",
                user_content="hello",
                identity=identity_snapshot,
                group_id="123",
                on_segment=_on_segment,
            )
    finally:
        await client.close()

    assert reply == ""
    assert sent == ["先接住这个点。", "再补一句就够。"]
    assert call.await_count == 3
    assert timeline.get_turns("123")[-1]["content"] == "先接住这个点。\n再补一句就够。"
    assert [row["call_type"] for row in usage_rows] == [
        "proactive_plan",
        "proactive_utter",
        "proactive_utter",
    ]


async def test_chat_plan_then_utter_writes_block_trace_parent_span(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
    tmp_path,
) -> None:
    store = BlockTraceStore(tmp_path / "trace.db")
    await store.init()
    timeline = GroupTimeline()
    client = await _make_client(
        timeline,
        persona_runtime,
        humanization=ResolvedHumanization(plan_then_utter_enabled=True),
        budget_manager=SimpleNamespace(_store=store),
    )

    async def _on_segment(_segment: str) -> bool:
        return True

    try:
        with (
            patch("services.llm.client.asyncio.sleep", new_callable=AsyncMock, return_value=None),
            patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                side_effect=[
                    _result('{"utterances":["先接住","再补一句"]}'),
                    _result("先接住这个点。"),
                    _result("再补一句就够。"),
                ],
            ),
        ):
            await client.chat(
                session_id="group_123",
                user_id="111",
                user_content="hello",
                identity=identity_snapshot,
                group_id="123",
                on_segment=_on_segment,
            )
        traces = await store.recent(limit=10)
    finally:
        await client.close()
        await store.close()

    p6_traces = [trace for trace in traces if trace.provider == "plan_then_utter"]
    assert {trace.task for trace in p6_traces} == {"proactive_plan", "proactive_utter"}
    parent_ids = {trace.metadata["parent_span_id"] for trace in p6_traces}
    assert len(parent_ids) == 1
    assert {trace.metadata["status"] for trace in p6_traces} >= {"planned", "emitted"}


async def test_chat_plan_then_utter_invalid_plan_falls_back_to_main_path(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    sent: list[str] = []
    usage_rows: list[dict[str, Any]] = []
    client = await _make_client(
        timeline,
        persona_runtime,
        humanization=ResolvedHumanization(plan_then_utter_enabled=True),
    )

    async def _on_segment(segment: str) -> bool:
        sent.append(segment)
        return True

    def _record_usage(**kwargs: Any) -> None:
        usage_rows.append(kwargs)

    try:
        client._record_usage = _record_usage  # type: ignore[method-assign]
        with patch(
            "services.llm.client.call_api",
            new_callable=AsyncMock,
            side_effect=[
                _result("只有一条"),
                _result("回到旧路径。"),
            ],
        ) as call:
            reply = await client.chat(
                session_id="group_123",
                user_id="111",
                user_content="hello",
                identity=identity_snapshot,
                group_id="123",
                on_segment=_on_segment,
            )
    finally:
        await client.close()

    assert reply in {"回到旧路径", "回到旧路径。"}
    assert sent == []
    assert call.await_count == 2
    assert [row["call_type"] for row in usage_rows] == ["proactive_plan", "proactive"]


async def test_chat_plan_then_utter_plan_cancel_re_raises_without_dirty_write(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    sent: list[str] = []
    usage_rows: list[dict[str, Any]] = []
    client = await _make_client(
        timeline,
        persona_runtime,
        humanization=ResolvedHumanization(plan_then_utter_enabled=True),
    )

    async def _on_segment(segment: str) -> bool:
        sent.append(segment)
        return True

    def _record_usage(**kwargs: Any) -> None:
        usage_rows.append(kwargs)

    async def _cancel(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise asyncio.CancelledError

    try:
        client._record_usage = _record_usage  # type: ignore[method-assign]
        with (
            patch("services.llm.client.call_api", side_effect=_cancel),
            pytest.raises(asyncio.CancelledError),
        ):
            await client.chat(
                session_id="group_123",
                user_id="111",
                user_content="hello",
                identity=identity_snapshot,
                group_id="123",
                on_segment=_on_segment,
            )
    finally:
        await client.close()

    await asyncio.sleep(0)
    assert sent == []
    assert usage_rows == []
    assert list(timeline.get_turns("123")) == []


async def test_chat_plan_then_utter_business_tool_blocks_pilot(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    registry = ToolRegistry()
    registry.register(_StaticTool())  # real business tool, not pass_turn
    usage_rows: list[dict[str, Any]] = []
    client = await _make_client(
        timeline,
        persona_runtime,
        humanization=ResolvedHumanization(plan_then_utter_enabled=True),
        tools=registry,
    )

    async def _on_segment(_segment: str) -> bool:
        return True

    def _record_usage(**kwargs: Any) -> None:
        usage_rows.append(kwargs)

    try:
        client._record_usage = _record_usage  # type: ignore[method-assign]
        with patch("services.llm.client.call_api", new_callable=AsyncMock, return_value=_result("普通回复")) as call:
            reply = await client.chat(
                session_id="group_123",
                user_id="111",
                user_content="hello",
                identity=identity_snapshot,
                group_id="123",
                on_segment=_on_segment,
            )
    finally:
        await client.close()

    assert reply == "普通回复"
    assert call.await_count == 1
    assert [row["call_type"] for row in usage_rows] == ["proactive"]
