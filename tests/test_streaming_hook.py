from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kernel.config import ReplySegmentationConfig, ResolvedHumanization
from services.llm.client import LLMClient, call_api
from services.llm.llm_request import LLMRequest
from services.llm.prompt_builder import PromptBuilder
from services.llm.provider import ToolUse
from services.llm.providers.anthropic import AnthropicProvider
from services.llm.providers.deepseek import DeepSeekProvider
from services.llm.providers.openai import OpenAIProvider
from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot, PersonaRuntime
from services.tools.base import Tool
from services.tools.context import ToolContext
from services.tools.registry import ToolRegistry


def _prompt(persona_runtime: PersonaRuntime) -> PromptBuilder:
    return PromptBuilder(persona_runtime=persona_runtime)


def _make_sse_lines(*events: str) -> list[bytes]:
    return [f"data: {event}\n".encode() for event in events]


def _mock_session(sse_lines: list[bytes]) -> MagicMock:
    async def _aiter():
        for line in sse_lines:
            yield line

    resp = AsyncMock()
    resp.status = 200
    resp.raise_for_status = MagicMock()
    resp.content.__aiter__ = lambda self: _aiter().__aiter__()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.post.return_value = ctx
    return session


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


class _StaticTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} description"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        del ctx, kwargs
        return "ok"


class _CapturingTool(_StaticTool):
    def __init__(self, name: str, captured: list[dict[str, Any]]) -> None:
        super().__init__(name)
        self._captured = captured

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        del kwargs
        self._captured.append(dict(ctx.extra))
        return "captured"


def test_providers_extract_visible_text_delta() -> None:
    assert DeepSeekProvider("http://fake", "sk").extract_text_delta(
        'data: {"choices":[{"delta":{"content":"你好"}}]}',
    ) == "你好"
    assert OpenAIProvider("http://fake", "sk").extract_text_delta(
        'data: {"choices":[{"delta":{"content":" there"}}]}',
    ) == " there"
    assert AnthropicProvider("http://fake", "sk").extract_text_delta(
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}',
    ) == "Hi"
    assert DeepSeekProvider("http://fake", "sk").extract_text_delta(
        'data: {"choices":[{"delta":{"reasoning_content":"hidden"}}]}',
    ) == ""


async def test_call_api_invokes_text_delta_callback() -> None:
    events = [
        json.dumps({"choices": [{"delta": {"content": "Hi"}}]}),
        json.dumps({"choices": [{"delta": {"content": " there"}}], "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 3,
        }}),
        "[DONE]",
    ]
    seen: list[str] = []

    async def _on_delta(delta: str) -> None:
        seen.append(delta)

    result = await call_api(
        _mock_session(_make_sse_lines(*events)),
        "http://fake/v1",
        "sk-test",
        "openai-model",
        [{"type": "text", "text": "system"}],
        [{"role": "user", "content": "hi"}],
        api_format="openai",
        on_text_delta=_on_delta,
    )

    assert seen == ["Hi", " there"]
    assert result["text"] == "Hi there"


async def test_dispatch_call_passes_text_delta_callback(persona_runtime: PersonaRuntime) -> None:
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        thinker_enabled=False,
    )
    seen: list[str] = []

    async def _fake_call_api(*args: Any, **kwargs: Any) -> dict[str, Any]:
        await kwargs["on_text_delta"]("增量")
        return _result("增量")

    async def _on_delta(delta: str) -> None:
        seen.append(delta)

    try:
        with patch("services.llm.client.call_api", side_effect=_fake_call_api):
            result = await client._call([], [], on_text_delta=_on_delta)
    finally:
        await client.close()

    assert seen == ["增量"]
    assert result["text"] == "增量"


async def test_stream_with_segments_cleans_buffer_on_cancel(persona_runtime: PersonaRuntime) -> None:
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        thinker_enabled=False,
    )
    sent: list[str] = []
    cancel_seen: list[bool] = []

    class TrackingSegmenter:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.buffer = ""

        def push(self, chunk: str) -> list[str]:
            self.buffer += chunk
            return []

        def finish(self) -> list[str]:
            if not self.buffer:
                return []
            text = self.buffer
            self.buffer = ""
            return [text]

        def cancel(self) -> list[str]:
            cancel_seen.append(True)
            self.buffer = ""
            return []

    async def _on_segment(segment: str) -> bool:
        sent.append(segment)
        return True

    async def _cancelled_call(request: LLMRequest, *, on_text_delta: Any = None) -> dict[str, Any]:
        await on_text_delta("未完成的旧内容")
        raise asyncio.CancelledError

    async def _successful_call(request: LLMRequest, *, on_text_delta: Any = None) -> dict[str, Any]:
        await on_text_delta("新的内容。")
        return _result("新的内容。")

    request = LLMRequest(task="main", static_blocks=[], user_messages=[])
    try:
        with (
            patch("services.llm.client.StreamingSegmenter", TrackingSegmenter),
            patch.object(client, "_call", side_effect=_cancelled_call),
            pytest.raises(asyncio.CancelledError),
        ):
            await client._stream_with_segments(
                request,
                on_segment=_on_segment,
                session_id="group_1",
                group_id="1",
                user_id="2",
                turn_id="t1",
            )
        with (
            patch("services.llm.client.StreamingSegmenter", TrackingSegmenter),
            patch.object(client, "_call", side_effect=_successful_call),
        ):
            _result_payload, streamed = await client._stream_with_segments(
                request,
                on_segment=_on_segment,
                session_id="group_1",
                group_id="1",
                user_id="2",
                turn_id="t2",
            )
    finally:
        await client.close()

    assert cancel_seen == [True]
    assert sent == ["新的内容。"]
    assert streamed == ["新的内容。"]


