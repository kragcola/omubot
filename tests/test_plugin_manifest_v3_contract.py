"""Runtime contract tests for strict manifest v3 discovery."""

from __future__ import annotations

import json
from pathlib import Path

from kernel.bus import PluginBus


def _write_directory_plugin(
    tmp_path: Path,
    *,
    manifest_version: int | None,
) -> str:
    plugin_name = "future_manifest"
    plugin_dir = tmp_path / plugin_name
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text(
        """
from kernel.types import AmadeusPlugin


class FutureManifestPlugin(AmadeusPlugin):
    name = "future_manifest"
    version = "1.0.0"
    priority = 100
""",
        encoding="utf-8",
    )
    (plugin_dir / "config.default.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": plugin_name,
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
    manifest: dict[str, object] = {
        "name": plugin_name,
        "display_name": "Future Manifest",
        "version": "1.0.0",
        "description": "A directory plugin used to exercise the manifest v3 contract.",
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
        "store": {"visibility": "local"},
    }
    if manifest_version is not None:
        manifest["manifest_version"] = manifest_version
    (plugin_dir / "plugin.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    return plugin_name


def test_discovery_rejects_directory_plugin_with_future_manifest_version(
    tmp_path: Path,
) -> None:
    plugin_name = _write_directory_plugin(tmp_path, manifest_version=999)
    bus = PluginBus()

    discovered = bus.discover_plugins(str(tmp_path))

    assert discovered == 0, "future manifest versions must fail closed during discovery"
    assert bus.get_plugin(plugin_name) is None


def test_discovery_rejects_directory_plugin_without_manifest_version(
    tmp_path: Path,
) -> None:
    plugin_name = _write_directory_plugin(tmp_path, manifest_version=None)
    bus = PluginBus()

    discovered = bus.discover_plugins(str(tmp_path))

    assert discovered == 0, "manifests without manifest_version must fail closed"
    assert bus.get_plugin(plugin_name) is None


def test_discovery_rejects_directory_plugin_with_non_object_manifest(
    tmp_path: Path,
) -> None:
    plugin_name = _write_directory_plugin(tmp_path, manifest_version=3)
    plugin_dir = tmp_path / plugin_name
    (plugin_dir / "plugin.json").write_text(json.dumps([]), encoding="utf-8")
    bus = PluginBus()

    discovered = bus.discover_plugins(str(tmp_path))

    assert discovered == 0, "non-object plugin manifests must fail closed"
    assert bus.get_plugin(plugin_name) is None
