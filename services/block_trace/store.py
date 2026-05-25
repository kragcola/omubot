"""BlockTraceStore — SQLite persistence for prompt-block trace records."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite

from services.block_trace.types import PromptBlockTrace
from services.storage import close_with_checkpoint, connect_sqlite

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS prompt_block_traces (
    trace_id        TEXT PRIMARY KEY,
    request_id      TEXT NOT NULL,
    task            TEXT NOT NULL,
    source          TEXT NOT NULL,
    provider        TEXT NOT NULL,
    candidate_id    TEXT NOT NULL,
    decision        TEXT NOT NULL,
    hit_reason      TEXT NOT NULL DEFAULT '',
    evidence_refs   TEXT NOT NULL DEFAULT '[]',
    token_estimate  INTEGER NOT NULL DEFAULT 0,
    char_count      INTEGER NOT NULL DEFAULT 0,
    position        TEXT NOT NULL DEFAULT 'dynamic',
    label           TEXT NOT NULL DEFAULT '',
    priority        INTEGER NOT NULL DEFAULT 100,
    decay_state     TEXT NOT NULL DEFAULT '',
    budget_reason   TEXT NOT NULL DEFAULT '',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
)"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_bt_request ON prompt_block_traces(request_id)",
    "CREATE INDEX IF NOT EXISTS idx_bt_source ON prompt_block_traces(source, candidate_id)",
    "CREATE INDEX IF NOT EXISTS idx_bt_created ON prompt_block_traces(created_at)",
]

_CREATE_HUMANIZATION_METRICS_TABLE = """\
CREATE TABLE IF NOT EXISTS humanization_metrics (
    metric_id       TEXT PRIMARY KEY,
    request_id      TEXT NOT NULL,
    group_id        TEXT NOT NULL DEFAULT '',
    session_id      TEXT NOT NULL DEFAULT '',
    turn_id         TEXT NOT NULL DEFAULT '',
    score           REAL NOT NULL DEFAULT 0,
    axes_json       TEXT NOT NULL DEFAULT '{}',
    issues_json     TEXT NOT NULL DEFAULT '[]',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
)"""

_CREATE_HUMANIZATION_METRICS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_hm_request ON humanization_metrics(request_id)",
    "CREATE INDEX IF NOT EXISTS idx_hm_group_session_created "
    "ON humanization_metrics(group_id, session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_hm_created ON humanization_metrics(created_at)",
]


def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")


def _row_to_trace(row: aiosqlite.Row) -> PromptBlockTrace:
    keys = row.keys()
    refs_raw = row["evidence_refs"] if "evidence_refs" in keys else "[]"
    try:
        evidence_refs = tuple(json.loads(refs_raw or "[]"))
    except (json.JSONDecodeError, TypeError):
        evidence_refs = ()
    meta_raw = row["metadata_json"] if "metadata_json" in keys else "{}"
    try:
        metadata = json.loads(meta_raw or "{}")
    except (json.JSONDecodeError, TypeError):
        metadata = {}
    return PromptBlockTrace(
        trace_id=row["trace_id"],
        request_id=row["request_id"],
        task=row["task"],
        source=row["source"],
        provider=row["provider"],
        candidate_id=row["candidate_id"],
        decision=row["decision"],
        hit_reason=row["hit_reason"] if "hit_reason" in keys else "",
        evidence_refs=evidence_refs,
        token_estimate=int(row["token_estimate"]) if "token_estimate" in keys else 0,
        char_count=int(row["char_count"]) if "char_count" in keys else 0,
        position=row["position"] if "position" in keys else "dynamic",
        label=row["label"] if "label" in keys else "",
        priority=int(row["priority"]) if "priority" in keys else 100,
        decay_state=row["decay_state"] if "decay_state" in keys else "",
        budget_reason=row["budget_reason"] if "budget_reason" in keys else "",
        metadata=metadata,
        created_at=row["created_at"],
    )


def _json_obj(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _row_to_metric(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "metric_id": row["metric_id"],
        "request_id": row["request_id"],
        "group_id": row["group_id"],
        "session_id": row["session_id"],
        "turn_id": row["turn_id"],
        "score": float(row["score"]),
        "axes": _json_obj(row["axes_json"]),
        "issues": _json_list(row["issues_json"]),
        "metadata": _json_obj(row["metadata_json"]),
        "created_at": row["created_at"],
    }


def _score_payload(score: Any) -> tuple[float, dict[str, Any], list[str], dict[str, Any]]:
    if isinstance(score, dict):
        total = score.get("score", score.get("total", 0))
        axes = score.get("axes", {})
        issues = score.get("issues", [])
        metadata = score.get("metadata", score.get("meta", {}))
    else:
        total = getattr(score, "total", 0)
        axes = getattr(score, "axes", {})
        issues = getattr(score, "issues", [])
        metadata = getattr(score, "meta", {})
    try:
        total_value = float(total)
    except (TypeError, ValueError):
        total_value = 0.0
    if not isinstance(axes, dict):
        axes = {}
    if not isinstance(metadata, dict):
        metadata = {}
    if not isinstance(issues, list):
        issues = list(issues) if isinstance(issues, tuple) else []
    return total_value, axes, [str(item) for item in issues], metadata


class BlockTraceStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("BlockTraceStore is not initialized")
        return self._db

    async def init(self) -> None:
        db = await connect_sqlite(self._db_path)
        self._db = db
        await db.execute(_CREATE_TABLE)
        for stmt in _CREATE_INDEXES:
            await db.execute(stmt)
        await db.execute(_CREATE_HUMANIZATION_METRICS_TABLE)
        for stmt in _CREATE_HUMANIZATION_METRICS_INDEXES:
            await db.execute(stmt)
        await db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await close_with_checkpoint(self._db, name="block_trace")
            self._db = None

    async def record(self, trace: PromptBlockTrace) -> None:
        await self._insert(trace)
        await self._conn().commit()

    async def record_batch(self, traces: list[PromptBlockTrace]) -> None:
        for t in traces:
            await self._insert(t)
        await self._conn().commit()

    async def _insert(self, trace: PromptBlockTrace) -> None:
        tid = trace.trace_id or ("bt_" + secrets.token_hex(6))
        now = trace.created_at or _now_iso()
        await self._conn().execute(
            """INSERT OR IGNORE INTO prompt_block_traces
               (trace_id, request_id, task, source, provider,
                candidate_id, decision, hit_reason, evidence_refs,
                token_estimate, char_count, position, label, priority,
                decay_state, budget_reason, metadata_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                tid, trace.request_id, trace.task, trace.source,
                trace.provider, trace.candidate_id, trace.decision,
                trace.hit_reason,
                json.dumps(list(trace.evidence_refs), ensure_ascii=False),
                trace.token_estimate, trace.char_count, trace.position,
                trace.label, trace.priority,
                trace.decay_state, trace.budget_reason,
                json.dumps(trace.metadata, ensure_ascii=False),
                now,
            ),
        )

    async def list_for_request(
        self, request_id: str,
    ) -> list[PromptBlockTrace]:
        cursor = await self._conn().execute(
            "SELECT * FROM prompt_block_traces WHERE request_id = ? "
            "ORDER BY priority ASC, created_at ASC",
            (request_id,),
        )
        return [_row_to_trace(r) for r in await cursor.fetchall()]

    async def find_by_source_ref(
        self, *, source: str, source_id: str, limit: int = 100,
    ) -> list[PromptBlockTrace]:
        cursor = await self._conn().execute(
            "SELECT * FROM prompt_block_traces "
            "WHERE source = ? AND candidate_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (source, source_id, limit),
        )
        return [_row_to_trace(r) for r in await cursor.fetchall()]

    async def recent(self, limit: int = 50) -> list[PromptBlockTrace]:
        cursor = await self._conn().execute(
            "SELECT * FROM prompt_block_traces "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_trace(r) for r in await cursor.fetchall()]

    async def record_humanization_metrics(
        self,
        *,
        request_id: str,
        score: Any,
        group_id: str = "",
        session_id: str = "",
        turn_id: str = "",
        metadata: dict[str, Any] | None = None,
        metric_id: str = "",
        created_at: str = "",
    ) -> str:
        total, axes, issues, score_meta = _score_payload(score)
        merged_meta = dict(score_meta)
        if metadata:
            merged_meta.update(metadata)
        mid = metric_id or ("hm_" + secrets.token_hex(6))
        await self._conn().execute(
            """INSERT OR REPLACE INTO humanization_metrics
               (metric_id, request_id, group_id, session_id, turn_id, score,
                axes_json, issues_json, metadata_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                mid,
                request_id,
                group_id,
                session_id,
                turn_id,
                total,
                json.dumps(axes, ensure_ascii=False),
                json.dumps(issues, ensure_ascii=False),
                json.dumps(merged_meta, ensure_ascii=False),
                created_at or _now_iso(),
            ),
        )
        await self._conn().commit()
        return mid

    async def list_humanization_metrics(
        self,
        *,
        request_id: str | None = None,
        group_id: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if request_id is not None:
            clauses.append("request_id = ?")
            params.append(request_id)
        if group_id is not None:
            clauses.append("group_id = ?")
            params.append(group_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        cursor = await self._conn().execute(
            f"SELECT * FROM humanization_metrics {where}ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        return [_row_to_metric(r) for r in await cursor.fetchall()]

    async def humanization_metric_stats(self, *, group_id: str | None = None) -> dict[str, Any]:
        params: tuple[Any, ...] = (group_id,) if group_id is not None else ()
        where = "WHERE group_id = ?" if group_id is not None else ""
        cursor = await self._conn().execute(
            f"SELECT COUNT(*) AS cnt, AVG(score) AS avg_score FROM humanization_metrics {where}",
            params,
        )
        row = await cursor.fetchone()
        if row is None:
            return {"total": 0, "avg_score": 0.0, "by_issue": {}}
        rows = await self.list_humanization_metrics(group_id=group_id, limit=1000)
        by_issue: dict[str, int] = {}
        for item in rows:
            for issue in item["issues"]:
                by_issue[issue] = by_issue.get(issue, 0) + 1
        return {
            "total": int(row["cnt"]),
            "avg_score": float(row["avg_score"] or 0.0),
            "by_issue": by_issue,
        }

    async def stats(self) -> dict[str, Any]:
        db = self._conn()
        cursor = await db.execute(
            "SELECT decision, COUNT(*) AS cnt "
            "FROM prompt_block_traces GROUP BY decision"
        )
        by_decision = {r["decision"]: r["cnt"] for r in await cursor.fetchall()}
        cursor = await db.execute(
            "SELECT source, COUNT(*) AS cnt "
            "FROM prompt_block_traces GROUP BY source"
        )
        by_source = {r["source"]: r["cnt"] for r in await cursor.fetchall()}
        cursor = await db.execute(
            "SELECT position, COUNT(*) AS cnt "
            "FROM prompt_block_traces GROUP BY position"
        )
        by_position = {r["position"]: r["cnt"] for r in await cursor.fetchall()}
        cursor = await db.execute(
            "SELECT COUNT(*) AS cnt FROM prompt_block_traces"
        )
        row = await cursor.fetchone()
        total = int(row["cnt"]) if row is not None else 0
        return {
            "total": total,
            "by_decision": by_decision,
            "by_source": by_source,
            "by_position": by_position,
        }

    async def prune(self, *, keep_days: int = 7) -> int:
        cutoff = (
            datetime.now(TZ_SHANGHAI) - timedelta(days=keep_days)
        ).isoformat(timespec="seconds")
        db = self._conn()
        cursor = await db.execute(
            "DELETE FROM prompt_block_traces WHERE created_at < ?",
            (cutoff,),
        )
        await db.execute(
            "DELETE FROM humanization_metrics WHERE created_at < ?",
            (cutoff,),
        )
        await db.commit()
        return cursor.rowcount
