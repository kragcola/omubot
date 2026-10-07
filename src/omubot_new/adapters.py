"""OneBot/Anthropic boundaries and shared bounded HTTP transport, with no retries."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import NoReturn, cast

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from .bilibili_sources import extended_bilibili_card
from .echo import RichEchoPayload
from .onebot_payload import (
    OneBotEnvelope,
    canonical_onebot_params,
    echo_send_params,
    granted_write_spec,
    onebot_response_error,
    onebot_response_evidence,
    qq_write_spec,
    send_action,
    send_params,
    sticker_send_params,
)
from .onebot_ws import OneBotMessageView, decode_onebot_message_view, onebot_read_identity
from .rich_messages import (
    AtSegment,
    FaceSegment,
    ForwardNode,
    ForwardSegment,
    ImageRef,
    ImageSegment,
    JsonSegment,
    ReplySegment,
    Segment,
    TextSegment,
)
from .types import (
    ContactAddressee,
    ConversationScope,
    Event,
    ModelReply,
    ModelRequest,
    ModelUsage,
    OperationError,
    PrivateScope,
    QQTransportError,
    QQTransportEvidence,
    QQWriteGrant,
    QQWriteSpec,
    ReplyTarget,
    Scope,
    SendReceipt,
    StickerImage,
    ToolCall,
)

_JSON = TypeAdapter(dict[str, JsonValue])
_CQ_ENTITY_RE = re.compile(r"(?:&amp;|&#44;|&#91;|&#93;)")
_CQ_ENTITY_VALUES = {
    "&amp;": "&",
    "&#44;": ",",
    "&#91;": "[",
    "&#93;": "]",
}
_MAX_RICH_SEGMENTS = 128
_MAX_RICH_NODES = 64
_MAX_RICH_DEPTH = 4
_MAX_RICH_TEXT = 8000
_MAX_RICH_JSON = 4096
_SAFE_IMAGE_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_RICH_TYPES = {"text", "at", "reply", "face", "image", "json", "forward"}
_IMAGE_FIELDS = {
    "file",
    "file_id",
    "url",
    "path",
    "type",
    "sub_type",
    "cache",
    "file_size",
    "id",
    "summary",
}


def _object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise OperationError("invalid_protocol")
    return value


def _segment_object(value: JsonValue) -> dict[str, JsonValue]:
    try:
        return _object(value)
    except OperationError as exc:
        raise OperationError("unsupported_message") from exc


def _id(value: JsonValue) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise OperationError("invalid_identity")
    result = str(value)
    if not result or len(result) > 64:
        raise OperationError("invalid_identity")
    return result


def _decode_cq_text(value: str) -> str:
    """Decode OneBot's four CQ escapes in one pass."""
    return _CQ_ENTITY_RE.sub(lambda match: _CQ_ENTITY_VALUES[match.group(0)], value)


def _cq_error() -> NoReturn:
    raise OperationError("unsupported_message")


def _parse_cq_code(raw: str) -> tuple[str, dict[str, str]]:
    if not raw or "[" in raw or "]" in raw:
        _cq_error()
    fields = raw.split(",")
    name = fields[0]
    if not name or re.fullmatch(r"[A-Za-z0-9_]+", name) is None:
        _cq_error()
    params: dict[str, str] = {}
    for field in fields[1:]:
        if "=" not in field:
            _cq_error()
        key, value = field.split("=", 1)
        if not key or re.fullmatch(r"[A-Za-z0-9_]+", key) is None or key in params:
            _cq_error()
        params[key] = _decode_cq_text(value)
    return name, params


@dataclass
class _RichParseBudget:
    segments: int = 0
    nodes: int = 0

    def take_segment(self) -> None:
        self.segments += 1
        if self.segments > _MAX_RICH_SEGMENTS:
            _cq_error()

    def take_node(self) -> None:
        self.nodes += 1
        if self.nodes > _MAX_RICH_NODES:
            _cq_error()


@dataclass(frozen=True)
class _ParsedMessage:
    text: str
    reply_to: str
    mentioned: bool
    mention_targets: tuple[str, ...]
    rich_segments: tuple[Segment, ...]
    image_urls: tuple[tuple[int, str], ...] = ()


def _strict_fields(
    body: dict[str, JsonValue], *, allowed: set[str], required: set[str] | None = None
) -> None:
    required = set() if required is None else required
    if set(body) - allowed or required - set(body):
        _cq_error()


def _bounded_text(value: JsonValue, *, limit: int = _MAX_RICH_TEXT) -> str:
    if not isinstance(value, str) or len(value) > limit:
        _cq_error()
    return value


def _image_ref(body: dict[str, JsonValue]) -> ImageRef | None:
    for key in ("file_id", "file"):
        value = body.get(key)
        if isinstance(value, str) and _SAFE_IMAGE_REF_RE.fullmatch(value):
            return ImageRef(f"onebot:{key}:{value}")
    return None