async def test_chat_streaming_sends_segments_and_returns_empty_to_avoid_duplicate(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    sent: list[str] = []

    async def _on_segment(segment: str) -> bool:
        sent.append(segment)
        return True

    async def _fake_call_api(*args: Any, **kwargs: Any) -> dict[str, Any]:
        await kwargs["on_text_delta"]("第一段很长很长。")
        await kwargs["on_text_delta"]("第二段也很长。")
        return _result("第一段很长很长。第二段也很长。")

    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        group_timeline=timeline,
        thinker_enabled=False,
        humanization_resolver=lambda group_id: ResolvedHumanization(
            streaming_segment_enabled=True,
            disable_natural_split=True,
        ),
    )
    try:
        with patch("services.llm.client.call_api", side_effect=_fake_call_api):
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
    assert sent == ["第一段很长很长。", "第二段也很长。"]
    assert timeline.get_turns("123")[-1]["content"] == "第一段很长很长。\n第二段也很长。"


async def test_balanced_long_reply_with_business_tools_streams_segments(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    sent: list[str] = []
    tools = ToolRegistry()
    tools.register(_StaticTool("send_sticker"))

    async def _on_segment(segment: str) -> bool:
        sent.append(segment)
        return True

    async def _fake_call_api(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs.get("on_text_delta") is not None
        await kwargs["on_text_delta"]("第一段很长很长。")
        await kwargs["on_text_delta"]("第二段也很长。")
        return _result("第一段很长很长。第二段也很长。")

    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=tools,
        group_timeline=timeline,
        thinker_enabled=False,
        humanization_resolver=lambda group_id: ResolvedHumanization(
            streaming_segment_enabled=True,
            disable_natural_split=True,
        ),
    )
    try:
        with patch("services.llm.client.call_api", side_effect=_fake_call_api):
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
    assert sent == ["第一段很长很长。", "第二段也很长。"]
    assert timeline.get_turns("123")[-1]["content"] == "第一段很长很长。\n第二段也很长。"


async def test_tool_execution_receives_resolved_humanization_context(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    captured: list[dict[str, Any]] = []
    tools = ToolRegistry()
    tools.register(_CapturingTool("poke_user", captured))
    resolved = ResolvedHumanization(qq_interactions_poke_outbound_enabled=True)

    responses = [
        {
            **_result(""),
            "tool_uses": [ToolUse(id="tool_1", name="poke_user", input={"user_id": "222"})],
        },
        _result("工具执行完毕。"),
    ]

    async def _fake_call_api(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return responses.pop(0)

    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=tools,
        group_timeline=timeline,
        thinker_enabled=False,
        humanization_resolver=lambda group_id: resolved,
    )
    try:
        with patch("services.llm.client.call_api", side_effect=_fake_call_api):
            reply = await client.chat(
                session_id="group_123",
                user_id="111",
                user_content="hello",
                identity=identity_snapshot,
                group_id="123",
                force_reply=True,
            )
    finally:
        await client.close()

    assert reply in {"工具执行完毕", "工具执行完毕。"}
    assert len(captured) == 1
    assert captured[0]["resolved_humanization"] is resolved
    assert captured[0]["humanization"] is resolved


async def test_streaming_disabled_fallback_uses_natural_split(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    sent: list[str] = []
    tools = ToolRegistry()
    tools.register(_StaticTool("lookup_cards"))
    text = (
        "甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲"
        "甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲。"
        "乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙"
        "乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙乙。"
    )

    async def _on_segment(segment: str) -> bool:
        sent.append(segment)
        return True

    async def _fake_call_api(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs.get("on_text_delta") is None
        return _result(text)

    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=tools,
        group_timeline=timeline,
        thinker_enabled=False,
        humanization_resolver=lambda group_id: ResolvedHumanization(
            streaming_segment_enabled=True,
            disable_natural_split=True,
        ),
        reply_segmentation_config=ReplySegmentationConfig(
            natural_split_enabled=True,
            max_segment_chars=12,
            inter_segment_delay_s=0.0,
        ),
    )
    try:
        with (
            patch("services.llm.client.call_api", side_effect=_fake_call_api),
            patch("services.llm.client.asyncio.sleep", new_callable=AsyncMock),
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

    assert len(sent) >= 1
    assert reply
    assert "\n".join([*sent, reply]) == timeline.get_turns("123")[-1]["content"]
    full_reply = timeline.get_turns("123")[-1]["content"]
    assert full_reply.replace("\n", "").replace("。", "") == text.replace("。", "")


async def test_chat_balanced_streaming_handles_quote_anchor(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    sent: list[str] = []

    async def _on_segment(segment: str) -> bool:
        sent.append(segment)
        return True

    async def _fake_call_api(*args: Any, **kwargs: Any) -> dict[str, Any]:
        await kwargs["on_text_delta"]('<quote msg_id="42"/>第一段很长很长。')
        await kwargs["on_text_delta"]("第二段也很长。")
        return _result('<quote msg_id="42"/>第一段很长很长。第二段也很长。')

    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        group_timeline=timeline,
        thinker_enabled=False,
        humanization_rewrite_threshold=0.4,
        humanization_resolver=lambda group_id: ResolvedHumanization(
            streaming_segment_enabled=True,
            pause_then_extend_enabled=True,
            disable_natural_split=True,
            qq_interactions_quote_reply_enabled=True,
        ),
    )
    try:
        with patch("services.llm.client.call_api", side_effect=_fake_call_api):
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
    assert sent == ["[CQ:reply,id=42]第一段很长很长。", "第二段也很长。"]
    assert timeline.get_turns("123")[-1]["content"] == "[CQ:reply,id=42]第一段很长很长。\n第二段也很长。"


async def test_quote_reply_disabled_strips_cq_reply_on_non_streaming_path(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    sent: list[str] = []
    tools = ToolRegistry()
    tools.register(_StaticTool("lookup_cards"))

    async def _on_segment(segment: str) -> bool:
        sent.append(segment)
        return True

    async def _fake_call_api(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs.get("on_text_delta") is None
        return _result("[CQ:reply,id=42]好的我接这句！")

    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=tools,
        group_timeline=timeline,
        thinker_enabled=False,
        humanization_resolver=lambda group_id: ResolvedHumanization(
            streaming_segment_enabled=True,
            qq_interactions_quote_reply_enabled=False,
        ),
    )
    try:
        with patch("services.llm.client.call_api", side_effect=_fake_call_api):
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

    assert sent == []
    assert reply == "好的我接这句！"
    assert "[CQ:reply" not in timeline.get_turns("123")[-1]["content"]


async def test_quote_reply_disabled_strips_cq_reply_on_streaming_path(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    timeline = GroupTimeline()
    sent: list[str] = []

    async def _on_segment(segment: str) -> bool:
        sent.append(segment)
        return True

    async def _fake_call_api(*args: Any, **kwargs: Any) -> dict[str, Any]:
        await kwargs["on_text_delta"]("[CQ:reply,id=42]第一段很长很长。")
        return _result("[CQ:reply,id=42]第一段很长很长。")

    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        group_timeline=timeline,
        thinker_enabled=False,
        humanization_resolver=lambda group_id: ResolvedHumanization(
            streaming_segment_enabled=True,
            qq_interactions_quote_reply_enabled=False,
        ),
    )
    try:
        with patch("services.llm.client.call_api", side_effect=_fake_call_api):
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
    assert sent == ["第一段很长很长。"]
    assert timeline.get_turns("123")[-1]["content"] == "第一段很长很长。"


async def test_stream_with_segments_falls_back_to_result_text_without_deltas(persona_runtime: PersonaRuntime) -> None:
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=_prompt(persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        thinker_enabled=False,
    )
    sent: list[str] = []

    async def _on_segment(segment: str) -> bool:
        sent.append(segment)
        return True

    async def _call_without_delta(request: LLMRequest, *, on_text_delta: Any = None) -> dict[str, Any]:
        del request, on_text_delta
        return _result("最终文本很长很长。")

    try:
        with patch.object(client, "_call", side_effect=_call_without_delta):
            _result_payload, streamed = await client._stream_with_segments(
                LLMRequest(task="main", static_blocks=[], user_messages=[]),
                on_segment=_on_segment,
                session_id="group_1",
                group_id="1",
                user_id="2",
                turn_id="t1",
            )
    finally:
        await client.close()

    assert sent == ["最终文本很长很长。"]
    assert streamed == ["最终文本很长很长。"]
