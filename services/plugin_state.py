"""Persistent runtime state for plugin governance."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class PluginStateStore:
    """Small JSON store for Admin-managed plugin enabled states.

    This intentionally stores only runtime governance decisions. Static plugin
    metadata remains in plugin classes / plugin.json manifests.
    """

    def __init__(self, path: str | Path = "storage/plugins/plugin-state.json") -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, bool]:
        payload = self._read_payload()
        plugins = payload.get("plugins", {})
        if not isinstance(plugins, dict):
            return {}

        states: dict[str, bool] = {}
        for name, item in plugins.items():
            if not isinstance(name, str) or not isinstance(item, dict):
                continue
            enabled = item.get("enabled")
            if isinstance(enabled, bool):
                states[name] = enabled
        return states

    def get(self, name: str) -> bool | None:
        return self.load().get(name)

    def set_enabled(self, name: str, enabled: bool) -> None:
        payload = self._read_payload()
        plugins = payload.setdefault("plugins", {})
        if not isinstance(plugins, dict):
            plugins = {}
            payload["plugins"] = plugins

        plugins[name] = {
            "enabled": enabled,
            "updated_at": time.time(),
        }
        payload["version"] = 1
        self._write_payload(payload)

    def as_payload(self) -> dict[str, Any]:
        payload = self._read_payload()
        payload.setdefault("version", 1)
        payload.setdefault("plugins", {})
        payload["path"] = str(self._path)
        return payload

    def _read_payload(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"version": 1, "plugins": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "plugins": {}}
        return data if isinstance(data, dict) else {"version": 1, "plugins": {}}

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self._path)
