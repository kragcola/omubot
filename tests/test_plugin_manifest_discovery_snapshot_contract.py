from __future__ import annotations

import json
from pathlib import Path

from kernel.bus import PluginBus


def _manifest_payload(*, priority: int) -> dict[str, object]:
    return {
        "manifest_version": 3,
        "name": "snapshot_plugin",
        "display_name": {"zh": "快照插件", "en": "Snapshot Plugin"},
        "description": "A valid plugin manifest used to verify discovery snapshots.",
        "version": "1.0.0",
        "priority": priority,
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
    }


def test_discovery_applies_the_manifest_snapshot_validated_before_import(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "snapshot_plugin"
    plugin_dir.mkdir()
    manifest_path = plugin_dir / "plugin.json"
    manifest_path.write_text(
        json.dumps(_manifest_payload(priority=100)),
        encoding="utf-8",
    )
    (plugin_dir / "config.default.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": "snapshot_plugin",
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
    rewritten_manifest = json.dumps(_manifest_payload(priority=999))
    (plugin_dir / "plugin.py").write_text(
        f"""
from pathlib import Path

from kernel.types import AmadeusPlugin


Path(__file__).with_name("plugin.json").write_text(
    {rewritten_manifest!r},
    encoding="utf-8",
)


class SnapshotPlugin(AmadeusPlugin):
    name = "snapshot_plugin"
    version = "1.0.0"
    priority = 1
""",
        encoding="utf-8",
    )

    bus = PluginBus()
    discovered = bus.discover_plugins(str(tmp_path))

    assert discovered == 1
    [plugin] = bus.plugins
    assert plugin.priority == 100, (
        "discovery must apply the validated pre-import manifest snapshot; "
        f"got priority {plugin.priority}"
    )
