"""OpenAI Chat Completions API provider (/v1/chat/completions)."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from services.llm.provider import LLMProvider, ToolUse

_log = logger.bind(channel="api")


class RateLimitError(Exception):
    """HTTP 429 or rate limit in stream."""


class OpenAIProvider(LLMProvider):
    """Provider for OpenAI Chat Completions API (/v1/chat/completions).

    Uses Anthropic-compatible tool schema in responses so callers don't need
    to know about the underlying format difference.
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url
        self._api_key = api_key

    # ------------------------------------------------------------------
    # build_request
    # ------------------------------------------------------------------

    def build_request(
        self,
        system_blocks: list[dict[str, Any]],
        messages: list[Any],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        model: str,
        thinking: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        # Build OpenAI-format messages
        oai_messages: list[dict[str, Any]] = []
        for block in system_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                oai_messages.append({"role": "system", "content": block["text"]})
        oai_messages.extend(messages)

        body: dict[str, Any] = {
            "model": model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            # Convert Anthropic tool format → OpenAI tool format
            oai_tools = _to_openai_tools(tools)
            # Cache-control on last tool (marker for provider-agnostic caching)
            if tools and isinstance(tools[-1], dict) and tools[-1].get("cache_control"):
                oai_tools[-1]["cache_control"] = tools[-1]["cache_control"]
            body["tools"] = oai_tools

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        return body, headers

    # ------------------------------------------------------------------
    # parse_sse_stream
    # ------------------------------------------------------------------

    def parse_sse_stream(self, raw_lines: list[str]) -> dict[str, Any]:
        text_parts: list[str] = []
        tool_uses: list[ToolUse] = []
        thinking_blocks: list[dict[str, Any]] = []
        # Track tool calls by index for accumulation
        tool_call_buf: dict[int, dict[str, Any]] = {}
        usage: dict[str, int] = {}

        for raw_line in raw_lines:
            line = raw_line.strip()
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                continue

            try:
                data: dict[str, Any] = json.loads(payload)
            except json.JSONDecodeError:
                continue

            choices: list[dict[str, Any]] = data.get("choices", [])
            if not choices:
                continue

            delta: dict[str, Any] = choices[0].get("delta", {})

            # Text content
            content = delta.get("content")
            if content:
                text_parts.append(content)

            # Reasoning / thinking (OpenAI o-series / DeepSeek via OpenAI endpoint)
            reasoning = delta.get("reasoning_content")
            if reasoning:
                # Accumulate into a single thinking block
                if thinking_blocks and thinking_blocks[-1].get("type") == "thinking":
                    thinking_blocks[-1]["thinking"] += reasoning
                else:
                    thinking_blocks.append({"type": "thinking", "thinking": reasoning})

            # Tool calls
            tool_calls = delta.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    idx: int = tc.get("index", 0)
                    if idx not in tool_call_buf:
                        tool_call_buf[idx] = {
                            "id": tc.get("id", ""),
                            "name": "",
                            "arguments": "",
                        }
                    buf = tool_call_buf[idx]
                    if tc.get("id"):
                        buf["id"] = tc["id"]
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        buf["name"] += fn["name"]
                    if fn.get("arguments"):
                        buf["arguments"] += fn["arguments"]

            # Usage (usually in final chunk)
            u = data.get("usage")
            if u:
                usage = {
                    "input_tokens": u.get("prompt_tokens", 0),
                    "output_tokens": u.get("completion_tokens", 0),
                }

        # Finalize tool calls
        for idx in sorted(tool_call_buf.keys()):
            buf = tool_call_buf[idx]
            if buf["id"] and buf["name"]:
                try:
                    args = json.loads(buf["arguments"]) if buf["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                tool_uses.append(ToolUse(id=buf["id"], name=buf["name"], input=args))

        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)

        return {
            "text": "".join(text_parts),
            "tool_uses": tool_uses,
            "thinking_blocks": thinking_blocks,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read": 0,
            "cache_create": 0,
        }


def _to_openai_tools(anthropic_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic tool definitions to OpenAI format."""
    oai_tools: list[dict[str, Any]] = []
    for t in anthropic_tools:
        oai_tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        })
    return oai_tools
