"""Regression coverage for the production Runtime v2 source provisioner."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.agent_runtime.activation import ProductionActivationProfileV1
from services.agent_runtime.operator_auth import OperatorAuthorizationStoreV1
from services.agent_runtime.rollout_attestation import ProfileBoundRolloutAttestorV1


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
