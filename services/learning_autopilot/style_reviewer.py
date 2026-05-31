"""AI reviewer for style expressions (pending → approved/rejected)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from .base import AggressivenessConfig, CandidateItem, ReviewBatchResult, ReviewState
from .llm_assess import assess_candidate

logger = logging.getLogger(__name__)
TZ = timezone(timedelta(hours=8))

_STATE_KEY = "autopilot_style_review_state"
_DONE_KEY = "autopilot_style_review_last_done"


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


class StyleAIReviewer:
    domain = "style"

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def get_state(self) -> ReviewState:
        if not self._db_path.exists():
            return ReviewState()
        async with aiosqlite.connect(self._db_path) as db:
            state = await self._load_meta(db, _STATE_KEY, {})
            done = await self._load_meta(db, _DONE_KEY, "")
            return ReviewState(
                active=bool(state.get("active")),
                processed=int(state.get("processed", 0)),
                approved=int(state.get("approved", 0)),
                rejected=int(state.get("rejected", 0)),
                kept=int(state.get("kept", 0)),
                total_at_start=int(state.get("total_at_start", 0)),
                started_at=str(state.get("started_at", "")),
                last_progress_at=str(state.get("last_progress_at", "")),
                last_done_at=str(done),
            )

    async def reset_state(self) -> None:
        if not self._db_path.exists():
            return
        async with aiosqlite.connect(self._db_path) as db:
            await self._save_meta(db, _STATE_KEY, {})

    async def count_pending(self, config: AggressivenessConfig) -> int:
        if not self._db_path.exists():
            return 0
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM style_expressions WHERE status = 'pending'"
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def run_one_batch(
        self, *, batch_size: int, config: AggressivenessConfig, llm_client: Any
    ) -> ReviewBatchResult:
        if not self._db_path.exists():
            return ReviewBatchResult(ok=True, completed=True)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            state = await self._load_meta(db, _STATE_KEY, {})
            if not state.get("active"):
                total = await self._count_pending_inner(db)
                if total == 0:
                    await self._save_meta(db, _DONE_KEY, _now())
                    return ReviewBatchResult(ok=True, completed=True)
                state = {
                    "active": True, "processed": 0, "approved": 0, "rejected": 0,
                    "kept": 0, "total_at_start": total, "started_at": _now(), "last_id": "",
                }
                await self._save_meta(db, _STATE_KEY, state)

            last_id = str(state.get("last_id", ""))
            cur = await db.execute(
                "SELECT expression_id, situation, style, scope, group_id, confidence, meta_json "
                "FROM style_expressions WHERE status = 'pending' AND expression_id > ? "
                "ORDER BY expression_id LIMIT ?",
                (last_id, batch_size),
            )
            rows = await cur.fetchall()
            if not rows:
                state["active"] = False
                await self._save_meta(db, _STATE_KEY, state)
                await self._save_meta(db, _DONE_KEY, _now())
                return ReviewBatchResult(ok=True, completed=True, total_at_start=int(state.get("total_at_start", 0)))

            sem = asyncio.Semaphore(config.concurrency)
            items_and_rows: list[tuple[dict, CandidateItem]] = []
            for row in rows:
                d = dict(row)
                item = CandidateItem(
                    id=str(d["expression_id"]),
                    domain="style",
                    content=f"{d.get('situation', '')} / {d.get('style', '')}",
                    context=str(d.get("scope", "")),
                    group_id=str(d.get("group_id", "")),
                    confidence=float(d.get("confidence", 0.5)),
                )
                items_and_rows.append((d, item))

            async def _assess(pair: tuple[dict, CandidateItem]) -> tuple[dict, CandidateItem, Any]:
                async with sem:
                    verdict = await assess_candidate(llm_client, pair[1])
                return (pair[0], pair[1], verdict)

            results = await asyncio.gather(*[_assess(p) for p in items_and_rows])
            for d, item, verdict in results:
                await self._apply_verdict(db, d, verdict, config)
                state["last_id"] = item.id
                state["processed"] = int(state.get("processed", 0)) + 1
                state[verdict.decision] = int(state.get(verdict.decision, 0)) + 1
                state["last_progress_at"] = _now()

            await self._save_meta(db, _STATE_KEY, state)
            remaining = await self._count_pending_inner(db)
            return ReviewBatchResult(
                ok=True,
                processed_in_batch=len(rows),
                remaining=remaining,
                total_at_start=int(state.get("total_at_start", 0)),
            )

    async def _apply_verdict(
        self, db: aiosqlite.Connection, row: dict, verdict: Any, config: AggressivenessConfig,
    ) -> None:
        eid = row["expression_id"]
        meta = json.loads(row.get("meta_json") or "{}") if isinstance(row.get("meta_json"), str) else {}
        meta["ai_review"] = {
            "decision": verdict.decision, "confidence": verdict.confidence,
            "reason": verdict.reason, "reviewed_at": _now(),
        }
        meta["ai_review_decision"] = verdict.decision
        meta["ai_reviewed_at"] = _now()

        new_status = "pending"
        if verdict.decision == "approved" and verdict.confidence >= config.auto_approve_min_confidence:
            new_status = "approved"
        elif verdict.decision == "rejected" and verdict.confidence >= config.auto_reject_max_confidence:
            new_status = "rejected"

        await db.execute(
            "UPDATE style_expressions SET status = ?, meta_json = ?, updated_at = ? WHERE expression_id = ?",
            (new_status, json.dumps(meta, ensure_ascii=False), _now(), eid),
        )
        await db.commit()

    async def _count_pending_inner(self, db: aiosqlite.Connection) -> int:
        cur = await db.execute("SELECT COUNT(*) FROM style_expressions WHERE status = 'pending'")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def _load_meta(self, db: aiosqlite.Connection, key: str, default: Any) -> Any:
        try:
            cur = await db.execute("SELECT value FROM style_meta WHERE key = ?", (key,))
            row = await cur.fetchone()
            if row:
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            pass
        return default

    async def _save_meta(self, db: aiosqlite.Connection, key: str, value: Any) -> None:
        try:
            await db.execute("CREATE TABLE IF NOT EXISTS style_meta (key TEXT PRIMARY KEY, value TEXT)")
            await db.execute(
                "INSERT OR REPLACE INTO style_meta (key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("style_meta save failed: %s", exc)
