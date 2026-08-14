"""Explicit-path append-only store for governed Worldbook proposals."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from services.memory.governance_contracts import EvidenceAtomV1
from services.storage import connect_sqlite
from services.storage.catalog import ConnectionProfile
from services.storage.migrations import Migration, MigrationRunner
from services.worldbook.domain import EventRecord
from services.worldbook.governance_contracts import (
    ScheduleSourceBindingV1,
    SocialSourceBindingV1,
    WorldbookCommitReceiptV1,
    WorldbookEventProposalV1,
    WorldbookEventSource,
    WorldbookOperatorDecisionV1,
    WorldRefV1,
    canonical_worldbook_json,
    worldbook_sha256,
)

_DB_ID = "worldbook_governance_shadow"
_MIGRATION_LEDGER_TABLE = "_omubot_schema_migrations"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

_CREATE_PROPOSALS = """
CREATE TABLE worldbook_governance_proposals (
    proposal_seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id           TEXT NOT NULL UNIQUE,
    proposal_sha256       TEXT NOT NULL UNIQUE,
    world_id              TEXT NOT NULL,
    source_kind           TEXT NOT NULL,
    event_id              TEXT NOT NULL UNIQUE,
    proposed_at           TEXT NOT NULL,
    payload_json          TEXT NOT NULL,
    recorded_at           TEXT NOT NULL
)
"""

_CREATE_DECISIONS = """
CREATE TABLE worldbook_governance_decisions (
    decision_seq   INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id    TEXT NOT NULL UNIQUE,
    proposal_id    TEXT NOT NULL UNIQUE,
    record_sha256  TEXT NOT NULL UNIQUE,
    decision       TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    decided_at     TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    recorded_at    TEXT NOT NULL,
    FOREIGN KEY(proposal_id)
        REFERENCES worldbook_governance_proposals(proposal_id)
)
"""

_CREATE_RECEIPTS = """
CREATE TABLE worldbook_governance_receipts (
    receipt_seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id     TEXT NOT NULL UNIQUE,
    receipt_sha256  TEXT NOT NULL UNIQUE,
    event_id        TEXT NOT NULL UNIQUE,
    payload_json    TEXT NOT NULL,
    recorded_at     TEXT NOT NULL,
    FOREIGN KEY(proposal_id)
        REFERENCES worldbook_governance_proposals(proposal_id)
)
"""

_CREATE_INDEXES = (
    "CREATE INDEX idx_worldbook_governance_proposals_world_source_seq "
    "ON worldbook_governance_proposals(world_id, source_kind, proposal_seq)",
    "CREATE INDEX idx_worldbook_governance_decisions_proposal "
    "ON worldbook_governance_decisions(proposal_id, decision)",
    "CREATE INDEX idx_worldbook_governance_receipts_proposal "
    "ON worldbook_governance_receipts(proposal_id)",
)

_TRUTH_TABLES = (
    "worldbook_governance_proposals",
    "worldbook_governance_decisions",
    "worldbook_governance_receipts",
)


def _immutable_triggers() -> tuple[str, ...]:
    statements: list[str] = []
    for table in _TRUTH_TABLES:
        statements.extend(
            (
                f"""
                CREATE TRIGGER deny_update_{table}
                BEFORE UPDATE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'worldbook governance truth is append-only');
                END
                """,
                f"""
                CREATE TRIGGER deny_delete_{table}
                BEFORE DELETE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'worldbook governance truth is append-only');
                END
                """,
            )
        )
    return tuple(statements)


_CREATE_TRIGGERS = _immutable_triggers()
_SCHEMA_STATEMENTS = (
    _CREATE_PROPOSALS,
    _CREATE_DECISIONS,
    _CREATE_RECEIPTS,
    *_CREATE_INDEXES,
    *_CREATE_TRIGGERS,
)
_MIGRATION_V1_CHECKSUM = "sha256:" + hashlib.sha256(
    "\n".join(_SCHEMA_STATEMENTS).encode("utf-8")
).hexdigest()


def _normalized_sql(value: str) -> str:
    return " ".join(value.split())


def _expected_schema() -> dict[tuple[str, str], str]:
    connection = sqlite3.connect(":memory:")
    try:
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)
        return {
            (str(row[0]), str(row[1])): _normalized_sql(str(row[2]))
            for row in connection.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE type IN ('table', 'index', 'trigger') "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        connection.close()


_EXPECTED_SCHEMA = _expected_schema()


async def _apply_v1(db: aiosqlite.Connection) -> None:
    for statement in _SCHEMA_STATEMENTS:
        await db.execute(statement)


async def _verify_v1(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE type IN ('table', 'index', 'view', 'trigger') "
        "AND name NOT LIKE 'sqlite_%'"
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    actual_names = {(str(row["type"]), str(row["name"])) for row in rows}
    expected_names = {*_EXPECTED_SCHEMA, ("table", _MIGRATION_LEDGER_TABLE)}
    if actual_names != expected_names:
        return False
    actual_sql = {
        (str(row["type"]), str(row["name"])): _normalized_sql(str(row["sql"]))
        for row in rows
        if str(row["name"]) != _MIGRATION_LEDGER_TABLE
    }
    if actual_sql != _EXPECTED_SCHEMA:
        return False
    cursor = await db.execute(f"PRAGMA table_info({_MIGRATION_LEDGER_TABLE})")
    try:
        migration_columns = {str(row["name"]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    return {
        "db_id",
        "version",
        "name",
        "checksum",
        "applied_at",
        "adopted",
    } == migration_columns


_MIGRATION_V1 = Migration(
    version=1,
    name="create_worldbook_governance_shadow_store",
    checksum=_MIGRATION_V1_CHECKSUM,
    apply=_apply_v1,
    verify=_verify_v1,
)


class WorldbookGovernanceStore:
    """Durable Worldbook governance truth; initialization is always explicit."""

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
            try:
                await _verify_all_rows(db)
            except BaseException:
                await _shielded_close(db)
                raise
            async with self._write_lock:
                self._db = db

    async def close(self) -> None:
        async with self._lifecycle_lock, self._write_lock:
            db = self._db
            self._db = None
            if db is not None:
                await _shielded_close(db)

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("WorldbookGovernanceStore is not initialized")
        return self._db

    async def append_proposal(
        self,
        proposal: WorldbookEventProposalV1,
    ) -> WorldbookEventProposalV1:
        canonical = _validated_proposal(proposal)
        payload = canonical_worldbook_json(canonical.to_dict())
        async with self._write_lock:
            db = self._require_db()
            try:
                await _begin_immediate(db)
                existing = await _row_for_proposal(db, canonical.proposal_id)
                if existing is None:
                    await db.execute(
                        """
                        INSERT INTO worldbook_governance_proposals (
                            proposal_id, proposal_sha256, world_id, source_kind,
                            event_id, proposed_at, payload_json, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            canonical.proposal_id,
                            canonical.proposal_sha256,
                            canonical.world_ref.world_id,
                            canonical.source.value,
                            canonical.event.event_id,
                            _iso(canonical.proposed_at),
                            payload,
                            _now_iso(),
                        ),
                    )
                elif _proposal_from_row(existing) != canonical:
                    raise ValueError("Worldbook proposal identity collision")
                await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise
        return canonical

    async def get_proposal(
        self,
        proposal_id: str,
    ) -> WorldbookEventProposalV1 | None:
        async with self._write_lock:
            row = await _row_for_proposal(self._require_db(), str(proposal_id))
        return _proposal_from_row(row) if row is not None else None

    async def append_operator_decision(
        self,
        decision: WorldbookOperatorDecisionV1,
    ) -> WorldbookOperatorDecisionV1:
        canonical = _validated_decision(decision)
        payload = canonical_worldbook_json(canonical.to_dict())
        async with self._write_lock:
            db = self._require_db()
            try:
                await _begin_immediate(db)
                proposal = await _row_for_proposal(db, canonical.proposal_id)
                if proposal is None:
                    raise ValueError("Worldbook decision proposal does not exist")
                existing = await _row_for_decision(db, canonical.proposal_id)
                if existing is None:
                    await db.execute(
                        """
                        INSERT INTO worldbook_governance_decisions (
                            decision_id, proposal_id, record_sha256, decision,
                            decided_at, payload_json, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            canonical.decision_id,
                            canonical.proposal_id,
                            canonical.record_sha256,
                            canonical.decision,
                            _iso(canonical.decided_at),
                            payload,
                            _now_iso(),
                        ),
                    )
                elif _decision_from_row(existing) != canonical:
                    raise ValueError("Worldbook proposal already has another decision")
                await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise
        return canonical

    async def get_operator_decision(
        self,
        proposal_id: str,
    ) -> WorldbookOperatorDecisionV1 | None:
        async with self._write_lock:
            row = await _row_for_decision(self._require_db(), str(proposal_id))
        return _decision_from_row(row) if row is not None else None

    async def append_commit_receipt(
        self,
        receipt: WorldbookCommitReceiptV1,
    ) -> WorldbookCommitReceiptV1:
        canonical = _validated_receipt(receipt)
        payload = canonical_worldbook_json(canonical.to_dict())
        async with self._write_lock:
            db = self._require_db()
            try:
                await _begin_immediate(db)
                proposal_row = await _row_for_proposal(db, canonical.proposal_id)
                if proposal_row is None:
                    raise ValueError("Worldbook receipt proposal does not exist")
                proposal = _proposal_from_row(proposal_row)
                decision_row = await _row_for_decision(db, canonical.proposal_id)
                if decision_row is None or _decision_from_row(decision_row).decision != "approve":
                    raise ValueError("Worldbook receipt requires an approved proposal")
                _verify_receipt_binding(canonical, proposal)
                existing = await _row_for_receipt(db, canonical.proposal_id)
                if existing is None:
                    await db.execute(
                        """
                        INSERT INTO worldbook_governance_receipts (
                            proposal_id, receipt_sha256, event_id,
                            payload_json, recorded_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            canonical.proposal_id,
                            canonical.receipt_sha256,
                            canonical.event_id,
                            payload,
                            _now_iso(),
                        ),
                    )
                elif _receipt_from_row(existing) != canonical:
                    raise ValueError("Worldbook proposal already has another receipt")
                await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise
        return canonical

    async def get_commit_receipt(
        self,
        proposal_id: str,
    ) -> WorldbookCommitReceiptV1 | None:
        async with self._write_lock:
            row = await _row_for_receipt(self._require_db(), str(proposal_id))
        return _receipt_from_row(row) if row is not None else None


