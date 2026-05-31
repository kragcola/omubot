"""Persistent SHA-256 recognition cache for character identification.

L2 cache (the sidecar's in-memory cache is L1): survives bot restarts and
saves a sidecar round-trip on repeat images. Keyed by the *full* 64-char
SHA-256 of the image bytes — deliberately not the `[:8]` truncation used by
the VL desc_cache, since at scale a 32-bit key would collide and mis-label.

aiosqlite + WAL + close_with_checkpoint, following the slang.db corruption
treatment (named volume DB, checkpoint-on-close).
"""
from __future__ import annotations

import time
from pathlib import Path

import aiosqlite
from loguru import logger

from services.storage import close_with_checkpoint, connect_sqlite

_L = logger.bind(channel="debug")

_CREATE = """\
CREATE TABLE IF NOT EXISTS image_recognition_cache (
    image_sha256   TEXT PRIMARY KEY,
    character_id   TEXT,
    character_name TEXT,
    relation       TEXT,
    source         TEXT NOT NULL DEFAULT 'ccip-sidecar',
    confidence     REAL,
    created_at     REAL NOT NULL,
    accessed_at    REAL NOT NULL
)"""

_INDEX = "CREATE INDEX IF NOT EXISTS idx_recog_accessed ON image_recognition_cache(accessed_at)"


class RecognitionCache:
    """Persistent recognition result cache, keyed by full image SHA-256."""

    def __init__(self, db_path: str | Path, *, max_entries: int = 20_000) -> None:
        self._db_path = str(db_path)
        self._max_entries = max(100, int(max_entries))
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        db = await connect_sqlite(self._db_path)
        await db.execute(_CREATE)
        await db.execute(_INDEX)
        await db.commit()
        self._db = db

    async def close(self) -> None:
        if self._db is not None:
            await close_with_checkpoint(self._db, name="recognition_cache")
            self._db = None

    async def get(self, image_sha256: str) -> dict[str, object] | None:
        if self._db is None or not image_sha256:
            return None
        cur = await self._db.execute(
            "SELECT character_id, character_name, relation, source, confidence "
            "FROM image_recognition_cache WHERE image_sha256 = ?",
            (image_sha256,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        # Touch accessed_at for LRU; best-effort, don't fail the read.
        try:
            await self._db.execute(
                "UPDATE image_recognition_cache SET accessed_at = ? WHERE image_sha256 = ?",
                (time.time(), image_sha256),
            )
            await self._db.commit()
        except aiosqlite.Error:
            pass
        return {
            "character_id": row["character_id"],
            "character_name": row["character_name"],
            "relation": row["relation"],
            "source": row["source"],
            "confidence": row["confidence"],
        }

    async def put(
        self,
        image_sha256: str,
        *,
        character_id: str | None,
        character_name: str | None,
        relation: str | None,
        source: str = "ccip-sidecar",
        confidence: float | None = None,
    ) -> None:
        if self._db is None or not image_sha256:
            return
        now = time.time()
        await self._db.execute(
            "INSERT INTO image_recognition_cache "
            "(image_sha256, character_id, character_name, relation, source, confidence, created_at, accessed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(image_sha256) DO UPDATE SET "
            "character_id=excluded.character_id, character_name=excluded.character_name, "
            "relation=excluded.relation, source=excluded.source, confidence=excluded.confidence, "
            "accessed_at=excluded.accessed_at",
            (image_sha256, character_id, character_name, relation, source, confidence, now, now),
        )
        await self._db.commit()
        await self._prune()

    async def _prune(self) -> None:
        """Drop oldest-accessed rows beyond max_entries. Cheap amortized check."""
        if self._db is None:
            return
        cur = await self._db.execute("SELECT COUNT(*) FROM image_recognition_cache")
        row = await cur.fetchone()
        await cur.close()
        count = int(row[0]) if row else 0
        if count <= self._max_entries:
            return
        await self._db.execute(
            "DELETE FROM image_recognition_cache WHERE image_sha256 IN ("
            "  SELECT image_sha256 FROM image_recognition_cache "
            "  ORDER BY accessed_at ASC LIMIT ?)",
            (count - self._max_entries,),
        )
        await self._db.commit()

    async def stats(self) -> dict[str, int]:
        if self._db is None:
            return {"total": 0, "matched": 0}
        cur = await self._db.execute(
            "SELECT COUNT(*), COUNT(character_id) FROM image_recognition_cache"
        )
        row = await cur.fetchone()
        await cur.close()
        return {"total": int(row[0]) if row else 0, "matched": int(row[1]) if row else 0}

