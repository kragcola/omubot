"""Tests for provider SSE parsing — both Anthropic and OpenAI formats."""

import json

from services.llm.providers.anthropic import AnthropicProvider
from services.llm.providers.openai import OpenAIProvider


def _make_sse_lines(*events: str) -> list[str]:
    """Build raw SSE lines from event JSON strings."""
    return [f"data: {event}" for event in events]


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


def test_anthropic_output_tokens_from_message_delta() -> None:
    """output_tokens should come from the last message_delta, not message_start."""
    provider = AnthropicProvider("http://fake", "sk-test")
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

    result = provider.parse_sse_stream(_make_sse_lines(*events))

    assert result["output_tokens"] == 42
    assert result["text"] == "Hello"
    assert result["input_tokens"] == 100 + 50 + 10  # total input
    assert result["cache_read"] == 50
    assert result["cache_create"] == 10


def test_anthropic_tool_use() -> None:
    """Tool use should be extracted across multiple content_block events."""
    provider = AnthropicProvider("http://fake", "sk-test")
    events = [
        json.dumps({"type": "message_start", "message": {"usage": {}}}),
        json.dumps({
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "id": "tu_1", "name": "search"},
        }),
        json.dumps({"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"q":'}}),
        json.dumps({"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '"hello"}'}}),
        json.dumps({"type": "content_block_stop"}),
        json.dumps({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 10}}),
    ]

    result = provider.parse_sse_stream(_make_sse_lines(*events))

    assert len(result["tool_uses"]) == 1
    assert result["tool_uses"][0].id == "tu_1"
    assert result["tool_uses"][0].name == "search"
    assert result["tool_uses"][0].input == {"q": "hello"}


def test_anthropic_thinking_block() -> None:
    """Thinking blocks should be extracted with signature."""
    provider = AnthropicProvider("http://fake", "sk-test")
    events = [
        json.dumps({"type": "message_start", "message": {"usage": {}}}),
        json.dumps({"type": "content_block_start", "content_block": {"type": "thinking", "signature": "sig123"}}),
        json.dumps({"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "Let me think..."}}),
        json.dumps({"type": "content_block_stop"}),
        json.dumps({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}}),
    ]

    result = provider.parse_sse_stream(_make_sse_lines(*events))

    assert len(result["thinking_blocks"]) == 1
    assert result["thinking_blocks"][0]["type"] == "thinking"
    assert result["thinking_blocks"][0]["thinking"] == "Let me think..."
    assert result["thinking_blocks"][0]["signature"] == "sig123"


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------


def test_openai_text() -> None:
    """Text deltas should be accumulated from choices[0].delta.content."""
    provider = OpenAIProvider("http://fake", "sk-test")
    events = [
        json.dumps({"choices": [{"delta": {"content": "Hello"}}]}),
        json.dumps({"choices": [{"delta": {"content": " world"}}]}),
        json.dumps({"choices": [{"delta": {}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}),
    ]

    result = provider.parse_sse_stream(_make_sse_lines(*events))

    assert result["text"] == "Hello world"
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 5


def test_openai_tool_calls() -> None:
    """Tool calls should be accumulated by index from delta.tool_calls."""
    provider = OpenAIProvider("http://fake", "sk-test")
    tc1 = {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "call_1", "function": {"name": "search", "arguments": '{"q":'}},
    ]}}]}
    tc2 = {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": '"hello"'}},
    ]}}]}
    tc3 = {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": "}"}},
    ]}}]}
    tc4 = {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    events = [json.dumps(tc1), json.dumps(tc2), json.dumps(tc3), json.dumps(tc4)]

    result = provider.parse_sse_stream(_make_sse_lines(*events))

    assert len(result["tool_uses"]) == 1
    assert result["tool_uses"][0].id == "call_1"
    assert result["tool_uses"][0].name == "search"
    assert result["tool_uses"][0].input == {"q": "hello"}


def test_openai_reasoning_content() -> None:
    """Reasoning content should be accumulated into thinking blocks."""
    provider = OpenAIProvider("http://fake", "sk-test")
    events = [
        json.dumps({"choices": [{"delta": {"reasoning_content": "Let me think..."}}]}),
        json.dumps({"choices": [{"delta": {"reasoning_content": " more"}}]}),
        json.dumps({"choices": [{"delta": {"content": "Answer"}}]}),
        json.dumps({"choices": [{"delta": {}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}),
    ]

    result = provider.parse_sse_stream(_make_sse_lines(*events))

    assert result["text"] == "Answer"
    assert len(result["thinking_blocks"]) == 1
    assert result["thinking_blocks"][0]["type"] == "thinking"
    assert result["thinking_blocks"][0]["thinking"] == "Let me think... more"


def test_openai_done_marker() -> None:
    """[DONE] payload should be ignored."""
    provider = OpenAIProvider("http://fake", "sk-test")
    events = [
        json.dumps({"choices": [{"delta": {"content": "Hi"}}]}),
        "[DONE]",
        json.dumps({"choices": [{"delta": {}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}),
    ]

    result = provider.parse_sse_stream(_make_sse_lines(*events))

    assert result["text"] == "Hi"