def _parse_image(body: dict[str, JsonValue]) -> ImageSegment:
    _strict_fields(body, allowed=_IMAGE_FIELDS)
    if not set(body) & {"file", "file_id", "url", "path"}:
        _cq_error()
    for key in ("file", "file_id", "url", "path", "type", "summary"):
        if key in body:
            _bounded_text(body[key], limit=_MAX_RICH_TEXT)
    subtype = body.get("sub_type", "0")
    if type(subtype) is int:
        if not 0 <= subtype <= 255:
            _cq_error()
    elif isinstance(subtype, str):
        if subtype and (not subtype.isdecimal() or len(subtype) > 3 or int(subtype) > 255):
            _cq_error()
    else:
        _cq_error()
    if "id" in body:
        _id(body["id"])
    if "cache" in body:
        cache = body["cache"]
        if type(cache) is not bool and (not isinstance(cache, str) or cache not in {"0", "1"}):
            _cq_error()
    if "file_size" in body:
        size = body["file_size"]
        if isinstance(size, str) and size.isdecimal():
            size = int(size)
        if type(size) is not int or size < 0 or size > 1_000_000_000:
            _cq_error()
    flash = body.get("type") == "flash"
    return ImageSegment(ref=_image_ref(body), is_flash=flash,
                        is_sticker=not flash and (str(subtype) == "1"
                                                 or body.get("file") == "marketface"))


def _parse_forward_nodes(
    content: JsonValue, *, bot: str, budget: _RichParseBudget, depth: int
) -> tuple[ForwardNode, ...]:
    if depth >= _MAX_RICH_DEPTH or not isinstance(content, list) or len(content) > _MAX_RICH_NODES:
        _cq_error()
    nodes: list[ForwardNode] = []
    for raw_node in content:
        budget.take_node()
        item = _segment_object(raw_node)
        if set(item) != {"type", "data"} or item.get("type") != "node":
            _cq_error()
        body = _segment_object(item.get("data"))
        _strict_fields(
            body,
            allowed={"name", "nickname", "uin", "user_id", "content"},
            required={"content"},
        )
        sender_value = body.get("uin", body.get("user_id"))
        if sender_value is None:
            _cq_error()
        sender_id = _id(sender_value)
        name_value = body.get("name", body.get("nickname"))
        if name_value is None:
            _cq_error()
        sender_name = _bounded_text(name_value, limit=128)
        children = body["content"]
        if not isinstance(children, list):
            _cq_error()
        parsed = _parse_segment_list(
            children,
            bot=bot,
            budget=budget,
            depth=depth + 1,
            nested=True,
        )
        nodes.append(ForwardNode(sender_id, sender_name, parsed.rich_segments))
    return tuple(nodes)


def _parse_one_segment(
    item: dict[str, JsonValue], *, bot: str, budget: _RichParseBudget, depth: int
) -> tuple[Segment, str, str | None, bool, str | None]:
    if set(item) != {"type", "data"}:
        _cq_error()
    segment_type = item.get("type")
    if not isinstance(segment_type, str) or segment_type not in _RICH_TYPES:
        _cq_error()
    body = _segment_object(item.get("data"))
    if segment_type == "text":
        _strict_fields(body, allowed={"text"}, required={"text"})
        text = _bounded_text(body["text"])
        return TextSegment(text), text, None, False, None
    if segment_type == "at":
        _strict_fields(body, allowed={"qq", "name"}, required={"qq"})
        if "name" in body:
            _bounded_text(body["name"], limit=128)
        target = _id(body["qq"])
        text = "@Bot" if target == bot else "@" + target
        return AtSegment(target), text, None, target == bot, target
    if segment_type == "reply":
        _strict_fields(body, allowed={"id", "text", "qq", "time", "seq"}, required={"id"})
        if "text" in body:
            _bounded_text(body["text"])
        if "qq" in body:
            _id(body["qq"])
        for key in ("time", "seq"):
            if key in body:
                value = body[key]
                if isinstance(value, str):
                    if len(value) > 64:
                        _cq_error()
                elif type(value) is not int:
                    _cq_error()
        reply_to = _id(body["id"])
        return ReplySegment(reply_to), "", reply_to, False, None
    if segment_type == "face":
        _strict_fields(body, allowed={"id", "text"}, required={"id"})
        if "text" in body:
            _bounded_text(body["text"], limit=128)
        face_id = _id(body["id"])
        return FaceSegment(face_id), f"«表情 {face_id}»", None, False, None
    if segment_type == "image":
        image = _parse_image(body)
        return image, "«图片»", None, False, None
    if segment_type == "json":
        _strict_fields(body, allowed={"data", "resid", "extra"}, required={"data"})
        raw_card = _bounded_text(body["data"], limit=_MAX_RICH_JSON)
        if "resid" in body:
            _bounded_text(body["resid"], limit=128)
        if "extra" in body:
            _bounded_text(body["extra"], limit=_MAX_RICH_JSON)
        return JsonSegment(
            summary="JSON卡片", video_refs=extended_bilibili_card(raw_card)
        ), "«卡片»", None, False, None
    _strict_fields(body, allowed={"id", "content"})
    if "id" not in body and "content" not in body:
        _cq_error()
    forward_id = _id(body["id"]) if "id" in body else ""
    nodes = (
        _parse_forward_nodes(body["content"], bot=bot, budget=budget, depth=depth)
        if "content" in body
        else ()
    )
    if forward_id and not nodes:
        piece = f"«合并转发消息 #{forward_id}（未展开）»"
    else:
        piece = "«合并转发消息»"
    return ForwardSegment(forward_id, nodes), piece, None, False, None


