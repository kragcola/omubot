"""Versioned topic-assignment storage derived from raw research events."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from services.storage import close_with_checkpoint, connect_sqlite
from services.storage.migrations import Migration, MigrationRunner
from services.storage.schema_contracts import (
    RESEARCH_TOPIC_ASSIGNMENTS_V1_MIGRATION_CHECKSUM,
    RESEARCH_TOPIC_ASSIGNMENTS_V1_MIGRATION_NAME,
    verify_catalog_schema_async,
)


@dataclass(frozen=True, slots=True)
class AssignmentRun:
    run_uuid: str
    algorithm_version: str
    algorithm_config_hash: str
    input_digest: str
    input_cutoff: str
    started_at: str
    completed_at: str
    status: str
    event_count: int
    assignment_count: int


@dataclass(frozen=True, slots=True)
class TopicBlockIdentity:
    block_uuid: str
    algorithm_version: str
    group_id: str
    seed_event_uid: str
    created_at: str


@dataclass(frozen=True, slots=True)
class TopicAssignment:
    event_uid: str
    algorithm_version: str
    run_uuid: str
    block_uuid: str
    assigned_at: str
    reason: str
    score: float | None
    runner_up_margin: float | None
    reply_predecessor_event_uid: str | None
    evidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class UtteranceMembership:
    event_uid: str
    algorithm_version: str
    run_uuid: str
    utterance_uuid: str
    ordinal: int
    assigned_at: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProjectionCommitResult:
    status: Literal["committed", "duplicate"]
    inserted_assignments: int
    inserted_memberships: int


class ProjectionConflictError(RuntimeError):
    """Raised when one algorithm version tries to rewrite derived history."""


_CREATE_ASSIGNMENT_RUN = """
CREATE TABLE IF NOT EXISTS assignment_run (
    run_uuid TEXT PRIMARY KEY,
    algorithm_version TEXT NOT NULL,
    algorithm_config_hash TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    input_cutoff TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    assignment_count INTEGER NOT NULL,
    UNIQUE (run_uuid, algorithm_version),
    UNIQUE (algorithm_version, input_digest)
)
"""

_CREATE_TOPIC_BLOCK_IDENTITY = """
CREATE TABLE IF NOT EXISTS topic_block_identity (
    block_uuid TEXT PRIMARY KEY,
    algorithm_version TEXT NOT NULL,
    group_id TEXT NOT NULL,
    seed_event_uid TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (block_uuid, algorithm_version),
    UNIQUE (algorithm_version, group_id, seed_event_uid)
)
"""

_CREATE_TOPIC_ASSIGNMENT = """
CREATE TABLE IF NOT EXISTS topic_assignment (
    event_uid TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    run_uuid TEXT NOT NULL,
    block_uuid TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    score REAL,
    runner_up_margin REAL,
    reply_predecessor_event_uid TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (event_uid, algorithm_version),
    FOREIGN KEY (run_uuid, algorithm_version)
        REFERENCES assignment_run(run_uuid, algorithm_version),
    FOREIGN KEY (block_uuid, algorithm_version)
        REFERENCES topic_block_identity(block_uuid, algorithm_version)
)
"""

_CREATE_UTTERANCE_MEMBERSHIP = """
CREATE TABLE IF NOT EXISTS utterance_membership (
    event_uid TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    run_uuid TEXT NOT NULL,
    utterance_uuid TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    assigned_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (event_uid, algorithm_version),
    UNIQUE (utterance_uuid, algorithm_version, ordinal),
    FOREIGN KEY (run_uuid, algorithm_version)
        REFERENCES assignment_run(run_uuid, algorithm_version),
    FOREIGN KEY (event_uid, algorithm_version)
        REFERENCES topic_assignment(event_uid, algorithm_version)
)
"""

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_assignment_run_version ON assignment_run(algorithm_version, completed_at)",
    "CREATE INDEX IF NOT EXISTS idx_topic_block_group ON topic_block_identity(algorithm_version, group_id)",
    "CREATE INDEX IF NOT EXISTS idx_topic_assignment_block ON topic_assignment(algorithm_version, block_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_topic_assignment_run ON topic_assignment(run_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_utterance_membership_uuid "
    "ON utterance_membership(algorithm_version, utterance_uuid, ordinal)",
)

_V1_DDL = "\n".join(
    (
        _CREATE_ASSIGNMENT_RUN,
        _CREATE_TOPIC_BLOCK_IDENTITY,
        _CREATE_TOPIC_ASSIGNMENT,
        _CREATE_UTTERANCE_MEMBERSHIP,
        *_CREATE_INDEXES,
    )
)
_V1_DDL_CHECKSUM = "sha256:" + hashlib.sha256(_V1_DDL.encode()).hexdigest()
if _V1_DDL_CHECKSUM != RESEARCH_TOPIC_ASSIGNMENTS_V1_MIGRATION_CHECKSUM:
    raise RuntimeError("topic assignment v1 DDL changed without a migration version bump")


async def _apply_v1(db: aiosqlite.Connection) -> None:
    await db.execute(_CREATE_ASSIGNMENT_RUN)
    await db.execute(_CREATE_TOPIC_BLOCK_IDENTITY)
    await db.execute(_CREATE_TOPIC_ASSIGNMENT)
    await db.execute(_CREATE_UTTERANCE_MEMBERSHIP)
    for statement in _CREATE_INDEXES:
        await db.execute(statement)


async def _verify_v1(db: aiosqlite.Connection) -> bool:
    return bool(
        await verify_catalog_schema_async(
            "research_topic_assignments",
            db,
            1,
        )
    )


_TOPIC_ASSIGNMENT_V1 = Migration(
    version=1,
    name=RESEARCH_TOPIC_ASSIGNMENTS_V1_MIGRATION_NAME,
    checksum=RESEARCH_TOPIC_ASSIGNMENTS_V1_MIGRATION_CHECKSUM,
    apply=_apply_v1,
    verify=_verify_v1,
)


class TopicAssignmentStore:
    """Own the independent, rebuildable Phase 2 derived database."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if self._db is not None:
            return
        await MigrationRunner(
            db_path=self._db_path,
            db_id="research_topic_assignments",
        ).ensure((_TOPIC_ASSIGNMENT_V1,))
        self._db_path.chmod(0o600)
        self._db = await connect_sqlite(self._db_path)

    async def close(self) -> None:
        db = self._db
        self._db = None
        await close_with_checkpoint(db, name="research_topic_assignments")

    async def commit_projection(
        self,
        *,
        run: AssignmentRun,
        blocks: Sequence[TopicBlockIdentity],
        assignments: Sequence[TopicAssignment],
        memberships: Sequence[UtteranceMembership],
    ) -> ProjectionCommitResult:
        _validate_projection_batch(
            run=run,
            blocks=blocks,
            assignments=assignments,
            memberships=memberships,
        )
        db = self._require_db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            existing_run = await _find_snapshot_run(
                db,
                algorithm_version=run.algorithm_version,
                input_digest=run.input_digest,
            )
            if existing_run is not None:
                await _verify_existing_projection(
                    db,
                    run=run,
                    existing_run=existing_run,
                    blocks=blocks,
                    assignments=assignments,
                    memberships=memberships,
                )
                await db.commit()
                return ProjectionCommitResult(
                    status="duplicate",
                    inserted_assignments=0,
                    inserted_memberships=0,
                )
            await db.execute(
                """
                INSERT INTO assignment_run (
                    run_uuid, algorithm_version, algorithm_config_hash,
                    input_digest, input_cutoff, started_at, completed_at,
                    status, event_count, assignment_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_uuid,
                    run.algorithm_version,
                    run.algorithm_config_hash,
                    run.input_digest,
                    run.input_cutoff,
                    run.started_at,
                    run.completed_at,
                    run.status,
                    run.event_count,
                    run.assignment_count,
                ),
            )
            inserted_assignments = 0
            inserted_memberships = 0
            for block in blocks:
                await _ensure_block_identity(db, block)
            for assignment in assignments:
                if await _ensure_assignment(db, assignment):
                    inserted_assignments += 1
            for membership in memberships:
                if await _ensure_membership(db, membership):
                    inserted_memberships += 1
            await db.commit()
        except BaseException:
            await _shielded_rollback(db)
            raise
        return ProjectionCommitResult(
            status="committed",
            inserted_assignments=inserted_assignments,
            inserted_memberships=inserted_memberships,
        )

    async def list_assignments(self, algorithm_version: str) -> list[TopicAssignment]:
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT * FROM topic_assignment
            WHERE algorithm_version = ?
            ORDER BY event_uid
            """,
            (algorithm_version,),
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        return [_assignment_from_row(row) for row in rows]

    async def list_utterance_members(
        self,
        algorithm_version: str,
        utterance_uuid: str,
    ) -> list[UtteranceMembership]:
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT * FROM utterance_membership
            WHERE algorithm_version = ? AND utterance_uuid = ?
            ORDER BY ordinal
            """,
            (algorithm_version, utterance_uuid),
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        return [_membership_from_row(row) for row in rows]

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("TopicAssignmentStore is not initialized")
        return self._db


async def _shielded_rollback(db: aiosqlite.Connection) -> None:
    task = asyncio.create_task(db.rollback())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task


def _json_dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _assignment_from_row(row: aiosqlite.Row) -> TopicAssignment:
    return TopicAssignment(
        event_uid=str(row["event_uid"]),
        algorithm_version=str(row["algorithm_version"]),
        run_uuid=str(row["run_uuid"]),
        block_uuid=str(row["block_uuid"]),
        assigned_at=str(row["assigned_at"]),
        reason=str(row["reason"]),
        score=float(row["score"]) if row["score"] is not None else None,
        runner_up_margin=(
            float(row["runner_up_margin"])
            if row["runner_up_margin"] is not None
            else None
        ),
        reply_predecessor_event_uid=(
            str(row["reply_predecessor_event_uid"])
            if row["reply_predecessor_event_uid"] is not None
            else None
        ),
        evidence=json.loads(str(row["evidence_json"])),
    )


def _membership_from_row(row: aiosqlite.Row) -> UtteranceMembership:
    return UtteranceMembership(
        event_uid=str(row["event_uid"]),
        algorithm_version=str(row["algorithm_version"]),
        run_uuid=str(row["run_uuid"]),
        utterance_uuid=str(row["utterance_uuid"]),
        ordinal=int(row["ordinal"]),
        assigned_at=str(row["assigned_at"]),
        reason=str(row["reason"]),
    )


def _validate_projection_batch(
    *,
    run: AssignmentRun,
    blocks: Sequence[TopicBlockIdentity],
    assignments: Sequence[TopicAssignment],
    memberships: Sequence[UtteranceMembership],
) -> None:
    if run.status != "completed":
        raise ValueError("only completed assignment runs can be committed")
    if run.event_count != len(assignments) or run.event_count != len(memberships):
        raise ValueError("event_count must match assignment and membership counts")
    if run.assignment_count != len(assignments):
        raise ValueError("assignment_count must match assignments")

    assignment_events = [assignment.event_uid for assignment in assignments]
    membership_events = [membership.event_uid for membership in memberships]
    if len(set(assignment_events)) != len(assignment_events):
        raise ValueError("assignment event_uid values must be unique")
    if len(set(membership_events)) != len(membership_events):
        raise ValueError("membership event_uid values must be unique")
    if set(assignment_events) != set(membership_events):
        raise ValueError("assignment and membership event sets must match")

    block_keys = [(block.algorithm_version, block.group_id, block.seed_event_uid) for block in blocks]
    block_uuids = [(block.algorithm_version, block.block_uuid) for block in blocks]
    if len(set(block_keys)) != len(block_keys) or len(set(block_uuids)) != len(block_uuids):
        raise ValueError("topic block identities must be unique")
    if any(block.algorithm_version != run.algorithm_version for block in blocks):
        raise ValueError("block algorithm_version must match run")
    if any(block.seed_event_uid not in set(assignment_events) for block in blocks):
        raise ValueError("block seed_event_uid must belong to the projected snapshot")

    available_blocks = {block.block_uuid for block in blocks}
    for assignment in assignments:
        if assignment.algorithm_version != run.algorithm_version:
            raise ValueError("assignment algorithm_version must match run")
        if assignment.run_uuid != run.run_uuid:
            raise ValueError("assignment run_uuid must match run")
        if assignment.block_uuid not in available_blocks:
            raise ValueError("assignment block_uuid must be declared in the projection")
        predecessor = assignment.reply_predecessor_event_uid
        if predecessor is not None and predecessor not in set(assignment_events):
            raise ValueError("reply predecessor must belong to the projected snapshot")

    utterance_positions: set[tuple[str, int]] = set()
    for membership in memberships:
        if membership.algorithm_version != run.algorithm_version:
            raise ValueError("membership algorithm_version must match run")
        if membership.run_uuid != run.run_uuid:
            raise ValueError("membership run_uuid must match run")
        if membership.ordinal < 0:
            raise ValueError("membership ordinal must be non-negative")
        position = (membership.utterance_uuid, membership.ordinal)
        if position in utterance_positions:
            raise ValueError("utterance ordinal must be unique within the projection")
        utterance_positions.add(position)


async def _find_snapshot_run(
    db: aiosqlite.Connection,
    *,
    algorithm_version: str,
    input_digest: str,
) -> aiosqlite.Row | None:
    cursor = await db.execute(
        """
        SELECT * FROM assignment_run
        WHERE algorithm_version = ? AND input_digest = ?
        """,
        (algorithm_version, input_digest),
    )
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


async def _verify_existing_projection(
    db: aiosqlite.Connection,
    *,
    run: AssignmentRun,
    existing_run: aiosqlite.Row,
    blocks: Sequence[TopicBlockIdentity],
    assignments: Sequence[TopicAssignment],
    memberships: Sequence[UtteranceMembership],
) -> None:
    existing_run_semantics = (
        str(existing_run["algorithm_config_hash"]),
        str(existing_run["status"]),
        int(existing_run["event_count"]),
        int(existing_run["assignment_count"]),
    )
    candidate_run_semantics = (
        run.algorithm_config_hash,
        run.status,
        run.event_count,
        run.assignment_count,
    )
    if existing_run_semantics != candidate_run_semantics:
        raise ProjectionConflictError("assignment run metadata conflicts with existing snapshot")

    for block in blocks:
        row = await _fetchone(
            db,
            """
            SELECT * FROM topic_block_identity
            WHERE algorithm_version = ? AND group_id = ? AND seed_event_uid = ?
            """,
            (block.algorithm_version, block.group_id, block.seed_event_uid),
        )
        if row is None or (
            str(row["block_uuid"]),
            str(row["algorithm_version"]),
            str(row["group_id"]),
            str(row["seed_event_uid"]),
        ) != (
            block.block_uuid,
            block.algorithm_version,
            block.group_id,
            block.seed_event_uid,
        ):
            raise ProjectionConflictError("topic block identity conflicts with existing snapshot")

    for assignment in assignments:
        row = await _fetchone(
            db,
            """
            SELECT * FROM topic_assignment
            WHERE event_uid = ? AND algorithm_version = ?
            """,
            (assignment.event_uid, assignment.algorithm_version),
        )
        if row is None or _assignment_semantics_from_row(row) != _assignment_semantics(assignment):
            raise ProjectionConflictError(
                f"topic assignment conflicts for event {assignment.event_uid}"
            )

    for membership in memberships:
        row = await _fetchone(
            db,
            """
            SELECT * FROM utterance_membership
            WHERE event_uid = ? AND algorithm_version = ?
            """,
            (membership.event_uid, membership.algorithm_version),
        )
        if row is None or _membership_semantics_from_row(row) != _membership_semantics(membership):
            raise ProjectionConflictError(
                f"utterance membership conflicts for event {membership.event_uid}"
            )


async def _fetchone(
    db: aiosqlite.Connection,
    sql: str,
    parameters: tuple[object, ...],
) -> aiosqlite.Row | None:
    cursor = await db.execute(sql, parameters)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


def _assignment_semantics(assignment: TopicAssignment) -> tuple[object, ...]:
    return (
        assignment.block_uuid,
        assignment.reason,
        assignment.score,
        assignment.runner_up_margin,
        assignment.reply_predecessor_event_uid,
        _json_dumps(assignment.evidence),
    )


def _assignment_semantics_from_row(row: aiosqlite.Row) -> tuple[object, ...]:
    return (
        str(row["block_uuid"]),
        str(row["reason"]),
        float(row["score"]) if row["score"] is not None else None,
        float(row["runner_up_margin"]) if row["runner_up_margin"] is not None else None,
        (
            str(row["reply_predecessor_event_uid"])
            if row["reply_predecessor_event_uid"] is not None
            else None
        ),
        _json_dumps(json.loads(str(row["evidence_json"]))),
    )


def _membership_semantics(membership: UtteranceMembership) -> tuple[object, ...]:
    return (membership.utterance_uuid, membership.ordinal, membership.reason)


def _membership_semantics_from_row(row: aiosqlite.Row) -> tuple[object, ...]:
    return (str(row["utterance_uuid"]), int(row["ordinal"]), str(row["reason"]))


async def _ensure_block_identity(
    db: aiosqlite.Connection,
    block: TopicBlockIdentity,
) -> None:
    row = await _fetchone(
        db,
        """
        SELECT * FROM topic_block_identity
        WHERE algorithm_version = ? AND group_id = ? AND seed_event_uid = ?
        """,
        (block.algorithm_version, block.group_id, block.seed_event_uid),
    )
    if row is not None:
        if (
            str(row["block_uuid"]),
            str(row["algorithm_version"]),
            str(row["group_id"]),
            str(row["seed_event_uid"]),
        ) != (
            block.block_uuid,
            block.algorithm_version,
            block.group_id,
            block.seed_event_uid,
        ):
            raise ProjectionConflictError("topic block seed maps to a different stable UUID")
        return

    uuid_row = await _fetchone(
        db,
        """
        SELECT * FROM topic_block_identity
        WHERE block_uuid = ? AND algorithm_version = ?
        """,
        (block.block_uuid, block.algorithm_version),
    )
    if uuid_row is not None:
        raise ProjectionConflictError("stable block UUID maps to a different seed")
    await db.execute(
        """
        INSERT INTO topic_block_identity (
            block_uuid, algorithm_version, group_id, seed_event_uid, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            block.block_uuid,
            block.algorithm_version,
            block.group_id,
            block.seed_event_uid,
            block.created_at,
        ),
    )


