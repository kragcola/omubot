"""AI reviewer that processes the existing candidate backlog one batch per tick.

Walks the candidate backlog in confidence-priority order, calls the review LLM
(with optional web search), and either promotes the term to ``approved``, mutes
it, or leaves it for the next round. Supports auto-approve for high-confidence
candidates with search evidence.

Progress is persisted in the ``slang_settings`` meta table so a single tick
processes one batch (default 50) and the next tick resumes via cursor — no
single 600s ``wait_for`` window has to encompass the entire 1000+ row pool.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from services.slang.errors import SlangCollisionError
from services.slang.quality import assess_candidate_quality
from services.slang.review_utils import (
    SlangReviewAssessment,
    assess_with_llm,
    build_search_queries,
    run_web_search,
)
from services.slang.types import SlangExtraction, SlangSettings, SlangTerm

if TYPE_CHECKING:
    from services.slang.store import SlangStore

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

_BACKLOG_STATE_KEY = "backlog_review_state"
_BACKLOG_LAST_DONE_KEY = "backlog_review_last_done_at"

# Confidence floor below which a "not approved" assessment is treated as
# undecided and the candidate is kept rather than muted. Avoids muting on a
# single low-signal LLM disagreement.
_MUTE_MIN_CONFIDENCE = 0.5


def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")


def _fresh_state(total: int) -> dict[str, Any]:
    return {
        "active": True,
        "started_at": _now_iso(),
        "total_at_start": int(total),
        "processed": 0,
        "approved": 0,
        "muted": 0,
        "kept": 0,
        "last_term_id": "",
        "last_run_id": "",
        "last_progress_at": "",
    }


def _term_to_extraction(term: SlangTerm) -> SlangExtraction:
    """Adapt a stored candidate row to the assess_with_llm input shape."""
    evidence = ""
    meta = term.meta or {}
    if isinstance(meta, dict):
        evidence = str(meta.get("group_evidence") or meta.get("evidence") or "")
    return SlangExtraction(
        term=term.term,
        meaning=term.meaning,
        aliases=list(term.aliases),
        evidence=evidence,
        confidence=float(term.confidence),
        reason="",
        repeat_policy=term.repeat_policy,
    )


class SlangBacklogReviewer:
    """Process the slang candidate backlog in resumable batches."""

    def __init__(self, llm_client: Any = None) -> None:
        self._llm_client = llm_client

    async def get_state(self, store: SlangStore) -> dict[str, Any]:
        raw = await store.get_meta(_BACKLOG_STATE_KEY, {}) or {}
        if not isinstance(raw, dict):
            return {}
        return dict(raw)

    async def reset(self, store: SlangStore) -> dict[str, Any]:
        """Force the state machine back to inactive so the next tick re-counts."""
        cleared = {
            "active": False,
            "processed": 0,
            "approved": 0,
            "muted": 0,
            "kept": 0,
            "last_term_id": "",
            "total_at_start": 0,
            "started_at": "",
            "last_run_id": "",
            "last_progress_at": "",
        }
        await store.set_meta(_BACKLOG_STATE_KEY, cleared)
        return cleared

    async def status(self, store: SlangStore, settings: SlangSettings | None = None) -> dict[str, Any]:
        state = await self.get_state(store)
        last_done_at = await store.get_meta(_BACKLOG_LAST_DONE_KEY, "") or ""
        # Live remaining count using same filters as run_one_batch.
        min_confidence = float(settings.backlog_review_min_confidence) if settings else 0.0
        min_usage_count = int(getattr(settings, "backlog_review_min_usage_count", 3)) if settings else 3
        gated = bool(getattr(settings, "backlog_threshold_gating_enabled", True)) if settings else True
        remaining = await store.count_backlog_candidates(
            min_confidence=min_confidence,
            min_usage_count=min_usage_count,
            gated_by_threshold=gated,
        )
        return {
            "active": bool(state.get("active", False)),
            "processed": int(state.get("processed", 0)),
            "approved": int(state.get("approved", 0)),
            "muted": int(state.get("muted", 0)),
            "kept": int(state.get("kept", 0)),
            "total_at_start": int(state.get("total_at_start", 0)),
            "started_at": str(state.get("started_at", "") or ""),
            "last_progress_at": str(state.get("last_progress_at", "") or ""),
            "last_run_id": str(state.get("last_run_id", "") or ""),
            "last_done_at": str(last_done_at),
            "remaining": int(remaining),
        }

    async def run_one_batch(
        self,
        *,
        store: SlangStore,
        message_log: Any,
        settings: SlangSettings,
        tool_registry: Any = None,
        group_filter: Callable[[str | None], bool] | None = None,
        batch_size_override: int | None = None,
        min_confidence_override: float | None = None,
    ) -> dict[str, Any]:
        if self._llm_client is None:
            return {"ok": False, "error": "LLMClient not available"}

        batch_size = int(batch_size_override or settings.backlog_review_batch_size)
        min_confidence = float(
            min_confidence_override
            if min_confidence_override is not None
            else settings.backlog_review_min_confidence
        )
        min_usage_count = int(getattr(settings, "backlog_review_min_usage_count", 3))
        approve_threshold = settings.backlog_auto_approve_min_confidence

        state = await self.get_state(store)
        gated = bool(getattr(settings, "backlog_threshold_gating_enabled", True))
        if not state.get("active"):
            total = await store.count_backlog_candidates(
                min_confidence=min_confidence,
                min_usage_count=min_usage_count,
                gated_by_threshold=gated,
            )
            if total == 0:
                cleared = _fresh_state(0)
                cleared["active"] = False
                await store.set_meta(_BACKLOG_STATE_KEY, cleared)
                await store.set_meta(_BACKLOG_LAST_DONE_KEY, _now_iso())
                return {
                    "ok": True,
                    "skipped": "empty_backlog",
                    "remaining": 0,
                    "processed": 0,
                    "approved": 0,
                    "muted": 0,
                    "kept": 0,
                    "total_at_start": 0,
                }
            state = _fresh_state(total)
            await store.set_meta(_BACKLOG_STATE_KEY, state)

        candidates = await store.list_backlog_candidates(
            after_term_id=str(state.get("last_term_id", "") or ""),
            min_confidence=min_confidence,
            min_usage_count=min_usage_count,
            limit=batch_size,
            gated_by_threshold=bool(getattr(settings, "backlog_threshold_gating_enabled", True)),
        )
        if not candidates:
            state["active"] = False
            await store.set_meta(_BACKLOG_STATE_KEY, state)
            await store.set_meta(_BACKLOG_LAST_DONE_KEY, _now_iso())
            return {
                "ok": True,
                "completed": True,
                "remaining": 0,
                "processed": int(state.get("processed", 0)),
                "approved": int(state.get("approved", 0)),
                "muted": int(state.get("muted", 0)),
                "kept": int(state.get("kept", 0)),
                "total_at_start": int(state.get("total_at_start", 0)),
            }

        batch_index = (int(state.get("processed", 0)) // max(batch_size, 1)) + 1
        run_id = await store.start_extraction_run(
            group_count=0,
            meta={
                "kind": "backlog_ai_review",
                "batch_index": batch_index,
                "batch_size": batch_size,
                "min_confidence": min_confidence,
                "search_enabled": settings.backlog_review_search_enabled,
            },
        )
        state["last_run_id"] = run_id
        await store.set_meta(_BACKLOG_STATE_KEY, state)

        approved_before = int(state.get("approved", 0))
        muted_before = int(state.get("muted", 0))
        kept_before = int(state.get("kept", 0))
        search_count = 0
        search_failed = 0
        run_status = "success"
        run_error = ""
        try:
            for term in candidates:
                try:
                    state, used_search, success = await self._review_one(
                        term=term,
                        state=state,
                        store=store,
                        message_log=message_log,
                        settings=settings,
                        tool_registry=tool_registry,
                        group_filter=group_filter,
                        approve_threshold=approve_threshold,
                    )
                except SlangCollisionError:
                    state["kept"] = int(state.get("kept", 0)) + 1
                    used_search = False
                    success = True
                if used_search:
                    if success:
                        search_count += 1
                    else:
                        search_failed += 1
                state["processed"] = int(state.get("processed", 0)) + 1
                state["last_term_id"] = term.term_id
                state["last_progress_at"] = _now_iso()
                # Per-term flush so a crash mid-batch doesn't roll back progress.
                await store.set_meta(_BACKLOG_STATE_KEY, state)
        except asyncio.CancelledError:
            run_status = "cancelled"
            run_error = "backlog review cancelled (timeout or shutdown)"
            raise
        except Exception as exc:
            run_status = "failed"
            run_error = str(exc)
            return {
                "ok": False,
                "run_id": run_id,
                "error": run_error,
                "processed": int(state.get("processed", 0)),
            }
        finally:
            with contextlib.suppress(Exception):
                approved_in_batch = int(state.get("approved", 0)) - approved_before
                muted_in_batch = int(state.get("muted", 0)) - muted_before
                kept_in_batch = int(state.get("kept", 0)) - kept_before
                await asyncio.shield(
                    store.finish_extraction_run(
                        run_id,
                        status=run_status,
                        group_count=0,
                        scanned_messages=0,
                        extracted_terms=len(candidates),
                        promoted_candidates=approved_in_batch,
                        error=run_error,
                        meta={
                            "kind": "backlog_ai_review",
                            "batch_index": batch_index,
                            "batch_size": batch_size,
                            "backlog_processed": int(state.get("processed", 0)),
                            "backlog_total": int(state.get("total_at_start", 0)),
                            "backlog_approved": int(state.get("approved", 0)),
                            "backlog_muted": int(state.get("muted", 0)),
                            "backlog_kept": int(state.get("kept", 0)),
                            "approved_in_batch": approved_in_batch,
                            "muted_in_batch": muted_in_batch,
                            "kept_in_batch": kept_in_batch,
                            "search_count": search_count,
                            "search_failed": search_failed,
                            **({"cancelled": True} if run_status == "cancelled" else {}),
                        },
                    )
                )

        # Auto-finalize if we drained the backlog.
        if int(state.get("processed", 0)) >= int(state.get("total_at_start", 0)):
            state["active"] = False
            await store.set_meta(_BACKLOG_STATE_KEY, state)
            await store.set_meta(_BACKLOG_LAST_DONE_KEY, _now_iso())

        remaining = await store.count_backlog_candidates(
            min_confidence=min_confidence,
            min_usage_count=min_usage_count,
            gated_by_threshold=gated,
        )
        return {
            "ok": True,
            "run_id": run_id,
            "batch_size": len(candidates),
            "processed": int(state.get("processed", 0)),
            "approved": int(state.get("approved", 0)),
            "muted": int(state.get("muted", 0)),
            "kept": int(state.get("kept", 0)),
            "approved_in_batch": int(state.get("approved", 0)) - approved_before,
            "muted_in_batch": int(state.get("muted", 0)) - muted_before,
            "kept_in_batch": int(state.get("kept", 0)) - kept_before,
            "remaining": remaining,
            "total_at_start": int(state.get("total_at_start", 0)),
            "search_count": search_count,
            "search_failed": search_failed,
            "completed": not state.get("active", False),
        }

    async def _review_one(
        self,
        *,
        term: SlangTerm,
        state: dict[str, Any],
        store: SlangStore,
        message_log: Any,
        settings: SlangSettings,
        tool_registry: Any,
        group_filter: Callable[[str | None], bool] | None,
        approve_threshold: float,
    ) -> tuple[dict[str, Any], bool, bool]:
        """Review a single candidate. Returns (new_state, search_attempted, search_succeeded)."""
        if group_filter is not None and term.group_id and not group_filter(term.group_id):
            state["kept"] = int(state.get("kept", 0)) + 1
            return state, False, False

        context = await self._collect_context(message_log, term, settings)
        extraction = _term_to_extraction(term)
        queries = build_search_queries(extraction)
        search_result = ""
        used_search = False
        succeeded = False
        if settings.backlog_review_search_enabled and queries:
            used_search = True
            search_result = await run_web_search(tool_registry, queries, group_id=str(term.group_id or ""))
            succeeded = bool(search_result)

        assessment: SlangReviewAssessment = await assess_with_llm(
            self._llm_client,
            extraction,
            group_id=str(term.group_id or ""),
            context=context,
            search_result=search_result,
        )

        new_meaning = (assessment.meaning or term.meaning).strip()
        merged_aliases = list(dict.fromkeys([*term.aliases, *assessment.aliases]))
        quality = assess_candidate_quality(assessment.term or term.term, new_meaning, merged_aliases)
        if not quality.accepted and assessment.meaning and term.meaning and new_meaning != term.meaning:
            new_meaning = term.meaning
            quality = assess_candidate_quality(term.term, new_meaning, merged_aliases)
        cleaned_aliases = quality.cleaned_aliases if quality.accepted else list(term.aliases)
        new_confidence = max(float(term.confidence), float(assessment.confidence or 0.0))
        now = _now_iso()
        meta_patch = dict(term.meta or {})
        # P2-2: last_inference_count is only stamped on a DEFINITIVE decision
        # (approved or rejected) so the threshold gate can skip already-decided
        # terms. Kept (undecided) terms intentionally leave it blank so the
        # next round can revisit without waiting for a usage_count stair.
        meta_patch["backlog_review"] = {
            "reviewed_at": now,
            "approved": bool(assessment.approved),
            "confidence": float(assessment.confidence or 0.0),
            "search_used": used_search,
            "search_succeeded": succeeded,
            "is_public_meme": bool(assessment.is_public_meme),
            "reason": assessment.reason,
        }
        meta_patch["ai_reviewed_at"] = now
        meta_patch["ai_review_source"] = "backlog"

        can_approve = settings.backlog_auto_approve_enabled
        if (
            can_approve
            and assessment.approved
            and float(assessment.confidence or 0.0) >= approve_threshold
        ):
            meta_patch["ai_review_decision"] = "approved"
            meta_patch["last_inference_count"] = int(term.usage_count or 0)
            await store.update_term(
                term.term_id,
                meaning=new_meaning,
                aliases=cleaned_aliases,
                confidence=new_confidence,
                repeat_policy=assessment.repeat_policy,
                meta=meta_patch,
                revision_action="backlog_review:approve",
                revision_actor="ai_review",
                revision_reason="backlog_approved",
            )
            await store.set_status(
                term.term_id,
                "approved",
                actor="ai_review",
                reason="backlog_approved",
            )
            state["approved"] = int(state.get("approved", 0)) + 1
        elif (not assessment.approved) and float(assessment.confidence or 0.0) >= _MUTE_MIN_CONFIDENCE:
            meta_patch["ai_review_decision"] = "rejected"
            meta_patch["last_inference_count"] = int(term.usage_count or 0)
            await store.update_term(
                term.term_id,
                meta=meta_patch,
                revision_action="backlog_review:mute",
                revision_actor="ai_review",
                revision_reason="backlog_rejected",
            )
            await store.set_status(
                term.term_id,
                "muted",
                actor="ai_review",
                reason="backlog_rejected",
            )
            state["muted"] = int(state.get("muted", 0)) + 1
        else:
            # Undecided — keep as candidate for a future round but record the
            # review meta so we can see what the LLM said.
            kept_streak = int(meta_patch.get("backlog_kept_streak", 0)) + 1
            meta_patch["backlog_kept_streak"] = kept_streak
            kept_history = list(meta_patch.get("backlog_kept_history") or [])
            kept_history.append({
                "run_id": state.get("run_id", ""),
                "confidence": float(assessment.confidence or 0.0),
                "reason": assessment.reason,
                "at": now,
            })
            meta_patch["backlog_kept_history"] = kept_history[-3:]

            streak_limit = int(getattr(settings, "backlog_kept_streak_limit", 2))
            if kept_streak >= streak_limit:
                meta_patch["ai_review_decision"] = "rejected"
                meta_patch["last_inference_count"] = int(term.usage_count or 0)
                await store.update_term(
                    term.term_id,
                    meta=meta_patch,
                    revision_action="backlog_review:streak_mute",
                    revision_actor="ai_review",
                    revision_reason="backlog_kept_streak_exceeded",
                    record_revision=True,
                )
                await store.set_status(
                    term.term_id,
                    "muted",
                    actor="ai_review",
                    reason="backlog_kept_streak_exceeded",
                )
                state["muted"] = int(state.get("muted", 0)) + 1
            else:
                meta_patch["ai_review_decision"] = "kept"
                await store.update_term(
                    term.term_id,
                    meta=meta_patch,
                    revision_action="backlog_review:keep",
                    revision_actor="ai_review",
                    revision_reason="backlog_undecided",
                    record_revision=False,
                )
                state["kept"] = int(state.get("kept", 0)) + 1

        return state, used_search, succeeded

    @staticmethod
    async def _collect_context(message_log: Any, term: SlangTerm, settings: SlangSettings) -> str:
        """Collect concrete UGC evidence for a candidate term.

        Strategy:
        1. Try term-targeted hits via MessageLog.query_term_hits (LIKE on
           term + aliases). Existing slang from days ago won't show up in
           "last 8 messages", so we have to look up by content.
        2. If we get fewer than 2 term-hits, fall back to recent group
           chatter so the LLM at least has the speaking style.

        Returns a string with section labels so the LLM knows which lines
        actually contain the term vs. background context.
        """
        if message_log is None or not term.group_id:
            return ""
        evidence_count = int(getattr(settings, "backlog_local_evidence_count", 5) or 0)
        group_id = str(term.group_id)
        term_hits: list[dict[str, Any]] = []
        if evidence_count > 0 and hasattr(message_log, "query_term_hits"):
            search_terms = [term.term, *list(term.aliases or [])][:6]
            try:
                term_hits = await message_log.query_term_hits(
                    group_id, search_terms, limit=evidence_count,
                )
            except Exception:
                term_hits = []
        sections: list[str] = []
        if term_hits:
            hit_lines = [
                str(row.get("content_text") or "").strip()
                for row in term_hits
                if str(row.get("content_text") or "").strip()
            ]
            if hit_lines:
                sections.append(
                    f"[群内出现 '{term.term}' 的实际消息（共 {len(hit_lines)} 条）]\n"
                    + "\n".join(hit_lines)
                )
        # Always pad with a small recent-context window so the LLM has the
        # speaking-style baseline; cap at 8 to avoid prompt bloat.
        if len(term_hits) < 2:
            try:
                rows = await message_log.query_recent(
                    group_id,
                    limit=int(settings.daily_ai_recent_message_limit),
                )
            except Exception:
                rows = []
            user_rows = [
                row for row in rows
                if row.get("role") == "user" and row.get("content_text")
            ]
            if user_rows:
                tail_lines = [
                    str(row.get("content_text") or "").strip()
                    for row in user_rows[-8:]
                    if str(row.get("content_text") or "").strip()
                ]
                if tail_lines:
                    sections.append(
                        "[群内最近聊天片段（参考说话风格，不一定包含该词）]\n"
                        + "\n".join(tail_lines)
                    )
        return "\n\n".join(sections)
