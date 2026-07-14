"""Contract tests for manifest-declared plugin config paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import kernel.manifest
from services.plugin_config import PluginConfigStore


def _write_endpoint_plugin(plugin_root: Path, plugin_name: str) -> None:
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
                "required": ["endpoint"],
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
                "display_name": {"zh": "配置覆盖", "en": "Config Override"},
                "description": "A plugin with schema-validated stored overrides.",
                "version": "1.0.0",
                "priority": 100,
                "tier": "user",
                "toggle_policy": "restart_required",
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


def test_plugin_config_store_uses_manifest_declared_config_paths(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugins"
    plugin_dir = plugin_root / "custom_config"
    plugin_dir.mkdir(parents=True)

    defaults_path = plugin_dir / "defaults.custom.json"
    defaults_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": "custom_config",
                "values": {"greeting": "from-manifest"},
            }
        ),
        encoding="utf-8",
    )
    schema_path = plugin_dir / "schema.custom.json"
    schema_path.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"greeting": {"type": "string"}},
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "custom_config",
                "display_name": {"zh": "自定义配置", "en": "Custom Config"},
                "description": "A plugin with manifest-declared config paths.",
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
                    "defaults": "defaults.custom.json",
                    "schema": "schema.custom.json",
                    "apply_mode": "hot",
                    "restart_required_fields": [],
                },
                "store": {"visibility": "local", "marketplace_id": ""},
            }
        ),
        encoding="utf-8",
    )

    store = PluginConfigStore(
        path=tmp_path / "storage" / "plugins" / "config",
        plugin_root=plugin_root,
    )

    entry = store.get_entry("custom_config")

    assert Path(entry["default_path"]) == defaults_path
    assert Path(entry["schema_path"]) == schema_path
    assert entry["defaults"]["greeting"] == "from-manifest"
    assert entry["effective_values"]["greeting"] == "from-manifest"


def test_plugin_config_store_rejects_schema_invalid_stored_override(
    tmp_path: Path,
) -> None:
    plugin_name = "stored_override"
    plugin_root = tmp_path / "plugins"
    _write_endpoint_plugin(plugin_root, plugin_name)
    config_dir = tmp_path / "storage" / "plugins" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / f"{plugin_name}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": plugin_name,
                "values": {"endpoint": 123},
            }
        ),
        encoding="utf-8",
    )
    store = PluginConfigStore(path=config_dir, plugin_root=plugin_root)

    with pytest.raises(ValueError, match=r"schema validation failed.*endpoint"):
        store.get_entry(plugin_name)


def test_plugin_config_store_rejects_schema_invalid_values_before_write(
    tmp_path: Path,
) -> None:
    plugin_name = "invalid_write"
    plugin_root = tmp_path / "plugins"
    _write_endpoint_plugin(plugin_root, plugin_name)
    config_dir = tmp_path / "storage" / "plugins" / "config"
    store = PluginConfigStore(path=config_dir, plugin_root=plugin_root)

    with pytest.raises(ValueError, match=r"schema validation failed.*endpoint"):
        store.set_values(plugin_name, {"endpoint": 123})

    assert not store.plugin_path(plugin_name).exists()


def test_plugin_config_store_rejects_malformed_override_json(tmp_path: Path) -> None:
    plugin_name = "malformed_override"
    plugin_root = tmp_path / "plugins"
    _write_endpoint_plugin(plugin_root, plugin_name)
    config_dir = tmp_path / "storage" / "plugins" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / f"{plugin_name}.json").write_text("{", encoding="utf-8")
    store = PluginConfigStore(path=config_dir, plugin_root=plugin_root)

    with pytest.raises(ValueError, match=r"override JSON is invalid"):
        store.get_entry(plugin_name)


def test_plugin_config_store_exposes_manifest_restart_metadata(
    tmp_path: Path,
) -> None:
    plugin_name = "restart_metadata"
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
                "display_name": {
                    "zh": "重启配置元数据",
                    "en": "Restart Config Metadata",
                },
                "description": "A plugin with manifest-owned restart metadata.",
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

    store = PluginConfigStore(
        path=tmp_path / "storage" / "plugins" / "config",
        plugin_root=plugin_root,
    )

    entry = store.get_entry(plugin_name)

    assert entry["defaults"]["endpoint"] == "https://example.test"
    assert (
        entry.get("apply_mode"),
        entry.get("restart_required_fields"),
    ) == ("restart_required", ["endpoint"])


def test_load_plugin_manifest_rejects_config_path_outside_plugin_directory(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "plugins" / "sample"
    plugin_dir.mkdir(parents=True)

    (plugin_dir.parent / "outside.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": "sample",
                "values": {},
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "config.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    manifest_path = plugin_dir / "plugin.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "sample",
                "display_name": {"zh": "示例", "en": "Sample"},
                "description": "A sample plugin with an escaping config path.",
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
                    "defaults": "../outside.json",
                    "schema": "config.schema.json",
                    "apply_mode": "hot",
                    "restart_required_fields": [],
                },
                "store": {"visibility": "local", "marketplace_id": ""},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        kernel.manifest.load_plugin_manifest(manifest_path, expected_name="sample")


def test_load_plugin_manifest_rejects_restart_field_missing_from_schema(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "plugins" / "restart_fields"
    plugin_dir.mkdir(parents=True)

    (plugin_dir / "config.default.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": "restart_fields",
                "values": {"present": 1},
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "config.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"present": {"type": "integer"}},
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    manifest_path = plugin_dir / "plugin.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "restart_fields",
                "display_name": {"zh": "重启字段", "en": "Restart Fields"},
                "description": "A plugin with field-level restart requirements.",
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
                    "restart_required_fields": ["missing"],
                },
                "store": {"visibility": "local", "marketplace_id": ""},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        kernel.manifest.load_plugin_manifest(
            manifest_path,
            expected_name="restart_fields",
        )


def test_load_plugin_manifest_rejects_defaults_that_violate_config_schema(
    tmp_path: Path,
) -> None:
    plugin_name = "schema_values"
    plugin_dir = tmp_path / "plugins" / plugin_name
    plugin_dir.mkdir(parents=True)

    (plugin_dir / "config.default.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": plugin_name,
                "values": {"endpoint": 123},
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "config.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"endpoint": {"type": "string"}},
                "required": ["endpoint"],
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    manifest_path = plugin_dir / "plugin.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": plugin_name,
                "display_name": {"zh": "配置校验", "en": "Schema Values"},
                "description": "A plugin with schema-validated config defaults.",
                "version": "1.0.0",
                "priority": 100,
                "tier": "user",
                "toggle_policy": "restart_required",
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

    with pytest.raises(ValueError):
        kernel.manifest.load_plugin_manifest(
            manifest_path,
            expected_name=plugin_name,
        )
