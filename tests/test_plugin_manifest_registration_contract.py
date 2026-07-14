"""Contracts for manifest validation during explicit plugin registration."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import kernel.manifest as manifest_module
from kernel.bus import PluginBus


def test_register_rejects_directory_plugin_with_future_manifest_version(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "strict_plugin"
    plugin_dir.mkdir()
    plugin_path = plugin_dir / "plugin.py"
    plugin_path.write_text(
        """
from kernel.types import AmadeusPlugin


class StrictPlugin(AmadeusPlugin):
    name = "strict_plugin"
    version = "1.0.0"
    priority = 100
""",
        encoding="utf-8",
    )
    (plugin_dir / "config.default.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": "strict_plugin",
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
                "manifest_version": 999,
                "name": "strict_plugin",
                "display_name": "Strict Plugin",
                "description": "A directory plugin with a future manifest version.",
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
                "store": {"visibility": "local"},
            }
        ),
        encoding="utf-8",
    )

    module_name = "strict_plugin_registration_contract"
    spec = importlib.util.spec_from_file_location(module_name, plugin_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    try:
        spec.loader.exec_module(module)
        instance = module.StrictPlugin()
        bus = PluginBus()

        with pytest.raises(ValueError):
            bus.register(instance)

        assert bus.get_plugin("strict_plugin") is None
    finally:
        sys.modules.pop(module_name, None)


def test_load_plugin_manifest_rejects_name_mismatch(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "sample_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "config.default.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": "different_name",
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
                "name": "different_name",
                "display_name": {"zh": "名称不匹配", "en": "Different Name"},
                "description": "A valid manifest whose name differs from its directory.",
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
    loader = getattr(
        manifest_module,
        "load_plugin_manifest",
        lambda path, *, expected_name: manifest_module.parse_plugin_manifest_data(
            json.loads(path.read_text(encoding="utf-8"))
        ),
    )

    with pytest.raises(ValueError):
        loader(manifest_path, expected_name="sample_plugin")


def test_discovery_rejects_invalid_manifest_before_importing_plugin(
    tmp_path: Path,
) -> None:
    plugin_name = "invalid_priority"
    plugin_dir = tmp_path / plugin_name
    plugin_dir.mkdir()
    sentinel = tmp_path / "plugin_imported"
    (plugin_dir / "plugin.py").write_text(
        f"""
from pathlib import Path

from kernel.types import AmadeusPlugin


Path({str(sentinel)!r}).write_text("imported")


class InvalidPriorityPlugin(AmadeusPlugin):
    name = "invalid_priority"
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
                    "zh": "无效优先级插件",
                    "en": "Invalid Priority Plugin",
                },
                "description": "A plugin whose manifest priority has the wrong type.",
                "version": "1.0.0",
                "priority": "high",
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
    bus = PluginBus()

    discovered = bus.discover_plugins(str(tmp_path))

    assert discovered == 0
    assert bus.get_plugin(plugin_name) is None
    assert not sentinel.exists(), "invalid manifest must fail before plugin import"


def test_discovery_rejects_plugin_requiring_newer_omubot_before_import(
    tmp_path: Path,
) -> None:
    plugin_name = "future_runtime"
    plugin_dir = tmp_path / plugin_name
    plugin_dir.mkdir()
    sentinel = tmp_path / "runtime_plugin_imported"
    (plugin_dir / "plugin.py").write_text(
        f"""
from pathlib import Path

from kernel.types import AmadeusPlugin


Path({str(sentinel)!r}).write_text("imported")


class FutureRuntimePlugin(AmadeusPlugin):
    name = "future_runtime"
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
                    "zh": "未来运行时插件",
                    "en": "Future Runtime Plugin",
                },
                "description": "A plugin requiring a newer Omubot runtime.",
                "version": "1.0.0",
                "priority": 100,
                "tier": "user",
                "toggle_policy": "runtime",
                "category": "tool",
                "permissions": [],
                "capabilities": [],
                "author": "Omubot Tests",
                "min_omubot_version": "999.0.0",
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

    try:
        bus = PluginBus(omubot_version="1.5.0")
    except TypeError:
        bus = PluginBus()

    discovered = bus.discover_plugins(str(tmp_path))

    assert discovered == 0
    assert bus.get_plugin(plugin_name) is None
    assert not sentinel.exists(), "runtime gate must run before plugin import"
