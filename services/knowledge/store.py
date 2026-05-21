"""SQLite persistence for KnowledgeService chunks and source status."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from services.knowledge.types import KnowledgeChunk, KnowledgeSourceStatus
from services.storage import close_with_checkpoint_sync

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

_CREATE_SOURCES = """\
CREATE TABLE IF NOT EXISTS knowledge_sources (
    source         TEXT PRIMARY KEY,
    path           TEXT NOT NULL,
    status         TEXT NOT NULL,
    chunk_count    INTEGER NOT NULL,
    source_hash    TEXT NOT NULL,
    skipped_reason TEXT NOT NULL,
    updated_at     TEXT NOT NULL
)"""

_CREATE_CHUNKS = """\
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id      TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT NOT NULL,
    source_path   TEXT NOT NULL,
    source_hash   TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY(source) REFERENCES knowledge_sources(source) ON DELETE CASCADE
)"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_source ON knowledge_chunks(source)",
    "CREATE INDEX IF NOT EXISTS idx_knowledge_sources_status ON knowledge_sources(status)",
]


class KnowledgeIndexStore:
    """Small sync SQLite cache for Markdown knowledge chunks."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self._db_path))
        self._db.row_factory = sqlite3.Row
        self._init_schema()

    @property
    def path(self) -> str:
        return str(self._db_path)

    def close(self) -> None:
        close_with_checkpoint_sync(self._db, name="knowledge")

    def load(self) -> tuple[dict[str, KnowledgeChunk], dict[str, KnowledgeSourceStatus]]:
        sources = {
            row["source"]: KnowledgeSourceStatus(
                source=row["source"],
                path=row["path"],
                status=row["status"],
                chunk_count=int(row["chunk_count"]),
                source_hash=row["source_hash"],
                skipped_reason=row["skipped_reason"],
            )
            for row in self._db.execute("SELECT * FROM knowledge_sources ORDER BY source")
        }
        chunks = {
            row["chunk_id"]: KnowledgeChunk(
                chunk_id=row["chunk_id"],
                title=row["title"],
                content=row["content"],
                source=row["source"],
                source_path=row["source_path"],
                source_hash=row["source_hash"],
                metadata=json.loads(row["metadata_json"] or "{}"),
            )
            for row in self._db.execute("SELECT * FROM knowledge_chunks ORDER BY chunk_id")
        }
        return chunks, sources

    def source_hashes(self) -> dict[str, str]:
        return {
            row["source"]: row["source_hash"]
            for row in self._db.execute("SELECT source, source_hash FROM knowledge_sources")
        }

    def replace_source(
        self,
        status: KnowledgeSourceStatus,
        chunks: list[KnowledgeChunk],
    ) -> None:
        now = _now_iso()
        with self._db:
            self._db.execute("DELETE FROM knowledge_chunks WHERE source = ?", (status.source,))
            self._db.execute(
                "INSERT INTO knowledge_sources "
                "(source, path, status, chunk_count, source_hash, skipped_reason, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source) DO UPDATE SET "
                "path = excluded.path, status = excluded.status, chunk_count = excluded.chunk_count, "
                "source_hash = excluded.source_hash, skipped_reason = excluded.skipped_reason, "
                "updated_at = excluded.updated_at",
                (
                    status.source,
                    status.path,
                    status.status,
                    status.chunk_count,
                    status.source_hash,
                    status.skipped_reason,
                    now,
                ),
            )
            self._db.executemany(
                "INSERT INTO knowledge_chunks "
                "(chunk_id, source, title, content, source_path, source_hash, metadata_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        chunk.chunk_id,
                        chunk.source,
                        chunk.title,
                        chunk.content,
                        chunk.source_path,
                        chunk.source_hash,
                        json.dumps(chunk.metadata, ensure_ascii=False),
                        now,
                    )
                    for chunk in chunks
                ],
            )

    def delete_sources(self, sources: set[str]) -> None:
        if not sources:
            return
        with self._db:
            self._db.executemany(
                "DELETE FROM knowledge_sources WHERE source = ?",
                [(source,) for source in sources],
            )

    def clear(self) -> None:
        """Remove every persisted source/chunk from the local index cache."""
        with self._db:
            self._db.execute("DELETE FROM knowledge_chunks")
            self._db.execute("DELETE FROM knowledge_sources")

    def _init_schema(self) -> None:
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute(_CREATE_SOURCES)
        self._db.execute(_CREATE_CHUNKS)
        for statement in _CREATE_INDEXES:
            self._db.execute(statement)
        self._db.commit()


def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")
