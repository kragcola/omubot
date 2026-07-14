from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api.plugins import create_plugins_router
from kernel.bus import PluginBus
from kernel.types import AmadeusPlugin
from services.plugin_config import PluginConfigStore


class _ConfigTruthPlugin(AmadeusPlugin):
    name = "config_truth"
    version = "1.0.0"
    priority = 100

    def __init__(self) -> None:
        super().__init__()
        self.hot_apply_calls = 0

    def apply_runtime_settings(
        self,
        values: dict[str, object],
        *,
        changed_fields: frozenset[str],
    ) -> frozenset[str]:
        self.hot_apply_calls += 1
        return changed_fields


def _config_truth_client(tmp_path: Path) -> tuple[TestClient, _ConfigTruthPlugin]:
    plugin_name = "config_truth"
    plugin_root = tmp_path / "plugins"
    plugin_dir = plugin_root / plugin_name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "config.default.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": plugin_name,
                "values": {"endpoint": "https://example.test"},
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "config.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"endpoint": {"type": "string"}},
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": plugin_name,
                "display_name": {"zh": "配置真值", "en": "Config Truth"},
                "description": "Manifest-owned plugin configuration metadata.",
                "version": "1.0.0",
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
                    "restart_required_fields": ["endpoint"],
                },
                "store": {"visibility": "local", "marketplace_id": ""},
            }
        ),
        encoding="utf-8",
    )

    bus = PluginBus()
    plugin = _ConfigTruthPlugin()
    bus.register(plugin)
    plugin.config_spec = {"apply_mode": "hot", "restart_required_fields": []}
    config_store = PluginConfigStore(
        tmp_path / "storage" / "plugins" / "config",
        plugin_root=plugin_root,
    )
    app = FastAPI()
    app.include_router(
        create_plugins_router(bus=bus, plugin_config_store=config_store),
        prefix="/api/admin",
    )
    return TestClient(app), plugin


def test_plugin_settings_uses_manifest_owned_restart_metadata(tmp_path: Path) -> None:
    client, _plugin = _config_truth_client(tmp_path)

    response = client.get("/api/admin/plugins/config_truth/settings")

    assert response.status_code == 200
    settings = response.json()
    assert (
        settings["apply_mode"],
        settings["restart_required_fields"],
    ) == ("restart_required", ["endpoint"])


def test_plugin_settings_update_uses_manifest_owned_restart_metadata(
    tmp_path: Path,
) -> None:
    client, plugin = _config_truth_client(tmp_path)

    response = client.post(
        "/api/admin/plugins/config_truth/settings",
        json={"values": {"endpoint": "https://changed.example.test"}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert (
        payload.get("requires_restart"),
        payload.get("restart_required_fields"),
        plugin.hot_apply_calls,
    ) == (True, ["endpoint"], 0)


class _NestedConfigTruthPlugin(_ConfigTruthPlugin):
    name = "nested_config_truth"


def _nested_config_truth_client(
    tmp_path: Path,
) -> tuple[TestClient, _NestedConfigTruthPlugin]:
    plugin_name = "nested_config_truth"
    restart_field = "dialogue_climate.m2_enabled"
    plugin_root = tmp_path / "plugins"
    plugin_dir = plugin_root / plugin_name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "config.default.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": plugin_name,
                "values": {"dialogue_climate": {"m2_enabled": False}},
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "config.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "dialogue_climate": {
                        "type": "object",
                        "properties": {"m2_enabled": {"type": "boolean"}},
                        "additionalProperties": False,
                    }
                },
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": plugin_name,
                "display_name": {
                    "zh": "嵌套配置真值",
                    "en": "Nested Config Truth",
                },
                "description": "Manifest-owned nested configuration metadata.",
                "version": "1.0.0",
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
                    "restart_required_fields": [restart_field],
                },
                "store": {"visibility": "local", "marketplace_id": ""},
            }
        ),
        encoding="utf-8",
    )

    bus = PluginBus()
    plugin = _NestedConfigTruthPlugin()
    bus.register(plugin)
    plugin.config_spec = {"apply_mode": "hot", "restart_required_fields": []}
    config_store = PluginConfigStore(
        tmp_path / "storage" / "plugins" / "config",
        plugin_root=plugin_root,
    )
    app = FastAPI()
    app.include_router(
        create_plugins_router(bus=bus, plugin_config_store=config_store),
        prefix="/api/admin",
    )
    return TestClient(app), plugin


def test_plugin_settings_update_classifies_nested_restart_field(
    tmp_path: Path,
) -> None:
    client, plugin = _nested_config_truth_client(tmp_path)

    response = client.post(
        "/api/admin/plugins/nested_config_truth/settings",
        json={"values": {"dialogue_climate": {"m2_enabled": True}}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert (
        payload.get("requires_restart"),
        payload.get("restart_required_fields"),
        plugin.hot_apply_calls,
    ) == (True, ["dialogue_climate.m2_enabled"], 0)


def test_plugin_settings_rejects_schema_invalid_override_without_persisting(
    tmp_path: Path,
) -> None:
    client, plugin = _config_truth_client(tmp_path)

    response = client.post(
        "/api/admin/plugins/config_truth/settings",
        json={"values": {"endpoint": 123}},
    )
    payload = response.json()
    settings = client.get("/api/admin/plugins/config_truth/settings").json()
    error = str(payload.get("error", ""))

    assert (
        response.status_code,
        payload.get("ok"),
        "endpoint" in error,
        settings.get("values"),
        settings.get("effective_values", {}).get("endpoint"),
        plugin.hot_apply_calls,
    ) == (200, False, True, {}, "https://example.test", 0)


def test_plugin_settings_fails_closed_when_manifest_schema_is_missing(
    tmp_path: Path,
) -> None:
    client, plugin = _config_truth_client(tmp_path)
    (tmp_path / "plugins" / "config_truth" / "config.schema.json").unlink()
    override_path = (
        tmp_path / "storage" / "plugins" / "config" / "config_truth.json"
    )

    response = client.post(
        "/api/admin/plugins/config_truth/settings",
        json={"values": {"endpoint": 123}},
    )
    payload = response.json()
    error = str(payload.get("error", "")).lower()

    assert (
        response.status_code,
        payload.get("ok"),
        "schema" in error or "manifest" in error,
        plugin.hot_apply_calls,
        override_path.exists(),
    ) == (200, False, True, 0, False)
