from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from kernel.config import ReplySegmentationConfig
from services.identity import Identity
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.llm.segmentation import ReplySegmentPlan
from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.tools.registry import ToolRegistry

_IDENTITY = Identity(id="t", name="Bot", personality="p")
_MOCK_RESULT = {
    "text": "reply text",
    "tool_uses": [],
    "input_tokens": 160,
    "output_tokens": 200,
    "cache_read": 50,
    "cache_create": 10,
}


def _prompt() -> PromptBuilder:
    prompt = PromptBuilder(instruction="test")
    prompt.build_static(_IDENTITY, bot_self_id="999")
    return prompt


async def test_chat_uses_reply_segment_plan_dynamic_delays() -> None:
    cfg = ReplySegmentationConfig(natural_split_enabled=True, inter_segment_delay_s=0.0)
    timeline = GroupTimeline()
    sent: list[str] = []
    sleeps: list[float] = []
    captured: dict[str, object] = {}

    async def _on_segment(seg: str) -> None:
        sent.append(seg)

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)

    def _plan(
        reply: str,
        cfg: Any,
        *,
        register: Any | None = None,
        slot_energy: float = 1.0,
    ) -> ReplySegmentPlan:
        captured["reply"] = reply
        captured["cfg"] = cfg
        captured["register"] = register
        captured["slot_energy"] = slot_energy
        return ReplySegmentPlan(
            segments=["第一段", "第二段", "第三段"],
            raw_count=3,
            limit_status="none",
            inter_segment_delays=[0.5, 1.25],
        )

    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        group_timeline=timeline,
        thinker_enabled=False,
        reply_segmentation_config=cfg,
    )
    try:
        with (
            patch(
                "services.llm.client.call_api",
                new_callable=AsyncMock,
                return_value={**_MOCK_RESULT, "text": "第一段！第二段！第三段。"},
            ),
            patch("services.llm.client._reply_segment_plan", side_effect=_plan),
            patch("services.llm.client.asyncio.sleep", side_effect=_sleep),
        ):
            result = await client.chat(
                session_id="group_12345",
                user_id="111",
                user_content="hello",
                identity=_IDENTITY,
                group_id="12345",
                on_segment=_on_segment,
            )
    finally:
        await client.close()

    assert sent == ["第一段", "第二段"]
    assert sleeps == [0.5, 1.25]
    assert result == "第三段"
    assert timeline.get_turns("12345")[-1]["content"] == "第一段\n第二段\n第三段"
    assert captured["cfg"] is cfg
    assert captured["register"] is None
    assert captured["slot_energy"] == 1.0