async def _begin_immediate(db: aiosqlite.Connection) -> None:
    while True:
        try:
            await db.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            await asyncio.sleep(0.01)


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


async def _shielded_rollback(db: aiosqlite.Connection) -> None:
    _, cancellation = await _await_cleanup_task(
        asyncio.create_task(db.rollback(), name="worldbook-governance-rollback")
    )
    if cancellation is not None:
        raise cancellation


async def _shielded_close(db: aiosqlite.Connection) -> None:
    _, cancellation = await _await_cleanup_task(
        asyncio.create_task(db.close(), name="worldbook-governance-close")
    )
    if cancellation is not None:
        raise cancellation


async def _row_for_proposal(db: aiosqlite.Connection, proposal_id: str) -> Any | None:
    return await _fetch_one(
        db,
        "SELECT * FROM worldbook_governance_proposals WHERE proposal_id = ?",
        (proposal_id,),
    )


async def _row_for_decision(db: aiosqlite.Connection, proposal_id: str) -> Any | None:
    return await _fetch_one(
        db,
        "SELECT * FROM worldbook_governance_decisions WHERE proposal_id = ?",
        (proposal_id,),
    )


async def _row_for_receipt(db: aiosqlite.Connection, proposal_id: str) -> Any | None:
    return await _fetch_one(
        db,
        "SELECT * FROM worldbook_governance_receipts WHERE proposal_id = ?",
        (proposal_id,),
    )