def _parse_segment_list(
    message: list[JsonValue], *, bot: str, budget: _RichParseBudget, depth: int, nested: bool
) -> _ParsedMessage:
    if len(message) > _MAX_RICH_SEGMENTS or depth > _MAX_RICH_DEPTH:
        _cq_error()
    parts: list[str] = []
    segments: list[Segment] = []
    image_urls: list[tuple[int, str]] = []
    reply_to = ""
    mentioned = False
    mention_targets: list[str] = []
    for raw_segment in message:
        budget.take_segment()
        segment, piece, segment_reply, segment_mentioned, target = _parse_one_segment(
            _segment_object(raw_segment), bot=bot, budget=budget, depth=depth
        )
        if not nested and isinstance(segment, ImageSegment):
            body = _segment_object(raw_segment).get("data")
            if isinstance(body, dict) and isinstance(body.get("url"), str):
                image_urls.append((len(segments), str(body["url"])))
        segments.append(segment)
        parts.append(piece)
        if segment_reply:
            if reply_to:
                _cq_error()
            reply_to = segment_reply
        if segment_mentioned:
            mentioned = True
        if target is not None and target not in mention_targets:
            mention_targets.append(target)
    text = "".join(parts)
    if reply_to and not text and not nested:
        text = "请回应引用的消息。"
    return _ParsedMessage(
        text, reply_to, mentioned, tuple(mention_targets), tuple(segments), tuple(image_urls)
    )


def _parse_cq_message(message: str, *, bot: str) -> _ParsedMessage:
    budget = _RichParseBudget()
    parts: list[str] = []
    segments: list[Segment] = []
    image_urls: list[tuple[int, str]] = []
    reply_to = ""
    mentioned = False
    mention_targets: list[str] = []
    cursor = 0
    code_count = 0

    def append_plain(value: str) -> None:
        if not value:
            return
        budget.take_segment()
        decoded = _decode_cq_text(value)
        segments.append(TextSegment(decoded))
        parts.append(decoded)

    while True:
        start = message.find("[CQ:", cursor)
        if start < 0:
            append_plain(message[cursor:])
            break
        code_count += 1
        if code_count > _MAX_RICH_SEGMENTS:
            _cq_error()
        append_plain(message[cursor:start])
        end = message.find("]", start + 4)
        if end < 0:
            _cq_error()
        name, params = _parse_cq_code(message[start + 4 : end])
        budget.take_segment()
        segment, piece, segment_reply, segment_mentioned, target = _parse_one_segment(
            {"type": name, "data": cast(dict[str, JsonValue], params)},
            bot=bot,
            budget=budget,
            depth=0,
        )
        if isinstance(segment, ImageSegment) and "url" in params:
            image_urls.append((len(segments), params["url"]))
        segments.append(segment)
        parts.append(piece)
        if segment_reply:
            if reply_to:
                _cq_error()
            reply_to = segment_reply
        mentioned = mentioned or segment_mentioned
        if target is not None and target not in mention_targets:
            mention_targets.append(target)
        cursor = end + 1
    text = "".join(parts)
    if reply_to and not text:
        text = "请回应引用的消息。"
    return _ParsedMessage(
        text, reply_to, mentioned, tuple(mention_targets), tuple(segments), tuple(image_urls)
    )


def _parse_segment_message(message: list[JsonValue], *, bot: str) -> _ParsedMessage:
    return _parse_segment_list(message, bot=bot, budget=_RichParseBudget(), depth=0, nested=False)


