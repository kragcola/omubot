"""OpenAI Chat Completions API provider (/v1/chat/completions)."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from services.llm.provider import LLMProvider, ThinkingMode, ToolUse, provider_mode

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

    def request_url(self) -> str:
        base = self._base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

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
        thinking: ThinkingMode = None,
        request_options: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
        oai_messages = _to_openai_messages(system_blocks, messages)

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
        return body, headers, {
            "provider_kind": "openai",
            "provider_mode": provider_mode("openai", self._base_url),
            "payload_sanitized": False,
            "reasoning_replay_tokens": 0,
        }

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

            u = data.get("usage")
            if isinstance(u, dict):
                usage = {
                    "input_tokens": u.get("prompt_tokens", 0),
                    "output_tokens": u.get("completion_tokens", 0),
                }

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
            "usage": usage,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": input_tokens,
            "reasoning_tokens": 0,
        }

    def extract_text_delta(self, raw_line: str) -> str:
        line = raw_line.strip()
        if not line.startswith("data: "):
            return ""
        payload = line[6:]
        if payload == "[DONE]":
            return ""
        try:
            data: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError:
            return ""
        choices: list[dict[str, Any]] = data.get("choices", [])
        if not choices:
            return ""
        delta = choices[0].get("delta", {})
        if not isinstance(delta, dict):
            return ""
        content = delta.get("content")
        return str(content) if content else ""


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


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            parts.append(str(block.get("text", "")))
        elif block_type in {"image", "image_ref"}:
            parts.append("«图片»")
    return "\n".join(part for part in parts if part)


def _to_openai_messages(
    system_blocks: list[dict[str, Any]],
    messages: list[Any],
) -> list[dict[str, Any]]:
    """Convert Omubot's Anthropic-shaped history to OpenAI chat messages."""
    oai_messages: list[dict[str, Any]] = []
    for block in system_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            oai_messages.append({"role": "system", "content": block["text"]})

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, list):
            tool_calls: list[dict[str, Any]] = []
            tool_results: list[dict[str, str]] = []
            text_parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    text_parts.append(str(block.get("text", "")))
                elif block_type == "tool_use":
                    tool_calls.append({
                        "id": str(block.get("id", "")),
                        "type": "function",
                        "function": {
                            "name": str(block.get("name", "")),
                            "arguments": json.dumps(block.get("input", {}) or {}, ensure_ascii=False),
                        },
                    })
                elif block_type == "tool_result":
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": str(block.get("tool_use_id", "")),
                        "content": str(block.get("content", "")),
                    })
                elif block_type in {"image", "image_ref"}:
                    text_parts.append("«图片»")

            if tool_calls:
                oai_messages.append({
                    "role": "assistant",
                    "content": "\n".join(part for part in text_parts if part) or None,
                    "tool_calls": tool_calls,
                })
            elif text_parts:
                oai_messages.append({"role": role, "content": "\n".join(part for part in text_parts if part)})
            oai_messages.extend(tool_results)
            continue

        oai_messages.append({"role": role, "content": _content_text(content)})

    return oai_messages
