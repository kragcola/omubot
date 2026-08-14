"""Contracts for the digest-pinned Agent Runtime v2 attestation manifest."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.agent_runtime.activation import ProductionActivationProfileV1

_ACTIVATION_GATES = (
    "production_principal_construction",
    "trusted_invocation_persistence",
    "trusted_invocation_reconstruction",
    "operator_identity",
    "operator_authentication",
    "production_runtime_source_path",
    "production_memory_source_path",
    "llm_client_wiring",
    "tool_registry_wiring",
    "bootstrap_wiring",
    "single_worker_gate",
    "exclusive_recovery_gate",
    "worldbook_governance_source_path",
    "worldbook_single_world_gate",
    "worldbook_authoritative_reread",
    "worldbook_reducer_verifier",
    "worldbook_no_dual_truth",
)
_ROLLBACK_GATES = (
    "dark_code_removal",
    "production_database_rollback",
    "production_migration_rollback",
    "production_runtime_wiring_rollback",
)
_SOURCE_SCHEMAS = {
    "runtime": 2,
    "memory": 1,
    "worldbook": 1,
    "operator": 1,
    "invocation": 2,
}


def _provider_type() -> type[Any]:
    module_name = "services.agent_runtime.rollout_attestation"
    spec = importlib.util.find_spec(module_name)
    assert spec is not None, "digest-pinned rollout attestation reader is required"
    module = importlib.import_module(module_name)
    provider_type = getattr(module, "ProfileBoundRolloutAttestorV1", None)
    assert isinstance(provider_type, type), "ProfileBoundRolloutAttestorV1 is required"
    return provider_type


def _source(name: str, schema_version: int) -> SimpleNamespace:
    digest_seed = {
        "runtime": "a",
        "memory": "b",
        "worldbook": "c",
        "operator": "d",
        "invocation": "e",
    }[name]
    return SimpleNamespace(
        db_path=f"storage/agent-runtime/{name}.db",
        expected_schema_version=schema_version,
        backup_path=f"storage/backups/agent-runtime-{name}.bak",
        backup_sha256="sha256:" + digest_seed * 64,
        restore_evidence_ref=f"restore:agent-runtime:{name}:20260814",
        rollback_evidence_ref=f"rollback:agent-runtime:{name}:20260814",
    )


def _settings() -> SimpleNamespace:
    sources = {
        name: _source(name, schema_version)
        for name, schema_version in _SOURCE_SCHEMAS.items()
    }
    return SimpleNamespace(
        enabled=True,
        worker_id="agent-runtime-manifest-worker",
        max_workers=1,
        principal_scopes=("runtime:read",),
        allowed_target_refs=("onebot:group:123:message:789",),
        runtime=sources["runtime"],
        memory=sources["memory"],
        worldbook=sources["worldbook"],
        operator=sources["operator"],
        invocation=sources["invocation"],
    )


def _gates(names: tuple[str, ...], *, blocked: str | None = None) -> dict[str, Any]:
    evidence_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace(
        "+00:00", "Z"
    )
    return {
        name: {
            "status": "not_ready" if name == blocked else "ready",
            "reason": "missing_requirement" if name == blocked else "verified",
            "evidence_at": evidence_at,
            "evidence_ref": f"evidence:agent-runtime:{name}:20260814",
        }
        for name in names
    }


def _pinned_profile(
    tmp_path: Path,
    *,
    activation: dict[str, Any] | None = None,
    rollback: dict[str, Any] | None = None,
    profile_fingerprint: str | None = None,
    schema_version: object = 1,
) -> ProductionActivationProfileV1:
    settings = _settings()
    path = tmp_path / "storage/agent-runtime/rollout-attestation.json"
    settings.attestation = SimpleNamespace(
        manifest_path=str(path.relative_to(tmp_path)),
        manifest_sha256="sha256:" + "0" * 64,
    )
    profile_input = ProductionActivationProfileV1.from_settings(
        settings,
        repo_root=tmp_path,
    )
    assert profile_input is not None
    manifest = {
        "contract_version": "agent_runtime_rollout_attestation.v1",
        "schema_version": schema_version,
        "profile_fingerprint": profile_fingerprint
        or profile_input.attestation_profile_fingerprint(),
        "activation": activation or _gates(_ACTIVATION_GATES),
        "rollback": rollback or _gates(_ROLLBACK_GATES),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(payload)
    settings.attestation = SimpleNamespace(
        manifest_path=str(path.relative_to(tmp_path)),
        manifest_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
    )
    profile = ProductionActivationProfileV1.from_settings(settings, repo_root=tmp_path)
    assert profile is not None and profile.attestation is not None
    return profile


@pytest.mark.asyncio
async def test_pinned_profile_bound_manifest_returns_only_readiness_gate_fields(
    tmp_path: Path,
) -> None:
    provider_type = _provider_type()
    profile = _pinned_profile(tmp_path)
    provider = provider_type(profile)

    activation = await provider.activation_attestation()
    rollback = await provider.rollback_attestation()

    assert tuple(activation) == _ACTIVATION_GATES
    assert tuple(rollback) == _ROLLBACK_GATES
    for gate in (*activation.values(), *rollback.values()):
        assert set(gate) == {"status", "reason", "evidence_at"}
        assert gate["status"] == "ready"
        assert gate["reason"] == "verified"
        assert gate["evidence_at"] is not None
    serialized = json.dumps((activation, rollback), sort_keys=True)
    assert "evidence_ref" not in serialized
    assert "rollout-attestation.json" not in serialized


@pytest.mark.asyncio
async def test_pinned_partial_gate_is_preserved_without_its_evidence_reference(
    tmp_path: Path,
) -> None:
    provider_type = _provider_type()
    blocked = "worldbook_authoritative_reread"
    profile = _pinned_profile(
        tmp_path,
        activation=_gates(_ACTIVATION_GATES, blocked=blocked),
    )

    activation = await provider_type(profile).activation_attestation()

    gate = activation[blocked]
    assert set(gate) == {"status", "reason", "evidence_at"}
    assert gate["status"] == "not_ready"
    assert gate["reason"] == "missing_requirement"
    assert gate["evidence_at"] is not None
    assert "evidence_ref" not in json.dumps(activation, sort_keys=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("digest", "profile", "gate_schema"))
async def test_manifest_tamper_or_schema_mismatch_fails_closed(
    tmp_path: Path,
    failure: str,
) -> None:
    provider_type = _provider_type()
    kwargs: dict[str, Any] = {}
    if failure == "profile":
        kwargs["profile_fingerprint"] = "sha256:" + "0" * 64
    elif failure == "gate_schema":
        malformed = _gates(_ACTIVATION_GATES)
        malformed[_ACTIVATION_GATES[0]].pop("evidence_ref")
        kwargs["activation"] = malformed
    profile = _pinned_profile(tmp_path, **kwargs)
    if failure == "digest":
        assert profile.attestation is not None
        profile.attestation.manifest_path.write_text("{}", encoding="utf-8")

    provider = provider_type(profile)
    with pytest.raises(ValueError):
        await provider.activation_attestation()


@pytest.mark.asyncio
async def test_manifest_rejects_boolean_schema_version(tmp_path: Path) -> None:
    provider_type = _provider_type()
    profile = _pinned_profile(tmp_path, schema_version=True)

    with pytest.raises(ValueError):
        await provider_type(profile).activation_attestation()
