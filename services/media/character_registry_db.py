"""Local character registry — per-bot relation/name source of truth.

The CCIP embeddings live in the sidecar (charpack npz); this DB holds the
*semantic* layer that is per-bot and admin-editable: each character's
relation (self/friend/known), display name, and aliases. `scan_and_sync`
seeds rows from charpack manifests but never overwrites an existing row's
relation/name — so admin edits survive pack reloads.

This separation is the multi-bot seam (supervisor §A.7): embeddings shared
read-only in the sidecar, relation kept per-bot here.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import aiosqlite
from loguru import logger

from services.media.character_pack_manifest import (
    character_aliases,
    character_id,
    effective_character_relation,
    iter_manifest_characters,
)
from services.storage import close_with_checkpoint, connect_sqlite

_L = logger.bind(channel="debug")

_VALID_RELATIONS = {"self", "friend", "known"}

_CREATE_REGISTRY = """\
CREATE TABLE IF NOT EXISTS character_registry (
    character_id TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    relation     TEXT NOT NULL DEFAULT 'known',
    updated_at   REAL NOT NULL
)"""

_CREATE_PACK_META = """\
CREATE TABLE IF NOT EXISTS character_pack_meta (
    pack_name   TEXT PRIMARY KEY,
    source_hash TEXT NOT NULL,
    synced_at   REAL NOT NULL
)"""


class CharacterRegistryDB:
    """relation/name/aliases per character_id, plus pack sync bookkeeping."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        db = await connect_sqlite(self._db_path)
        await db.execute(_CREATE_REGISTRY)
        await db.execute(_CREATE_PACK_META)
        await db.commit()
        self._db = db

    async def close(self) -> None:
        if self._db is not None:
            await close_with_checkpoint(self._db, name="character_registry")
            self._db = None

    async def get(self, character_id: str) -> dict[str, object] | None:
        if self._db is None or not character_id:
            return None
        cur = await self._db.execute(
            "SELECT character_id, name, aliases_json, relation FROM character_registry "
            "WHERE character_id = ?",
            (character_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        return self._row_to_dict(row)

    async def list_all(self) -> list[dict[str, object]]:
        if self._db is None:
            return []
        cur = await self._db.execute(
            "SELECT character_id, name, aliases_json, relation FROM character_registry "
            "ORDER BY character_id"
        )
        rows = await cur.fetchall()
        await cur.close()
        return [self._row_to_dict(r) for r in rows]

    async def update(
        self,
        character_id: str,
        *,
        name: str | None = None,
        relation: str | None = None,
        aliases: list[str] | None = None,
    ) -> bool:
        """Admin edit. Returns False if the character_id doesn't exist."""
        if self._db is None or not character_id:
            return False
        existing = await self.get(character_id)
        if existing is None:
            return False
        new_name = name if name is not None else str(existing["name"])
        new_relation = relation if relation in _VALID_RELATIONS else str(existing["relation"])
        new_aliases = aliases if aliases is not None else list(existing["aliases"])  # type: ignore[arg-type]
        await self._db.execute(
            "UPDATE character_registry SET name=?, relation=?, aliases_json=?, updated_at=? "
            "WHERE character_id=?",
            (new_name, new_relation, json.dumps(new_aliases, ensure_ascii=False), time.time(), character_id),
        )
        await self._db.commit()
        return True

    async def scan_and_sync(self, packs_dir: str | Path) -> dict[str, int]:
        """Seed registry rows from charpack manifests. Never overwrites an
        existing row's relation/name (admin edits win); only inserts new ones."""
        if self._db is None:
            return {"packs": 0, "inserted": 0, "skipped": 0}
        packs_path = Path(packs_dir)
        if not packs_path.exists():
            return {"packs": 0, "inserted": 0, "skipped": 0}

        inserted = 0
        skipped = 0
        pack_count = 0
        for manifest_file in sorted(packs_path.glob("*.charpack/manifest.json")):
            try:
                raw = manifest_file.read_text(encoding="utf-8")
                manifest = json.loads(raw)
            except (OSError, json.JSONDecodeError):
                continue
            pack_count += 1
            source_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
            for item in iter_manifest_characters(manifest):
                cid = character_id(item)
                if not cid:
                    continue
                if await self.get(cid) is not None:
                    skipped += 1
                    continue
                name = str(item.get("name") or cid).strip() or cid
                relation = effective_character_relation(manifest, item)
                aliases = character_aliases(item)
                await self._db.execute(
                    "INSERT INTO character_registry "
                    "(character_id, name, aliases_json, relation, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (cid, name, json.dumps(aliases, ensure_ascii=False), relation, time.time()),
                )
                inserted += 1
            await self._db.execute(
                "INSERT INTO character_pack_meta (pack_name, source_hash, synced_at) VALUES (?, ?, ?) "
                "ON CONFLICT(pack_name) DO UPDATE SET source_hash=excluded.source_hash, synced_at=excluded.synced_at",
                (manifest_file.parent.name, source_hash, time.time()),
            )
        await self._db.commit()
        _L.info("character registry sync | packs={} inserted={} skipped={}", pack_count, inserted, skipped)
        return {"packs": pack_count, "inserted": inserted, "skipped": skipped}

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict[str, object]:
        try:
            aliases = json.loads(row["aliases_json"])
        except (json.JSONDecodeError, TypeError):
            aliases = []
        return {
            "character_id": row["character_id"],
            "name": row["name"],
            "aliases": aliases if isinstance(aliases, list) else [],
            "relation": row["relation"],
        }