def parse_event(payload: object, *, expected_bot_id: str) -> Event:
    """Caller authenticates the connection before invoking this parser."""
    try:
        data = _JSON.validate_python(payload, strict=True)
        if "is_admin" in data or "capabilities" in data:
            raise OperationError("forged_privilege")
        sender = data.get("sender")
        if isinstance(sender, dict) and ("is_admin" in sender or "capabilities" in sender):
            raise OperationError("forged_privilege")
        message_type = data.get("message_type")
        if data.get("post_type") != "message" or message_type not in ("group", "private"):
            raise OperationError("unsupported_event")
        bot = _id(data.get("self_id"))
        if bot != expected_bot_id:
            raise OperationError("wrong_bot")
        if type(data.get("user_id")) in (str, int) and str(data["user_id"]) == bot:
            raise OperationError("unsupported_event")
        user, message_id = (_id(data.get(key)) for key in ("user_id", "message_id"))
        scope: ConversationScope
        if message_type == "group":
            group = _id(data.get("group_id"))
            scope = Scope(bot_id=bot, group_id=group)
            identity = [bot, group, message_id]
        else:
            scope = PrivateScope(bot_id=bot, kind="private", private_user_id=user)
            identity = [bot, "private", user, message_id]
        message = data.get("message")
        if isinstance(message, list):
            parsed = _parse_segment_message(message, bot=bot)
        elif isinstance(message, str):
            parsed = _parse_cq_message(message, bot=bot)
        else:
            raise OperationError("unsupported_message")
        key = json.dumps(identity, separators=(",", ":"))
        wire_time = data.get("time")
        event_time = (
            wire_time
            if type(wire_time) is int and 946684800 <= wire_time <= 4102444800
            else None
        )
        event = Event(
            event_id="onebot:" + hashlib.sha256(key.encode()).hexdigest(),
            user_id=user,
            scope=scope,
            text=parsed.text,
            event_time=event_time,
            message_id=message_id,
            reply_to=parsed.reply_to,
            mentioned=parsed.mentioned,
            mention_targets=parsed.mention_targets,
            rich_segments=parsed.rich_segments,
        )
        event.bind_current_image_urls(parsed.image_urls)
        return event
    except ValidationError as exc:
        raise OperationError("invalid_event") from exc


def parse_usage(raw: JsonValue, protocol: str) -> ModelUsage | None:
    """Normalize reported counters only; absent/malformed usage stays unknown."""
    if not isinstance(raw, dict):
        return None
    names = ("prompt_tokens", "completion_tokens") if protocol == "chat" else (
        "input_tokens", "output_tokens"
    )
    incoming, outgoing = raw.get(names[0]), raw.get(names[1])
    if type(incoming) is not int or type(outgoing) is not int:
        return None
    cached: JsonValue = 0
    written: JsonValue = 0
    if protocol == "anthropic":
        cached = raw.get("cache_read_input_tokens", 0)
        written = raw.get("cache_creation_input_tokens", 0)
    else:
        details = raw.get("prompt_tokens_details" if protocol == "chat" else "input_tokens_details")
        cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
        if protocol == "chat" and "prompt_cache_hit_tokens" in raw:
            cached = raw["prompt_cache_hit_tokens"]
    if any(type(n) is not int or n < 0 or n > 1_000_000_000
           for n in (incoming, outgoing, cached, written)):
        return None
    assert isinstance(cached, int) and isinstance(written, int)
    # Anthropic input_tokens excludes cache reads/writes; normalize total input.
    total_input = incoming + cached + written if protocol == "anthropic" else incoming
    if cached > total_input or total_input > 1_000_000_000:
        return None
    return ModelUsage(input_tokens=total_input, output_tokens=outgoing,
                      cached_input_tokens=cached, cache_write_tokens=written)


async def _post(
    client: httpx.AsyncClient, endpoint: str, headers: dict[str, str], body: dict[str, JsonValue], limit: int
) -> dict[str, JsonValue]:
    # stream prevents httpx eagerly accumulating an attacker-controlled response body.
    try:
        async with client.stream(
            "POST",
            endpoint,
            headers={**headers, "Accept-Encoding": "identity"},
            json=body,
            follow_redirects=False,
        ) as response:
            response.raise_for_status()
            if response.headers.get("content-encoding", "identity") != "identity":
                raise OperationError("unsupported_encoding")
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                if len(data) + len(chunk) > limit:
                    raise OperationError("protocol_output_limit")
                data.extend(chunk)
            try:
                return _JSON.validate_json(bytes(data), strict=True)
            except ValidationError as exc:
                raise OperationError("invalid_protocol") from exc
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


class OfflineModel:
    is_external = False

    async def request(self, request: ModelRequest) -> ModelReply:
        if request.tool_result is not None:
            return ModelReply(text="当前时间：" + str(request.tool_result.get("local", "")))
        text = request.messages[-1].content
        if "时间" in text or "time" in text.lower():
            return ModelReply(tool_call=ToolCall(id="offline-time-1", name="time.now", arguments={}))
        return ModelReply(text="隔离测试回复：" + text[:1980])

    async def close(self) -> None:
        pass


