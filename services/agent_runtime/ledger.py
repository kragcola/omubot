"""Durable Agent Runtime ledger implementation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from services.storage import close_with_checkpoint, connect_sqlite
from services.storage.migrations import Migration, MigrationRunner

_DB_ID = "agent_runtime"
_TRIGGER_TYPES = frozenset({"message", "tick", "domain_event", "recovery"})
_RUN_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({
        "waiting_approval",
        "waiting_retry",
        "waiting_external",
        "succeeded",
        "failed",
        "cancelled",
    }),
    "waiting_approval": frozenset({"running", "failed", "cancelled"}),
    "waiting_retry": frozenset({"running", "failed", "cancelled"}),
    "waiting_external": frozenset({"running", "succeeded", "failed", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
_TOOL_EFFECTS = frozenset({
    "legacy_unclassified",
    "pure",
    "read",
    "external_read",
    "write_local",
    "external_reversible",
    "external_irreversible",
})
_TOOL_IDEMPOTENCY_MODES = frozenset({
    "not_needed",
    "required",
    "provider_supported",
    "reconcile_only",
})
_TOOL_CONCURRENCY_MODES = frozenset({
    "parallel",
    "keyed_serial",
    "global_serial",
})
_TOOL_CALL_TERMINAL = frozenset({
    "denied",
    "succeeded",
    "failed_terminal",
    "unknown",
    "cancelled",
})
_TOOL_CALL_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"denied", "approval_pending", "ready", "cancelled"}),
    "approval_pending": frozenset({"denied", "ready", "cancelled"}),
    "ready": frozenset({
        "claimed",
        "failed_retryable",
        "failed_terminal",
        "cancelled",
    }),
    "claimed": frozenset({
        "ready",
        "dispatching",
        "failed_retryable",
        "failed_terminal",
        "unknown",
        "cancelled",
    }),
    "dispatching": frozenset({
        "succeeded",
        "failed_retryable",
        "failed_terminal",
        "unknown",
    }),
    "failed_retryable": frozenset({"ready", "failed_terminal", "cancelled"}),
    "denied": frozenset(),
    "succeeded": frozenset(),
    "failed_terminal": frozenset(),
    "unknown": frozenset(),
    "cancelled": frozenset(),
}
_RECONCILIATION_DECISIONS = frozenset({
    "confirmed_succeeded",
    "confirmed_not_applied",
})

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id              TEXT PRIMARY KEY,
    trigger_type        TEXT NOT NULL
                        CHECK (trigger_type IN ('message', 'tick', 'domain_event', 'recovery')),
    trigger_ref         TEXT NOT NULL,
    principal_kind      TEXT NOT NULL,
    principal_id        TEXT NOT NULL,
    session_id          TEXT NOT NULL DEFAULT '',
    group_id            TEXT NOT NULL DEFAULT '',
    registry_generation INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL
                        CHECK (status IN (
                            'queued', 'running', 'waiting_approval',
                            'waiting_retry', 'waiting_external',
                            'succeeded', 'failed', 'cancelled'
                        )),
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    terminal_at         TEXT NOT NULL DEFAULT ''
)
"""

