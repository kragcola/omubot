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


class BlockTraceStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await connect_sqlite(self._db_path)
        await self._db.execute(_CREATE_TABLE)
        for stmt in _CREATE_INDEXES:
            await self._db.execute(stmt)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await close_with_checkpoint(self._db, name="block_trace")
            self._db = None

    async def record(self, trace: PromptBlockTrace) -> None:
        await self._insert(trace)
        await self._db.commit()

    async def record_batch(self, traces: list[PromptBlockTrace]) -> None:
        for t in traces:
            await self._insert(t)
        await self._db.commit()

    async def _insert(self, trace: PromptBlockTrace) -> None:
        tid = trace.trace_id or ("bt_" + secrets.token_hex(6))
        now = trace.created_at or _now_iso()
        await self._db.execute(
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
        cursor = await self._db.execute(
            "SELECT * FROM prompt_block_traces WHERE request_id = ? "
            "ORDER BY priority ASC, created_at ASC",
            (request_id,),
        )
        return [_row_to_trace(r) for r in await cursor.fetchall()]

    async def find_by_source_ref(
        self, *, source: str, source_id: str, limit: int = 100,
    ) -> list[PromptBlockTrace]:
        cursor = await self._db.execute(
            "SELECT * FROM prompt_block_traces "
            "WHERE source = ? AND candidate_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (source, source_id, limit),
        )
        return [_row_to_trace(r) for r in await cursor.fetchall()]

    async def recent(self, limit: int = 50) -> list[PromptBlockTrace]:
        cursor = await self._db.execute(
            "SELECT * FROM prompt_block_traces "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_trace(r) for r in await cursor.fetchall()]

    async def stats(self) -> dict[str, Any]:
        db = self._db
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
        total = int((await cursor.fetchone())["cnt"])
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
        cursor = await self._db.execute(
            "DELETE FROM prompt_block_traces WHERE created_at < ?",
            (cutoff,),
        )
        await self._db.commit()
        return cursor.rowcount
