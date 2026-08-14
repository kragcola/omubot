"""Explicit-path append-only store for memory governance shadow facts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from services.memory.governance_contracts import (
    ActorKind,
    CandidateEnvelopeV1,
    CardClaimV1,
    ConflictV1,
    EpisodeClaimV1,
    EvidenceAtomV1,
    FactClaimV1,
    GraphRelationClaimV1,
    ObservationV1,
    ProjectionProposalV1,
    PromotionEventV1,
    PromotionFoldV1,
    PromotionKind,
    SlangClaimV1,
    StyleClaimV1,
    canonical_json,
    fold_promotion_events,
)
from services.storage import connect_sqlite
from services.storage.catalog import ConnectionProfile
from services.storage.migrations import Migration, MigrationRunner

_DB_ID = "memory_governance_shadow"

_CREATE_OBSERVATIONS = """
CREATE TABLE memory_governance_observations (
    observation_id     TEXT PRIMARY KEY,
    observation_sha256 TEXT NOT NULL UNIQUE,
    payload_json       TEXT NOT NULL,
    observed_at        TEXT NOT NULL,
    recorded_at        TEXT NOT NULL
)
"""

_CREATE_CANDIDATES = """
CREATE TABLE memory_governance_candidates (
    candidate_id       TEXT PRIMARY KEY,
    candidate_sha256   TEXT NOT NULL UNIQUE,
    observation_id     TEXT NOT NULL,
    projection_kind    TEXT NOT NULL,
    operation          TEXT NOT NULL,
    payload_json       TEXT NOT NULL,
    produced_at        TEXT NOT NULL,
    recorded_at        TEXT NOT NULL,
    FOREIGN KEY(observation_id)
        REFERENCES memory_governance_observations(observation_id)
)
"""

_CREATE_APPEND_ORDER = """
CREATE TABLE memory_governance_append_order (
    append_seq  INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_kind TEXT NOT NULL CHECK (entity_kind IN ('conflict', 'promotion_event')),
    entity_id   TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL
)
"""

_CREATE_CONFLICTS = """
CREATE TABLE memory_governance_conflicts (
    conflict_id        TEXT PRIMARY KEY,
    conflict_key       TEXT NOT NULL,
    append_seq         INTEGER NOT NULL UNIQUE,
    payload_json       TEXT NOT NULL,
    detected_at        TEXT NOT NULL,
    recorded_at        TEXT NOT NULL,
    FOREIGN KEY(append_seq) REFERENCES memory_governance_append_order(append_seq)
)
"""

_CREATE_CONFLICT_OBSERVATIONS = """
CREATE TABLE memory_governance_conflict_observations (
    conflict_id   TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    PRIMARY KEY(conflict_id, observation_id),
    FOREIGN KEY(conflict_id) REFERENCES memory_governance_conflicts(conflict_id),
    FOREIGN KEY(observation_id)
        REFERENCES memory_governance_observations(observation_id)
)
"""

_CREATE_CONFLICT_CANDIDATES = """
CREATE TABLE memory_governance_conflict_candidates (
    conflict_id  TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    PRIMARY KEY(conflict_id, candidate_id),
    FOREIGN KEY(conflict_id) REFERENCES memory_governance_conflicts(conflict_id),
    FOREIGN KEY(candidate_id) REFERENCES memory_governance_candidates(candidate_id)
)
"""

_CREATE_CONFLICT_PROJECTIONS = """
CREATE TABLE memory_governance_conflict_projections (
    conflict_id   TEXT NOT NULL,
    projection_ref TEXT NOT NULL,
    PRIMARY KEY(conflict_id, projection_ref),
    FOREIGN KEY(conflict_id) REFERENCES memory_governance_conflicts(conflict_id)
)
"""

_CREATE_PROMOTION_EVENTS = """
CREATE TABLE memory_governance_promotion_events (
    event_seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    append_seq       INTEGER NOT NULL UNIQUE,
    event_id         TEXT NOT NULL UNIQUE,
    idempotency_key  TEXT NOT NULL UNIQUE,
    candidate_id     TEXT NOT NULL,
    candidate_sha256 TEXT NOT NULL,
    event_kind       TEXT NOT NULL,
    projection_kind  TEXT NOT NULL,
    operation        TEXT NOT NULL,
    payload_json     TEXT NOT NULL,
    occurred_at      TEXT NOT NULL,
    recorded_at      TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES memory_governance_candidates(candidate_id),
    FOREIGN KEY(append_seq) REFERENCES memory_governance_append_order(append_seq)
)
"""

_CREATE_INDEXES = (
    "CREATE INDEX idx_memory_candidates_observation "
    "ON memory_governance_candidates(observation_id)",
    "CREATE INDEX idx_memory_append_order_kind "
    "ON memory_governance_append_order(entity_kind, append_seq)",
    "CREATE INDEX idx_memory_conflicts_key "
    "ON memory_governance_conflicts(conflict_key, detected_at)",
    "CREATE INDEX idx_memory_conflict_observations_observation "
    "ON memory_governance_conflict_observations(observation_id, conflict_id)",
    "CREATE INDEX idx_memory_conflict_candidates_candidate "
    "ON memory_governance_conflict_candidates(candidate_id, conflict_id)",
    "CREATE INDEX idx_memory_conflict_projections_projection "
    "ON memory_governance_conflict_projections(projection_ref, conflict_id)",
    "CREATE INDEX idx_memory_promotion_events_candidate "
    "ON memory_governance_promotion_events(candidate_id, event_seq)",
)

_TRUTH_TABLES = (
    "memory_governance_observations",
    "memory_governance_candidates",
    "memory_governance_append_order",
    "memory_governance_conflicts",
    "memory_governance_conflict_observations",
    "memory_governance_conflict_candidates",
    "memory_governance_conflict_projections",
    "memory_governance_promotion_events",
)
_MIGRATION_LEDGER_TABLE = "_omubot_schema_migrations"


def _immutable_triggers() -> tuple[str, ...]:
    statements: list[str] = []
    for table in _TRUTH_TABLES:
        statements.extend(
            (
                f"""
                CREATE TRIGGER deny_update_{table}
                BEFORE UPDATE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'memory governance truth is append-only');
                END
                """,
                f"""
                CREATE TRIGGER deny_delete_{table}
                BEFORE DELETE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'memory governance truth is append-only');
                END
                """,
            )
        )
    return tuple(statements)


_CREATE_TRIGGERS = _immutable_triggers()
_SCHEMA_STATEMENTS = (
    _CREATE_OBSERVATIONS,
    _CREATE_CANDIDATES,
    _CREATE_APPEND_ORDER,
    _CREATE_CONFLICTS,
    _CREATE_CONFLICT_OBSERVATIONS,
    _CREATE_CONFLICT_CANDIDATES,
    _CREATE_CONFLICT_PROJECTIONS,
    _CREATE_PROMOTION_EVENTS,
    *_CREATE_INDEXES,
    *_CREATE_TRIGGERS,
)
_MIGRATION_V1_CHECKSUM = "sha256:" + hashlib.sha256(
    "\n".join(_SCHEMA_STATEMENTS).encode("utf-8")
).hexdigest()


def _schema_contract() -> tuple[
    dict[str, dict[str, tuple[str, int, str | None, int]]],
    dict[str, frozenset[tuple[str, str, str, str, str]]],
    dict[str, str],
    dict[str, tuple[str, tuple[str, ...], int, int, str]],
    dict[str, tuple[str, str]],
]:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    try:
        for statement in _SCHEMA_STATEMENTS:
            db.execute(statement)
        columns: dict[str, dict[str, tuple[str, int, str | None, int]]] = {}
        foreign_keys: dict[str, frozenset[tuple[str, str, str, str, str]]] = {}
        for table in _TRUTH_TABLES:
            columns[table] = {
                str(row["name"]): (
                    str(row["type"]).upper(),
                    int(row["notnull"]),
                    None if row["dflt_value"] is None else str(row["dflt_value"]),
                    int(row["pk"]),
                )
                for row in db.execute(f"PRAGMA table_info({table})")
            }
            foreign_keys[table] = frozenset(
                (
                    str(row["table"]),
                    str(row["from"]),
                    str(row["to"]),
                    str(row["on_update"]),
                    str(row["on_delete"]),
                )
                for row in db.execute(f"PRAGMA foreign_key_list({table})")
            )
        table_sql = {
            str(row["name"]): _normalized_sql(str(row["sql"]))
            for row in db.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
            if str(row["name"]) in _TRUTH_TABLES
        }
        indexes: dict[str, tuple[str, tuple[str, ...], int, int, str]] = {}
        for row in db.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master "
            "WHERE type = 'index' AND sql IS NOT NULL"
        ):
            name = str(row["name"])
            table = str(row["tbl_name"])
            metadata = next(
                item
                for item in db.execute(f"PRAGMA index_list({table})")
                if str(item["name"]) == name
            )
            indexes[name] = (
                table,
                tuple(
                    str(column["name"])
                    for column in db.execute(f"PRAGMA index_info({name})")
                ),
                int(metadata["unique"]),
                int(metadata["partial"]),
                _normalized_sql(str(row["sql"])),
            )
        triggers = {
            str(row["name"]): (str(row["tbl_name"]), _normalized_sql(str(row["sql"])))
            for row in db.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        return columns, foreign_keys, table_sql, indexes, triggers
    finally:
        db.close()


def _normalized_sql(value: str) -> str:
    return " ".join(value.split())


(
    _EXPECTED_COLUMNS,
    _EXPECTED_FOREIGN_KEYS,
    _EXPECTED_TABLE_SQL,
    _EXPECTED_INDEXES,
    _EXPECTED_TRIGGERS,
) = _schema_contract()


async def _apply_v1(db: aiosqlite.Connection) -> None:
    for statement in _SCHEMA_STATEMENTS:
        await db.execute(statement)


async def _verify_v1(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE type IN ('table', 'index', 'view', 'trigger') "
        "AND name NOT LIKE 'sqlite_%'"
    )
    try:
        actual_schema_objects = {
            (str(row["type"]), str(row["name"]))
            for row in await cursor.fetchall()
        }
    finally:
        await cursor.close()
    expected_schema_objects = {
        *(("table", table) for table in _TRUTH_TABLES),
        ("table", _MIGRATION_LEDGER_TABLE),
        *(("index", name) for name in _EXPECTED_INDEXES),
        *(("trigger", name) for name in _EXPECTED_TRIGGERS),
    }
    if actual_schema_objects != expected_schema_objects:
        return False

    for table in _TRUTH_TABLES:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        try:
            columns = {
                str(row["name"]): (
                    str(row["type"]).upper(),
                    int(row["notnull"]),
                    None if row["dflt_value"] is None else str(row["dflt_value"]),
                    int(row["pk"]),
                )
                for row in await cursor.fetchall()
            }
        finally:
            await cursor.close()
        if columns != _EXPECTED_COLUMNS[table]:
            return False
        cursor = await db.execute(f"PRAGMA foreign_key_list({table})")
        try:
            foreign_keys = frozenset(
                (
                    str(row["table"]),
                    str(row["from"]),
                    str(row["to"]),
                    str(row["on_update"]),
                    str(row["on_delete"]),
                )
                for row in await cursor.fetchall()
            )
        finally:
            await cursor.close()
        if foreign_keys != _EXPECTED_FOREIGN_KEYS[table]:
            return False

    cursor = await db.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    try:
        table_sql = {
            str(row["name"]): _normalized_sql(str(row["sql"]))
            for row in await cursor.fetchall()
            if str(row["name"]) in _TRUTH_TABLES
        }
    finally:
        await cursor.close()
    if table_sql != _EXPECTED_TABLE_SQL:
        return False

    cursor = await db.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master "
        "WHERE type = 'index' AND sql IS NOT NULL"
    )
    try:
        index_rows = {
            str(row["name"]): (str(row["tbl_name"]), str(row["sql"]))
            for row in await cursor.fetchall()
        }
    finally:
        await cursor.close()
    if set(index_rows) != set(_EXPECTED_INDEXES):
        return False
    for name, expected in _EXPECTED_INDEXES.items():
        expected_table, expected_columns, expected_unique, expected_partial, expected_sql = (
            expected
        )
        actual = index_rows.get(name)
        if actual is None or actual[0] != expected_table:
            return False
        cursor = await db.execute(f"PRAGMA index_info({name})")
        try:
            columns = tuple(str(row["name"]) for row in await cursor.fetchall())
        finally:
            await cursor.close()
        if columns != expected_columns:
            return False
        cursor = await db.execute(f"PRAGMA index_list({expected_table})")
        try:
            metadata = next(
                (
                    row
                    for row in await cursor.fetchall()
                    if str(row["name"]) == name
                ),
                None,
            )
        finally:
            await cursor.close()
        if metadata is None or (
            int(metadata["unique"]),
            int(metadata["partial"]),
            _normalized_sql(actual[1]),
        ) != (expected_unique, expected_partial, expected_sql):
            return False

    cursor = await db.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger'"
    )
    try:
        triggers = {
            str(row["name"]): (
                str(row["tbl_name"]),
                _normalized_sql(str(row["sql"])),
            )
            for row in await cursor.fetchall()
        }
    finally:
        await cursor.close()
    return triggers == _EXPECTED_TRIGGERS


_MIGRATION_V1 = Migration(
    version=1,
    name="create_memory_governance_shadow_store",
    checksum=_MIGRATION_V1_CHECKSUM,
    apply=_apply_v1,
    verify=_verify_v1,
)


@dataclass(frozen=True, slots=True)
class _ConflictContext:
    detected_at: datetime
    append_seq: int


class MemoryGovernanceStore:
    """Durable shadow ledger; initialization is always explicit."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        async with self._lifecycle_lock:
            if self._db is not None:
                return
            await MigrationRunner(
                db_path=self.db_path,
                db_id=_DB_ID,
                profile=ConnectionProfile.DELETE_FULL,
            ).ensure((_MIGRATION_V1,))
            db = await connect_sqlite(
                self.db_path,
                busy_timeout_ms=0,
                profile=ConnectionProfile.DELETE_FULL,
            )
            async with self._write_lock:
                self._db = db

    async def close(self) -> None:
        async with self._lifecycle_lock, self._write_lock:
            db = self._db
            self._db = None
            if db is None:
                return
            _, cancellation = await _await_cleanup_task(
                asyncio.create_task(
                    db.close(),
                    name="memory-governance-store-close",
                )
            )
            if cancellation is not None:
                raise cancellation

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("MemoryGovernanceStore is not initialized")
        return self._db

    async def append_candidate(
        self,
        candidate: CandidateEnvelopeV1,
    ) -> CandidateEnvelopeV1:
        canonical = _validated_candidate(candidate)
        observation_json = canonical_json(canonical.observation.to_dict())
        candidate_json = canonical_json(canonical.to_dict())
        async with self._write_lock:
            db = self._require_db()
            try:
                await _begin_immediate(db)
                existing_observation = await _row_for_key(
                    db,
                    table="memory_governance_observations",
                    key_column="observation_id",
                    key=canonical.observation.observation_id,
                )
                if existing_observation is None:
                    await db.execute(
                        """
                        INSERT INTO memory_governance_observations (
                            observation_id, observation_sha256, payload_json,
                            observed_at, recorded_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            canonical.observation.observation_id,
                            canonical.observation.observation_sha256,
                            observation_json,
                            _iso(canonical.observation.observed_at),
                            _now_iso(),
                        ),
                    )
                elif _observation_from_row(existing_observation) != canonical.observation:
                    raise ValueError("observation identity collision")

                existing_candidate = await _row_for_key(
                    db,
                    table="memory_governance_candidates",
                    key_column="candidate_id",
                    key=canonical.candidate_id,
                )
                if existing_candidate is None:
                    await db.execute(
                        """
                        INSERT INTO memory_governance_candidates (
                            candidate_id, candidate_sha256, observation_id,
                            projection_kind, operation, payload_json,
                            produced_at, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            canonical.candidate_id,
                            canonical.candidate_sha256,
                            canonical.observation.observation_id,
                            canonical.proposal.projection_kind.value,
                            canonical.proposal.operation.value,
                            candidate_json,
                            _iso(canonical.produced_at),
                            _now_iso(),
                        ),
                    )
                elif _candidate_from_row(existing_candidate) != canonical:
                    raise ValueError("candidate identity collision")
                await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise
        return canonical

    async def get_observation(self, observation_id: str) -> ObservationV1 | None:
        async with self._write_lock:
            row = await _row_for_key(
                self._require_db(),
                table="memory_governance_observations",
                key_column="observation_id",
                key=str(observation_id),
            )
        return _observation_from_row(row) if row is not None else None

    async def get_candidate(self, candidate_id: str) -> CandidateEnvelopeV1 | None:
        async with self._write_lock:
            row = await _row_for_key(
                self._require_db(),
                table="memory_governance_candidates",
                key_column="candidate_id",
                key=str(candidate_id),
            )
        return _candidate_from_row(row) if row is not None else None

    async def append_conflict(self, conflict: ConflictV1) -> ConflictV1:
        canonical = _validated_conflict(conflict)
        payload_json = canonical_json(canonical.to_dict())
        async with self._write_lock:
            db = self._require_db()
            try:
                await _begin_immediate(db)
                existing = await _row_for_key(
                    db,
                    table="memory_governance_conflicts",
                    key_column="conflict_id",
                    key=canonical.conflict_id,
                )
                if existing is not None:
                    if await _conflict_from_row_verified(db, existing) != canonical:
                        raise ValueError("conflict identity collision")
                    await db.commit()
                    return canonical
                await _require_ids(
                    db,
                    table="memory_governance_observations",
                    key_column="observation_id",
                    values=canonical.observation_ids,
                    label="observation",
                )
                await _require_ids(
                    db,
                    table="memory_governance_candidates",
                    key_column="candidate_id",
                    values=canonical.candidate_ids,
                    label="candidate",
                )
                recorded_at = _now_iso()
                append_seq = await _append_order(
                    db,
                    entity_kind="conflict",
                    entity_id=canonical.conflict_id,
                    recorded_at=recorded_at,
                )
                await db.execute(
                    """
                    INSERT INTO memory_governance_conflicts (
                        conflict_id, conflict_key, append_seq, payload_json,
                        detected_at, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        canonical.conflict_id,
                        canonical.conflict_key,
                        append_seq,
                        payload_json,
                        _iso(canonical.detected_at),
                        recorded_at,
                    ),
                )
                await db.executemany(
                    "INSERT INTO memory_governance_conflict_observations "
                    "(conflict_id, observation_id) VALUES (?, ?)",
                    tuple(
                        (canonical.conflict_id, value)
                        for value in canonical.observation_ids
                    ),
                )
                await db.executemany(
                    "INSERT INTO memory_governance_conflict_candidates "
                    "(conflict_id, candidate_id) VALUES (?, ?)",
                    tuple(
                        (canonical.conflict_id, value)
                        for value in canonical.candidate_ids
                    ),
                )
                await db.executemany(
                    "INSERT INTO memory_governance_conflict_projections "
                    "(conflict_id, projection_ref) VALUES (?, ?)",
                    tuple(
                        (canonical.conflict_id, value)
                        for value in canonical.existing_projection_refs
                    ),
                )
                await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise
        return canonical

    async def get_conflict(self, conflict_id: str) -> ConflictV1 | None:
        async with self._write_lock:
            db = self._require_db()
            row = await _row_for_key(
                db,
                table="memory_governance_conflicts",
                key_column="conflict_id",
                key=str(conflict_id),
            )
            return (
                await _conflict_from_row_verified(db, row)
                if row is not None
                else None
            )

    async def append_promotion_event(
        self,
        event: PromotionEventV1,
    ) -> PromotionEventV1:
        canonical, _ = await self.append_promotion_event_with_outcome(event)
        return canonical

    async def append_promotion_event_with_outcome(
        self,
        event: PromotionEventV1,
    ) -> tuple[PromotionEventV1, bool]:
        return await self._append_promotion_event(event)

    async def _append_promotion_event(
        self,
        event: PromotionEventV1,
    ) -> tuple[PromotionEventV1, bool]:
        canonical = _validated_event(event)
        payload_json = canonical_json(canonical.to_dict())
        async with self._write_lock:
            db = self._require_db()
            try:
                await _begin_immediate(db)
                candidate = await _candidate_for_update(db, canonical.candidate_id)
                if candidate is None:
                    raise ValueError("promotion event candidate does not exist")
                if candidate.candidate_sha256 != canonical.candidate_sha256:
                    raise ValueError("promotion event candidate digest mismatch")
                if (
                    candidate.proposal.projection_kind is not canonical.projection_kind
                    or candidate.proposal.operation is not canonical.operation
                ):
                    raise ValueError("promotion event does not match candidate proposal")
                existing = await _event_by_identity(db, canonical)
                if existing is not None:
                    if existing != canonical:
                        raise ValueError("promotion event identity collision")
                    await db.commit()
                    return canonical, False
                previous = await _list_promotion_events(db, canonical.candidate_id)
                conflict_context = await _candidate_conflict_context(
                    db,
                    canonical.candidate_id,
                )
                if (
                    canonical.actor_kind is ActorKind.OPERATOR
                    and canonical.event_kind
                    in {
                        PromotionKind.PROMOTION_APPROVED,
                        PromotionKind.PROMOTION_REJECTED,
                    }
                    and set(canonical.conflict_ids) != set(conflict_context)
                ):
                    raise ValueError(
                        "operator decision conflict set must match current conflicts"
                    )
                recorded_at = _now_iso()
                append_seq = await _append_order(
                    db,
                    entity_kind="promotion_event",
                    entity_id=canonical.event_id,
                    recorded_at=recorded_at,
                )
                event_append_seq = await _promotion_event_append_sequences(
                    db,
                    canonical.candidate_id,
                )
                event_append_seq[canonical.event_id] = append_seq
                fold_promotion_events(
                    (*previous, canonical),
                    known_conflict_ids=conflict_context,
                    conflict_known_at={
                        conflict_id: context.detected_at
                        for conflict_id, context in conflict_context.items()
                    },
                    event_append_seq=event_append_seq,
                    conflict_append_seq={
                        conflict_id: context.append_seq
                        for conflict_id, context in conflict_context.items()
                    },
                )
                await db.execute(
                    """
                    INSERT INTO memory_governance_promotion_events (
                        append_seq, event_id, idempotency_key, candidate_id,
                        candidate_sha256, event_kind, projection_kind, operation,
                        payload_json, occurred_at, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        append_seq,
                        canonical.event_id,
                        canonical.idempotency_key,
                        canonical.candidate_id,
                        canonical.candidate_sha256,
                        canonical.event_kind.value,
                        canonical.projection_kind.value,
                        canonical.operation.value,
                        payload_json,
                        _iso(canonical.occurred_at),
                        recorded_at,
                    ),
                )
                await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise
        return canonical, True

    async def list_promotion_events(
        self,
        candidate_id: str,
    ) -> tuple[PromotionEventV1, ...]:
        async with self._write_lock:
            return await _list_promotion_events(
                self._require_db(),
                str(candidate_id),
            )

    async def fold_candidate(self, candidate_id: str) -> PromotionFoldV1:
        async with self._write_lock:
            db = self._require_db()
            events = await _list_promotion_events(db, str(candidate_id))
            conflict_context = await _candidate_conflict_context(
                db,
                str(candidate_id),
            )
            return fold_promotion_events(
                events,
                known_conflict_ids=conflict_context,
                conflict_known_at={
                    conflict_id: context.detected_at
                    for conflict_id, context in conflict_context.items()
                },
                event_append_seq=await _promotion_event_append_sequences(
                    db,
                    str(candidate_id),
                ),
                conflict_append_seq={
                    conflict_id: context.append_seq
                    for conflict_id, context in conflict_context.items()
                },
            )


