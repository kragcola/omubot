"""OpenAI Responses API adapter with explicit, stateless tool turns."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import cast

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from .adapters import _post, parse_usage  # pyright: ignore[reportPrivateUsage]
from .types import ImagePart, ModelPort, ModelReply, ModelRequest, OperationError, ToolCall

_MAX_OUTPUT_ITEMS = 8
_MAX_CONTENT_ITEMS = 8
_MAX_TEXT = 2000
_MAX_ID = 128
_MAX_STREAM_EVENT_BYTES = 65536
_MAX_STREAM_BODY_BYTES = 262144
_OBJECT = TypeAdapter(dict[str, JsonValue])
_WIRE_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")


def _image_data_url(image: ImagePart) -> str:
    return f"data:{image.media_type};base64,{image.data}"


def _object(value: JsonValue, code: str = "invalid_protocol") -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise OperationError(code)
    return value


def _required_string(value: JsonValue, code: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_ID:
        raise OperationError(code)
    return value


def _item_status(item: dict[str, JsonValue]) -> None:
    # Responses output items normally carry status=completed.  Some compatible
    # endpoints omit the item status, so omission is accepted while any other
    # explicit status is rejected.
    if "status" in item and item["status"] != "completed":
        raise OperationError("invalid_protocol")


def _tool_definitions(
    definitions: list[dict[str, JsonValue]],
) -> tuple[dict[str, str], list[JsonValue]]:
    aliases: dict[str, str] = {}
    wire: list[JsonValue] = []
    for definition in definitions:
        if set(definition) - {"name", "description", "input_schema"}:
            raise OperationError("invalid_tool_schema")
        name = definition.get("name")
        description = definition.get("description")
        parameters = definition.get("input_schema")
        if (
            not isinstance(name, str)
            or not isinstance(parameters, dict)
            or (description is not None and not isinstance(description, str))
        ):
            raise OperationError("invalid_tool_schema")
        alias = name.replace(".", "_")
        if alias in aliases or _WIRE_NAME.fullmatch(alias) is None:
            raise OperationError("invalid_tool_schema")
        aliases[alias] = name
        function: dict[str, JsonValue] = {
            "type": "function",
            "name": alias,
            "parameters": parameters,
            # Keep schemas non-strict: strict Responses schemas require every
            # property to be required, unlike the internal schema.
            "strict": False,
        }
        if isinstance(description, str):
            function["description"] = description
        wire.append(function)
    return aliases, wire


def _parse_function_call(item: dict[str, JsonValue], aliases: dict[str, str]) -> ToolCall:
    _item_status(item)
    item_id = _required_string(item.get("id"), "invalid_tool_call")
    call_id = _required_string(item.get("call_id"), "invalid_tool_call")
    wire_name = item.get("name")
    arguments = item.get("arguments")
    if not isinstance(wire_name, str) or wire_name not in aliases or not isinstance(arguments, str):
        raise OperationError("invalid_tool_call")
    # Keep the output item id validated even though the internal port carries
    # call_id: function_call_output must refer to call_id, not the item id.
    if not item_id:
        raise OperationError("invalid_tool_call")
    try:
        parsed = json.loads(arguments)
    except (TypeError, ValueError) as exc:
        raise OperationError("invalid_tool_call") from exc
    if not isinstance(parsed, dict):
        raise OperationError("invalid_tool_call")
    return ToolCall(
        id=call_id,
        name=aliases[wire_name],
        arguments=cast(dict[str, JsonValue], parsed),
    )


def _parse_message(item: dict[str, JsonValue], *, max_output_chars: int) -> str:
    _item_status(item)
    role = item.get("role")
    if role is not None and role != "assistant":
        raise OperationError("invalid_protocol")
    content = item.get("content")
    if not isinstance(content, list) or not 0 <= len(content) <= _MAX_CONTENT_ITEMS:
        raise OperationError("invalid_protocol")
    text = ""
    for raw_part in content:
        part = _object(raw_part)
        part_type = part.get("type")
        if part_type == "output_text":
            value = part.get("text")
            if not isinstance(value, str):
                raise OperationError("invalid_protocol")
            text += value
        elif part_type == "refusal":
            raise OperationError("invalid_protocol")
        else:
            raise OperationError("invalid_protocol")
        if len(text) > max_output_chars:
            raise OperationError("output_limit")
    return text


def _validate_continuation(
    continuation: list[dict[str, JsonValue]],
    aliases: dict[str, str],
    previous: ToolCall,
) -> None:
    if not 1 <= len(continuation) <= _MAX_OUTPUT_ITEMS:
        raise OperationError("invalid_tool_result")
    found: ToolCall | None = None
    for raw_item in continuation:
        item = _object(raw_item, "invalid_tool_result")
        item_type = item.get("type")
        if item_type == "reasoning":
            _item_status(item)
        elif item_type == "message":
            _parse_message(item, max_output_chars=_MAX_TEXT)
        elif item_type == "function_call":
            if found is not None:
                raise OperationError("invalid_tool_result")
            try:
                found = _parse_function_call(item, aliases)
            except OperationError as exc:
                raise OperationError("invalid_tool_result") from exc
        else:
            raise OperationError("invalid_tool_result")
    if found is None or found != previous:
        raise OperationError("invalid_tool_result")


def _input_items(request: ModelRequest, aliases: dict[str, str]) -> list[JsonValue]:
    messages: list[JsonValue] = []
    for index, message in enumerate(request.messages):
        content: JsonValue = message.content
        if request.current_images and index == len(request.messages) - 1:
            parts: list[dict[str, JsonValue]] = [
                {"type": "input_text", "text": message.content}
            ]
            parts.extend(
                {
                    "type": "input_image",
                    "image_url": _image_data_url(image),
                }
                for image in request.current_images
            )
            content = cast(JsonValue, parts)
        messages.append({"role": message.role, "content": content})
    previous = request.previous_tool
    if previous is None:
        if request.tool_result is not None or request.continuation:
            raise OperationError("invalid_tool_result")
        return messages
    if request.tool_result is None:
        raise OperationError("invalid_tool_result")
    if previous.name not in aliases.values():
        raise OperationError("invalid_tool_result")
    try:
        _validate_continuation(request.continuation, aliases, previous)
    except OperationError as exc:
        raise OperationError("invalid_tool_result") from exc
    messages.extend(cast(list[JsonValue], request.continuation))
    try:
        encoded_result = json.dumps(
            request.tool_result, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise OperationError("invalid_tool_result") from exc
    messages.append({"type": "function_call_output", "call_id": previous.id, "output": encoded_result})
    return messages


def _parse_response(
    data: dict[str, JsonValue], aliases: dict[str, str], *, max_output_chars: int,
) -> ModelReply:
    if data.get("status") != "completed" or data.get("incomplete_details") is not None:
        raise OperationError("invalid_protocol")
    raw_output = data.get("output")
    if not isinstance(raw_output, list) or not 1 <= len(raw_output) <= _MAX_OUTPUT_ITEMS:
        raise OperationError("invalid_protocol")

    text = ""
    tool: ToolCall | None = None
    continuation: list[dict[str, JsonValue]] = []
    for raw_item in raw_output:
        item = _object(raw_item)
        item_type = item.get("type")
        if item_type == "reasoning":
            _item_status(item)
        elif item_type == "message":
            text += _parse_message(item, max_output_chars=max_output_chars)
            if len(text) > max_output_chars:
                raise OperationError("output_limit")
        elif item_type == "function_call":
            if tool is not None:
                raise OperationError("tool_limit")
            tool = _parse_function_call(item, aliases)
        else:
            raise OperationError("invalid_protocol")
        continuation.append(item)

    if not text and tool is None:
        raise OperationError("invalid_protocol")
    if tool is None:
        continuation = []
    return ModelReply(text=text, tool_call=tool, continuation=continuation,
                      usage=parse_usage(data.get("usage"), "responses"))


class ResponsesModel(ModelPort):
    is_external = True

    def __init__(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        api_key: str,
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        vision_enabled: bool = False,
    ) -> None:
        self._client = client
        self._endpoint = endpoint
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort
        self._vision_enabled = vision_enabled
        self._closed = False

    def _body(self, request: ModelRequest) -> tuple[dict[str, str], dict[str, JsonValue]]:
        aliases, tools = _tool_definitions(request.tools)
        body: dict[str, JsonValue] = {
            "store": False,
            "model": request.model,
            "input": _input_items(request, aliases),
            "max_output_tokens": (
                self._max_tokens if request.max_output_tokens is None
                else min(request.max_output_tokens, self._max_tokens)
            ),
            "tools": tools,
            "parallel_tool_calls": False,
            "include": ["reasoning.encrypted_content"],
        }
        if request.system:
            body["instructions"] = request.system
        if self._temperature is not None:
            body["temperature"] = self._temperature
        if self._reasoning_effort is not None:
            body["reasoning"] = {"effort": self._reasoning_effort}
        return aliases, body

    async def request(self, request: ModelRequest) -> ModelReply:
        if self._closed or self._client.is_closed:
            raise OperationError("model_closed")
        if request.current_images and not self._vision_enabled:
            raise OperationError("vision_unavailable")
        aliases, body = self._body(request)
        data = await _post(
            self._client,
            self._endpoint,
            {"Authorization": "Bearer " + self._api_key},
            body,
            65536,
        )
        return _parse_response(data, aliases, max_output_chars=request.max_output_chars)

    async def request_stream(
        self, request: ModelRequest, on_text_delta: Callable[[str], Awaitable[None]],
    ) -> ModelReply:
        """Stream one tool-free text response using the existing HTTP client.

        Responses terminates with a typed completed response, not Chat's DONE
        sentinel. Delivery owns final-prefix validation; the completed text is
        returned without silently replacing it with the concatenated deltas.
        """
        if self._closed or self._client.is_closed:
            raise OperationError("model_closed")
        if (request.tools or request.previous_tool is not None or request.tool_result is not None
                or request.continuation or request.current_images):
            raise OperationError("stream_unsupported")
        _, body = self._body(request)
        body["stream"] = True
        response_id: str | None = None
        items: dict[int, tuple[str, str]] = {}
        sequence = -1
        text_chars = 0
        completed: ModelReply | None = None

        def position(event: dict[str, JsonValue]) -> tuple[int, str]:
            index = event.get("output_index")
            if type(index) is not int or not 0 <= index < _MAX_OUTPUT_ITEMS:
                raise OperationError("invalid_protocol")
            item_id = _required_string(event.get("item_id"), "invalid_protocol")
            if index not in items or items[index][0] != item_id:
                raise OperationError("invalid_protocol")
            return index, items[index][1]

        async def consume(payload: bytes, event_name: str) -> None:
            nonlocal response_id, sequence, text_chars, completed
            try:
                event = _OBJECT.validate_json(payload, strict=True)
            except (ValidationError, ValueError) as exc:
                raise OperationError("invalid_protocol") from exc
            kind = event.get("type")
            number = event.get("sequence_number")
            if (not isinstance(kind, str) or (event_name and event_name != kind)
                    or type(number) is not int or number <= sequence or completed is not None):
                raise OperationError("invalid_protocol")
            sequence = number
            if kind in {"error", "response.failed"}:
                raise OperationError("upstream_stream_failed")
            if kind == "response.incomplete":
                raise OperationError("invalid_protocol")
            if kind in {"response.created", "response.in_progress", "response.completed"}:
                data = _object(event.get("response"))
                identity = _required_string(data.get("id"), "invalid_protocol")
                if response_id is not None and identity != response_id:
                    raise OperationError("invalid_protocol")
                if kind == "response.created" and response_id is not None:
                    raise OperationError("invalid_protocol")
                response_id = identity
                if kind == "response.completed":
                    if data.get("error") is not None:
                        raise OperationError("upstream_stream_failed")
                    completed = _parse_response(data, {}, max_output_chars=request.max_output_chars)
                    output = cast(list[JsonValue], data["output"])
                    for index, raw in enumerate(output):
                        item = _object(raw)
                        if items.get(index) != (item.get("id"), item.get("type")):
                            raise OperationError("invalid_protocol")
                elif data.get("status") != "in_progress":
                    raise OperationError("invalid_protocol")
                return
            if response_id is None:
                raise OperationError("invalid_protocol")
            if kind in {"response.output_item.added", "response.output_item.done"}:
                index = event.get("output_index")
                item = _object(event.get("item"))
                identity = _required_string(item.get("id"), "invalid_protocol")
                item_kind = item.get("type")
                if (type(index) is not int or not 0 <= index < _MAX_OUTPUT_ITEMS
                        or item_kind not in {"message", "reasoning"}):
                    raise OperationError("invalid_protocol")
                binding = (identity, item_kind)
                if index in items and items[index] != binding:
                    raise OperationError("invalid_protocol")
                items[index] = binding
                if item_kind == "message" and item.get("role") != "assistant":
                    raise OperationError("invalid_protocol")
                if kind == "response.output_item.done":
                    if item_kind == "message":
                        _parse_message(item, max_output_chars=request.max_output_chars)
                    else:
                        _item_status(item)
                return
            _, item_kind = position(event)
            if kind in {"response.output_text.delta", "response.output_text.done",
                        "response.output_text.annotation.added"}:
                content_index = event.get("content_index")
                if (item_kind != "message" or type(content_index) is not int
                        or not 0 <= content_index < _MAX_CONTENT_ITEMS):
                    raise OperationError("invalid_protocol")
                if kind == "response.output_text.delta":
                    delta = event.get("delta")
                    if not isinstance(delta, str):
                        raise OperationError("invalid_protocol")
                    text_chars += len(delta)
                    if text_chars > request.max_output_chars:
                        raise OperationError("output_limit")
                    if delta:
                        await on_text_delta(delta)
                elif kind == "response.output_text.done":
                    text = event.get("text")
                    if not isinstance(text, str) or len(text) > request.max_output_chars:
                        raise OperationError("invalid_protocol")
            elif kind in {"response.content_part.added", "response.content_part.done"}:
                part = _object(event.get("part"))
                if part.get("type") != ("output_text" if item_kind == "message" else "reasoning_text"):
                    raise OperationError("invalid_protocol")
            elif kind in {"response.reasoning_summary_part.added", "response.reasoning_summary_part.done",
                          "response.reasoning_summary_text.delta", "response.reasoning_summary_text.done",
                          "response.reasoning_text.delta", "response.reasoning_text.done"}:
                if item_kind != "reasoning":
                    raise OperationError("invalid_protocol")
            else:
                raise OperationError("invalid_protocol")

        buffer = b""
        data_lines: list[bytes] = []
        event_name = ""
        event_bytes = 0
        body_bytes = 0
        try:
            async with self._client.stream(
                "POST", self._endpoint, json=body,
                headers={"Authorization": "Bearer " + self._api_key,
                         "Accept": "text/event-stream", "Accept-Encoding": "identity"},
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise OperationError("unsupported_encoding")
                async for chunk in response.aiter_bytes():
                    body_bytes += len(chunk)
                    if body_bytes > _MAX_STREAM_BODY_BYTES:
                        raise OperationError("protocol_output_limit")
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        event_bytes += len(line) + 1
                        if event_bytes > _MAX_STREAM_EVENT_BYTES:
                            raise OperationError("protocol_output_limit")
                        line = line.removesuffix(b"\r")
                        if not line:
                            if data_lines:
                                await consume(b"\n".join(data_lines), event_name)
                            data_lines.clear()
                            event_name, event_bytes = "", 0
                            if completed is not None:
                                return completed
                        elif not line.startswith(b":"):
                            field, separator, value = line.partition(b":")
                            if not separator or field not in {b"data", b"event", b"id", b"retry"}:
                                raise OperationError("invalid_protocol")
                            value = value.removeprefix(b" ")
                            if field == b"data":
                                data_lines.append(value)
                            elif field == b"event":
                                try:
                                    event_name = value.decode("utf8")
                                except UnicodeDecodeError as exc:
                                    raise OperationError("invalid_protocol") from exc
                    if event_bytes + len(buffer) > _MAX_STREAM_EVENT_BYTES:
                        raise OperationError("protocol_output_limit")
        except httpx.TimeoutException as exc:
            raise OperationError("upstream_timeout") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            code = ("upstream_auth" if status in {401, 403} else "upstream_rate_limited"
                    if status == 429 else "upstream_http")
            raise OperationError(code) from exc
        except httpx.RequestError as exc:
            raise OperationError("upstream_unavailable") from exc
        raise OperationError("invalid_protocol")

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._client.aclose()
