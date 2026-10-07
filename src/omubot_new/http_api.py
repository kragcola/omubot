"""Bounded public HTTPS GET and explicit POST; Actions owns authorization and replay.

Register neither tool by default. Callers bind the exact canonical URL, tool ID
and validated serialized arguments to Actions before invoking either handler.
POST failures after transport entry are unknown and must never be retried.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import Iterable, Mapping
from typing import Final, Literal, Protocol, cast

import httpx
from pydantic import Field, JsonValue, ValidationError, field_validator

from .bilibili_sources import validate_bili_step_url
from .native_media import NativePinnedHTTPTransport, SystemAddressResolver
from .tools import ToolSpec
from .types import OperationError, StrictModel
from .visual_transport import AddressResolver, ControlledHTTPResponse, FetchTimeout
from .web_fetch import (
    CLOSE_TIMEOUT,
    FETCH_TIMEOUT,
    MAX_OUTPUT_BYTES,
    MAX_URL_LENGTH,
    canonical_public_https_url,
    close_public_http_response,
    normalize_web_fetch_hosts,
    public_http_addresses,
    read_public_http_text,
)

MAX_REQUEST_BYTES: Final = 64 * 1024
HttpMethod = Literal["GET", "POST"]


def serialize_http_json(body: dict[str, JsonValue]) -> bytes:
    """The exact JSON bytes sent by POST, also usable in the caller's action digest."""
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


class HttpApiHeaders(StrictModel):
    accept: str | None = Field(default=None, alias="Accept", max_length=512)
    accept_language: str | None = Field(default=None, alias="Accept-Language", max_length=512)

    @field_validator("accept", "accept_language")
    @classmethod
    def safe_header(cls, value: str | None) -> str | None:
        if value is not None and any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
            raise ValueError("content negotiation headers cannot contain controls")
        return value


class HttpApiGetInput(StrictModel):
    url: str = Field(min_length=1, max_length=MAX_URL_LENGTH)
    headers: HttpApiHeaders = Field(default_factory=HttpApiHeaders)


