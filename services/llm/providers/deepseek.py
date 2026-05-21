"""DeepSeek V4 native chat/completions provider."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from services.llm.provider import (
    LLMProvider,
    ThinkingMode,
    ToolUse,
    normalize_thinking_mode,
    provider_mode,
)

_log = logger.bind(channel="api")
_PLACEHOLDER_REASONING = "(reasoning omitted)"


class RateLimitError(Exception):
    """HTTP 429 or rate limit in stream."""


class DeepSeekProvider(LLMProvider):
    """Provider for DeepSeek native `/chat/completions` streaming API."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url
        self._api_key = api_key

    def request_url(self) -> str:
        base = self._base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1") or base.endswith("/beta"):
            return f"{base}/chat/completions"
        return f"{base}/chat/completions"

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
        options = request_options or {}
        replay_reasoning = normalize_thinking_mode(thinking) != "disabled"
        ds_messages, replay_chars, payload_sanitized = _to_deepseek_messages(
            system_blocks,
            messages,
            replay_reasoning=replay_reasoning,
        )
        reasoning_replay_tokens = replay_chars // 4 if replay_chars > 0 else 0

        body: dict[str, Any] = {
            "model": model,
            "messages": ds_messages,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {
                "include_usage": True,
            },
        }

        thinking_mode = normalize_thinking_mode(thinking)
        if thinking_mode == "disabled":
            body["thinking"] = {"type": "disabled"}
        elif isinstance(thinking, dict):
            body["thinking"] = thinking

        reasoning_effort = str(options.get("reasoning_effort", "") or "").strip().lower()
        if reasoning_effort and thinking_mode != "disabled":
            body["reasoning_effort"] = reasoning_effort

        user_id = str(options.get("user_id", "") or "").strip()
        if user_id:
            body["user_id"] = user_id

        if tools:
            ds_tools = _to_deepseek_tools(tools)
            if tools and isinstance(tools[-1], dict) and tools[-1].get("cache_control"):
                ds_tools[-1]["cache_control"] = tools[-1]["cache_control"]
            body["tools"] = ds_tools

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        return body, headers, {
            "provider_kind": "deepseek",
            "provider_mode": provider_mode("deepseek", self._base_url),
            "payload_sanitized": payload_sanitized,
            "reasoning_replay_tokens": reasoning_replay_tokens,
        }

    def parse_sse_stream(self, raw_lines: list[str]) -> dict[str, Any]:
        text_parts: list[str] = []
        tool_uses: list[ToolUse] = []
        thinking_blocks: list[dict[str, Any]] = []
        tool_call_buf: dict[int, dict[str, Any]] = {}
        usage_raw: dict[str, Any] = {}

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

            if data.get("error"):
                error_msg = str(data["error"].get("message", data["error"]))
                if "rate limit" in error_msg.lower():
                    raise RateLimitError(f"DeepSeek API stream error: {error_msg}")
                raise RuntimeError(f"DeepSeek API stream error: {error_msg}")

            if isinstance(data.get("usage"), dict):
                usage_raw = data["usage"]

            choices: list[dict[str, Any]] = data.get("choices", [])
            if not choices:
                continue

            delta: dict[str, Any] = choices[0].get("delta", {})

            content = delta.get("content")
            if content:
                text_parts.append(str(content))

            reasoning = delta.get("reasoning_content")
            if reasoning:
                if thinking_blocks and thinking_blocks[-1].get("type") == "thinking":
                    thinking_blocks[-1]["thinking"] += str(reasoning)
                else:
                    thinking_blocks.append({"type": "thinking", "thinking": str(reasoning)})

            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    idx = int(tc.get("index", 0) or 0)
                    if idx not in tool_call_buf:
                        tool_call_buf[idx] = {
                            "id": str(tc.get("id", "") or ""),
                            "name": "",
                            "arguments": "",
                        }
                    buf = tool_call_buf[idx]
                    if tc.get("id"):
                        buf["id"] = str(tc["id"])
                    fn = tc.get("function", {})
                    if isinstance(fn, dict):
                        if fn.get("name"):
                            buf["name"] += str(fn["name"])
                        if fn.get("arguments"):
                            buf["arguments"] += str(fn["arguments"])

        for idx in sorted(tool_call_buf.keys()):
            buf = tool_call_buf[idx]
            if buf["id"] and buf["name"]:
                try:
                    args = json.loads(buf["arguments"]) if buf["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                tool_uses.append(ToolUse(id=buf["id"], name=buf["name"], input=args))

        prompt_tokens = int(usage_raw.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage_raw.get("completion_tokens", 0) or 0)
        cached_tokens = int(
            (
                (usage_raw.get("prompt_tokens_details") or {})
                if isinstance(usage_raw, dict)
                else {}
            ).get("cached_tokens", 0)
            or 0
        )
        prompt_cache_hit_tokens = int(usage_raw.get("prompt_cache_hit_tokens", cached_tokens) or 0)
        prompt_cache_miss_tokens = int(
            usage_raw.get(
                "prompt_cache_miss_tokens",
                max(0, prompt_tokens - prompt_cache_hit_tokens),
            ) or 0
        )
        reasoning_tokens = int(
            (
                (usage_raw.get("completion_tokens_details") or {})
                if isinstance(usage_raw, dict)
                else {}
            ).get("reasoning_tokens", 0)
            or 0
        )
        total_input = prompt_tokens or (prompt_cache_hit_tokens + prompt_cache_miss_tokens)
        output_tokens = completion_tokens or reasoning_tokens

        return {
            "text": "".join(text_parts),
            "tool_uses": tool_uses,
            "thinking_blocks": thinking_blocks,
            "input_tokens": total_input,
            "output_tokens": output_tokens,
            "cache_read": prompt_cache_hit_tokens,
            "cache_create": 0,
            "usage": usage_raw,
            "prompt_cache_hit_tokens": prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": prompt_cache_miss_tokens,
            "reasoning_tokens": reasoning_tokens,
        }


def _to_deepseek_tools(anthropic_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ds_tools: list[dict[str, Any]] = []
    for tool in anthropic_tools:
        ds_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        })
    return ds_tools


