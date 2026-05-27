from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from services.block_trace.store import BlockTraceStore
from services.humanization import LAST_METRICS_SLOT, create_humanization_state_bus
from services.identity import Identity
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.tools.registry import ToolRegistry

_IDENTITY = Identity(id="t", name="Bot", personality="p")


def _prompt() -> PromptBuilder:
    prompt = PromptBuilder(instruction="test")
    prompt.build_static(_IDENTITY, bot_self_id="999")
    return prompt


def _result(text: str) -> dict[str, Any]:
    return {
        "text": text,
        "tool_uses": [],
        "input_tokens": 120,
        "output_tokens": 20,
        "cache_read": 0,
        "cache_create": 0,
    }


def _normalize_reply(text: str | None) -> str:
    """Strip whitespace, commas, and trailing periods that natural_split may mutate."""
    if text is None:
        return ""
    return text.replace("\n", "").replace(" ", "").replace("，", "").rstrip("。")


async def _client(
    short_term: ShortTermMemory,
    *,
    rewrite_threshold: float = -1.0,
    runtime_state: object | None = None,
    budget_manager: object | None = None,
    timeline: GroupTimeline | None = None,
    runtime_groups: list[str] | None = None,
) -> LLMClient:
    return LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(),
        short_term=short_term,
        tools=ToolRegistry(),
        group_timeline=timeline,
        thinker_enabled=False,
        runtime_state=runtime_state,
        budget_manager=budget_manager,
        humanization_rewrite_threshold=rewrite_threshold,
        humanization_runtime_groups=runtime_groups,
    )


async def test_humanization_rewrite_default_off_does_not_score_or_call_twice() -> None:
    short_term = ShortTermMemory()
    runtime_state = create_humanization_state_bus()
    client = await _client(short_term, runtime_state=runtime_state)
    try:
        with patch(
            "services.llm.client.call_api",
            new_callable=AsyncMock,
            return_value=_result("作为一个AI，我会尽力解释——以下是答案。"),
        ) as mock_api:
            reply = await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="hello",
                identity=_IDENTITY,
            )
    finally:
        await client.close()

    assert _normalize_reply(reply) == _normalize_reply("作为一个AI，我会尽力解释——以下是答案。")
    assert mock_api.await_count == 1
    assert all("humanization.last_metrics" not in key for key in runtime_state.snapshot_all_for_trace())


async def test_humanization_rewrite_skips_non_gray_group() -> None:
    short_term = ShortTermMemory()
    runtime_state = create_humanization_state_bus()
    timeline = GroupTimeline()
    timeline.add("100", role="user", content="hello", speaker="user(100)")
    client = await _client(
        short_term,
        rewrite_threshold=0.95,
        runtime_state=runtime_state,
        timeline=timeline,
        runtime_groups=["200"],
    )
    try:
        with patch(
            "services.llm.client.call_api",
            new_callable=AsyncMock,
            return_value=_result("作为一个AI，我会尽力解释——以下是答案。"),
        ) as mock_api:
            reply = await client.chat(
                session_id="group_100",
                group_id="100",
                user_id="100",
                user_content="hello",
                identity=_IDENTITY,
            )
    finally:
        await client.close()

    assert _normalize_reply(reply) == _normalize_reply("作为一个AI，我会尽力解释——以下是答案。")
    assert mock_api.await_count == 1
    assert _normalize_reply(timeline.get_turns("100")[-1]["content"]) == _normalize_reply(
        "作为一个AI，我会尽力解释——以下是答案。",
    )
    assert all("humanization.last_metrics" not in key for key in runtime_state.snapshot_all_for_trace())


async def test_humanization_rewrite_low_score_runs_one_extra_round_and_persists_metric(
    tmp_path,
) -> None:
    short_term = ShortTermMemory()
    runtime_state = create_humanization_state_bus()
    trace_store = BlockTraceStore(tmp_path / "trace.db")
    await trace_store.init()
    budget_manager = SimpleNamespace(_store=trace_store)
    client = await _client(
        short_term,
        rewrite_threshold=0.95,
        runtime_state=runtime_state,
        budget_manager=budget_manager,
    )
    rows: list[dict[str, Any]] = []
    try:
        with patch(
            "services.llm.client.call_api",
            new_callable=AsyncMock,
            side_effect=[
                _result("作为一个AI，我会尽力解释——以下是答案。"),
                _result("我先按这个方向接一下。"),
            ],
        ) as mock_api:
            reply = await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="hello",
                identity=_IDENTITY,
            )
            rows = await trace_store.list_humanization_metrics(session_id="private_100")
    finally:
        await client.close()
        await trace_store.close()

    assert _normalize_reply(reply) == _normalize_reply("我先按这个方向接一下。")
    assert mock_api.await_count == 2
    last_content = short_term.get("private_100")[-1]["content"]
    assert isinstance(last_content, str)
    assert _normalize_reply(last_content) == _normalize_reply("我先按这个方向接一下。")

    trace = runtime_state.snapshot_all_for_trace()
    metric_items = [item for item in trace.values() if item["slot_id"] == LAST_METRICS_SLOT]
    assert len(metric_items) == 1
    assert metric_items[0]["value"]["meta"]["rewrite_applied"] is True

    assert len(rows) == 1
    assert rows[0]["metadata"]["rewrite_applied"] is True
    assert rows[0]["metadata"]["initial_score"] < 0.95


async def test_humanization_rewrite_cancel_path_does_not_write_assistant_or_metrics() -> None:
    short_term = ShortTermMemory()
    runtime_state = create_humanization_state_bus()
    client = await _client(short_term, rewrite_threshold=0.95, runtime_state=runtime_state)
    calls = 0

    async def _fake_api(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        if calls == 0:
            calls += 1
            return _result("作为一个AI，我会尽力解释——以下是答案。")
        await asyncio.sleep(60)
        return _result("我先按这个方向接一下。")

    try:
        with patch("services.llm.client.call_api", new=_fake_api):
            task = asyncio.create_task(
                client.chat(
                    session_id="private_100",
                    user_id="100",
                    user_content="hello",
                    identity=_IDENTITY,
                )
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await client.close()

    assert [msg["role"] for msg in short_term.get("private_100")] == ["user"]
    assert all("humanization.last_metrics" not in key for key in runtime_state.snapshot_all_for_trace())
