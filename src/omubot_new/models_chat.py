"""Bounded OpenAI-compatible Chat Completions model adapters."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import cast

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from .adapters import _post, parse_usage  # pyright: ignore[reportPrivateUsage]
from .types import ImagePart, ModelPort, ModelReply, ModelRequest, ModelUsage, OperationError, ToolCall

_OBJECT = TypeAdapter(dict[str, JsonValue])
_WIRE_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")
_TOOL_NAME = re.compile(r"[a-zA-Z0-9_.-]{1,64}")
_TOKEN_PARAMETERS = {"max_tokens", "max_completion_tokens"}
_MAX_STREAM_EVENT_BYTES = 65536


def _image_data_url(image: ImagePart) -> str:
    return f"data:{image.media_type};base64,{image.data}"


def _bounded_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise OperationError("invalid_identity")
    result = str(value)
    if not result or len(result) > 64:
        raise OperationError("invalid_identity")
    return result


class ChatModel(ModelPort):
    """A small, non-streaming OpenAI Chat Completions protocol boundary.

    ``deepseek`` selects DeepSeek's OpenAI-compatible extensions.  It is an
    explicit constructor setting so behavior never depends on a model name.
    """

    is_external = True

    def __init__(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        api_key: str,
        *,
        deepseek: bool = False,
        max_tokens: int = 1024,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        thinking: bool = False,
        token_parameter: str = "max_completion_tokens",
        vision_enabled: bool = False,
    ) -> None:
        if token_parameter not in _TOKEN_PARAMETERS:
            raise ValueError("token_parameter must be max_tokens or max_completion_tokens")
        if max_tokens < 1:
            raise ValueError("max_tokens must be a positive integer")
        self._client = client
        self._endpoint = endpoint
        self._api_key = api_key
        self._deepseek = deepseek
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort
        self._thinking = thinking
        self._token_parameter = token_parameter
        self._vision_enabled = vision_enabled
        self._closed = False

    @staticmethod
    def _wire_name(name: object) -> str:
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
            raise OperationError("invalid_tool_schema")
        wire = name.replace(".", "_")
        if not _WIRE_NAME.fullmatch(wire):
            raise OperationError("invalid_tool_schema")
        return wire

    def _tools(
        self, request: ModelRequest
    ) -> tuple[dict[str, str], list[dict[str, JsonValue]]]:
        aliases: dict[str, str] = {}
        definitions: list[dict[str, JsonValue]] = []
        for definition in request.tools:
            if set(definition) - {"name", "description", "input_schema"}:
                raise OperationError("invalid_tool_schema")
            name = definition.get("name")
            wire = self._wire_name(name)
            if wire in aliases:
                raise OperationError("invalid_tool_schema")
            schema = definition.get("input_schema")
            try:
                parameters = _OBJECT.validate_python(schema, strict=True)
            except ValidationError as exc:
                raise OperationError("invalid_tool_schema") from exc
            description = definition.get("description")
            if "description" in definition and not isinstance(description, str):
                raise OperationError("invalid_tool_schema")
            function: dict[str, JsonValue] = {
                "name": wire,
                "parameters": cast(JsonValue, parameters),
            }
            if description is not None:
                function["description"] = description
            aliases[wire] = cast(str, name)
            definitions.append({"type": "function", "function": function})
        return aliases, definitions

    @staticmethod
    def _arguments(value: object) -> tuple[str, dict[str, JsonValue]]:
        if not isinstance(value, str) or not value:
            raise OperationError("invalid_tool_call")
        try:
            arguments = _OBJECT.validate_json(value, strict=True)
        except (ValidationError, ValueError) as exc:
            raise OperationError("invalid_tool_call") from exc
        return value, arguments

    def _continuation(
        self,
        request: ModelRequest,
        call: ToolCall,
        aliases: dict[str, str],
    ) -> dict[str, JsonValue]:
        if len(request.continuation) != 1:
            raise OperationError("invalid_tool_result")
        candidate = request.continuation[0]
        allowed = {"role", "content", "tool_calls"}
        if self._deepseek and self._thinking:
            allowed.add("reasoning_content")
        if set(candidate) - allowed or candidate.get("role") != "assistant":
            raise OperationError("invalid_tool_result")
        content = candidate.get("content")
        if content is not None and not isinstance(content, str):
            raise OperationError("invalid_tool_result")
        if isinstance(content, str) and len(content) > 2000:
            raise OperationError("output_limit")
        raw_calls = candidate.get("tool_calls")
        if not isinstance(raw_calls, list) or len(raw_calls) != 1:
            raise OperationError("invalid_tool_result")
        raw_call = raw_calls[0]
        if not isinstance(raw_call, dict) or set(raw_call) - {"id", "type", "function"}:
            raise OperationError("invalid_tool_result")
        if raw_call.get("type") != "function":
            raise OperationError("invalid_tool_result")
        try:
            call_id = _bounded_id(raw_call.get("id"))
        except OperationError as exc:
            raise OperationError("invalid_tool_result") from exc
        if call_id != call.id:
            raise OperationError("invalid_tool_result")
        function = raw_call.get("function")
        if not isinstance(function, dict) or set(function) - {"name", "arguments"}:
            raise OperationError("invalid_tool_result")
        wire_name = function.get("name")
        if not isinstance(wire_name, str) or wire_name not in aliases or aliases[wire_name] != call.name:
            raise OperationError("invalid_tool_result")
        try:
            raw_arguments, arguments = self._arguments(function.get("arguments"))
        except OperationError as exc:
            raise OperationError("invalid_tool_result") from exc
        if arguments != call.arguments:
            raise OperationError("invalid_tool_result")
        if self._deepseek and self._thinking:
            reasoning = candidate.get("reasoning_content")
            if not isinstance(reasoning, str) or not reasoning:
                raise OperationError("invalid_tool_result")
        elif "reasoning_content" in candidate:
            raise OperationError("invalid_tool_result")

        assistant: dict[str, JsonValue] = {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": wire_name, "arguments": raw_arguments},
                }
            ],
        }
        if self._deepseek and self._thinking:
            assistant["reasoning_content"] = cast(str, candidate["reasoning_content"])
        return assistant

    def _messages(
        self,
        request: ModelRequest,
        aliases: dict[str, str],
    ) -> list[dict[str, JsonValue]]:
        if request.current_images and not self._vision_enabled:
            raise OperationError("vision_unavailable")
        if self._deepseek and self._thinking and any(
            message.role == "assistant" for message in request.messages
        ):
            # DeepSeek thinking tool requests require opaque reasoning from
            # every prior assistant turn; this adapter only carries the
            # current turn through ``continuation``.
            raise OperationError("invalid_protocol")
        messages: list[dict[str, JsonValue]] = []
        for index, message in enumerate(request.messages):
            content: JsonValue = message.content
            if request.current_images and index == len(request.messages) - 1:
                parts: list[dict[str, JsonValue]] = [
                    {"type": "text", "text": message.content}
                ]
                parts.extend(
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(image)},
                    }
                    for image in request.current_images
                )
                content = cast(JsonValue, parts)
            messages.append({"role": message.role, "content": content})
        if request.previous_tool is None:
            if request.tool_result is not None or request.continuation:
                raise OperationError("invalid_tool_result")
            return messages
        call = request.previous_tool
        if request.tool_result is None or not request.continuation:
            raise OperationError("invalid_tool_result")
        if call.name not in aliases.values():
            raise OperationError("invalid_tool_result")
        try:
            call_id = _bounded_id(call.id)
        except OperationError as exc:
            raise OperationError("invalid_tool_result") from exc
        if call_id != call.id:
            raise OperationError("invalid_tool_result")
        messages.append(self._continuation(request, call, aliases))
        try:
            tool_content = json.dumps(request.tool_result, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise OperationError("invalid_tool_result") from exc
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": tool_content,
            }
        )
        return messages

    def _body(self, request: ModelRequest) -> dict[str, JsonValue]:
        aliases, definitions = self._tools(request)
        messages = self._messages(request, aliases)
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        body: dict[str, JsonValue] = {
            "model": request.model,
            "messages": cast(JsonValue, messages),
            "stream": False,
            self._max_token_key(): (
                self._max_tokens if request.max_output_tokens is None
                else min(request.max_output_tokens, self._max_tokens)
            ),
        }
        if definitions:
            body["tools"] = cast(JsonValue, definitions)
        if self._reasoning_effort is not None:
            body["reasoning_effort"] = self._reasoning_effort
        if self._deepseek:
            body["thinking"] = {"type": "enabled" if self._thinking else "disabled"}
        if self._temperature is not None and not (self._deepseek and self._thinking):
            body["temperature"] = self._temperature
        return body

    def _max_token_key(self) -> str:
        return "max_tokens" if self._deepseek else self._token_parameter

    def _parse_tool(
        self,
        raw: object,
        aliases: dict[str, str],
    ) -> tuple[ToolCall, dict[str, JsonValue]]:
        raw_value: object = raw
        if not isinstance(raw_value, dict):
            raise OperationError("invalid_tool_call")
        raw = cast(dict[str, JsonValue], raw_value)
        if raw.get("type") != "function":
            raise OperationError("invalid_tool_call")
        try:
            call_id = _bounded_id(raw.get("id"))
        except OperationError as exc:
            raise OperationError("invalid_tool_call") from exc
        function = raw.get("function")
        if not isinstance(function, dict):
            raise OperationError("invalid_tool_call")
        wire_name = function.get("name")
        if not isinstance(wire_name, str) or wire_name not in aliases:
            raise OperationError("invalid_tool_call")
        raw_arguments, arguments = self._arguments(function.get("arguments"))
        call = ToolCall(id=call_id, name=aliases[wire_name], arguments=arguments)
        wire_call: dict[str, JsonValue] = {
            "id": call_id,
            "type": "function",
            "function": {"name": wire_name, "arguments": raw_arguments},
        }
        return call, wire_call

    async def request(self, request: ModelRequest) -> ModelReply:
        if self._closed or self._client.is_closed:
            raise OperationError("model_closed")
        aliases, _ = self._tools(request)
        body = self._body(request)
        data = await _post(
            self._client,
            self._endpoint,
            {"Authorization": "Bearer " + self._api_key},
            body,
            65536,
        )
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise OperationError("invalid_protocol")
        raw_choice: object = choices[0]
        if not isinstance(raw_choice, dict):
            raise OperationError("invalid_protocol")
        choice = cast(dict[str, JsonValue], raw_choice)
        raw_message: object = choice.get("message")
        if not isinstance(raw_message, dict):
            raise OperationError("invalid_protocol")
        message = cast(dict[str, JsonValue], raw_message)
        if message.get("role") != "assistant":
            raise OperationError("invalid_protocol")
        if message.get("refusal") is not None:
            raise OperationError("invalid_protocol")
        finish_reason = choice.get("finish_reason")
        if finish_reason not in {"stop", "tool_calls"}:
            raise OperationError("invalid_protocol")
        raw_content = message.get("content")
        if raw_content is None:
            text = ""
        elif isinstance(raw_content, str):
            text = raw_content
        else:
            raise OperationError("invalid_protocol")
        if len(text) > request.max_output_chars:
            raise OperationError("output_limit")
        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls is None:
            raw_tool_calls = []
        if not isinstance(raw_tool_calls, list):
            raise OperationError("invalid_tool_call")
        if len(raw_tool_calls) > 1:
            raise OperationError("tool_limit")
        tool: ToolCall | None = None
        wire_tool: dict[str, JsonValue] | None = None
        if raw_tool_calls:
            tool, wire_tool = self._parse_tool(raw_tool_calls[0], aliases)
        if (finish_reason == "tool_calls") != (tool is not None):
            raise OperationError("invalid_protocol")
        if tool is None:
            if not text:
                raise OperationError("invalid_protocol")
            return ModelReply(text=text, usage=parse_usage(data.get("usage"), "chat"))

        assistant: dict[str, JsonValue] = {
            "role": "assistant",
            "content": raw_content,
            "tool_calls": [cast(JsonValue, wire_tool)],
        }
        if self._deepseek and self._thinking:
            reasoning = message.get("reasoning_content")
            if not isinstance(reasoning, str) or not reasoning:
                raise OperationError("invalid_protocol")
            assistant["reasoning_content"] = reasoning
        return ModelReply(text=text, tool_call=tool, continuation=[assistant],
                          usage=parse_usage(data.get("usage"), "chat"))

    async def request_stream(
        self,
        request: ModelRequest,
        on_text_delta: Callable[[str], Awaitable[None]],
    ) -> ModelReply:
        """Request one plain-text Chat Completions response over SSE.

        Streaming is deliberately narrower than :meth:`request`: a stream has
        no tool or media turn to replay, and it never falls back to a second
        non-streaming request after the provider has started sending events.
        The callback is awaited for each non-empty text delta in wire order.
        """
        if self._closed or self._client.is_closed:
            raise OperationError("model_closed")
        if (
            request.tools
            or request.previous_tool is not None
            or request.tool_result is not None
            or request.continuation
            or request.current_images
        ):
            raise OperationError("stream_unsupported")

        body = self._body(request)
        body["stream"] = True
        text = ""
        usage: ModelUsage | None = None
        finished = False
        saw_done = False
        saw_choice = False

        async def consume_event(payload: str) -> None:
            nonlocal text, usage, finished, saw_done, saw_choice
            if saw_done:
                raise OperationError("invalid_protocol")
            if payload.strip() == "[DONE]":
                if not finished:
                    raise OperationError("invalid_protocol")
                saw_done = True
                return
            try:
                event = _OBJECT.validate_json(payload, strict=True)
            except (ValidationError, ValueError) as exc:
                raise OperationError("invalid_protocol") from exc

            choices = event.get("choices")
            if not isinstance(choices, list):
                raise OperationError("invalid_protocol")
            if not choices:
                # OpenAI-compatible providers may send a final usage-only
                # event after the choice carrying finish_reason=stop.
                if not finished:
                    raise OperationError("invalid_protocol")
                parsed_usage = parse_usage(event.get("usage"), "chat")
                if parsed_usage is not None:
                    usage = parsed_usage
                return
            if len(choices) != 1 or finished:
                raise OperationError("invalid_protocol")
            raw_choice = choices[0]
            if not isinstance(raw_choice, dict):
                raise OperationError("invalid_protocol")
            choice = cast(dict[str, JsonValue], raw_choice)
            if "index" in choice and type(choice["index"]) is not int:
                raise OperationError("invalid_protocol")
            if choice.get("index", 0) != 0:
                raise OperationError("invalid_protocol")
            if "finish_reason" not in choice:
                raise OperationError("invalid_protocol")
            finish_reason = choice["finish_reason"]
            if finish_reason is not None and finish_reason != "stop":
                raise OperationError("invalid_protocol")
            raw_delta = choice.get("delta")
            if not isinstance(raw_delta, dict):
                raise OperationError("invalid_protocol")
            delta = cast(dict[str, JsonValue], raw_delta)
            allowed_delta = {"role", "content"}
            if self._deepseek and self._thinking:
                # DeepSeek thinking streams hidden reasoning separately from
                # visible content.  It is validated but never sent to the
                # text callback.
                allowed_delta.add("reasoning_content")
            if set(delta) - allowed_delta:
                # In particular, tool_calls/function_call/refusal events are
                # outside this plain-text streaming seam.
                raise OperationError("invalid_protocol")
            role = delta.get("role")
            if role is not None and role != "assistant":
                raise OperationError("invalid_protocol")
            raw_content = delta.get("content")
            if raw_content is not None and not isinstance(raw_content, str):
                raise OperationError("invalid_protocol")
            reasoning = delta.get("reasoning_content")
            if reasoning is not None and not isinstance(reasoning, str):
                raise OperationError("invalid_protocol")
            content = raw_content if isinstance(raw_content, str) else ""
            if content:
                if len(content) > _MAX_STREAM_EVENT_BYTES:
                    raise OperationError("protocol_output_limit")
                if len(text) + len(content) > request.max_output_chars:
                    raise OperationError("output_limit")
                await on_text_delta(content)
                text += content
            saw_choice = True
            if finish_reason == "stop":
                finished = True
            parsed_usage = parse_usage(event.get("usage"), "chat")
            if parsed_usage is not None:
                usage = parsed_usage

        def consume_line(line: str, data_lines: list[str]) -> None:
            # Kept as a small helper so line handling remains explicit: SSE
            # data fields are joined with a newline at the event boundary.
            if line.startswith(":"):
                return
            field, separator, value = line.partition(":")
            if not separator or field not in {"data", "event", "id", "retry"}:
                raise OperationError("invalid_protocol")
            if field == "data":
                if value.startswith(" "):
                    value = value[1:]
                data_lines.append(value)

        event_bytes = 0
        data_lines: list[str] = []

        try:
            async with self._client.stream(
                "POST",
                self._endpoint,
                headers={
                    "Authorization": "Bearer " + self._api_key,
                    "Accept": "text/event-stream",
                    "Accept-Encoding": "identity",
                },
                json=body,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise OperationError("unsupported_encoding")
                async for line in response.aiter_lines():
                    event_bytes += len(line.encode("utf-8")) + 1
                    if event_bytes > _MAX_STREAM_EVENT_BYTES:
                        raise OperationError("protocol_output_limit")
                    if line == "":
                        if data_lines:
                            payload = "\n".join(data_lines)
                            if len(payload.encode("utf-8")) > _MAX_STREAM_EVENT_BYTES:
                                raise OperationError("protocol_output_limit")
                            await consume_event(payload)
                        data_lines.clear()
                        event_bytes = 0
                        if saw_done:
                            break
                        continue
                    consume_line(line, data_lines)
                else:
                    if data_lines:
                        payload = "\n".join(data_lines)
                        if len(payload.encode("utf-8")) > _MAX_STREAM_EVENT_BYTES:
                            raise OperationError("protocol_output_limit")
                        await consume_event(payload)
                        data_lines.clear()
        except OperationError:
            raise
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as exc:
            raise OperationError("upstream_timeout") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                code = "upstream_auth"
            elif status == 429:
                code = "upstream_rate_limited"
            else:
                code = "upstream_http"
            raise OperationError(code) from exc
        except httpx.RequestError as exc:
            raise OperationError("upstream_unavailable") from exc

        if not saw_done or not finished or not saw_choice or not text:
            raise OperationError("invalid_protocol")
        return ModelReply(text=text, usage=usage)

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._client.aclose()
