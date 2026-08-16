"""Regression coverage for the production Runtime v2 source provisioner."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import importlib.util
import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.agent_runtime.activation import ProductionActivationProfileV1
from services.agent_runtime.operator_auth import OperatorAuthorizationStoreV1
from services.agent_runtime.rollout_attestation import ProfileBoundRolloutAttestorV1
from services.tools.web_search import WebSearchTool


def _api() -> Any:
    path = Path(__file__).resolve().parents[1] / "tools/agent_runtime_provision_sources.py"
    spec = importlib.util.spec_from_file_location("agent_runtime_provision_sources", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _installer_api() -> Any:
    path = Path(__file__).resolve().parents[1] / "tools/agent_runtime_install_profile.py"
    spec = importlib.util.spec_from_file_location("agent_runtime_install_profile", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_legacy_worldbook(root: Path) -> None:
    worldbook = root / "storage" / "worldbook"
    for name in ("proposals", "decisions", "commits"):
        (worldbook / name).mkdir(parents=True, exist_ok=True)
    (worldbook / "proposals" / "dream.one.json").write_text(
        json.dumps({"proposal_id": "dream.one"}), encoding="utf-8"
    )
    (worldbook / "decisions" / "decision.dream.one.json").write_text(
        json.dumps({"proposal_id": "dream.one", "decision_id": "decision.dream.one"}),
        encoding="utf-8",
    )
    (worldbook / "commits" / "commit.dream.one.json").write_text(
        json.dumps(
            {
                "proposal_id": "dream.one",
                "decision_id": "decision.dream.one",
                "event_id": "dream.test.one",
            }
        ),
        encoding="utf-8",
    )
    (worldbook / "life_state.json").write_text(
        json.dumps(
            {
                "revision": 1,
                "applied_event_ids": ["storylet.test.one"],
                "items": {},
            }
        ),
        encoding="utf-8",
    )


def test_worldbook_witness_rejects_mismatched_decision_chain(tmp_path: Path) -> None:
    module = _api()
    _write_legacy_worldbook(tmp_path)
    commit = tmp_path / "storage" / "worldbook" / "commits" / "commit.dream.one.json"
    commit.write_text(
        json.dumps(
            {
                "proposal_id": "dream.one",
                "decision_id": "decision.other",
                "event_id": "dream.test.one",
            }
        ),
        encoding="utf-8",
    )

    witness = module._worldbook_witness(tmp_path)

    assert witness["status"] == "not_ready"
    assert witness["reason"] == "proposal_decision_mismatch"


def _settings(fragment: dict[str, Any]) -> SimpleNamespace:
    def convert(value: Any) -> Any:
        if isinstance(value, dict):
            return SimpleNamespace(**{key: convert(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(convert(item) for item in value)
        return value

    return convert(fragment)


async def _install_legacy_web_search_profile(
    tmp_path: Path,
    *,
    provision_run_id: str,
    install_run_id: str,
    targets: tuple[str, ...] = ("network:web-search",),
) -> tuple[Any, Path, Path]:
    """Create a pre-fix fixture without bypassing source preflight contracts."""

    provisioner = _api()
    installer = _installer_api()
    _write_legacy_worldbook(tmp_path)
    result = await provisioner.provision_sources(
        repo_root=tmp_path,
        run_id=provision_run_id,
        worker_id="agent-runtime-prod-legacy",
        operator_id="prod-operator",
        credential="operator-credential-0123456789-abcdefghij",
    )
    fragment_path = tmp_path / result["activation_config_path"]
    fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
    runtime = fragment["agent_runtime"]
    runtime["principal_scopes"] = ["memory:read", "time:read"]
    runtime["allowed_target_refs"] = list(targets)
    legacy_profile = ProductionActivationProfileV1.from_settings(
        _settings(runtime),
        repo_root=tmp_path,
    )
    assert legacy_profile is not None

    manifest_path = tmp_path / runtime["attestation"]["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["profile_fingerprint"] = legacy_profile.attestation_profile_fingerprint()
    manifest_payload = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    manifest_path.write_bytes(manifest_payload)
    source_inventory_path = manifest_path.parent / "source-inventory.json"
    source_inventory = json.loads(source_inventory_path.read_text(encoding="utf-8"))
    source_inventory["manifest_sha256"] = "sha256:" + hashlib.sha256(manifest_payload).hexdigest()
    source_inventory_path.write_text(
        json.dumps(source_inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    runtime["attestation"]["manifest_sha256"] = (
        "sha256:" + hashlib.sha256(manifest_payload).hexdigest()
    )
    fragment_path.write_text(
        json.dumps(fragment, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    config_path = tmp_path / "config" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"log": {"level": "INFO"}}), encoding="utf-8")
    await installer.install_ingress_only_profile(
        repo_root=tmp_path,
        config_path=config_path,
        fragment_path=fragment_path,
        run_id=install_run_id,
    )
    return installer, config_path, manifest_path


@pytest.mark.asyncio
async def test_provision_creates_isolated_sources_backups_and_initial_manifest(
    tmp_path: Path,
) -> None:
    module = _api()
    _write_legacy_worldbook(tmp_path)

    result = await module.provision_sources(
        repo_root=tmp_path,
        run_id="20260816t000000z",
        worker_id="agent-runtime-prod-001",
        operator_id="prod-operator",
        credential="operator-credential-0123456789-abcdefghij",
    )

    final_root = tmp_path / "storage" / "agent-runtime-v2"
    assert final_root.is_dir()
    assert result["worldbook_witness"]["status"] == "ready"
    assert result["worldbook_witness"]["legacy_namespaces"] == {
        "commit": "dream",
        "life_state": "storylet",
    }

    fragment = json.loads((final_root / "activation-config.json").read_text())
    profile = ProductionActivationProfileV1.from_settings(
        _settings(fragment["agent_runtime"]),
        repo_root=tmp_path,
    )
    assert profile is not None
    assert (await profile.preflight())["status"] == "ready"
    assert profile.allowed_target_refs == ("network:web-search",)
    assert set(WebSearchTool().spec.required_scopes).issubset(profile.principal_scopes)

    for name, source in fragment["agent_runtime"].items():
        if name in {"attestation", "enabled", "worker_id", "max_workers", "principal_scopes", "allowed_target_refs"}:
            continue
        database = tmp_path / source["db_path"]
        backup = tmp_path / source["backup_path"]
        restored = final_root / "rehearsals" / "restore" / f"{name}.db"
        assert database.is_file()
        assert backup.is_file()
        assert restored.is_file()
        assert source["backup_sha256"] == "sha256:" + hashlib.sha256(backup.read_bytes()).hexdigest()
        with sqlite3.connect(f"file:{restored}?mode=ro", uri=True) as connection:
            assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    attestor = ProfileBoundRolloutAttestorV1(profile)
    assert all(
        gate["status"] == "not_assessed"
        for gate in (await attestor.activation_attestation()).values()
    )

    secret_path = tmp_path / result["operator_secret_path"]
    credential = dict(
        line.split("=", 1)
        for line in secret_path.read_text(encoding="utf-8").splitlines()
        if line
    )["AGENT_RUNTIME_OPERATOR_CREDENTIAL"]
    operators = OperatorAuthorizationStoreV1(
        tmp_path / fragment["agent_runtime"]["operator"]["db_path"]
    )
    await operators.init()
    try:
        authenticated = await operators.authenticate(
            operator_id="prod-operator",
            credential=credential,
        )
    finally:
        await operators.close()
    assert authenticated is not None
    assert "runtime:tool:approve" in authenticated.granted_scopes


@pytest.mark.asyncio
async def test_provision_refuses_to_overwrite_existing_sources(tmp_path: Path) -> None:
    module = _api()
    _write_legacy_worldbook(tmp_path)
    kwargs = {
        "repo_root": tmp_path,
        "run_id": "20260816t000000z",
        "worker_id": "agent-runtime-prod-001",
        "operator_id": "prod-operator",
        "credential": "operator-credential-0123456789-abcdefghij",
    }
    await module.provision_sources(**kwargs)

    with pytest.raises(FileExistsError):
        await module.provision_sources(**kwargs)


@pytest.mark.asyncio
async def test_provision_rejects_web_search_without_required_scope(tmp_path: Path) -> None:
    module = _api()

    with pytest.raises(ValueError, match="network:web-search requires network:search"):
        await module.provision_sources(
            repo_root=tmp_path,
            run_id="20260816t000009z",
            worker_id="agent-runtime-prod-009",
            operator_id="prod-operator",
            credential="operator-credential-0123456789-abcdefghij",
            principal_scopes=("memory:read", "time:read"),
            allowed_target_refs=("network:web-search",),
        )

    assert not (tmp_path / "storage" / "agent-runtime-v2").exists()


def test_provision_cli_resolves_project_services_from_tools_directory(tmp_path: Path) -> None:
    """The production one-shot container executes the script by path."""

    _write_legacy_worldbook(tmp_path)
    script = Path(__file__).resolve().parents[1] / "tools" / "agent_runtime_provision_sources.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--repo-root",
            str(tmp_path),
            "--run-id",
            "20260816t000002z",
            "--worker-id",
            "agent-runtime-prod-003",
            "--operator-id",
            "prod-operator",
            "--credential",
            "operator-credential-0123456789-abcdefghij",
        ],
        cwd=script.parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "provisioned_not_assessed"


@pytest.mark.asyncio
async def test_ingress_only_profile_install_is_backed_up_and_cannot_start_worker(
    tmp_path: Path,
) -> None:
    provisioner = _api()
    installer = _installer_api()
    _write_legacy_worldbook(tmp_path)
    result = await provisioner.provision_sources(
        repo_root=tmp_path,
        run_id="20260816t000001z",
        worker_id="agent-runtime-prod-002",
        operator_id="prod-operator",
        credential="operator-credential-0123456789-abcdefghij",
    )
    config_path = tmp_path / "config" / "config.json"
    original = {"log": {"level": "INFO"}}
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(original), encoding="utf-8")

    installed = await installer.install_ingress_only_profile(
        repo_root=tmp_path,
        config_path=config_path,
        fragment_path=tmp_path / result["activation_config_path"],
        run_id="20260816t000001z",
    )

    assert installed["status"] == "installed_ingress_only"
    assert json.loads(Path(installed["backup_path"]).read_text(encoding="utf-8")) == original
    active = json.loads(config_path.read_text(encoding="utf-8"))
    profile = ProductionActivationProfileV1.from_settings(
        _settings(active["agent_runtime"]),
        repo_root=tmp_path,
    )
    assert profile is not None
    attestor = ProfileBoundRolloutAttestorV1(profile)
    assert all(
        gate["status"] == "not_assessed"
        for gate in (await attestor.activation_attestation()).values()
    )
    assert all(
        gate["status"] == "not_assessed"
        for gate in (await attestor.rollback_attestation()).values()
    )

    with pytest.raises(FileExistsError):
        await installer.install_ingress_only_profile(
            repo_root=tmp_path,
            config_path=config_path,
            fragment_path=tmp_path / result["activation_config_path"],
            run_id="20260816t000001z",
        )


@pytest.mark.asyncio
async def test_ingress_only_profile_install_rejects_unsafe_backup_directory(
    tmp_path: Path,
) -> None:
    provisioner = _api()
    installer = _installer_api()
    _write_legacy_worldbook(tmp_path)
    result = await provisioner.provision_sources(
        repo_root=tmp_path,
        run_id="20260816t000003z",
        worker_id="agent-runtime-prod-003",
        operator_id="prod-operator",
        credential="operator-credential-0123456789-abcdefghij",
    )
    config_path = tmp_path / "config" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"log": {"level": "INFO"}}), encoding="utf-8")
    before_config = config_path.read_bytes()
    outside = tmp_path / "outside"
    outside.mkdir()
    (config_path.parent / "backups").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="child directory is unsafe"):
        await installer.install_ingress_only_profile(
            repo_root=tmp_path,
            config_path=config_path,
            fragment_path=tmp_path / result["activation_config_path"],
            run_id="20260816t000003z",
        )

    assert config_path.read_bytes() == before_config
    assert list(outside.iterdir()) == []


def test_descriptor_writers_sync_parent_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installer = _installer_api()
    output = tmp_path / "output"
    output.mkdir()
    directory_fd = os.open(output, installer._directory_flags())
    original_fsync = os.fsync
    synced_types: list[str] = []

    def record_fsync(descriptor: int) -> None:
        synced_types.append(
            "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
        )
        original_fsync(descriptor)

    monkeypatch.setattr(installer.os, "fsync", record_fsync)
    try:
        installer._write_exclusive_at(
            directory_fd,
            "manifest.json",
            b"{}\n",
            mode=0o600,
        )
        assert synced_types == ["file", "directory"]

        synced_types.clear()
        installer._write_atomic_at(
            directory_fd,
            "config.json",
            b"{}\n",
            mode=0o600,
        )
        assert synced_types == ["file", "directory", "directory"]
    finally:
        os.close(directory_fd)


def test_new_child_directory_syncs_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = _installer_api()
    parent = tmp_path / "parent"
    parent.mkdir()
    parent_fd = os.open(parent, installer._directory_flags())
    original_fsync = os.fsync
    synced_types: list[str] = []

    def record_fsync(descriptor: int) -> None:
        synced_types.append(
            "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
        )
        original_fsync(descriptor)

    monkeypatch.setattr(installer.os, "fsync", record_fsync)
    try:
        with installer._safe_child_directory(parent_fd, "attestation-generations") as child_fd:
            assert stat.S_ISDIR(os.fstat(child_fd).st_mode)
        assert synced_types == ["directory"]
    finally:
        os.close(parent_fd)


@pytest.mark.asyncio
async def test_repair_unassessed_web_search_scope_rebinds_only_dark_profile(
    tmp_path: Path,
) -> None:
    installer, config_path, manifest_path = await _install_legacy_web_search_profile(
        tmp_path,
        provision_run_id="20260816t000004z",
        install_run_id="20260816t000004z",
    )

    before_config = config_path.read_bytes()
    before_manifest = manifest_path.read_bytes()
    planned = await installer.repair_unassessed_web_search_scope(
        repo_root=tmp_path,
        config_path=config_path,
        run_id="20260816t000006z",
        apply=False,
    )
    assert planned["status"] == "web_search_scope_repair_ready"
    assert config_path.read_bytes() == before_config
    assert manifest_path.read_bytes() == before_manifest
    assert not (manifest_path.parent / "attestation-generations").exists()

    repaired = await installer.repair_unassessed_web_search_scope(
        repo_root=tmp_path,
        config_path=config_path,
        run_id="20260816t000006z",
    )

    assert repaired["status"] == "repinned_ingress_only_web_search_scope"
    config_backup = Path(repaired["config_backup_path"])
    new_manifest_path = Path(repaired["manifest_path"])
    assert config_backup.read_bytes() == before_config
    assert new_manifest_path.is_file()
    assert manifest_path.read_bytes() == before_manifest
    assert repaired["previous_manifest_path"] == str(manifest_path)
    assert planned["config_sha256"] == repaired["config_sha256"]
    assert planned["manifest_sha256"] == repaired["manifest_sha256"]
    source_inventory = json.loads(
        (manifest_path.parent / "source-inventory.json").read_text(encoding="utf-8")
    )
    assert source_inventory["manifest_sha256"] == (
        "sha256:" + hashlib.sha256(before_manifest).hexdigest()
    )
    active = json.loads(config_path.read_text(encoding="utf-8"))
    assert active["agent_runtime"]["attestation"]["manifest_path"] == str(
        new_manifest_path.relative_to(tmp_path)
    )
    profile = ProductionActivationProfileV1.from_settings(
        _settings(active["agent_runtime"]),
        repo_root=tmp_path,
    )
    assert profile is not None
    assert set(WebSearchTool().spec.required_scopes).issubset(profile.principal_scopes)
    assert profile.allowed_target_refs == ("network:web-search",)
    attestor = ProfileBoundRolloutAttestorV1(profile)
    assert all(
        gate["status"] == "not_assessed"
        for gate in (await attestor.activation_attestation()).values()
    )
    assert all(
        gate["status"] == "not_assessed"
        for gate in (await attestor.rollback_attestation()).values()
    )
    assert await installer.repair_unassessed_web_search_scope(
        repo_root=tmp_path,
        config_path=config_path,
        run_id="20260816t000007z",
    ) == {"status": "web_search_scope_already_present"}


@pytest.mark.asyncio
async def test_repair_web_search_scope_rejects_an_assessed_profile(tmp_path: Path) -> None:
    installer, config_path, manifest_path = await _install_legacy_web_search_profile(
        tmp_path,
        provision_run_id="20260816t000008z",
        install_run_id="20260816t000008z",
    )

    active = json.loads(config_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["activation"]["trusted_invocation_persistence"] = {
        "status": "not_ready",
        "reason": "evidence_pending",
        "evidence_at": "2026-08-15T00:00:00Z",
        "evidence_ref": "evidence:trusted-ingress:pending",
    }
    manifest_payload = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    manifest_path.write_bytes(manifest_payload)
    active["agent_runtime"]["attestation"]["manifest_sha256"] = (
        "sha256:" + hashlib.sha256(manifest_payload).hexdigest()
    )
    config_path.write_text(json.dumps(active), encoding="utf-8")
    before_config = config_path.read_bytes()
    before_manifest = manifest_path.read_bytes()

    with pytest.raises(ValueError, match="must not be worker-ready"):
        await installer.repair_unassessed_web_search_scope(
            repo_root=tmp_path,
            config_path=config_path,
            run_id="20260816t000009z",
        )

    assert config_path.read_bytes() == before_config
    assert manifest_path.read_bytes() == before_manifest


@pytest.mark.asyncio
async def test_repair_web_search_scope_rejects_extra_targets(tmp_path: Path) -> None:
    installer, config_path, manifest_path = await _install_legacy_web_search_profile(
        tmp_path,
        provision_run_id="20260816t000010z",
        install_run_id="20260816t000010z",
        targets=("memory:other", "network:web-search"),
    )
    before_config = config_path.read_bytes()
    before_manifest = manifest_path.read_bytes()

    with pytest.raises(ValueError, match="only canary target"):
        await installer.repair_unassessed_web_search_scope(
            repo_root=tmp_path,
            config_path=config_path,
            run_id="20260816t000011z",
        )

    assert config_path.read_bytes() == before_config
    assert manifest_path.read_bytes() == before_manifest


@pytest.mark.asyncio
async def test_repair_web_search_scope_rejects_unsafe_output_directories(tmp_path: Path) -> None:
    installer, config_path, manifest_path = await _install_legacy_web_search_profile(
        tmp_path,
        provision_run_id="20260816t000012z",
        install_run_id="20260816t000012z",
    )
    before_config = config_path.read_bytes()
    before_manifest = manifest_path.read_bytes()
    outside = tmp_path / "storage" / "other"
    outside.mkdir(parents=True)
    config_backup_dir = config_path.parent / "backups"
    for child in config_backup_dir.iterdir():
        child.unlink()
    config_backup_dir.rmdir()
    config_backup_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="child directory is unsafe"):
        await installer.repair_unassessed_web_search_scope(
            repo_root=tmp_path,
            config_path=config_path,
            run_id="20260816t000013z",
        )

    assert config_path.read_bytes() == before_config
    assert manifest_path.read_bytes() == before_manifest
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_repair_web_search_scope_rejects_unsafe_manifest_generation_directory(
    tmp_path: Path,
) -> None:
    installer, config_path, manifest_path = await _install_legacy_web_search_profile(
        tmp_path,
        provision_run_id="20260816t000014z",
        install_run_id="20260816t000014z",
    )
    before_config = config_path.read_bytes()
    before_manifest = manifest_path.read_bytes()
    outside = tmp_path / "storage" / "other"
    outside.mkdir(parents=True)
    generation_dir = manifest_path.parent / "attestation-generations"
    generation_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="child directory is unsafe"):
        await installer.repair_unassessed_web_search_scope(
            repo_root=tmp_path,
            config_path=config_path,
            run_id="20260816t000015z",
        )

    assert config_path.read_bytes() == before_config
    assert manifest_path.read_bytes() == before_manifest
    assert list(outside.iterdir()) == []
    assert not any(
        "web-search-scope" in path.name for path in (config_path.parent / "backups").iterdir()
    )


@pytest.mark.asyncio
async def test_repair_directory_swap_cannot_redirect_a_verified_output_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer, config_path, manifest_path = await _install_legacy_web_search_profile(
        tmp_path,
        provision_run_id="20260816t000016z",
        install_run_id="20260816t000016z",
    )
    before_config = config_path.read_bytes()
    before_manifest = manifest_path.read_bytes()
    backup_dir = config_path.parent / "backups"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_write = installer._write_exclusive_at
    swapped = False

    def swap_directory_then_write(
        directory_fd: int,
        name: str,
        payload: bytes,
        *,
        mode: int,
    ) -> None:
        nonlocal swapped
        if not swapped and name.startswith("config.before-agent-runtime-web-search-scope-"):
            for child in backup_dir.iterdir():
                child.unlink()
            backup_dir.rmdir()
            backup_dir.symlink_to(outside, target_is_directory=True)
            swapped = True
        original_write(directory_fd, name, payload, mode=mode)

    monkeypatch.setattr(installer, "_write_exclusive_at", swap_directory_then_write)

    with pytest.raises((RuntimeError, ValueError)):
        await installer.repair_unassessed_web_search_scope(
            repo_root=tmp_path,
            config_path=config_path,
            run_id="20260816t000017z",
        )

    assert config_path.read_bytes() == before_config
    assert manifest_path.read_bytes() == before_manifest
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_repair_web_search_scope_rejects_an_held_repair_lock(tmp_path: Path) -> None:
    installer, config_path, manifest_path = await _install_legacy_web_search_profile(
        tmp_path,
        provision_run_id="20260816t000018z",
        install_run_id="20260816t000018z",
    )
    before_config = config_path.read_bytes()
    before_manifest = manifest_path.read_bytes()
    lock_path = config_path.parent / ".agent-runtime-profile-repair.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="another profile repair is already running"):
            await installer.repair_unassessed_web_search_scope(
                repo_root=tmp_path,
                config_path=config_path,
                run_id="20260816t000019z",
            )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert config_path.read_bytes() == before_config
    assert manifest_path.read_bytes() == before_manifest


@pytest.mark.asyncio
async def test_repair_web_search_scope_cancellation_leaves_active_profile_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer, config_path, manifest_path = await _install_legacy_web_search_profile(
        tmp_path,
        provision_run_id="20260816t000020z",
        install_run_id="20260816t000020z",
    )
    before_config = config_path.read_bytes()
    before_manifest = manifest_path.read_bytes()
    original_validate = installer._validate_ingress_only_fragment
    validation_started = asyncio.Event()
    hold_validation = asyncio.Event()

    async def delayed_validate(*, repo_root: Path, fragment: dict[str, Any]) -> Any:
        profile = await original_validate(repo_root=repo_root, fragment=fragment)
        validation_started.set()
        await hold_validation.wait()
        return profile

    monkeypatch.setattr(installer, "_validate_ingress_only_fragment", delayed_validate)
    task = asyncio.create_task(
        installer.repair_unassessed_web_search_scope(
            repo_root=tmp_path,
            config_path=config_path,
            run_id="20260816t000021z",
        )
    )
    await asyncio.wait_for(validation_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert config_path.read_bytes() == before_config
    assert manifest_path.read_bytes() == before_manifest
    assert not (manifest_path.parent / "attestation-generations").exists()
    assert not (
        config_path.parent
        / "backups"
        / "config.before-agent-runtime-web-search-scope-20260816t000021z.json"
    ).exists()
    assert not (config_path.parent / ".agent-runtime-profile-repair.lock").exists()


def test_profile_installer_cli_rejects_ambiguous_apply_arguments(tmp_path: Path) -> None:
    installer = _installer_api()
    config_path = tmp_path / "config" / "config.json"

    with pytest.raises(SystemExit) as missing_fragment:
        installer.main(
            [
                "--repo-root",
                str(tmp_path),
                "--config",
                str(config_path),
                "--run-id",
                "20260816t000018z",
                "--apply",
            ]
        )
    assert missing_fragment.value.code == 2

    with pytest.raises(SystemExit) as dry_run_missing_fragment:
        installer.main(
            [
                "--repo-root",
                str(tmp_path),
                "--config",
                str(config_path),
                "--run-id",
                "20260816t000019z",
            ]
        )
    assert dry_run_missing_fragment.value.code == 2

    with pytest.raises(SystemExit) as repair_fragment:
        installer.main(
            [
                "--repo-root",
                str(tmp_path),
                "--config",
                str(config_path),
                "--fragment",
                str(tmp_path / "storage" / "fragment.json"),
                "--run-id",
                "20260816t000020z",
                "--repair-web-search-scope",
            ]
        )
    assert repair_fragment.value.code == 2
