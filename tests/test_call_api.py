"""Tests for _call_api SSE parsing."""

import json
from unittest.mock import AsyncMock, MagicMock

from services.llm.client import call_api


def _make_sse_lines(*events: str) -> list[bytes]:
    """Build raw SSE byte lines from event JSON strings."""
    lines: list[bytes] = []
    for event in events:
        lines.append(f"data: {event}\n".encode())
    return lines


def _mock_session(sse_lines: list[bytes]) -> MagicMock:
    """Create a mock aiohttp session whose post() yields given SSE lines."""

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


async def test_output_tokens_from_message_delta() -> None:
    """output_tokens should come from the last message_delta, not message_start."""
    events = [
        json.dumps({
            "type": "message_start",
            "message": {
                "usage": {"input_tokens": 100, "output_tokens": 1,
                           "cache_read_input_tokens": 50, "cache_creation_input_tokens": 10}
            },
        }),
        json.dumps({"type": "content_block_start", "content_block": {"type": "text", "text": ""}}),
        json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}),
        json.dumps({"type": "content_block_stop"}),
        json.dumps({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 42}}),
    ]

    session = _mock_session(_make_sse_lines(*events))
    result = await call_api(session, "http://fake", "sk-test", "model", [], [{"role": "user", "content": "hi"}])

    assert result["output_tokens"] == 42
    assert result["text"] == "Hello"
    assert result["input_tokens"] == 100 + 50 + 10  # total input
    assert result["cache_read"] == 50
    assert result["cache_create"] == 10


async def test_openai_profile_stream_parses_chat_completion_delta() -> None:
    events = [
        json.dumps({"choices": [{"delta": {"content": "Hi"}}]}),
        json.dumps({"choices": [{"delta": {"content": " there"}}], "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 3,
        }}),
        "[DONE]",
    ]

    session = _mock_session(_make_sse_lines(*events))
    result = await call_api(
        session,
        "http://fake/v1",
        "sk-test",
        "openai-model",
        [{"type": "text", "text": "system"}],
        [{"role": "user", "content": "hi"}],
        api_format="openai",
    )

    assert result["text"] == "Hi there"
    assert result["input_tokens"] == 12
    assert result["output_tokens"] == 3
    assert session.post.call_args.args[0] == "http://fake/v1/chat/completions"


async def test_openai_profile_converts_tool_history_request_body() -> None:
    session = _mock_session(_make_sse_lines("[DONE]"))

    await call_api(
        session,
        "http://fake/v1",
        "sk-test",
        "openai-model",
        [{"type": "text", "text": "system"}],
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "id": "tool-1", "name": "lookup", "input": {"q": "猫饼"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tool-1", "content": "result"},
            ]},
        ],
        tools=[{
            "name": "lookup",
            "description": "lookup tool",
            "input_schema": {"type": "object", "properties": {}},
        }],
        api_format="openai",
    )

    body = json.loads(session.post.call_args.kwargs["data"].getvalue().decode())
    assert body["tools"][0]["function"]["name"] == "lookup"
    assistant_msg = body["messages"][2]
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "lookup"
    assert body["messages"][3]["role"] == "tool"
    assert body["messages"][3]["tool_call_id"] == "tool-1"


async def test_deepseek_profile_uses_native_request_and_parses_cache_usage() -> None:
    events = [
        json.dumps({"choices": [{"delta": {"reasoning_content": "先想一下"}}]}),
        json.dumps({"choices": [{"delta": {"content": "好的"}}]}),
        json.dumps({"usage": {
            "prompt_tokens": 120,
            "completion_tokens": 8,
            "prompt_cache_hit_tokens": 72,
            "prompt_cache_miss_tokens": 48,
            "completion_tokens_details": {"reasoning_tokens": 3},
        }}),
        "[DONE]",
    ]

    session = _mock_session(_make_sse_lines(*events))
    result = await call_api(
        session,
        "https://api.deepseek.com",
        "sk-test",
        "deepseek-v4-flash",
        [{"type": "text", "text": "system"}],
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tool-1", "name": "lookup", "input": {"q": "猫饼"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tool-1", "content": "result"},
            ]},
        ],
        api_format="deepseek",
        request_options={"user_id": "grp_deadbeef", "reasoning_effort": "high"},
    )

    body = json.loads(session.post.call_args.kwargs["data"].getvalue().decode())
    assert session.post.call_args.args[0] == "https://api.deepseek.com/chat/completions"
    assert body["stream_options"]["include_usage"] is True
    assert body["user_id"] == "grp_deadbeef"
    assert body["reasoning_effort"] == "high"
    assert body["messages"][2]["reasoning_content"] == "(reasoning omitted)"
    assert result["text"] == "好的"
    assert result["prompt_cache_hit_tokens"] == 72
    assert result["prompt_cache_miss_tokens"] == 48
    assert result["reasoning_tokens"] == 3
    assert result["provider_kind"] == "deepseek"
    assert result["provider_mode"] == "native"
    assert result["payload_sanitized"] is True
    assert result["reasoning_replay_tokens"] > 0
