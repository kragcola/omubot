"""Finite text-only QZone CGI adapter, inactive until trusted runtime validation.

Request shape and strict response classification are inherited from legacy
v0.8.2 transport/delivery, not its cookies, attestation or deployment authority.
Root alone supplies a verified runtime result; no Web Boolean validates a wire.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from typing import NoReturn, cast

import httpx

from .journal import journal_digest, validate_journal_hash
from .types import OperationError, SendReceipt

ENDPOINT = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
PROFILE_ID = "qzone-text-v1-unverified"
STATIC_FORM = {"format": "json", "feedversion": "1", "ver": "1", "ugc_right": "1"}
CONTRACT_SHA256 = journal_digest([ENDPOINT, "con", "hostuin", STATIC_FORM, "strict-json-or-jsonp-v1"])
_CODE_KEYS = ("code", "ret", "errcode", "error_code")
_REMOTE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}\Z")
_JSONP = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\((.*)\);?\Z", re.DOTALL)


class JournalPublishRejected(OperationError):
    """A local pre-POST failure or explicit CGI rejection; no ambiguous success."""


@dataclass(frozen=True, slots=True)
class QZoneRuntimeValidation:
    """Trusted bootstrap output of stage-three sanitized capture verification.

    This DTO records identity bindings, not a user-configurable validated flag.
    An ordinary Web/config request must never be accepted as its constructor input.
    No runtime validation artifact is created by the phase-two implementation.
    """

    profile_id: str
    account_id: str
    request_contract_sha256: str
    validated_capture_sha256: str

    def __post_init__(self) -> None:
        if self.profile_id != PROFILE_ID or self.request_contract_sha256 != CONTRACT_SHA256:
            raise OperationError("journal_wire_contract_mismatch")
        if (
            type(self.account_id) is not str
            or not self.account_id.isdigit()
            or not self.account_id.strip("0")
            or len(self.account_id) > 20
        ):
            raise OperationError("journal_wire_account_invalid")
        validate_journal_hash(self.validated_capture_sha256)


@dataclass(frozen=True, slots=True, repr=False)
class QZoneCredentials:
    uin: str = field(repr=False)
    cookie_header: str = field(repr=False)
    p_skey: str = field(repr=False)

    def __repr__(self) -> str:
        return "QZoneCredentials(<redacted>)"


def g_tk(p_skey: str) -> int:
    token = 5381
    for char in p_skey:
        token = (token + (token << 5) + ord(char)) & 0x7FFFFFFF
    return token


def build_request(body: str, credentials: QZoneCredentials) -> httpx.Request:
    """Pure request builder; publication owner performs trusted validation first."""
    if type(body) is not str or not body.strip() or len(body) > 280:
        raise JournalPublishRejected("journal_body_invalid")
    if (
        type(credentials) is not QZoneCredentials
        or not credentials.uin.isdigit()
        or not credentials.uin.strip("0")
        or not 1 <= len(credentials.uin) <= 20
        or not credentials.p_skey
        or len(credentials.p_skey) > 4096
        or len(credentials.cookie_header) > 8192
        or any(c in credentials.cookie_header for c in "\r\n")
    ):
        raise JournalPublishRejected("journal_credentials_invalid")
    cookie = SimpleCookie()
    try:
        cookie.load(credentials.cookie_header)
    except CookieError:
        raise JournalPublishRejected("journal_credentials_invalid") from None
    if (
        "p_skey" not in cookie
        or cookie["p_skey"].value != credentials.p_skey
        or "uin" not in cookie
        or cookie["uin"].value.removeprefix("o").lstrip("0") != credentials.uin.lstrip("0")
    ):
        raise JournalPublishRejected("journal_credentials_invalid")
    return httpx.Request(
        "POST",
        ENDPOINT,
        params={"g_tk": str(g_tk(credentials.p_skey))},
        data={**STATIC_FORM, "con": body, "hostuin": credentials.uin},
        headers={
            "Cookie": credentials.cookie_header,
            "Origin": "https://user.qzone.qq.com",
            "Referer": "https://user.qzone.qq.com/" + credentials.uin,
        },
    )


@dataclass(frozen=True, slots=True)
class QZoneResponse:
    state: str
    remote_id: str = ""
    code: str = ""


def _response_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate response field")
        value[key] = item
    return value


def _invalid_constant(_value: str) -> NoReturn:
    raise ValueError("invalid JSON constant")


def parse_response(status_code: int, body: bytes) -> QZoneResponse:
    """No body/error prose escape; 200 alone, missing/conflicting IDs => unknown."""
    if not 200 <= status_code < 300 or len(body) > 65536:
        return QZoneResponse("unknown", code="http_or_body_boundary")
    try:
        text = body.decode("utf-8-sig").strip()
    except UnicodeDecodeError:
        return QZoneResponse("unknown", code="invalid_encoding")
    if not text.startswith("{"):
        match = _JSONP.fullmatch(text)
        if match is None or len(match[1]) > 64:
            return QZoneResponse("unknown", code="invalid_envelope")
        text = match[2]
    try:
        value: object = json.loads(text, object_pairs_hook=_response_object, parse_constant=_invalid_constant)
    except (ValueError, RecursionError):
        return QZoneResponse("unknown", code="invalid_json")
    if type(value) is not dict:
        return QZoneResponse("unknown", code="invalid_json")
    payload = cast(dict[str, object], value)
    codes = [payload[key] for key in _CODE_KEYS if key in payload]
    error = payload.get("err")
    if isinstance(error, dict) and "code" in error:
        codes.append(cast(dict[str, object], error)["code"])
    normalized: list[int] = []
    for code in codes:
        if type(code) is int:
            normalized.append(code)
        elif type(code) is str and re.fullmatch(r"-?[0-9]{1,12}", code.strip()):
            normalized.append(int(code))
        else:
            return QZoneResponse("unknown", code="invalid_code")
    if not normalized or len(set(normalized)) != 1:
        return QZoneResponse("unknown", code="missing_or_conflicting_code")
    if normalized[0] != 0:
        return QZoneResponse("failed", code="cgi_rejected")
    containers = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        containers.append(cast(dict[str, object], data))
    ids: list[str] = []
    for container in containers:
        for key in ("tid", "remote_id"):
            if key not in container:
                continue
            remote = container[key]
            if type(remote) not in {str, int} or not _REMOTE_ID.fullmatch(str(remote)) or str(remote) == "0":
                return QZoneResponse("unknown", code="invalid_remote_id")
            ids.append(str(remote))
    if not ids or len(set(ids)) != 1:
        return QZoneResponse("unknown", code="missing_or_conflicting_remote_id")
    return QZoneResponse("published", remote_id=ids[0])


class QZoneHttpPublisher:
    is_external = True
    destination = "journal-text"

    def __init__(
        self,
        client: httpx.AsyncClient,
        acquire_credentials: Callable[[], Awaitable[QZoneCredentials]],
        *,
        runtime_validation: QZoneRuntimeValidation | None = None,
    ) -> None:
        if runtime_validation is not None and type(runtime_validation) is not QZoneRuntimeValidation:
            raise OperationError("journal_wire_validation_invalid")
        self.client, self.acquire_credentials = client, acquire_credentials
        self.runtime_validation = runtime_validation

    @property
    def wire_validated(self) -> bool:
        return self.runtime_validation is not None

    @property
    def validated_account_id(self) -> str:
        return "" if self.runtime_validation is None else self.runtime_validation.account_id

    async def publish(
        self, body: str, *, idempotency_key: str, before_post: Callable[[], Awaitable[None]]
    ) -> SendReceipt:
        if not self.wire_validated:
            raise JournalPublishRejected("journal_wire_unvalidated")
        credentials = await self.acquire_credentials()
        request = build_request(body, credentials)
        if credentials.uin != self.validated_account_id:
            raise JournalPublishRejected("journal_wire_account_mismatch")
        await before_post()
        # Stream bounds apply before decoding; never follow credential-bearing redirects.
        async with self.client.stream(
            request.method,
            request.url,
            headers=request.headers,
            content=request.content,
            follow_redirects=False,
            timeout=30,
        ) as response:
            chunks = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65537):
                if len(chunks) + len(chunk) > 65536:
                    raise OperationError("journal_response_unknown")
                chunks.extend(chunk)
            parsed = parse_response(response.status_code, bytes(chunks))
        if parsed.state == "failed":
            raise JournalPublishRejected("journal_remote_rejected")
        if parsed.state != "published":
            raise OperationError("journal_response_unknown")
        return SendReceipt(message_id=parsed.remote_id)