async def _fetch_one(
    db: aiosqlite.Connection,
    query: str,
    parameters: tuple[Any, ...],
) -> Any | None:
    cursor = await db.execute(query, parameters)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


async def _verify_all_rows(db: aiosqlite.Connection) -> None:
    for table, parser in (
        ("worldbook_governance_proposals", _proposal_from_row),
        ("worldbook_governance_decisions", _decision_from_row),
        ("worldbook_governance_receipts", _receipt_from_row),
    ):
        cursor = await db.execute(f"SELECT * FROM {table}")
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        for row in rows:
            parser(row)
    cursor = await db.execute(
        """
        SELECT proposals.proposal_id
        FROM worldbook_governance_proposals AS proposals
        LEFT JOIN worldbook_governance_decisions AS decisions
          ON decisions.proposal_id = proposals.proposal_id
        LEFT JOIN worldbook_governance_receipts AS receipts
          ON receipts.proposal_id = proposals.proposal_id
        WHERE receipts.proposal_id IS NOT NULL
          AND (decisions.decision IS NULL OR decisions.decision != 'approve')
        LIMIT 1
        """
    )
    try:
        if await cursor.fetchone() is not None:
            raise ValueError("Worldbook receipt lifecycle is invalid")
    finally:
        await cursor.close()


