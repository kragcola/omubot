from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from kernel.config import ResolvedHumanization
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.llm.segmentation import ReplySegmentationConfig
from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot, PersonaRuntime
from services.tools.registry import ToolRegistry


def _prompt(persona_runtime: PersonaRuntime) -> PromptBuilder:
    return PromptBuilder(persona_runtime=persona_runtime)


def _result(text: str) -> dict[str, Any]:
    return {
        "text": text,
        "tool_uses": [],
        "input_tokens": 120,
        "output_tokens": 20,
        "cache_read": 0,
        "cache_create": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 120,
        "reasoning_replay_tokens": 0,
    }


async def _make_client(
    timeline: GroupTimeline,
    persona_runtime: PersonaRuntime,
    *,
    humanization: ResolvedHumanization | None = None,
    reply_segmentation_config: ReplySegmentationConfig | None = None,
) -> LLMClient:
    return LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        group_timeline=timeline,
        thinker_enabled=False,
        reply_segmentation_config=reply_segmentation_config,
        humanization_resolver=lambda group_id: humanization or ResolvedHumanization(),
    )


async def test_maybe_extend_feature_off_does_not_call_llm(persona_runtime: PersonaRuntime) -> None:
    timeline = GroupTimeline()
    client = await _make_client(timeline, persona_runtime)
    sent: list[str] = []

    async def _on_segment(segment: str) -> None:
        sent.append(segment)

    try:
        with patch.object(client, "_call", new_callable=AsyncMock) as call:
            emitted = await client._maybe_extend(
                last_reply="我先说第一层，不过，",
                system_blocks=[],
                messages=[],
                session_id="group_123",
                group_id="123",
                user_id="111",
                turn_id="t1",
                humanization=ResolvedHumanization(pause_then_extend_enabled=False),
                on_segment=_on_segment,
            )
    finally:
        await client.close()

    assert emitted == []
    assert sent == []
    call.assert_not_awaited()


async def test_chat_pause_extend_sends_initial_reply_then_extension(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    sent: list[str] = []
    usage_rows: list[dict[str, Any]] = []
    client = await _make_client(timeline, persona_runtime,
        humanization=ResolvedHumanization(
            pause_then_extend_enabled=True,
            disable_natural_split=True,
        ),
        reply_segmentation_config=ReplySegmentationConfig(natural_split_enabled=False),
    )

    async def _on_segment(segment: str) -> None:
        sent.append(segment)

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
                    _result("我先说第一层，不过，"),
                    _result("补一句就这个。"),
                ],
            ) as call_api,
        ):
            reply = await client.chat(
                session_id="group_123",
                user_id="111",
                user_content="hello",
                identity=identity_snapshot,
                group_id="123",
                on_segment=_on_segment,
                force_reply=True,
            )
    finally:
        await client.close()

    assert reply == ""
    assert sent == ["我先说第一层，不过，", "补一句就这个。"]
    assert call_api.await_count == 2
    assert [turn["content"] for turn in timeline.get_turns("123")[-2:]] == [
        "我先说第一层，不过，",
        "补一句就这个。",
    ]
    assert "proactive_extend" in {row["call_type"] for row in usage_rows}


async def test_maybe_extend_user_reply_during_wait_drops_extension(persona_runtime: PersonaRuntime) -> None:
    timeline = GroupTimeline()
    client = await _make_client(timeline, persona_runtime)
    sent: list[str] = []

    async def _on_segment(segment: str) -> None:
        sent.append(segment)

    async def _sleep_then_user_reply(_delay: float) -> None:
        timeline.add("123", role="user", content="我插一句", speaker="user(222)")

    try:
        with (
            patch("services.llm.client.asyncio.sleep", side_effect=_sleep_then_user_reply),
            patch.object(client, "_call", new_callable=AsyncMock) as call,
        ):
            emitted = await client._maybe_extend(
                last_reply="我先说第一层，不过，",
                system_blocks=[],
                messages=[],
                session_id="group_123",
                group_id="123",
                user_id="111",
                turn_id="t1",
                humanization=ResolvedHumanization(pause_then_extend_enabled=True),
                on_segment=_on_segment,
            )
    finally:
        await client.close()

    assert emitted == []
    assert sent == []
    call.assert_not_awaited()
    assert len(timeline.get_pending("123")) == 1
    assert list(timeline.get_turns("123")) == []


async def test_pause_extend_timing_after_last_segment(persona_runtime: PersonaRuntime) -> None:
    timeline = GroupTimeline()
    client = await _make_client(timeline, persona_runtime)
    sleeps: list[float] = []
    sent: list[str] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)

    async def _on_segment(segment: str) -> None:
        sent.append(segment)

    try:
        with (
            patch("services.llm.client.time.monotonic", side_effect=[11.0, 11.0]),
            patch("services.llm.client.asyncio.sleep", side_effect=_sleep),
            patch.object(client, "_call", new_callable=AsyncMock, return_value=_result("补一句就这个。")),
            patch.object(client, "_record_pause_extend_trace", new_callable=AsyncMock),
        ):
            emitted = await client._maybe_extend(
                last_reply="我先说第一层，不过，",
                system_blocks=[],
                messages=[],
                session_id="group_123",
                group_id="123",
                user_id="111",
                turn_id="t1",
                humanization=ResolvedHumanization(pause_then_extend_enabled=True),
                on_segment=_on_segment,
                last_segment_emitted_at=10.0,
            )
    finally:
        await client.close()

    assert sleeps == pytest.approx([1.2])
    assert emitted == ["补一句就这个。"]
    assert sent == ["补一句就这个。"]


async def test_maybe_extend_cancel_during_wait_re_raises_without_extension(persona_runtime: PersonaRuntime) -> None:
    timeline = GroupTimeline()
    client = await _make_client(timeline, persona_runtime)
    sent: list[str] = []

    async def _on_segment(segment: str) -> None:
        sent.append(segment)

    try:
        with (
            patch("services.llm.client.asyncio.sleep", new_callable=AsyncMock, side_effect=asyncio.CancelledError),
            patch.object(client, "_call", new_callable=AsyncMock) as call,
            pytest.raises(asyncio.CancelledError),
        ):
            await client._maybe_extend(
                last_reply="我先说第一层，不过，",
                system_blocks=[],
                messages=[],
                session_id="group_123",
                group_id="123",
                user_id="111",
                turn_id="t1",
                humanization=ResolvedHumanization(pause_then_extend_enabled=True),
                on_segment=_on_segment,
            )
    finally:
        await client.close()

    await asyncio.sleep(0)
    assert sent == []
    call.assert_not_awaited()
    assert list(timeline.get_turns("123")) == []
