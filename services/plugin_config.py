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
        return self._plugin_root / name / "config.default.json"

    def schema_path(self, name: str) -> Path:
        return self._plugin_root / name / "config.schema.json"

    def load_defaults(self, name: str) -> dict[str, Any]:
        return _unwrap_values(_read_json_object(self.default_path(name)))

    def load_schema(self, name: str) -> dict[str, Any]:
        return _read_json_object(self.schema_path(name))

    def load(self) -> dict[str, dict[str, Any]]:
        values_by_name: dict[str, dict[str, Any]] = {}
        if self._config_dir.is_dir():
            for path in sorted(self._config_dir.glob("*.json")):
                payload = _read_json_object(path)
                name = str(payload.get("plugin") or path.stem)
                values = _unwrap_values(payload)
                if name and values:
                    values_by_name[name] = values

        for name, values in self._load_legacy().items():
            values_by_name.setdefault(name, values)
        return values_by_name

    def get(self, name: str) -> dict[str, Any]:
        return dict(self.get_entry(name).get("values", {}))

    def set_values(self, name: str, values: dict[str, Any]) -> None:
        if not isinstance(values, dict):
            raise TypeError("plugin config values must be a dict")
        payload = {
            "schema_version": 1,
            "plugin": str(name),
            "values": values,
            "updated_at": time.time(),
        }
        self._write_payload(self.plugin_path(name), payload)

    def get_entry(self, name: str) -> dict[str, Any]:
        default_path = self.default_path(name)
        schema_path = self.schema_path(name)
        override_path = self.plugin_path(name)
        defaults = self.load_defaults(name)
        schema = self.load_schema(name)

        payload = _read_json_object(override_path)
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
            payload = {
                "schema_version": 1,
                "plugin": name,
                "values": values,
                "updated_at": self._legacy_updated_at(name) or time.time(),
                "migrated_from": str(self._legacy_path),
            }
            self._write_payload(path, payload)

    def _write_payload(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
