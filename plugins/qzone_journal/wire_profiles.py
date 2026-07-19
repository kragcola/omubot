"""Fail-closed QZone wire-profile loading from sanitized real captures."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any

from plugins.qzone_journal.delivery import BUILTIN_WIRE_PROFILE
from plugins.qzone_journal.fixture_conformance import (
    FixtureValidationError,
    load_fixture_bytes,
    run_conformance,
)
from plugins.qzone_journal.transport import WireProfile

DEFAULT_PROFILE_DIR = Path("config/qzone_wire_profiles")
DEFAULT_ATTESTATION_SECRET_PATH = Path(
    "storage/qzone_wire_profiles/attestation.secret"
)
_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ATTESTATION_KEYS = frozenset({
    "schema_version",
    "profile_id",
    "fixture_sha256",
    "hmac_sha256",
})
_ATTESTATION_DOMAIN = b"omubot:qzone-wire-profile-attestation:v1"


class WireProfileLoadError(RuntimeError):
    """A configured profile cannot be proven safe for live publication."""


def attestation_path_for(
    profile_id: str,
    *,
    profile_dir: str | Path = DEFAULT_PROFILE_DIR,
) -> Path:
    return Path(profile_dir) / f"{profile_id}.attestation.json"


def _root_attestation_secret(
    secret: str | bytes | None,
    *,
    secret_path: str | Path = DEFAULT_ATTESTATION_SECRET_PATH,
) -> bytes:
    if secret is None:
        try:
            encoded = Path(secret_path).read_bytes()
        except OSError:
            raise WireProfileLoadError(
                "QZone capture attestation secret is unavailable"
            ) from None
    else:
        encoded = secret if isinstance(secret, bytes) else str(secret).encode("utf-8")
    if len(encoded) < 16:
        raise WireProfileLoadError("QZone capture attestation secret is unavailable")
    return encoded


def _attestation_mac(
    *,
    profile_id: str,
    fixture_bytes: bytes,
    secret: str | bytes | None,
    secret_path: str | Path = DEFAULT_ATTESTATION_SECRET_PATH,
) -> str:
    root = _root_attestation_secret(secret, secret_path=secret_path)
    key = hmac.new(root, _ATTESTATION_DOMAIN, hashlib.sha256).digest()
    material = _ATTESTATION_DOMAIN + b"\0" + profile_id.encode("utf-8") + b"\0" + fixture_bytes
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def build_capture_attestation(
    *,
    profile_id: str,
    fixture_bytes: bytes,
    secret: str | bytes | None = None,
    secret_path: str | Path = DEFAULT_ATTESTATION_SECRET_PATH,
) -> dict[str, Any]:
    """Create a detached, deployment-secret-bound capture attestation."""

    if _PROFILE_ID_RE.fullmatch(profile_id) is None:
        raise WireProfileLoadError("invalid QZone wire profile id")
    return {
        "schema_version": 1,
        "profile_id": profile_id,
        "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "hmac_sha256": _attestation_mac(
            profile_id=profile_id,
            fixture_bytes=fixture_bytes,
            secret=secret,
            secret_path=secret_path,
        ),
    }


def verify_capture_attestation(
    *,
    profile_id: str,
    fixture_bytes: bytes,
    attestation_path: str | Path,
    secret: str | bytes | None = None,
    secret_path: str | Path = DEFAULT_ATTESTATION_SECRET_PATH,
) -> None:
    """Require exact fixture bytes signed by the deployment-local secret."""

    try:
        raw = json.loads(Path(attestation_path).read_text(encoding="utf-8"))
    except Exception:
        raise WireProfileLoadError("QZone capture attestation is missing or invalid") from None
    if not isinstance(raw, dict) or set(raw) != _ATTESTATION_KEYS:
        raise WireProfileLoadError("QZone capture attestation is missing or invalid")
    if raw.get("schema_version") != 1 or raw.get("profile_id") != profile_id:
        raise WireProfileLoadError("QZone capture attestation is missing or invalid")
    fixture_sha = raw.get("fixture_sha256")
    mac = raw.get("hmac_sha256")
    if (
        not isinstance(fixture_sha, str)
        or _SHA256_RE.fullmatch(fixture_sha) is None
        or not isinstance(mac, str)
        or _SHA256_RE.fullmatch(mac) is None
    ):
        raise WireProfileLoadError("QZone capture attestation is missing or invalid")
    expected_sha = hashlib.sha256(fixture_bytes).hexdigest()
    expected_mac = _attestation_mac(
        profile_id=profile_id,
        fixture_bytes=fixture_bytes,
        secret=secret,
        secret_path=secret_path,
    )
    if not hmac.compare_digest(fixture_sha, expected_sha) or not hmac.compare_digest(
        mac,
        expected_mac,
    ):
        raise WireProfileLoadError("QZone capture attestation is missing or invalid")


def _candidate_profile(profile_id: str) -> WireProfile:
    return WireProfile(
        profile_id=profile_id,
        endpoint=BUILTIN_WIRE_PROFILE.endpoint,
        validated=False,
        content_field=BUILTIN_WIRE_PROFILE.content_field,
        uin_field=BUILTIN_WIRE_PROFILE.uin_field,
        static_form_fields=dict(BUILTIN_WIRE_PROFILE.static_form_fields),
        max_content_chars=BUILTIN_WIRE_PROFILE.max_content_chars,
    )


def load_wire_profile(
    profile_id: str,
    *,
    profile_dir: str | Path = DEFAULT_PROFILE_DIR,
    attestation_secret: str | bytes | None = None,
    attestation_secret_path: str | Path = DEFAULT_ATTESTATION_SECRET_PATH,
) -> WireProfile:
    """Load one profile, validating custom profiles from real sanitized fixtures.

    The built-in profile remains permanently unvalidated. A custom profile is
    created as a distinct object only when its names-only request fingerprint
    and sanitized response both conform and the fixture origin is
    ``real_sanitized``.
    """

    normalized = str(profile_id or "").strip()
    if normalized == BUILTIN_WIRE_PROFILE.profile_id:
        return BUILTIN_WIRE_PROFILE
    if _PROFILE_ID_RE.fullmatch(normalized) is None:
        raise WireProfileLoadError("invalid QZone wire profile id")

    root = Path(profile_dir)
    fixture_path = root / f"{normalized}.json"
    try:
        fixture_bytes = fixture_path.read_bytes()
    except OSError:
        raise WireProfileLoadError("QZone wire profile fixture is unavailable") from None
    verify_capture_attestation(
        profile_id=normalized,
        fixture_bytes=fixture_bytes,
        attestation_path=attestation_path_for(normalized, profile_dir=root),
        secret=attestation_secret,
        secret_path=attestation_secret_path,
    )
    try:
        fixture = load_fixture_bytes(fixture_bytes)
    except FixtureValidationError:
        raise WireProfileLoadError(
            "QZone wire profile fixture failed schema or secret-safety validation"
        ) from None

    candidate = _candidate_profile(normalized)
    report = run_conformance(fixture, profile=candidate)
    if not report.profile_creation_eligible:
        raise WireProfileLoadError(
            "QZone wire profile fixture is not eligible for validated runtime use"
        )

    return WireProfile(
        profile_id=normalized,
        endpoint=candidate.endpoint,
        validated=True,
        content_field=candidate.content_field,
        uin_field=candidate.uin_field,
        static_form_fields=dict(candidate.static_form_fields),
        max_content_chars=candidate.max_content_chars,
    )


__all__ = [
    "BUILTIN_WIRE_PROFILE",
    "DEFAULT_ATTESTATION_SECRET_PATH",
    "DEFAULT_PROFILE_DIR",
    "WireProfileLoadError",
    "attestation_path_for",
    "build_capture_attestation",
    "load_wire_profile",
    "verify_capture_attestation",
]