def _validated_proposal(value: object) -> WorldbookEventProposalV1:
    if not isinstance(value, WorldbookEventProposalV1):
        raise TypeError("proposal must be WorldbookEventProposalV1")
    canonical = _proposal_from_mapping(value.to_dict())
    if canonical != value:
        raise ValueError("Worldbook proposal is not canonical")
    return canonical


def _validated_decision(value: object) -> WorldbookOperatorDecisionV1:
    if not isinstance(value, WorldbookOperatorDecisionV1):
        raise TypeError("decision must be WorldbookOperatorDecisionV1")
    canonical = _decision_from_mapping(value.to_dict())
    if canonical != value:
        raise ValueError("Worldbook decision is not canonical")
    return canonical


def _validated_receipt(value: object) -> WorldbookCommitReceiptV1:
    if not isinstance(value, WorldbookCommitReceiptV1):
        raise TypeError("receipt must be WorldbookCommitReceiptV1")
    canonical = _receipt_from_mapping(value.to_dict())
    if canonical != value:
        raise ValueError("Worldbook receipt is not canonical")
    return canonical


def _proposal_from_row(row: Any) -> WorldbookEventProposalV1:
    proposal = _proposal_from_mapping(_json_mapping(str(row["payload_json"])))
    if (
        str(row["proposal_id"]) != proposal.proposal_id
        or str(row["proposal_sha256"]) != proposal.proposal_sha256
        or str(row["world_id"]) != proposal.world_ref.world_id
        or str(row["source_kind"]) != proposal.source.value
        or str(row["event_id"]) != proposal.event.event_id
        or str(row["proposed_at"]) != _iso(proposal.proposed_at)
    ):
        raise ValueError("stored Worldbook proposal columns do not match payload")
    return proposal


def _decision_from_row(row: Any) -> WorldbookOperatorDecisionV1:
    decision = _decision_from_mapping(_json_mapping(str(row["payload_json"])))
    if (
        str(row["decision_id"]) != decision.decision_id
        or str(row["proposal_id"]) != decision.proposal_id
        or str(row["record_sha256"]) != decision.record_sha256
        or str(row["decision"]) != decision.decision
        or str(row["decided_at"]) != _iso(decision.decided_at)
    ):
        raise ValueError("stored Worldbook decision columns do not match payload")
    return decision


def _receipt_from_row(row: Any) -> WorldbookCommitReceiptV1:
    receipt = _receipt_from_mapping(_json_mapping(str(row["payload_json"])))
    if (
        str(row["proposal_id"]) != receipt.proposal_id
        or str(row["receipt_sha256"]) != receipt.receipt_sha256
        or str(row["event_id"]) != receipt.event_id
    ):
        raise ValueError("stored Worldbook receipt columns do not match payload")
    return receipt


def _proposal_from_mapping(data: Mapping[str, Any]) -> WorldbookEventProposalV1:
    world_raw = _mapping(data.get("world_ref"), "world_ref")
    world_ref = WorldRefV1(
        world_id=str(world_raw.get("world_id") or ""),
        arc_id=str(world_raw.get("arc_id") or ""),
    )
    source = WorldbookEventSource(str(data.get("source") or ""))
    binding_raw = _mapping(data.get("source_binding"), "source_binding")
    if source is WorldbookEventSource.SCHEDULE:
        binding = ScheduleSourceBindingV1(
            schedule_date=str(binding_raw.get("schedule_date") or ""),
            summary_sha256=str(binding_raw.get("summary_sha256") or ""),
        )
    else:
        binding = SocialSourceBindingV1(
            experience_id=str(binding_raw.get("experience_id") or ""),
            group_id=str(binding_raw.get("group_id") or ""),
            user_id=str(binding_raw.get("user_id") or ""),
            evidence_message_id=str(binding_raw.get("evidence_message_id") or ""),
            evidence_source=str(binding_raw.get("evidence_source") or ""),
            evidence_time=str(binding_raw.get("evidence_time") or ""),
        )
    evidence_values = data.get("evidence")
    if not isinstance(evidence_values, list):
        raise TypeError("stored Worldbook evidence must be a list")
    evidence = tuple(_evidence_from_mapping(item) for item in evidence_values)
    event = EventRecord.from_dict(_mapping(data.get("event"), "event"))
    proposal = WorldbookEventProposalV1.create(
        world_ref=world_ref,
        source=source,
        source_ref=str(data.get("source_ref") or ""),
        source_binding=binding,
        evidence=evidence,
        event=event,
        proposed_at=str(data.get("proposed_at") or ""),
    )
    if proposal.to_dict() != dict(data):
        raise ValueError("stored Worldbook proposal payload is not canonical")
    return proposal


