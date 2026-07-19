"""Concurrent slang autopilot reviewer — bypasses serial backlog reviewer."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from typing import Any

from .base import AggressivenessConfig, CandidateItem, ReviewBatchResult, ReviewState
from .llm_assess import assess_candidate

logger = logging.getLogger(__name__)

_STATE_KEY = "backlog_review_state"
_DONE_KEY = "backlog_review_last_done_at"


class SlangReviewerAdapter:
    """Concurrent slang reviewer using assess_candidate directly."""

    domain = "slang"

    def __init__(
        self, *, backlog_reviewer: Any, store: Any, message_log: Any,
        settings_loader: Any, tool_registry: Any = None,
    ) -> None:
        self._reviewer = backlog_reviewer
        self._store = store
        self._message_log = message_log
        self._settings_loader = settings_loader
        self._tool_registry = tool_registry

    async def get_state(self) -> ReviewState:
        try:
            status = await self._reviewer.status(self._store, await self._get_settings())
            return ReviewState(
                active=bool(status.get("active")),
                processed=int(status.get("processed", 0)),
                approved=int(status.get("approved", 0)),
                rejected=int(status.get("muted", 0)),
                kept=int(status.get("kept", 0)),
                total_at_start=int(status.get("total_at_start", 0)),
                remaining=int(status.get("remaining", 0)),
                started_at=str(status.get("started_at", "")),
                last_progress_at=str(status.get("last_progress_at", "")),
                last_done_at=str(status.get("last_done_at", "")),
            )
        except Exception as exc:
            logger.warning("slang adapter get_state failed: %s", exc)
            return ReviewState()

    async def reset_state(self) -> None:
        await self._reviewer.reset(self._store)

    async def count_pending(self, config: AggressivenessConfig) -> int:
        """Actionable unreviewed candidate backlog (excludes ai_reviewed_at sticky)."""
        try:
            db = self._store._require_db()
            cur = await db.execute(
                "SELECT COUNT(*) FROM slang_terms"
                " WHERE status = 'candidate'"
                " AND json_extract(meta_json, '$.ai_reviewed_at') IS NULL"
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    async def run_one_batch(
        self, *, batch_size: int, config: AggressivenessConfig, llm_client: Any
    ) -> ReviewBatchResult:
        try:
            state = await self._reviewer.get_state(self._store)
            if not isinstance(state, dict):
                state = {}
            last_term_id = str(state.get("last_term_id", "") or "")

            candidates = await self._store.list_backlog_candidates(
                after_term_id=last_term_id,
                min_confidence=0.0,
                min_usage_count=0,
                limit=batch_size,
                gated_by_threshold=False,
            )
            if not candidates and last_term_id:
                # Pass cursor exhausted; reset for a later rescan, then re-fetch.
                state["last_term_id"] = ""
                await self._store.set_meta(_STATE_KEY, state)
                candidates = await self._store.list_backlog_candidates(
                    after_term_id="",
                    min_confidence=0.0,
                    min_usage_count=0,
                    limit=batch_size,
                    gated_by_threshold=False,
                )
            if not candidates:
                remaining = await self.count_pending(config)
                drained = remaining == 0
                state["last_term_id"] = ""
                if drained:
                    # Optional done stamp when truly empty actionable backlog.
                    state["active"] = False
                    with contextlib.suppress(Exception):
                        await self._store.set_meta(_DONE_KEY, _now_iso())
                await self._store.set_meta(_STATE_KEY, state)
                return ReviewBatchResult(
                    ok=True,
                    processed_in_batch=0,
                    remaining=remaining,
                    completed=drained,
                    total_at_start=int(state.get("total_at_start", 0)),
                )

            # Skip already-reviewed sticky candidates (not actionable).
            unreviewed = [t for t in candidates if not (t.meta or {}).get("ai_reviewed_at")]
            if not unreviewed:
                # Advance cursor past already-reviewed page; report honest remaining.
                state["last_term_id"] = candidates[-1].term_id
                await self._store.set_meta(_STATE_KEY, state)
                remaining = await self.count_pending(config)
                return ReviewBatchResult(
                    ok=True,
                    processed_in_batch=0,
                    remaining=remaining,
                    completed=remaining == 0,
                    total_at_start=int(state.get("total_at_start", 0)),
                )

            sem = asyncio.Semaphore(config.concurrency)

            async def _assess(term: Any) -> tuple[Any, Any]:
                item = CandidateItem(
                    id=term.term_id,
                    domain="slang",
                    content=f"{term.term} = {term.meaning or ''}",
                    context=str(getattr(term, "notes", "") or ""),
                    group_id=str(getattr(term, "group_id", "") or ""),
                    confidence=float(getattr(term, "confidence", 0.5)),
                )
                async with sem:
                    verdict = await assess_candidate(llm_client, item)
                return (term, verdict)

            results = await asyncio.gather(*[_assess(t) for t in unreviewed])

            approved_count = 0
            rejected_count = 0
            kept_count = 0
            for term, verdict in results:
                applied = await self._apply_verdict(term, verdict, config)
                if applied == "approved":
                    approved_count += 1
                elif applied == "rejected":
                    rejected_count += 1
                else:
                    kept_count += 1

            last_id = candidates[-1].term_id
            state["last_term_id"] = last_id
            state["processed"] = int(state.get("processed", 0)) + len(unreviewed)
            state["approved"] = int(state.get("approved", 0)) + approved_count
            state["muted"] = int(state.get("muted", 0)) + rejected_count
            state["kept"] = int(state.get("kept", 0)) + kept_count
            await self._store.set_meta(_STATE_KEY, state)

            # Actionable remaining only (candidate without ai_reviewed_at).
            remaining = await self.count_pending(config)
            if remaining == 0:
                state["active"] = False
                with contextlib.suppress(Exception):
                    await self._store.set_meta(_DONE_KEY, _now_iso())
                await self._store.set_meta(_STATE_KEY, state)

            return ReviewBatchResult(
                ok=True,
                processed_in_batch=len(unreviewed),
                approved_in_batch=approved_count,
                rejected_in_batch=rejected_count,
                kept_in_batch=kept_count,
                remaining=remaining,
                completed=remaining == 0,
                total_at_start=int(state.get("total_at_start", 0)),
            )
        except Exception as exc:
            logger.exception("slang adapter run_one_batch failed")
            return ReviewBatchResult(ok=False, error=str(exc))

    async def _apply_verdict(
        self, term: Any, verdict: Any, config: AggressivenessConfig,
    ) -> str:
        """Apply LLM verdict after thresholds; return applied outcome.

        - high-confidence approved → store promote (approved)
        - high-confidence rejected → store mute (muted) counted as rejected
        - otherwise → meta-only keep (kept); term stays candidate with ai_reviewed_at
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        meta = dict(getattr(term, "meta", None) or {})
        decision = str(getattr(verdict, "decision", "kept") or "kept")
        confidence = float(getattr(verdict, "confidence", 0.0) or 0.0)
        reason = str(getattr(verdict, "reason", "") or "")

        meta["ai_reviewed_at"] = now
        meta["ai_review_source"] = "autopilot"
        meta["ai_review_decision"] = decision
        meta["ai_review_confidence"] = confidence
        meta["ai_review_reason"] = reason

        if (
            decision == "approved"
            and confidence >= config.auto_approve_min_confidence
        ):
            await self._store.update_term(term.term_id, meta=meta)
            await self._store.set_status(term.term_id, "approved", actor="ai_review")
            return "approved"

        if (
            decision == "rejected"
            and confidence >= config.auto_reject_max_confidence
        ):
            meta["ai_review_decision"] = "rejected"
            await self._store.update_term(term.term_id, meta=meta)
            await self._store.set_status(term.term_id, "muted", actor="ai_review")
            return "rejected"

        meta["ai_review_decision"] = "kept"
        await self._store.update_term(term.term_id, meta=meta)
        return "kept"

    async def _get_settings(self) -> Any:
        if callable(self._settings_loader):
            loaded = self._settings_loader()
            if inspect.isawaitable(loaded):
                return await loaded
            return loaded
        return self._settings_loader


def _now_iso() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
