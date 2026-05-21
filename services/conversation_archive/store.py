"""SQLite-backed conversation archive and MessageLog compatibility layer."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

import aiosqlite
from loguru import logger

from services.storage import close_with_checkpoint, connect_sqlite

_L = logger.bind(channel="debug")

_CREATE_LEGACY_GROUP_MESSAGES = """
CREATE TABLE IF NOT EXISTS group_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id     TEXT    NOT NULL,
    role         TEXT    NOT NULL,
    speaker      TEXT,
    content_text TEXT,
    content_json TEXT,
    message_id   INTEGER,
    created_at   REAL    NOT NULL
)
"""

_CREATE_LEGACY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_gm_group_time ON group_messages(group_id, created_at)",
]

_CREATE_MESSAGES = """
CREATE TABLE IF NOT EXISTS conversation_messages (
    message_pk          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_type           TEXT    NOT NULL,
    chat_id             TEXT    NOT NULL,
    legacy_group_id     TEXT    NOT NULL,
    legacy_row_id       INTEGER,
    role                TEXT    NOT NULL,
    speaker             TEXT,
    content_text        TEXT,
    content_json        TEXT,
    platform_message_id INTEGER,
    created_at          REAL    NOT NULL,
    ingested_at         REAL    NOT NULL,
    meta_json           TEXT
)
"""

_CREATE_CURSORS = """
CREATE TABLE IF NOT EXISTS conversation_scan_cursors (
    scanner_name     TEXT    NOT NULL,
    chat_type        TEXT    NOT NULL,
    chat_id          TEXT    NOT NULL,
    scope_key        TEXT    NOT NULL DEFAULT 'chat',
    required         INTEGER NOT NULL DEFAULT 1,
    last_message_pk  INTEGER NOT NULL DEFAULT 0,
    last_created_at  REAL    NOT NULL DEFAULT 0,
    scanner_version  TEXT    NOT NULL DEFAULT '',
    params_hash      TEXT    NOT NULL DEFAULT '',
    status           TEXT    NOT NULL DEFAULT 'active',
    updated_at       REAL    NOT NULL,
    meta_json        TEXT,
    PRIMARY KEY (scanner_name, chat_type, chat_id, scope_key)
)
"""

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS conversation_scan_runs (
    run_id                     TEXT PRIMARY KEY,
    scanner_name               TEXT    NOT NULL,
    chat_type                  TEXT    NOT NULL,
    chat_id                    TEXT    NOT NULL,
    scope_key                  TEXT    NOT NULL DEFAULT 'chat',
    from_message_pk            INTEGER NOT NULL,
    to_message_pk              INTEGER NOT NULL,
    backtrack_from_message_pk  INTEGER NOT NULL,
    scanned_count              INTEGER NOT NULL DEFAULT 0,
    extracted_count            INTEGER NOT NULL DEFAULT 0,
    filtered_count             INTEGER NOT NULL DEFAULT 0,
    saved_count                INTEGER NOT NULL DEFAULT 0,
    status                     TEXT    NOT NULL,
    error                      TEXT,
    started_at                 REAL    NOT NULL,
    finished_at                REAL,
    meta_json                  TEXT
)
"""

_CREATE_POLICIES = """
CREATE TABLE IF NOT EXISTS conversation_retention_policies (
    chat_type           TEXT    NOT NULL,
    chat_id             TEXT    NOT NULL,
    cleanup_enabled     INTEGER NOT NULL DEFAULT 0,
    keep_raw_forever    INTEGER NOT NULL DEFAULT 1,
    raw_retention_days  INTEGER,
    compact_after_days  INTEGER,
    media_policy        TEXT    NOT NULL DEFAULT 'metadata_only',
    updated_at          REAL    NOT NULL,
    updated_by          TEXT,
    reason              TEXT,
    meta_json           TEXT,
    PRIMARY KEY (chat_type, chat_id)
)
"""

_CREATE_REFS = """
CREATE TABLE IF NOT EXISTS conversation_message_refs (
    ref_id          TEXT PRIMARY KEY,
    message_pk      INTEGER NOT NULL,
    ref_owner       TEXT    NOT NULL,
    ref_type        TEXT    NOT NULL,
    external_table  TEXT,
    external_id     TEXT,
    snapshot_text   TEXT,
    snapshot_json   TEXT,
    created_at      REAL    NOT NULL,
    expires_at      REAL,
    meta_json       TEXT
)
"""