def _decision_from_mapping(data: Mapping[str, Any]) -> WorldbookOperatorDecisionV1:
    decision = WorldbookOperatorDecisionV1.create(
        proposal_id=str(data.get("proposal_id") or ""),
        decision=str(data.get("decision") or ""),
        reason_code=str(data.get("reason_code") or ""),
        operator_ref=str(data.get("operator_ref") or ""),
        decided_at=str(data.get("decided_at") or ""),
    )
    if decision.to_dict() != dict(data):
        raise ValueError("stored Worldbook decision payload is not canonical")
    return decision


def _receipt_from_mapping(data: Mapping[str, Any]) -> WorldbookCommitReceiptV1:
    world_raw = _mapping(data.get("world_ref"), "world_ref")
    world_ref = WorldRefV1(
        world_id=str(world_raw.get("world_id") or ""),
        arc_id=str(world_raw.get("arc_id") or ""),
    )
    digests = {
        field: _stored_digest(data.get(field), field)
        for field in (
            "proposal_sha256",
            "persisted_event_sha256",
            "persisted_arc_sha256",
            "committed_event_ids_sha256",
            "receipt_sha256",
        )
    }
    persisted_revision = data.get("persisted_revision")
    if isinstance(persisted_revision, bool) or not isinstance(persisted_revision, int):
        raise TypeError("stored persisted_revision must be an integer")
    receipt = object.__new__(WorldbookCommitReceiptV1)
    object.__setattr__(receipt, "world_ref", world_ref)
    object.__setattr__(receipt, "proposal_id", str(data.get("proposal_id") or ""))
    object.__setattr__(receipt, "proposal_sha256", digests["proposal_sha256"])
    object.__setattr__(receipt, "event_id", str(data.get("event_id") or ""))
    object.__setattr__(receipt, "arc_id", str(data.get("arc_id") or ""))
    object.__setattr__(receipt, "persisted_revision", persisted_revision)
    object.__setattr__(receipt, "contract_version", "worldbook.commit_receipt.v1")
    object.__setattr__(
        receipt,
        "persisted_event_sha256",
        digests["persisted_event_sha256"],
    )
    object.__setattr__(receipt, "persisted_arc_sha256", digests["persisted_arc_sha256"])
    object.__setattr__(
        receipt,
        "committed_event_ids_sha256",
        digests["committed_event_ids_sha256"],
    )
    object.__setattr__(receipt, "receipt_sha256", digests["receipt_sha256"])
    if receipt.receipt_sha256 != worldbook_sha256(receipt._identity_dict()):
        raise ValueError("stored Worldbook receipt digest is invalid")
    if receipt.to_dict() != dict(data):
        raise ValueError("stored Worldbook receipt payload is not canonical")
    return receipt


def _evidence_from_mapping(value: object) -> EvidenceAtomV1:
    data = _mapping(value, "evidence")
    return EvidenceAtomV1(
        evidence_ref=str(data.get("evidence_ref") or ""),
        content_sha256=str(data.get("content_sha256") or ""),
        quote=str(data.get("quote") or ""),
        actor_ref=str(data.get("actor_ref") or ""),
        occurred_at=data.get("occurred_at"),
    )


def _verify_receipt_binding(
    receipt: WorldbookCommitReceiptV1,
    proposal: WorldbookEventProposalV1,
) -> None:
    if (
        receipt.world_ref != proposal.world_ref
        or receipt.proposal_id != proposal.proposal_id
        or receipt.proposal_sha256 != proposal.proposal_sha256
        or receipt.event_id != proposal.event.event_id
        or receipt.arc_id != proposal.world_ref.arc_id
    ):
        raise ValueError("Worldbook receipt does not match its governed proposal")


def _json_mapping(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TypeError("stored Worldbook payload must be an object")
    return parsed


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"stored {field_name} must be an object")
    return value


def _stored_digest(value: object, field_name: str) -> str:
    digest = str(value or "").strip().lower()
    if _DIGEST_RE.fullmatch(digest) is None:
        raise ValueError(f"stored {field_name} is invalid")
    return digest


def _iso(value: datetime | str) -> str:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Worldbook timestamp must be timezone-aware")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["WorldbookGovernanceStore"]
