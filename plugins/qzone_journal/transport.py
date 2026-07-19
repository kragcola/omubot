"""Credential and HTTP boundary for QZone Journal.

The live QZone CGI wire contract is intentionally fail-closed until a
sanitized, versioned profile has been verified.  This module never persists
credentials and keeps all secret-bearing values out of object reprs and
dry-run descriptors.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlsplit

import httpx

from plugins.qzone_journal.response_parser import parse_qzone_publish_response

_QZONE_COOKIE_DOMAIN = "user.qzone.qq.com"


class CredentialError(RuntimeError):
    """NapCat could not provide a complete, internally consistent credential."""


class UnverifiedWireProfileError(RuntimeError):
    """A network request was attempted with an unverified QZone wire profile."""


def compute_g_tk(p_skey: str) -> int:
    """Return QZone's deterministic hash33 token for a ``p_skey`` value."""

    value = 5381
    for character in str(p_skey):
        value = (value + (value << 5) + ord(character)) & 0x7FFFFFFF
    return value


@dataclass(frozen=True, slots=True, repr=False)
class CredentialBundle:
    """Ephemeral QZone credentials.

    Secret fields deliberately have no dataclass repr.  Callers must keep this
    object in memory only and must not serialize it.
    """

    uin: str
    cookie_header: str = field(repr=False)
    p_skey: str = field(repr=False)
    g_tk: int = field(repr=False)

    def __repr__(self) -> str:
        return f"CredentialBundle(uin={self.uin!r}, credentials='<redacted>')"


@dataclass(frozen=True, slots=True)
class WireProfile:
    """Versioned description of a QZone text-publish request."""

    profile_id: str
    endpoint: str
    validated: bool
    content_field: str
    uin_field: str
    static_form_fields: Mapping[str, str] = field(default_factory=dict)
    max_content_chars: int = 2000

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.path:
            raise ValueError("QZone wire endpoint must be a fixed HTTPS URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "QZone wire endpoint must not contain credentials, query or fragments"
            )
        if parsed.hostname != _QZONE_COOKIE_DOMAIN:
            raise ValueError("QZone wire endpoint host is not allowlisted")
        if not self.profile_id.strip():
            raise ValueError("wire profile_id is required")
        if not self.content_field.strip() or not self.uin_field.strip():
            raise ValueError("wire content and uin fields are required")
        if self.max_content_chars <= 0:
            raise ValueError("max_content_chars must be positive")


@dataclass(frozen=True, slots=True)
class BuiltRequest:
    request: httpx.Request
    follow_redirects: bool = False


