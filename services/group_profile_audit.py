"""Compact audit trail for per-group profile changes."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class GroupProfileAuditStore:
    """Persist recent group profile edits in a compact JSON file."""

    def __init__(
        self,
        path: str | Path = "storage/groups/group-profile-audit.json",
        *,
        max_entries: int = 240,
    ) -> None:
        self._path = Path(path)
        self._max_entries = max(20, int(max_entries or 240))

    @property
    def path(self) -> Path:
        return self._path

    def recent(self, *, group_id: str | int | None = None, limit: int = 8) -> list[dict[str, Any]]:
        payload = self._read_payload()
        entries = payload.get("entries", [])
        if not isinstance(entries, list):
            return []
        normalized = [item for item in entries if isinstance(item, dict)]
        if group_id is not None:
            target = str(group_id)
            normalized = [item for item in normalized if str(item.get("group_id", "")) == target]
        return normalized[: max(1, int(limit or 8))]

    def append(
        self,
        *,
        group_id: str | int,
        action: str,
        summary: dict[str, Any],
        changes: list[dict[str, Any]],
        group_name: str = "",
    ) -> dict[str, Any]:
        payload = self._read_payload()
        entries = payload.setdefault("entries", [])
        if not isinstance(entries, list):
            entries = []
            payload["entries"] = entries

        ts = time.time()
        entry = {
            "id": f"grp-{int(ts * 1000)}",
            "saved_at": ts,
            "group_id": str(group_id),
            "group_name": str(group_name or ""),
            "action": str(action or "save"),
            "summary": dict(summary or {}),
            "changes": [dict(item) for item in changes if isinstance(item, dict)][:80],
        }
        entries.insert(0, entry)
        payload["version"] = 1
        payload["entries"] = entries[: self._max_entries]
        self._write_payload(payload)
        return entry

    def as_payload(
        self,
        *,
        group_id: str | int | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        payload = self._read_payload()
        payload.setdefault("version", 1)
        payload["entries"] = self.recent(group_id=group_id, limit=limit)
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
