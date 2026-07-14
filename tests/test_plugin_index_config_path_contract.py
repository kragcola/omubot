"""Contracts for manifest-declared config paths in the plugin index."""

from __future__ import annotations

import json
from pathlib import Path

from services.plugin_index import PluginIndexService


def test_plugin_index_preserves_manifest_declared_config_paths(
    tmp_path: Path,
) -> None:
    plugin_name = "custom_config_paths"
    plugin_root = tmp_path / "plugins"
    plugin_dir = plugin_root / plugin_name
    settings_dir = plugin_dir / "settings"
    settings_dir.mkdir(parents=True)

    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(
        """from kernel.types import AmadeusPlugin


class CustomConfigPathsPlugin(AmadeusPlugin):
    name = "custom_config_paths"
    version = "1.0.0"
    priority = 100
""",
        encoding="utf-8",
    )

    defaults_path = settings_dir / "defaults.json"
    defaults_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": plugin_name,
                "values": {},
            }
        ),
        encoding="utf-8",
    )
    schema_path = settings_dir / "schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {},
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
                    "zh": "自定义配置路径",
                    "en": "Custom Config Paths",
                },
                "description": "A plugin whose config files live below settings/.",
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
                    "defaults": "settings/defaults.json",
                    "schema": "settings/schema.json",
                    "apply_mode": "hot",
                    "restart_required_fields": [],
                },
                "store": {
                    "visibility": "local",
                    "marketplace_id": "",
                },
            }
        ),
        encoding="utf-8",
    )

    service = PluginIndexService(
        plugin_root=plugin_root,
        repo_root=tmp_path,
        omubot_version="1.5.0",
    )

    entry = service.entry_for(plugin_name)

    assert entry is not None
    assert entry["manifest_status"] == "ok"

    def resolve_index_path(value: object) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        path = Path(value)
        if path.is_absolute():
            return path.resolve()
        return (tmp_path / path).resolve()

    assert (
        resolve_index_path(entry.get("config_default_path")),
        resolve_index_path(entry.get("config_schema_path")),
    ) == (defaults_path.resolve(), schema_path.resolve())
