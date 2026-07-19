"""Strict, secret-safe QZone CGI publish response classification.

This module never treats HTTP 200 alone as success.  It classifies an
``httpx.Response`` into one of three normalized outcomes:

* ``published`` — explicit success code/field **and** a nonempty allowlisted
  remote id (``tid`` / ``remote_id``)
* ``failed`` — explicit nonzero failure code/field, secret-free reason only
* ``ambiguous`` — anything else (malformed body, HTML, redirect, non-2xx,
  unknown schema, oversized body, missing remote id on a zero code)

Raw response bodies, cookies, ``p_skey``, ``g_tk``, tokens, and free-form
text content are never returned or included in reasons.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final, Literal

import httpx

ParseStatus = Literal["published", "failed", "ambiguous"]

# Conservative body cap: large enough for a normal CGI JSON envelope, small
# enough that HTML login pages and dump payloads do not get fully decoded.
_MAX_BODY_BYTES: Final[int] = 64 * 1024

# JSONP callback: identifiers / dotted member chains only (no parentheses,
# no brackets, no string/template injection surface).
_JSONP_CALLBACK_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
_JSONP_MAX_CALLBACK_LEN: Final[int] = 64

# Success/failure code keys commonly seen on QZone CGI envelopes.  Presence
# of a known zero/nonzero value is required; unknown schemas stay ambiguous.
_CODE_KEYS: Final[tuple[str, ...]] = ("code", "ret", "errcode", "error_code")

# Allowlisted remote-id keys only (top-level or under a single ``data`` object).
_REMOTE_ID_KEYS: Final[tuple[str, ...]] = ("tid", "remote_id")
# Max length 120 matches JournalStore.mark_published / admin external_post_id
# (store allows a slightly wider charset; parser stays a safe subset).
_REMOTE_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$"
)

_BOM: Final[bytes] = b"\xef\xbb\xbf"
_HTML_MARKERS: Final[tuple[bytes, ...]] = (
    b"<!doctype",
    b"<html",
    b"<head",
    b"<body",
    b"<script",
)


def _reason_only(code: str) -> dict[str, Any]:
    return {"status": "ambiguous", "reason": code}


def _failed(code: str) -> dict[str, Any]:
    return {"status": "failed", "reason": code}


def _published(remote_id: str) -> dict[str, Any]:
    return {"status": "published", "remote_id": remote_id}


def _looks_like_html(body: bytes) -> bool:
    sample = body.lstrip()[:512].lower()
    return any(marker in sample for marker in _HTML_MARKERS)


def _strip_bom_and_ws(raw: bytes) -> bytes:
    body = raw
    if body.startswith(_BOM):
        body = body[len(_BOM) :]
    return body.lstrip()


def _decode_utf8(body: bytes) -> str | None:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _unwrap_jsonp(text: str) -> tuple[str | None, str | None]:
    """Return ``(json_text, error_reason)``.  One of the two is always set."""

    stripped = text.strip()
    if not stripped:
        return None, "empty_body"

    # Optional trailing semicolon after the JSONP wrapper.
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()

    open_paren = stripped.find("(")
    if open_paren <= 0 or not stripped.endswith(")"):
        return None, "not_jsonp"

    callback = stripped[:open_paren].strip()
    if not callback or len(callback) > _JSONP_MAX_CALLBACK_LEN:
        return None, "invalid_jsonp_callback"
    if not _JSONP_CALLBACK_RE.fullmatch(callback):
        return None, "invalid_jsonp_callback"

    inner = stripped[open_paren + 1 : -1].strip()
    if not inner:
        return None, "empty_jsonp_payload"
    return inner, None


def _loads_json_object(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _coerce_code(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _extract_code(payload: dict[str, Any]) -> int | None:
    values: list[int] = []
    for key in _CODE_KEYS:
        if key not in payload:
            continue
        parsed = _coerce_code(payload[key])
        if parsed is None:
            return None
        values.append(parsed)

    # Nested err.code style without free-form message fields.
    nested = payload.get("err")
    if isinstance(nested, dict) and "code" in nested:
        parsed = _coerce_code(nested["code"])
        if parsed is None:
            return None
        values.append(parsed)

    if not values or len(set(values)) != 1:
        return None
    return values[0]


def _normalize_remote_id(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    if text == "0" or not _REMOTE_ID_RE.fullmatch(text):
        return None
    return text


def _extract_remote_id(payload: dict[str, Any]) -> str | None:
    values: list[str] = []
    containers = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        containers.append(data)

    for container in containers:
        for key in _REMOTE_ID_KEYS:
            if key not in container:
                continue
            parsed = _normalize_remote_id(container[key])
            if parsed is None:
                return None
            values.append(parsed)

    if not values or len(set(values)) != 1:
        return None
    return values[0]


def _classify_payload(payload: dict[str, Any]) -> dict[str, Any]:
    code = _extract_code(payload)
    if code is None:
        return _reason_only("unknown_schema")

    if code != 0:
        # Explicit CGI failure — do not surface message text or body.
        return _failed("cgi_error")

    remote_id = _extract_remote_id(payload)
    if remote_id is None:
        return _reason_only("missing_remote_id")
    return _published(remote_id)


def _parse_body_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return _reason_only("empty_body")

    # Prefer raw JSON object when the body clearly starts as one.
    if stripped[0] in "{[":
        payload = _loads_json_object(stripped)
        if payload is None:
            return _reason_only("malformed_json")
        return _classify_payload(payload)

    # Otherwise require conservative JSONP.
    json_text, error = _unwrap_jsonp(stripped)
    if error is not None:
        # HTML/login pages that survived the byte-level HTML check still fall
        # here as non-JSON / non-JSONP and stay ambiguous without content.
        if stripped[:1] == "<" or stripped.lower().startswith("<!doctype"):
            return _reason_only("html_body")
        return _reason_only(error)

    assert json_text is not None
    payload = _loads_json_object(json_text)
    if payload is None:
        return _reason_only("malformed_jsonp")
    return _classify_payload(payload)


def parse_qzone_publish_response(response: httpx.Response) -> dict[str, Any]:
    """Classify a live publish ``httpx.Response`` into a secret-free dict.

    Returned shapes (never include body text or secrets):

    * ``{"status": "published", "remote_id": "<id>"}``
    * ``{"status": "failed", "reason": "<code>"}``
    * ``{"status": "ambiguous", "reason": "<code>"}``
    """

    # Redirects and non-2xx are never success, even with a JSON body.
    status_code = int(getattr(response, "status_code", 0) or 0)
    if 300 <= status_code < 400:
        return _reason_only("http_redirect")
    if status_code < 200 or status_code >= 300:
        return _reason_only("http_non_2xx")

    # Prefer content attribute; fall back to read()/aread already done by caller.
    raw = getattr(response, "content", None)
    if raw is None:
        return _reason_only("missing_body")
    if not isinstance(raw, (bytes, bytearray)):
        return _reason_only("missing_body")
    body = bytes(raw)

    if len(body) > _MAX_BODY_BYTES:
        return _reason_only("oversized_body")

    cleaned = _strip_bom_and_ws(body)
    if not cleaned:
        return _reason_only("empty_body")

    if _looks_like_html(cleaned):
        return _reason_only("html_body")

    text = _decode_utf8(cleaned)
    if text is None:
        return _reason_only("invalid_encoding")

    return _parse_body_text(text)


__all__ = [
    "ParseStatus",
    "parse_qzone_publish_response",
]
