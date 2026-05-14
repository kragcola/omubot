"""Sticker store: persistent storage and index for collected stickers."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Magic byte constants for image format detection
# ---------------------------------------------------------------------------

_MAGIC_JPEG = b"\xff\xd8\xff"
_MAGIC_PNG = b"\x89PNG"  # 89 50 4e 47
_MAGIC_WEBP_RIFF = b"RIFF"
_MAGIC_GIF87 = b"GIF87a"
_MAGIC_GIF89 = b"GIF89a"

_INDEX_FILE = "index.json"
_USAGE_FILE = "usage.json"
_RECENT_GROUP_WINDOW = 6
_RECENT_GLOBAL_WINDOW = 20
_GLOBAL_HOT_THRESHOLD = 4
_LONG_TERM_SHARE_THRESHOLD = 0.20
_LONG_TERM_MIN_TOTAL_SENDS = 10
_LONG_TERM_RECENT_HOURS = 24
_MIN_LIBRARY_FOR_COOLDOWN = 8
_MIN_ALTERNATIVES_FOR_COOLDOWN = 3
_MAX_USAGE_EVENTS = 500


@dataclass(frozen=True)
class StickerSendDecision:
    allowed: bool
    reason: str = ""
    alternatives: tuple[str, ...] = ()
    cooled_ids: tuple[str, ...] = ()


def _detect_format(data: bytes) -> str:
    """Detect image format from magic bytes.

    Returns the file extension (without dot): 'jpg', 'png', 'webp', 'gif'.
    Raises ValueError for unknown formats.
    """
    if data[:3] == _MAGIC_JPEG:
        return "jpg"
    if data[:4] == _MAGIC_PNG:
        return "png"
    if data[:4] == _MAGIC_WEBP_RIFF:
        return "webp"
    if data[:6] in (_MAGIC_GIF87, _MAGIC_GIF89):
        return "gif"

    raise ValueError(f"Unknown or unsupported image format (header: {data[:8].hex()})")


def _compute_hash(data: bytes) -> str:
    """Return the first 8 hex chars of SHA-256 digest."""
    return hashlib.sha256(data).hexdigest()[:8]


class StickerStore:
    """Persistent sticker library backed by a directory and index.json."""

    def __init__(self, storage_dir: str, max_count: int = 200) -> None:
        self._storage_dir = Path(storage_dir)
        self._max_count = max_count
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, Any] = self._load_index()
        self._usage: dict[str, Any] = self._load_usage()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def storage_dir(self) -> Path:
        """Return the resolved storage directory path."""
        return self._storage_dir

    @property
    def max_count(self) -> int:
        """Return the configured maximum sticker count."""
        return self._max_count

    # ------------------------------------------------------------------
    # Index I/O
    # ------------------------------------------------------------------

    def _index_path(self) -> Path:
        return self._storage_dir / _INDEX_FILE

    def _usage_path(self) -> Path:
        return self._storage_dir / _USAGE_FILE

    def _load_index(self) -> dict[str, Any]:
        """Load index from disk. Returns empty index if file doesn't exist."""
        path = self._index_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return dict(data.get("stickers", {}))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_index(self) -> None:
        """Persist the current index to disk atomically."""
        path = self._index_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"stickers": self._index}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def _load_usage(self) -> dict[str, Any]:
        path = self._usage_path()
        if not path.exists():
            return {"version": 1, "events": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            events = data.get("events", [])
            if not isinstance(events, list):
                events = []
            return {"version": 1, "events": events[-_MAX_USAGE_EVENTS:]}
        except (json.JSONDecodeError, OSError):
            return {"version": 1, "events": []}

    def _save_usage(self) -> None:
        path = self._usage_path()
        tmp = path.with_suffix(".json.tmp")
        events = list(self._usage.get("events", []))[-_MAX_USAGE_EVENTS:]
        self._usage = {"version": 1, "events": events}
        tmp.write_text(
            json.dumps(self._usage, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def list_all(self) -> dict[str, Any]:
        """Return a copy of all sticker entries."""
        return dict(self._index)

    def list_sorted(self, sort: str = "default") -> list[dict[str, Any]]:
        """Return sticker entries in the requested sort order."""
        rows: list[dict[str, Any]] = []
        for sticker_id, info in self._index.items():
            rows.append({
                "id": sticker_id,
                "description": info.get("description", ""),
                "usage_hint": info.get("usage_hint", ""),
                "send_count": int(info.get("send_count", 0) or 0),
                "source": info.get("source", ""),
                "created_at": info.get("created_at"),
                "last_sent": info.get("last_sent"),
            })

        def _time_score(item: dict[str, Any]) -> float:
            return max(
                self._timestamp(item.get("last_sent")),
                self._timestamp(item.get("created_at")),
            )

        if sort == "time":
            rows.sort(
                key=lambda item: (
                    _time_score(item),
                    int(item.get("send_count", 0) or 0),
                    str(item.get("id") or ""),
                ),
                reverse=True,
            )
            return rows

        rows.sort(
            key=lambda item: (
                int(item.get("send_count", 0) or 0),
                self._timestamp(item.get("last_sent")),
                self._timestamp(item.get("created_at")),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )
        return rows

    def get(self, sticker_id: str) -> dict[str, Any] | None:
        """Return entry for the given sticker_id, or None if not found."""
        return self._index.get(sticker_id)

    def resolve_path(self, sticker_id: str) -> Path | None:
        """Return the absolute file path for the sticker, or None if not found."""
        entry = self._index.get(sticker_id)
        if entry is None:
            return None
        return self._storage_dir / entry["file"]

    def lookup_by_hash(self, image_data: bytes) -> str | None:
        """Return sticker_id if image_data matches an existing sticker, else None."""
        sticker_id = "stk_" + _compute_hash(image_data)
        if sticker_id in self._index:
            return sticker_id
        return None

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add(
        self,
        image_data: bytes,
        description: str,
        usage_hint: str,
        source: str = "auto",
    ) -> tuple[str, bool]:
        """Add a sticker to the store.

        Rejects GIFs and unknown formats.
        Deduplicates by content hash.

        Returns:
            (sticker_id, is_new) — is_new=False means it already existed.
        """
        ext = _detect_format(image_data)  # raises ValueError for GIF / unknown
        hash_prefix = _compute_hash(image_data)
        sticker_id = f"stk_{hash_prefix}"

        # Dedup: already known
        if sticker_id in self._index:
            return (sticker_id, False)

        filename = f"{sticker_id}.{ext}"
        file_path = self._storage_dir / filename
        file_path.write_bytes(image_data)

        now_iso = datetime.now(UTC).isoformat()
        self._index[sticker_id] = {
            "file": filename,
            "description": description,
            "usage_hint": usage_hint,
            "source": source,
            "send_count": 0,
            "last_sent": None,
            "created_at": now_iso,
        }
        self._save_index()
        return (sticker_id, True)

    def remove(self, sticker_id: str) -> bool:
        """Remove sticker from index and delete its file.

        Returns True if the sticker was found and removed, False otherwise.
        """
        entry = self._index.pop(sticker_id, None)
        if entry is None:
            return False

        file_path = self._storage_dir / entry["file"]
        if file_path.exists():
            file_path.unlink()

        self._save_index()
        return True

    def update(
        self,
        sticker_id: str,
        description: str | None = None,
        usage_hint: str | None = None,
    ) -> bool:
        """Update description and/or usage_hint for a sticker.

        Returns True if the sticker was found and updated, False otherwise.
        """
        entry = self._index.get(sticker_id)
        if entry is None:
            return False
        if description is not None:
            entry["description"] = description
        if usage_hint is not None:
            entry["usage_hint"] = usage_hint
        self._save_index()
        return True

    def record_send(
        self,
        sticker_id: str,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Increment send_count and update last_sent timestamp."""
        entry = self._index.get(sticker_id)
        if entry is None:
            return
        entry["send_count"] = entry.get("send_count", 0) + 1
        now_iso = datetime.now(UTC).isoformat()
        entry["last_sent"] = now_iso
        self._save_index()
        self._usage.setdefault("events", []).append(
            {
                "sticker_id": sticker_id,
                "group_id": str(group_id or ""),
                "user_id": str(user_id or ""),
                "sent_at": now_iso,
            }
        )
        self._save_usage()

    def check_send_allowed(
        self,
        sticker_id: str,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
    ) -> StickerSendDecision:
        if sticker_id not in self._index:
            return StickerSendDecision(allowed=False, reason="表情包不存在")
        alternatives = self.suggest_candidates(
            group_id=group_id,
            user_id=user_id,
            limit=6,
            exclude={sticker_id},
            ignore_minimum=True,
        )
        if not self._cooldown_enabled(alternatives):
            return StickerSendDecision(allowed=True)

        cooled_ids = self._cooled_sticker_ids(group_id=group_id, user_id=user_id)
        if sticker_id in cooled_ids:
            reason = self._cooldown_reason(sticker_id, group_id=group_id, user_id=user_id)
            return StickerSendDecision(
                allowed=False,
                reason=reason,
                alternatives=tuple(alternatives),
                cooled_ids=tuple(sorted(cooled_ids)),
            )
        return StickerSendDecision(allowed=True, cooled_ids=tuple(sorted(cooled_ids)))

    def suggest_candidates(
        self,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
        limit: int = 6,
        exclude: set[str] | None = None,
        ignore_minimum: bool = False,
    ) -> list[str]:
        exclude_ids = set(exclude or set())
        available_ids = [sticker_id for sticker_id in self._index if sticker_id not in exclude_ids]
        cooled_ids = self._cooled_sticker_ids(group_id=group_id, user_id=user_id)
        uncooled_ids = [sticker_id for sticker_id in available_ids if sticker_id not in cooled_ids]
        candidate_ids = uncooled_ids if ignore_minimum or self._cooldown_enabled(uncooled_ids) else available_ids
        return self._sort_candidate_ids(candidate_ids)[:limit]

    def _sort_candidate_ids(self, sticker_ids: list[str]) -> list[str]:
        rows: list[tuple[tuple[int, float, float], str]] = []
        now = datetime.now(UTC)
        for sticker_id in sticker_ids:
            entry = self._index.get(sticker_id)
            if entry is None:
                continue
            send_count = int(entry.get("send_count") or 0)
            last_sent = self._parse_dt(entry.get("last_sent"))
            created_at = self._parse_dt(entry.get("created_at")) or now
            # Low count first, then least recently sent, then newer discoveries.
            last_sent_ts = last_sent.timestamp() if last_sent else 0.0
            rows.append(((send_count, last_sent_ts, -created_at.timestamp()), sticker_id))
        rows.sort(key=lambda item: item[0])
        return [sticker_id for _score, sticker_id in rows]

    # ------------------------------------------------------------------
    # Prompt injection
    # ------------------------------------------------------------------

    def format_prompt_view(
        self,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
        max_items: int = 40,
    ) -> str:
        """Return a compact view of the sticker library for system prompt injection.

        Only includes id, format, description, and usage_hint — volatile fields
        (send_count, last_sent, created_at) are excluded for prompt cache stability.
        """
        if not self._index:
            return "当前表情包库为空"

        cooled_ids = self._cooled_sticker_ids(group_id=group_id, user_id=user_id)
        uncooled_ids = [sticker_id for sticker_id in self._index if sticker_id not in cooled_ids]
        cooldown_active = self._cooldown_enabled(uncooled_ids)
        recommended = self.suggest_candidates(group_id=group_id, user_id=user_id, limit=max_items)
        if not recommended:
            fallback_ids = uncooled_ids if cooldown_active else list(self._index)
            recommended = self._sort_candidate_ids(fallback_ids)[:max_items]

        lines = [
            "当前表情包库（优先从推荐候选里选择，避免重复使用冷却中的表情）：",
            "推荐候选：",
        ]
        for sticker_id in recommended[:max_items]:
            entry = self._index[sticker_id]
            description = entry.get("description", "")
            usage_hint = entry.get("usage_hint", "")
            file_name = entry.get("file", "")
            fmt = file_name.rsplit(".", 1)[-1].upper() if "." in file_name else "?"
            fmt_tag = "动图" if fmt == "GIF" else "静态"
            lines.append(
                f"«表情包:{sticker_id}» [{fmt_tag}] {description} | {usage_hint}"
            )
        if cooldown_active and cooled_ids:
            visible_cooled = [sid for sid in self._index if sid in cooled_ids][:12]
            if visible_cooled:
                lines.append("冷却中，请不要选择：" + "、".join(visible_cooled))

        return "\n".join(lines)

    def _cooldown_enabled(self, alternatives: list[str] | tuple[str, ...]) -> bool:
        return len(self._index) >= _MIN_LIBRARY_FOR_COOLDOWN and len(alternatives) >= _MIN_ALTERNATIVES_FOR_COOLDOWN

    def _events(self) -> list[dict[str, Any]]:
        return [event for event in self._usage.get("events", []) if isinstance(event, dict)]

    def _cooled_sticker_ids(
        self,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
    ) -> set[str]:
        cooled: set[str] = set()
        events = self._events()
        if group_id:
            group_events = [e for e in events if str(e.get("group_id") or "") == str(group_id)]
            cooled.update(
                str(e.get("sticker_id") or "")
                for e in group_events[-_RECENT_GROUP_WINDOW:]
                if e.get("sticker_id") in self._index
            )
        elif user_id:
            private_events = [
                e for e in events
                if not str(e.get("group_id") or "") and str(e.get("user_id") or "") == str(user_id)
            ]
            cooled.update(
                str(e.get("sticker_id") or "")
                for e in private_events[-_RECENT_GROUP_WINDOW:]
                if e.get("sticker_id") in self._index
            )

        recent_global = events[-_RECENT_GLOBAL_WINDOW:]
        for sticker_id in self._index:
            if sum(1 for e in recent_global if e.get("sticker_id") == sticker_id) >= _GLOBAL_HOT_THRESHOLD:
                cooled.add(sticker_id)

        total_sends = sum(int(entry.get("send_count") or 0) for entry in self._index.values())
        if total_sends >= _LONG_TERM_MIN_TOTAL_SENDS:
            cutoff = datetime.now(UTC) - timedelta(hours=_LONG_TERM_RECENT_HOURS)
            for sticker_id, entry in self._index.items():
                count = int(entry.get("send_count") or 0)
                if count / total_sends < _LONG_TERM_SHARE_THRESHOLD:
                    continue
                last_sent = self._parse_dt(entry.get("last_sent"))
                if last_sent is not None and last_sent >= cutoff:
                    cooled.add(sticker_id)
        cooled.discard("")
        return cooled

    def _cooldown_reason(
        self,
        sticker_id: str,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        events = self._events()
        if group_id:
            group_events = [e for e in events if str(e.get("group_id") or "") == str(group_id)]
            if sticker_id in {
                str(e.get("sticker_id") or "")
                for e in group_events[-_RECENT_GROUP_WINDOW:]
            }:
                return f"这张表情刚在本群最近 {_RECENT_GROUP_WINDOW} 次表情发送中用过"
        elif user_id:
            private_events = [
                e for e in events
                if not str(e.get("group_id") or "") and str(e.get("user_id") or "") == str(user_id)
            ]
            if sticker_id in {
                str(e.get("sticker_id") or "")
                for e in private_events[-_RECENT_GROUP_WINDOW:]
            }:
                return f"这张表情刚在本私聊最近 {_RECENT_GROUP_WINDOW} 次表情发送中用过"
        recent_global = events[-_RECENT_GLOBAL_WINDOW:]
        if sum(1 for e in recent_global if e.get("sticker_id") == sticker_id) >= _GLOBAL_HOT_THRESHOLD:
            return f"这张表情在全局最近 {_RECENT_GLOBAL_WINDOW} 次中出现过多"
        return "这张表情近期使用占比过高，先换一张"

    @classmethod
    def _timestamp(cls, value: Any) -> float:
        parsed = cls._parse_dt(value)
        return parsed.timestamp() if parsed is not None else 0.0

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
