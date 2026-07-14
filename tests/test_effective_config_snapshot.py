from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient


def _entries_by_path(snapshot: dict[str, object]) -> dict[str, dict[str, object]]:
    entries = snapshot["entries"]
    assert isinstance(entries, list)
    return {str(entry["path"]): entry for entry in entries if isinstance(entry, dict)}


def _write_snapshot_fixture(root: Path) -> Path:
    config_dir = root / "config"
    config_dir.mkdir()
    config_path = config_dir / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "llm": {
                    "api_key": "main-secret",
                    "base_url": "https://main.example/v1",
                    "model": "main-model",
                },
                "group": {
                    "access": {
                        "mode": "blacklist",
                        "blacklist": [100],
                    }
                },
                "slang_lookup": {"tianapi_key": "legacy-named-secret"},
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "group-policy.json").write_text(
        json.dumps(
            {
                "access": {
                    "mode": "whitelist",
                    "whitelist": [200],
                    "log_dropped": False,
                }
            }
        ),
        encoding="utf-8",
    )

    plugin_dir = root / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "demo",
                "display_name": {"zh": "演示", "en": "Demo"},
                "version": "1.0.0",
                "description": "Effective configuration snapshot test plugin.",
                "priority": 100,
                "tier": "user",
                "toggle_policy": "runtime",
                "category": "tool",
                "permissions": [],
                "capabilities": [],
                "author": "Omubot Tests",
                "min_omubot_version": "0.1.0",
                "config": {
                    "defaults": "config.default.json",
                    "schema": "config.schema.json",
                    "apply_mode": "restart_required",
                    "restart_required_fields": ["enabled", "api_token"],
                },
                "store": {"visibility": "local", "marketplace_id": ""},
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "config.default.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": "demo",
                "values": {
                    "enabled": True,
                    "api_token": "default-plugin-secret",
                    "limit": 3,
                },
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "config.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "api_token": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    override_dir = root / "storage" / "plugins" / "config"
    override_dir.mkdir(parents=True)
    (override_dir / "demo.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": "demo",
                "values": {
                    "enabled": False,
                    "api_token": "override-plugin-secret",
                },
            }
        ),
        encoding="utf-8",
    )
    return config_path


def test_effective_snapshot_explains_precedence_and_masks_secrets(tmp_path: Path) -> None:
    from services.effective_config import build_effective_config_snapshot

    config_path = _write_snapshot_fixture(tmp_path)
    snapshot = build_effective_config_snapshot(
        config_path=config_path,
        project_root=tmp_path,
        environment={
            "LLM_MODEL": "environment-model",
            "LLM_API_KEY": "environment-secret",
        },
    )
    payload = snapshot.to_dict()
    entries = _entries_by_path(payload)

    default_entry = entries["llm.max_tokens"]
    assert default_entry["source"]["kind"] == "default"  # type: ignore[index]
    assert default_entry["secret"] is False
    assert isinstance(default_entry["value"], int)

    environment_entry = entries["llm.model"]
    assert environment_entry["value"] == "environment-model"
    assert environment_entry["source"] == {
        "kind": "environment",
        "name": "LLM_MODEL",
        "location": "environment",
    }
    assert [source["kind"] for source in environment_entry["source_chain"]] == [  # type: ignore[index]
        "default",
        "main_config",
        "environment",
    ]

    main_entry = entries["llm.base_url"]
    assert main_entry["value"] == "https://main.example/v1"
    assert main_entry["source"]["kind"] == "main_config"  # type: ignore[index]

    secret_entry = entries["llm.api_key"]
    assert "value" not in secret_entry
    assert secret_entry["secret"] is True
    assert secret_entry["present"] is True
    assert secret_entry["mask"] == "********"

    legacy_named_secret = entries["slang_lookup.tianapi_key"]
    assert legacy_named_secret["secret"] is True
    assert "value" not in legacy_named_secret

    group_entry = entries["group.access.mode"]
    assert group_entry["value"] == "whitelist"
    assert group_entry["source"]["kind"] == "group_policy"  # type: ignore[index]

    plugin_entry = entries["plugins.demo.enabled"]
    assert plugin_entry["value"] is False
    assert plugin_entry["source"]["kind"] == "plugin_override"  # type: ignore[index]
    assert plugin_entry["restart_requirement"] == "required"
    assert entries["plugins.demo.limit"]["restart_requirement"] == "none"

    plugin_secret = entries["plugins.demo.api_token"]
    assert "value" not in plugin_secret
    assert plugin_secret["present"] is True
    serialized = json.dumps(payload)
    for raw_secret in (
        "main-secret",
        "environment-secret",
        "default-plugin-secret",
        "override-plugin-secret",
        "legacy-named-secret",
    ):
        assert raw_secret not in serialized


@pytest.mark.parametrize(
    ("path", "environment_name"),
    [
        ("llm.profiles.main.model", "LLM_MODEL"),
        ("llm.profiles.main.api_key", "LLM_API_KEY"),
    ],
)
def test_effective_snapshot_attributes_derived_main_profile_to_environment(
    tmp_path: Path,
    path: str,
    environment_name: str,
) -> None:
    from services.effective_config import build_effective_config_snapshot

    config_path = _write_snapshot_fixture(tmp_path)
    snapshot = build_effective_config_snapshot(
        config_path=config_path,
        project_root=tmp_path,
        environment={
            "LLM_MODEL": "environment-model",
            "LLM_API_KEY": "environment-secret",
        },
    )
    entry = _entries_by_path(snapshot.to_dict())[path]

    assert entry["source"] == {
        "kind": "environment",
        "name": environment_name,
        "location": "environment",
    }
    assert entry["source_chain"][-1] == entry["source"]  # type: ignore[index]


def test_effective_snapshot_admin_route_is_read_only(tmp_path: Path) -> None:
    from admin.routes.api.effective_config import create_effective_config_router

    config_path = _write_snapshot_fixture(tmp_path)
    app = FastAPI()
    app.include_router(
        create_effective_config_router(
            config_path=str(config_path),
            project_root=tmp_path,
            environment={"LLM_API_KEY": "route-environment-secret"},
        ),
        prefix="/api/admin",
    )
    client = TestClient(app)

    response = client.get("/api/admin/config/effective")
    assert response.status_code == 200
    assert response.json()["config_path"] == str(config_path)
    assert "route-environment-secret" not in response.text
    assert client.post("/api/admin/config/effective").status_code == 405


def test_effective_snapshot_is_mounted_on_admin_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from admin.routes.api import create_api_router

    config_path = _write_snapshot_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    app = FastAPI()
    app.include_router(create_api_router(config_path=str(config_path)))

    response = TestClient(app).get("/api/admin/config/effective")
    assert response.status_code == 200
    assert response.json()["config_path"] == str(config_path)