async def _ensure_assignment(
    db: aiosqlite.Connection,
    assignment: TopicAssignment,
) -> bool:
    row = await _fetchone(
        db,
        """
        SELECT * FROM topic_assignment
        WHERE event_uid = ? AND algorithm_version = ?
        """,
        (assignment.event_uid, assignment.algorithm_version),
    )
    if row is not None:
        if _assignment_semantics_from_row(row) != _assignment_semantics(assignment):
            raise ProjectionConflictError(
                f"topic assignment conflicts for event {assignment.event_uid}"
            )
        return False
    await db.execute(
        """
        INSERT INTO topic_assignment (
            event_uid, algorithm_version, run_uuid, block_uuid,
            assigned_at, reason, score, runner_up_margin,
            reply_predecessor_event_uid, evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assignment.event_uid,
            assignment.algorithm_version,
            assignment.run_uuid,
            assignment.block_uuid,
            assignment.assigned_at,
            assignment.reason,
            assignment.score,
            assignment.runner_up_margin,
            assignment.reply_predecessor_event_uid,
            _json_dumps(assignment.evidence),
        ),
    )
    return True


async def _ensure_membership(
    db: aiosqlite.Connection,
    membership: UtteranceMembership,
) -> bool:
    row = await _fetchone(
        db,
        """
        SELECT * FROM utterance_membership
        WHERE event_uid = ? AND algorithm_version = ?
        """,
        (membership.event_uid, membership.algorithm_version),
    )
    if row is not None:
        if _membership_semantics_from_row(row) != _membership_semantics(membership):
            raise ProjectionConflictError(
                f"utterance membership conflicts for event {membership.event_uid}"
            )
        return False
    await db.execute(
        """
        INSERT INTO utterance_membership (
            event_uid, algorithm_version, run_uuid, utterance_uuid,
            ordinal, assigned_at, reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            membership.event_uid,
            membership.algorithm_version,
            membership.run_uuid,
            membership.utterance_uuid,
            membership.ordinal,
            membership.assigned_at,
            membership.reason,
        ),
    )
    return True
