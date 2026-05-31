from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from kernel.config import GroupConfig, ReplySegmentationConfig, ResolvedHumanization
from kernel.types import TriggerContext
from services.llm.arbiter import InterruptionResult
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.llm.segmentation import ReplySegmentPlan
from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot
from services.scheduler import GroupChatScheduler, _GroupSlot
from services.tools.registry import ToolRegistry


class _FakeRuntime:
    def identity_snapshot(self) -> IdentitySnapshot:
        return IdentitySnapshot(id="test", name="测试", personality="测试", proactive="ok")


class _PromptRuntime:
    @property
    def static_text(self) -> str:
        return "【persona】"

    def identity_snapshot(self) -> IdentitySnapshot:
        return IdentitySnapshot(id="test", name="测试", personality="测试", proactive="ok")

    def block_for(self, module_id: str) -> Any:
        del module_id
        return SimpleNamespace(text="")

    def group_profile_text(self, group_id: str | None) -> str:
        del group_id
        return ""


class _FakeArbiter:
    def __init__(self, result: InterruptionResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def judge_interruption(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _SegmentLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def chat(self, **kwargs) -> str | None:  # type: ignore[override]
        self.calls.append(kwargs)
        on_segment = kwargs["on_segment"]
        should_continue = await on_segment("第一段。")
        if not should_continue:
            return ""
        await asyncio.sleep(0.35)
        should_continue = await on_segment("第二段。")
        if not should_continue:
            return ""
        return ""


class _BotStub:
    def __init__(self, *, on_send: Any | None = None) -> None:
        self.sent: list[dict[str, object]] = []
        self._on_send = on_send

    async def send_group_msg(self, *, group_id: int, message: object) -> None:
        self.sent.append({"group_id": group_id, "message": message})
        if self._on_send is not None:
            await self._on_send(group_id=group_id, message=message, send_count=len(self.sent))


def _group_config() -> GroupConfig:
    return GroupConfig(talk_value=1.0, planner_smooth=0, batch_size=100)


def _make_client(
    timeline: GroupTimeline,
    *,
    pause_then_extend_enabled: bool = True,
) -> LLMClient:
    return LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=_PromptRuntime()),  # type: ignore[arg-type]
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        group_timeline=timeline,
        thinker_enabled=False,
        humanization_resolver=lambda group_id: ResolvedHumanization(
            pause_then_extend_enabled=pause_then_extend_enabled,
            disable_natural_split=False,
        ),
        reply_segmentation_config=ReplySegmentationConfig(
            natural_split_enabled=True,
            max_segment_chars=4,
            inter_segment_delay_s=0.0,
        ),
    )


def _segmented_result(text: str) -> dict[str, object]:
    return {
        "text": text,
        "tool_uses": [],
        "input_tokens": 10,
        "output_tokens": 4,
        "cache_read": 0,
        "cache_create": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 10,
        "reasoning_replay_tokens": 0,
    }


def _arbiter_cfg() -> object:
    return type(
        "Cfg",
        (),
        {
            "enabled": True,
            "interruption_enabled": True,
            "runtime_groups": [],
        },
    )()


@pytest.mark.asyncio
async def test_segment_aborted_on_arbiter_abort() -> None:
    timeline = GroupTimeline()
    llm = _SegmentLLM()
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=timeline,
        persona_runtime=_FakeRuntime(),  # type: ignore[arg-type]
        group_config=_group_config(),
    )
    scheduler._arbiter_config = _arbiter_cfg()  # type: ignore[attr-defined]
    arbiter = _FakeArbiter(InterruptionResult(action="abort_unsent", reason="new info"))
    scheduler.set_arbiter(arbiter)  # type: ignore[arg-type]
    async def _inject(**kwargs) -> None:
        if kwargs["send_count"] == 1:
            timeline.add("123", role="user", content="补充一句", speaker="小明(42)")

    bot = _BotStub(on_send=_inject)
    scheduler._bot = bot  # type: ignore[attr-defined]
    slot = scheduler._slots.setdefault("123", _GroupSlot())  # type: ignore[attr-defined]
    slot.last_user_id = "42"
    timeline.add("123", role="user", content="原消息", speaker="小明(42)")

    await scheduler._do_chat("123", trigger=TriggerContext(reason="有人@了你", mode="at_mention"))  # type: ignore[attr-defined]

    assert len(arbiter.calls) == 1
    assert arbiter.calls[0]["new_messages"] == ["补充一句"]
    assert len(bot.sent) == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_segment_continues_on_arbiter_continue() -> None:
    timeline = GroupTimeline()
    llm = _SegmentLLM()
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=timeline,
        persona_runtime=_FakeRuntime(),  # type: ignore[arg-type]
        group_config=_group_config(),
    )
    scheduler._arbiter_config = _arbiter_cfg()  # type: ignore[attr-defined]
    arbiter = _FakeArbiter(InterruptionResult(action="continue", reason="ok"))
    scheduler.set_arbiter(arbiter)  # type: ignore[arg-type]
    async def _inject(**kwargs) -> None:
        if kwargs["send_count"] == 1:
            timeline.add("123", role="user", content="补充一句", speaker="小明(42)")

    bot = _BotStub(on_send=_inject)
    scheduler._bot = bot  # type: ignore[attr-defined]
    slot = scheduler._slots.setdefault("123", _GroupSlot())  # type: ignore[attr-defined]
    slot.last_user_id = "42"
    timeline.add("123", role="user", content="原消息", speaker="小明(42)")

    await scheduler._do_chat("123", trigger=TriggerContext(reason="有人@了你", mode="at_mention"))  # type: ignore[attr-defined]

    assert len(arbiter.calls) >= 1
    assert arbiter.calls[0]["new_messages"] == ["补充一句"]
    assert len(bot.sent) == 2
    await scheduler.close()


