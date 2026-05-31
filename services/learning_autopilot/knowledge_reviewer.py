"""AI reviewer for knowledge graph candidates (fact + graph_relation)."""

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


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


class KnowledgeAIReviewer:
    """Reviews extraction_candidates for fact or graph_relation domain."""

    def __init__(self, db_path: Path, domain: str = "fact") -> None:
        self._db_path = db_path
        self.domain = domain
        self._state_key = f"autopilot_{domain}_review_state"
        self._done_key = f"autopilot_{domain}_review_last_done"

    async def get_state(self) -> ReviewState:
        if not self._db_path.exists():
            return ReviewState()
        async with aiosqlite.connect(self._db_path) as db:
            state = await self._load_meta(db, self._state_key, {})
            done = await self._load_meta(db, self._done_key, "")
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
            await self._save_meta(db, self._state_key, {})

    async def count_pending(self, config: AggressivenessConfig) -> int:
        if not self._db_path.exists():
            return 0
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM extraction_candidates WHERE status = 'pending'"
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
            state = await self._load_meta(db, self._state_key, {})
            if not state.get("active"):
                cur = await db.execute("SELECT COUNT(*) FROM extraction_candidates WHERE status = 'pending'")
                total = int((await cur.fetchone())[0])
                if total == 0:
                    await self._save_meta(db, self._done_key, _now())
                    return ReviewBatchResult(ok=True, completed=True)
                state = {
                    "active": True, "processed": 0, "approved": 0, "rejected": 0,
                    "kept": 0, "total_at_start": total, "started_at": _now(), "last_id": "",
                }
                await self._save_meta(db, self._state_key, state)

            last_id = str(state.get("last_id", ""))
            cur = await db.execute(
                "SELECT candidate_id, subject, predicate, object, confidence, scope_id, evidence_json, review_note "
                "FROM extraction_candidates WHERE status = 'pending' AND candidate_id > ? "
                "ORDER BY candidate_id LIMIT ?",
                (last_id, batch_size),
            )
            rows = await cur.fetchall()
            if not rows:
                state["active"] = False
                await self._save_meta(db, self._state_key, state)
                await self._save_meta(db, self._done_key, _now())
                remaining = 0
            else:
                sem = asyncio.Semaphore(config.concurrency)
                items_and_rows: list[tuple[dict, CandidateItem]] = []
                for row in rows:
                    d = dict(row)
                    triple = f"{d.get('subject', '')} → {d.get('predicate', '')} → {d.get('object', '')}"
                    evidence = str(d.get("evidence_json", ""))[:500]
                    item = CandidateItem(
                        id=str(d["candidate_id"]),
                        domain=self.domain,
                        content=triple,
                        context=evidence,
                        group_id=str(d.get("scope_id", "")),
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

                await self._save_meta(db, self._state_key, state)
                cur2 = await db.execute("SELECT COUNT(*) FROM extraction_candidates WHERE status = 'pending'")
                remaining = int((await cur2.fetchone())[0])

            return ReviewBatchResult(
                ok=True,
                processed_in_batch=len(rows) if rows else 0,
                remaining=remaining if rows else 0,
                completed=not rows,
                total_at_start=int(state.get("total_at_start", 0)),
            )

    async def _apply_verdict(
        self, db: aiosqlite.Connection, row: dict, verdict: Any, config: AggressivenessConfig,
    ) -> None:
        cid = row["candidate_id"]
        new_status = "pending"
        if verdict.decision == "approved" and verdict.confidence >= config.auto_approve_min_confidence:
            new_status = "approved"
        elif verdict.decision == "rejected" and verdict.confidence >= config.auto_reject_max_confidence:
            new_status = "rejected"

        review_note = json.dumps({
            "ai_review": {
                "decision": verdict.decision, "confidence": verdict.confidence,
                "reason": verdict.reason, "reviewed_at": _now(),
            },
        }, ensure_ascii=False)

        await db.execute(
            "UPDATE extraction_candidates SET status = ?, review_note = ?, updated_at = ? WHERE candidate_id = ?",
            (new_status, review_note, _now(), cid),
        )
        await db.commit()

    async def _load_meta(self, db: aiosqlite.Connection, key: str, default: Any) -> Any:
        try:
            await db.execute("CREATE TABLE IF NOT EXISTS kg_meta (key TEXT PRIMARY KEY, value TEXT)")
            cur = await db.execute("SELECT value FROM kg_meta WHERE key = ?", (key,))
            row = await cur.fetchone()
            if row:
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            pass
        return default

    async def _save_meta(self, db: aiosqlite.Connection, key: str, value: Any) -> None:
        try:
            await db.execute("CREATE TABLE IF NOT EXISTS kg_meta (key TEXT PRIMARY KEY, value TEXT)")
            await db.execute(
                "INSERT OR REPLACE INTO kg_meta (key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("kg_meta save failed: %s", exc)
