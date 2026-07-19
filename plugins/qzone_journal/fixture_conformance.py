"""Versioned, secret-safe offline QZone fixture contract and conformance.

This module validates sanitized (or synthetic template) JSON fixtures against
a frozen schema, compares request fingerprints to a candidate WireProfile by
**names only**, and checks that ``parse_qzone_publish_response`` agrees with
the fixture's expected outcome.

Safety invariants:

* Never mutates a ``WireProfile`` and never creates ``validated=True``.
* ``profile_creation_eligible`` is advisory only; true solely when
  ``origin == real_sanitized`` and all structural + parser checks pass.
* Synthetic success always reports ``profile_creation_eligible=False``.
* No network I/O, no credential sources, no production DB.
* Secret-bearing keys/values are rejected at load time; reports never echo
  response bodies or secret material.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit

import httpx

from plugins.qzone_journal.response_parser import parse_qzone_publish_response
from plugins.qzone_journal.transport import WireProfile

FIXTURE_SCHEMA_VERSION: Final[int] = 1

# Align with response_parser body cap.
MAX_FIXTURE_BODY_BYTES: Final[int] = 64 * 1024
MAX_FIXTURE_FILE_BYTES: Final[int] = 128 * 1024

FixtureOrigin = Literal["synthetic", "real_sanitized"]
ExpectedStatus = Literal["published", "failed", "ambiguous"]

_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "origin",
        "note",
        "request_fingerprint",
        "response",
        "expected",
    }
)
_FINGERPRINT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "method",
        "scheme",
        "host",
        "path",
        "query_field_names",
        "form_field_names",
        "header_names",
        "follow_redirects",
    }
)
_RESPONSE_KEYS: Final[frozenset[str]] = frozenset(
    {"status_code", "headers", "body"}
)
_EXPECTED_KEYS: Final[frozenset[str]] = frozenset(
    {"status", "remote_id", "reason"}
)
_ALLOWED_RESPONSE_HEADERS: Final[frozenset[str]] = frozenset({"content-type"})

# Secret-bearing key names (case-insensitive) that must never appear as
# object keys outside the allowlisted fingerprint *name lists*.
_SECRET_KEY_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "cookie",
        "set-cookie",
        "set_cookie",
        "authorization",
        "p_skey",
        "skey",
        "g_tk",
        "g_tk_value",
        "access_token",
        "token",
        "raw_request",
        "request_content",
        "form_field_values",
        "header_values",
        "query_values",
        "query_field_values",
        "content",
        "text",
        "body_raw",
    }
)

# Substrings that indicate leaked credential material in free-form strings.
_SECRET_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?i)\bp_skey\s*="),
    re.compile(r"(?i)\bskey\s*="),
    re.compile(r"(?i)\bg_tk\s*="),
    re.compile(r"(?i)\baccess_token\s*="),
    re.compile(r"(?i)\bauthorization\s*:"),
    re.compile(r"(?i)\bset-cookie\s*:"),
    re.compile(r"(?i)bearer\s+[a-z0-9._\-+/=]{8,}"),
    re.compile(
        r"(?i)[\"']?(?:p_skey|skey|pt4_token|g_tk|access_token|token|"
        r"cookie|set-cookie|authorization|uin|hostuin)[\"']?\s*:"
    ),
)

_SAFE_REMOTE_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
)

# Field-name allowlist for fingerprint name lists (structure only).
_ALLOWED_QUERY_NAMES: Final[frozenset[str]] = frozenset({"g_tk"})
_ALLOWED_FORM_NAMES: Final[frozenset[str]] = frozenset(
    {
        "con",
        "hostuin",
        "format",
        "feedversion",
        "ver",
        "ugc_right",
    }
)
_ALLOWED_HEADER_NAMES: Final[frozenset[str]] = frozenset(
    {"Cookie", "Origin", "Referer"}
)


class FixtureValidationError(ValueError):
    """Fixture failed schema, type, ordering, or secret-safety validation."""


@dataclass(frozen=True, slots=True)
class RequestFingerprint:
    method: str
    scheme: str
    host: str
    path: str
    query_field_names: tuple[str, ...]
    form_field_names: tuple[str, ...]
    header_names: tuple[str, ...]
    follow_redirects: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "scheme": self.scheme,
            "host": self.host,
            "path": self.path,
            "query_field_names": list(self.query_field_names),
            "form_field_names": list(self.form_field_names),
            "header_names": list(self.header_names),
            "follow_redirects": self.follow_redirects,
        }


@dataclass(frozen=True, slots=True)
class FixtureResponse:
    status_code: int
    headers: Mapping[str, str]
    body: str


@dataclass(frozen=True, slots=True)
class FixtureExpected:
    status: ExpectedStatus
    remote_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SanitizedFixture:
    schema_version: int
    origin: FixtureOrigin
    request_fingerprint: RequestFingerprint
    response: FixtureResponse
    expected: FixtureExpected
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    """Secret-free offline conformance result (never includes body/secrets)."""

    ok: bool
    fixture_origin: FixtureOrigin
    request_match: bool
    response_match: bool
    profile_creation_eligible: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    parser_status: str | None = None
    expected_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "fixture_origin": self.fixture_origin,
            "request_match": self.request_match,
            "response_match": self.response_match,
            "profile_creation_eligible": self.profile_creation_eligible,
            "reasons": list(self.reasons),
            "parser_status": self.parser_status,
            "expected_status": self.expected_status,
        }


def _reject_unknown_keys(
    data: Mapping[str, Any],
    allowed: frozenset[str],
    *,
    where: str,
) -> None:
    unknown = sorted(set(data.keys()) - allowed)
    if unknown:
        raise FixtureValidationError(
            f"{where}: unknown keys not allowed: {', '.join(unknown)}"
        )


def _is_sorted_unique_names(names: list[str]) -> bool:
    if len(names) != len(set(names)):
        return False
    return names == sorted(names)


def _scan_secret_value(value: str, *, where: str) -> None:
    if not value:
        return
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.search(value):
            raise FixtureValidationError(
                f"{where}: secret-bearing value pattern rejected"
            )
    # Cookie-style name=value pairs for known secret cookie names.
    if re.search(r"(?i)\b(p_skey|skey|pt4_token|uin)\s*=\s*\S+", value):
        # Allow only pure identifier remote ids elsewhere; bodies with these
        # look like leaked cookies.
        raise FixtureValidationError(
            f"{where}: secret-bearing cookie-like value rejected"
        )


def _walk_secret_scan(obj: Any, *, where: str, allow_body_text: bool = False) -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            key_s = str(key)
            # Body is scanned separately with value patterns only; key "body"
            # itself is allowlisted under response.
            if key_s.lower() in {"body", "note"} and allow_body_text:
                if isinstance(value, str):
                    _scan_secret_value(value, where=f"{where}.{key_s}")
                continue
            if key_s in {
                "query_field_names",
                "form_field_names",
                "header_names",
            }:
                # Name lists may include Cookie / g_tk as *names*.
                if not isinstance(value, list):
                    raise FixtureValidationError(
                        f"{where}.{key_s}: must be a list of names"
                    )
                for name in value:
                    if not isinstance(name, str):
                        raise FixtureValidationError(
                            f"{where}.{key_s}: names must be strings"
                        )
                continue
            if key_s == "headers" and isinstance(value, Mapping):
                for hk, hv in value.items():
                    if str(hk).lower() not in _ALLOWED_RESPONSE_HEADERS:
                        raise FixtureValidationError(
                            f"{where}.headers: header not allowlisted: {hk!r}"
                        )
                    if not isinstance(hv, str):
                        raise FixtureValidationError(
                            f"{where}.headers[{hk!r}]: must be string"
                        )
                    _scan_secret_value(hv, where=f"{where}.headers[{hk}]")
                continue
            lowered = key_s.lower().replace("-", "_")
            if lowered in _SECRET_KEY_MARKERS or key_s.lower() in {
                "cookie",
                "set-cookie",
                "authorization",
                "p_skey",
                "skey",
                "access_token",
                "token",
                "raw_request",
                "content",
                "text",
            }:
                # g_tk as a bare key outside name lists is rejected.
                if lowered == "g_tk" or key_s == "g_tk":
                    raise FixtureValidationError(
                        f"{where}: secret-bearing key rejected: {key_s!r}"
                    )
                raise FixtureValidationError(
                    f"{where}: secret-bearing key rejected: {key_s!r}"
                )
            _walk_secret_scan(
                value, where=f"{where}.{key_s}", allow_body_text=allow_body_text
            )
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            _walk_secret_scan(
                item, where=f"{where}[{index}]", allow_body_text=allow_body_text
            )
    elif isinstance(obj, str) and not allow_body_text:
        _scan_secret_value(obj, where=where)


def _parse_name_list(
    value: Any,
    *,
    where: str,
    allowed: frozenset[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise FixtureValidationError(f"{where}: must be a list of strings")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise FixtureValidationError(f"{where}: entries must be non-empty strings")
        names.append(item)
    if not _is_sorted_unique_names(names):
        raise FixtureValidationError(
            f"{where}: names must be unique and sorted ascending "
            "(canonicalization must not hide drift)"
        )
    if allowed is not None:
        extra = sorted(set(names) - allowed)
        if extra:
            raise FixtureValidationError(
                f"{where}: unexpected field names: {', '.join(extra)}"
            )
    return tuple(names)


def _parse_fingerprint(raw: Any) -> RequestFingerprint:
    if not isinstance(raw, Mapping):
        raise FixtureValidationError("request_fingerprint must be an object")
    _reject_unknown_keys(raw, _FINGERPRINT_KEYS, where="request_fingerprint")
    required = (
        "method",
        "scheme",
        "host",
        "path",
        "query_field_names",
        "form_field_names",
        "header_names",
        "follow_redirects",
    )
    for key in required:
        if key not in raw:
            raise FixtureValidationError(
                f"request_fingerprint: missing required key {key!r}"
            )

    method = raw["method"]
    scheme = raw["scheme"]
    host = raw["host"]
    path = raw["path"]
    if method != "POST":
        raise FixtureValidationError("request_fingerprint.method must be POST")
    if scheme != "https":
        raise FixtureValidationError("request_fingerprint.scheme must be https")
    if not isinstance(host, str) or not host:
        raise FixtureValidationError("request_fingerprint.host must be a non-empty string")
    if not isinstance(path, str) or not path.startswith("/"):
        raise FixtureValidationError(
            "request_fingerprint.path must be an absolute path string"
        )
    follow = raw["follow_redirects"]
    if follow is not False:
        raise FixtureValidationError(
            "request_fingerprint.follow_redirects must be false"
        )

    query_names = _parse_name_list(
        raw["query_field_names"],
        where="request_fingerprint.query_field_names",
        allowed=_ALLOWED_QUERY_NAMES,
    )
    form_names = _parse_name_list(
        raw["form_field_names"],
        where="request_fingerprint.form_field_names",
        allowed=_ALLOWED_FORM_NAMES,
    )
    # Header names: exact casing as sent (Cookie/Origin/Referer); sorted.
    header_names = _parse_name_list(
        raw["header_names"],
        where="request_fingerprint.header_names",
        allowed=_ALLOWED_HEADER_NAMES,
    )
    return RequestFingerprint(
        method=method,
        scheme=scheme,
        host=host,
        path=path,
        query_field_names=query_names,
        form_field_names=form_names,
        header_names=header_names,
        follow_redirects=False,
    )


def _parse_response(raw: Any) -> FixtureResponse:
    if not isinstance(raw, Mapping):
        raise FixtureValidationError("response must be an object")
    _reject_unknown_keys(raw, _RESPONSE_KEYS, where="response")
    for key in ("status_code", "headers", "body"):
        if key not in raw:
            raise FixtureValidationError(f"response: missing required key {key!r}")

    status = raw["status_code"]
    if not isinstance(status, int) or isinstance(status, bool):
        raise FixtureValidationError("response.status_code must be an int")
    if status < 100 or status > 599:
        raise FixtureValidationError("response.status_code out of range")

    headers_raw = raw["headers"]
    if not isinstance(headers_raw, Mapping):
        raise FixtureValidationError("response.headers must be an object")
    headers: dict[str, str] = {}
    for key, value in headers_raw.items():
        if not isinstance(key, str):
            raise FixtureValidationError("response.headers keys must be strings")
        normalized_key = key.lower()
        if normalized_key not in _ALLOWED_RESPONSE_HEADERS:
            raise FixtureValidationError(
                f"response.headers: header not allowlisted: {key!r}"
            )
        if normalized_key in headers:
            raise FixtureValidationError(
                f"response.headers: duplicate case-insensitive header: {key!r}"
            )
        if not isinstance(value, str):
            raise FixtureValidationError(
                f"response.headers[{key!r}] must be a string"
            )
        _scan_secret_value(value, where=f"response.headers[{key}]")
        headers[normalized_key] = value

    body = raw["body"]
    if not isinstance(body, str):
        raise FixtureValidationError("response.body must be a UTF-8 string")
    body_bytes = body.encode("utf-8")
    if len(body_bytes) > MAX_FIXTURE_BODY_BYTES:
        raise FixtureValidationError(
            f"response.body exceeds {MAX_FIXTURE_BODY_BYTES} bytes"
        )
    _scan_secret_value(body, where="response.body")
    return FixtureResponse(status_code=status, headers=headers, body=body)


def _parse_expected(raw: Any) -> FixtureExpected:
    if not isinstance(raw, Mapping):
        raise FixtureValidationError("expected must be an object")
    _reject_unknown_keys(raw, _EXPECTED_KEYS, where="expected")
    if "status" not in raw:
        raise FixtureValidationError("expected: missing required key 'status'")
    status = raw["status"]
    if status not in ("published", "failed", "ambiguous"):
        raise FixtureValidationError(
            "expected.status must be published|failed|ambiguous"
        )
    remote_id = raw.get("remote_id")
    reason = raw.get("reason")
    if remote_id is not None and not isinstance(remote_id, str):
        raise FixtureValidationError("expected.remote_id must be a string when set")
    if reason is not None and not isinstance(reason, str):
        raise FixtureValidationError("expected.reason must be a string when set")
    if status == "published":
        if not remote_id:
            raise FixtureValidationError(
                "expected.remote_id required when status is published"
            )
        if reason is not None:
            raise FixtureValidationError(
                "expected.reason must be omitted when status is published"
            )
    else:
        if remote_id is not None:
            raise FixtureValidationError(
                "expected.remote_id must be omitted when status is not published"
            )
    if remote_id is not None:
        if not _SAFE_REMOTE_ID_RE.fullmatch(remote_id):
            raise FixtureValidationError(
                "expected.remote_id must be a finite safe identifier"
            )
        _scan_secret_value(remote_id, where="expected.remote_id")
    if reason is not None:
        _scan_secret_value(reason, where="expected.reason")
    return FixtureExpected(
        status=status,  # type: ignore[arg-type]
        remote_id=remote_id,
        reason=reason,
    )


def validate_fixture_dict(data: Any) -> SanitizedFixture:
    """Validate a fixture mapping; raise ``FixtureValidationError`` on failure."""

    if not isinstance(data, Mapping):
        raise FixtureValidationError("fixture root must be a JSON object")
    _reject_unknown_keys(data, _TOP_LEVEL_KEYS, where="fixture")
    _walk_secret_scan(data, where="fixture", allow_body_text=True)

    if data.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise FixtureValidationError(
            f"schema_version must be {FIXTURE_SCHEMA_VERSION}"
        )
    origin = data.get("origin")
    if origin not in ("synthetic", "real_sanitized"):
        raise FixtureValidationError(
            "origin must be 'synthetic' or 'real_sanitized'"
        )
    note = data.get("note")
    if note is not None and not isinstance(note, str):
        raise FixtureValidationError("note must be a string when present")
    if note is not None:
        _scan_secret_value(note, where="note")

    for required in ("request_fingerprint", "response", "expected"):
        if required not in data:
            raise FixtureValidationError(f"missing required key {required!r}")

    return SanitizedFixture(
        schema_version=FIXTURE_SCHEMA_VERSION,
        origin=origin,  # type: ignore[arg-type]
        request_fingerprint=_parse_fingerprint(data["request_fingerprint"]),
        response=_parse_response(data["response"]),
        expected=_parse_expected(data["expected"]),
        note=note,
    )


def load_fixture(path: str | Path) -> SanitizedFixture:
    """Load and validate one fixture JSON file from disk (offline only)."""

    file_path = Path(path)
    if not file_path.is_file():
        raise FixtureValidationError(f"fixture file not found: {file_path}")
    return load_fixture_bytes(file_path.read_bytes())


def load_fixture_bytes(data: bytes) -> SanitizedFixture:
    """Validate one already-read fixture payload without reopening a path."""

    if not isinstance(data, bytes):
        raise FixtureValidationError("fixture payload must be bytes")
    if len(data) > MAX_FIXTURE_FILE_BYTES:
        raise FixtureValidationError(
            f"fixture file exceeds {MAX_FIXTURE_FILE_BYTES} bytes"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FixtureValidationError("fixture file is not valid UTF-8") from exc
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_object_keys)
    except json.JSONDecodeError as exc:
        raise FixtureValidationError(f"fixture JSON is malformed: {exc}") from exc
    if not isinstance(payload, dict):
        raise FixtureValidationError("fixture root must be a JSON object")
    return validate_fixture_dict(payload)


def _reject_duplicate_object_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FixtureValidationError(
                f"fixture JSON contains duplicate object key: {key!r}"
            )
        result[key] = value
    return result


def fingerprint_from_wire_profile(profile: WireProfile) -> RequestFingerprint:
    """Derive a names-only request fingerprint from a candidate WireProfile."""

    parsed = urlsplit(profile.endpoint)
    form_names = sorted(
        {
            *{str(k) for k in profile.static_form_fields},
            profile.content_field,
            profile.uin_field,
        }
    )
    return RequestFingerprint(
        method="POST",
        scheme="https",
        host=parsed.hostname or "",
        path=parsed.path,
        query_field_names=("g_tk",),
        form_field_names=tuple(form_names),
        header_names=tuple(sorted(["Cookie", "Origin", "Referer"])),
        follow_redirects=False,
    )


def build_httpx_response(fixture: SanitizedFixture) -> httpx.Response:
    """Construct an offline ``httpx.Response`` from the sanitized response block."""

    headers = dict(fixture.response.headers)
    return httpx.Response(
        status_code=fixture.response.status_code,
        headers=headers,
        content=fixture.response.body.encode("utf-8"),
        request=httpx.Request("POST", "https://user.qzone.qq.com/offline-fixture"),
    )


def _fingerprints_match(
    left: RequestFingerprint, right: RequestFingerprint
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if left.method != right.method:
        reasons.append("request_fingerprint method mismatch")
    if left.scheme != right.scheme:
        reasons.append("request_fingerprint scheme mismatch")
    if left.host != right.host:
        reasons.append("request_fingerprint host mismatch")
    if left.path != right.path:
        reasons.append("request_fingerprint path mismatch")
    if left.query_field_names != right.query_field_names:
        reasons.append("request_fingerprint query_field_names mismatch")
    if left.form_field_names != right.form_field_names:
        reasons.append("request_fingerprint form_field_names mismatch")
    if left.header_names != right.header_names:
        reasons.append("request_fingerprint header_names mismatch")
    if left.follow_redirects != right.follow_redirects:
        reasons.append("request_fingerprint follow_redirects mismatch")
    return (not reasons, reasons)


def _parser_matches_expected(
    parsed: Mapping[str, Any], expected: FixtureExpected
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    status = str(parsed.get("status", "") or "")
    if status != expected.status:
        reasons.append(
            f"parser outcome status mismatch: got {status!r}, "
            f"expected {expected.status!r}"
        )
        return False, reasons
    if expected.status == "published":
        remote = str(parsed.get("remote_id", "") or "")
        if remote != (expected.remote_id or ""):
            reasons.append("parser outcome remote_id mismatch")
    else:
        reason = str(parsed.get("reason", "") or "")
        if expected.reason is not None and reason != expected.reason:
            reasons.append("parser outcome reason mismatch")
    return (not reasons, reasons)


def run_conformance(
    fixture: SanitizedFixture,
    *,
    profile: WireProfile,
) -> ConformanceReport:
    """Compare fixture fingerprint + parser outcome to a candidate profile.

    Never mutates ``profile``.  ``profile_creation_eligible`` is advisory only
    and is true only for ``real_sanitized`` fixtures that fully pass.
    """

    # Read validated flag without writing; ensure object identity unchanged.
    _ = profile.validated

    candidate_fp = fingerprint_from_wire_profile(profile)
    request_ok, request_reasons = _fingerprints_match(
        fixture.request_fingerprint, candidate_fp
    )

    response = build_httpx_response(fixture)
    parsed = parse_qzone_publish_response(response)
    response_ok, response_reasons = _parser_matches_expected(
        parsed, fixture.expected
    )

    reasons = tuple(sorted(request_reasons + response_reasons))
    ok = request_ok and response_ok
    eligible = ok and fixture.origin == "real_sanitized"

    return ConformanceReport(
        ok=ok,
        fixture_origin=fixture.origin,
        request_match=request_ok,
        response_match=response_ok,
        profile_creation_eligible=eligible,
        reasons=reasons,
        parser_status=str(parsed.get("status", "") or "") or None,
        expected_status=fixture.expected.status,
    )


__all__ = [
    "FIXTURE_SCHEMA_VERSION",
    "MAX_FIXTURE_BODY_BYTES",
    "MAX_FIXTURE_FILE_BYTES",
    "ConformanceReport",
    "FixtureExpected",
    "FixtureOrigin",
    "FixtureResponse",
    "FixtureValidationError",
    "RequestFingerprint",
    "SanitizedFixture",
    "build_httpx_response",
    "fingerprint_from_wire_profile",
    "load_fixture",
    "load_fixture_bytes",
    "run_conformance",
    "validate_fixture_dict",
]  # isort-style: ConformanceReport, FIXTURE_*, Fixture*, MAX_*, Request*, Sanitized*, callables
