"""Bounded, redacted offline Admin queries for an already-open Runtime ledger."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from services.agent_runtime.ledger import AgentRuntimeLedger

_CONTRACT_VERSION = "runtime_admin_query.v1"
_ADMIN_SCHEMA_VERSION = 1
_SOURCE_SCHEMA_VERSION = 2
_MODE = "offline_dark"
_RUN_STATUSES = frozenset(
    {
        "queued",
        "running",
        "waiting_approval",
        "waiting_retry",
        "waiting_external",
        "succeeded",
        "failed",
        "cancelled",
    }
)
_TOOL_STATUSES = frozenset(
    {
        "proposed",
        "denied",
        "approval_pending",
        "ready",
        "claimed",
        "dispatching",
        "succeeded",
        "failed_retryable",
        "failed_terminal",
        "unknown",
        "cancelled",
    }
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _base(*, available: bool, reason: str) -> dict[str, Any]:
    return {
        "available": available,
        "mode": _MODE,
        "reason": reason,
        "contract_version": _CONTRACT_VERSION,
        "admin_schema_version": _ADMIN_SCHEMA_VERSION,
        "source_schema_version": _SOURCE_SCHEMA_VERSION,
        "snapshot_at": _now_iso(),
    }


def _bounded_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    return value


def _filter_digest(kind: str, filters: Mapping[str, Any]) -> str:
    raw = json.dumps(
        {"kind": kind, "filters": dict(filters)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("invalid admin cursor encoding") from exc


def _encode_cursor(
    *,
    kind: str,
    filters: Mapping[str, Any],
    updated_at: str,
    item_id: str,
) -> str:
    payload = {
        "v": 1,
        "kind": kind,
        "filter": _filter_digest(kind, filters),
        "updated_at": updated_at,
        "item_id": item_id,
    }
    encoded = _b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    checksum = hashlib.sha256(f"runtime-admin-cursor-v1:{encoded}".encode()).hexdigest()
    return f"{encoded}.{checksum}"


def _decode_cursor(
    value: str | None,
    *,
    kind: str,
    filters: Mapping[str, Any],
) -> tuple[str, str] | None:
    if value is None or value == "":
        return None
    encoded, separator, checksum = str(value).partition(".")
    expected = hashlib.sha256(f"runtime-admin-cursor-v1:{encoded}".encode()).hexdigest()
    if not separator or not hmac.compare_digest(checksum, expected):
        raise ValueError("invalid or tampered admin cursor")
    try:
        payload = json.loads(_b64decode(encoded))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid admin cursor payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid admin cursor payload")
    if (
        payload.get("v") != 1
        or payload.get("kind") != kind
        or payload.get("filter") != _filter_digest(kind, filters)
    ):
        raise ValueError("admin cursor does not match query filters")
    updated_at = str(payload.get("updated_at") or "").strip()
    item_id = str(payload.get("item_id") or "").strip()
    if not updated_at or not item_id:
        raise ValueError("admin cursor is missing keyset values")
    return updated_at, item_id


def _open_source(source: AgentRuntimeLedger | None) -> tuple[Any, Any] | None:
    if source is None:
        return None
    db = getattr(source, "_db", None)
    lock = getattr(source, "_write_lock", None)
    if db is None or lock is None:
        return None
    return db, lock


class RuntimeAdminQueryV1:
    """Read-only projection over a caller-owned, already-open Runtime ledger."""

    def __init__(self, source: AgentRuntimeLedger | None) -> None:
        self._source = source

    async def summary(self) -> dict[str, Any]:
        opened = _open_source(self._source)
        if opened is None:
            return {
                **_base(available=False, reason="source_unavailable"),
                "counts": None,
            }
        db, lock = opened
        try:
            async with lock:
                counts: dict[str, int] = {}
                for key, table in (
                    ("runs", "agent_runs"),
                    ("tool_calls", "agent_tool_calls"),
                    ("events", "agent_runtime_events"),
                ):
                    cursor = await db.execute(f"SELECT COUNT(*) AS count FROM {table}")
                    try:
                        row = await cursor.fetchone()
                    finally:
                        await cursor.close()
                    counts[key] = int(row["count"]) if row is not None else 0
        except Exception as exc:
            return {
                **_base(
                    available=False,
                    reason=f"query_failed:{type(exc).__name__}",
                ),
                "counts": None,
            }
        return {**_base(available=True, reason="ok"), "counts": counts}

    async def list_runs(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        page_limit = _bounded_limit(limit)
        clean_status = str(status or "").strip()
        if clean_status and clean_status not in _RUN_STATUSES:
            raise ValueError("invalid run status filter")
        filters = {"status": clean_status}
        keyset = _decode_cursor(cursor, kind="runs", filters=filters)
        opened = _open_source(self._source)
        if opened is None:
            return {
                **_base(available=False, reason="source_unavailable"),
                "items": [],
                "next_cursor": None,
            }

        conditions: list[str] = []
        parameters: list[Any] = []
        if clean_status:
            conditions.append("status = ?")
            parameters.append(clean_status)
        if keyset is not None:
            conditions.append("(updated_at < ? OR (updated_at = ? AND run_id < ?))")
            parameters.extend((keyset[0], keyset[0], keyset[1]))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(page_limit + 1)
        db, lock = opened
        try:
            async with lock:
                query_cursor = await db.execute(
                    f"""
                    SELECT run_id, trigger_type, status, registry_generation,
                           created_at, updated_at, terminal_at
                    FROM agent_runs
                    {where}
                    ORDER BY updated_at DESC, run_id DESC
                    LIMIT ?
                    """,
                    tuple(parameters),
                )
                try:
                    rows = await query_cursor.fetchall()
                finally:
                    await query_cursor.close()
        except Exception as exc:
            return {
                **_base(
                    available=False,
                    reason=f"query_failed:{type(exc).__name__}",
                ),
                "items": [],
                "next_cursor": None,
            }

        visible = rows[:page_limit]
        items = [
            {
                "run_id": str(row["run_id"]),
                "trigger_type": str(row["trigger_type"]),
                "status": str(row["status"]),
                "registry_generation": int(row["registry_generation"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "terminal_at": str(row["terminal_at"]),
            }
            for row in visible
        ]
        next_cursor = None
        if len(rows) > page_limit and visible:
            last = visible[-1]
            next_cursor = _encode_cursor(
                kind="runs",
                filters=filters,
                updated_at=str(last["updated_at"]),
                item_id=str(last["run_id"]),
            )
        return {
            **_base(available=True, reason="ok"),
            "items": items,
            "next_cursor": next_cursor,
        }

    async def get_run(self, run_id: str) -> dict[str, Any]:
        clean_run_id = str(run_id or "").strip()
        if not clean_run_id:
            raise ValueError("run_id is required")
        opened = _open_source(self._source)
        if opened is None:
            return {
                **_base(available=False, reason="source_unavailable"),
                "item": None,
            }
        db, lock = opened
        try:
            async with lock:
                query_cursor = await db.execute(
                    """
                    SELECT run_id, trigger_type, status, registry_generation,
                           created_at, updated_at, terminal_at
                    FROM agent_runs
                    WHERE run_id = ?
                    """,
                    (clean_run_id,),
                )
                try:
                    row = await query_cursor.fetchone()
                finally:
                    await query_cursor.close()
        except Exception as exc:
            return {
                **_base(
                    available=False,
                    reason=f"query_failed:{type(exc).__name__}",
                ),
                "item": None,
            }
        item = None
        if row is not None:
            item = {
                "run_id": str(row["run_id"]),
                "trigger_type": str(row["trigger_type"]),
                "status": str(row["status"]),
                "registry_generation": int(row["registry_generation"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "terminal_at": str(row["terminal_at"]),
            }
        return {**_base(available=True, reason="ok"), "item": item}

    async def list_tool_calls(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        status: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        page_limit = _bounded_limit(limit)
        clean_status = str(status or "").strip()
        clean_run_id = str(run_id or "").strip()
        if clean_status and clean_status not in _TOOL_STATUSES:
            raise ValueError("invalid tool call status filter")
        filters = {"status": clean_status, "run_id": clean_run_id}
        keyset = _decode_cursor(cursor, kind="tool_calls", filters=filters)
        opened = _open_source(self._source)
        if opened is None:
            return {
                **_base(available=False, reason="source_unavailable"),
                "items": [],
                "next_cursor": None,
            }

        conditions: list[str] = []
        parameters: list[Any] = []
        if clean_status:
            conditions.append("calls.status = ?")
            parameters.append(clean_status)
        if clean_run_id:
            conditions.append("calls.run_id = ?")
            parameters.append(clean_run_id)
        if keyset is not None:
            conditions.append(
                "(calls.updated_at < ? OR "
                "(calls.updated_at = ? AND calls.call_id < ?))"
            )
            parameters.extend((keyset[0], keyset[0], keyset[1]))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(page_limit + 1)
        db, lock = opened
        try:
            async with lock:
                query_cursor = await db.execute(
                    f"""
                    SELECT calls.call_id, calls.run_id, calls.step_id,
                           calls.tool_name, calls.tool_version, calls.owner,
                           calls.effect, calls.idempotency_mode,
                           calls.concurrency_mode, calls.status,
                           calls.attempt_count, calls.created_at,
                           calls.updated_at, calls.terminal_at,
                           (
                               SELECT MIN(events.event_at)
                               FROM agent_runtime_events AS events
                               WHERE events.call_id = calls.call_id
                                 AND events.entity_kind = 'tool_call'
                                 AND events.from_status = 'proposed'
                                 AND events.to_status = 'approval_pending'
                           ) AS approval_requested_at,
                           (
                               SELECT MIN(events.event_at)
                               FROM agent_runtime_events AS events
                               WHERE events.call_id = calls.call_id
                                 AND events.entity_kind = 'tool_call'
                                 AND events.event_type = 'approval_granted'
                           ) AS approval_granted_at
                    FROM agent_tool_calls AS calls
                    {where}
                    ORDER BY calls.updated_at DESC, calls.call_id DESC
                    LIMIT ?
                    """,
                    tuple(parameters),
                )
                try:
                    rows = await query_cursor.fetchall()
                finally:
                    await query_cursor.close()
        except Exception as exc:
            return {
                **_base(
                    available=False,
                    reason=f"query_failed:{type(exc).__name__}",
                ),
                "items": [],
                "next_cursor": None,
            }

        visible = rows[:page_limit]
        items = [
            {
                "call_id": str(row["call_id"]),
                "run_id": str(row["run_id"]),
                "step_id": str(row["step_id"]),
                "tool_name": str(row["tool_name"]),
                "tool_version": str(row["tool_version"]),
                "owner": str(row["owner"]),
                "effect": str(row["effect"]),
                "idempotency_mode": str(row["idempotency_mode"]),
                "concurrency_mode": str(row["concurrency_mode"]),
                "status": str(row["status"]),
                "attempt_count": int(row["attempt_count"]),
                "approval_requested_at": (
                    str(row["approval_requested_at"])
                    if row["approval_requested_at"] is not None
                    else None
                ),
                "approval_granted_at": (
                    str(row["approval_granted_at"])
                    if row["approval_granted_at"] is not None
                    else None
                ),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "terminal_at": str(row["terminal_at"]),
            }
            for row in visible
        ]
        next_cursor = None
        if len(rows) > page_limit and visible:
            last = visible[-1]
            next_cursor = _encode_cursor(
                kind="tool_calls",
                filters=filters,
                updated_at=str(last["updated_at"]),
                item_id=str(last["call_id"]),
            )
        return {
            **_base(available=True, reason="ok"),
            "items": items,
            "next_cursor": next_cursor,
        }

    async def list_events(
        self,
        run_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        page_limit = _bounded_limit(limit)
        clean_run_id = str(run_id or "").strip()
        if not clean_run_id:
            raise ValueError("run_id is required")
        filters = {"run_id": clean_run_id}
        keyset = _decode_cursor(cursor, kind="events", filters=filters)
        after_event_id = 0
        if keyset is not None:
            if keyset[0] != keyset[1]:
                raise ValueError("invalid event cursor keyset")
            try:
                after_event_id = int(keyset[0])
            except ValueError as exc:
                raise ValueError("invalid event cursor keyset") from exc
            if after_event_id <= 0:
                raise ValueError("invalid event cursor keyset")
        opened = _open_source(self._source)
        if opened is None:
            return {
                **_base(available=False, reason="source_unavailable"),
                "items": [],
                "next_cursor": None,
            }

        db, lock = opened
        try:
            async with lock:
                query_cursor = await db.execute(
                    """
                    SELECT event_id, run_id, call_id, entity_kind, event_type,
                           from_status, to_status, event_at
                    FROM agent_runtime_events
                    WHERE run_id = ? AND event_id > ?
                    ORDER BY event_id
                    LIMIT ?
                    """,
                    (clean_run_id, after_event_id, page_limit + 1),
                )
                try:
                    rows = await query_cursor.fetchall()
                finally:
                    await query_cursor.close()
        except Exception as exc:
            return {
                **_base(
                    available=False,
                    reason=f"query_failed:{type(exc).__name__}",
                ),
                "items": [],
                "next_cursor": None,
            }

        visible = rows[:page_limit]
        items = [
            {
                "event_id": int(row["event_id"]),
                "run_id": str(row["run_id"]),
                "call_id": str(row["call_id"]),
                "entity_kind": str(row["entity_kind"]),
                "event_type": str(row["event_type"]),
                "from_status": str(row["from_status"]),
                "to_status": str(row["to_status"]),
                "event_at": str(row["event_at"]),
            }
            for row in visible
        ]
        next_cursor = None
        if len(rows) > page_limit and visible:
            last_event_id = str(visible[-1]["event_id"])
            next_cursor = _encode_cursor(
                kind="events",
                filters=filters,
                updated_at=last_event_id,
                item_id=last_event_id,
            )
        return {
            **_base(available=True, reason="ok"),
            "items": items,
            "next_cursor": next_cursor,
        }


__all__ = ["RuntimeAdminQueryV1"]
