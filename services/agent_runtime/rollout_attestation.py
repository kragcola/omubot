"""Digest-pinned, profile-bound rollout evidence for Agent Runtime v2."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from services.agent_runtime.activation import ProductionActivationProfileV1
from services.agent_runtime.rollout_readiness import (
    ACTIVATION_GATE_NAMES,
    ROLLBACK_GATE_NAMES,
)

_CONTRACT_VERSION = "agent_runtime_rollout_attestation.v1"
_SCHEMA_VERSION = 1
_GATE_STATUSES = frozenset({"ready", "not_ready", "not_assessed"})
_REASON_RE = re.compile(r"^[a-z0-9_]{1,120}$")
_EVIDENCE_REF_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{2,239}$")
_MANIFEST_FIELDS = frozenset(
    {
        "contract_version",
        "schema_version",
        "profile_fingerprint",
        "activation",
        "rollback",
    }
)
_MANIFEST_GATE_FIELDS = frozenset(
    {"status", "reason", "evidence_at", "evidence_ref"}
)


class ProfileBoundRolloutAttestorV1:
    """Read attestation only when its content and profile binding both match."""

    def __init__(self, profile: ProductionActivationProfileV1) -> None:
        if not isinstance(profile, ProductionActivationProfileV1):
            raise TypeError("profile must be a ProductionActivationProfileV1")
        if profile.attestation is None:
            raise ValueError("production attestation source is required")
        self._profile = profile

    async def activation_attestation(self) -> Mapping[str, dict[str, Any]]:
        manifest = await self._load_manifest()
        return _read_gate_set(manifest["activation"], ACTIVATION_GATE_NAMES)

    async def rollback_attestation(self) -> Mapping[str, dict[str, Any]]:
        manifest = await self._load_manifest()
        return _read_gate_set(manifest["rollback"], ROLLBACK_GATE_NAMES)

    async def _load_manifest(self) -> Mapping[str, Any]:
        source = self._profile.attestation
        if source is None:
            raise ValueError("production attestation source is required")
        try:
            payload = await asyncio.to_thread(source.manifest_path.read_bytes)
        except OSError:
            raise ValueError("rollout attestation manifest is unavailable") from None
        if _sha256_bytes(payload) != source.manifest_sha256:
            raise ValueError("rollout attestation manifest integrity failed")
        try:
            manifest = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("rollout attestation manifest is invalid") from None
        if not isinstance(manifest, Mapping) or set(manifest) != _MANIFEST_FIELDS:
            raise ValueError("rollout attestation manifest is invalid")
        schema_version = manifest.get("schema_version")
        if (
            manifest.get("contract_version") != _CONTRACT_VERSION
            or type(schema_version) is not int
            or schema_version != _SCHEMA_VERSION
            or manifest.get("profile_fingerprint")
            != self._profile.attestation_profile_fingerprint()
        ):
            raise ValueError("rollout attestation manifest does not match profile")
        return manifest


def _read_gate_set(
    raw: object,
    names: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping) or set(raw) != set(names):
        raise ValueError("rollout attestation gates are invalid")
    return {name: _read_gate(raw[name]) for name in names}


def _read_gate(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != _MANIFEST_GATE_FIELDS:
        raise ValueError("rollout attestation gate is invalid")
    status = raw.get("status")
    reason = raw.get("reason")
    if (
        not isinstance(status, str)
        or status not in _GATE_STATUSES
        or not isinstance(reason, str)
        or _REASON_RE.fullmatch(reason) is None
    ):
        raise ValueError("rollout attestation gate is invalid")
    evidence_at = raw.get("evidence_at")
    evidence_ref = raw.get("evidence_ref")
    if status == "not_assessed":
        if evidence_at is not None or evidence_ref is not None:
            raise ValueError("unassessed rollout gate cannot claim evidence")
        return {
            "status": status,
            "reason": reason,
            "evidence_at": None,
        }
    if not isinstance(evidence_ref, str) or _EVIDENCE_REF_RE.fullmatch(evidence_ref) is None:
        raise ValueError("rollout attestation evidence is invalid")
    normalized_time = _evidence_time(evidence_at)
    return {
        "status": status,
        "reason": reason,
        "evidence_at": normalized_time,
    }


def _evidence_time(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("rollout attestation evidence is invalid")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("rollout attestation evidence is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("rollout attestation evidence is invalid")
    normalized = parsed.astimezone(UTC)
    if normalized > datetime.now(UTC):
        raise ValueError("rollout attestation evidence is invalid")
    return normalized.isoformat().replace("+00:00", "Z")


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


__all__ = ["ProfileBoundRolloutAttestorV1"]
