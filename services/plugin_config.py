"""Standardized JSON plugin configuration storage.

Runtime overrides are stored per plugin:

    storage/plugins/config/<name>.json

Each file uses the fixed contract:

    {"schema_version": 1, "plugin": "<name>", "values": {...}}

The previous aggregate store at ``storage/plugins/plugin-config.json`` is kept
as a read-only migration fallback so older Admin saves remain visible until the
next write creates the new per-plugin file.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from kernel.manifest import (
    PluginManifestError,
    load_plugin_manifest,
    resolve_manifest_config_paths,
    validate_plugin_config_values,
)


def _read_json_object(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _unwrap_values(payload: dict[str, Any]) -> dict[str, Any]:
    values = payload.get("values")
    if isinstance(values, dict):
        return values
    return {}


def read_plugin_override_payload(path: Path, *, plugin_name: str) -> dict[str, Any]:
    """Read one canonical override without treating corruption as no override."""

    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PluginManifestError(
            f"plugin config override JSON is invalid for {plugin_name}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise PluginManifestError(
            f"plugin config override must be an object for {plugin_name}"
        )
    if payload.get("schema_version") != 1:
        raise PluginManifestError(
            f"plugin config override requires schema_version=1 for {plugin_name}"
        )
    if payload.get("plugin") != plugin_name:
        raise PluginManifestError(
            "plugin config override identity mismatch: "
            f"expected={plugin_name} actual={payload.get('plugin')}"
        )
    if not isinstance(payload.get("values"), dict):
        raise PluginManifestError(
            f"plugin config override values must be an object for {plugin_name}"
        )
    return payload


class PluginConfigStore:
    """Per-plugin JSON override store with legacy aggregate fallback."""

    def __init__(
        self,
        path: str | Path = "storage/plugins/config",
        *,
        plugin_root: str | Path = "plugins",
    ) -> None:
        raw_path = Path(path)
        if raw_path.suffix == ".json":
            self._legacy_path = raw_path
            self._config_dir = raw_path.parent / "config"
        else:
            self._config_dir = raw_path
            self._legacy_path = raw_path.parent / "plugin-config.json"
        self._plugin_root = Path(plugin_root)
        self._migrate_legacy_if_needed()

    @property
    def path(self) -> Path:
        return self._config_dir

    @property
    def legacy_path(self) -> Path:
        return self._legacy_path

    def plugin_path(self, name: str) -> Path:
        safe_name = str(name).strip()
        return self._config_dir / f"{safe_name}.json"

    def default_path(self, name: str) -> Path:
        plugin_dir = self._plugin_root / name
        manifest_path = plugin_dir / "plugin.json"
        if manifest_path.is_file():
            manifest = load_plugin_manifest(manifest_path, expected_name=name)
            default_path, _ = resolve_manifest_config_paths(manifest_path, manifest)
            return default_path
        return plugin_dir / "config.default.json"

    def schema_path(self, name: str) -> Path:
        plugin_dir = self._plugin_root / name
        manifest_path = plugin_dir / "plugin.json"
        if manifest_path.is_file():
            manifest = load_plugin_manifest(manifest_path, expected_name=name)
            _, schema_path = resolve_manifest_config_paths(manifest_path, manifest)
            return schema_path
        return plugin_dir / "config.schema.json"

    def load_defaults(self, name: str) -> dict[str, Any]:
        return _unwrap_values(_read_json_object(self.default_path(name)))

    def load_schema(self, name: str) -> dict[str, Any]:
        return _read_json_object(self.schema_path(name))

    def load(self) -> dict[str, dict[str, Any]]:
        names: set[str] = set()
        if self._config_dir.is_dir():
            for path in sorted(self._config_dir.glob("*.json")):
                read_plugin_override_payload(path, plugin_name=path.stem)
                names.add(path.stem)
        names.update(self._load_legacy())
        return {
            name: values
            for name in sorted(names)
            if (values := self.get(name))
        }

    def get(self, name: str) -> dict[str, Any]:
        return dict(self.get_entry(name).get("values", {}))

    def set_values(self, name: str, values: dict[str, Any]) -> None:
        if not isinstance(values, dict):
            raise TypeError("plugin config values must be a dict")
        self._validate_effective_values(name, values)
        payload = {
            "schema_version": 1,
            "plugin": str(name),
            "values": values,
            "updated_at": time.time(),
        }
        self._write_payload(self.plugin_path(name), payload)

    def get_entry(self, name: str) -> dict[str, Any]:
        plugin_dir = self._plugin_root / name
        manifest_path = plugin_dir / "plugin.json"
        if manifest_path.is_file():
            manifest = load_plugin_manifest(manifest_path, expected_name=name)
            default_path, schema_path = resolve_manifest_config_paths(
                manifest_path,
                manifest,
            )
            apply_mode = manifest.config.apply_mode
            restart_required_fields = list(
                manifest.config.restart_required_fields
            )
        else:
            default_path = plugin_dir / "config.default.json"
            schema_path = plugin_dir / "config.schema.json"
            apply_mode = None
            restart_required_fields = None
        override_path = self.plugin_path(name)
        defaults = self.load_defaults(name)
        schema = self.load_schema(name)

        payload = read_plugin_override_payload(override_path, plugin_name=name)
        values = _unwrap_values(payload)
        updated_at = payload.get("updated_at", 0.0)
        source = "override" if values or override_path.is_file() else ""

        if not values and not override_path.is_file():
            legacy_values = self._load_legacy().get(name, {})
            if legacy_values:
                values = legacy_values
                updated_at = self._legacy_updated_at(name)
                source = "legacy"

        effective_values = _merge_dicts(defaults, values)
        if manifest_path.is_file():
            validate_plugin_config_values(
                schema,
                effective_values,
                plugin_name=name,
            )
        return {
            "schema_version": 1,
            "plugin": name,
            "values": values,
            "defaults": defaults,
            "effective_values": effective_values,
            "schema": schema,
            "updated_at": updated_at,
            "path": str(override_path),
            "default_path": str(default_path),
            "schema_path": str(schema_path),
            "has_saved_values": bool(values),
            "source": source,
            "apply_mode": apply_mode,
            "restart_required_fields": restart_required_fields,
        }

    def as_payload(self) -> dict[str, Any]:
        return {
            "version": 2,
            "schema_version": 1,
            "path": str(self._config_dir),
            "legacy_path": str(self._legacy_path),
            "plugins": {
                name: {
                    "values": values,
                    "path": str(self.plugin_path(name)),
                }
                for name, values in sorted(self.load().items())
            },
        }

    def _load_legacy(self) -> dict[str, dict[str, Any]]:
        payload = _read_json_object(self._legacy_path)
        plugins = payload.get("plugins", {})
        if not isinstance(plugins, dict):
            return {}

        values_by_name: dict[str, dict[str, Any]] = {}
        for name, item in plugins.items():
            if not isinstance(name, str) or not isinstance(item, dict):
                continue
            values = item.get("values")
            if isinstance(values, dict):
                values_by_name[name] = values
        return values_by_name

    def _legacy_updated_at(self, name: str) -> float:
        payload = _read_json_object(self._legacy_path)
        plugins = payload.get("plugins", {})
        if not isinstance(plugins, dict):
            return 0.0
        entry = plugins.get(name)
        if not isinstance(entry, dict):
            return 0.0
        try:
            return float(entry.get("updated_at") or 0.0)
        except Exception:
            return 0.0

    def _migrate_legacy_if_needed(self) -> None:
        legacy_values = self._load_legacy()
        if not legacy_values:
            return
        for name, values in legacy_values.items():
            path = self.plugin_path(name)
            if path.is_file():
                continue
            self._validate_effective_values(name, values)
            payload = {
                "schema_version": 1,
                "plugin": name,
                "values": values,
                "updated_at": self._legacy_updated_at(name) or time.time(),
                "migrated_from": str(self._legacy_path),
            }
            self._write_payload(path, payload)

    def _validate_effective_values(
        self,
        name: str,
        values: dict[str, Any],
    ) -> None:
        manifest_path = self._plugin_root / name / "plugin.json"
        if not manifest_path.is_file():
            return
        manifest = load_plugin_manifest(manifest_path, expected_name=name)
        defaults_path, schema_path = resolve_manifest_config_paths(
            manifest_path,
            manifest,
        )
        defaults = _unwrap_values(_read_json_object(defaults_path))
        schema = _read_json_object(schema_path)
        validate_plugin_config_values(
            schema,
            _merge_dicts(defaults, values),
            plugin_name=name,
        )

    def _write_payload(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
