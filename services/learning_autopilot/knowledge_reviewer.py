"""AI reviewer for knowledge graph candidates (canonical fact domain).

Promotes high-confidence approvals through KnowledgeGraphService so
graph_facts + evidence + listeners are materialized. Never writes the
invalid intermediate status ``approved`` (not in GraphStatus).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from services.knowledge_graph.service import KnowledgeGraphService

from .base import AggressivenessConfig, CandidateItem, ReviewBatchResult, ReviewState, ReviewVerdict
from .llm_assess import assess_candidate

logger = logging.getLogger(__name__)
TZ = timezone(timedelta(hours=8))

_LEGACY_APPROVED = "approved"
_BACKLOG_STATUSES = ("pending", _LEGACY_APPROVED)


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _serialize_ai_review(
    decision: str,
    confidence: float,
    reason: str,
    *,
    repaired: bool = False,
) -> str:
    payload: dict[str, Any] = {
        "ai_review": {
            "decision": decision,
            "confidence": confidence,
            "reason": reason,
            "reviewed_at": _now(),
        },
    }
    if repaired:
        payload["ai_review"]["repaired_legacy_approved"] = True
    return json.dumps(payload, ensure_ascii=False)


_VALID_DECISIONS = frozenset({"approved", "rejected", "kept"})


def _parse_strict_confidence(raw: Any) -> float | None:
    """Return confidence only for exact int/float (not bool), finite, in [0, 1]."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    conf_f = float(raw)
    if not math.isfinite(conf_f):
        return None
    if conf_f < 0.0 or conf_f > 1.0:
        return None
    return conf_f


def _normalize_llm_verdict(verdict: ReviewVerdict | Any) -> tuple[str, float, str]:
    """Strictly validate LLM verdict before promotion.

    - decision must be exact ``approved`` / ``rejected`` / ``kept`` (unknown → kept)
    - confidence must be exact int/float (not bool), finite, within [0, 1]
    Invalid confidence forces decision ``kept`` so nothing auto-approves/rejects.
    """
    raw_decision = getattr(verdict, "decision", "kept")
    decision = (
        raw_decision
        if isinstance(raw_decision, str) and raw_decision in _VALID_DECISIONS
        else "kept"
    )

    conf = _parse_strict_confidence(getattr(verdict, "confidence", None))
    reason = str(getattr(verdict, "reason", "") or "")
    if conf is None:
        return "kept", 0.0, reason or "invalid confidence; fail closed to kept"
    if decision == "kept" and raw_decision not in _VALID_DECISIONS:
        return "kept", conf, reason or "unknown decision; fail closed to kept"
    return decision, conf, reason


def _parse_legacy_repairable_ai_review(
    review_note: str,
    *,
    min_confidence: float,
) -> tuple[float, str] | None:
    """Return (confidence, reason) if legacy note is safe to repair without LLM.

    Strict contract:
    - decision must be exactly the string ``approved``
    - confidence must be a finite non-bool numeric in [0, 1] meeting ``min_confidence``
    Malformed metadata returns None (fail closed → re-review as pending).
    """
    if not review_note or not isinstance(review_note, str):
        return None
    try:
        data = json.loads(review_note)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    ai = data.get("ai_review")
    if not isinstance(ai, dict):
        return None
    if ai.get("decision") != "approved":
        return None
    conf_f = _parse_strict_confidence(ai.get("confidence"))
    if conf_f is None:
        return None
    if conf_f < min_confidence:
        return None
    reason = str(ai.get("reason") or "legacy repair")
    return conf_f, reason


