"""Contracts for the read-only Agent Runtime v2 activation preflight CLI."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.agent_runtime.activation import ProductionActivationProfileV1
from services.agent_runtime.rollout_readiness import (
    ACTIVATION_GATE_NAMES,
    ROLLBACK_GATE_NAMES,
)


def _api() -> Any:
    path = Path(__file__).resolve().parents[1] / "tools/agent_runtime_preflight.py"
    spec = importlib.util.spec_from_file_location("agent_runtime_preflight", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_database(path: Path, *, schema_version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE proof (id INTEGER PRIMARY KEY)")
        connection.execute(f"PRAGMA user_version={schema_version}")


def _settings_from_sources(tmp_path: Path) -> SimpleNamespace:
    schema_versions = {
        "runtime": 2,
        "memory": 1,
        "worldbook": 1,
        "operator": 1,
        "invocation": 2,
    }
    sources: dict[str, SimpleNamespace] = {}
    for name, schema_version in schema_versions.items():
        database = tmp_path / "storage" / "agent-runtime" / f"{name}.db"
        backup = tmp_path / "storage" / "agent-runtime-backups" / f"{name}.sqlite"
        _create_database(database, schema_version=schema_version)
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(f"frozen-backup:{name}".encode())
        digest = hashlib.sha256(backup.read_bytes()).hexdigest()
        sources[name] = SimpleNamespace(
            db_path=str(database.relative_to(tmp_path)),
            expected_schema_version=schema_version,
            backup_path=str(backup.relative_to(tmp_path)),
            backup_sha256=f"sha256:{digest}",
            restore_evidence_ref=f"restore:top_secret_{name}:20260815",
            rollback_evidence_ref=f"rollback:top_secret_{name}:20260815",
        )
    return SimpleNamespace(
        enabled=True,
        worker_id="agent-runtime-preflight-worker",
        max_workers=1,
        principal_scopes=("runtime:read",),
        allowed_target_refs=("onebot:group:123:message:789",),
        runtime=sources["runtime"],
        memory=sources["memory"],
        worldbook=sources["worldbook"],
        operator=sources["operator"],
        invocation=sources["invocation"],
        attestation=SimpleNamespace(
            manifest_path="storage/agent-runtime/rollout-attestation.json",
            manifest_sha256="sha256:" + "0" * 64,
        ),
    )


def _ready_gates(names: tuple[str, ...]) -> dict[str, dict[str, object]]:
    evidence_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace(
        "+00:00", "Z"
    )
    return {
        name: {
            "status": "ready",
            "reason": "verified",
            "evidence_at": evidence_at,
            "evidence_ref": f"evidence:top_secret_{name}:20260815",
        }
        for name in names
    }


def _write_config(settings: SimpleNamespace, path: Path) -> None:
    lines = [
        "[agent_runtime]",
        "enabled = true",
        f'worker_id = "{settings.worker_id}"',
        "max_workers = 1",
        'principal_scopes = ["runtime:read"]',
        'allowed_target_refs = ["onebot:group:123:message:789"]',
    ]
    for name in ("runtime", "memory", "worldbook", "operator", "invocation"):
        source = getattr(settings, name)
        lines.extend(
            (
                "",
                f"[agent_runtime.{name}]",
                f'db_path = "{source.db_path}"',
                f"expected_schema_version = {source.expected_schema_version}",
                f'backup_path = "{source.backup_path}"',
                f'backup_sha256 = "{source.backup_sha256}"',
                f'restore_evidence_ref = "{source.restore_evidence_ref}"',
                f'rollback_evidence_ref = "{source.rollback_evidence_ref}"',
            )
        )
    lines.extend(
        (
            "",
            "[agent_runtime.attestation]",
            f'manifest_path = "{settings.attestation.manifest_path}"',
            f'manifest_sha256 = "{settings.attestation.manifest_sha256}"',
            "",
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


async def _ready_config(tmp_path: Path) -> Path:
    settings = _settings_from_sources(tmp_path)
    profile = ProductionActivationProfileV1.from_settings(settings, repo_root=tmp_path)
    assert profile is not None
    manifest = {
        "contract_version": "agent_runtime_rollout_attestation.v1",
        "schema_version": 1,
        "profile_fingerprint": profile.attestation_profile_fingerprint(),
        "activation": _ready_gates(ACTIVATION_GATE_NAMES),
        "rollback": _ready_gates(ROLLBACK_GATE_NAMES),
    }
    payload = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest_path = tmp_path / settings.attestation.manifest_path
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(payload)
    settings.attestation.manifest_sha256 = "sha256:" + hashlib.sha256(payload).hexdigest()
    config_path = tmp_path / "config" / "agent-runtime.toml"
    _write_config(settings, config_path)
    return config_path


@pytest.mark.asyncio
async def test_preflight_reports_disabled_without_resolving_storage(tmp_path: Path) -> None:
    module = _api()
    config_path = tmp_path / "config" / "agent-runtime.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[agent_runtime]\nenabled = false\n", encoding="utf-8")

    report = await module.run_preflight(config_path=config_path, repo_root=tmp_path)

    assert report == {"status": "not_ready", "reason": "agent_runtime_disabled"}
    assert not (tmp_path / "storage").exists()


@pytest.mark.asyncio
async def test_preflight_validates_sources_and_pinned_manifest_without_leaking_inputs(
    tmp_path: Path,
) -> None:
    module = _api()
    config_path = await _ready_config(tmp_path)

    report = await module.run_preflight(config_path=config_path, repo_root=tmp_path)

    assert report["status"] == "ready"
    assert report["sources"]["runtime"] == {"status": "ready", "reason": "verified"}
    assert report["activation"]["status"] == "ready"
    assert report["rollback"]["status"] == "ready"
    serialized = json.dumps(report, sort_keys=True)
    assert "top_secret" not in serialized
    assert "database_path" not in serialized
    assert "backup_path" not in serialized
    assert "evidence_ref" not in serialized
