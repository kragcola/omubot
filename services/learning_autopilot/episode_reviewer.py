"""AI reviewer for episode candidates (candidate → approved/disabled)."""

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

_STATE_KEY = "autopilot_episode_review_state"
_DONE_KEY = "autopilot_episode_review_last_done"


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


class EpisodeAIReviewer:
    domain = "episode"

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
            return await self._count_candidate_inner(db)

    async def run_one_batch(
        self, *, batch_size: int, config: AggressivenessConfig, llm_client: Any
    ) -> ReviewBatchResult:
        if not self._db_path.exists():
            return ReviewBatchResult(ok=True, completed=True)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            state = await self._load_meta(db, _STATE_KEY, {})
            if not state.get("active"):
                total = await self._count_candidate_inner(db)
                if total == 0:
                    await self._save_meta(db, _DONE_KEY, _now())
                    return ReviewBatchResult(ok=True, completed=True)
                state = {
                    "active": True,
                    "processed": 0,
                    "approved": 0,
                    "rejected": 0,
                    "kept": 0,
                    "total_at_start": total,
                    "started_at": _now(),
                    "last_id": "",
                }
                await self._save_meta(db, _STATE_KEY, state)

            last_id = str(state.get("last_id", ""))
            cur = await db.execute(
                "SELECT episode_id, situation, reflection, group_id, confidence, meta_json "
                "FROM episodes WHERE episode_state = 'candidate' AND episode_id > ? "
                "ORDER BY episode_id LIMIT ?",
                (last_id, batch_size),
            )
            rows = [dict(r) for r in await cur.fetchall()]

            if not rows:
                # Cursor exhausted for this pass; sticky candidate/kept may remain.
                remaining = await self._count_candidate_inner(db)
                drained = remaining == 0
                state["active"] = False
                state["last_id"] = ""
                if drained:
                    await self._save_meta(db, _DONE_KEY, _now())
                await self._save_meta(db, _STATE_KEY, state)
                return ReviewBatchResult(
                    ok=True,
                    processed_in_batch=0,
                    remaining=remaining,
                    completed=drained,
                    total_at_start=int(state.get("total_at_start", 0)),
                )

            sem = asyncio.Semaphore(config.concurrency)
            items_and_rows: list[tuple[dict[str, Any], CandidateItem]] = []
            for d in rows:
                item = CandidateItem(
                    id=str(d["episode_id"]),
                    domain="episode",
                    content=f"{d.get('situation', '')} / {d.get('reflection', '')}",
                    group_id=str(d.get("group_id", "")),
                    confidence=float(d.get("confidence", 0.5) or 0.5),
                )
                items_and_rows.append((d, item))

            async def _assess(
                pair: tuple[dict[str, Any], CandidateItem],
            ) -> tuple[dict[str, Any], CandidateItem, Any]:
                async with sem:
                    verdict = await assess_candidate(llm_client, pair[1])
                return (pair[0], pair[1], verdict)

            results = await asyncio.gather(*[_assess(p) for p in items_and_rows])
            approved_n = 0
            rejected_n = 0
            kept_n = 0
            for d, _item, verdict in results:
                applied = await self._apply_verdict(db, d, verdict, config)
                if applied == "approved":
                    approved_n += 1
                elif applied == "rejected":
                    rejected_n += 1
                else:
                    kept_n += 1

            # Applied-outcome counters (not raw LLM decision).
            # enabled_for_prompt → approved; disabled → rejected; candidate → kept.
            state["processed"] = int(state.get("processed", 0)) + len(rows)
            state["approved"] = int(state.get("approved", 0)) + approved_n
            state["rejected"] = int(state.get("rejected", 0)) + rejected_n
            state["kept"] = int(state.get("kept", 0)) + kept_n
            state["last_id"] = str(rows[-1]["episode_id"])
            state["last_progress_at"] = _now()

            remaining = await self._count_candidate_inner(db)
            if remaining == 0:
                state["active"] = False
                await self._save_meta(db, _DONE_KEY, _now())
            await self._save_meta(db, _STATE_KEY, state)

            return ReviewBatchResult(
                ok=True,
                processed_in_batch=len(rows),
                approved_in_batch=approved_n,
                rejected_in_batch=rejected_n,
                kept_in_batch=kept_n,
                remaining=remaining,
                completed=remaining == 0,
                total_at_start=int(state.get("total_at_start", 0)),
            )

    async def _apply_verdict(
        self,
        db: aiosqlite.Connection,
        row: dict[str, Any],
        verdict: Any,
        config: AggressivenessConfig,
    ) -> str:
        """Apply LLM verdict with thresholds; return applied outcome.

        Episode store model is preserved:
        - applied approved → episode_state ``enabled_for_prompt``
        - applied rejected → episode_state ``disabled``
        - applied kept → remains ``candidate``
        """
        eid = row["episode_id"]
        meta = (
            json.loads(row.get("meta_json") or "{}")
            if isinstance(row.get("meta_json"), str)
            else {}
        )
        decision = str(getattr(verdict, "decision", "kept") or "kept")
        confidence = float(getattr(verdict, "confidence", 0.0) or 0.0)
        reason = str(getattr(verdict, "reason", "") or "")
        meta["ai_review"] = {
            "decision": decision,
            "confidence": confidence,
            "reason": reason,
            "reviewed_at": _now(),
        }
        meta["ai_review_decision"] = decision
        meta["ai_reviewed_at"] = _now()

        new_state = "candidate"
        applied = "kept"
        if (
            decision == "approved"
            and confidence >= config.auto_approve_min_confidence
        ):
            new_state = "enabled_for_prompt"
            applied = "approved"
        elif (
            decision == "rejected"
            and confidence >= config.auto_reject_max_confidence
        ):
            new_state = "disabled"
            applied = "rejected"

        await db.execute(
            "UPDATE episodes SET episode_state = ?, meta_json = ?, updated_at = ? "
            "WHERE episode_id = ?",
            (new_state, json.dumps(meta, ensure_ascii=False), _now(), eid),
        )
        await db.commit()
        return applied

    async def _count_candidate_inner(self, db: aiosqlite.Connection) -> int:
        cur = await db.execute(
            "SELECT COUNT(*) FROM episodes WHERE episode_state = 'candidate'"
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def _load_meta(self, db: aiosqlite.Connection, key: str, default: Any) -> Any:
        try:
            cur = await db.execute("SELECT value FROM episode_meta WHERE key = ?", (key,))
            row = await cur.fetchone()
            if row:
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            pass
        return default

    async def _save_meta(self, db: aiosqlite.Connection, key: str, value: Any) -> None:
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS episode_meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            await db.execute(
                "INSERT OR REPLACE INTO episode_meta (key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("episode_meta save failed: %s", exc)