class KnowledgeAIReviewer:
    """Reviews extraction_candidates via the live KnowledgeGraphService.

    Domain is always the canonical ``fact`` noun for the shared table.
    ``graph_relation`` consolidator inventory is a separate pipeline and is
    not promoted by this reviewer.
    """

    domain = "fact"

    def __init__(
        self,
        graph: KnowledgeGraphService,
        *,
        domain: str = "fact",
    ) -> None:
        self._graph = graph
        # Accept fact / legacy graph_relation only; both canonicalize to fact.
        if domain not in {"fact", "graph_relation"}:
            raise ValueError(
                f"KnowledgeAIReviewer domain must be 'fact' or legacy "
                f"'graph_relation', got {domain!r}"
            )
        self.domain = "fact"
        self._state_key = f"autopilot_{self.domain}_review_state"
        self._done_key = f"autopilot_{self.domain}_review_last_done"

    @property
    def _db_path(self) -> Path:
        return Path(self._graph.db_path)

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
            placeholders = ", ".join("?" for _ in _BACKLOG_STATUSES)
            cur = await db.execute(
                f"SELECT COUNT(*) FROM extraction_candidates "
                f"WHERE status IN ({placeholders})",
                _BACKLOG_STATUSES,
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def run_one_batch(
        self, *, batch_size: int, config: AggressivenessConfig, llm_client: Any
    ) -> ReviewBatchResult:
        if not self._db_path.exists():
            return ReviewBatchResult(ok=True, completed=True)

        # State meta uses a short-lived connection; promotions go through the
        # injected KnowledgeGraphService (single connection owner for facts).
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            state = await self._load_meta(db, self._state_key, {})
            backlog_total = await self._count_backlog(db)
            if not state.get("active"):
                if backlog_total == 0:
                    await self._save_meta(db, self._done_key, _now())
                    return ReviewBatchResult(ok=True, completed=True)
                state = {
                    "active": True,
                    "processed": 0,
                    "approved": 0,
                    "rejected": 0,
                    "kept": 0,
                    "total_at_start": backlog_total,
                    "started_at": _now(),
                    "last_id": "",
                }
                await self._save_meta(db, self._state_key, state)

            last_id = str(state.get("last_id", ""))
            placeholders = ", ".join("?" for _ in _BACKLOG_STATUSES)
            cur = await db.execute(
                "SELECT candidate_id, subject, predicate, object, confidence, "
                "scope_id, evidence_json, review_note, status "
                f"FROM extraction_candidates WHERE status IN ({placeholders}) "
                "AND candidate_id > ? ORDER BY candidate_id LIMIT ?",
                (*_BACKLOG_STATUSES, last_id, batch_size),
            )
            rows = [dict(r) for r in await cur.fetchall()]

        if not rows:
            # Cursor exhausted for this pass, but sticky kept/pending rows may
            # still sit at or before last_id. Report the true backlog; only
            # mark completed + last_done when remaining is actually zero.
            # Reset last_id / inactive so a later explicit run can re-scan.
            async with aiosqlite.connect(self._db_path) as db:
                remaining = await self._count_backlog(db)
                drained = remaining == 0
                state["active"] = False
                state["last_id"] = ""
                if drained:
                    await self._save_meta(db, self._done_key, _now())
                await self._save_meta(db, self._state_key, state)
            return ReviewBatchResult(
                ok=True,
                processed_in_batch=0,
                remaining=remaining,
                completed=drained,
                total_at_start=int(state.get("total_at_start", 0)),
            )

        approved_n = 0
        rejected_n = 0
        kept_n = 0
        sem = asyncio.Semaphore(config.concurrency)

        # Phase 1: legacy repair (no LLM) + queue LLM items
        to_assess: list[tuple[dict[str, Any], CandidateItem]] = []

        for d in rows:
            cid = str(d["candidate_id"])
            status = str(d.get("status") or "")
            if status == _LEGACY_APPROVED:
                repairable = _parse_legacy_repairable_ai_review(
                    str(d.get("review_note") or ""),
                    min_confidence=config.auto_approve_min_confidence,
                )
                if repairable is not None:
                    conf_f, reason = repairable
                    note = _serialize_ai_review(
                        "approved", conf_f, reason, repaired=True,
                    )
                    fact = await self._graph.approve_candidate(
                        cid,
                        review_note=note,
                        allow_legacy_approved=True,
                    )
                    if fact is not None:
                        approved_n += 1
                    else:
                        # Promotion failed: leave observable, do not claim approved
                        await self._graph.keep_candidate(
                            cid,
                            note=_serialize_ai_review(
                                "kept", conf_f, "legacy repair promotion failed",
                            ),
                        )
                        kept_n += 1
                    continue
                # Malformed legacy approved: re-queue to pending then re-review
                await self._graph.keep_candidate(
                    cid,
                    note=_serialize_ai_review(
                        "kept",
                        0.0,
                        "malformed legacy approved re-queued for re-review",
                    ),
                )
                # Fall through as pending for LLM assessment in this batch
                d["status"] = "pending"

            triple = f"{d.get('subject', '')} → {d.get('predicate', '')} → {d.get('object', '')}"
            evidence = str(d.get("evidence_json", ""))[:500]
            item = CandidateItem(
                id=cid,
                domain=self.domain,
                content=triple,
                context=evidence,
                group_id=str(d.get("scope_id", "")),
                confidence=float(d.get("confidence", 0.5) or 0.5),
            )
            to_assess.append((d, item))

        async def _assess(
            pair: tuple[dict[str, Any], CandidateItem],
        ) -> tuple[dict[str, Any], CandidateItem, ReviewVerdict]:
            async with sem:
                verdict = await assess_candidate(llm_client, pair[1])
            return (pair[0], pair[1], verdict)

        if to_assess:
            results = await asyncio.gather(*[_assess(p) for p in to_assess])
            for d, _item, verdict in results:
                applied = await self._apply_verdict(d, verdict, config)
                if applied == "approved":
                    approved_n += 1
                elif applied == "rejected":
                    rejected_n += 1
                else:
                    kept_n += 1

        # Update state counters from *applied* batch counters only
        state["processed"] = int(state.get("processed", 0)) + len(rows)
        state["approved"] = int(state.get("approved", 0)) + approved_n
        state["rejected"] = int(state.get("rejected", 0)) + rejected_n
        state["kept"] = int(state.get("kept", 0)) + kept_n
        if rows:
            state["last_id"] = str(rows[-1]["candidate_id"])
        state["last_progress_at"] = _now()

        async with aiosqlite.connect(self._db_path) as db:
            remaining = await self._count_backlog(db)
            if remaining == 0:
                state["active"] = False
                await self._save_meta(db, self._done_key, _now())
            await self._save_meta(db, self._state_key, state)

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
        row: dict[str, Any],
        verdict: ReviewVerdict | Any,
        config: AggressivenessConfig,
    ) -> str:
        """Apply strictly validated LLM verdict via service; return applied decision."""
        cid = str(row["candidate_id"])
        decision, confidence, reason = _normalize_llm_verdict(verdict)
        note = _serialize_ai_review(decision, confidence, reason)

        if (
            decision == "approved"
            and confidence >= config.auto_approve_min_confidence
        ):
            fact = await self._graph.approve_candidate(
                cid, review_note=note, allow_legacy_approved=True,
            )
            if fact is not None:
                return "approved"
            # Promotion failed: keep pending, do not claim approved
            await self._graph.keep_candidate(
                cid,
                note=_serialize_ai_review(
                    "kept", confidence, f"promotion failed: {reason}",
                ),
            )
            return "kept"

        if (
            decision == "rejected"
            and confidence >= config.auto_reject_max_confidence
        ):
            ok = await self._graph.reject_candidate(cid, note=note)
            if ok:
                return "rejected"
            await self._graph.keep_candidate(
                cid,
                note=_serialize_ai_review(
                    "kept", confidence, f"reject failed: {reason}",
                ),
            )
            return "kept"

        # Low confidence / kept / invalid: leave valid pending with review note
        await self._graph.keep_candidate(cid, note=note)
        return "kept"

    async def _count_backlog(self, db: aiosqlite.Connection) -> int:
        placeholders = ", ".join("?" for _ in _BACKLOG_STATUSES)
        cur = await db.execute(
            f"SELECT COUNT(*) FROM extraction_candidates WHERE status IN ({placeholders})",
            _BACKLOG_STATUSES,
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def _load_meta(self, db: aiosqlite.Connection, key: str, default: Any) -> Any:
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS kg_meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            cur = await db.execute("SELECT value FROM kg_meta WHERE key = ?", (key,))
            row = await cur.fetchone()
            if row:
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except Exception:
            pass
        return default

    async def _save_meta(self, db: aiosqlite.Connection, key: str, value: Any) -> None:
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS kg_meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            await db.execute(
                "INSERT OR REPLACE INTO kg_meta (key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("kg_meta save failed: %s", exc)
