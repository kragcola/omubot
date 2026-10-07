"""Bounded, local-only access to the NapCat WebUI HTTP API.

The adapter deliberately owns only an in-memory WebUI credential.  It never
persists the supplied token and it does not fetch QR-code URLs on behalf of a
caller.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
from collections.abc import Callable
from typing import Final, cast
from urllib.parse import urlsplit, urlunsplit

import httpx

_REQUEST_TIMEOUT: Final[float] = 5.0
_LOG_TIMEOUT: Final[float] = 2.0
_MAX_RESPONSE_BYTES: Final[int] = 128 * 1024
_MAX_LOG_BYTES: Final[int] = 32 * 1024
_MAX_LOG_LINES: Final[int] = 100
_MAX_QR_URL_LENGTH: Final[int] = 4096
_MAX_PHASE_LENGTH: Final[int] = 256
_MAX_TOKEN_LENGTH: Final[int] = 4096
_MAX_CREDENTIAL_LENGTH: Final[int] = 16384
_OLD_INSTANCE_PORT: Final[int] = 6099
_KNOWN_PHASES: Final[frozenset[str]] = frozenset(
    {
        "",
        "initializing",
        "qrcode",
        "qrcode_scanned",
        "login",
        "logging_in",
        "reconnecting",
        "online",
        "offline",
        "loading",
        "confirm",
        "success",
        "failed",
        "error",
    }
)

_LOGIN_PATH: Final[str] = "/api/auth/login"
_STATUS_PATH: Final[str] = "/api/QQLogin/CheckLoginStatus"
_REFRESH_QR_PATH: Final[str] = "/api/QQLogin/RefreshQRcode"
_LOG_PATH: Final[str] = "/api/Log/GetLogRealTime"

_ANSI_RE: Final[re.Pattern[str]] = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)
_SENSITIVE_QUERY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?P<prefix>(?:^|(?<=[\s?&]))"
    r"(?:token|key|api[_-]?key|access[_-]?token|credential|authorization|hash)=)"
    r"[^\s&#]+"
)


class NapCatError(Exception):
    """Stable, body-free adapter error returned to the web boundary."""

    code: str

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _has_control(value: str) -> bool:
    return any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)


def _validate_endpoint(endpoint: str) -> str:
    if type(endpoint) is not str or not endpoint or endpoint != endpoint.strip() or _has_control(endpoint):
        raise NapCatError("invalid_endpoint")
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise NapCatError("invalid_endpoint") from None
    if (
        parsed.scheme not in {"http", "https"}
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is None
        or not 1 <= port <= 65535
        or port == _OLD_INSTANCE_PORT
    ):
        raise NapCatError("invalid_endpoint")

    normalized_host = host.lower()
    if normalized_host == "localhost":
        normalized_host = "127.0.0.1"
    else:
        try:
            address = ipaddress.ip_address(normalized_host)
        except ValueError:
            raise NapCatError("invalid_endpoint") from None
        if not address.is_loopback:
            raise NapCatError("invalid_endpoint")
        normalized_host = address.compressed

    if ":" in normalized_host:
        netloc = f"[{normalized_host}]:{port}"
    else:
        netloc = f"{normalized_host}:{port}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def _validate_token(token: str) -> str:
    if (
        type(token) is not str
        or not token
        or token != token.strip()
        or _has_control(token)
        or len(token) > _MAX_TOKEN_LENGTH
    ):
        raise NapCatError("invalid_token")
    return token


def _validate_totp(totp_code: str) -> str:
    if (
        type(totp_code) is not str
        or len(totp_code) > 128
        or (totp_code and (totp_code != totp_code.strip() or _has_control(totp_code)))
    ):
        raise NapCatError("invalid_totp")
    return totp_code


def _validate_credential(credential: object) -> str:
    if (
        not isinstance(credential, str)
        or not credential
        or credential != credential.strip()
        or _has_control(credential)
        or len(credential) > _MAX_CREDENTIAL_LENGTH
    ):
        raise NapCatError("invalid_response")
    try:
        credential.encode("ascii")
    except UnicodeEncodeError:
        raise NapCatError("invalid_response") from None
    return credential


def _validate_qr_url(value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise NapCatError("invalid_response")
    if allow_empty and not value:
        return ""
    if (
        not value
        or len(value) > _MAX_QR_URL_LENGTH
        or value != value.strip()
        or _has_control(value)
    ):
        raise NapCatError("invalid_response")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise NapCatError("invalid_response") from None
    if (
        parsed.scheme != "https"
        or host is None
        or (host.lower() != "qq.com" and not host.lower().endswith(".qq.com"))
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise NapCatError("invalid_response")
    return value


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise NapCatError("invalid_response")
    return cast(dict[str, object], value)


class NapCatClient:
    """A small, bounded NapCat WebUI client for one local instance."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        totp_code: str = "",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = _validate_endpoint(endpoint)
        self._token = _validate_token(token)
        self._totp_code = _validate_totp(totp_code)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._credential: str | None = None
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise NapCatError("client_closed")

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def _ensure_client(self) -> httpx.AsyncClient:
        self._ensure_open()
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._endpoint,
                timeout=_REQUEST_TIMEOUT,
                trust_env=False,
                follow_redirects=False,
                transport=self._transport,
            )
        return self._client

    def _auth_headers(self) -> dict[str, str]:
        credential = self._credential
        if credential is None:
            raise NapCatError("not_connected")
        return {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Authorization": "Bearer " + credential,
        }

    @staticmethod
    def _response_status(
        response: httpx.Response, *, clear_credential: Callable[[], None] | None = None
    ) -> None:
        status = response.status_code
        if 300 <= status < 400:
            raise NapCatError("redirect_denied")
        if status == 401:
            if clear_credential is not None:
                clear_credential()
            raise NapCatError("unauthorized")
        if not 200 <= status < 300:
            raise NapCatError("upstream_http")

    @staticmethod
    def _content_length(response: httpx.Response, limit: int, error_code: str) -> None:
        raw_length = response.headers.get("content-length")
        if raw_length is None:
            return
        try:
            length = int(raw_length)
        except ValueError:
            return
        if length < 0 or length > limit:
            raise NapCatError(error_code)

    async def _request_envelope(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        authenticated: bool,
    ) -> dict[str, object]:
        client = self._ensure_client()
        headers = self._auth_headers() if authenticated else {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT):
                async with client.stream(
                    method,
                    path,
                    headers=headers,
                    json=payload,
                    follow_redirects=False,
                ) as response:
                    self._response_status(
                        response, clear_credential=self._clear_credential if authenticated else None
                    )
                    self._content_length(response, _MAX_RESPONSE_BYTES, "response_too_large")
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                            raise NapCatError("response_too_large")
                        body.extend(chunk)
        except NapCatError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise NapCatError("upstream_unavailable") from None

        try:
            parsed = json.loads(bytes(body))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError):
            raise NapCatError("invalid_response") from None
        envelope = _object(parsed)
        code = envelope.get("code")
        if type(code) is not int:
            raise NapCatError("invalid_response")
        if code != 0:
            raise NapCatError("request_rejected")
        return envelope

    def _clear_credential(self) -> None:
        self._credential = None

    async def connect(self) -> None:
        """Authenticate once and keep the authentication material in memory."""
        self._ensure_open()
        if self._credential is not None:
            return
        payload: dict[str, object] = {
            "hash": hashlib.sha256((self._token + ".napcat").encode("utf-8")).hexdigest()
        }
        if self._totp_code:
            payload["totpCode"] = self._totp_code
        envelope = await self._request_envelope("POST", _LOGIN_PATH, payload=payload, authenticated=False)
        data = _object(envelope.get("data"))
        if data.get("require2FA") is True:
            raise NapCatError("two_factor_required")
        self._credential = _validate_credential(data.get("Credential"))
        self._totp_code = ""

    async def status(self) -> dict[str, object]:
        """Return the small status shape used by the local web boundary."""
        envelope = await self._request_envelope("POST", _STATUS_PATH, authenticated=True)
        data = _object(envelope.get("data"))
        logged_in = data.get("isLogin")
        offline = data.get("isOffline")
        if type(logged_in) is not bool or type(offline) is not bool:
            raise NapCatError("invalid_response")
        qr_url = _validate_qr_url(data.get("qrcodeurl"), allow_empty=True)
        if "loginPhase" in data:
            phase = data["loginPhase"]
            if not isinstance(phase, str) or len(phase) > _MAX_PHASE_LENGTH or _has_control(phase):
                raise NapCatError("invalid_response")
        elif logged_in:
            phase = "online"
        elif offline:
            phase = "offline"
        elif qr_url:
            phase = "qrcode"
        else:
            phase = "initializing"
        safe_phase = phase if phase in _KNOWN_PHASES else "unknown"
        return {
            "logged_in": logged_in,
            "offline": offline,
            "phase": safe_phase,
            "qr_url": qr_url,
        }

    async def refresh_qr(self) -> str:
        """Ask NapCat for a QR URL; the returned URL is never fetched here."""
        envelope = await self._request_envelope("POST", _REFRESH_QR_PATH, authenticated=True)
        data = envelope.get("data")
        if data is None:
            return ""
        data = _object(data)
        return _validate_qr_url(data.get("qrcodeurl"), allow_empty=True)

    async def _consume_sse(self, response: httpx.Response, lines: list[str]) -> None:
        pending = bytearray()
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > _MAX_LOG_BYTES:
                raise NapCatError("log_output_limit")
            pending.extend(chunk)
            while b"\n" in pending:
                raw_line, _, remainder = bytes(pending).partition(b"\n")
                pending = bytearray(remainder)
                self._append_sse_line(raw_line, lines)
                if len(lines) >= _MAX_LOG_LINES:
                    return
        if pending:
            self._append_sse_line(bytes(pending), lines)

    def _append_sse_line(self, raw_line: bytes, lines: list[str]) -> None:
        if not raw_line.startswith(b"data:"):
            return
        value = raw_line[5:]
        if value.startswith(b" "):
            value = value[1:]
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            raise NapCatError("invalid_response") from None
        lines.append(self._sanitize_log(text.rstrip("\r")))

    def _sanitize_log(self, text: str) -> str:
        sanitized = _ANSI_RE.sub("", text)
        sanitized = sanitized.replace(self._token, "[REDACTED]")
        if self._credential is not None:
            sanitized = sanitized.replace(self._credential, "[REDACTED]")
        return _SENSITIVE_QUERY_RE.sub(
            lambda match: cast(str, match.group("prefix")) + "[REDACTED]", sanitized
        )

    async def logs(self) -> list[str]:
        """Collect a bounded, at-most-two-second sample of the live SSE stream."""
        client = self._ensure_client()
        headers = self._auth_headers()
        lines: list[str] = []
        response_ready = False
        try:
            async with asyncio.timeout(_LOG_TIMEOUT):
                async with client.stream(
                    "GET",
                    _LOG_PATH,
                    headers={**headers, "Accept": "text/event-stream"},
                    follow_redirects=False,
                ) as response:
                    self._response_status(response, clear_credential=self._clear_credential)
                    self._content_length(response, _MAX_RESPONSE_BYTES, "response_too_large")
                    content_type = response.headers.get("content-type", "")
                    if content_type and "text/event-stream" not in content_type.lower():
                        raise NapCatError("invalid_response")
                    response_ready = True
                    await self._consume_sse(response, lines)
        except NapCatError:
            raise
        except TimeoutError:
            if response_ready:
                return lines
            raise NapCatError("upstream_unavailable") from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise NapCatError("upstream_unavailable") from None
        return lines

    async def close(self) -> None:
        """Close transport resources and drop the in-memory credential."""
        client = self._client
        self._client = None
        self._credential = None
        self._token = ""
        self._totp_code = ""
        self._closed = True
        if client is not None and not client.is_closed:
            try:
                await client.aclose()
            except Exception:
                pass
