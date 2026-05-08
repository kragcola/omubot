"""Lightweight restoreable config snapshots for Phase 8 rollback."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class ConfigBackupStore:
    """Persist recent restoreable config snapshots with compact metadata."""

    def __init__(
        self,
        path: str | Path = "storage/config/config-backups.json",
        *,
        snapshot_dir: str | Path | None = None,
        max_entries: int = 20,
    ) -> None:
        self._path = Path(path)
        self._snapshot_dir = Path(snapshot_dir) if snapshot_dir else self._path.parent / "backups"
        self._max_entries = max(6, int(max_entries or 20))

    @property
    def path(self) -> Path:
        return self._path

    @property
    def snapshot_dir(self) -> Path:
        return self._snapshot_dir

    def recent(self, limit: int = 8) -> list[dict[str, Any]]:
        payload = self._read_payload()
        entries = payload.get("entries", [])
        if not isinstance(entries, list):
            return []
        public_entries: list[dict[str, Any]] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            snapshot_path = Path(str(item.get("snapshot_path", "")))
            if not snapshot_path.is_file():
                continue
            public_entries.append(self._public_entry(item))
            if len(public_entries) >= max(1, int(limit or 8)):
                break
        return public_entries

    def append(
        self,
        *,
        config_path: str,
        values: dict[str, Any],
        trigger: str,
        mode: str,
        summary: dict[str, Any] | None = None,
        note: str = "",
        source_backup_id: str | None = None,
    ) -> dict[str, Any]:
        payload = self._read_payload()
        entries = payload.setdefault("entries", [])
        if not isinstance(entries, list):
            entries = []
            payload["entries"] = entries

        timestamp = time.time()
        backup_id = f"cfgbk-{time.time_ns()}"
        snapshot_path = self._snapshot_dir / f"{backup_id}.json"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")

        entry = {
            "id": backup_id,
            "created_at": timestamp,
            "config_path": str(config_path),
            "trigger": str(trigger or "save"),
            "mode": str(mode or "structured"),
            "summary": dict(summary or {}),
            "note": str(note or ""),
            "source_backup_id": str(source_backup_id) if source_backup_id else "",
            "snapshot_path": str(snapshot_path),
            "size_bytes": snapshot_path.stat().st_size if snapshot_path.is_file() else 0,
        }
        entries.insert(0, entry)

        removed_entries = entries[self._max_entries :]
        payload["version"] = 1
        payload["entries"] = entries[: self._max_entries]
        self._write_payload(payload)
        for old_entry in removed_entries:
            self._delete_snapshot_file(old_entry)
        return self._public_entry(entry)

    def get_values(self, backup_id: str) -> dict[str, Any]:
        entry = self._find_entry(backup_id)
        if not entry:
            raise KeyError(f"配置快照不存在: {backup_id}")
        snapshot_path = Path(str(entry.get("snapshot_path", "")))
        if not snapshot_path.is_file():
            raise FileNotFoundError(f"配置快照文件不存在: {snapshot_path}")
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("配置快照内容必须是对象")
        return payload

    def get_entry(self, backup_id: str) -> dict[str, Any] | None:
        entry = self._find_entry(backup_id)
        return self._public_entry(entry) if entry else None

    def as_payload(self, *, limit: int = 8) -> dict[str, Any]:
        return {
            "version": 1,
            "path": str(self._path),
            "snapshot_dir": str(self._snapshot_dir),
            "entries": self.recent(limit),
        }

    def _find_entry(self, backup_id: str) -> dict[str, Any] | None:
        payload = self._read_payload()
        entries = payload.get("entries", [])
        if not isinstance(entries, list):
            return None
        for item in entries:
            if isinstance(item, dict) and item.get("id") == backup_id:
                return item
        return None

    def _public_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(entry.get("id", "")),
            "created_at": float(entry.get("created_at", 0) or 0),
            "config_path": str(entry.get("config_path", "")),
            "trigger": str(entry.get("trigger", "save") or "save"),
            "mode": str(entry.get("mode", "structured") or "structured"),
            "summary": dict(entry.get("summary", {}) or {}),
            "note": str(entry.get("note", "")),
            "source_backup_id": str(entry.get("source_backup_id", "")),
            "size_bytes": int(entry.get("size_bytes", 0) or 0),
        }

    def _delete_snapshot_file(self, entry: Any) -> None:
        if not isinstance(entry, dict):
            return
        snapshot_path = Path(str(entry.get("snapshot_path", "")))
        try:
            if snapshot_path.is_file():
                snapshot_path.unlink()
        except Exception:
            return

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