def _to_deepseek_messages(
    system_blocks: list[dict[str, Any]],
    messages: list[Any],
    *,
    replay_reasoning: bool,
) -> tuple[list[dict[str, Any]], int, bool]:
    out: list[dict[str, Any]] = []
    pending_tool_calls: set[str] = set()
    payload_sanitized = False

    for block in system_blocks:
        if isinstance(block, dict) and block.get("type") == "text" and str(block.get("text", "")).strip():
            out.append({"role": "system", "content": str(block["text"])})

    for index, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "user") or "user")
        content = msg.get("content", "")

        if isinstance(content, list):
            tool_calls: list[dict[str, Any]] = []
            tool_results: list[tuple[str, dict[str, Any]]] = []
            text_parts: list[str] = []
            thinking_parts: list[str] = []

            later_user_turn = _has_later_real_user_turn(messages, index)

            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type", "") or "")
                if block_type == "text":
                    text_parts.append(str(block.get("text", "")))
                elif block_type == "thinking":
                    thinking_parts.append(str(block.get("thinking", "")))
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
                    tool_use_id = str(block.get("tool_use_id", ""))
                    tool_results.append((
                        tool_use_id,
                        {
                            "role": "tool",
                            "tool_call_id": tool_use_id,
                            "content": str(block.get("content", "")),
                        },
                    ))
                elif block_type in {"image", "image_ref"}:
                    text_parts.append("«图片»")

            if role == "assistant":
                text_content = "\n".join(part for part in text_parts if part)
                reasoning_content = "\n".join(part for part in thinking_parts if part).strip()
                has_tool_calls = bool(tool_calls)
                include_reasoning = replay_reasoning and (has_tool_calls or not later_user_turn)
                if include_reasoning and has_tool_calls and not reasoning_content:
                    reasoning_content = _PLACEHOLDER_REASONING
                    payload_sanitized = True

                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": text_content if text_content else ("" if reasoning_content else None),
                }
                if include_reasoning and reasoning_content:
                    assistant_msg["reasoning_content"] = reasoning_content
                if has_tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                    pending_tool_calls = {
                        str(call.get("id", ""))
                        for call in tool_calls
                        if str(call.get("id", ""))
                    }
                else:
                    pending_tool_calls.clear()
                if assistant_msg.get("content") is not None or assistant_msg.get("tool_calls"):
                    out.append(assistant_msg)
            elif role == "user":
                text_content = "\n".join(part for part in text_parts if part)
                if text_content:
                    out.append({"role": "user", "content": text_content})

            if tool_results:
                if pending_tool_calls:
                    for tool_use_id, tool_message in tool_results:
                        if tool_use_id in pending_tool_calls:
                            out.append(tool_message)
                else:
                    out.extend(tool for _, tool in tool_results)
            continue

        rendered = _content_text(content)
        if rendered:
            out.append({"role": role, "content": rendered})

    replay_chars = 0
    for msg in out:
        if msg.get("role") != "assistant":
            continue
        if msg.get("tool_calls") and not str(msg.get("reasoning_content", "") or "").strip() and replay_reasoning:
            msg["reasoning_content"] = _PLACEHOLDER_REASONING
            payload_sanitized = True
        reasoning = str(msg.get("reasoning_content", "") or "").strip()
        if reasoning:
            replay_chars += len(reasoning)

    if payload_sanitized:
        _log.warning("deepseek payload sanitizer injected placeholder reasoning_content")

    return out, replay_chars, payload_sanitized


def _has_later_real_user_turn(messages: list[Any], start_index: int) -> bool:
    for later in messages[start_index + 1:]:
        if not isinstance(later, dict) or str(later.get("role", "")) != "user":
            continue
        content = later.get("content", "")
        if isinstance(content, str) and content.strip():
            return True
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and str(block.get("text", "")).strip():
                    return True
    return False


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", "") or "")
        if block_type == "text":
            parts.append(str(block.get("text", "")))
        elif block_type in {"image", "image_ref"}:
            parts.append("«图片»")
    return "\n".join(part for part in parts if part)
