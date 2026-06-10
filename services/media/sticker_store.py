"""Sticker store: persistent storage and index for collected stickers.

Backed by a directory of image files plus a small SQLite index database
(``stickers.db``). Earlier versions used a JSON index (``index.json``); on
first run the store transparently migrates any legacy ``index.json`` into
SQLite, then leaves the JSON file frozen as a rollback snapshot. Reads are
served from an in-memory mirror of the table (the library is small,
<= max_count); writes go write-through to SQLite. Intent search reuses the
dependency-free ``KeywordBM25Retriever`` from the knowledge service.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.knowledge.retrievers import KeywordBM25Retriever
from services.knowledge.types import KnowledgeChunk
from services.media.jpeg_util import ensure_jfif_app0
from services.storage import close_with_checkpoint_sync

# ---------------------------------------------------------------------------
# Magic byte constants for image format detection
# ---------------------------------------------------------------------------

_MAGIC_JPEG = b"\xff\xd8\xff"
_MAGIC_PNG = b"\x89PNG"  # 89 50 4e 47
_MAGIC_WEBP_RIFF = b"RIFF"
_MAGIC_GIF87 = b"GIF87a"
_MAGIC_GIF89 = b"GIF89a"

_INDEX_FILE = "index.json"  # legacy JSON index (migration source / rollback snapshot)
_DB_FILE = "stickers.db"

# Only auto-captured stickers are eligible for capacity/quality eviction.
# Migrated library, admin-curated, and tool-saved stickers are protected.
_EVICTABLE_SOURCE_PREFIXES = ("stolen_silent",)

_CREATE_STICKERS = """\
CREATE TABLE IF NOT EXISTS stickers (
    sticker_id   TEXT PRIMARY KEY,
    file         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    usage_hint   TEXT NOT NULL DEFAULT '',
    ocr_text     TEXT,
    source       TEXT NOT NULL DEFAULT 'auto',
    send_count   INTEGER NOT NULL DEFAULT 0,
    last_sent    TEXT,
    created_at   TEXT NOT NULL
)"""

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


def _row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a stickers row into the legacy entry dict shape.

    ocr_text uses NULL as "OCR never attempted" — those rows omit the key
    entirely (matching pre-stage-1 entries), so the Dream backfill can still
    detect them via ``"ocr_text" not in entry``. A non-NULL value (including
    "") means a rich-description pass has run and is surfaced as the key.
    """
    entry: dict[str, Any] = {
        "file": row["file"],
        "description": row["description"],
        "usage_hint": row["usage_hint"],
        "source": row["source"],
        "send_count": int(row["send_count"]),
        "last_sent": row["last_sent"],
        "created_at": row["created_at"],
    }
    if row["ocr_text"] is not None:
        entry["ocr_text"] = row["ocr_text"]
    return entry


