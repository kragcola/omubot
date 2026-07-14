from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from kernel.bus import PluginBus
from kernel.types import AmadeusPlugin


def _load_manifest_plugin(source_root: Path, module_name: str) -> AmadeusPlugin:
    plugin_name = "shared_runtime"
    plugin_dir = source_root / plugin_name
    plugin_dir.mkdir(parents=True)
    plugin_path = plugin_dir / "plugin.py"
    plugin_path.write_text(
        """
from kernel.types import AmadeusPlugin


class RuntimeCollisionPlugin(AmadeusPlugin):
    name = "shared_runtime"
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
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": plugin_name,
                "display_name": {
                    "zh": "运行时重名插件",
                    "en": "Runtime Name Collision Plugin",
                },
                "description": "A valid plugin used to test runtime name collisions.",
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
                "store": {
                    "visibility": "local",
                    "marketplace_id": "",
                },
            }
        ),
        encoding="utf-8",
    )

    spec = importlib.util.spec_from_file_location(module_name, plugin_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load test plugin module {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.RuntimeCollisionPlugin()


def test_register_duplicate_runtime_name_fails_closed(tmp_path: Path) -> None:
    module_names = ("runtime_collision_first", "runtime_collision_second")
    try:
        first = _load_manifest_plugin(tmp_path / "first", module_names[0])
        second = _load_manifest_plugin(tmp_path / "second", module_names[1])

        bus = PluginBus()
        bus.register(first)

        with pytest.raises(ValueError):
            bus.register(second)

        assert len(bus.plugins) == 1
    finally:
        for module_name in module_names:
            sys.modules.pop(module_name, None)
