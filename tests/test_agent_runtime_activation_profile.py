"""Production activation profile contracts for Agent Runtime v2."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kernel.config import BotConfig


def _api() -> Any:
    module_name = "services.agent_runtime.activation"
    if importlib.util.find_spec(module_name) is None:
        pytest.fail("ProductionActivationProfileV1 is required for production activation")
    module = importlib.import_module(module_name)
    profile_type = getattr(module, "ProductionActivationProfileV1", None)
    assert isinstance(profile_type, type), "ProductionActivationProfileV1 is required"
    return profile_type


def _source(
    *,
    db_path: str,
    schema_version: int,
    backup_path: str,
    backup_sha256: str,
    restore_evidence_ref: str = "restore:runbook:20260814",
    rollback_evidence_ref: str = "rollback:runbook:20260814",
) -> SimpleNamespace:
    return SimpleNamespace(
        db_path=db_path,
        expected_schema_version=schema_version,
        backup_path=backup_path,
        backup_sha256=backup_sha256,
        restore_evidence_ref=restore_evidence_ref,
        rollback_evidence_ref=rollback_evidence_ref,
    )


def _settings(tmp_path: Path) -> SimpleNamespace:
    sources: dict[str, SimpleNamespace] = {}
    schema_versions = {
        "runtime": 2,
        "memory": 1,
        "worldbook": 1,
        "operator": 1,
        "invocation": 2,
    }
    for name, version in schema_versions.items():
        database = tmp_path / "storage" / f"agent-runtime-{name}.db"
        backup = tmp_path / "storage" / "backups" / f"agent-runtime-{name}.bak"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(f"backup:{name}".encode())
        digest = hashlib.sha256(backup.read_bytes()).hexdigest()
        sources[name] = _source(
            db_path=str(database.relative_to(tmp_path)),
            schema_version=version,
            backup_path=str(backup.relative_to(tmp_path)),
            backup_sha256=f"sha256:{digest}",
        )
    return SimpleNamespace(
        enabled=True,
        worker_id="agent-runtime-worker-1",
        max_workers=1,
        principal_scopes=("runtime:tool:invoke",),
        allowed_target_refs=("runtime:tool:catalog",),
        runtime=sources["runtime"],
        memory=sources["memory"],
        worldbook=sources["worldbook"],
        operator=sources["operator"],
        invocation=sources["invocation"],
        attestation=SimpleNamespace(
            manifest_path="storage/agent-runtime/attestation.json",
            manifest_sha256="sha256:" + "a" * 64,
        ),
    )


def _create_database(path: Path, *, user_version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE proof (id INTEGER PRIMARY KEY)")
        db.execute(f"PRAGMA user_version={user_version}")


def test_disabled_profile_is_none_and_does_not_resolve_default_paths(tmp_path: Path) -> None:
    profile_type = _api()
    disabled = SimpleNamespace(enabled=False)

    profile = profile_type.from_settings(disabled, repo_root=tmp_path)

    assert profile is None
    assert not (tmp_path / "storage").exists()


def test_bot_config_keeps_production_activation_disabled_by_default() -> None:
    config = BotConfig()

    assert config.agent_runtime.enabled is False
    assert config.agent_runtime.worker_id == ""
    assert config.agent_runtime.max_workers == 1
    assert config.agent_runtime.principal_scopes == ()
    assert config.agent_runtime.allowed_target_refs == ()
    assert config.agent_runtime.runtime.db_path == ""
    assert config.agent_runtime.invocation.backup_sha256 == ""
    assert config.agent_runtime.attestation.manifest_path == ""
    assert config.agent_runtime.attestation.manifest_sha256 == ""


def test_enabled_profile_requires_all_explicit_source_evidence(tmp_path: Path) -> None:
    profile_type = _api()
    settings = _settings(tmp_path)
    settings.operator.backup_sha256 = ""

    with pytest.raises(ValueError, match=r"operator.*backup|backup.*operator"):
        profile_type.from_settings(settings, repo_root=tmp_path)


def test_enabled_profile_rejects_aliasing_between_independent_sources(tmp_path: Path) -> None:
    profile_type = _api()
    settings = _settings(tmp_path)
    settings.invocation.db_path = settings.runtime.db_path

    with pytest.raises(ValueError, match=r"distinct|duplicate|alias"):
        profile_type.from_settings(settings, repo_root=tmp_path)


def test_enabled_profile_carries_only_explicit_principal_policy(tmp_path: Path) -> None:
    profile_type = _api()
    settings = _settings(tmp_path)
    settings.principal_scopes = ("network:search",)
    settings.allowed_target_refs = ("network:web-search",)

    profile = profile_type.from_settings(settings, repo_root=tmp_path)

    assert profile is not None
    assert profile.principal_scopes == ("network:search",)
    assert profile.allowed_target_refs == ("network:web-search",)

    settings.allowed_target_refs = ("network:*",)
    with pytest.raises(ValueError, match=r"target|invalid|wildcard"):
        profile_type.from_settings(settings, repo_root=tmp_path)


def test_enabled_profile_requires_nonempty_exact_principal_policy(tmp_path: Path) -> None:
    profile_type = _api()
    settings = _settings(tmp_path)
    settings.principal_scopes = ()

    with pytest.raises(ValueError, match=r"principal.*scope|scope.*required"):
        profile_type.from_settings(settings, repo_root=tmp_path)

    settings = _settings(tmp_path)
    settings.allowed_target_refs = ()
    with pytest.raises(ValueError, match=r"target.*required|allowed_target"):
        profile_type.from_settings(settings, repo_root=tmp_path)

    settings = _settings(tmp_path)
    settings.principal_scopes = ("runtime:*",)
    with pytest.raises(ValueError, match=r"scope.*invalid|principal.*scope"):
        profile_type.from_settings(settings, repo_root=tmp_path)


def test_enabled_profile_requires_complete_digest_pinned_attestation_input(
    tmp_path: Path,
) -> None:
    profile_type = _api()
    settings = _settings(tmp_path)
    settings.attestation = SimpleNamespace(manifest_path="", manifest_sha256="")

    with pytest.raises(ValueError, match=r"digest-pinned|attestation.*manifest"):
        profile_type.from_settings(settings, repo_root=tmp_path)

    settings = _settings(tmp_path)
    settings.attestation = SimpleNamespace(
        manifest_path="storage/agent-runtime/attestation.json",
        manifest_sha256="",
    )

    with pytest.raises(ValueError, match=r"attestation.*manifest|manifest.*attestation"):
        profile_type.from_settings(settings, repo_root=tmp_path)

    settings.attestation.manifest_sha256 = "sha256:" + "a" * 64
    profile = profile_type.from_settings(settings, repo_root=tmp_path)

    assert profile is not None
    assert profile.attestation is not None
    assert profile.attestation.manifest_path == (
        tmp_path / "storage/agent-runtime/attestation.json"
    )
    assert profile.attestation.manifest_sha256 == "sha256:" + "a" * 64


def test_attestation_profile_fingerprint_binds_policy_and_source_evidence(
    tmp_path: Path,
) -> None:
    profile_type = _api()
    settings = _settings(tmp_path)
    profile = profile_type.from_settings(settings, repo_root=tmp_path)

    assert profile is not None
    fingerprint = profile.attestation_profile_fingerprint()
    assert fingerprint.startswith("sha256:")
    assert len(fingerprint) == len("sha256:") + 64

    settings.worldbook.rollback_evidence_ref = "rollback:runbook:20260815"
    changed = profile_type.from_settings(settings, repo_root=tmp_path)
    assert changed is not None
    assert changed.attestation_profile_fingerprint() != fingerprint


def test_preflight_reads_only_explicit_sources_and_redacts_paths(tmp_path: Path) -> None:
    profile_type = _api()
    settings = _settings(tmp_path)
    for name, expected_version in (
        ("runtime", 2),
        ("memory", 1),
        ("worldbook", 1),
        ("operator", 1),
        ("invocation", 2),
    ):
        _create_database(
            tmp_path / getattr(settings, name).db_path,
            user_version=expected_version,
        )
    profile = profile_type.from_settings(settings, repo_root=tmp_path)

    report = asyncio.run(profile.preflight())

    assert report["status"] == "ready"
    assert tuple(report["sources"]) == (
        "runtime",
        "memory",
        "worldbook",
        "operator",
        "invocation",
    )
    assert all(item["status"] == "ready" for item in report["sources"].values())
    serialized = repr(report)
    assert str(tmp_path) not in serialized
    assert "agent-runtime-runtime.db" not in serialized


def test_preflight_fails_closed_when_a_backup_no_longer_matches(tmp_path: Path) -> None:
    profile_type = _api()
    settings = _settings(tmp_path)
    for name, expected_version in (
        ("runtime", 2),
        ("memory", 1),
        ("worldbook", 1),
        ("operator", 1),
        ("invocation", 2),
    ):
        _create_database(
            tmp_path / getattr(settings, name).db_path,
            user_version=expected_version,
        )
    (tmp_path / settings.worldbook.backup_path).write_bytes(b"tampered")
    profile = profile_type.from_settings(settings, repo_root=tmp_path)

    report = asyncio.run(profile.preflight())

    assert report["status"] == "not_ready"
    assert report["sources"]["worldbook"] == {
        "status": "not_ready",
        "reason": "backup_integrity_failed",
    }