class StickerStore:
    """Persistent sticker library backed by a directory and a SQLite index."""

    def __init__(
        self,
        storage_dir: str,
        max_count: int = 300,
        *,
        prompt_view_max: int = 100,
        prompt_view_recent_slice: int = 20,
    ) -> None:
        self._storage_dir = Path(storage_dir)
        self._max_count = max_count
        self._prompt_view_max = max(1, int(prompt_view_max))
        self._prompt_view_recent_slice = max(0, int(prompt_view_recent_slice))
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        # add() runs inside asyncio.to_thread workers (silent sticker learning),
        # while reads/updates run on the event loop thread. Allow cross-thread
        # use and serialize every DB + mirror access with a re-entrant lock.
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            str(self._storage_dir / _DB_FILE),
            check_same_thread=False,
        )
        self._db.row_factory = sqlite3.Row
        self._init_schema()
        self._migrate_legacy_index()
        # In-memory mirror of the table (library is small, <= max_count).
        self._index: dict[str, Any] = self._load_index()
        # Lazy BM25 index over rich descriptions; rebuilt on demand when dirty.
        self._retriever = KeywordBM25Retriever()
        self._search_dirty = True
        # Cached prompt-view member set (sticker_ids). Recomputed only when the
        # library changes (add/remove/evict), NOT on every send — so send_count
        # bursts don't churn the system-prompt cache breakpoint.
        self._view_members: list[str] | None = None

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

    @property
    def count(self) -> int:
        """Return the current number of stickers in the library."""
        with self._lock:
            return len(self._index)

    def close(self) -> None:
        """Checkpoint the WAL and close the SQLite connection."""
        close_with_checkpoint_sync(self._db, name="stickers")

    # ------------------------------------------------------------------
    # Schema / migration / load
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute(_CREATE_STICKERS)
        self._db.commit()

    def _migrate_legacy_index(self) -> None:
        """One-time import of a legacy index.json into SQLite.

        Idempotent: keyed off an empty table, not the presence of index.json,
        so re-runs after migration are no-ops. The JSON file is left in place
        as a frozen rollback snapshot (never rewritten after migration).
        """
        row = self._db.execute("SELECT COUNT(*) AS n FROM stickers").fetchone()
        if row is not None and int(row["n"]) > 0:
            return  # table already populated
        legacy = self._storage_dir / _INDEX_FILE
        if not legacy.exists():
            return
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            stickers = dict(data.get("stickers", {}))
        except (json.JSONDecodeError, OSError):
            return
        rows = [
            (
                sticker_id,
                entry.get("file", ""),
                entry.get("description", ""),
                entry.get("usage_hint", ""),
                # Preserve the "OCR never attempted" signal: a pre-stage-1 entry
                # has no ocr_text key -> store NULL so the Dream backfill still
                # sees it as pending. An explicit value (incl. "") is kept.
                (str(entry["ocr_text"]) if entry.get("ocr_text") is not None else "")
                if "ocr_text" in entry
                else None,
                entry.get("source", "auto"),
                int(entry.get("send_count", 0) or 0),
                entry.get("last_sent"),
                entry.get("created_at") or datetime.now(UTC).isoformat(),
            )
            for sticker_id, entry in stickers.items()
            if isinstance(entry, dict)
        ]
        if not rows:
            return
        with self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO stickers "
                "(sticker_id, file, description, usage_hint, ocr_text, source, "
                "send_count, last_sent, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def _load_index(self) -> dict[str, Any]:
        """Load the full table into the in-memory mirror."""
        return {
            row["sticker_id"]: _row_to_entry(row)
            for row in self._db.execute("SELECT * FROM stickers ORDER BY created_at, sticker_id")
        }

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def list_all(self) -> dict[str, Any]:
        """Return a copy of all sticker entries."""
        with self._lock:
            return {sticker_id: dict(entry) for sticker_id, entry in self._index.items()}

    def get(self, sticker_id: str) -> dict[str, Any] | None:
        """Return entry for the given sticker_id, or None if not found."""
        with self._lock:
            return self._index.get(sticker_id)

    def resolve_path(self, sticker_id: str) -> Path | None:
        """Return the absolute file path for the sticker, or None if not found."""
        with self._lock:
            entry = self._index.get(sticker_id)
        if entry is None:
            return None
        return self._storage_dir / entry["file"]

    def lookup_by_hash(self, image_data: bytes) -> str | None:
        """Return sticker_id if image_data matches an existing sticker, else None."""
        sticker_id = "stk_" + _compute_hash(image_data)
        with self._lock:
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
        ocr_text: str = "",
    ) -> tuple[str, bool]:
        """Add a sticker to the store.

        Rejects GIFs and unknown formats.
        Deduplicates by content hash.

        Args:
            ocr_text: text extracted from the image (OCR), empty if none.

        Returns:
            (sticker_id, is_new) — is_new=False means it already existed.
        """
        ext = _detect_format(image_data)  # raises ValueError for GIF / unknown
        if ext == "jpg":
            # Defense in depth: never store a naked JPEG (no JFIF APP0), which
            # QQ's rich-media upload rejects. Normalize before hashing so the
            # sticker_id reflects the actually-sendable bytes.
            image_data = ensure_jfif_app0(image_data)
        hash_prefix = _compute_hash(image_data)
        sticker_id = f"stk_{hash_prefix}"

        with self._lock:
            # Dedup: already known
            if sticker_id in self._index:
                return (sticker_id, False)

            # Capacity guard: enforce max_count by evicting the least-valuable
            # auto-captured stickers (lowest send_count, then oldest).  Protected
            # sources (migrated/admin/manual) are never auto-evicted.
            if self._max_count > 0 and len(self._index) >= self._max_count:
                self._evict_for_capacity_locked(len(self._index) - self._max_count + 1)

            filename = f"{sticker_id}.{ext}"
            file_path = self._storage_dir / filename
            file_path.write_bytes(image_data)

            now_iso = datetime.now(UTC).isoformat()
            entry = {
                "file": filename,
                "description": description,
                "usage_hint": usage_hint,
                "ocr_text": ocr_text,
                "source": source,
                "send_count": 0,
                "last_sent": None,
                "created_at": now_iso,
            }
            with self._db:
                self._db.execute(
                    "INSERT INTO stickers "
                    "(sticker_id, file, description, usage_hint, ocr_text, source, "
                    "send_count, last_sent, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (sticker_id, filename, description, usage_hint, ocr_text, source, 0, None, now_iso),
                )
            self._index[sticker_id] = entry
            self._search_dirty = True
            self._view_members = None
        return (sticker_id, True)

    def remove(self, sticker_id: str) -> bool:
        """Remove sticker from index and delete its file.

        Returns True if the sticker was found and removed, False otherwise.
        """
        with self._lock:
            entry = self._index.pop(sticker_id, None)
            if entry is None:
                return False
            with self._db:
                self._db.execute("DELETE FROM stickers WHERE sticker_id = ?", (sticker_id,))
            self._search_dirty = True
            self._view_members = None

        file_path = self._storage_dir / entry["file"]
        if file_path.exists():
            file_path.unlink()
        return True

    def renormalize_naked_jpegs(self, *, dry_run: bool = False) -> list[tuple[str, str]]:
        """Repair stored "naked" JPEGs (no JFIF APP0) that QQ rejects on send.

        Rewrites each affected file with a standard JFIF APP0 segment and
        re-keys its row to the new content hash (so a future capture of the same
        image dedupes correctly via ``add``, which now normalizes before
        hashing). All metadata (description/usage_hint/ocr_text/source/
        send_count/last_sent/created_at) is preserved. Returns ``(old_id,
        new_id)`` pairs that were (or, when ``dry_run``, would be) repaired.
        """
        repaired: list[tuple[str, str]] = []
        with self._lock:
            for old_id, entry in list(self._index.items()):
                filename = str(entry.get("file") or "")
                if not filename.endswith(".jpg"):
                    continue
                path = self._storage_dir / filename
                if not path.exists():
                    continue
                data = path.read_bytes()
                fixed = ensure_jfif_app0(data)
                if fixed == data:
                    continue  # already standard / not naked
                new_id = f"stk_{_compute_hash(fixed)}"
                repaired.append((old_id, new_id))
                if dry_run:
                    continue
                new_file = f"{new_id}.jpg"
                (self._storage_dir / new_file).write_bytes(fixed)
                new_entry = dict(entry)
                new_entry["file"] = new_file
                with self._db:
                    if new_id == old_id:
                        self._db.execute(
                            "UPDATE stickers SET file = ? WHERE sticker_id = ?",
                            (new_file, old_id),
                        )
                    else:
                        self._db.execute(
                            "UPDATE stickers SET sticker_id = ?, file = ? WHERE sticker_id = ?",
                            (new_id, new_file, old_id),
                        )
                self._index.pop(old_id, None)
                self._index[new_id] = new_entry
                if filename != new_file:
                    old_path = self._storage_dir / filename
                    if old_path.exists():
                        old_path.unlink()
            if not dry_run and repaired:
                self._search_dirty = True
                self._view_members = None
        return repaired

    @staticmethod
    def _is_evictable(source: str) -> bool:
        return any(str(source or "").startswith(p) for p in _EVICTABLE_SOURCE_PREFIXES)

    def _eviction_order_locked(self) -> list[str]:
        """Return evictable sticker_ids worst-first (lowest send_count, oldest).

        Caller must hold ``self._lock``.  Only auto-captured (``stolen_silent*``)
        stickers are returned; protected sources are excluded.
        """
        candidates = [
            (sid, entry)
            for sid, entry in self._index.items()
            if self._is_evictable(entry.get("source", ""))
        ]
        candidates.sort(
            key=lambda kv: (
                int(kv[1].get("send_count", 0) or 0),
                str(kv[1].get("created_at", "") or ""),
            )
        )
        return [sid for sid, _ in candidates]

    def _evict_for_capacity_locked(self, n: int) -> int:
        """Evict up to *n* least-valuable auto-captured stickers. Returns count removed.

        Caller must hold ``self._lock``.  Removes from DB + mirror + disk inline.
        If no evictable candidates exist (library full of protected stickers),
        removes nothing — the soft cap does not force-delete protected content.
        """
        if n <= 0:
            return 0
        victims = self._eviction_order_locked()[:n]
        removed = 0
        for sid in victims:
            entry = self._index.pop(sid, None)
            if entry is None:
                continue
            with self._db:
                self._db.execute("DELETE FROM stickers WHERE sticker_id = ?", (sid,))
            file_path = self._storage_dir / entry["file"]
            if file_path.exists():
                file_path.unlink()
            removed += 1
        if removed:
            self._search_dirty = True
            self._view_members = None
        return removed

    def eviction_candidates(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return up to *limit* auto-captured stickers that never got sent.

        Deterministic quality-driven cleanup feed for the Dream agent: only
        ``stolen_silent*`` sources with ``send_count == 0``, oldest first.  Each
        item carries id/description/usage_hint/created_at so Dream can rescue
        genuinely valuable ones while deleting the rest.
        """
        with self._lock:
            rows = [
                {
                    "id": sid,
                    "description": entry.get("description", ""),
                    "usage_hint": entry.get("usage_hint", ""),
                    "created_at": entry.get("created_at", ""),
                    "source": entry.get("source", ""),
                }
                for sid, entry in self._index.items()
                if self._is_evictable(entry.get("source", ""))
                and int(entry.get("send_count", 0) or 0) == 0
            ]
        rows.sort(key=lambda r: str(r.get("created_at", "") or ""))
        return rows[: max(0, limit)]

    def update(
        self,
        sticker_id: str,
        description: str | None = None,
        usage_hint: str | None = None,
        ocr_text: str | None = None,
    ) -> bool:
        """Update description, usage_hint, and/or ocr_text for a sticker.

        Returns True if the sticker was found and updated, False otherwise.
        """
        with self._lock:
            entry = self._index.get(sticker_id)
            if entry is None:
                return False
            sets: list[str] = []
            params: list[Any] = []
            if description is not None:
                entry["description"] = description
                sets.append("description = ?")
                params.append(description)
            if usage_hint is not None:
                entry["usage_hint"] = usage_hint
                sets.append("usage_hint = ?")
                params.append(usage_hint)
            if ocr_text is not None:
                entry["ocr_text"] = ocr_text
                sets.append("ocr_text = ?")
                params.append(ocr_text)
            if sets:
                params.append(sticker_id)
                with self._db:
                    self._db.execute(
                        f"UPDATE stickers SET {', '.join(sets)} WHERE sticker_id = ?",
                        params,
                    )
                self._search_dirty = True
        return True

    def record_send(self, sticker_id: str) -> None:
        """Increment send_count and update last_sent timestamp."""
        with self._lock:
            entry = self._index.get(sticker_id)
            if entry is None:
                return
            new_count = entry.get("send_count", 0) + 1
            now_iso = datetime.now(UTC).isoformat()
            entry["send_count"] = new_count
            entry["last_sent"] = now_iso
            with self._db:
                self._db.execute(
                    "UPDATE stickers SET send_count = ?, last_sent = ? WHERE sticker_id = ?",
                    (new_count, now_iso, sticker_id),
                )

    # ------------------------------------------------------------------
    # Intent search (BM25 over rich descriptions)
    # ------------------------------------------------------------------

    def _ensure_search_index(self) -> None:
        """Rebuild the BM25 index from the in-memory mirror if dirty.

        The library is small (<= max_count), so a full rebuild is cheap; we
        only pay it when the store has been mutated since the last search.
        """
        if not self._search_dirty:
            return
        chunks: dict[str, KnowledgeChunk] = {}
        for sticker_id, entry in self._index.items():
            description = str(entry.get("description", "") or "")
            usage_hint = str(entry.get("usage_hint", "") or "")
            ocr_text = str(entry.get("ocr_text", "") or "")
            content = "\n".join(part for part in (usage_hint, ocr_text) if part)
            chunks[sticker_id] = KnowledgeChunk(
                chunk_id=sticker_id,
                title=description,
                content=content,
                source="sticker",
                source_path="",
                source_hash="",
            )
        self._retriever.rebuild(chunks)
        self._search_dirty = False

    def search_by_intent(self, query: str, top_k: int = 5) -> list[str]:
        """Return up to ``top_k`` sticker_ids best matching an intent query.

        Scores each sticker's rich description (description + usage_hint +
        ocr_text) against the query with BM25. Returns [] for an empty query,
        an empty library, or when nothing matches.
        """
        if not query or not query.strip():
            return []
        with self._lock:
            if not self._index:
                return []
            self._ensure_search_index()
            scored = self._retriever.score(query)
        return [sticker_id for sticker_id, _score in scored[:top_k]]

    # ------------------------------------------------------------------
    # Prompt injection
    # ------------------------------------------------------------------

    def _compute_view_members_locked(self) -> list[str]:
        """Pick which sticker_ids the prompt view exposes. Caller holds the lock.

        Fills up to ``prompt_view_max`` slots, ranked by send_count desc then
        newest first (most useful + freshest win when the cap bites), while
        guaranteeing the newest ``prompt_view_recent_slice`` stickers are always
        included — a fresh-exposure window so brand-new (send_count==0) stickers
        can earn sends instead of being buried under the proven core.
        """
        items = list(self._index.items())
        if len(items) <= self._prompt_view_max:
            ordered = self._rank_locked(items)
            return [sid for sid, _ in ordered]

        def created(entry: dict[str, Any]) -> str:
            return str(entry.get("created_at", "") or "")

        # Guarantee the newest N stickers a slot.
        by_recent = sorted(items, key=lambda kv: created(kv[1]), reverse=True)
        guaranteed = [sid for sid, _ in by_recent[: self._prompt_view_recent_slice]]
        guaranteed_set = set(guaranteed)
        # Fill the remaining budget with the highest-ranked non-guaranteed stickers.
        budget = max(0, self._prompt_view_max - len(guaranteed))
        ranked = self._rank_locked([kv for kv in items if kv[0] not in guaranteed_set])
        fill = [sid for sid, _ in ranked[:budget]]
        # Keep overall order ranked (guaranteed ids re-sorted into the ranking).
        members = set(guaranteed) | set(fill)
        final = self._rank_locked([kv for kv in items if kv[0] in members])
        return [sid for sid, _ in final[: self._prompt_view_max]]

    def _rank_locked(self, items: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
        """Rank items by send_count desc, then created_at desc, then id. Lock held."""
        return sorted(
            items,
            key=lambda kv: (
                int(kv[1].get("send_count", 0) or 0),
                str(kv[1].get("created_at", "") or ""),
                kv[0],
            ),
            reverse=True,
        )

    def format_prompt_view(self) -> str:
        """Return a compact view of the sticker library for system prompt injection.

        Only exposes a bounded top-N slice (proven-useful + recently added), not
        the whole library, so the system-prompt block stays small and its cache
        breakpoint stable.  Per line: id, format, description, usage_hint,
        ocr_text — volatile fields (send_count/last_sent/created_at) are excluded
        for cache stability; the member set is cached and only recomputed when
        the library changes, so send bursts don't churn the prompt cache.
        """
        with self._lock:
            if not self._index:
                return "当前表情包库为空"
            if self._view_members is None:
                self._view_members = self._compute_view_members_locked()
            member_ids = [sid for sid in self._view_members if sid in self._index]
            entries = [(sid, self._index[sid]) for sid in member_ids]
            total = len(self._index)

        lines = ["当前表情包库："]
        if len(entries) < total:
            lines[0] = f"当前表情包库（共 {total} 张，下列为常用/最新 {len(entries)} 张）："
        for sticker_id, entry in entries:
            description = entry.get("description", "")
            usage_hint = entry.get("usage_hint", "")
            ocr_text = str(entry.get("ocr_text", "") or "").strip()
            file_name = entry.get("file", "")
            fmt = file_name.rsplit(".", 1)[-1].upper() if "." in file_name else "?"
            fmt_tag = "动图" if fmt == "GIF" else "静态"
            line = f"«表情包:{sticker_id}» [{fmt_tag}] {description} | {usage_hint}"
            if ocr_text:
                line += f" | 图上文字：{ocr_text}"
            lines.append(line)

        return "\n".join(lines)
