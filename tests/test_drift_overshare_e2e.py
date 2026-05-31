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


def _normalize_reply(text: str | None) -> str:
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
) -> LLMClient:
    return LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        group_timeline=timeline,
        thinker_enabled=False,
        runtime_state=create_humanization_state_bus(),
        budget_manager=SimpleNamespace(_store=trace_store),
        schedule_overshare_config=SimpleNamespace(
            enabled=True,
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
        persona_drift_config=SimpleNamespace(
            enabled=True,
            lambda_ewma=0.3,
            theta_repair=0.6,
            theta_block=0.95,
            repair_max_retries=0,
        ),
    )


@pytest.mark.asyncio
async def test_guardrail_pipeline_rewrites_persona_drift_and_schedule_overshare_together(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
    tmp_path,
) -> None:
    timeline = GroupTimeline()
    timeline.add("100", role="user", content="哈哈好搞笑", speaker="user(100)")
    trace_store = BlockTraceStore(tmp_path / "trace-drift-overshare.db")
    await trace_store.init()
    client = await _client(persona_runtime, timeline, trace_store)
    try:
        with patch(
            "services.llm.client.call_api",
            new_callable=AsyncMock,
            return_value=_result("我是凤笑梦，下午3:00还要排练呢。先聊这个。"),
        ):
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

    assert _normalize_reply(reply) == "先聊这个"
    assert len(rows) == 1
    metadata = rows[0]["metadata"]
    assert metadata["persona_drift_detector_action"] == "repair"
    assert metadata["persona_drift_hits"] == 1
    assert metadata["persona_drift_rewritten"] == 1
    assert metadata["schedule_overshare_hits"] == 1
    assert metadata["schedule_overshare_rewritten"] == 1
    assert stats["persona_drift_hits"] == 1
    assert stats["persona_drift_rewritten"] == 1
    assert stats["schedule_overshare_hits"] == 1
    assert stats["schedule_overshare_rewritten"] == 1
