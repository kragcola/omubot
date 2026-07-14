"""Contract tests for strict manifest validation in the plugin index."""

from __future__ import annotations

import json
from pathlib import Path

from services.plugin_index import PluginIndexService


def test_plugin_index_rejects_unknown_manifest_v3_top_level_field(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugins"
    plugin_dir = plugin_root / "bad_index"
    plugin_dir.mkdir(parents=True)

    (plugin_dir / "plugin.py").write_text(
        """from kernel.types import AmadeusPlugin


class BadIndexPlugin(AmadeusPlugin):
    name = "bad_index"
    version = "1.0.0"
    priority = 100
""",
        encoding="utf-8",
    )
    (plugin_dir / "config.default.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": "bad_index",
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
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "bad_index",
                "display_name": {"zh": "坏索引", "en": "Bad Index"},
                "description": "A plugin whose manifest has an unknown field.",
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
                    "apply_mode": "hot",
                    "restart_required_fields": [],
                },
                "store": {"visibility": "local", "marketplace_id": ""},
                "unexpected_field": True,
            }
        ),
        encoding="utf-8",
    )

    service = PluginIndexService(
        plugin_root=plugin_root,
        repo_root=tmp_path,
        omubot_version="1.5.0",
    )

    entry = service.entry_for("bad_index")

    assert entry is not None
    assert entry["manifest_status"] == "invalid"
    assert any("plugin.json" in warning for warning in entry["warnings"])


def test_plugin_index_preserves_manifest_v3_dependency_fields(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugins"
    plugin_dir = plugin_root / "dependency_index"
    plugin_dir.mkdir(parents=True)

    (plugin_dir / "plugin.py").write_text(
        """from kernel.types import AmadeusPlugin


class DependencyIndexPlugin(AmadeusPlugin):
    name = "dependency_index"
    version = "1.0.0"
    priority = 100
""",
        encoding="utf-8",
    )
    (plugin_dir / "config.default.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": "dependency_index",
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
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "dependency_index",
                "display_name": {"zh": "依赖索引", "en": "Dependency Index"},
                "description": "A plugin with manifest v3 dependency fields.",
                "version": "1.0.0",
                "priority": 100,
                "tier": "user",
                "toggle_policy": "runtime",
                "category": "tool",
                "permissions": [],
                "capabilities": [],
                "author": "Omubot Tests",
                "min_omubot_version": "0.1.0",
                "required_dependencies": {"context": ">=1.0.0"},
                "optional_dependencies": {"slang": ">=0.1.0"},
                "config": {
                    "defaults": "config.default.json",
                    "schema": "config.schema.json",
                    "apply_mode": "hot",
                    "restart_required_fields": [],
                },
                "store": {"visibility": "local", "marketplace_id": ""},
            }
        ),
        encoding="utf-8",
    )

    service = PluginIndexService(
        plugin_root=plugin_root,
        repo_root=tmp_path,
        omubot_version="1.5.0",
    )

    entry = service.entry_for("dependency_index")

    assert entry is not None
    assert entry.get("required_dependencies", {}) == {"context": ">=1.0.0"}
    assert entry.get("optional_dependencies", {}) == {"slang": ">=0.1.0"}
    assert entry.get("dependencies", {}) == {}