class OfflineSender:
    is_external = False

    def __init__(self) -> None:
        self.sent: list[tuple[ConversationScope, str]] = []
        self.sent_addressees: list[ContactAddressee | None] = []
        self.echoed: list[tuple[ConversationScope, RichEchoPayload]] = []
        self.total_sent = 0
        self._closed = False

    async def send(
        self, scope: ConversationScope, text: str, *, target: ReplyTarget | None = None,
        addressee: ContactAddressee | None = None,
    ) -> SendReceipt:
        if self._closed:
            raise OperationError("sender_closed")
        if not text or len(text) > 2000:
            raise OperationError("output_limit")
        if addressee is not None:
            send_params(scope, text, target, addressee=addressee)
        self.total_sent += 1
        self.sent.append((scope, text))
        self.sent_addressees.append(addressee)
        if len(self.sent) > 64:
            del self.sent[0]
            del self.sent_addressees[0]
        return SendReceipt(message_id=f"offline-{self.total_sent}")

    async def close(self) -> None:
        self._closed = True
        self.sent.clear()
        self.sent_addressees.clear()
        self.echoed.clear()

    async def send_echo(
        self, scope: ConversationScope, payload: RichEchoPayload, *, target: ReplyTarget | None = None,
    ) -> SendReceipt:
        if self._closed:
            raise OperationError("sender_closed")
        echo_send_params(scope, payload, target)
        self.total_sent += 1
        self.echoed.append((scope, payload))
        if len(self.echoed) > 64:
            del self.echoed[0]
        return SendReceipt(message_id=f"offline-{self.total_sent}")