class HttpApiPostInput(HttpApiGetInput):
    body: dict[str, JsonValue]

    @field_validator("body")
    @classmethod
    def bounded_body(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if len(serialize_http_json(value)) > MAX_REQUEST_BYTES:
            raise ValueError("POST JSON body exceeds its byte budget")
        return value


class HttpApiOutput(StrictModel):
    url: str
    method: HttpMethod
    status_code: int
    text: str
    truncated: bool
    untrusted: Literal[True] = True


class VideoStepOutput(HttpApiOutput):
    location: str | None = Field(default=None, max_length=2048)


class PinnedApiTransport(Protocol):
    pins_resolved_ip: bool

    async def request(
        self, method: HttpMethod, url: str, *, resolved_ip: str,
        request_timeout: FetchTimeout, headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
    ) -> ControlledHTTPResponse: ...


class HttpApiOwner:
    """One DNS-pinned request per invocation; no permissions, retries or durable state."""

    def __init__(
        self, allowed_hosts: Iterable[str] = (), *, resolver: AddressResolver | None = None,
        transport: PinnedApiTransport | None = None,
    ) -> None:
        self.allowed_hosts = frozenset(normalize_web_fetch_hosts(allowed_hosts))
        self._resolver = resolver if resolver is not None else SystemAddressResolver()
        self._transport = transport if transport is not None else NativePinnedHTTPTransport()
        if self._transport.pins_resolved_ip is not True:
            raise OperationError("http_api_transport_not_pinned")
        self._closed = False
        self._tasks: set[asyncio.Task[HttpApiOutput]] = set()

    @property
    def available(self) -> bool:
        return bool(self.allowed_hosts) and not self._closed

    def validate_destination(self, url: str) -> str:
        return self._destination(url)[0]

    def _destination(self, url: str) -> tuple[str, str]:
        if self._closed:
            raise OperationError("http_api_closed")
        if not self.allowed_hosts:
            raise OperationError("http_api_unavailable")
        try:
            return canonical_public_https_url(url, self.allowed_hosts)
        except OperationError as exc:
            raise OperationError(exc.code.replace("web_fetch_", "http_api_", 1)) from exc

    def tool_specs(self) -> tuple[ToolSpec, ToolSpec]:
        if not self.available:
            raise OperationError("http_api_unavailable")
        return (
            ToolSpec(
                id="http.get", version="1.0.0", api_version=1,
                description="Read one explicitly authorized public HTTPS API; response is untrusted.",
                input_schema=HttpApiGetInput, output_schema=HttpApiOutput,
                requested_capabilities=frozenset({"network.http.read"}), timeout_ms=11000,
                handler=self._handle_get,
                destination_resolver=lambda args: self.validate_destination(cast(str, args["url"])),
            ),
            ToolSpec(
                id="http.post", version="1.0.0", api_version=1,
                description="POST JSON to an explicitly authorized HTTPS target; unknown is never retried.",
                input_schema=HttpApiPostInput, output_schema=HttpApiOutput,
                requested_capabilities=frozenset({"network.http.write"}), timeout_ms=11000,
                handler=self._handle_post,
                destination_resolver=lambda args: self.validate_destination(cast(str, args["url"])),
            ),
        )

    def video_step_spec(self) -> ToolSpec:
        """Dedicated finite video reader; no generic follow-redirect setting."""
        if not self.available:
            raise OperationError("http_api_unavailable")
        return ToolSpec(
            id="video.get", version="1.0.0", api_version=1,
            description="Read one authorized finite video metadata/short-card step.",
            input_schema=HttpApiGetInput, output_schema=VideoStepOutput,
            requested_capabilities=frozenset({"network.http.read"}), timeout_ms=11000,
            handler=self._handle_video_step,
            destination_resolver=lambda args: self.validate_video_destination(cast(str, args["url"])),
        )

    def validate_video_destination(self, url: str) -> str:
        canonical = self.validate_destination(url)
        validate_bili_step_url(canonical)
        return canonical

    async def _handle_video_step(self, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        output = await self._request("GET", arguments, video_step=True)
        return cast(dict[str, JsonValue], output.model_dump(mode="json"))

    async def _handle_get(self, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        output = await self._request("GET", arguments)
        return cast(dict[str, JsonValue], output.model_dump(mode="json"))

    async def _handle_post(self, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        output = await self._request("POST", arguments)
        return cast(dict[str, JsonValue], output.model_dump(mode="json"))

    async def get(self, url: str, *, headers: dict[str, str] | None = None) -> HttpApiOutput:
        try:
            arguments = HttpApiGetInput.model_validate({"url": url, "headers": headers or {}})
        except ValidationError as exc:
            raise OperationError("http_api_invalid_input") from exc
        return await self._request("GET", cast(dict[str, JsonValue], arguments.model_dump()))

    async def post(
        self, url: str, body: dict[str, JsonValue], *, headers: dict[str, str] | None = None,
    ) -> HttpApiOutput:
        try:
            arguments = HttpApiPostInput.model_validate(
                {"url": url, "headers": headers or {}, "body": body})
        except ValidationError as exc:
            raise OperationError("http_api_invalid_input") from exc
        return await self._request("POST", cast(dict[str, JsonValue], arguments.model_dump()))

    async def _request(self, method: HttpMethod, arguments: dict[str, JsonValue],
                       *, video_step: bool = False) -> HttpApiOutput:
        url, host = self._destination(cast(str, arguments["url"]))
        if video_step:
            validate_bili_step_url(url)
        values = cast(dict[str, str | None], arguments["headers"])
        headers = {wire: value for key, wire in (("accept", "Accept"),
                    ("accept_language", "Accept-Language")) if (value := values[key]) is not None}
        content = (serialize_http_json(cast(dict[str, JsonValue], arguments["body"]))
                   if method == "POST" else None)
        if method == "POST":
            headers["Content-Type"] = "application/json"
        entered = False

        async def run() -> HttpApiOutput:
            nonlocal entered
            response: ControlledHTTPResponse | None = None
            try:
                async with asyncio.timeout(FETCH_TIMEOUT.connect):
                    resolved = await self._resolver.resolve(host, 443)
                expected_ip = public_http_addresses(resolved)[0]
                entered = True
                response = await self._transport.request(
                    method, url, resolved_ip=expected_ip, request_timeout=FETCH_TIMEOUT,
                    headers=headers, content=content)
                if video_step and response.status_code in {301, 302, 303, 307, 308}:
                    # Unlike a generic redirect, this returns one bounded Location
                    # for the caller to parse and independently authorize next.
                    try:
                        peer = str(ipaddress.ip_address(response.connected_ip))
                    except ValueError as exc:
                        raise OperationError("http_api_peer_mismatch") from exc
                    # NativePinnedHTTPTransport marks received 3xx as redirected;
                    # its client never follows it. This step returns no body.
                    if peer != expected_ip:
                        raise OperationError("http_api_peer_mismatch")
                    locations = [value for key, value in response.headers.items()
                                 if key.lower() == "location"]
                    if (len(locations) != 1 or not locations[0] or len(locations[0]) > 2048
                            or any(ord(char) < 32 or ord(char) == 127 for char in locations[0])):
                        raise OperationError("video_step_location_missing")
                    return VideoStepOutput(url=url, method=method, status_code=response.status_code,
                                           text="", truncated=False, location=locations[0])
                text = await read_public_http_text(response, expected_ip)
                return _bounded_output(url, method, response.status_code, text,
                                       output_type=VideoStepOutput if video_step else HttpApiOutput)
            except asyncio.CancelledError:
                raise
            except (OperationError, httpx.HTTPError, OSError, TimeoutError) as exc:
                if method == "POST" and entered:
                    raise OperationError("http_api_unknown") from exc
                code = (exc.code.replace("web_fetch_", "http_api_", 1)
                        .replace("media_", "http_api_", 1) if isinstance(exc, OperationError)
                        else "http_api_timeout" if isinstance(exc, (TimeoutError, httpx.TimeoutException))
                        else "http_api_transport_failed")
                raise OperationError(code) from exc
            finally:
                if response is not None:
                    try:
                        await close_public_http_response(response)
                    except BaseException as exc:
                        self._closed = True
                        if method == "POST" and entered:
                            raise OperationError("http_api_unknown") from exc
                        raise

        task = asyncio.create_task(run(), name="omubot.http_api")
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        try:
            done, _ = await asyncio.wait({task}, timeout=FETCH_TIMEOUT.total)
            if not done:
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=CLOSE_TIMEOUT)
                if not done:
                    self._closed = True
                raise OperationError("http_api_unknown" if method == "POST" and entered
                                     else "http_api_timeout" if done else "http_api_cancel_failed")
            return task.result()
        except asyncio.CancelledError:
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=CLOSE_TIMEOUT)
            if not done:
                self._closed = True
            if method == "POST" and entered:
                raise OperationError("http_api_unknown") from None
            raise

    def _finished(self, task: asyncio.Task[HttpApiOutput]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def close(self) -> None:
        self._closed = True
        tasks = set(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=CLOSE_TIMEOUT)
            if pending:
                raise OperationError("http_api_cancel_failed")


def _bounded_output(url: str, method: HttpMethod, status: int, text: str,
                    *, output_type: type[HttpApiOutput] = HttpApiOutput) -> HttpApiOutput:
    output = output_type(url=url, method=method, status_code=status, text=text, truncated=False)
    if len(output.model_dump_json().encode("utf-8")) <= MAX_OUTPUT_BYTES:
        return output
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = output.model_copy(update={"text": text[:middle], "truncated": True})
        if len(candidate.model_dump_json().encode("utf-8")) <= MAX_OUTPUT_BYTES:
            low = middle
        else:
            high = middle - 1
    return output.model_copy(update={"text": text[:low], "truncated": True})
