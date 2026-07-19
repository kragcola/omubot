"""Governed SQLite registry for scope-safe memory entity aliases."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite

from services.memory.entity_identity import parse_entity_key
from services.similarity import normalize_text_key
from services.storage import close_with_checkpoint, connect_sqlite
from services.storage.migrations import Migration, MigrationRunner
from services.storage.schema_contracts import verify_catalog_schema_async

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
_VALID_SCOPES = frozenset({"group", "user", "global"})
_VALID_STATUSES = frozenset({"active", "ambiguous", "superseded", "revoked"})

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS entity_aliases (
    alias_id       TEXT PRIMARY KEY,
    entity_key     TEXT NOT NULL,
    alias_norm     TEXT NOT NULL,
    alias_surface  TEXT NOT NULL,
    scope          TEXT NOT NULL,
    scope_id       TEXT NOT NULL DEFAULT '',
    confidence     REAL NOT NULL DEFAULT 0.5,
    source         TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active',
    valid_from     TEXT NOT NULL,
    valid_to       TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    meta_json      TEXT NOT NULL DEFAULT '{}'
)"""

_CREATE_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_entity_alias_active_scope_norm "
    "ON entity_aliases(scope, scope_id, alias_norm) "
    "WHERE status = 'active' AND valid_to = ''",
    "CREATE INDEX IF NOT EXISTS idx_entity_alias_entity "
    "ON entity_aliases(entity_key, status)",
    "CREATE INDEX IF NOT EXISTS idx_entity_alias_resolve "
    "ON entity_aliases(scope, scope_id, alias_norm, status)",
)


async def _apply_entity_aliases_v1(db: aiosqlite.Connection) -> None:
    await db.execute(_CREATE_TABLE)
    for statement in _CREATE_INDEXES:
        await db.execute(statement)


async def _verify_entity_aliases_v1(db: aiosqlite.Connection) -> bool:
    return bool(await verify_catalog_schema_async("entity_aliases", db, 1))


_ENTITY_ALIASES_V1_DDL = "\n".join((_CREATE_TABLE, *_CREATE_INDEXES))
_ENTITY_ALIASES_V1 = Migration(
    version=1,
    name="entity_aliases_baseline_v1",
    checksum="sha256:" + hashlib.sha256(_ENTITY_ALIASES_V1_DDL.encode()).hexdigest(),
    apply=_apply_entity_aliases_v1,
    verify=_verify_entity_aliases_v1,
    adopt_existing=True,
)


@dataclass(frozen=True, slots=True)
class EntityAlias:
    alias_id: str
    entity_key: str
    alias_norm: str
    alias_surface: str
    scope: str
    scope_id: str
    confidence: float
    source: str
    status: str
    valid_from: str
    valid_to: str | None
    created_at: str
    updated_at: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AliasObservationResult:
    outcome: str
    alias: EntityAlias | None
    conflicting_entity_key: str | None = None


