"""Anthropic Messages API provider (also works with DeepSeek Anthropic-compatible)."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from services.llm.provider import LLMProvider, ToolUse

_log = logger.bind(channel="api")


class RateLimitError(Exception):
    """HTTP 429 or rate limit in stream."""


class AnthropicProvider(LLMProvider):
    """Provider for Anthropic Messages API (/v1/messages)."""

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
        body: dict[str, Any] = {
            "model": model,
            "system": system_blocks,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if thinking is not None:
            body["thinking"] = thinking
        if tools:
            cached_tools = [*tools]
            cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}
            body["tools"] = cached_tools

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2024-10-22",
        }
        return body, headers

    # ------------------------------------------------------------------
    # parse_sse_stream
    # ------------------------------------------------------------------

    def parse_sse_stream(self, raw_lines: list[str]) -> dict[str, Any]:
        text_parts: list[str] = []
        tool_uses: list[ToolUse] = []
        thinking_blocks: list[dict[str, Any]] = []
        current_tool: dict[str, str] = {}
        current_thinking: str = ""
        current_thinking_sig: str = ""
        current_block_type: str = ""
        usage: dict[str, int] = {}

        for raw_line in raw_lines:
            line = raw_line.strip()
            if not line.startswith("data: "):
                continue
            data: dict[str, Any] = json.loads(line[6:])
            event_type = data.get("type", "")

            if event_type == "message_start":
                msg_usage: dict[str, Any] = data.get("message", {}).get("usage", {})
                usage = {k: v for k, v in msg_usage.items() if isinstance(v, int)}
            elif event_type == "content_block_start":
                block: dict[str, Any] = data.get("content_block", {})
                current_block_type = block.get("type", "")
                if current_block_type == "tool_use":
                    current_tool = {"id": block["id"], "name": block["name"], "input_json": ""}
                elif current_block_type == "thinking":
                    current_thinking = ""
                    current_thinking_sig = block.get("signature", "")
            elif event_type == "content_block_delta":
                delta: dict[str, Any] = data.get("delta", {})
                if delta.get("type") == "text_delta" and current_block_type != "thinking":
                    text_parts.append(delta["text"])
                elif delta.get("type") == "thinking_delta":
                    current_thinking += delta.get("thinking", "")
                elif delta.get("type") == "input_json_delta":
                    current_tool["input_json"] += delta.get("partial_json", "")
            elif event_type == "content_block_stop":
                if current_tool:
                    input_data: dict[str, Any] = (
                        json.loads(current_tool["input_json"]) if current_tool["input_json"] else {}
                    )
                    tool_uses.append(ToolUse(id=current_tool["id"], name=current_tool["name"], input=input_data))
                    current_tool = {}
                elif current_block_type == "thinking":
                    tb: dict[str, Any] = {"type": "thinking", "thinking": current_thinking}
                    if current_thinking_sig:
                        tb["signature"] = current_thinking_sig
                    thinking_blocks.append(tb)
                    current_thinking = ""
                    current_thinking_sig = ""
            elif event_type == "message_delta":
                delta_usage: dict[str, Any] = data.get("usage", {})
                for k, v in delta_usage.items():
                    if isinstance(v, int):
                        usage[k] = v
            elif event_type == "error":
                error_data = data.get("error", {})
                error_msg = error_data.get("message", str(data))
                if "rate limit" in error_msg.lower():
                    raise RateLimitError(f"Anthropic API stream error: {error_msg}")
                raise RuntimeError(f"Anthropic API stream error: {error_msg}")

        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)
        input_tokens = usage.get("input_tokens", 0)
        total_input = input_tokens + cache_read + cache_create
        output_tokens = usage.get("output_tokens", 0)

        return {
            "text": "".join(text_parts),
            "tool_uses": tool_uses,
            "thinking_blocks": thinking_blocks,
            "input_tokens": total_input,
            "output_tokens": output_tokens,
            "cache_read": cache_read,
            "cache_create": cache_create,
        }