class AnthropicModel:
    is_external = True

    def __init__(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        api_key: str,
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        vision_enabled: bool = False,
    ) -> None:
        self._client, self._endpoint, self._api_key = client, endpoint, api_key
        self._max_tokens, self._temperature = max_tokens, temperature
        self._vision_enabled = vision_enabled

    async def request(self, request: ModelRequest) -> ModelReply:
        if request.current_images and not self._vision_enabled:
            raise OperationError("vision_unavailable")
        aliases: dict[str, str] = {}
        definitions: list[JsonValue] = []
        for definition in request.tools:
            name = definition.get("name")
            if not isinstance(name, str):
                raise OperationError("invalid_tool_schema")
            wire = name.replace(".", "_")
            if wire in aliases or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", wire):
                raise OperationError("invalid_tool_schema")
            aliases[wire] = name
            definitions.append({**definition, "name": wire})
        messages: list[JsonValue] = []
        for index, msg in enumerate(request.messages):
            content: JsonValue = msg.content
            if request.current_images and index == len(request.messages) - 1:
                parts: list[dict[str, JsonValue]] = [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image.media_type,
                            "data": image.data,
                        },
                    }
                    for image in request.current_images
                ]
                parts.append({"type": "text", "text": msg.content})
                content = cast(JsonValue, parts)
            messages.append({"role": msg.role, "content": content})
        if request.previous_tool is not None:
            call = request.previous_tool
            if request.tool_result is None or call.name not in aliases.values():
                raise OperationError("invalid_tool_result")
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": call.id,
                                "name": call.name.replace(".", "_"),
                                "input": call.arguments,
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": call.id,
                                "content": json.dumps(request.tool_result, ensure_ascii=False),
                            }
                        ],
                    },
                ]
            )
        body: dict[str, JsonValue] = {
            "model": request.model,
            "max_tokens": (
                self._max_tokens if request.max_output_tokens is None
                else min(request.max_output_tokens, self._max_tokens)
            ),
            "messages": messages,
            "tools": definitions,
        }
        if request.system:
            body["system"] = request.system
        if self._temperature is not None:
            body["temperature"] = self._temperature
        data = await _post(
            self._client,
            self._endpoint,
            {"x-api-key": self._api_key, "anthropic-version": "2023-06-01"},
            body,
            65536,
        )
        content = data.get("content")
        if not isinstance(content, list) or not 1 <= len(content) <= 8:
            raise OperationError("invalid_protocol")
        text = ""
        tool: ToolCall | None = None
        for value in content:
            block = _object(value)
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text += cast(str, block["text"])
            elif block.get("type") == "tool_use":
                name, call_id = block.get("name"), block.get("id")
                if tool is not None:
                    raise OperationError("tool_limit")
                if (
                    not isinstance(name, str)
                    or name not in aliases
                    or not isinstance(call_id, str)
                    or not call_id
                ):
                    raise OperationError("invalid_tool_call")
                tool = ToolCall(id=call_id, name=aliases[name], arguments=_object(block.get("input")))
            else:
                raise OperationError("invalid_protocol")
        if len(text) > request.max_output_chars:
            raise OperationError("output_limit")
        if data.get("stop_reason") != ("tool_use" if tool else "end_turn") or not (text or tool):
            raise OperationError("invalid_protocol")
        return ModelReply(text=text, tool_call=tool, usage=parse_usage(data.get("usage"), "anthropic"))

    async def request_stream(
        self, request: ModelRequest, on_text_delta: Callable[[str], Awaitable[None]]
    ) -> ModelReply:
        """Read one bounded Messages text stream, without tools or retries.

        Messages closes indexed content blocks and then the message; it has no
        independent final text snapshot. Only text deltas reach Delivery, while
        thinking/signatures stay private and usage counters are cumulative.
        """
        if self._client.is_closed:
            raise OperationError("model_closed")
        if (request.tools or request.previous_tool is not None or request.tool_result is not None
                or request.continuation or request.current_images):
            raise OperationError("stream_unsupported")
        body: dict[str, JsonValue] = {
            "model": request.model,
            "max_tokens": (
                self._max_tokens if request.max_output_tokens is None
                else min(request.max_output_tokens, self._max_tokens)
            ),
            "stream": True,
            "messages": [{"role": msg.role, "content": msg.content} for msg in request.messages],
            "tools": [],
        }
        if request.system:
            body["system"] = request.system
        if self._temperature is not None:
            body["temperature"] = self._temperature
        started = False
        block_count = 0
        block_kind: str | None = None
        ending = False
        stop_reason: JsonValue = None
        text = ""
        usage: dict[str, JsonValue] = {}
        completed: ModelReply | None = None

        def validate_counters(counters: dict[str, JsonValue]) -> None:
            # Optional cache counters can be explicitly unknown. Validate the
            # reported counters without turning unknown cache usage into a
            # returned zero; parse_usage receives the original values at finish.
            known = {name: value for name, value in counters.items() if not (
                name in {"cache_read_input_tokens", "cache_creation_input_tokens"} and value is None
            )}
            if parse_usage(known, "anthropic") is None:
                raise OperationError("invalid_protocol")

        async def consume(payload: bytes, event_name: str) -> None:
            nonlocal started, block_count, block_kind, ending, stop_reason, text, usage, completed
            try:
                event = _JSON.validate_json(payload, strict=True)
            except ValidationError as exc:
                raise OperationError("invalid_protocol") from exc
            kind = event.get("type")
            if not isinstance(kind, str) or (event_name and event_name != kind):
                raise OperationError("invalid_protocol")
            if kind == "error":
                raise OperationError("upstream_stream_failed")
            if kind == "ping":
                return
            if kind == "message_start":
                message = _object(event.get("message"))
                identity = message.get("id")
                if (started or not isinstance(identity, str) or not 1 <= len(identity) <= 128
                        or message.get("type") != "message" or message.get("role") != "assistant"
                        or message.get("content") != [] or message.get("stop_reason") is not None):
                    raise OperationError("invalid_protocol")
                usage = _object(message.get("usage"))
                validate_counters(usage)
                started = True
                return
            if not started:
                raise OperationError("invalid_protocol")
            if kind in {"content_block_start", "content_block_delta", "content_block_stop"}:
                index = event.get("index")
                if ending or type(index) is not int or index != block_count or not 0 <= index < 8:
                    raise OperationError("invalid_protocol")
                if kind == "content_block_start":
                    if block_kind is not None:
                        raise OperationError("invalid_protocol")
                    block = _object(event.get("content_block"))
                    candidate_kind = block.get("type")
                    if candidate_kind not in ("text", "thinking", "redacted_thinking"):
                        raise OperationError("stream_unsupported")
                    if candidate_kind == "text":
                        if block.get("text") != "":
                            raise OperationError("invalid_protocol")
                    elif not isinstance(block.get(
                        "thinking" if candidate_kind == "thinking" else "data"
                    ), str):
                        raise OperationError("invalid_protocol")
                    assert isinstance(candidate_kind, str)
                    block_kind = candidate_kind
                elif block_kind is None:
                    raise OperationError("invalid_protocol")
                elif kind == "content_block_stop":
                    block_kind = None
                    block_count += 1
                else:
                    delta = _object(event.get("delta"))
                    if block_kind == "text" and delta.get("type") == "text_delta":
                        value = delta.get("text")
                        if not isinstance(value, str):
                            raise OperationError("invalid_protocol")
                        if len(text) + len(value) > request.max_output_chars:
                            raise OperationError("output_limit")
                        text += value
                        if value:
                            await on_text_delta(value)
                    elif (block_kind == "thinking"
                          and delta.get("type") in ("thinking_delta", "signature_delta")):
                        field = "thinking" if delta.get("type") == "thinking_delta" else "signature"
                        if not isinstance(delta.get(field), str):
                            raise OperationError("invalid_protocol")
                    else:
                        raise OperationError("stream_unsupported")
                return
            if kind == "message_delta":
                if block_kind is not None:
                    raise OperationError("invalid_protocol")
                delta = _object(event.get("delta"))
                reason = delta.get("stop_reason")
                if reason not in (None, "end_turn") or delta.get("stop_sequence") is not None:
                    raise OperationError("invalid_protocol")
                update = _object(event.get("usage"))
                outgoing = update.get("output_tokens")
                if type(outgoing) is not int:
                    raise OperationError("invalid_protocol")
                # Nullable input/cache delta fields carry no new count; keep
                # the previous actual report rather than overwrite it with null.
                combined = {**usage, **{name: value for name, value in update.items()
                    if value is not None}}
                validate_counters(combined)
                previous_output = usage["output_tokens"]
                assert isinstance(previous_output, int)
                if outgoing < previous_output:
                    raise OperationError("invalid_protocol")
                usage = combined
                if reason is not None:
                    stop_reason = reason
                ending = True
                return
            if kind == "message_stop":
                if not ending or block_kind is not None or stop_reason != "end_turn" or not text:
                    raise OperationError("invalid_protocol")
                completed = ModelReply(text=text, usage=parse_usage(usage, "anthropic"))
                return
            raise OperationError("stream_unsupported")

        buffer = b""
        data_lines: list[bytes] = []
        event_name = ""
        event_bytes = 0
        body_bytes = 0
        try:
            async with self._client.stream(
                "POST", self._endpoint, json=body,
                headers={"x-api-key": self._api_key, "anthropic-version": "2023-06-01",
                         "Accept": "text/event-stream", "Accept-Encoding": "identity"},
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise OperationError("unsupported_encoding")
                async for chunk in response.aiter_bytes():
                    body_bytes += len(chunk)
                    if body_bytes > 262144:
                        raise OperationError("protocol_output_limit")
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        event_bytes += len(line) + 1
                        if event_bytes > 65536:
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
                    if event_bytes + len(buffer) > 65536:
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
        await self._client.aclose()


class OneBotSender:
    is_external = True

    def __init__(self, client: httpx.AsyncClient, endpoint: str, token: str, bot_id: str) -> None:
        self._client, self._endpoint, self._token, self._bot_id = client, endpoint, token, bot_id
        self._generation = 1
        self.ready = False

    @property
    def bot_id(self) -> str:
        return self._bot_id

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def connection_generation(self) -> int:
        return self._generation

    def describe_send(
        self, scope: ConversationScope, text: str, *, target: ReplyTarget | None = None,
        addressee: ContactAddressee | None = None,
    ) -> QQWriteSpec:
        if scope.bot_id != self.bot_id:
            raise OperationError("wrong_bot")
        if not text or len(text) > 2000:
            raise OperationError("output_limit")
        return qq_write_spec(scope, send_action(scope), send_params(scope, text, target, addressee=addressee))

    def describe_sticker(
        self, scope: ConversationScope, image: StickerImage, *, target: ReplyTarget | None = None,
    ) -> QQWriteSpec:
        if scope.bot_id != self.bot_id:
            raise OperationError("wrong_bot")
        return qq_write_spec(scope, send_action(scope), sticker_send_params(scope, image, target))

    def describe_echo(
        self, scope: ConversationScope, payload: RichEchoPayload, *, target: ReplyTarget | None = None,
    ) -> QQWriteSpec:
        if scope.bot_id != self.bot_id:
            raise OperationError("wrong_bot")
        return qq_write_spec(scope, send_action(scope), echo_send_params(scope, payload, target))

    async def verify_identity(self) -> None:
        """Configured bot ID becomes ready only after a fixed authenticated read."""
        self.ready = False
        generation = self._generation
        response = await self.request_envelope("get_login_info", {})
        data = self._response_data(response)
        identity = data.get("user_id")
        if type(identity) not in {int, str} or str(identity) != self.bot_id:
            raise onebot_response_error("wrong_bot", response, "http")
        if generation != self._generation or self._client.is_closed:
            raise OperationError("onebot_disconnected")
        self.ready = True

    async def probe_online(self) -> bool:
        """Identity, transport availability and online status remain distinct facts."""
        if not self.ready:
            raise OperationError("onebot_not_ready")
        response = await self.request_envelope("get_status", {})
        online = self._response_data(response).get("online")
        if type(online) is not bool:
            raise onebot_response_error("onebot_status_unknown", response, "http")
        return online

    async def get_msg(self, scope: ConversationScope, message_id: str) -> OneBotMessageView:
        if scope.bot_id != self._bot_id:
            raise OperationError("wrong_bot")
        if scope.kind != "group":
            raise OperationError("unsupported_scope")
        requested_id = onebot_read_identity(message_id, error="invalid_message_id")
        response = await self.request_envelope("get_msg", {"message_id": requested_id})
        return decode_onebot_message_view(scope, requested_id, self._response_data(response))

    async def send(
        self, scope: ConversationScope, text: str, *, target: ReplyTarget | None = None,
        grant: QQWriteGrant | None = None,
        addressee: ContactAddressee | None = None,
    ) -> SendReceipt:
        if scope.bot_id != self._bot_id:
            raise OperationError("wrong_bot")
        if not text or len(text) > 2000:
            raise OperationError("output_limit")
        return await self._send(send_action(scope), send_params(scope, text, target, addressee=addressee),
                                grant=grant)

    async def send_sticker(
        self, scope: ConversationScope, image: StickerImage, *, target: ReplyTarget | None = None,
        grant: QQWriteGrant | None = None,
    ) -> SendReceipt:
        if scope.bot_id != self._bot_id:
            raise OperationError("wrong_bot")
        return await self._send(send_action(scope), sticker_send_params(scope, image, target), grant=grant)

    async def send_echo(
        self, scope: ConversationScope, payload: RichEchoPayload, *, target: ReplyTarget | None = None,
        grant: QQWriteGrant | None = None,
    ) -> SendReceipt:
        if scope.bot_id != self._bot_id:
            raise OperationError("wrong_bot")
        return await self._send(send_action(scope), echo_send_params(scope, payload, target), grant=grant)

    async def request_envelope(
        self, action: str, params: dict[str, JsonValue], *, grant: QQWriteGrant | None = None,
    ) -> dict[str, JsonValue]:
        """Static reads and granted writes share the fixed bounded HTTP client."""
        params = cast(dict[str, JsonValue], json.loads(canonical_onebot_params(params)))
        try:
            spec = granted_write_spec(self.bot_id, action, params, grant)
            if self._client.is_closed:
                raise OperationError("onebot_disconnected")
            if spec is not None:
                if not self.ready:
                    raise OperationError("onebot_not_ready")
                assert grant is not None
                grant.consume(spec, self._generation)
        except OperationError as exc:
            raise QQTransportError(exc.code, QQTransportEvidence("http", "not_started")) from exc
        status: int | None = None
        try:
            # The next await is the actual request. No lock/cooldown follows consumption.
            async with self._client.stream(
                "POST", self._endpoint.rstrip("/") + "/" + action,
                headers={"Authorization": "Bearer " + self._token, "Accept-Encoding": "identity"},
                json=params, follow_redirects=False,
            ) as response:
                status = response.status_code
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise OperationError("unsupported_encoding")
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    if len(body) + len(chunk) > 16384:
                        raise OperationError("protocol_output_limit")
                    body.extend(chunk)
                try:
                    envelope = _JSON.validate_json(bytes(body), strict=True)
                except ValidationError as exc:
                    raise OperationError("invalid_protocol") from exc
            return OneBotEnvelope(envelope, onebot_response_evidence(envelope, "http", http_status=status))
        except (OperationError, httpx.RequestError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, OperationError):
                code = exc.code
            elif isinstance(exc, httpx.TimeoutException):
                code = "upstream_timeout"
            elif isinstance(exc, httpx.HTTPStatusError):
                code = "upstream_auth" if status in {401, 403} else (
                    "upstream_rate_limited" if status == 429 else "upstream_http"
                )
            else:
                code = "upstream_unavailable"
            phase = "acknowledged" if status is not None else "may_have_started"
            raise QQTransportError(code, QQTransportEvidence("http", phase, http_status=status)) from None

    @staticmethod
    def _response_data(response: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if (response.get("status") != "ok" or type(response.get("retcode")) is not int
                or response.get("retcode") != 0):
            raise onebot_response_error("send_rejected", response, "http")
        data = response.get("data")
        if not isinstance(data, dict):
            raise onebot_response_error("invalid_protocol", response, "http")
        return data

    async def _send(
        self, action: str, params: dict[str, JsonValue], *, grant: QQWriteGrant | None = None,
    ) -> SendReceipt:
        response = await self.request_envelope(action, params, grant=grant)
        data = self._response_data(response)
        try:
            identity = _id(data.get("message_id"))
        except OperationError as exc:
            raise onebot_response_error(exc.code, response, "http") from exc
        return SendReceipt(message_id=identity)

    async def close(self) -> None:
        self.ready = False
        self._generation += 1
        await self._client.aclose()


class OfflineThinker:
    """Deterministic local fixture, not a simulation of model judgement."""

    is_external = False

    async def request(self, request: ModelRequest) -> ModelReply:
        message = request.messages[-1].content
        stage = "B" if '"stage":"B"' in message else "A"
        outcome = "continue" if stage == "B" else "complete"
        return ModelReply(text=json.dumps({"stage": stage, "outcome": outcome}))

    async def close(self) -> None:
        pass