class EntityAliasStore:
    def __init__(self, db_path: str | Path = "storage/entity_aliases.db") -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        await MigrationRunner(
            db_path=self._db_path,
            db_id="entity_aliases",
        ).ensure((_ENTITY_ALIASES_V1,))
        self._db = await connect_sqlite(self._db_path)

    async def close(self) -> None:
        if self._db is not None:
            await close_with_checkpoint(self._db, name="entity_aliases")
            self._db = None

    async def observe(
        self,
        *,
        entity_key: str,
        alias: str,
        scope: str,
        scope_id: str,
        confidence: float,
        source: str,
        observed_at: str = "",
        meta: dict[str, Any] | None = None,
    ) -> AliasObservationResult:
        identity, surface, alias_norm, clean_scope, clean_scope_id, score, clean_source, now = (
            _validate_observation(
                entity_key=entity_key,
                alias=alias,
                scope=scope,
                scope_id=scope_id,
                confidence=confidence,
                source=source,
                observed_at=observed_at,
            )
        )
        db = self._require_db()
        rows = await self._rows_for_alias(
            scope=clean_scope,
            scope_id=clean_scope_id,
            alias_norm=alias_norm,
        )
        ambiguous = [row for row in rows if row.status == "ambiguous"]
        if ambiguous:
            match = next((row for row in ambiguous if row.entity_key == identity), ambiguous[0])
            return AliasObservationResult(
                outcome="ignored_collision",
                alias=match,
                conflicting_entity_key=next(
                    (row.entity_key for row in ambiguous if row.entity_key != identity),
                    None,
                ),
            )

        active = next(
            (row for row in rows if row.status == "active" and row.valid_to is None),
            None,
        )
        alias_id = _alias_id(identity, clean_scope, clean_scope_id, alias_norm)
        if active is not None and active.entity_key == identity:
            merged_meta = _merge_meta(active.meta, meta, clean_source)
            await db.execute(
                "UPDATE entity_aliases SET alias_surface = ?, confidence = ?, source = ?, "
                "updated_at = ?, meta_json = ? WHERE alias_id = ?",
                (
                    surface,
                    max(active.confidence, score),
                    clean_source,
                    now,
                    json.dumps(merged_meta, ensure_ascii=False, sort_keys=True),
                    active.alias_id,
                ),
            )
            await db.commit()
            refreshed = await self._get_alias(active.alias_id)
            return AliasObservationResult("refreshed", refreshed)

        if active is not None and active.entity_key != identity:
            await db.execute(
                "UPDATE entity_aliases SET status = 'ambiguous', valid_to = ?, updated_at = ? "
                "WHERE alias_id = ?",
                (now, now, active.alias_id),
            )
            collision_meta = _merge_meta({}, meta, clean_source)
            await db.execute(
                "INSERT INTO entity_aliases "
                "(alias_id, entity_key, alias_norm, alias_surface, scope, scope_id, confidence, "
                "source, status, valid_from, valid_to, created_at, updated_at, meta_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ambiguous', ?, '', ?, ?, ?) "
                "ON CONFLICT(alias_id) DO UPDATE SET status = 'ambiguous', valid_to = '', "
                "updated_at = excluded.updated_at, confidence = MAX(confidence, excluded.confidence), "
                "meta_json = excluded.meta_json",
                (
                    alias_id,
                    identity,
                    alias_norm,
                    surface,
                    clean_scope,
                    clean_scope_id,
                    score,
                    clean_source,
                    now,
                    now,
                    now,
                    json.dumps(collision_meta, ensure_ascii=False, sort_keys=True),
                ),
            )
            await db.commit()
            collision = await self._get_alias(alias_id)
            return AliasObservationResult(
                "collision",
                collision,
                conflicting_entity_key=active.entity_key,
            )

        previous = next((row for row in rows if row.alias_id == alias_id), None)
        if previous is not None:
            return AliasObservationResult(
                "ignored_collision",
                previous,
                conflicting_entity_key=next(
                    (row.entity_key for row in rows if row.entity_key != identity),
                    None,
                ),
            )

        payload_meta = _merge_meta({}, meta, clean_source)
        await db.execute(
            "INSERT INTO entity_aliases "
            "(alias_id, entity_key, alias_norm, alias_surface, scope, scope_id, confidence, "
            "source, status, valid_from, valid_to, created_at, updated_at, meta_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, '', ?, ?, ?)",
            (
                alias_id,
                identity,
                alias_norm,
                surface,
                clean_scope,
                clean_scope_id,
                score,
                clean_source,
                now,
                now,
                now,
                json.dumps(payload_meta, ensure_ascii=False, sort_keys=True),
            ),
        )
        await db.commit()
        created = await self._get_alias(alias_id)
        return AliasObservationResult("created", created)

    async def resolve(
        self,
        *,
        alias: str,
        scope: str,
        scope_id: str,
        at: str = "",
    ) -> str | None:
        clean_scope, clean_scope_id = _validate_scope(scope, scope_id)
        alias_norm = _normalize_alias(alias)
        if not alias_norm:
            return None
        instant = _normalize_instant(at)
        cursor = await self._require_db().execute(
            "SELECT * FROM entity_aliases WHERE scope = ? AND scope_id = ? AND alias_norm = ? "
            "AND valid_from <= ? AND (valid_to = '' OR valid_to > ?) "
            "AND (status != 'ambiguous' OR valid_to != '')",
            (clean_scope, clean_scope_id, alias_norm, instant, instant),
        )
        rows = [_row_to_alias(row) for row in await cursor.fetchall()]
        entity_keys = {row.entity_key for row in rows}
        return next(iter(entity_keys)) if len(entity_keys) == 1 else None

    async def supersede(
        self,
        *,
        entity_key: str,
        old_alias: str,
        new_alias: str,
        scope: str,
        scope_id: str,
        confidence: float,
        source: str,
        observed_at: str = "",
    ) -> EntityAlias | None:
        identity = _validate_entity_key(entity_key)
        clean_scope, clean_scope_id = _validate_scope(scope, scope_id)
        old_norm = _normalize_alias(old_alias)
        if not old_norm:
            raise ValueError("old alias must contain a normalizable value")
        now = _normalize_instant(observed_at)
        db = self._require_db()
        cursor = await db.execute(
            "SELECT alias_id FROM entity_aliases WHERE entity_key = ? AND scope = ? "
            "AND scope_id = ? AND alias_norm = ? AND status = 'active' AND valid_to = ''",
            (identity, clean_scope, clean_scope_id, old_norm),
        )
        row = await cursor.fetchone()
        if row is not None:
            await db.execute(
                "UPDATE entity_aliases SET status = 'superseded', valid_to = ?, updated_at = ? "
                "WHERE alias_id = ?",
                (now, now, row["alias_id"]),
            )
            await db.commit()
        result = await self.observe(
            entity_key=identity,
            alias=new_alias,
            scope=clean_scope,
            scope_id=clean_scope_id,
            confidence=confidence,
            source=source,
            observed_at=now,
        )
        return result.alias

    async def revoke(
        self,
        *,
        entity_key: str,
        alias: str,
        scope: str,
        scope_id: str,
        observed_at: str = "",
    ) -> EntityAlias | None:
        identity = _validate_entity_key(entity_key)
        clean_scope, clean_scope_id = _validate_scope(scope, scope_id)
        alias_norm = _normalize_alias(alias)
        if not alias_norm:
            raise ValueError("alias must contain a normalizable value")
        now = _normalize_instant(observed_at)
        db = self._require_db()
        cursor = await db.execute(
            "SELECT alias_id FROM entity_aliases WHERE entity_key = ? AND scope = ? "
            "AND scope_id = ? AND alias_norm = ? AND status = 'active' AND valid_to = ''",
            (identity, clean_scope, clean_scope_id, alias_norm),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        await db.execute(
            "UPDATE entity_aliases SET status = 'revoked', valid_to = ?, updated_at = ? "
            "WHERE alias_id = ?",
            (now, now, row["alias_id"]),
        )
        await db.commit()
        return await self._get_alias(str(row["alias_id"]))

    async def list_aliases(
        self,
        *,
        scope: str = "",
        scope_id: str = "",
        entity_key: str = "",
        status: str = "",
        limit: int = 100,
    ) -> list[EntityAlias]:
        clauses: list[str] = []
        params: list[Any] = []
        if scope:
            if scope not in _VALID_SCOPES:
                raise ValueError(f"invalid scope: {scope!r}")
            clauses.append("scope = ?")
            params.append(scope)
        if scope_id:
            clauses.append("scope_id = ?")
            params.append(str(scope_id))
        if entity_key:
            clauses.append("entity_key = ?")
            params.append(_validate_entity_key(entity_key))
        if status:
            if status not in _VALID_STATUSES:
                raise ValueError(f"invalid alias status: {status!r}")
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(0, min(200, int(limit))))
        cursor = await self._require_db().execute(
            f"SELECT * FROM entity_aliases {where} ORDER BY updated_at DESC, alias_id ASC LIMIT ?",
            params,
        )
        return [_row_to_alias(row) for row in await cursor.fetchall()]

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("EntityAliasStore not initialized")
        return self._db

    async def _get_alias(self, alias_id: str) -> EntityAlias | None:
        cursor = await self._require_db().execute(
            "SELECT * FROM entity_aliases WHERE alias_id = ?",
            (alias_id,),
        )
        row = await cursor.fetchone()
        return _row_to_alias(row) if row is not None else None

    async def _rows_for_alias(
        self,
        *,
        scope: str,
        scope_id: str,
        alias_norm: str,
    ) -> list[EntityAlias]:
        cursor = await self._require_db().execute(
            "SELECT * FROM entity_aliases WHERE scope = ? AND scope_id = ? AND alias_norm = ? "
            "ORDER BY created_at ASC, alias_id ASC",
            (scope, scope_id, alias_norm),
        )
        return [_row_to_alias(row) for row in await cursor.fetchall()]


