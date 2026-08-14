"""Bounded, redacted offline Admin queries for Memory governance truth."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from services.memory.governance_contracts import Operation, ProjectionKind
from services.memory.governance_store import MemoryGovernanceStore

_CONTRACT_VERSION = "memory_governance_admin_query.v1"
_ADMIN_SCHEMA_VERSION = 1
_SOURCE_SCHEMA_VERSION = 1
_MODE = "offline_dark"


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


def _clean_enum_filter(value: object, enum_type: type[Any], field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    try:
        return str(enum_type(clean).value)
    except ValueError as exc:
        raise ValueError(f"invalid {field} filter") from exc


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
    except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
        raise ValueError("invalid memory governance cursor encoding") from exc


def _encode_cursor(
    *,
    kind: str,
    filters: Mapping[str, Any],
    recorded_at: str,
    item_id: str,
) -> str:
    payload = {
        "v": 1,
        "kind": kind,
        "filter": _filter_digest(kind, filters),
        "recorded_at": recorded_at,
        "item_id": item_id,
    }
    encoded = _b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    checksum = hashlib.sha256(
        f"memory-governance-admin-cursor-v1:{encoded}".encode()
    ).hexdigest()
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
    expected = hashlib.sha256(
        f"memory-governance-admin-cursor-v1:{encoded}".encode()
    ).hexdigest()
    if not separator or not hmac.compare_digest(checksum, expected):
        raise ValueError("invalid or tampered memory governance cursor")
    try:
        payload = json.loads(_b64decode(encoded))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid memory governance cursor payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid memory governance cursor payload")
    if (
        payload.get("v") != 1
        or payload.get("kind") != kind
        or payload.get("filter") != _filter_digest(kind, filters)
    ):
        raise ValueError("memory governance cursor does not match query filters")
    recorded_at = str(payload.get("recorded_at") or "").strip()
    item_id = str(payload.get("item_id") or "").strip()
    if not recorded_at or not item_id:
        raise ValueError("memory governance cursor is missing keyset values")
    return recorded_at, item_id


def _open_source(source: MemoryGovernanceStore | None) -> tuple[Any, Any] | None:
    if source is None:
        return None
    db = getattr(source, "_db", None)
    lock = getattr(source, "_write_lock", None)
    if db is None or lock is None:
        return None
    return db, lock


def _json_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid governance payload") from exc
    if not isinstance(parsed, dict):
        raise ValueError("invalid governance payload")
    return parsed


def _page_unavailable(reason: str = "source_unavailable") -> dict[str, Any]:
    return {
        **_base(available=False, reason=reason),
        "items": [],
        "next_cursor": None,
    }


def _detail_unavailable(reason: str = "source_unavailable") -> dict[str, Any]:
    return {**_base(available=False, reason=reason), "item": None}


class MemoryGovernanceAdminQueryV1:
    """Read-only projection over a caller-owned, already-open governance store."""

    def __init__(self, source: MemoryGovernanceStore | None) -> None:
        self._source = source

    async def summary(self) -> dict[str, Any]:
        opened = _open_source(self._source)
        if opened is None:
            return {
                **_base(available=False, reason="source_unavailable"),
                "observation_count": None,
                "candidate_count": None,
                "conflict_count": None,
                "promotion_event_count": None,
            }
        db, lock = opened
        tables = {
            "observation_count": "memory_governance_observations",
            "candidate_count": "memory_governance_candidates",
            "conflict_count": "memory_governance_conflicts",
            "promotion_event_count": "memory_governance_promotion_events",
        }
        try:
            counts: dict[str, int] = {}
            async with lock:
                for key, table in tables.items():
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
                **{key: None for key in tables},
            }
        return {**_base(available=True, reason=""), **counts}

    async def list_observations(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return await self._list_simple(
            kind="observations",
            table="memory_governance_observations",
            id_column="observation_id",
            limit=limit,
            cursor=cursor,
            filters={},
            select=(
                "observation_id, observation_sha256, payload_json, "
                "observed_at, recorded_at"
            ),
            project=self._observation_item,
        )

    async def list_candidates(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        projection_kind: str | None = None,
        operation: str | None = None,
    ) -> dict[str, Any]:
        page_limit = _bounded_limit(limit)
        clean_projection = _clean_enum_filter(
            projection_kind,
            ProjectionKind,
            "projection_kind",
        )
        clean_operation = _clean_enum_filter(operation, Operation, "operation")
        filters = {
            "projection_kind": clean_projection,
            "operation": clean_operation,
        }
        keyset = _decode_cursor(cursor, kind="candidates", filters=filters)
        opened = _open_source(self._source)
        if opened is None:
            return _page_unavailable()

        conditions: list[str] = []
        parameters: list[Any] = []
        if clean_projection:
            conditions.append("c.projection_kind = ?")
            parameters.append(clean_projection)
        if clean_operation:
            conditions.append("c.operation = ?")
            parameters.append(clean_operation)
        if keyset is not None:
            conditions.append(
                "(c.recorded_at < ? OR "
                "(c.recorded_at = ? AND c.candidate_id < ?))"
            )
            parameters.extend((keyset[0], keyset[0], keyset[1]))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(page_limit + 1)
        db, lock = opened
        try:
            async with lock:
                query_cursor = await db.execute(
                    f"""
                    SELECT c.candidate_id, c.candidate_sha256,
                           c.observation_id, c.projection_kind, c.operation,
                           c.produced_at, c.recorded_at,
                           (
                               SELECT COUNT(*)
                               FROM memory_governance_conflict_candidates AS cc
                               WHERE cc.candidate_id = c.candidate_id
                           ) AS conflict_count,
                           (
                               SELECT COUNT(*)
                               FROM memory_governance_promotion_events AS pe
                               WHERE pe.candidate_id = c.candidate_id
                           ) AS promotion_event_count
                    FROM memory_governance_candidates AS c
                    {where}
                    ORDER BY c.recorded_at DESC, c.candidate_id DESC
                    LIMIT ?
                    """,
                    tuple(parameters),
                )
                try:
                    rows = await query_cursor.fetchall()
                finally:
                    await query_cursor.close()
            visible = rows[:page_limit]
            items = [await self._candidate_item(row) for row in visible]
        except Exception as exc:
            return _page_unavailable(f"query_failed:{type(exc).__name__}")

        next_cursor = None
        if len(rows) > page_limit and visible:
            last = visible[-1]
            next_cursor = _encode_cursor(
                kind="candidates",
                filters=filters,
                recorded_at=str(last["recorded_at"]),
                item_id=str(last["candidate_id"]),
            )
        return {
            **_base(available=True, reason=""),
            "items": items,
            "next_cursor": next_cursor,
        }

    async def list_conflicts(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return await self._list_simple(
            kind="conflicts",
            table="memory_governance_conflicts",
            id_column="conflict_id",
            limit=limit,
            cursor=cursor,
            filters={},
            select=(
                "conflict_id, payload_json, detected_at, recorded_at, "
                "(SELECT COUNT(*) FROM memory_governance_conflict_observations co "
                " WHERE co.conflict_id = memory_governance_conflicts.conflict_id) "
                "AS observation_count, "
                "(SELECT COUNT(*) FROM memory_governance_conflict_candidates cc "
                " WHERE cc.conflict_id = memory_governance_conflicts.conflict_id) "
                "AS candidate_count"
            ),
            project=self._conflict_item,
        )

    async def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        clean_candidate_id = str(candidate_id or "").strip()
        if not clean_candidate_id:
            raise ValueError("candidate_id is required")
        opened = _open_source(self._source)
        if opened is None:
            return _detail_unavailable()
        db, lock = opened
        try:
            async with lock:
                cursor = await db.execute(
                    """
                    SELECT c.candidate_id, c.candidate_sha256,
                           c.observation_id, c.projection_kind, c.operation,
                           c.produced_at, c.recorded_at,
                           (
                               SELECT COUNT(*)
                               FROM memory_governance_conflict_candidates AS cc
                               WHERE cc.candidate_id = c.candidate_id
                           ) AS conflict_count,
                           (
                               SELECT COUNT(*)
                               FROM memory_governance_promotion_events AS pe
                               WHERE pe.candidate_id = c.candidate_id
                           ) AS promotion_event_count
                    FROM memory_governance_candidates AS c
                    WHERE c.candidate_id = ?
                    """,
                    (clean_candidate_id,),
                )
                try:
                    row = await cursor.fetchone()
                finally:
                    await cursor.close()
            item = await self._candidate_item(row, detail=True) if row is not None else None
        except Exception as exc:
            return _detail_unavailable(f"query_failed:{type(exc).__name__}")
        return {**_base(available=True, reason=""), "item": item}

    async def _list_simple(
        self,
        *,
        kind: str,
        table: str,
        id_column: str,
        limit: object,
        cursor: str | None,
        filters: Mapping[str, Any],
        select: str,
        project: Any,
    ) -> dict[str, Any]:
        page_limit = _bounded_limit(limit)
        keyset = _decode_cursor(cursor, kind=kind, filters=filters)
        opened = _open_source(self._source)
        if opened is None:
            return _page_unavailable()
        conditions: list[str] = []
        parameters: list[Any] = []
        if keyset is not None:
            conditions.append(
                f"(recorded_at < ? OR (recorded_at = ? AND {id_column} < ?))"
            )
            parameters.extend((keyset[0], keyset[0], keyset[1]))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(page_limit + 1)
        db, lock = opened
        try:
            async with lock:
                query_cursor = await db.execute(
                    f"""
                    SELECT {select}
                    FROM {table}
                    {where}
                    ORDER BY recorded_at DESC, {id_column} DESC
                    LIMIT ?
                    """,
                    tuple(parameters),
                )
                try:
                    rows = await query_cursor.fetchall()
                finally:
                    await query_cursor.close()
            visible = rows[:page_limit]
            items = [project(row) for row in visible]
        except Exception as exc:
            return _page_unavailable(f"query_failed:{type(exc).__name__}")
        next_cursor = None
        if len(rows) > page_limit and visible:
            last = visible[-1]
            next_cursor = _encode_cursor(
                kind=kind,
                filters=filters,
                recorded_at=str(last["recorded_at"]),
                item_id=str(last[id_column]),
            )
        return {
            **_base(available=True, reason=""),
            "items": items,
            "next_cursor": next_cursor,
        }

    @staticmethod
    def _observation_item(row: Any) -> dict[str, Any]:
        payload = _json_object(row["payload_json"])
        evidence = payload.get("evidence")
        evidence_count = len(evidence) if isinstance(evidence, list) else 0
        return {
            "contract_version": str(
                payload.get("contract_version") or "memory.observation.v1"
            ),
            "observation_id": str(row["observation_id"]),
            "observation_sha256": str(row["observation_sha256"]),
            "source_kind": str(payload.get("source_kind") or ""),
            "producer_kind": str(payload.get("producer_kind") or ""),
            "evidence_count": evidence_count,
            "observed_at": str(row["observed_at"]),
        }

    async def _candidate_item(
        self,
        row: Any,
        *,
        detail: bool = False,
    ) -> dict[str, Any]:
        source = self._source
        if source is None:
            raise RuntimeError("memory governance source is unavailable")
        event_count = int(row["promotion_event_count"])
        conflict_count = int(row["conflict_count"])
        fold = (
            await source.fold_candidate(str(row["candidate_id"]))
            if event_count
            else None
        )
        item = {
            "contract_version": "memory.candidate.v1",
            "candidate_id": str(row["candidate_id"]),
            "candidate_sha256": str(row["candidate_sha256"]),
            "observation_id": str(row["observation_id"]),
            "projection_kind": str(row["projection_kind"]),
            "operation": str(row["operation"]),
            "fold_status": str(fold.status) if fold is not None else "unreviewed",
            "conflict_count": conflict_count,
            "promotion_event_count": event_count,
            "produced_at": str(row["produced_at"]),
        }
        if detail:
            item.update(
                {
                    "resolved_conflict_count": (
                        len(fold.resolved_conflict_ids) if fold is not None else 0
                    ),
                    "unresolved_conflict_count": (
                        len(fold.unresolved_conflict_ids)
                        if fold is not None
                        else conflict_count
                    ),
                }
            )
        return item

    @staticmethod
    def _conflict_item(row: Any) -> dict[str, Any]:
        payload = _json_object(row["payload_json"])
        return {
            "contract_version": str(
                payload.get("contract_version") or "memory.conflict.v1"
            ),
            "conflict_id": str(row["conflict_id"]),
            "kind": str(payload.get("kind") or ""),
            "observation_count": int(row["observation_count"]),
            "candidate_count": int(row["candidate_count"]),
            "detected_at": str(row["detected_at"]),
        }


__all__ = ["MemoryGovernanceAdminQueryV1"]