async def _begin_immediate(db: aiosqlite.Connection) -> None:
    while True:
        try:
            await db.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            await asyncio.sleep(0.01)


async def _shielded_rollback(db: aiosqlite.Connection) -> None:
    task = asyncio.create_task(db.rollback(), name="memory-governance-rollback")
    _, cancellation = await _await_cleanup_task(task)
    if cancellation is not None:
        raise cancellation


async def _await_cleanup_task(
    task: asyncio.Task[Any],
) -> tuple[Any, asyncio.CancelledError | None]:
    first_cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if first_cancellation is None:
                first_cancellation = exc
    return task.result(), first_cancellation


async def _row_for_key(
    db: aiosqlite.Connection,
    *,
    table: str,
    key_column: str,
    key: str,
) -> Any | None:
    if table not in _TRUTH_TABLES or key_column not in {
        "observation_id",
        "candidate_id",
        "conflict_id",
    }:
        raise ValueError("invalid governance lookup")
    cursor = await db.execute(
        f"SELECT * FROM {table} WHERE {key_column} = ?",
        (key,),
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    return row


def _positive_append_seq(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("stored append_seq must be an integer")
    if value <= 0:
        raise ValueError("stored append_seq must be positive")
    return value


async def _append_order(
    db: aiosqlite.Connection,
    *,
    entity_kind: str,
    entity_id: str,
    recorded_at: str,
) -> int:
    if entity_kind not in {"conflict", "promotion_event"}:
        raise ValueError("unsupported governance append entity kind")
    cursor = await db.execute(
        """
        INSERT INTO memory_governance_append_order (
            entity_kind, entity_id, recorded_at
        ) VALUES (?, ?, ?)
        """,
        (entity_kind, entity_id, recorded_at),
    )
    try:
        return _positive_append_seq(cursor.lastrowid)
    finally:
        await cursor.close()


async def _verify_append_order_binding(
    db: aiosqlite.Connection,
    *,
    append_seq: object,
    entity_kind: str,
    entity_id: str,
    recorded_at: object,
) -> int:
    sequence = _positive_append_seq(append_seq)
    cursor = await db.execute(
        """
        SELECT entity_kind, entity_id, recorded_at
        FROM memory_governance_append_order
        WHERE append_seq = ?
        """,
        (sequence,),
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None or (
        str(row["entity_kind"]),
        str(row["entity_id"]),
        str(row["recorded_at"]),
    ) != (entity_kind, entity_id, str(recorded_at)):
        raise ValueError("stored append order does not match governance row")
    return sequence


async def _conflict_from_row_verified(
    db: aiosqlite.Connection,
    row: Any,
) -> ConflictV1:
    conflict = _conflict_from_row(row)
    await _verify_append_order_binding(
        db,
        append_seq=row["append_seq"],
        entity_kind="conflict",
        entity_id=conflict.conflict_id,
        recorded_at=row["recorded_at"],
    )
    await _verify_conflict_links(db, conflict)
    return conflict


async def _verify_conflict_links(
    db: aiosqlite.Connection,
    conflict: ConflictV1,
) -> None:
    contracts = (
        (
            "memory_governance_conflict_observations",
            "observation_id",
            conflict.observation_ids,
        ),
        (
            "memory_governance_conflict_candidates",
            "candidate_id",
            conflict.candidate_ids,
        ),
        (
            "memory_governance_conflict_projections",
            "projection_ref",
            conflict.existing_projection_refs,
        ),
    )
    for table, value_column, expected in contracts:
        cursor = await db.execute(
            f"SELECT {value_column} FROM {table} "
            f"WHERE conflict_id = ? ORDER BY {value_column}",
            (conflict.conflict_id,),
        )
        try:
            actual = tuple(
                str(row[value_column]) for row in await cursor.fetchall()
            )
        finally:
            await cursor.close()
        if actual != tuple(sorted(expected)):
            raise ValueError("stored conflict links do not match payload")


async def _event_from_row_verified(
    db: aiosqlite.Connection,
    row: Any,
) -> PromotionEventV1:
    event = _event_from_row(row)
    await _verify_append_order_binding(
        db,
        append_seq=row["append_seq"],
        entity_kind="promotion_event",
        entity_id=event.event_id,
        recorded_at=row["recorded_at"],
    )
    return event


async def _require_ids(
    db: aiosqlite.Connection,
    *,
    table: str,
    key_column: str,
    values: tuple[str, ...],
    label: str,
) -> None:
    for value in values:
        row = await _row_for_key(
            db,
            table=table,
            key_column=key_column,
            key=value,
        )
        if row is None:
            raise ValueError(f"conflict references missing {label}: {value}")
        if table == "memory_governance_observations":
            _observation_from_row(row)
        elif table == "memory_governance_candidates":
            _candidate_from_row(row)


async def _candidate_for_update(
    db: aiosqlite.Connection,
    candidate_id: str,
) -> CandidateEnvelopeV1 | None:
    row = await _row_for_key(
        db,
        table="memory_governance_candidates",
        key_column="candidate_id",
        key=candidate_id,
    )
    return _candidate_from_row(row) if row is not None else None


async def _event_by_identity(
    db: aiosqlite.Connection,
    event: PromotionEventV1,
) -> PromotionEventV1 | None:
    cursor = await db.execute(
        """
        SELECT *
        FROM memory_governance_promotion_events
        WHERE event_id = ? OR idempotency_key = ?
        """,
        (event.event_id, event.idempotency_key),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    if not rows:
        return None
    events = tuple(
        [await _event_from_row_verified(db, row) for row in rows]
    )
    if any(item != events[0] for item in events[1:]):
        raise ValueError("promotion event identity is internally inconsistent")
    return events[0]


async def _list_promotion_events(
    db: aiosqlite.Connection,
    candidate_id: str,
) -> tuple[PromotionEventV1, ...]:
    cursor = await db.execute(
        """
        SELECT *
        FROM memory_governance_promotion_events
        WHERE candidate_id = ?
        ORDER BY event_seq
        """,
        (candidate_id,),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return tuple([await _event_from_row_verified(db, row) for row in rows])


async def _promotion_event_append_sequences(
    db: aiosqlite.Connection,
    candidate_id: str,
) -> dict[str, int]:
    cursor = await db.execute(
        """
        SELECT event_id, append_seq, recorded_at
        FROM memory_governance_promotion_events
        WHERE candidate_id = ?
        ORDER BY append_seq
        """,
        (candidate_id,),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    result: dict[str, int] = {}
    for row in rows:
        event_id = str(row["event_id"])
        result[event_id] = await _verify_append_order_binding(
            db,
            append_seq=row["append_seq"],
            entity_kind="promotion_event",
            entity_id=event_id,
            recorded_at=row["recorded_at"],
        )
    return result


async def _candidate_conflict_ids(
    db: aiosqlite.Connection,
    candidate_id: str,
) -> tuple[str, ...]:
    cursor = await db.execute(
        """
        SELECT conflict_id
        FROM memory_governance_conflict_candidates
        WHERE candidate_id = ?
        UNION
        SELECT conflict_observations.conflict_id
        FROM memory_governance_conflict_observations AS conflict_observations
        JOIN memory_governance_candidates AS candidates
          ON candidates.observation_id = conflict_observations.observation_id
        WHERE candidates.candidate_id = ?
        UNION
        SELECT conflicts.conflict_id
        FROM memory_governance_conflicts AS conflicts
        JOIN json_each(conflicts.payload_json, '$.candidate_ids') AS payload_candidates
          ON payload_candidates.value = ?
        UNION
        SELECT conflicts.conflict_id
        FROM memory_governance_conflicts AS conflicts
        JOIN json_each(
            conflicts.payload_json,
            '$.observation_ids'
        ) AS payload_observations
        JOIN memory_governance_candidates AS candidates
          ON candidates.observation_id = payload_observations.value
        WHERE candidates.candidate_id = ?
        ORDER BY conflict_id
        """,
        (candidate_id, candidate_id, candidate_id, candidate_id),
    )
    try:
        return tuple(str(row["conflict_id"]) for row in await cursor.fetchall())
    finally:
        await cursor.close()


async def _candidate_conflict_context(
    db: aiosqlite.Connection,
    candidate_id: str,
) -> dict[str, _ConflictContext]:
    result: dict[str, _ConflictContext] = {}
    for conflict_id in await _candidate_conflict_ids(db, candidate_id):
        row = await _row_for_key(
            db,
            table="memory_governance_conflicts",
            key_column="conflict_id",
            key=conflict_id,
        )
        if row is None:
            raise ValueError("candidate conflict link references a missing conflict")
        conflict = await _conflict_from_row_verified(db, row)
        result[conflict.conflict_id] = _ConflictContext(
            detected_at=conflict.detected_at,
            append_seq=_positive_append_seq(row["append_seq"]),
        )
    return result


def _validated_candidate(value: object) -> CandidateEnvelopeV1:
    if not isinstance(value, CandidateEnvelopeV1):
        raise TypeError("append_candidate requires CandidateEnvelopeV1")
    canonical = _candidate_from_mapping(value.to_dict())
    if canonical != value:
        raise ValueError("candidate object is not canonical")
    return canonical


def _validated_conflict(value: object) -> ConflictV1:
    if not isinstance(value, ConflictV1):
        raise TypeError("append_conflict requires ConflictV1")
    canonical = _conflict_from_mapping(value.to_dict())
    if canonical != value:
        raise ValueError("conflict object is not canonical")
    return canonical


def _validated_event(value: object) -> PromotionEventV1:
    if not isinstance(value, PromotionEventV1):
        raise TypeError("append_promotion_event requires PromotionEventV1")
    canonical = _event_from_mapping(value.to_dict())
    if canonical != value:
        raise ValueError("promotion event object is not canonical")
    return canonical


def _json_mapping(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("governance payload must be a JSON object")
    return {str(key): item for key, item in parsed.items()}


def _claim_from_mapping(value: object) -> Any:
    data = _mapping(value, "claim")
    kind = str(data.get("kind") or "")
    if kind == "card":
        return CardClaimV1(category=data["category"], content=data["content"])
    if kind == "fact":
        return FactClaimV1(
            subject=data["subject"],
            predicate=data["predicate"],
            object_value=data["object_value"],
        )
    if kind == "slang":
        return SlangClaimV1(
            term=data["term"],
            meaning=data["meaning"],
            aliases=tuple(data.get("aliases") or ()),
            repeat_policy=data.get("repeat_policy"),  # type: ignore[arg-type]
        )
    if kind == "style":
        return StyleClaimV1(
            expression=data["expression"],
            situation=data["situation"],
            outcome_signal=data.get("outcome_signal", ""),
        )
    if kind == "episode":
        return EpisodeClaimV1(
            situation=data["situation"],
            observed_context=data.get("observed_context", ""),
            action_taken=data.get("action_taken", ""),
            outcome_signal=data.get("outcome_signal", ""),
            reflection=data.get("reflection", ""),
        )
    if kind == "graph_relation":
        return GraphRelationClaimV1(
            subject_node=data["subject_node"],
            predicate=data["predicate"],
            object_node=data["object_node"],
            edge_type=data.get("edge_type"),  # type: ignore[arg-type]
        )
    raise ValueError(f"unsupported stored claim kind: {kind!r}")


def _evidence_from_mapping(value: object) -> EvidenceAtomV1:
    data = _mapping(value, "evidence")
    return EvidenceAtomV1(
        evidence_ref=data["evidence_ref"],
        content_sha256=data["content_sha256"],
        quote=data["quote"],
        actor_ref=data["actor_ref"],
        occurred_at=data.get("occurred_at"),
    )


def _observation_from_mapping(value: object) -> ObservationV1:
    data = _mapping(value, "observation")
    observation = ObservationV1(
        source_kind=data["source_kind"],  # type: ignore[arg-type]
        producer_kind=data["producer_kind"],  # type: ignore[arg-type]
        producer_version=data["producer_version"],
        producer_run_id=data["producer_run_id"],
        subject_ref=data["subject_ref"],
        owner_scope=data["owner_scope"],  # type: ignore[arg-type]
        owner_id=data["owner_id"],
        visibility=data["visibility"],  # type: ignore[arg-type]
        origin_group_ref=data.get("origin_group_ref"),
        claim=_claim_from_mapping(data["claim"]),
        evidence=tuple(
            _evidence_from_mapping(item) for item in data.get("evidence") or ()
        ),
        observed_at=data["observed_at"],
        source_occurred_at=data.get("source_occurred_at"),
        time_basis=data["time_basis"],  # type: ignore[arg-type]
        valid_from=data.get("valid_from"),
        valid_to=data.get("valid_to"),
        confidence=data["confidence"],
    )
    _require_derived(data, "contract_version", observation.contract_version)
    _require_derived(data, "observation_id", observation.observation_id)
    _require_derived(data, "observation_sha256", observation.observation_sha256)
    return observation


def _proposal_from_mapping(value: object) -> ProjectionProposalV1:
    data = _mapping(value, "proposal")
    proposal = ProjectionProposalV1.create(
        projection_kind=data["projection_kind"],  # type: ignore[arg-type]
        operation=data["operation"],  # type: ignore[arg-type]
        target_ref=data.get("target_ref"),
        payload=_mapping(data["payload"], "proposal payload"),
    )
    _require_derived(data, "contract_version", proposal.contract_version)
    return proposal


def _candidate_from_mapping(value: object) -> CandidateEnvelopeV1:
    data = _mapping(value, "candidate")
    candidate = CandidateEnvelopeV1(
        observation=_observation_from_mapping(data["observation"]),
        proposal=_proposal_from_mapping(data["proposal"]),
        producer_kind=data["producer_kind"],  # type: ignore[arg-type]
        producer_run_id=data["producer_run_id"],
        producer_item_id=data["producer_item_id"],
        produced_at=data["produced_at"],  # type: ignore[arg-type]
        model_output_sha256=data["model_output_sha256"],
    )
    for field_name in (
        "contract_version",
        "mode",
        "candidate_id",
        "candidate_sha256",
        "idempotency_key",
    ):
        _require_derived(data, field_name, getattr(candidate, field_name))
    return candidate


def _conflict_from_mapping(value: object) -> ConflictV1:
    data = _mapping(value, "conflict")
    conflict = ConflictV1.create(
        kind=data["kind"],  # type: ignore[arg-type]
        subject_ref=data["subject_ref"],
        claim_key=data["claim_key"],
        observation_ids=tuple(data.get("observation_ids") or ()),
        candidate_ids=tuple(data.get("candidate_ids") or ()),
        existing_projection_refs=tuple(data.get("existing_projection_refs") or ()),
        detected_at=data["detected_at"],
        detector=data["detector"],  # type: ignore[arg-type]
        basis_evidence_refs=tuple(data.get("basis_evidence_refs") or ()),
    )
    for field_name in ("contract_version", "conflict_id", "conflict_key"):
        _require_derived(data, field_name, getattr(conflict, field_name))
    return conflict


def _event_from_mapping(value: object) -> PromotionEventV1:
    data = _mapping(value, "promotion event")
    event = PromotionEventV1.create(
        candidate_id=data["candidate_id"],
        candidate_sha256=data["candidate_sha256"],
        event_kind=data["event_kind"],  # type: ignore[arg-type]
        actor_kind=data["actor_kind"],  # type: ignore[arg-type]
        actor_ref=data["actor_ref"],
        occurred_at=data["occurred_at"],
        reason_code=data["reason_code"],
        operator_note=data.get("operator_note", ""),
        conflict_ids=tuple(data.get("conflict_ids") or ()),
        projection_kind=data["projection_kind"],  # type: ignore[arg-type]
        operation=data["operation"],  # type: ignore[arg-type]
        projection_ref=data.get("projection_ref"),
        receipt_ref=data.get("receipt_ref"),
    )
    for field_name in (
        "contract_version",
        "event_id",
        "note_sha256",
        "idempotency_key",
    ):
        _require_derived(data, field_name, getattr(event, field_name))
    return event


def _observation_from_row(row: Any) -> ObservationV1:
    observation = _observation_from_json(str(row["payload_json"]))
    _require_row_values(
        row,
        {
            "observation_id": observation.observation_id,
            "observation_sha256": observation.observation_sha256,
            "observed_at": _iso(observation.observed_at),
        },
    )
    return observation


def _candidate_from_row(row: Any) -> CandidateEnvelopeV1:
    candidate = _candidate_from_json(str(row["payload_json"]))
    _require_row_values(
        row,
        {
            "candidate_id": candidate.candidate_id,
            "candidate_sha256": candidate.candidate_sha256,
            "observation_id": candidate.observation.observation_id,
            "projection_kind": candidate.proposal.projection_kind.value,
            "operation": candidate.proposal.operation.value,
            "produced_at": _iso(candidate.produced_at),
        },
    )
    return candidate


def _conflict_from_row(row: Any) -> ConflictV1:
    conflict = _conflict_from_json(str(row["payload_json"]))
    _positive_append_seq(row["append_seq"])
    _require_row_values(
        row,
        {
            "conflict_id": conflict.conflict_id,
            "conflict_key": conflict.conflict_key,
            "detected_at": _iso(conflict.detected_at),
        },
    )
    return conflict


def _event_from_row(row: Any) -> PromotionEventV1:
    event = _event_from_json(str(row["payload_json"]))
    _positive_append_seq(row["event_seq"])
    _positive_append_seq(row["append_seq"])
    _require_row_values(
        row,
        {
            "event_id": event.event_id,
            "idempotency_key": event.idempotency_key,
            "candidate_id": event.candidate_id,
            "candidate_sha256": event.candidate_sha256,
            "event_kind": event.event_kind.value,
            "projection_kind": event.projection_kind.value,
            "operation": event.operation.value,
            "occurred_at": _iso(event.occurred_at),
        },
    )
    return event


def _observation_from_json(value: str) -> ObservationV1:
    return _observation_from_mapping(_json_mapping(value))


def _candidate_from_json(value: str) -> CandidateEnvelopeV1:
    return _candidate_from_mapping(_json_mapping(value))


def _conflict_from_json(value: str) -> ConflictV1:
    return _conflict_from_mapping(_json_mapping(value))


def _event_from_json(value: str) -> PromotionEventV1:
    return _event_from_mapping(_json_mapping(value))


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _require_derived(data: Mapping[str, Any], field_name: str, expected: object) -> None:
    if data.get(field_name) != expected:
        raise ValueError(f"stored {field_name} does not match canonical value")


def _require_row_values(row: Any, expected: Mapping[str, object]) -> None:
    for field_name, expected_value in expected.items():
        if row[field_name] != expected_value:
            raise ValueError(f"stored {field_name} column does not match payload")


def _iso(value: object) -> str:
    if not isinstance(value, datetime):
        raise TypeError("governance timestamp was not normalized")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["MemoryGovernanceStore"]
