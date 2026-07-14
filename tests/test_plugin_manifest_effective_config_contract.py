from __future__ import annotations

import json
from pathlib import Path


def test_effective_config_uses_manifest_restart_required_fields_per_field(
    tmp_path: Path,
) -> None:
    from services.effective_config import build_effective_config_snapshot

    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    plugin_dir = tmp_path / "plugins" / "field_apply"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "field_apply",
                "display_name": {"zh": "字段应用", "en": "Field Apply"},
                "version": "1.0.0",
                "description": "Exercises field-level restart requirements.",
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
                    "restart_required_fields": ["cold"],
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
                "plugin": "field_apply",
                "values": {"cold": 1, "hot": 2},
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "config.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "cold": {"type": "integer"},
                    "hot": {"type": "integer"},
                },
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_effective_config_snapshot(
        config_path=config_path,
        project_root=tmp_path,
        environment={},
    )
    entries = {entry.path: entry for entry in snapshot.entries}

    cold = entries["plugins.field_apply.cold"]
    hot = entries["plugins.field_apply.hot"]
    assert cold.restart_requirement == "required"
    assert hot.restart_requirement == "none"
    assert cold.apply_mode == "restart_required"
    assert hot.apply_mode == "restart_required"


def test_effective_config_skips_schema_invalid_stored_plugin_override(
    tmp_path: Path,
) -> None:
    from services.effective_config import build_effective_config_snapshot

    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    plugin_name = "invalid_override"
    plugin_dir = tmp_path / "plugins" / plugin_name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": plugin_name,
                "display_name": {"zh": "坏覆盖", "en": "Invalid Override"},
                "version": "1.0.0",
                "description": "Rejects a persisted value outside its schema.",
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
    override_dir = tmp_path / "storage" / "plugins" / "config"
    override_dir.mkdir(parents=True)
    (override_dir / f"{plugin_name}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": plugin_name,
                "values": {"endpoint": 123},
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_effective_config_snapshot(
        config_path=config_path,
        project_root=tmp_path,
        environment={},
    )

    assert not any(
        entry.path.startswith(f"plugins.{plugin_name}.")
        for entry in snapshot.entries
    )
    assert any(
        "schema validation failed" in warning and plugin_name in warning
        for warning in snapshot.warnings
    )