_CREATE_ARCHIVE_INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS idx_conv_msg_chat_time
    ON conversation_messages(chat_type, chat_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conv_msg_chat_pk
    ON conversation_messages(chat_type, chat_id, message_pk)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conv_msg_legacy_time
    ON conversation_messages(legacy_group_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conv_msg_platform
    ON conversation_messages(chat_type, chat_id, platform_message_id)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_conv_msg_legacy_row
    ON conversation_messages(legacy_row_id)
    WHERE legacy_row_id IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conv_cursor_chat
    ON conversation_scan_cursors(chat_type, chat_id, required, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conv_runs_scanner_time
    ON conversation_scan_runs(scanner_name, started_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conv_runs_chat_time
    ON conversation_scan_runs(chat_type, chat_id, started_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conv_refs_message
    ON conversation_message_refs(message_pk)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conv_refs_owner_external
    ON conversation_message_refs(ref_owner, external_table, external_id)
    """,
]

_INSERT_LEGACY = """
INSERT INTO group_messages
    (group_id, role, speaker, content_text, content_json, message_id, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_ARCHIVE = """
INSERT OR IGNORE INTO conversation_messages
    (chat_type, chat_id, legacy_group_id, legacy_row_id, role, speaker,
     content_text, content_json, platform_message_id, created_at, ingested_at, meta_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _legacy_to_chat(group_id: str) -> tuple[str, str, str]:
    legacy_group_id = str(group_id)
    if legacy_group_id.startswith("session:"):
        return "session", legacy_group_id.removeprefix("session:"), legacy_group_id
    return "group", legacy_group_id, legacy_group_id


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _stable_ref_id(
    *,
    message_pk: int,
    ref_owner: str,
    ref_type: str,
    external_table: str | None,
    external_id: str | None,
) -> str | None:
    if not external_table or not external_id:
        return None
    raw = "\x1f".join([
        str(message_pk),
        str(ref_owner or ""),
        str(ref_type or ""),
        str(external_table or ""),
        str(external_id or ""),
    ])
    return f"ref_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:24]}"


class ConversationArchive:
    """Conversation archive with the existing MessageLog public shape.

    The compatibility methods still maintain the legacy ``group_messages``
    table. New archive tables are filled in parallel and can be backfilled
    idempotently from legacy rows.
    """

    def __init__(self, db_path: str = "storage/messages.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Connect to SQLite and create legacy + archive schema."""
        self._db = await connect_sqlite(self._db_path)
        await self._db.execute("PRAGMA journal_mode=DELETE")
        await self._db.execute("PRAGMA synchronous=FULL")
        await self._create_schema()
        await self.backfill_legacy_messages()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await close_with_checkpoint(self._db, name="conversation_archive")
            self._db = None

    async def _create_schema(self) -> None:
        if not self._db:
            raise RuntimeError("ConversationArchive is not initialized")
        await self._db.execute(_CREATE_LEGACY_GROUP_MESSAGES)
        for idx in _CREATE_LEGACY_INDEXES:
            await self._db.execute(idx)
        await self._db.execute(_CREATE_MESSAGES)
        await self._db.execute(_CREATE_CURSORS)
        await self._db.execute(_CREATE_RUNS)
        await self._db.execute(_CREATE_POLICIES)
        await self._db.execute(_CREATE_REFS)
        for idx in _CREATE_ARCHIVE_INDEXES:
            await self._db.execute(idx)
        await self._db.commit()

    async def record(
        self,
        *,
        group_id: str,
        role: str,
        speaker: str | None,
        content_text: str | None,
        content_json: str | None,
        message_id: int | None = None,
        created_at: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int | None:
        """Insert a message row into legacy and archive tables.

        Errors are logged and swallowed to preserve ``MessageLog`` behavior.
        Returns the archive ``message_pk`` when available.
        """
        if not self._db:
            _L.warning("conversation_archive not initialized, skipping record")
            return None
        created = float(created_at if created_at is not None else time.time())
        try:
            legacy_cursor = await self._db.execute(
                _INSERT_LEGACY,
                (group_id, role, speaker, content_text, content_json, message_id, created),
            )
            legacy_row_id = int(legacy_cursor.lastrowid or 0) or None
            message_pk: int | None = None
            try:
                message_pk = await self._insert_archive_message(
                    group_id=group_id,
                    role=role,
                    speaker=speaker,
                    content_text=content_text,
                    content_json=content_json,
                    message_id=message_id,
                    created_at=created,
                    legacy_row_id=legacy_row_id,
                    meta=meta,
                )
            except Exception:
                _L.exception(
                    "conversation_archive archive-side insert failed, legacy row kept"
                )
            await self._db.commit()
            return message_pk
        except Exception:
            _L.exception("conversation_archive record failed")
            try:
                await self._db.rollback()
            except Exception:
                _L.exception("conversation_archive rollback failed")
            return None

    async def _insert_archive_message(
        self,
        *,
        group_id: str,
        role: str,
        speaker: str | None,
        content_text: str | None,
        content_json: str | None,
        message_id: int | None,
        created_at: float,
        legacy_row_id: int | None,
        meta: dict[str, Any] | None = None,
    ) -> int | None:
        if not self._db:
            return None
        chat_type, chat_id, legacy_group_id = _legacy_to_chat(group_id)
        cursor = await self._db.execute(
            _INSERT_ARCHIVE,
            (
                chat_type,
                chat_id,
                legacy_group_id,
                legacy_row_id,
                role,
                speaker,
                content_text,
                content_json,
                message_id,
                created_at,
                time.time(),
                _json_dumps(meta),
            ),
        )
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        if legacy_row_id is None:
            return None
        lookup = await self._db.execute(
            "SELECT message_pk FROM conversation_messages WHERE legacy_row_id = ?",
            (legacy_row_id,),
        )
        row = await lookup.fetchone()
        return int(row["message_pk"]) if row else None

    async def backfill_legacy_messages(self, *, batch_size: int = 500) -> int:
        """Backfill archive rows from the legacy ``group_messages`` table."""
        if not self._db:
            return 0
        copied = 0
        last_id = 0
        while True:
            cursor = await self._db.execute(
                """SELECT id, group_id, role, speaker, content_text, content_json,
                          message_id, created_at
                   FROM group_messages
                   WHERE id > ?
                   ORDER BY id
                   LIMIT ?""",
                (last_id, int(batch_size)),
            )
            rows = await cursor.fetchall()
            if not rows:
                break
            for row in rows:
                last_id = int(row["id"])
                if await self._archive_row_exists(legacy_row_id=last_id):
                    continue
                before = self._db.total_changes
                await self._insert_archive_message(
                    group_id=str(row["group_id"]),
                    role=str(row["role"]),
                    speaker=row["speaker"],
                    content_text=row["content_text"],
                    content_json=row["content_json"],
                    message_id=row["message_id"],
                    created_at=float(row["created_at"]),
                    legacy_row_id=int(row["id"]),
                    meta={"backfilled_from": "group_messages"},
                )
                if self._db.total_changes > before:
                    copied += 1
            await self._db.commit()
        return copied

    async def _archive_row_exists(self, *, legacy_row_id: int) -> bool:
        if not self._db:
            return False
        cursor = await self._db.execute(
            "SELECT 1 FROM conversation_messages WHERE legacy_row_id = ? LIMIT 1",
            (int(legacy_row_id),),
        )
        return await cursor.fetchone() is not None

    async def query_recent(
        self,
        group_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return the most recent N messages for a group or session."""
        if not self._db:
            return []
        cursor = await self._db.execute(
            """SELECT role, speaker, content_text,
                      message_id, created_at
               FROM group_messages
               WHERE group_id = ?
               ORDER BY created_at DESC, id DESC
               LIMIT ?""",
            (group_id, int(limit)),
        )
        rows = await cursor.fetchall()
        return [_row_dict(row) for row in list(rows)[::-1]]

    async def list_group_ids(self) -> list[str]:
        """Return distinct group IDs seen in the log, excluding private sessions."""
        if not self._db:
            return []
        cursor = await self._db.execute(
            """SELECT DISTINCT group_id
               FROM group_messages
               WHERE group_id NOT LIKE 'session:%'
               ORDER BY group_id"""
        )
        rows = await cursor.fetchall()
        return [str(row["group_id"]) for row in rows]

    async def group_activity_summary(
        self,
        *,
        since: float | None = None,
    ) -> dict[str, dict[str, float]]:
        """Aggregate per-group activity stats for admin dashboards.

        Returns a mapping ``{group_id: {"last_at": float, "count_window": int,
        "user_count_window": int}}`` where ``count_window`` / ``user_count_window``
        only include rows newer than *since* (a unix timestamp). When *since* is
        ``None`` the windowed counters degrade to all-time counts.
        """
        if not self._db:
            return {}
        threshold = float(since) if since is not None else 0.0
        cursor = await self._db.execute(
            """SELECT group_id,
                      MAX(created_at) AS last_at,
                      SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS count_window,
                      SUM(CASE WHEN created_at >= ? AND role = 'user' THEN 1 ELSE 0 END)
                          AS user_count_window
               FROM group_messages
               WHERE group_id NOT LIKE 'session:%'
               GROUP BY group_id""",
            (threshold, threshold),
        )
        rows = await cursor.fetchall()
        summary: dict[str, dict[str, float]] = {}
        for row in rows:
            gid = str(row["group_id"])
            summary[gid] = {
                "last_at": float(row["last_at"] or 0.0),
                "count_window": int(row["count_window"] or 0),
                "user_count_window": int(row["user_count_window"] or 0),
            }
        return summary

    async def record_session_msg(
        self,
        session_id: str,
        role: str,
        content_text: str,
    ) -> int | None:
        """Record a private-chat message for persistence across restarts."""
        return await self.record(
            group_id=f"session:{session_id}",
            role=role,
            speaker=None,
            content_text=content_text[:2000] if content_text else None,
            content_json=None,
            message_id=None,
        )

    async def query_for_compact(
        self,
        group_id: str,
        *,
        before: float,
    ) -> list[dict[str, Any]]:
        """Return messages for compaction: all rows up to *before* timestamp."""
        if not self._db:
            return []
        cursor = await self._db.execute(
            """SELECT role, speaker, content_text, content_json, message_id, created_at
               FROM group_messages
               WHERE group_id = ? AND created_at <= ?
               ORDER BY created_at ASC, id ASC""",
            (group_id, float(before)),
        )
        rows = await cursor.fetchall()
        return [_row_dict(row) for row in rows]

    async def max_message_pk(self, *, chat_type: str, chat_id: str) -> int:
        if not self._db:
            return 0
        cursor = await self._db.execute(
            """SELECT COALESCE(MAX(message_pk), 0) AS max_pk
               FROM conversation_messages
               WHERE chat_type = ? AND chat_id = ?""",
            (chat_type, chat_id),
        )
        row = await cursor.fetchone()
        return int(row["max_pk"] or 0) if row else 0

    async def count_messages_after_pk(
        self,
        *,
        chat_type: str,
        chat_id: str,
        after_message_pk: int,
    ) -> dict[str, int]:
        if not self._db:
            return {"raw": 0, "text": 0}
        cursor = await self._db.execute(
            """SELECT COUNT(*) AS raw_count,
                      COALESCE(SUM(
                          CASE
                              WHEN role = 'user' AND length(trim(content_text)) > 0 THEN 1
                              ELSE 0
                          END
                      ), 0) AS text_count
               FROM conversation_messages
               WHERE chat_type = ? AND chat_id = ? AND message_pk > ?""",
            (chat_type, chat_id, int(after_message_pk)),
        )
        row = await cursor.fetchone()
        if not row:
            return {"raw": 0, "text": 0}
        return {"raw": int(row["raw_count"] or 0), "text": int(row["text_count"] or 0)}

    async def list_messages_by_pk_range(
        self,
        *,
        chat_type: str,
        chat_id: str,
        from_message_pk: int,
        to_message_pk: int,
    ) -> list[dict[str, Any]]:
        if not self._db:
            return []
        cursor = await self._db.execute(
            """SELECT message_pk, chat_type, chat_id, legacy_group_id, role, speaker,
                      content_text, content_json, platform_message_id AS message_id,
                      created_at, ingested_at, meta_json
               FROM conversation_messages
               WHERE chat_type = ? AND chat_id = ?
                 AND message_pk > ? AND message_pk <= ?
               ORDER BY message_pk ASC""",
            (chat_type, chat_id, int(from_message_pk), int(to_message_pk)),
        )
        return [_row_dict(row) for row in await cursor.fetchall()]

    async def list_messages_after_pk(
        self,
        *,
        chat_type: str,
        chat_id: str,
        after_message_pk: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self._db:
            return []
        cursor = await self._db.execute(
            """SELECT message_pk, chat_type, chat_id, legacy_group_id, role, speaker,
                      content_text, content_json, platform_message_id AS message_id,
                      created_at, ingested_at, meta_json
               FROM conversation_messages
               WHERE chat_type = ? AND chat_id = ? AND message_pk > ?
               ORDER BY message_pk ASC
               LIMIT ?""",
            (chat_type, chat_id, int(after_message_pk), max(0, int(limit))),
        )
        return [_row_dict(row) for row in await cursor.fetchall()]

    async def list_recent_archive_messages(
        self,
        *,
        chat_type: str,
        chat_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self._db:
            return []
        cursor = await self._db.execute(
            """SELECT message_pk, chat_type, chat_id, legacy_group_id, role, speaker,
                      content_text, content_json, platform_message_id AS message_id,
                      created_at, ingested_at, meta_json
               FROM conversation_messages
               WHERE chat_type = ? AND chat_id = ?
               ORDER BY message_pk DESC
               LIMIT ?""",
            (chat_type, chat_id, max(0, int(limit))),
        )
        rows = [_row_dict(row) for row in await cursor.fetchall()]
        return list(reversed(rows))

    async def read_scan_batch(
        self,
        *,
        scanner_name: str,
        group_id: str,
        limit: int,
        scope_key: str = "chat",
        scanner_version: str = "",
        params_hash: str = "",
        required: bool = True,
        backtrack_window: int = 50,
        bootstrap_to_recent: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read one archive-backed scan batch without advancing the cursor.

        Compatibility reads still use ``group_messages``. This method is only
        for incremental scanners such as slang/style extraction. The first scan
        bootstraps to the most recent ``limit`` rows so enabling archive cursors
        does not unexpectedly replay the full history.
        """
        if not self._db:
            return await self._legacy_scan_batch(
                scanner_name=scanner_name,
                group_id=group_id,
                limit=limit,
                reason="archive_not_initialized",
            )
        chat_type, chat_id, legacy_group_id = _legacy_to_chat(group_id)
        safe_limit = max(0, int(limit))
        cursor = await self.get_cursor(
            scanner_name=scanner_name,
            chat_type=chat_type,
            chat_id=chat_id,
            scope_key=scope_key,
        )
        max_pk = await self.max_message_pk(chat_type=chat_type, chat_id=chat_id)
        cursor_status = str(cursor["status"]) if cursor else "active"
        needs_rescan = False
        if cursor:
            version_changed = str(cursor["scanner_version"] or "") != scanner_version
            params_changed = str(cursor["params_hash"] or "") != params_hash
            needs_rescan = version_changed or params_changed
            if needs_rescan and cursor_status != "needs_rescan":
                await self.upsert_cursor(
                    scanner_name=scanner_name,
                    chat_type=chat_type,
                    chat_id=chat_id,
                    scope_key=scope_key,
                    required=required,
                    last_message_pk=int(cursor["last_message_pk"] or 0),
                    last_created_at=float(cursor["last_created_at"] or 0),
                    scanner_version=str(cursor["scanner_version"] or ""),
                    params_hash=str(cursor["params_hash"] or ""),
                    status="needs_rescan",
                    meta={"requested_version": scanner_version, "requested_params_hash": params_hash},
                )
                cursor_status = "needs_rescan"
        if needs_rescan or cursor_status != "active":
            return await self._legacy_scan_batch(
                scanner_name=scanner_name,
                group_id=group_id,
                limit=limit,
                reason="cursor_needs_rescan",
                cursor_status=cursor_status,
                needs_rescan=needs_rescan,
            )

        if cursor:
            from_pk = int(cursor["last_message_pk"] or 0)
            from_created_at = float(cursor["last_created_at"] or 0)
            rows = await self.list_messages_after_pk(
                chat_type=chat_type,
                chat_id=chat_id,
                after_message_pk=from_pk,
                limit=safe_limit,
            )
            to_pk = self._last_message_pk(rows, fallback=from_pk)
        else:
            from_created_at = 0.0
            if bootstrap_to_recent and safe_limit > 0:
                rows = await self.list_recent_archive_messages(
                    chat_type=chat_type,
                    chat_id=chat_id,
                    limit=safe_limit,
                )
                from_pk = max(0, self._first_message_pk(rows, fallback=max_pk + 1) - 1)
                to_pk = self._last_message_pk(rows, fallback=max_pk)
            else:
                from_pk = 0
                rows = await self.list_messages_after_pk(
                    chat_type=chat_type,
                    chat_id=chat_id,
                    after_message_pk=from_pk,
                    limit=safe_limit,
                )
                to_pk = self._last_message_pk(rows, fallback=max_pk)
        backtrack_from_pk = max(0, from_pk - max(0, int(backtrack_window)))
        to_created_at = self._last_created_at(rows, fallback=from_created_at)
        run_id = await self.start_scan_run(
            scanner_name=scanner_name,
            chat_type=chat_type,
            chat_id=chat_id,
            scope_key=scope_key,
            from_message_pk=from_pk,
            to_message_pk=to_pk,
            backtrack_from_message_pk=backtrack_from_pk,
            meta={
                "source": "archive",
                "legacy_group_id": legacy_group_id,
                "limit": safe_limit,
                "bootstrap_to_recent": bootstrap_to_recent,
                **(meta or {}),
            },
        )
        return {
            "source": "archive",
            "scanner_name": scanner_name,
            "group_id": legacy_group_id,
            "chat_type": chat_type,
            "chat_id": chat_id,
            "scope_key": scope_key,
            "rows": rows,
            "run_id": run_id,
            "from_message_pk": from_pk,
            "to_message_pk": to_pk,
            "backtrack_from_message_pk": backtrack_from_pk,
            "last_created_at": to_created_at,
            "scanner_version": scanner_version,
            "params_hash": params_hash,
            "required": required,
            "cursor_status": cursor_status,
            "needs_rescan": False,
            "can_advance": True,
        }

    async def finish_scan_batch(
        self,
        batch: dict[str, Any],
        *,
        status: str,
        scanned_count: int = 0,
        extracted_count: int = 0,
        filtered_count: int = 0,
        saved_count: int = 0,
        error: str | None = None,
        advance_cursor: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Finish a batch created by :meth:`read_scan_batch`."""
        if batch.get("source") != "archive":
            return
        run_id = str(batch.get("run_id") or "")
        if run_id:
            await self.finish_scan_run(
                run_id,
                status=status,
                scanned_count=scanned_count,
                extracted_count=extracted_count,
                filtered_count=filtered_count,
                saved_count=saved_count,
                error=error,
                meta=meta,
            )
        if status != "success" or not advance_cursor or not batch.get("can_advance"):
            return
        await self.upsert_cursor(
            scanner_name=str(batch["scanner_name"]),
            chat_type=str(batch["chat_type"]),
            chat_id=str(batch["chat_id"]),
            scope_key=str(batch.get("scope_key") or "chat"),
            required=bool(batch.get("required", True)),
            last_message_pk=int(batch.get("to_message_pk") or 0),
            last_created_at=float(batch.get("last_created_at") or 0),
            scanner_version=str(batch.get("scanner_version") or ""),
            params_hash=str(batch.get("params_hash") or ""),
            status="active",
            meta={"last_run_id": run_id, **(meta or {})},
        )

    async def _legacy_scan_batch(
        self,
        *,
        scanner_name: str,
        group_id: str,
        limit: int,
        reason: str,
        cursor_status: str = "fallback",
        needs_rescan: bool = False,
    ) -> dict[str, Any]:
        rows = await self.query_recent(group_id, limit=limit)
        chat_type, chat_id, legacy_group_id = _legacy_to_chat(group_id)
        run_id: str | None = None
        if self._db and reason == "cursor_needs_rescan":
            run_id = await self.start_scan_run(
                scanner_name=scanner_name,
                chat_type=chat_type,
                chat_id=chat_id,
                scope_key="chat",
                from_message_pk=0,
                to_message_pk=0,
                backtrack_from_message_pk=0,
                meta={
                    "source": "legacy_fallback",
                    "fallback_reason": reason,
                    "cursor_status": cursor_status,
                    "needs_rescan": bool(needs_rescan),
                    "can_advance": False,
                    "legacy_group_id": legacy_group_id,
                    "limit": int(limit),
                },
            )
            await self.finish_scan_run(
                run_id,
                status="legacy_fallback",
                scanned_count=len(rows),
                meta={
                    "source": "legacy_fallback",
                    "fallback_reason": reason,
                    "cursor_status": cursor_status,
                    "needs_rescan": bool(needs_rescan),
                    "can_advance": False,
                },
            )
        return {
            "source": "legacy_fallback",
            "fallback_reason": reason,
            "scanner_name": scanner_name,
            "group_id": legacy_group_id,
            "chat_type": chat_type,
            "chat_id": chat_id,
            "scope_key": "chat",
            "rows": rows,
            "run_id": run_id,
            "from_message_pk": 0,
            "to_message_pk": 0,
            "backtrack_from_message_pk": 0,
            "last_created_at": self._last_created_at(rows, fallback=0.0),
            "scanner_version": "",
            "params_hash": "",
            "required": True,
            "cursor_status": cursor_status,
            "needs_rescan": needs_rescan,
            "can_advance": False,
        }

    @staticmethod
    def _last_created_at(rows: Iterable[dict[str, Any]], *, fallback: float = 0.0) -> float:
        latest = fallback
        for row in rows:
            try:
                latest = max(latest, float(row.get("created_at") or 0))
            except (TypeError, ValueError):
                continue
        return latest

    @staticmethod
    def _first_message_pk(rows: Sequence[dict[str, Any]], *, fallback: int = 0) -> int:
        if not rows:
            return fallback
        try:
            return int(rows[0].get("message_pk") or fallback)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _last_message_pk(rows: Sequence[dict[str, Any]], *, fallback: int = 0) -> int:
        if not rows:
            return fallback
        try:
            return int(rows[-1].get("message_pk") or fallback)
        except (TypeError, ValueError):
            return fallback

    async def get_cursor(
        self,
        *,
        scanner_name: str,
        chat_type: str,
        chat_id: str,
        scope_key: str = "chat",
    ) -> dict[str, Any] | None:
        if not self._db:
            return None
        cursor = await self._db.execute(
            """SELECT scanner_name, chat_type, chat_id, scope_key, required,
                      last_message_pk, last_created_at, scanner_version,
                      params_hash, status, updated_at, meta_json
               FROM conversation_scan_cursors
               WHERE scanner_name = ? AND chat_type = ? AND chat_id = ? AND scope_key = ?""",
            (scanner_name, chat_type, chat_id, scope_key),
        )
        row = await cursor.fetchone()
        return _row_dict(row) if row else None

    async def upsert_cursor(
        self,
        *,
        scanner_name: str,
        chat_type: str,
        chat_id: str,
        scope_key: str = "chat",
        required: bool = True,
        last_message_pk: int,
        last_created_at: float = 0,
        scanner_version: str = "",
        params_hash: str = "",
        status: str = "active",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._db:
            raise RuntimeError("ConversationArchive is not initialized")
        await self._db.execute(
            """INSERT INTO conversation_scan_cursors
                   (scanner_name, chat_type, chat_id, scope_key, required,
                    last_message_pk, last_created_at, scanner_version, params_hash,
                    status, updated_at, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(scanner_name, chat_type, chat_id, scope_key) DO UPDATE SET
                    required = excluded.required,
                    last_message_pk = excluded.last_message_pk,
                    last_created_at = excluded.last_created_at,
                    scanner_version = excluded.scanner_version,
                    params_hash = excluded.params_hash,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    meta_json = excluded.meta_json""",
            (
                scanner_name,
                chat_type,
                chat_id,
                scope_key,
                1 if required else 0,
                int(last_message_pk),
                float(last_created_at),
                scanner_version,
                params_hash,
                status,
                time.time(),
                _json_dumps(meta),
            ),
        )
        await self._db.commit()
        row = await self.get_cursor(
            scanner_name=scanner_name,
            chat_type=chat_type,
            chat_id=chat_id,
            scope_key=scope_key,
        )
        if row is None:
            raise RuntimeError("Failed to upsert conversation scan cursor")
        return row

    async def prepare_scan_window(
        self,
        *,
        scanner_name: str,
        chat_type: str,
        chat_id: str,
        scope_key: str = "chat",
        scanner_version: str = "",
        params_hash: str = "",
        required: bool = True,
        backtrack_window: int = 50,
    ) -> dict[str, Any]:
        """Resolve a bounded scan window without advancing the cursor."""
        cursor = await self.get_cursor(
            scanner_name=scanner_name,
            chat_type=chat_type,
            chat_id=chat_id,
            scope_key=scope_key,
        )
        last_pk = int(cursor["last_message_pk"]) if cursor else 0
        status = str(cursor["status"]) if cursor else "active"
        needs_rescan = False
        if cursor:
            version_changed = str(cursor["scanner_version"] or "") != scanner_version
            params_changed = str(cursor["params_hash"] or "") != params_hash
            needs_rescan = version_changed or params_changed
            if needs_rescan and status != "needs_rescan":
                await self.upsert_cursor(
                    scanner_name=scanner_name,
                    chat_type=chat_type,
                    chat_id=chat_id,
                    scope_key=scope_key,
                    required=required,
                    last_message_pk=last_pk,
                    last_created_at=float(cursor["last_created_at"] or 0),
                    scanner_version=str(cursor["scanner_version"] or ""),
                    params_hash=str(cursor["params_hash"] or ""),
                    status="needs_rescan",
                    meta={"requested_version": scanner_version, "requested_params_hash": params_hash},
                )
                status = "needs_rescan"
        to_pk = await self.max_message_pk(chat_type=chat_type, chat_id=chat_id)
        from_pk = max(0, last_pk - max(0, int(backtrack_window)))
        return {
            "scanner_name": scanner_name,
            "chat_type": chat_type,
            "chat_id": chat_id,
            "scope_key": scope_key,
            "from_message_pk": last_pk,
            "backtrack_from_message_pk": from_pk,
            "to_message_pk": to_pk,
            "needs_rescan": needs_rescan,
            "cursor_status": status,
            "cursor": cursor,
        }

    async def start_scan_run(
        self,
        *,
        scanner_name: str,
        chat_type: str,
        chat_id: str,
        from_message_pk: int,
        to_message_pk: int,
        backtrack_from_message_pk: int,
        scope_key: str = "chat",
        meta: dict[str, Any] | None = None,
    ) -> str:
        if not self._db:
            raise RuntimeError("ConversationArchive is not initialized")
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        await self._db.execute(
            """INSERT INTO conversation_scan_runs
                   (run_id, scanner_name, chat_type, chat_id, scope_key,
                    from_message_pk, to_message_pk, backtrack_from_message_pk,
                    status, started_at, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)""",
            (
                run_id,
                scanner_name,
                chat_type,
                chat_id,
                scope_key,
                int(from_message_pk),
                int(to_message_pk),
                int(backtrack_from_message_pk),
                time.time(),
                _json_dumps(meta),
            ),
        )
        await self._db.commit()
        return run_id

    async def finish_scan_run(
        self,
        run_id: str,
        *,
        status: str,
        scanned_count: int = 0,
        extracted_count: int = 0,
        filtered_count: int = 0,
        saved_count: int = 0,
        error: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        if not self._db:
            return
        await self._db.execute(
            """UPDATE conversation_scan_runs
               SET status = ?, scanned_count = ?, extracted_count = ?,
                   filtered_count = ?, saved_count = ?, error = ?,
                   finished_at = ?, meta_json = COALESCE(?, meta_json)
               WHERE run_id = ?""",
            (
                status,
                int(scanned_count),
                int(extracted_count),
                int(filtered_count),
                int(saved_count),
                error,
                time.time(),
                _json_dumps(meta),
                run_id,
            ),
        )
        await self._db.commit()

    async def set_retention_policy(
        self,
        *,
        chat_type: str,
        chat_id: str,
        cleanup_enabled: bool,
        keep_raw_forever: bool = True,
        raw_retention_days: int | None = None,
        compact_after_days: int | None = None,
        media_policy: str = "metadata_only",
        updated_by: str | None = None,
        reason: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._db:
            raise RuntimeError("ConversationArchive is not initialized")
        await self._db.execute(
            """INSERT INTO conversation_retention_policies
                   (chat_type, chat_id, cleanup_enabled, keep_raw_forever,
                    raw_retention_days, compact_after_days, media_policy,
                    updated_at, updated_by, reason, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_type, chat_id) DO UPDATE SET
                    cleanup_enabled = excluded.cleanup_enabled,
                    keep_raw_forever = excluded.keep_raw_forever,
                    raw_retention_days = excluded.raw_retention_days,
                    compact_after_days = excluded.compact_after_days,
                    media_policy = excluded.media_policy,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by,
                    reason = excluded.reason,
                    meta_json = excluded.meta_json""",
            (
                chat_type,
                chat_id,
                1 if cleanup_enabled else 0,
                1 if keep_raw_forever else 0,
                raw_retention_days,
                compact_after_days,
                media_policy,
                time.time(),
                updated_by,
                reason,
                _json_dumps(meta),
            ),
        )
        await self._db.commit()
        policy = await self.get_retention_policy(chat_type=chat_type, chat_id=chat_id)
        if policy is None:
            raise RuntimeError("Failed to upsert retention policy")
        return policy

    async def get_retention_policy(
        self,
        *,
        chat_type: str,
        chat_id: str,
    ) -> dict[str, Any] | None:
        if not self._db:
            return None
        cursor = await self._db.execute(
            """SELECT chat_type, chat_id, cleanup_enabled, keep_raw_forever,
                      raw_retention_days, compact_after_days, media_policy,
                      updated_at, updated_by, reason, meta_json
               FROM conversation_retention_policies
               WHERE chat_type = ? AND chat_id = ?""",
            (chat_type, chat_id),
        )
        row = await cursor.fetchone()
        return _row_dict(row) if row else None

    async def add_message_ref(
        self,
        *,
        message_pk: int,
        ref_owner: str,
        ref_type: str,
        external_table: str | None = None,
        external_id: str | None = None,
        snapshot_text: str | None = None,
        snapshot_json: str | None = None,
        expires_at: float | None = None,
        meta: dict[str, Any] | None = None,
        ref_id: str | None = None,
    ) -> str:
        if not self._db:
            raise RuntimeError("ConversationArchive is not initialized")
        resolved_ref_id = (
            ref_id
            or _stable_ref_id(
                message_pk=int(message_pk),
                ref_owner=ref_owner,
                ref_type=ref_type,
                external_table=external_table,
                external_id=external_id,
            )
            or f"ref_{uuid.uuid4().hex[:16]}"
        )
        await self._db.execute(
            """INSERT OR REPLACE INTO conversation_message_refs
                   (ref_id, message_pk, ref_owner, ref_type, external_table,
                    external_id, snapshot_text, snapshot_json, created_at,
                    expires_at, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                resolved_ref_id,
                int(message_pk),
                ref_owner,
                ref_type,
                external_table,
                external_id,
                snapshot_text,
                snapshot_json,
                time.time(),
                expires_at,
                _json_dumps(meta),
            ),
        )
        await self._db.commit()
        return resolved_ref_id

    async def find_message_pk_by_platform_message(
        self,
        *,
        chat_type: str,
        chat_id: str,
        platform_message_id: int | str | None,
    ) -> int | None:
        if not self._db or platform_message_id is None:
            return None
        try:
            message_id = int(platform_message_id)
        except (TypeError, ValueError):
            return None
        cursor = await self._db.execute(
            """SELECT message_pk
               FROM conversation_messages
               WHERE chat_type = ? AND chat_id = ? AND platform_message_id = ?
               ORDER BY message_pk DESC
               LIMIT 1""",
            (str(chat_type), str(chat_id), message_id),
        )
        row = await cursor.fetchone()
        return int(row["message_pk"]) if row else None

    async def add_message_ref_for_platform_message(
        self,
        *,
        chat_type: str,
        chat_id: str,
        platform_message_id: int | str | None,
        ref_owner: str,
        ref_type: str,
        external_table: str | None = None,
        external_id: str | None = None,
        snapshot_text: str | None = None,
        snapshot_json: str | None = None,
        expires_at: float | None = None,
        meta: dict[str, Any] | None = None,
        ref_id: str | None = None,
    ) -> str | None:
        message_pk = await self.find_message_pk_by_platform_message(
            chat_type=chat_type,
            chat_id=chat_id,
            platform_message_id=platform_message_id,
        )
        if message_pk is None:
            return None
        return await self.add_message_ref(
            message_pk=message_pk,
            ref_owner=ref_owner,
            ref_type=ref_type,
            external_table=external_table,
            external_id=external_id,
            snapshot_text=snapshot_text,
            snapshot_json=snapshot_json,
            expires_at=expires_at,
            meta=meta,
            ref_id=ref_id,
        )

    async def dry_run_cleanup(
        self,
        *,
        chat_type: str,
        chat_id: str,
        required_scanners: Sequence[str | tuple[str, str]] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Report raw rows that would be eligible for cleanup.

        This method never deletes data.
        """
        if not self._db:
            raise RuntimeError("ConversationArchive is not initialized")
        current_time = float(now if now is not None else time.time())
        policy = await self.get_retention_policy(chat_type=chat_type, chat_id=chat_id)
        blockers: list[str] = []
        if policy is None:
            blockers.append("retention_policy_missing")
            return self._cleanup_result(policy, blockers=blockers)
        if not int(policy["cleanup_enabled"] or 0):
            blockers.append("cleanup_disabled")
        if int(policy["keep_raw_forever"] or 0):
            blockers.append("keep_raw_forever")
        retention_days = policy["raw_retention_days"]
        if retention_days is None:
            blockers.append("raw_retention_days_missing")
        min_safe_pk: int | None = None
        cursor_states: list[dict[str, Any]] = []
        for item in required_scanners or ():
            if isinstance(item, tuple):
                scanner_name, scope_key = item
            else:
                scanner_name, scope_key = item, "chat"
            cursor = await self.get_cursor(
                scanner_name=scanner_name,
                chat_type=chat_type,
                chat_id=chat_id,
                scope_key=scope_key,
            )
            if cursor is None:
                blockers.append(f"missing_required_cursor:{scanner_name}:{scope_key}")
                cursor_states.append({
                    "scanner_name": scanner_name,
                    "scope_key": scope_key,
                    "status": "missing",
                })
                continue
            cursor_states.append(cursor)
            if str(cursor["status"]) != "active":
                blockers.append(
                    f"cursor_not_active:{scanner_name}:{scope_key}:{cursor['status']}"
                )
            cursor_pk = int(cursor["last_message_pk"] or 0)
            min_safe_pk = cursor_pk if min_safe_pk is None else min(min_safe_pk, cursor_pk)
        if blockers:
            return self._cleanup_result(
                policy,
                blockers=blockers,
                min_safe_pk=min_safe_pk,
                cursor_states=cursor_states,
            )
        assert retention_days is not None
        cutoff = current_time - int(retention_days) * 86400
        assert min_safe_pk is not None
        cursor = await self._db.execute(
            """SELECT message_pk, created_at
               FROM conversation_messages
               WHERE chat_type = ? AND chat_id = ?
                 AND message_pk <= ? AND created_at <= ?
               ORDER BY message_pk ASC""",
            (chat_type, chat_id, min_safe_pk, cutoff),
        )
        rows = await cursor.fetchall()
        candidate_pks = [int(row["message_pk"]) for row in rows]
        protected_pks = await self._active_ref_message_pks(candidate_pks, current_time)
        deletable_pks = [pk for pk in candidate_pks if pk not in protected_pks]
        return self._cleanup_result(
            policy,
            blockers=[],
            min_safe_pk=min_safe_pk,
            cursor_states=cursor_states,
            cutoff_created_at=cutoff,
            candidate_count=len(deletable_pks),
            protected_count=len(protected_pks),
            candidate_message_pks=deletable_pks,
            protected_message_pks=sorted(protected_pks),
        )

    def _cleanup_result(
        self,
        policy: dict[str, Any] | None,
        *,
        blockers: list[str],
        min_safe_pk: int | None = None,
        cursor_states: list[dict[str, Any]] | None = None,
        cutoff_created_at: float | None = None,
        candidate_count: int = 0,
        protected_count: int = 0,
        candidate_message_pks: list[int] | None = None,
        protected_message_pks: list[int] | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "dry_run": True,
            "blocked": bool(blockers),
            "blockers": blockers,
            "policy": policy,
            "min_safe_pk": min_safe_pk,
            "cursor_states": cursor_states or [],
            "cutoff_created_at": cutoff_created_at,
            "candidate_count": candidate_count,
            "protected_count": protected_count,
            "candidate_message_pks": candidate_message_pks or [],
            "protected_message_pks": protected_message_pks or [],
        }

    async def _active_ref_message_pks(
        self,
        message_pks: Iterable[int],
        now: float,
    ) -> set[int]:
        if not self._db:
            return set()
        pks = list(message_pks)
        if not pks:
            return set()
        protected: set[int] = set()
        for i in range(0, len(pks), 200):
            chunk = pks[i:i + 200]
            placeholders = ",".join("?" for _ in chunk)
            cursor = await self._db.execute(
                f"""SELECT DISTINCT message_pk
                    FROM conversation_message_refs
                    WHERE message_pk IN ({placeholders})
                      AND (expires_at IS NULL OR expires_at > ?)""",
                (*chunk, float(now)),
            )
            protected.update(int(row["message_pk"]) for row in await cursor.fetchall())
        return protected