def _row_to_alias(row: aiosqlite.Row) -> EntityAlias:
    try:
        meta = json.loads(row["meta_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    valid_to = str(row["valid_to"] or "").strip() or None
    return EntityAlias(
        alias_id=str(row["alias_id"]),
        entity_key=str(row["entity_key"]),
        alias_norm=str(row["alias_norm"]),
        alias_surface=str(row["alias_surface"]),
        scope=str(row["scope"]),
        scope_id=str(row["scope_id"]),
        confidence=float(row["confidence"]),
        source=str(row["source"]),
        status=str(row["status"]),
        valid_from=str(row["valid_from"]),
        valid_to=valid_to,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        meta=meta,
    )


def _validate_observation(
    *,
    entity_key: str,
    alias: str,
    scope: str,
    scope_id: str,
    confidence: float,
    source: str,
    observed_at: str,
) -> tuple[str, str, str, str, str, float, str, str]:
    identity = _validate_entity_key(entity_key)
    surface = str(alias or "").strip()
    alias_norm = _normalize_alias(surface)
    if not surface or not alias_norm:
        raise ValueError("alias must contain a normalizable value")
    clean_scope, clean_scope_id = _validate_scope(scope, scope_id)
    try:
        score = float(confidence)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be in [0, 1]") from exc
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError("confidence must be in [0, 1]")
    clean_source = str(source or "").strip()
    if not clean_source:
        raise ValueError("source must not be empty")
    return (
        identity,
        surface,
        alias_norm,
        clean_scope,
        clean_scope_id,
        score,
        clean_source,
        _normalize_instant(observed_at),
    )


def _validate_entity_key(entity_key: str) -> str:
    parsed = parse_entity_key(str(entity_key or "").strip())
    if parsed is None:
        raise ValueError(f"invalid entity key: {entity_key!r}")
    return parsed.entity_key


def _validate_scope(scope: str, scope_id: str) -> tuple[str, str]:
    clean_scope = str(scope or "").strip().lower()
    if clean_scope not in _VALID_SCOPES:
        raise ValueError(f"invalid scope: {scope!r}")
    clean_scope_id = str(scope_id or "").strip()
    if clean_scope in {"group", "user"} and not clean_scope_id:
        raise ValueError(f"{clean_scope} scope requires scope_id")
    if clean_scope == "global":
        clean_scope_id = clean_scope_id or "global"
    return clean_scope, clean_scope_id


def _normalize_alias(alias: str) -> str:
    normalized = normalize_text_key(str(alias or ""))
    return re.sub(r"[-‐‑–—]", "", normalized)


def _normalize_instant(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")
    try:
        return datetime.fromisoformat(clean).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp: {value!r}") from exc


def _alias_id(entity_key: str, scope: str, scope_id: str, alias_norm: str) -> str:
    material = "|".join((entity_key, scope, scope_id, alias_norm))
    return "ea_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _merge_meta(
    existing: dict[str, Any],
    incoming: dict[str, Any] | None,
    source: str,
) -> dict[str, Any]:
    merged = dict(existing)
    if incoming:
        merged.update(incoming)
    raw_sources = merged.get("sources")
    sources = [str(item) for item in raw_sources] if isinstance(raw_sources, list) else []
    for value in (existing.get("sources"), [source]):
        if not isinstance(value, list):
            continue
        for item in value:
            clean = str(item or "").strip()
            if clean and clean not in sources:
                sources.append(clean)
    merged["sources"] = sources
    return merged