@pytest.mark.asyncio
async def test_segment_abort_writes_partial_timeline() -> None:
    timeline = GroupTimeline()
    client = _make_client(timeline)
    sent: list[str] = []

    async def _on_segment(segment: str) -> bool:
        sent.append(segment)
        return len(sent) < 2

    async def _fake_call(request, *, on_text_delta=None):
        del request, on_text_delta
        return _segmented_result("第一段。第二段。第三段。")

    try:
        client._call = _fake_call  # type: ignore[method-assign]
        with patch(
            "services.llm.client._reply_segment_plan",
            return_value=ReplySegmentPlan(
                segments=["第一段。", "第二段。", "第三段。"],
                raw_count=3,
                limit_status="none",
                inter_segment_delays=[0.0, 0.0],
            ),
        ):
            reply = await client.chat(
                session_id="group_123",
                user_id="111",
                user_content="hello",
                identity=IdentitySnapshot(id="x", name="测试", personality="测试"),
                group_id="123",
                on_segment=_on_segment,
                force_reply=True,
            )
    finally:
        await client.close()

    assert reply == ""
    assert sent == ["第一段。", "第二段。"]
    assert timeline.get_turns("123")[-1]["content"] == "第一段。\n第二段。"


@pytest.mark.asyncio
async def test_no_new_messages_skips_arbiter_call() -> None:
    timeline = GroupTimeline()
    llm = _SegmentLLM()
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=timeline,
        persona_runtime=_FakeRuntime(),  # type: ignore[arg-type]
        group_config=_group_config(),
    )
    scheduler._arbiter_config = _arbiter_cfg()  # type: ignore[attr-defined]
    arbiter = _FakeArbiter(InterruptionResult(action="abort_unsent", reason="new info"))
    scheduler.set_arbiter(arbiter)  # type: ignore[arg-type]
    bot = _BotStub()
    scheduler._bot = bot  # type: ignore[attr-defined]
    slot = scheduler._slots.setdefault("123", _GroupSlot())  # type: ignore[attr-defined]
    slot.last_user_id = "42"
    timeline.add("123", role="user", content="原消息", speaker="小明(42)")

    await scheduler._do_chat("123", trigger=TriggerContext(reason="有人@了你", mode="at_mention"))  # type: ignore[attr-defined]

    assert arbiter.calls == []
    assert len(bot.sent) == 2
    await scheduler.close()


@pytest.mark.asyncio
async def test_arbiter_b_timeout_continues() -> None:
    timeline = GroupTimeline()
    llm = _SegmentLLM()
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=timeline,
        persona_runtime=_FakeRuntime(),  # type: ignore[arg-type]
        group_config=_group_config(),
    )
    scheduler._arbiter_config = _arbiter_cfg()  # type: ignore[attr-defined]
    arbiter = _FakeArbiter(InterruptionResult(action="continue", fallback=True))
    scheduler.set_arbiter(arbiter)  # type: ignore[arg-type]
    async def _inject(**kwargs) -> None:
        if kwargs["send_count"] == 1:
            timeline.add("123", role="user", content="补充一句", speaker="小明(42)")

    bot = _BotStub(on_send=_inject)
    scheduler._bot = bot  # type: ignore[attr-defined]
    slot = scheduler._slots.setdefault("123", _GroupSlot())  # type: ignore[attr-defined]
    slot.last_user_id = "42"
    timeline.add("123", role="user", content="原消息", speaker="小明(42)")

    await scheduler._do_chat("123", trigger=TriggerContext(reason="有人@了你", mode="at_mention"))  # type: ignore[attr-defined]

    assert len(arbiter.calls) >= 1
    assert arbiter.calls[0]["new_messages"] == ["补充一句"]
    assert len(bot.sent) == 2
    await scheduler.close()