_CREATE_TOOL_CALLS = """
CREATE TABLE IF NOT EXISTS agent_tool_calls (
    call_id                 TEXT PRIMARY KEY,
    run_id                  TEXT NOT NULL,
    step_id                 TEXT NOT NULL,
    tool_name               TEXT NOT NULL,
    tool_version            TEXT NOT NULL,
    owner                    TEXT NOT NULL,
    effect                   TEXT NOT NULL,
    principal_kind           TEXT NOT NULL,
    principal_id             TEXT NOT NULL,
    target_ref               TEXT NOT NULL DEFAULT '',
    args_digest              TEXT NOT NULL,
    idempotency_mode         TEXT NOT NULL
                             CHECK (idempotency_mode IN (
                                 'not_needed', 'required',
                                 'provider_supported', 'reconcile_only'
                             )),
    idempotency_key_digest   TEXT NOT NULL DEFAULT '',
    approval_ref_digest      TEXT NOT NULL DEFAULT '',
    concurrency_key          TEXT NOT NULL DEFAULT '',
    status                   TEXT NOT NULL
                             CHECK (status IN (
                                 'proposed', 'denied', 'approval_pending',
                                 'ready', 'claimed', 'dispatching',
                                 'succeeded', 'failed_retryable',
                                 'failed_terminal', 'unknown', 'cancelled'
                             )),
    attempt_count            INTEGER NOT NULL DEFAULT 0,
    lease_owner              TEXT NOT NULL DEFAULT '',
    lease_until              TEXT NOT NULL DEFAULT '',
    safe_result_json         TEXT NOT NULL DEFAULT '{}',
    error_code               TEXT NOT NULL DEFAULT '',
    external_id              TEXT NOT NULL DEFAULT '',
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    terminal_at              TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
)
"""

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS agent_runtime_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL,
    call_id        TEXT NOT NULL DEFAULT '',
    entity_kind    TEXT NOT NULL CHECK (entity_kind IN ('run', 'tool_call')),
    event_type     TEXT NOT NULL,
    from_status    TEXT NOT NULL DEFAULT '',
    to_status      TEXT NOT NULL,
    actor          TEXT NOT NULL,
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    event_at       TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
)
"""

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_agent_calls_run ON agent_tool_calls(run_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_agent_calls_status ON agent_tool_calls(status, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_agent_events_run ON agent_runtime_events(run_id, event_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_events_call ON agent_runtime_events(call_id, event_id)",
)
_MIGRATION_V1_CHECKSUM = "sha256:" + hashlib.sha256(
    "\n".join(
        (_CREATE_RUNS, _CREATE_TOOL_CALLS, _CREATE_EVENTS, *_CREATE_INDEXES)
    ).encode("utf-8")
).hexdigest()
_V1_TABLES = ("agent_runs", "agent_tool_calls", "agent_runtime_events")

_V2_ADD_CONCURRENCY_MODE = """
ALTER TABLE agent_tool_calls
ADD COLUMN concurrency_mode TEXT NOT NULL DEFAULT 'global_serial'
CHECK (concurrency_mode IN ('parallel', 'keyed_serial', 'global_serial'))
"""
_V2_CREATE_CONCURRENCY_INDEX = """
CREATE INDEX idx_agent_calls_concurrency
ON agent_tool_calls(concurrency_mode, concurrency_key, status, lease_until)
"""
_MIGRATION_V2_CHECKSUM = "sha256:" + hashlib.sha256(
    "\n".join(
        (_V2_ADD_CONCURRENCY_MODE, _V2_CREATE_CONCURRENCY_INDEX)
    ).encode("utf-8")
).hexdigest()


def _build_v1_schema_contract() -> tuple[
    dict[str, dict[str, tuple[str, int, str | None, int]]],
    dict[str, frozenset[tuple[str, str, str, str, str]]],
    dict[str, tuple[str, tuple[str, ...]]],
]:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    try:
        db.execute(_CREATE_RUNS)
        db.execute(_CREATE_TOOL_CALLS)
        db.execute(_CREATE_EVENTS)
        for statement in _CREATE_INDEXES:
            db.execute(statement)

        columns: dict[str, dict[str, tuple[str, int, str | None, int]]] = {}
        foreign_keys: dict[str, frozenset[tuple[str, str, str, str, str]]] = {}
        for table in _V1_TABLES:
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

        indexes: dict[str, tuple[str, tuple[str, ...]]] = {}
        for row in db.execute(
            """
            SELECT name, tbl_name
            FROM sqlite_master
            WHERE type = 'index' AND name NOT LIKE 'sqlite_autoindex_%'
            """
        ):
            index_name = str(row["name"])
            indexes[index_name] = (
                str(row["tbl_name"]),
                tuple(
                    str(column["name"])
                    for column in db.execute(f"PRAGMA index_info({index_name})")
                ),
            )
        return columns, foreign_keys, indexes
    finally:
        db.close()


_V1_COLUMNS, _V1_FOREIGN_KEYS, _V1_INDEXES = _build_v1_schema_contract()


@dataclass(frozen=True, slots=True)
class AgentRunRecord:
    run_id: str
    trigger_type: str
    trigger_ref: str
    principal_kind: str
    principal_id: str
    session_id: str
    group_id: str
    registry_generation: int
    status: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    terminal_at: str


@dataclass(frozen=True, slots=True)
class RuntimeEventRecord:
    event_id: int
    run_id: str
    call_id: str
    entity_kind: str
    event_type: str
    from_status: str
    to_status: str
    actor: str
    metadata: dict[str, Any]
    event_at: str


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    call_id: str
    run_id: str
    step_id: str
    tool_name: str
    tool_version: str
    owner: str
    effect: str
    principal_kind: str
    principal_id: str
    target_ref: str
    args_digest: str
    idempotency_mode: str
    idempotency_key_digest: str
    approval_ref_digest: str
    concurrency_mode: str
    concurrency_key: str
    status: str
    attempt_count: int
    lease_owner: str
    lease_until: str
    safe_result: dict[str, Any]
    error_code: str
    external_id: str
    created_at: str
    updated_at: str
    terminal_at: str


@dataclass(frozen=True, slots=True)
class ToolReconciliationRecord:
    event_id: int
    run_id: str
    call_id: str
    decision: str
    actor: str
    adapter_id: str
    evidence_ref: str
    note_digest: str
    external_id: str
    resolved_at: str


class ConcurrencyBusyError(RuntimeError):
    def __init__(
        self,
        *,
        call_id: str,
        blocking_call_id: str,
        concurrency_mode: str,
        concurrency_key: str,
    ) -> None:
        super().__init__(
            "tool concurrency busy: "
            f"call={call_id} blocked_by={blocking_call_id} "
            f"mode={concurrency_mode}"
        )
        self.call_id = call_id
        self.blocking_call_id = blocking_call_id
        self.concurrency_mode = concurrency_mode
        self.concurrency_key = concurrency_key


class LeaseOwnershipError(RuntimeError):
    def __init__(
        self,
        *,
        call_id: str,
        expected_owner: str,
        actual_owner: str,
    ) -> None:
        super().__init__(
            "tool call lease ownership mismatch: "
            f"call={call_id} expected={expected_owner!r} actual={actual_owner!r}"
        )
        self.call_id = call_id
        self.expected_owner = expected_owner
        self.actual_owner = actual_owner


async def _apply_v1(db: aiosqlite.Connection) -> None:
    await db.execute(_CREATE_RUNS)
    await db.execute(_CREATE_TOOL_CALLS)
    await db.execute(_CREATE_EVENTS)
    for statement in _CREATE_INDEXES:
        await db.execute(statement)


async def _verify_v1(db: aiosqlite.Connection) -> bool:
    expected = set(_V1_TABLES)
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?, ?)",
        tuple(sorted(expected)),
    )
    try:
        found = {str(row[0]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()
    if found != expected:
        return False

    for table, required_columns in _V1_COLUMNS.items():
        cursor = await db.execute(f"PRAGMA table_info({table})")
        try:
            columns = {
                str(row["name"]): (
                    str(row["type"]).upper(),
                    int(row["notnull"]),
                    None
                    if row["dflt_value"] is None
                    else str(row["dflt_value"]),
                    int(row["pk"]),
                )
                for row in await cursor.fetchall()
            }
        finally:
            await cursor.close()
        for column_name, required_spec in required_columns.items():
            if columns.get(column_name) != required_spec:
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
        if not _V1_FOREIGN_KEYS[table] <= foreign_keys:
            return False

    cursor = await db.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'"
    )
    try:
        index_tables = {
            str(row["name"]): str(row["tbl_name"])
            for row in await cursor.fetchall()
        }
    finally:
        await cursor.close()
    for index_name, (expected_table, expected_columns) in _V1_INDEXES.items():
        if index_tables.get(index_name) != expected_table:
            return False
        cursor = await db.execute(f"PRAGMA index_info({index_name})")
        try:
            columns = tuple(str(row["name"]) for row in await cursor.fetchall())
        finally:
            await cursor.close()
        if columns != expected_columns:
            return False
    return True


_MIGRATION_V1 = Migration(
    version=1,
    name="create_agent_runtime_ledger",
    checksum=_MIGRATION_V1_CHECKSUM,
    apply=_apply_v1,
    verify=_verify_v1,
)


async def _apply_v2(db: aiosqlite.Connection) -> None:
    await db.execute(_V2_ADD_CONCURRENCY_MODE)
    await db.execute(_V2_CREATE_CONCURRENCY_INDEX)


async def _verify_v2(db: aiosqlite.Connection) -> bool:
    if not await _verify_v1(db):
        return False
    cursor = await db.execute("PRAGMA table_info(agent_tool_calls)")
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
    if columns.get("concurrency_mode") != (
        "TEXT",
        1,
        "'global_serial'",
        0,
    ):
        return False
    cursor = await db.execute("PRAGMA index_info(idx_agent_calls_concurrency)")
    try:
        index_columns = tuple(str(row["name"]) for row in await cursor.fetchall())
    finally:
        await cursor.close()
    return index_columns == (
        "concurrency_mode",
        "concurrency_key",
        "status",
        "lease_until",
    )


_MIGRATION_V2 = Migration(
    version=2,
    name="add_agent_runtime_concurrency_snapshot",
    checksum=_MIGRATION_V2_CHECKSUM,
    apply=_apply_v2,
    verify=_verify_v2,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json_dump(value: dict[str, Any] | None) -> str:
    return json.dumps(
        value or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_load(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    raw = json.loads(value)
    return dict(raw) if isinstance(raw, dict) else {}


async def _await_cleanup_task(
    task: asyncio.Task[Any],
) -> tuple[Any, asyncio.CancelledError | None]:
    """Keep repeated cancellation from detaching required SQLite cleanup."""
    first_cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if first_cancellation is None:
                first_cancellation = exc
            continue
    return task.result(), first_cancellation


async def _shielded_rollback(db: aiosqlite.Connection) -> None:
    await _await_cleanup_task(
        asyncio.create_task(db.rollback(), name="agent-runtime-ledger-rollback")
    )


def _run_from_row(row: aiosqlite.Row) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=str(row["run_id"]),
        trigger_type=str(row["trigger_type"]),
        trigger_ref=str(row["trigger_ref"]),
        principal_kind=str(row["principal_kind"]),
        principal_id=str(row["principal_id"]),
        session_id=str(row["session_id"]),
        group_id=str(row["group_id"]),
        registry_generation=int(row["registry_generation"]),
        status=str(row["status"]),
        metadata=_json_load(row["metadata_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        terminal_at=str(row["terminal_at"]),
    )


def _event_from_row(row: aiosqlite.Row) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        event_id=int(row["event_id"]),
        run_id=str(row["run_id"]),
        call_id=str(row["call_id"]),
        entity_kind=str(row["entity_kind"]),
        event_type=str(row["event_type"]),
        from_status=str(row["from_status"]),
        to_status=str(row["to_status"]),
        actor=str(row["actor"]),
        metadata=_json_load(row["metadata_json"]),
        event_at=str(row["event_at"]),
    )


def _tool_call_from_row(row: aiosqlite.Row) -> ToolCallRecord:
    return ToolCallRecord(
        call_id=str(row["call_id"]),
        run_id=str(row["run_id"]),
        step_id=str(row["step_id"]),
        tool_name=str(row["tool_name"]),
        tool_version=str(row["tool_version"]),
        owner=str(row["owner"]),
        effect=str(row["effect"]),
        principal_kind=str(row["principal_kind"]),
        principal_id=str(row["principal_id"]),
        target_ref=str(row["target_ref"]),
        args_digest=str(row["args_digest"]),
        idempotency_mode=str(row["idempotency_mode"]),
        idempotency_key_digest=str(row["idempotency_key_digest"]),
        approval_ref_digest=str(row["approval_ref_digest"]),
        concurrency_mode=str(row["concurrency_mode"]),
        concurrency_key=str(row["concurrency_key"]),
        status=str(row["status"]),
        attempt_count=int(row["attempt_count"]),
        lease_owner=str(row["lease_owner"]),
        lease_until=str(row["lease_until"]),
        safe_result=_json_load(row["safe_result_json"]),
        error_code=str(row["error_code"]),
        external_id=str(row["external_id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        terminal_at=str(row["terminal_at"]),
    )


class AgentRuntimeLedger:
    """Explicitly initialized SQLite snapshots plus append-only events."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        if self._db is not None:
            return
        await MigrationRunner(db_path=self._db_path, db_id=_DB_ID).ensure(
            (_MIGRATION_V1, _MIGRATION_V2)
        )
        self._db = await connect_sqlite(self._db_path)

    async def close(self) -> None:
        db = self._db
        self._db = None
        if db is None:
            return
        _, cancellation = await _await_cleanup_task(
            asyncio.create_task(
                close_with_checkpoint(db, name=_DB_ID),
                name="agent-runtime-ledger-close",
            )
        )
        if cancellation is not None:
            raise cancellation

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("AgentRuntimeLedger is not initialized")
        return self._db

    async def create_run(
        self,
        *,
        run_id: str,
        trigger_type: str,
        trigger_ref: str,
        principal_kind: str,
        principal_id: str,
        session_id: str = "",
        group_id: str = "",
        registry_generation: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        clean_run_id = str(run_id or "").strip()
        clean_trigger = str(trigger_type or "").strip()
        if not clean_run_id:
            raise ValueError("run_id is required")
        if clean_trigger not in _TRIGGER_TYPES:
            raise ValueError(f"unsupported trigger_type: {trigger_type!r}")
        if not str(principal_kind or "").strip() or not str(principal_id or "").strip():
            raise ValueError("principal_kind and principal_id are required")
        if int(registry_generation) < 0:
            raise ValueError("registry_generation must be non-negative")

        db = self._require_db()
        now = _now_iso()
        async with self._write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    """
                    INSERT INTO agent_runs (
                        run_id, trigger_type, trigger_ref, principal_kind,
                        principal_id, session_id, group_id, registry_generation,
                        status, metadata_json, created_at, updated_at, terminal_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, '')
                    """,
                    (
                        clean_run_id,
                        clean_trigger,
                        str(trigger_ref or ""),
                        str(principal_kind).strip(),
                        str(principal_id).strip(),
                        str(session_id or ""),
                        str(group_id or ""),
                        int(registry_generation),
                        _json_dump(metadata),
                        now,
                        now,
                    ),
                )
                await self._append_event(
                    db,
                    run_id=clean_run_id,
                    entity_kind="run",
                    event_type="created",
                    from_status="",
                    to_status="queued",
                    actor="runtime",
                    metadata=metadata,
                    event_at=now,
                )
                await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise
        record = await self.get_run(clean_run_id)
        if record is None:
            raise RuntimeError("created Agent run is missing")
        return record

    async def get_run(self, run_id: str) -> AgentRunRecord | None:
        db = self._require_db()
        cursor = await db.execute(
            "SELECT * FROM agent_runs WHERE run_id = ?",
            (str(run_id),),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return _run_from_row(row) if row is not None else None

    async def transition_run(
        self,
        run_id: str,
        *,
        to_status: str,
        actor: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        clean_run_id = str(run_id or "").strip()
        target = str(to_status or "").strip()
        if target not in _RUN_TRANSITIONS:
            raise ValueError(f"unknown run status: {to_status!r}")
        if not str(actor or "").strip():
            raise ValueError("transition actor is required")

        db = self._require_db()
        now = _now_iso()
        async with self._write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    "SELECT status FROM agent_runs WHERE run_id = ?",
                    (clean_run_id,),
                )
                try:
                    row = await cursor.fetchone()
                finally:
                    await cursor.close()
                if row is None:
                    raise KeyError(clean_run_id)
                current = str(row["status"])
                if target == current:
                    await db.commit()
                else:
                    if target not in _RUN_TRANSITIONS[current]:
                        raise ValueError(
                            f"illegal run transition: {current} -> {target}"
                        )
                    if target == "succeeded" and await self._run_has_open_tool_calls(
                        db, clean_run_id
                    ):
                        raise ValueError("cannot succeed Agent run with open tool calls")
                    terminal_at = now if target in _RUN_TERMINAL else ""
                    updated = await db.execute(
                        """
                        UPDATE agent_runs
                        SET status = ?, updated_at = ?, terminal_at = ?
                        WHERE run_id = ? AND status = ?
                        """,
                        (target, now, terminal_at, clean_run_id, current),
                    )
                    if updated.rowcount != 1:
                        raise RuntimeError("run transition lost compare-and-swap")
                    if target in {"failed", "cancelled"}:
                        await self._close_open_tool_calls_for_run(
                            db,
                            run_id=clean_run_id,
                            run_status=target,
                            actor=str(actor).strip(),
                            event_at=now,
                        )
                    await self._append_event(
                        db,
                        run_id=clean_run_id,
                        entity_kind="run",
                        event_type="status_changed",
                        from_status=current,
                        to_status=target,
                        actor=str(actor).strip(),
                        metadata=metadata,
                        event_at=now,
                    )
                    await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise
        record = await self.get_run(clean_run_id)
        if record is None:
            raise KeyError(clean_run_id)
        return record

    @staticmethod
    async def _run_has_open_tool_calls(
        db: aiosqlite.Connection,
        run_id: str,
    ) -> bool:
        cursor = await db.execute(
            """
            SELECT 1
            FROM agent_tool_calls
            WHERE run_id = ?
              AND status NOT IN (
                  'denied', 'succeeded', 'failed_terminal', 'unknown', 'cancelled'
              )
            LIMIT 1
            """,
            (run_id,),
        )
        try:
            return await cursor.fetchone() is not None
        finally:
            await cursor.close()

    async def _close_open_tool_calls_for_run(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        run_status: str,
        actor: str,
        event_at: str,
    ) -> None:
        cursor = await db.execute(
            """
            SELECT call_id, status
            FROM agent_tool_calls
            WHERE run_id = ?
              AND status NOT IN (
                  'denied', 'succeeded', 'failed_terminal', 'unknown', 'cancelled'
              )
            ORDER BY created_at, call_id
            """,
            (run_id,),
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()

        for row in rows:
            call_id = str(row["call_id"])
            current = str(row["status"])
            target = "unknown" if current == "dispatching" else "cancelled"
            reason_prefix = "run_cancelled" if run_status == "cancelled" else "run_failed"
            error_code = (
                f"{reason_prefix}_post_dispatch_unknown"
                if target == "unknown"
                else reason_prefix
            )
            updated = await db.execute(
                """
                UPDATE agent_tool_calls
                SET status = ?, lease_owner = '', lease_until = '',
                    error_code = ?, updated_at = ?, terminal_at = ?
                WHERE call_id = ? AND status = ?
                """,
                (target, error_code, event_at, event_at, call_id, current),
            )
            if updated.rowcount != 1:
                raise RuntimeError("run cancellation lost tool call compare-and-swap")
            await self._append_event(
                db,
                run_id=run_id,
                call_id=call_id,
                entity_kind="tool_call",
                event_type="status_changed",
                from_status=current,
                to_status=target,
                actor=actor,
                metadata={"reason": error_code},
                event_at=event_at,
            )

    async def create_tool_call(
        self,
        *,
        call_id: str,
        run_id: str,
        step_id: str,
        tool_name: str,
        tool_version: str,
        owner: str,
        effect: str,
        principal_kind: str,
        principal_id: str,
        args_digest: str,
        idempotency_mode: str,
        target_ref: str = "",
        idempotency_key_digest: str = "",
        approval_ref_digest: str = "",
        concurrency_mode: str = "global_serial",
        concurrency_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ToolCallRecord:
        required = {
            "call_id": call_id,
            "run_id": run_id,
            "step_id": step_id,
            "tool_name": tool_name,
            "tool_version": tool_version,
            "owner": owner,
            "principal_kind": principal_kind,
            "principal_id": principal_id,
            "args_digest": args_digest,
        }
        cleaned = {name: str(value or "").strip() for name, value in required.items()}
        missing = [name for name, value in cleaned.items() if not value]
        if missing:
            raise ValueError(f"required tool call fields missing: {', '.join(missing)}")
        clean_effect = str(effect or "").strip()
        if clean_effect not in _TOOL_EFFECTS:
            raise ValueError(f"unknown tool effect: {effect!r}")
        clean_idempotency = str(idempotency_mode or "").strip()
        if clean_idempotency not in _TOOL_IDEMPOTENCY_MODES:
            raise ValueError(f"unknown tool idempotency mode: {idempotency_mode!r}")
        clean_concurrency = str(concurrency_mode or "").strip()
        if clean_concurrency not in _TOOL_CONCURRENCY_MODES:
            raise ValueError(f"unknown tool concurrency mode: {concurrency_mode!r}")
        clean_concurrency_key = str(concurrency_key or "").strip()
        if clean_concurrency == "keyed_serial" and not clean_concurrency_key:
            raise ValueError("keyed_serial tool call requires concurrency_key")
        clean_idempotency_key_digest = str(idempotency_key_digest or "").strip()
        if (
            clean_idempotency in {"required", "provider_supported"}
            and not clean_idempotency_key_digest
        ):
            raise ValueError(
                f"idempotency_key_digest is required for {clean_idempotency}"
            )

        db = self._require_db()
        now = _now_iso()
        async with self._write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                run_cursor = await db.execute(
                    """
                    SELECT principal_kind, principal_id, status
                    FROM agent_runs
                    WHERE run_id = ?
                    """,
                    (cleaned["run_id"],),
                )
                try:
                    run_row = await run_cursor.fetchone()
                finally:
                    await run_cursor.close()
                if run_row is None:
                    raise KeyError(cleaned["run_id"])
                if str(run_row["status"]) in _RUN_TERMINAL:
                    raise ValueError("cannot create tool call for terminal Agent run")
                if (
                    str(run_row["principal_kind"]) != cleaned["principal_kind"]
                    or str(run_row["principal_id"]) != cleaned["principal_id"]
                ):
                    raise ValueError("tool call principal must match its Agent run")
                await db.execute(
                    """
                    INSERT INTO agent_tool_calls (
                        call_id, run_id, step_id, tool_name, tool_version, owner,
                        effect, principal_kind, principal_id, target_ref,
                        args_digest, idempotency_mode, idempotency_key_digest,
                        approval_ref_digest, concurrency_mode, concurrency_key, status,
                        attempt_count, lease_owner, lease_until, safe_result_json,
                        error_code, external_id, created_at, updated_at, terminal_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'proposed', 0, '', '', '{}', '', '', ?, ?, ''
                    )
                    """,
                    (
                        cleaned["call_id"],
                        cleaned["run_id"],
                        cleaned["step_id"],
                        cleaned["tool_name"],
                        cleaned["tool_version"],
                        cleaned["owner"],
                        clean_effect,
                        cleaned["principal_kind"],
                        cleaned["principal_id"],
                        str(target_ref or ""),
                        cleaned["args_digest"],
                        clean_idempotency,
                        clean_idempotency_key_digest,
                        str(approval_ref_digest or ""),
                        clean_concurrency,
                        clean_concurrency_key,
                        now,
                        now,
                    ),
                )
                await self._append_event(
                    db,
                    run_id=cleaned["run_id"],
                    call_id=cleaned["call_id"],
                    entity_kind="tool_call",
                    event_type="created",
                    from_status="",
                    to_status="proposed",
                    actor="runtime",
                    metadata=metadata,
                    event_at=now,
                )
                await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise
        record = await self.get_tool_call(cleaned["call_id"])
        if record is None:
            raise RuntimeError("created Agent tool call is missing")
        return record

    async def get_tool_call(self, call_id: str) -> ToolCallRecord | None:
        db = self._require_db()
        cursor = await db.execute(
            "SELECT * FROM agent_tool_calls WHERE call_id = ?",
            (str(call_id),),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return _tool_call_from_row(row) if row is not None else None

    async def record_tool_call_approval(
        self,
        call_id: str,
        *,
        approval_ref_digest: str,
        actor: str,
    ) -> ToolCallRecord:
        clean_call_id = str(call_id or "").strip()
        clean_digest = str(approval_ref_digest or "").strip()
        clean_actor = str(actor or "").strip()
        if not clean_call_id or not clean_digest or not clean_actor:
            raise ValueError("call_id, approval_ref_digest and actor are required")
        db = self._require_db()
        now = _now_iso()
        async with self._write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    """
                    SELECT run_id, status, approval_ref_digest
                    FROM agent_tool_calls
                    WHERE call_id = ?
                    """,
                    (clean_call_id,),
                )
                try:
                    row = await cursor.fetchone()
                finally:
                    await cursor.close()
                if row is None:
                    raise KeyError(clean_call_id)
                if str(row["status"]) != "approval_pending":
                    raise ValueError("approval can only attach to approval_pending call")
                current_digest = str(row["approval_ref_digest"])
                if current_digest and current_digest != clean_digest:
                    raise ValueError("tool call already has a different approval")
                if current_digest == clean_digest:
                    await db.commit()
                else:
                    updated = await db.execute(
                        """
                        UPDATE agent_tool_calls
                        SET approval_ref_digest = ?, updated_at = ?
                        WHERE call_id = ? AND status = 'approval_pending'
                          AND approval_ref_digest = ''
                        """,
                        (clean_digest, now, clean_call_id),
                    )
                    if updated.rowcount != 1:
                        raise RuntimeError(
                            "tool call approval lost compare-and-swap"
                        )
                    await self._append_event(
                        db,
                        run_id=str(row["run_id"]),
                        call_id=clean_call_id,
                        entity_kind="tool_call",
                        event_type="approval_granted",
                        from_status="approval_pending",
                        to_status="approval_pending",
                        actor=clean_actor,
                        metadata={"approval_ref_digest": clean_digest},
                        event_at=now,
                    )
                    await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise
        record = await self.get_tool_call(clean_call_id)
        if record is None:
            raise KeyError(clean_call_id)
        return record

    async def transition_tool_call(
        self,
        call_id: str,
        *,
        to_status: str,
        actor: str,
        metadata: dict[str, Any] | None = None,
        safe_result: dict[str, Any] | None = None,
        error_code: str | None = None,
        external_id: str | None = None,
        expected_lease_owner: str | None = None,
    ) -> ToolCallRecord:
        if str(to_status or "").strip() == "claimed":
            raise ValueError("use claim_tool_call to enter claimed state")
        return await self._transition_tool_call(
            call_id,
            to_status=to_status,
            actor=actor,
            metadata=metadata,
            safe_result=safe_result,
            error_code=error_code,
            external_id=external_id,
            expected_lease_owner=expected_lease_owner,
        )

    async def claim_tool_call(
        self,
        call_id: str,
        *,
        lease_owner: str,
        lease_until: str,
        actor: str,
    ) -> ToolCallRecord:
        if not str(lease_owner or "").strip() or not str(lease_until or "").strip():
            raise ValueError("claim requires lease_owner and lease_until")
        try:
            lease_deadline = datetime.fromisoformat(
                str(lease_until).strip().replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                "lease_until must be timezone-aware ISO-8601"
            ) from exc
        if lease_deadline.utcoffset() is None:
            raise ValueError("lease_until must be timezone-aware ISO-8601")
        return await self._transition_tool_call(
            call_id,
            to_status="claimed",
            actor=actor,
            lease_owner=str(lease_owner).strip(),
            lease_until=lease_deadline.astimezone(UTC).isoformat(),
            increment_attempt=True,
            enforce_concurrency=True,
        )

    async def cancel_tool_call(
        self,
        call_id: str,
        *,
        actor: str,
        reason: str = "cancelled",
        expected_lease_owner: str | None = None,
        force: bool = False,
    ) -> ToolCallRecord:
        clean_reason = str(reason or "cancelled").strip() or "cancelled"
        return await self._transition_tool_call(
            call_id,
            to_status="cancelled",
            actor=actor,
            metadata={"reason": clean_reason},
            cancellation_reason=clean_reason,
            expected_lease_owner=expected_lease_owner,
            force_cancel=force,
        )

    async def recover_incomplete_tool_calls(
        self,
        *,
        actor: str,
        exclusive_startup: bool = False,
    ) -> list[ToolCallRecord]:
        if exclusive_startup is not True:
            raise ValueError(
                "recover_incomplete_tool_calls requires exclusive_startup=True"
            )
        clean_actor = str(actor or "").strip()
        if not clean_actor:
            raise ValueError("recovery actor is required")
        db = self._require_db()
        now = _now_iso()
        recovered_ids: list[str] = []
        async with self._write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    """
                    SELECT call_id, run_id, status
                    FROM agent_tool_calls
                    WHERE status IN ('claimed', 'dispatching')
                    ORDER BY created_at, call_id
                    """
                )
                try:
                    rows = await cursor.fetchall()
                finally:
                    await cursor.close()

                for row in rows:
                    call_id = str(row["call_id"])
                    current = str(row["status"])
                    target = "ready" if current == "claimed" else "unknown"
                    reason = (
                        "restart_before_dispatch"
                        if target == "ready"
                        else "restart_post_dispatch_unknown"
                    )
                    updated = await db.execute(
                        """
                        UPDATE agent_tool_calls
                        SET status = ?, lease_owner = '', lease_until = '',
                            error_code = ?, updated_at = ?, terminal_at = ?
                        WHERE call_id = ? AND status = ?
                        """,
                        (
                            target,
                            reason,
                            now,
                            now if target == "unknown" else "",
                            call_id,
                            current,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise RuntimeError(
                            "tool call recovery lost compare-and-swap"
                        )
                    await self._append_event(
                        db,
                        run_id=str(row["run_id"]),
                        call_id=call_id,
                        entity_kind="tool_call",
                        event_type="status_changed",
                        from_status=current,
                        to_status=target,
                        actor=clean_actor,
                        metadata={"reason": reason},
                        event_at=now,
                    )
                    recovered_ids.append(call_id)
                await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise

        recovered: list[ToolCallRecord] = []
        for call_id in recovered_ids:
            record = await self.get_tool_call(call_id)
            if record is None:
                raise RuntimeError(f"recovered tool call is missing: {call_id}")
            recovered.append(record)
        return recovered

    async def recover_expired_tool_calls(
        self,
        *,
        now: datetime,
        actor: str,
    ) -> list[ToolCallRecord]:
        clean_actor = str(actor or "").strip()
        if not clean_actor:
            raise ValueError("lease recovery actor is required")
        if now.utcoffset() is None:
            raise ValueError("lease recovery time must include timezone")
        event_at = now.astimezone(UTC).isoformat()
        db = self._require_db()
        recovered_ids: list[str] = []
        async with self._write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    """
                    SELECT call_id, run_id, status
                    FROM agent_tool_calls
                    WHERE status IN ('claimed', 'dispatching')
                      AND lease_until != ''
                      AND julianday(lease_until) <= julianday(?)
                    ORDER BY created_at, call_id
                    """,
                    (event_at,),
                )
                try:
                    rows = await cursor.fetchall()
                finally:
                    await cursor.close()

                for row in rows:
                    call_id = str(row["call_id"])
                    current = str(row["status"])
                    target = "ready" if current == "claimed" else "unknown"
                    reason = (
                        "lease_expired_before_dispatch"
                        if target == "ready"
                        else "lease_expired_post_dispatch_unknown"
                    )
                    updated = await db.execute(
                        """
                        UPDATE agent_tool_calls
                        SET status = ?, lease_owner = '', lease_until = '',
                            error_code = ?, updated_at = ?, terminal_at = ?
                        WHERE call_id = ? AND status = ?
                        """,
                        (
                            target,
                            reason,
                            event_at,
                            event_at if target == "unknown" else "",
                            call_id,
                            current,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise RuntimeError(
                            "expired tool call recovery lost compare-and-swap"
                        )
                    await self._append_event(
                        db,
                        run_id=str(row["run_id"]),
                        call_id=call_id,
                        entity_kind="tool_call",
                        event_type="status_changed",
                        from_status=current,
                        to_status=target,
                        actor=clean_actor,
                        metadata={"reason": reason},
                        event_at=event_at,
                    )
                    recovered_ids.append(call_id)
                await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise

        recovered: list[ToolCallRecord] = []
        for call_id in recovered_ids:
            record = await self.get_tool_call(call_id)
            if record is None:
                raise RuntimeError(f"recovered tool call is missing: {call_id}")
            recovered.append(record)
        return recovered

    async def _transition_tool_call(
        self,
        call_id: str,
        *,
        to_status: str,
        actor: str,
        metadata: dict[str, Any] | None = None,
        safe_result: dict[str, Any] | None = None,
        error_code: str | None = None,
        external_id: str | None = None,
        lease_owner: str | None = None,
        lease_until: str | None = None,
        increment_attempt: bool = False,
        cancellation_reason: str | None = None,
        enforce_concurrency: bool = False,
        expected_lease_owner: str | None = None,
        force_cancel: bool = False,
    ) -> ToolCallRecord:
        clean_call_id = str(call_id or "").strip()
        target = str(to_status or "").strip()
        if target not in _TOOL_CALL_TRANSITIONS:
            raise ValueError(f"unknown tool call status: {to_status!r}")
        if not str(actor or "").strip():
            raise ValueError("transition actor is required")

        db = self._require_db()
        now = _now_iso()
        async with self._write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    """
                    SELECT agent_tool_calls.*, agent_runs.status AS run_status
                    FROM agent_tool_calls
                    JOIN agent_runs ON agent_runs.run_id = agent_tool_calls.run_id
                    WHERE agent_tool_calls.call_id = ?
                    """,
                    (clean_call_id,),
                )
                try:
                    row = await cursor.fetchone()
                finally:
                    await cursor.close()
                if row is None:
                    raise KeyError(clean_call_id)
                current = str(row["status"])
                if (
                    not enforce_concurrency
                    and not (cancellation_reason is not None and force_cancel)
                    and current in {"claimed", "dispatching"}
                ):
                    expected_owner = str(expected_lease_owner or "").strip()
                    actual_owner = str(row["lease_owner"])
                    if not expected_owner or expected_owner != actual_owner:
                        raise LeaseOwnershipError(
                            call_id=clean_call_id,
                            expected_owner=expected_owner,
                            actual_owner=actual_owner,
                        )
                if enforce_concurrency:
                    await self._raise_if_concurrency_busy(
                        db,
                        row=row,
                        call_id=clean_call_id,
                    )
                transition_error_code = error_code
                if cancellation_reason is not None:
                    if current == "dispatching":
                        target = "unknown"
                        transition_error_code = (
                            f"{cancellation_reason}_post_dispatch_unknown"
                        )
                    elif "cancelled" in _TOOL_CALL_TRANSITIONS[current]:
                        target = "cancelled"
                        transition_error_code = cancellation_reason
                    else:
                        raise ValueError(
                            f"illegal tool call cancellation from {current}"
                        )
                if (
                    str(row["run_status"]) in _RUN_TERMINAL
                    and target not in {"cancelled", "unknown"}
                ):
                    raise ValueError(
                        "cannot advance tool call for terminal Agent run"
                    )
                if target == current:
                    if increment_attempt:
                        raise ValueError(
                            f"illegal tool call transition: {current} -> {target}"
                        )
                    await db.commit()
                else:
                    if target not in _TOOL_CALL_TRANSITIONS[current]:
                        raise ValueError(
                            f"illegal tool call transition: {current} -> {target}"
                        )
                    terminal_at = now if target in _TOOL_CALL_TERMINAL else ""
                    clear_lease = target == "ready" or target in _TOOL_CALL_TERMINAL
                    new_lease_owner = "" if clear_lease else str(
                        lease_owner if lease_owner is not None else row["lease_owner"]
                    )
                    new_lease_until = "" if clear_lease else str(
                        lease_until if lease_until is not None else row["lease_until"]
                    )
                    updated = await db.execute(
                        """
                        UPDATE agent_tool_calls
                        SET status = ?, attempt_count = ?, lease_owner = ?,
                            lease_until = ?, safe_result_json = ?, error_code = ?,
                            external_id = ?, updated_at = ?, terminal_at = ?
                        WHERE call_id = ? AND status = ?
                        """,
                        (
                            target,
                            int(row["attempt_count"]) + int(increment_attempt),
                            new_lease_owner,
                            new_lease_until,
                            _json_dump(safe_result)
                            if safe_result is not None
                            else str(row["safe_result_json"]),
                            str(transition_error_code)
                            if transition_error_code is not None
                            else (
                                ""
                                if target
                                in {"ready", "claimed", "dispatching", "succeeded"}
                                else str(row["error_code"])
                            ),
                            str(external_id)
                            if external_id is not None
                            else str(row["external_id"]),
                            now,
                            terminal_at,
                            clean_call_id,
                            current,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise RuntimeError("tool call transition lost compare-and-swap")
                    await self._append_event(
                        db,
                        run_id=str(row["run_id"]),
                        call_id=clean_call_id,
                        entity_kind="tool_call",
                        event_type="status_changed",
                        from_status=current,
                        to_status=target,
                        actor=str(actor).strip(),
                        metadata=metadata,
                        event_at=now,
                    )
                    await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise
        record = await self.get_tool_call(clean_call_id)
        if record is None:
            raise KeyError(clean_call_id)
        return record

    @staticmethod
    async def _raise_if_concurrency_busy(
        db: aiosqlite.Connection,
        *,
        row: aiosqlite.Row,
        call_id: str,
    ) -> None:
        mode = str(row["concurrency_mode"])
        key = str(row["concurrency_key"])
        if mode == "parallel":
            return
        if mode == "keyed_serial" and not key:
            raise ValueError("keyed_serial tool call has no concurrency_key")
        if mode == "global_serial":
            predicate = "concurrency_mode = 'global_serial'"
            params: tuple[str, ...] = (call_id,)
        elif mode == "keyed_serial":
            predicate = "concurrency_mode = 'keyed_serial' AND concurrency_key = ?"
            params = (call_id, key)
        else:
            raise ValueError(f"unknown durable concurrency mode: {mode!r}")
        cursor = await db.execute(
            f"""
            SELECT call_id
            FROM agent_tool_calls
            WHERE call_id != ?
              AND status IN ('claimed', 'dispatching')
              AND {predicate}
            ORDER BY created_at, call_id
            LIMIT 1
            """,
            params,
        )
        try:
            conflict = await cursor.fetchone()
        finally:
            await cursor.close()
        if conflict is not None:
            raise ConcurrencyBusyError(
                call_id=call_id,
                blocking_call_id=str(conflict["call_id"]),
                concurrency_mode=mode,
                concurrency_key=key,
            )

    async def list_events(self, *, run_id: str) -> list[RuntimeEventRecord]:
        db = self._require_db()
        cursor = await db.execute(
            "SELECT * FROM agent_runtime_events WHERE run_id = ? ORDER BY event_id",
            (str(run_id),),
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        return [_event_from_row(row) for row in rows]

    async def record_tool_reconciliation(
        self,
        call_id: str,
        *,
        decision: str,
        actor: str,
        adapter_id: str,
        evidence_ref: str,
        note_digest: str,
        external_id: str | None = None,
    ) -> ToolReconciliationRecord:
        clean_call_id = self._reconciliation_text(
            call_id,
            field="call_id",
            max_length=128,
        )
        clean_decision = str(decision or "").strip()
        if clean_decision not in _RECONCILIATION_DECISIONS:
            raise ValueError("unknown reconciliation decision")
        clean_actor = self._reconciliation_text(
            actor,
            field="actor",
            max_length=128,
        )
        clean_adapter_id = self._reconciliation_text(
            adapter_id,
            field="adapter_id",
            max_length=128,
        )
        clean_evidence = self._reconciliation_text(
            evidence_ref,
            field="evidence_ref",
            max_length=240,
        )
        clean_note_digest = str(note_digest or "").strip()
        digest_value = clean_note_digest.removeprefix("sha256:")
        if (
            not clean_note_digest.startswith("sha256:")
            or len(digest_value) != 64
            or any(character not in "0123456789abcdef" for character in digest_value)
        ):
            raise ValueError("note_digest must be a lowercase SHA-256 digest")
        clean_external = ""
        if external_id is not None and str(external_id or "").strip():
            clean_external = self._reconciliation_text(
                external_id,
                field="external_id",
                max_length=240,
            )
        if clean_decision == "confirmed_not_applied" and clean_external:
            raise ValueError(
                "confirmed_not_applied reconciliation cannot have external_id"
            )

        expected = {
            "decision": clean_decision,
            "adapter_id": clean_adapter_id,
            "evidence_ref": clean_evidence,
            "note_digest": clean_note_digest,
            "external_id": clean_external,
        }
        db = self._require_db()
        now = _now_iso()
        async with self._write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    """
                    SELECT agent_tool_calls.*, agent_runs.status AS run_status
                    FROM agent_tool_calls
                    JOIN agent_runs
                      ON agent_runs.run_id = agent_tool_calls.run_id
                    WHERE agent_tool_calls.call_id = ?
                    """,
                    (clean_call_id,),
                )
                try:
                    call_row = await cursor.fetchone()
                finally:
                    await cursor.close()
                if call_row is None:
                    raise KeyError(clean_call_id)

                existing_cursor = await db.execute(
                    """
                    SELECT * FROM agent_runtime_events
                    WHERE call_id = ?
                      AND event_type = 'reconciliation_resolved'
                    ORDER BY event_id
                    LIMIT 1
                    """,
                    (clean_call_id,),
                )
                try:
                    existing_row = await existing_cursor.fetchone()
                finally:
                    await existing_cursor.close()
                if existing_row is not None:
                    existing = _event_from_row(existing_row)
                    if existing.actor != clean_actor or existing.metadata != expected:
                        raise ValueError(
                            "tool call already has a different reconciliation"
                        )
                    await db.commit()
                    return self._reconciliation_from_event(existing)

                if str(call_row["status"]) != "unknown":
                    raise ValueError(
                        "only an unknown tool call can be reconciled"
                    )
                if str(call_row["idempotency_mode"]) != "reconcile_only":
                    raise ValueError(
                        "tool call does not permit manual reconciliation"
                    )
                if str(call_row["run_status"]) != "waiting_external":
                    raise ValueError(
                        "tool call run is not waiting for external resolution"
                    )
                run_id = str(call_row["run_id"])
                await self._append_event(
                    db,
                    run_id=run_id,
                    call_id=clean_call_id,
                    entity_kind="tool_call",
                    event_type="reconciliation_resolved",
                    from_status="unknown",
                    to_status="unknown",
                    actor=clean_actor,
                    metadata=expected,
                    event_at=now,
                )

                unresolved_cursor = await db.execute(
                    """
                    SELECT 1
                    FROM agent_tool_calls AS calls
                    WHERE calls.run_id = ?
                      AND calls.status = 'unknown'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM agent_runtime_events AS events
                          WHERE events.call_id = calls.call_id
                            AND events.event_type = 'reconciliation_resolved'
                      )
                    LIMIT 1
                    """,
                    (run_id,),
                )
                try:
                    has_unresolved = await unresolved_cursor.fetchone() is not None
                finally:
                    await unresolved_cursor.close()
                if not has_unresolved:
                    updated = await db.execute(
                        """
                        UPDATE agent_runs
                        SET status = 'running', updated_at = ?, terminal_at = ''
                        WHERE run_id = ? AND status = 'waiting_external'
                        """,
                        (now, run_id),
                    )
                    if updated.rowcount != 1:
                        raise RuntimeError(
                            "reconciliation lost run-state compare-and-swap"
                        )
                    await self._append_event(
                        db,
                        run_id=run_id,
                        entity_kind="run",
                        event_type="status_changed",
                        from_status="waiting_external",
                        to_status="running",
                        actor=clean_actor,
                        metadata={
                            "reason": "all_unknown_calls_reconciled",
                            "resolved_call_id": clean_call_id,
                        },
                        event_at=now,
                    )
                await db.commit()
            except BaseException:
                await _shielded_rollback(db)
                raise
        record = await self.get_tool_reconciliation(clean_call_id)
        if record is None:
            raise RuntimeError("tool reconciliation event is missing")
        return record

    async def get_tool_reconciliation(
        self,
        call_id: str,
    ) -> ToolReconciliationRecord | None:
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT * FROM agent_runtime_events
            WHERE call_id = ?
              AND event_type = 'reconciliation_resolved'
            ORDER BY event_id
            LIMIT 1
            """,
            (str(call_id),),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        if row is None:
            return None
        return self._reconciliation_from_event(_event_from_row(row))

    @staticmethod
    def _reconciliation_from_event(
        event: RuntimeEventRecord,
    ) -> ToolReconciliationRecord:
        return ToolReconciliationRecord(
            event_id=event.event_id,
            run_id=event.run_id,
            call_id=event.call_id,
            decision=str(event.metadata.get("decision", "")),
            actor=event.actor,
            adapter_id=str(event.metadata.get("adapter_id", "")),
            evidence_ref=str(event.metadata.get("evidence_ref", "")),
            note_digest=str(event.metadata.get("note_digest", "")),
            external_id=str(event.metadata.get("external_id", "")),
            resolved_at=event.event_at,
        )

    @staticmethod
    def _reconciliation_text(
        value: Any,
        *,
        field: str,
        max_length: int,
    ) -> str:
        clean = str(value or "").strip()
        allowed = (
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789:._/@+-"
        )
        if (
            not clean
            or len(clean) > max_length
            or not clean.isascii()
            or any(character not in allowed for character in clean)
        ):
            raise ValueError(f"{field} is invalid")
        return clean

    @staticmethod
    async def _append_event(
        db: aiosqlite.Connection,
        *,
        run_id: str,
        entity_kind: str,
        event_type: str,
        from_status: str,
        to_status: str,
        actor: str,
        metadata: dict[str, Any] | None,
        event_at: str,
        call_id: str = "",
    ) -> None:
        await db.execute(
            """
            INSERT INTO agent_runtime_events (
                run_id, call_id, entity_kind, event_type, from_status,
                to_status, actor, metadata_json, event_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                call_id,
                entity_kind,
                event_type,
                from_status,
                to_status,
                str(actor or "runtime"),
                _json_dump(metadata),
                event_at,
            ),
        )
