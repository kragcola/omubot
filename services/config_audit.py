"""Small config audit store for Phase 8 config governance."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class ConfigAuditStore:
    """Persist recent config save audits in a compact JSON file."""

    def __init__(
        self,
        path: str | Path = "storage/config/config-audit.json",
        *,
        max_entries: int = 60,
    ) -> None:
        self._path = Path(path)
        self._max_entries = max(10, int(max_entries or 60))

    @property
    def path(self) -> Path:
        return self._path

    def recent(self, limit: int = 8) -> list[dict[str, Any]]:
        payload = self._read_payload()
        entries = payload.get("entries", [])
        if not isinstance(entries, list):
            return []
        sanitized = [item for item in entries if isinstance(item, dict)]
        return sanitized[: max(1, int(limit or 8))]

    def append(
        self,
        *,
        config_path: str,
        mode: str,
        summary: dict[str, Any],
        changes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = self._read_payload()
        entries = payload.setdefault("entries", [])
        if not isinstance(entries, list):
            entries = []
            payload["entries"] = entries

        timestamp = time.time()
        entry = {
            "id": f"cfg-{int(timestamp * 1000)}",
            "saved_at": timestamp,
            "config_path": str(config_path),
            "mode": str(mode or "structured"),
            "summary": dict(summary or {}),
            "changes": [dict(item) for item in changes if isinstance(item, dict)][:120],
        }
        entries.insert(0, entry)
        payload["version"] = 1
        payload["entries"] = entries[: self._max_entries]
        self._write_payload(payload)
        return entry

    def as_payload(self, *, limit: int = 8) -> dict[str, Any]:
        payload = self._read_payload()
        payload.setdefault("version", 1)
        payload["entries"] = self.recent(limit)
        payload["path"] = str(self._path)
        return payload

    def _read_payload(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"version": 1, "entries": []}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "entries": []}
        return data if isinstance(data, dict) else {"version": 1, "entries": []}

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self._path)
