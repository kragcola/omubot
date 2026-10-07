"""One reverse WebSocket owner, bounded requests and no automatic send retries."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, cast

from pydantic import JsonValue
from starlette.websockets import WebSocketDisconnect

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
from .types import (
    ContactAddressee,
    ConversationScope,
    OperationError,
    QQTransportError,
    QQTransportEvidence,
    QQWriteGrant,
    QQWriteSpec,
    ReplyTarget,
    Scope,
    SendReceipt,
    StickerImage,
)

WriteFrame = Callable[[dict[str, JsonValue]], Awaitable[None]]

_SAFE_MEDIA_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_MEDIA_TYPE_RE = re.compile(r"image/[A-Za-z0-9.+-]{1,31}\Z")


@dataclass(frozen=True, slots=True)
class OneBotDirectImage:
    """One opaque token bound to its original top-level message segment."""

    segment_index: int
    token: str
    url_hint: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class OneBotMessageView:
    """The bounded part of a same-group ``get_msg`` result used by N5.

    ``sender_user_id`` comes only from this exact response's sender; ``None``
    is unknown, never the outer speaker or bot. Direct images retain original
    segment indices. URL hints remain transient/repr-hidden and must never be
    put in DB, audit or Model input. Forward contents, reply ancestry and raw
    dictionaries never cross this boundary. This view proves transport source
    identity, not current read/upload permission or current-turn ownership.
    """

    scope: Scope
    message_id: str
    sender_user_id: str | None
    direct_images: tuple[OneBotDirectImage, ...]
    invalid_image_count: int = 0

    @property
    def image_tokens(self) -> tuple[str, ...]:
        """Keep existing token consumers without discarding source indices."""

        return tuple(image.token for image in self.direct_images)


def onebot_read_identity(value: JsonValue, *, error: str = "invalid_protocol") -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise OperationError(error)
    result = str(value)
    if not result or result != result.strip() or len(result) > 64:
        raise OperationError(error)
    return result


def decode_onebot_message_view(
    scope: Scope, message_id: str, data: dict[str, JsonValue]
) -> OneBotMessageView:
    """Decode the same bounded get_msg contract for HTTP and reverse WS.

    The calling transport owns its authenticated bot/request lifecycle. The
    response must independently bind this exact group and requested message.
    Sender fields inside quoted/forward segments are not source evidence.
    """

    for key in ("self_id", "bot_id"):
        if key in data and onebot_read_identity(data[key]) != scope.bot_id:
            raise OperationError("wrong_bot")
    if onebot_read_identity(data.get("message_id")) != message_id:
        raise OperationError("wrong_message")
    if data.get("message_type") != "group":
        raise OperationError("wrong_group")
    # NapCat's top-level group binding is required; sender.group_id is not a
    # substitute when the standard provider response omits group identity.
    if onebot_read_identity(data.get("group_id")) != scope.group_id:
        raise OperationError("wrong_group")

    sender_user_id = None
    if "sender" in data:
        sender = data["sender"]
        if not isinstance(sender, dict):
            raise OperationError("invalid_protocol")
        if "user_id" in sender:
            sender_user_id = onebot_read_identity(sender["user_id"])
    if sender_user_id is not None and "user_id" in data:
        if onebot_read_identity(data["user_id"]) != sender_user_id:
            raise OperationError("wrong_author")

    message = data.get("message")
    if not isinstance(message, list):
        raise OperationError("invalid_protocol")
    images: list[OneBotDirectImage] = []
    invalid_image_count = 0
    for segment_index, segment in enumerate(message):
        if not isinstance(segment, dict) or segment.get("type") != "image":
            continue
        body = segment.get("data")
        token = None
        if isinstance(body, dict):
            for key in ("file_id", "file"):
                candidate = body.get(key)
                if isinstance(candidate, str) and _SAFE_MEDIA_TOKEN_RE.fullmatch(candidate):
                    token = candidate
                    break
        if token is None:
            invalid_image_count = min(invalid_image_count + 1, 2)
        elif len(images) < 2:
            url_hint = body.get("url") if isinstance(body, dict) else None
            if url_hint is not None and (not isinstance(url_hint, str) or len(url_hint) > 8000):
                raise OperationError("invalid_protocol")
            images.append(OneBotDirectImage(
                segment_index=segment_index, token=token, url_hint=url_hint,
            ))
    return OneBotMessageView(
        scope=scope,
        message_id=message_id,
        sender_user_id=sender_user_id,
        direct_images=tuple(images),
        invalid_image_count=invalid_image_count,
    )


@dataclass(frozen=True, slots=True)
class OneBotImageInfo:
    """Metadata returned by ``get_image`` without reading or fetching media.

    ``path_only`` is an explicit transport result: a OneBot path belongs to
    the implementation (often a NapCat container), and is never opened by
    this process.  There is deliberately no path, bytes, URL, or download
    field, so a caller cannot mistake container metadata for host media.
    """

    token: str
    status: Literal["path_only"]
    media_type: str | None = None
    file_size: int | None = None


class ReverseOneBotSender:
    is_external = True

    def __init__(self, bot_id: str, timeout: float = 5.0, capacity: int = 64) -> None:
        self.bot_id = bot_id
        self.timeout = timeout
        self.capacity = capacity
        self.ready = False
        self._write: WriteFrame | None = None
        self._generation = 0
        self._pending: dict[str, asyncio.Future[dict[str, JsonValue]]] = {}
        self._write_lock = asyncio.Lock()

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

    def attach(self, write: WriteFrame) -> int:
        if self._write is not None:
            raise OperationError("busy")
        self._generation += 1
        self._write = write
        self.ready = False
        return self._generation

    def detach(self, generation: int) -> None:
        if generation != self._generation:
            return
        self.ready = False
        self._write = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(OperationError("onebot_disconnected"))
        self._pending.clear()

    def receive(self, frame: dict[str, JsonValue]) -> bool:
        echo = frame.get("echo")
        if not isinstance(echo, str):
            return False
        future = self._pending.get(echo)
        # Late/unknown responses never complete a newer operation.
        if future is not None and not future.done():
            future.set_result(frame)
            return True
        return False

    async def request_envelope(
        self, action: str, params: dict[str, JsonValue], *, grant: QQWriteGrant | None = None,
    ) -> dict[str, JsonValue]:
        """Correlate bounded static reads and one permitted write without retries."""
        # Capture nested wire values before waiting for the existing write lock.
        params = cast(dict[str, JsonValue], json.loads(canonical_onebot_params(params)))
        try:
            spec = granted_write_spec(self.bot_id, action, params, grant)
            if spec is not None and not self.ready:
                raise OperationError("onebot_not_ready")
            write, generation = self._write, self._generation
            if write is None:
                raise OperationError("onebot_disconnected")
            if len(self._pending) >= self.capacity:
                raise OperationError("busy")
        except OperationError as exc:
            raise QQTransportError(exc.code, QQTransportEvidence("ws", "not_started")) from exc
        echo = secrets.token_hex(16)
        future: asyncio.Future[dict[str, JsonValue]] = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        started = False
        try:
            async with asyncio.timeout(self.timeout):
                async with self._write_lock:
                    if generation != self._generation or self._write is not write:
                        raise OperationError("onebot_disconnected")
                    if spec is not None:
                        if not self.ready:
                            raise OperationError("onebot_not_ready")
                        assert grant is not None
                        grant.consume(spec, generation)
                    started = True
                    await write({"action": action, "params": params, "echo": echo})
                response = await future
            return OneBotEnvelope(response, onebot_response_evidence(response, "ws"))
        except (OperationError, TimeoutError, OSError, RuntimeError, WebSocketDisconnect) as exc:
            if spec is None:
                raise
            code = exc.code if isinstance(exc, OperationError) else (
                "timeout" if isinstance(exc, TimeoutError) else (
                    "onebot_write_failed" if isinstance(exc, RuntimeError) else "onebot_disconnected"
                )
            )
            phase = "may_have_started" if started else "not_started"
            raise QQTransportError(code, QQTransportEvidence("ws", phase)) from None
        finally:
            self._pending.pop(echo, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def _request(
        self, action: str, params: dict[str, JsonValue], *, grant: QQWriteGrant | None = None,
    ) -> dict[str, JsonValue]:
        response = await self.request_envelope(action, params, grant=grant)
        if (
            response.get("status") != "ok"
            or type(response.get("retcode")) is not int
            or response.get("retcode") != 0
        ):
            raise onebot_response_error("send_rejected", response, "ws")
        data = response.get("data")
        if not isinstance(data, dict):
            raise onebot_response_error("invalid_protocol", response, "ws")
        return OneBotEnvelope(data, onebot_response_evidence(response, "ws"))

    @classmethod
    def _media_token(cls, value: object) -> str | None:
        if not isinstance(value, str) or cls._SAFE_TOKEN.fullmatch(value) is None:
            return None
        return value

    # Kept as a class attribute so tests and future protocol adapters cannot
    # accidentally widen the accepted opaque-token grammar in one method only.
    _SAFE_TOKEN = _SAFE_MEDIA_TOKEN_RE

    @staticmethod
    def _optional_media_type(value: object) -> str | None:
        if isinstance(value, str) and _SAFE_MEDIA_TYPE_RE.fullmatch(value):
            return value
        return None

    @staticmethod
    def _optional_file_size(value: object) -> int | None:
        if isinstance(value, str) and value.isdecimal():
            value = int(value)
        if type(value) is int and 0 <= value <= 1_000_000_000:
            return value
        return None

    async def get_msg(self, scope: ConversationScope, message_id: str) -> OneBotMessageView:
        """Read one exact same-group message's direct opaque image tokens.

        This is a protocol-only read port.  The caller remains responsible for
        checking ``media.read`` and binding the result to the current visual
        owner before calling ``get_image``.  The parser intentionally ignores
        forward/reply descendants and malformed image siblings, so one bad
        segment does not discard valid siblings or the caller's text context.
        """

        if scope.bot_id != self.bot_id:
            raise OperationError("wrong_bot")
        if scope.kind != "group":
            raise OperationError("unsupported_scope")
        if not self.ready:
            raise OperationError("onebot_not_ready")
        requested_id = onebot_read_identity(message_id, error="invalid_message_id")
        data = await self._request("get_msg", {"message_id": requested_id})
        return decode_onebot_message_view(scope, requested_id, data)

    async def get_image(self, file_token: str) -> OneBotImageInfo:
        """Return constrained ``get_image`` metadata, never image bytes.

        OneBot's standard ``data.file`` is an implementation-owned path.  This
        method records that path as ``path_only`` metadata and does not open it,
        read it, follow a returned URL, or invoke Docker.  Callers must pass
        the token through the future ``media.read`` owner/permission gate
        before invoking this protocol port.
        """

        if not self.ready:
            raise OperationError("onebot_not_ready")
        token = self._media_token(file_token)
        if token is None:
            raise OperationError("invalid_media_token")
        data = await self._request("get_image", {"file": token})
        location = data.get("file")
        if not isinstance(location, str) or not location or len(location) > 4096:
            raise OperationError("invalid_protocol")

        # ``location`` may be a container path or an implementation URL.  Both
        # are path-only at this boundary; neither is returned or fetched.
        return OneBotImageInfo(
            token=token,
            status="path_only",
            media_type=self._optional_media_type(data.get("file_type", data.get("type"))),
            file_size=self._optional_file_size(data.get("file_size")),
        )

    async def verify_identity(self) -> None:
        self.ready = False
        generation = self._generation
        data = await self._request("get_login_info", {})
        identity = data.get("user_id")
        if type(identity) not in {int, str} or str(identity) != self.bot_id:
            raise onebot_response_error("wrong_bot", data, "ws")
        if self._write is None or generation != self._generation:
            raise OperationError("onebot_disconnected")
        self.ready = True

    async def probe_online(self) -> bool:
        """Read current bridge online state without using a write as a probe."""
        if not self.ready:
            raise OperationError("onebot_not_ready")
        data = await self._request("get_status", {})
        online = data.get("online")
        if type(online) is not bool:
            raise onebot_response_error("onebot_status_unknown", data, "ws")
        return online

    async def send(
        self, scope: ConversationScope, text: str, *, target: ReplyTarget | None = None,
        grant: QQWriteGrant | None = None,
        addressee: ContactAddressee | None = None,
    ) -> SendReceipt:
        if scope.bot_id != self.bot_id:
            raise OperationError("wrong_bot")
        if not text or len(text) > 2000:
            raise OperationError("output_limit")
        if not self.ready:
            raise OperationError("onebot_not_ready")
        data = await self._request(
            send_action(scope), send_params(scope, text, target, addressee=addressee), grant=grant,
        )
        return self._receipt(data)

    async def send_sticker(
        self, scope: ConversationScope, image: StickerImage, *, target: ReplyTarget | None = None,
        grant: QQWriteGrant | None = None,
    ) -> SendReceipt:
        """Send one validated image segment through the same request owner.

        The image is encoded only by ``sticker_send_params`` after the
        bot and readiness checks pass.  No path, URL, CQ code, or retry route
        is accepted here.
        """

        if scope.bot_id != self.bot_id:
            raise OperationError("wrong_bot")
        if not self.ready:
            raise OperationError("onebot_not_ready")
        data = await self._request(
            send_action(scope), sticker_send_params(scope, image, target), grant=grant,
        )
        return self._receipt(data)

    @staticmethod
    def _receipt(data: dict[str, JsonValue]) -> SendReceipt:
        identity = data.get("message_id")
        if type(identity) not in {int, str} or not str(identity) or len(str(identity)) > 64:
            raise onebot_response_error("invalid_protocol", data, "ws")
        return SendReceipt(message_id=str(identity))

    async def send_echo(
        self, scope: ConversationScope, payload: RichEchoPayload, *, target: ReplyTarget | None = None,
        grant: QQWriteGrant | None = None,
    ) -> SendReceipt:
        if scope.bot_id != self.bot_id:
            raise OperationError("wrong_bot")
        if not self.ready:
            raise OperationError("onebot_not_ready")
        data = await self._request(send_action(scope), echo_send_params(scope, payload, target), grant=grant)
        return self._receipt(data)

    async def close(self) -> None:
        self.detach(self._generation)