def _unwrap_action_payload(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CredentialError("NapCat credential response is not an object")
    nested = value.get("data")
    if isinstance(nested, Mapping):
        return nested
    return value


def _normalize_uin(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("o"):
        text = text[1:]
    text = text.lstrip("0") or "0"
    if not text.isdigit() or text == "0":
        raise CredentialError("uin is missing or invalid")
    return text


def _parse_cookie_header(raw_cookie: str) -> dict[str, str]:
    cookie = SimpleCookie()
    try:
        cookie.load(raw_cookie)
    except Exception as exc:
        raise CredentialError("QZone cookie header is malformed") from exc
    return {name: morsel.value for name, morsel in cookie.items()}


class NapCatCredentialSource:
    """Acquire ephemeral QZone credentials through the connected OneBot bot."""

    def __init__(self, *, bot: Any) -> None:
        self._bot = bot

    async def acquire(self) -> CredentialBundle:
        login_payload = _unwrap_action_payload(
            await self._bot.call_api("get_login_info")
        )
        login_uin = _normalize_uin(
            login_payload.get("user_id", login_payload.get("uin"))
        )
        cookie_payload = _unwrap_action_payload(
            await self._bot.call_api(
                "get_cookies",
                domain=_QZONE_COOKIE_DOMAIN,
            )
        )
        raw_cookie = str(
            cookie_payload.get("cookies", cookie_payload.get("cookie", "")) or ""
        ).strip()
        if not raw_cookie:
            raise CredentialError("QZone cookies are missing")
        values = _parse_cookie_header(raw_cookie)
        p_skey = str(values.get("p_skey", "") or "").strip()
        if not p_skey:
            raise CredentialError("p_skey is missing from QZone cookies")
        cookie_uin = _normalize_uin(values.get("uin"))
        if cookie_uin != login_uin:
            raise CredentialError("uin mismatch between login and QZone cookies")
        return CredentialBundle(
            uin=login_uin,
            cookie_header=raw_cookie,
            p_skey=p_skey,
            g_tk=compute_g_tk(p_skey),
        )


class QZoneRequestBuilder:
    """Build one allowlisted, non-redirecting QZone text request."""

    def __init__(self, profile: WireProfile) -> None:
        self._profile = profile

    def build(self, *, credentials: CredentialBundle, text: str) -> BuiltRequest:
        profile = self._profile
        if not profile.validated:
            raise UnverifiedWireProfileError(
                f"QZone wire profile is not validated: {profile.profile_id}"
            )
        content = str(text)
        if not content.strip():
            raise ValueError("QZone journal text must not be empty")
        if len(content) > profile.max_content_chars:
            raise ValueError("QZone journal text exceeds the wire profile limit")
        if not credentials.uin.isdigit():
            raise CredentialError("credential uin is invalid")
        if credentials.g_tk != compute_g_tk(credentials.p_skey):
            raise CredentialError("credential g_tk does not match p_skey")

        form = {
            **{str(key): str(value) for key, value in profile.static_form_fields.items()},
            profile.content_field: content,
            profile.uin_field: credentials.uin,
        }
        request = httpx.Request(
            "POST",
            profile.endpoint,
            params={"g_tk": str(credentials.g_tk)},
            data=form,
            headers={
                "Cookie": credentials.cookie_header,
                "Origin": "https://user.qzone.qq.com",
                "Referer": f"https://user.qzone.qq.com/{credentials.uin}",
            },
        )
        return BuiltRequest(request=request, follow_redirects=False)


class QZoneTransport:
    """QZone text transport with a secret-free dry-run surface."""

    def __init__(self, *, http_client: Any | None = None) -> None:
        self._http_client = http_client or httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = http_client is None

    async def publish(
        self,
        *,
        profile: WireProfile,
        credentials: CredentialBundle,
        text: str,
    ) -> dict[str, Any]:
        """POST one validated-profile request and return a secret-free result.

        The HTTP client response is never returned raw.  Callers receive only
        the normalized parser outcome (``published`` / ``failed`` /
        ``ambiguous``).  An unverified wire profile still fails closed before
        any network I/O.
        """

        built = QZoneRequestBuilder(profile).build(
            credentials=credentials,
            text=text,
        )
        response = await self._http_client.send(
            built.request,
            follow_redirects=built.follow_redirects,
        )
        return parse_qzone_publish_response(response)

    def dry_run(
        self,
        *,
        profile: WireProfile,
        credentials: CredentialBundle,
        text: str,
    ) -> dict[str, Any]:
        built = QZoneRequestBuilder(profile).build(
            credentials=credentials,
            text=text,
        )
        del built
        return self.describe(profile=profile, text=text)

    def describe(self, *, profile: WireProfile, text: str) -> dict[str, Any]:
        content = str(text)
        if not content.strip():
            raise ValueError("QZone journal text must not be empty")
        if len(content) > profile.max_content_chars:
            raise ValueError("QZone journal text exceeds the wire profile limit")
        parsed = urlsplit(profile.endpoint)
        form_keys = sorted(
            {
                *profile.static_form_fields,
                profile.content_field,
                profile.uin_field,
            }
        )
        return {
            "profile_id": profile.profile_id,
            "validated": profile.validated,
            "method": "POST",
            "host": parsed.hostname or "",
            "path": parsed.path,
            "field_names": form_keys,
            "content_chars": len(content),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "follow_redirects": False,
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http_client.aclose()


__all__ = [
    "BuiltRequest",
    "CredentialBundle",
    "CredentialError",
    "NapCatCredentialSource",
    "QZoneRequestBuilder",
    "QZoneTransport",
    "UnverifiedWireProfileError",
    "WireProfile",
    "compute_g_tk",
    "parse_qzone_publish_response",
]
