"""Daily AI reviewer for search-assisted slang and meme approval."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.conversation_archive import add_evidence_message_ref, finish_scan_batch, read_scan_batch
from services.slang.extractor import SlangExtractor
from services.slang.quality import (
    assess_candidate_quality,
    estimate_slang_occurrences,
    select_slang_source_row,
    speaker_to_user_id,
)
from services.slang.semantic_reviewer import SlangSemanticAssessment, SlangSemanticReviewer
from services.slang.store import normalize_term
from services.slang.types import (
    VALID_REPEAT_POLICIES,
    RepeatPolicy,
    SlangExtraction,
    SlangPendingCandidate,
    SlangSettings,
    SlangTerm,
)
from services.tools.context import ToolContext

if TYPE_CHECKING:
    from services.slang.store import SlangStore

_L = logger.bind(channel="system")

_REVIEW_SYSTEM_PROMPT = """你是 Omubot 的黑话/网络梗复核器。

你会收到一个群聊候选词、群内证据和可选搜索结果。请判断它是否足够可靠，可以被标记为“AI 通过”。

只输出 JSON，不要输出 Markdown。格式：
{
  "decision": "approve|reject|observe",
  "approved": true,
  "term": "标准词条",
  "meaning": "简洁解释，优先保留群内语境；如果是公网梗，说明其常见含义",
  "aliases": ["可选别名"],
  "decision_confidence": 0.0,
  "confidence": 0.0,
  "reason": "判断依据",
  "repeat_policy": "understand_only",
  "is_public_meme": true
}

约束：
- 优先判断它是否是群内稳定用法；公网搜索只是辅助证据，不是唯一准入条件。
- 群内证据清楚给出含义，或同一用法多次出现时，即使搜索为空，也可以批准为本群黑话。
- decision=approve：证据足够支持它是群内黑话、社群术语、稳定领域缩写或常用梗。
- decision=reject：你能确定它只是普通句子、命令、刷屏片段、无意义文本、单次临时表达，或不应进入黑话库。
- decision=observe：它可能发展成黑话，但当前证据不足、含义不清或上下文冲突，需要等更多出现。
- approved 必须等于 decision 是否为 approve。reject/observe 时 approved=false。
- decision_confidence 表示你对“approve/reject/observe 这个决策”的把握，不是“它成为黑话的概率”。
- 搜索结果无关、同名撞词或无法证明候选含义时，不要把它当成支持证据。
- repeat_policy 只能是 understand_only / allow_rephrase / allow_use。
"""

_BATCH_REVIEW_SYSTEM_PROMPT = """你是 Omubot 的黑话候选批量复核器。

你会收到一组已抽取的群聊黑话候选。请逐条判断它是否足够可靠，可以被标记为“AI 通过”。

只输出 JSON，不要输出 Markdown。格式：
{
  "results": [
    {
      "index": 0,
      "decision": "approve|reject|observe",
      "approved": true,
      "term": "标准词条",
      "meaning": "简洁解释，优先保留群内语境",
      "aliases": ["可选别名"],
      "decision_confidence": 0.0,
      "confidence": 0.0,
      "reason": "判断依据",
      "repeat_policy": "understand_only",
      "is_public_meme": false
    }
  ]
}

约束：
- 每个输入 index 都必须返回一个结果。
- 优先判断群内证据是否支撑稳定用法；不要只因常见作品名、人名、问句、普通短句就批准。
- decision=approve：证据足够支持它是群内黑话、社群术语、稳定领域缩写或常用梗。
- decision=reject：你能确定它只是普通句子、命令、刷屏片段、无意义文本、单次临时表达，或不应进入黑话库。
- decision=observe：它可能发展成黑话，但当前证据不足、含义不清或上下文冲突，需要等更多出现。
- approved 必须等于 decision 是否为 approve。reject/observe 时 approved=false。
- decision_confidence 表示你对“approve/reject/observe 这个决策”的把握，不是“它成为黑话的概率”。
- repeat_policy 只能是 understand_only / allow_rephrase / allow_use。
"""

_MIN_CANDIDATE_REJECT_CONFIDENCE = 0.55
_PENDING_SEMANTIC_REVIEW_CONCURRENCY = 3


@dataclass
class SlangReviewAssessment:
    approved: bool = False
    decision: str = "observe"
    decision_confidence: float = 0.0
    term: str = ""
    meaning: str = ""
    aliases: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    repeat_policy: RepeatPolicy = "understand_only"
    is_public_meme: bool = False
    reviewed: bool = True


def _extract_json_object(text: str) -> dict[str, Any]:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {}
    try:
        loaded = json.loads(match.group(0))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _split_aliases(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,，\n]", value) if part.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _candidate_decision_from_raw(raw: dict[str, Any], *, approved: bool, confidence: float) -> tuple[str, float]:
    decision = str(raw.get("decision") or "").strip().lower()
    if decision not in {"approve", "reject", "observe"}:
        if approved:
            decision = "approve"
        elif confidence >= _MIN_CANDIDATE_REJECT_CONFIDENCE:
            decision = "reject"
        else:
            decision = "observe"
    try:
        decision_confidence = float(raw.get("decision_confidence"))
    except Exception:
        decision_confidence = confidence
    if "decision" in raw and "decision_confidence" not in raw and decision in {"approve", "reject"}:
        decision_confidence = max(decision_confidence, _MIN_CANDIDATE_REJECT_CONFIDENCE)
    return decision, max(0.0, min(1.0, decision_confidence))


class SlangDailyReviewer:
    """Run the daily search-assisted AI review without changing plugin contracts."""

    def __init__(self, llm_client: Any = None) -> None:
        self._llm_client = llm_client
        self._extractor = SlangExtractor(llm_client)
        self._semantic_reviewer = SlangSemanticReviewer(llm_client)

    def _resolve_review_call(self) -> Any:
        if self._llm_client is None:
            return None
        call = getattr(self._llm_client, "_call_slang_review", None)
        if call is not None:
            return call
        call = getattr(self._llm_client, "_call_slang", None)
        if call is not None:
            return call
        return getattr(self._llm_client, "_call", None)

    async def _extract_recent_candidates(
        self,
        rows: list[dict[str, Any]],
        *,
        settings: SlangSettings,
    ) -> tuple[list[SlangExtraction], int, int]:
        window_size = max(1, min(len(rows), int(settings.extraction_batch_limit or 80)))
        if window_size <= 0:
            return [], 0, 0
        window = rows[-window_size:]
        seen: set[str] = set()
        result: list[SlangExtraction] = []
        for item in await self._extractor.extract(window, settings=settings):
            key = normalize_term(item.term)
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(item)
            if len(result) >= max(1, int(settings.daily_ai_max_terms_per_group or 1)):
                break
        return result, 1, window_size

    @staticmethod
    def _pending_to_extraction(pending: SlangPendingCandidate) -> SlangExtraction:
        return SlangExtraction(
            term=pending.term,
            meaning=pending.meaning,
            aliases=list(pending.aliases),
            evidence=pending.evidence,
            confidence=pending.confidence,
            reason=pending.reason,
            repeat_policy=pending.repeat_policy,
        )

    @staticmethod
    def _has_group_evidence(
        *,
        settings: SlangSettings,
        search_result: str,
        observed_count: int,
    ) -> bool:
        if search_result:
            return True
        return observed_count >= max(1, int(settings.candidate_min_count or 1))

    async def _review_pending_candidates(
        self,
        *,
        store: SlangStore,
        settings: SlangSettings,
        tool_registry: Any,
        gid: str,
        user_rows: list[dict[str, Any]],
        review_all_pending: bool = False,
    ) -> dict[str, int]:
        stats = {
            "pending_reviewed": 0,
            "pending_approved": 0,
            "pending_rejected": 0,
            "pending_kept": 0,
            "pending_search_failed": 0,
            "semantic_reviewed": 0,
            "semantic_approved": 0,
            "semantic_rejected": 0,
            "semantic_kept": 0,
            "semantic_no_info": 0,
            "semantic_failed": 0,
        }
        pending_rows: list[SlangPendingCandidate] = []
        if review_all_pending:
            page_size = max(1, min(int(settings.bulk_page_size or 50), 200))
            offset = 0
            while True:
                batch, _total = await store.list_pending(
                    group_id=str(gid),
                    limit=page_size,
                    offset=offset,
                )
                if not batch:
                    break
                pending_rows.extend(batch)
                if len(batch) < page_size:
                    break
                offset += page_size
        else:
            pending_rows, _total = await store.list_pending(
                group_id=str(gid),
                limit=max(1, int(settings.daily_ai_max_terms_per_group or 1)),
            )
        if not pending_rows:
            return stats

        context = "\n".join(str(row.get("content_text") or "") for row in user_rows[-8:])
        review_items: list[tuple[SlangPendingCandidate, SlangExtraction, list[str]]] = []
        for pending in pending_rows:
            item = self._pending_to_extraction(pending)
            queries = self._build_search_queries(item)
            if not review_all_pending and self._semantic_reviewer.threshold_for_pending(pending) is None:
                stats["pending_kept"] += 1
                continue
            review_items.append((pending, item, queries))

        if not review_items:
            return stats

        semaphore = asyncio.Semaphore(_PENDING_SEMANTIC_REVIEW_CONCURRENCY)

        async def _review_one(
            pending: SlangPendingCandidate,
            item: SlangExtraction,
            queries: list[str],
        ) -> tuple[SlangPendingCandidate, SlangExtraction, list[str], SlangSemanticAssessment]:
            async with semaphore:
                assessment = await self._semantic_reviewer.review_pending(
                    pending,
                    group_id=str(gid),
                    user_rows=user_rows,
                    force=review_all_pending,
                )
            return pending, item, queries, assessment

        review_results = await asyncio.gather(
            *(
                _review_one(pending, item, queries)
                for pending, item, queries in review_items
            ),
        )
        for pending, item, queries, assessment in review_results:
            if not assessment.reviewed:
                stats["pending_kept"] += 1
                continue

            search_result = ""

            stats["pending_reviewed"] += 1
            stats["semantic_reviewed"] += 1
            semantic_meta = self._semantic_pending_meta(
                pending,
                assessment,
                queries=queries,
                search_result=search_result,
                search_enabled=False,
            )
            await store.update_pending_candidate_meta(pending.pending_id, meta=semantic_meta)

            if assessment.error:
                stats["pending_kept"] += 1
                stats["semantic_failed"] += 1
                continue
            if assessment.no_info or not assessment.complete or assessment.is_similar is None:
                stats["pending_kept"] += 1
                stats["semantic_no_info"] += 1
                continue

            term_value = item.term
            aliases = list(item.aliases)
            meaning = assessment.context_meaning or item.meaning
            quality = assess_candidate_quality(term_value, meaning, aliases)
            semantic_confidence = max(0.0, min(1.0, assessment.confidence or 0.0))
            confidence = max(max(0.0, min(1.0, item.confidence or 0.0)), semantic_confidence)
            observed_count = max(1, int(pending.count or 1))

            if assessment.is_similar:
                term_id = await store.reject_pending_candidate(
                    pending.pending_id,
                    group_id=str(gid),
                    reason=assessment.reason or pending.reason or "semantic_review_similar_to_literal",
                    meta={
                        **semantic_meta,
                        "semantic_rejected": True,
                        "review_reason": assessment.reason or pending.reason,
                    },
                )
                if term_id:
                    stats["pending_rejected"] += 1
                    stats["semantic_rejected"] += 1
                else:
                    stats["pending_kept"] += 1
                    stats["semantic_failed"] += 1
                continue

            should_auto_approve = (
                settings.daily_ai_auto_approve_enabled
                and semantic_confidence >= settings.daily_ai_auto_approve_min_confidence
                and quality.accepted
            )
            if should_auto_approve:
                term_id = await store.upsert_ai_approved_term(
                    term=term_value,
                    meaning=meaning,
                    aliases=quality.cleaned_aliases if quality.accepted else aliases,
                    group_id=str(gid),
                    user_id=self._speaker_to_user_id(None),
                    raw_text=str(pending.evidence or item.evidence),
                    context=context or item.evidence,
                    confidence=confidence,
                    reason=assessment.reason or pending.reason or "semantic_review",
                    repeat_policy=item.repeat_policy,
                    meta={
                        **semantic_meta,
                        "semantic_auto_approved": True,
                        "pending_id": pending.pending_id,
                    },
                    observed_count=observed_count,
                    settings=settings,
                )
                if term_id:
                    await store.resolve_pending_candidate(pending.pending_id, term_id)
                    stats["pending_approved"] += 1
                    stats["semantic_approved"] += 1
                else:
                    stats["pending_kept"] += 1
                    stats["semantic_failed"] += 1
                continue

            term_id = await store.promote_pending_candidate(
                pending.pending_id,
                meta={
                    **semantic_meta,
                    "semantic_candidate_confirmed": True,
                },
                meaning=meaning,
                aliases=quality.cleaned_aliases if quality.accepted else aliases,
                confidence=confidence,
                revision_reason=assessment.reason or pending.reason or "semantic review confirmed candidate",
            )
            if term_id:
                stats["semantic_kept"] += 1
            else:
                stats["pending_kept"] += 1
                stats["semantic_failed"] += 1
        return stats

    async def _review_existing_candidates(
        self,
        *,
        store: SlangStore,
        settings: SlangSettings,
        tool_registry: Any,
        gid: str,
        rerun_reviewed_candidates: bool = False,
        candidate_review_filter: str = "",
    ) -> dict[str, int]:
        stats = {
            "candidate_reviewed": 0,
            "candidate_approved": 0,
            "candidate_rejected": 0,
            "candidate_kept": 0,
            "candidate_failed": 0,
            "candidate_search_failed": 0,
            "candidate_skipped_reviewed": 0,
        }
        page_size = max(1, min(int(settings.bulk_page_size or 50), 200))
        candidate_terms: list[SlangTerm] = []
        offset = 0
        list_review_filter = candidate_review_filter.strip()
        if not list_review_filter:
            list_review_filter = "" if rerun_reviewed_candidates else "candidate_ai_unreviewed"
        while True:
            batch, _total = await store.list_terms(
                group_id=str(gid),
                status="candidate",
                review_filter=list_review_filter,
                limit=page_size,
                offset=offset,
            )
            if not batch:
                break
            candidate_terms.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

        if not candidate_terms:
            return stats

        if not rerun_reviewed_candidates and not candidate_review_filter:
            _, total_candidates = await store.list_terms(group_id=str(gid), status="candidate", limit=1, offset=0)
            stats["candidate_skipped_reviewed"] = max(0, total_candidates - len(candidate_terms))

        batch_size = max(1, min(page_size, 8))
        for start in range(0, len(candidate_terms), batch_size):
            chunk_terms = candidate_terms[start : start + batch_size]
            chunk_items: list[SlangExtraction] = []
            chunk_contexts: list[str] = []
            chunk_evidence: list[str] = []
            chunk_queries: list[list[str]] = []
            for term in chunk_terms:
                evidence = str(term.meta.get("evidence") or term.notes or term.meaning or term.term).strip()
                context = evidence or term.meaning or term.term
                item = SlangExtraction(
                    term=term.term,
                    meaning=term.meaning,
                    aliases=list(term.aliases),
                    evidence=evidence,
                    confidence=max(
                        0.0,
                        min(1.0, float(term.meta.get("llm_confidence", term.confidence) or term.confidence or 0.0)),
                    ),
                    reason=str(term.meta.get("evidence") or term.notes or term.source or "candidate_review"),
                    repeat_policy=term.repeat_policy,
                )
                chunk_items.append(item)
                chunk_contexts.append(context)
                chunk_evidence.append(evidence)
                chunk_queries.append(self._build_search_queries(item))

            assessments = await self._assess_candidate_batch(
                chunk_items,
                group_id=str(gid),
                contexts=chunk_contexts,
            )
            for term, _item, evidence, queries, context, assessment in zip(
                chunk_terms,
                chunk_items,
                chunk_evidence,
                chunk_queries,
                chunk_contexts,
                assessments,
                strict=False,
            ):
                stats["candidate_reviewed"] += 1
                reviewed_at = store_time_label()
                reviewed_meta = {
                    "daily_ai_review": True,
                    "candidate_review": True,
                    "candidate_reviewed": True,
                    "candidate_reviewed_at": reviewed_at,
                    "candidate_term_id": term.term_id,
                    "candidate_group_id": str(gid),
                    "candidate_search_failed": False,
                    "candidate_search_queries": queries,
                    "candidate_search_evidence": evidence,
                    "candidate_review_complete": bool(assessment.reviewed),
                    "candidate_review_decision": assessment.decision,
                    "candidate_review_decision_confidence": round(
                        max(0.0, min(1.0, float(assessment.decision_confidence or 0.0))),
                        3,
                    ),
                    "candidate_review_confidence": round(max(0.0, min(1.0, float(assessment.confidence or 0.0))), 3),
                    "candidate_review_approved": bool(assessment.approved),
                    "candidate_review_is_public_meme": bool(assessment.is_public_meme),
                }
                if assessment.reason:
                    reviewed_meta["candidate_review_reason"] = assessment.reason

                if not assessment.reviewed:
                    stats["candidate_failed"] += 1
                    failed_meta = {
                        **reviewed_meta,
                        "candidate_reviewed": False,
                        "candidate_review_complete": False,
                        "candidate_review_failed": True,
                        "candidate_review_failed_at": reviewed_at,
                        "candidate_review_state": "failed",
                        "review_decision": "retry",
                    }
                    await store.update_term(
                        term.term_id,
                        meta={**term.meta, **failed_meta},
                        last_inferred_at=reviewed_at,
                        revision_action="candidate_ai_review",
                        revision_actor="ai",
                        revision_reason=assessment.reason or "candidate review failed",
                        revision_meta=failed_meta,
                    )
                    continue

                meaning = assessment.meaning or term.meaning
                aliases = list(term.aliases)
                semantic_confidence = max(0.0, min(1.0, float(assessment.confidence or 0.0)))
                confidence = max(
                    semantic_confidence,
                    float(term.meta.get("llm_confidence", term.confidence) or term.confidence or 0.0),
                )
                quality = assess_candidate_quality(term.term, meaning, aliases)
                observed_count = max(1, int(term.usage_count or 1))
                should_auto_approve = (
                    settings.daily_ai_auto_approve_enabled
                    and assessment.approved
                    and assessment.decision == "approve"
                    and semantic_confidence >= settings.daily_ai_auto_approve_min_confidence
                    and quality.accepted
                    and self._has_group_evidence(
                        settings=settings,
                        search_result="",
                        observed_count=observed_count,
                    )
                )
                if should_auto_approve:
                    term_id = await store.upsert_ai_approved_term(
                        term=term.term,
                        meaning=meaning,
                        aliases=quality.cleaned_aliases if quality.accepted else aliases,
                        group_id=str(gid),
                        user_id=str(term.unique_users[0]) if term.unique_users else "",
                        raw_text=evidence,
                        context=context,
                        confidence=confidence,
                        reason=assessment.reason or term.notes or "candidate_ai_review",
                        repeat_policy=term.repeat_policy,
                        meta={**term.meta, **reviewed_meta, "candidate_ai_auto_approved": True},
                        observed_count=observed_count,
                        settings=settings,
                    )
                    if term_id:
                        stats["candidate_approved"] += 1
                        continue
                    continue

                if (
                    not assessment.approved
                    and assessment.decision == "reject"
                    and assessment.decision_confidence >= _MIN_CANDIDATE_REJECT_CONFIDENCE
                ):
                    rejected_meta = {
                        **reviewed_meta,
                        "ai_rejected": True,
                        "candidate_review_state": "rejected",
                        "review_decision": "denied",
                        "ai_reviewed_at": reviewed_at,
                        "ai_reason": assessment.reason or term.notes or "candidate review rejected",
                    }
                    await store.update_term(
                        term.term_id,
                        meaning=meaning,
                        aliases=quality.cleaned_aliases if quality.accepted else aliases,
                        confidence=confidence,
                        status="muted",
                        meta={**term.meta, **rejected_meta},
                        last_inferred_at=reviewed_at,
                        revision_action="candidate_ai_reject",
                        revision_actor="ai",
                        revision_reason=assessment.reason or term.notes or "candidate review rejected",
                        revision_meta=rejected_meta,
                    )
                    stats["candidate_rejected"] += 1
                    continue

                if assessment.approved and assessment.decision == "approve":
                    suggested_meta = {
                        **reviewed_meta,
                        "candidate_review_state": "suggested",
                        "review_decision": "suggested_approve",
                    }
                    await store.update_term(
                        term.term_id,
                        meaning=meaning,
                        aliases=quality.cleaned_aliases if quality.accepted else aliases,
                        confidence=confidence,
                        meta={**term.meta, **suggested_meta},
                        last_inferred_at=reviewed_at,
                        revision_action="candidate_ai_review",
                        revision_actor="ai",
                        revision_reason=assessment.reason or term.notes or "candidate review suggested approve",
                        revision_meta=suggested_meta,
                    )
                    stats["candidate_kept"] += 1
                    continue

                observe_meta = {
                    **reviewed_meta,
                    "candidate_review_state": "observing",
                    "review_decision": "observe_more",
                }
                await store.update_term(
                    term.term_id,
                    meaning=meaning,
                    aliases=quality.cleaned_aliases if quality.accepted else aliases,
                    confidence=confidence,
                    meta={**term.meta, **observe_meta},
                    last_inferred_at=reviewed_at,
                    revision_action="candidate_ai_review",
                    revision_actor="ai",
                    revision_reason=assessment.reason or term.notes or "candidate review observe more",
                    revision_meta=observe_meta,
                )
                stats["candidate_kept"] += 1
        return stats

    @staticmethod
    def _semantic_pending_meta(
        pending: SlangPendingCandidate,
        assessment: SlangSemanticAssessment,
        *,
        queries: list[str],
        search_result: str,
        search_enabled: bool,
    ) -> dict[str, Any]:
        return {
            **assessment.to_meta(),
            "daily_ai_review": True,
            "pending_review": True,
            "pending_id": pending.pending_id,
            "search_queries": queries,
            "search_evidence": search_result,
            "search_failed": bool(search_enabled and not search_result),
        }

    async def run(
        self,
        *,
        store: SlangStore,
        message_log: Any,
        settings: SlangSettings,
        tool_registry: Any = None,
        group_id: str | None = None,
        group_filter: Callable[[str | None], bool] | None = None,
        review_candidates: bool = False,
        review_all_pending: bool = False,
        rerun_reviewed_candidates: bool = False,
        candidate_review_filter: str = "",
    ) -> dict[str, Any]:
        if message_log is None:
            return {"ok": False, "error": "MessageLog not available"}
        if self._llm_client is None:
            return {"ok": False, "error": "LLMClient not available"}

        groups = [str(group_id)] if group_id else await message_log.list_group_ids()
        groups = [str(gid) for gid in groups if gid and settings.allows_group(str(gid))]
        if group_filter is not None:
            groups = [str(gid) for gid in groups if group_filter(str(gid))]
        run_id = await store.start_extraction_run(
            group_count=len(groups),
            meta={
                "kind": "daily_ai_review",
                "search_enabled": settings.daily_ai_review_search_enabled,
                "auto_approve_enabled": settings.daily_ai_auto_approve_enabled,
                "review_candidates": bool(review_candidates),
                "review_all_pending": bool(review_all_pending),
                "rerun_reviewed_candidates": bool(rerun_reviewed_candidates),
                "candidate_review_filter": candidate_review_filter,
            },
        )
        run_started = time.monotonic()
        _L.info(
            (
                "slang review start | task=slang_review run={} groups={} "
                "search_enabled={} auto_approve={} review_candidates={} review_all_pending={} "
                "rerun_reviewed_candidates={} candidate_review_filter={}"
            ),
            run_id,
            len(groups),
            settings.daily_ai_review_search_enabled,
            settings.daily_ai_auto_approve_enabled,
            bool(review_candidates),
            bool(review_all_pending),
            bool(rerun_reviewed_candidates),
            candidate_review_filter or "",
        )
        scanned = 0
        extracted = 0
        candidates = 0
        ai_approved = 0
        pending_reviewed = 0
        pending_approved = 0
        pending_rejected = 0
        pending_kept = 0
        semantic_reviewed = 0
        semantic_approved = 0
        semantic_rejected = 0
        semantic_kept = 0
        semantic_no_info = 0
        semantic_failed = 0
        candidate_reviewed = 0
        candidate_approved = 0
        candidate_rejected = 0
        candidate_kept = 0
        candidate_failed = 0
        candidate_search_failed = 0
        candidate_skipped_reviewed = 0
        drift_replay_reviewed = 0
        drift_replay_closed_same_meaning = 0
        drift_replay_aliased = 0
        drift_replay_kept_real_drift = 0
        drift_replay_kept_unclear = 0
        drift_replay_failed = 0
        drift_replay_error = ""
        search_count = 0
        search_failed = 0
        pending_search_failed = 0
        extract_batches_total = 0
        extract_window_total = 0
        active_scan_batch: dict[str, Any] | None = None
        active_group_scanned = 0
        active_group_extracted = 0
        active_group_saved = 0
        try:
            for gid in groups:
                batch = await read_scan_batch(
                    message_log,
                    scanner_name="slang_daily_review",
                    group_id=str(gid),
                    limit=settings.daily_ai_recent_message_limit,
                    scanner_version="v1",
                    meta={"slang_run_id": run_id, "kind": "daily_ai_review"},
                )
                active_scan_batch = batch
                active_group_scanned = 0
                active_group_extracted = 0
                active_group_saved = 0
                rows = list(batch.get("rows") or [])
                user_rows = [row for row in rows if row.get("role") == "user" and row.get("content_text")]
                review_context_rows = user_rows
                if not review_context_rows and batch.get("source") == "archive":
                    recent_rows = await message_log.query_recent(
                        str(gid),
                        limit=settings.daily_ai_recent_message_limit,
                    )
                    review_context_rows = [
                        row for row in recent_rows if row.get("role") == "user" and row.get("content_text")
                    ]
                if user_rows:
                    active_group_scanned = len(user_rows)
                    scanned += active_group_scanned
                    extractions, extract_batches, extract_window = await self._extract_recent_candidates(
                        user_rows,
                        settings=settings,
                    )
                    active_group_extracted = len(extractions)
                    extracted += active_group_extracted
                    extract_batches_total += extract_batches
                    extract_window_total += extract_window
                    written_for_group = 0
                    for item in extractions:
                        if written_for_group >= settings.daily_ai_max_terms_per_group:
                            break
                        source = self._pick_source_row(item.evidence, user_rows)
                        context = "\n".join(str(row.get("content_text") or "") for row in user_rows[-8:])
                        queries = self._build_search_queries(item)
                        search_result = ""
                        if settings.daily_ai_review_search_enabled:
                            search_result = await self._search(tool_registry, queries, group_id=str(gid))
                            if search_result:
                                search_count += 1
                            else:
                                search_failed += 1
                        assessment = await self._assess(
                            item,
                            group_id=str(gid),
                            context=context,
                            search_result=search_result,
                        )
                        term_value = assessment.term or item.term
                        aliases = [*item.aliases, *assessment.aliases]
                        meaning = assessment.meaning or item.meaning
                        quality = assess_candidate_quality(term_value, meaning, aliases)
                        if (
                            not quality.accepted
                            and assessment.meaning
                            and item.meaning
                            and assessment.meaning != item.meaning
                        ):
                            meaning = item.meaning
                            quality = assess_candidate_quality(term_value, meaning, aliases)
                        if not quality.accepted:
                            continue
                        aliases = quality.cleaned_aliases
                        confidence = max(0.0, min(1.0, assessment.confidence or item.confidence))
                        policy = assessment.repeat_policy or item.repeat_policy
                        reason = assessment.reason or item.reason
                        meta = {
                            "daily_ai_review": True,
                            "group_evidence": item.evidence or str(source.get("content_text") or ""),
                            "search_queries": queries,
                            "search_evidence": search_result,
                            "search_failed": settings.daily_ai_review_search_enabled and not bool(search_result),
                            "is_public_meme": assessment.is_public_meme,
                        }
                        raw_text = str(source.get("content_text") or item.evidence)
                        observed_count = self._estimate_occurrences(term_value, aliases, user_rows)
                        should_auto_approve = (
                            settings.daily_ai_auto_approve_enabled
                            and assessment.approved
                            and confidence >= settings.daily_ai_auto_approve_min_confidence
                            and self._has_group_evidence(
                                settings=settings,
                                search_result=search_result,
                                observed_count=observed_count,
                            )
                        )
                        if should_auto_approve:
                            term_id = await store.upsert_ai_approved_term(
                                term=term_value,
                                meaning=meaning,
                                aliases=aliases,
                                group_id=str(gid),
                                user_id=self._speaker_to_user_id(source.get("speaker")),
                                message_id=source.get("message_id"),
                                raw_text=raw_text,
                                context=context,
                                confidence=confidence,
                                reason=reason or "daily_ai_review",
                                repeat_policy=policy,
                                meta=meta,
                                observed_count=observed_count,
                                settings=settings,
                            )
                            if term_id:
                                await add_evidence_message_ref(
                                    message_log,
                                    group_id=str(gid),
                                    source_row=source,
                                    ref_owner="slang",
                                    external_table="slang_terms",
                                    external_id=term_id,
                                    snapshot_text=raw_text,
                                    meta={"source": "slang_daily_review", "slang_run_id": run_id},
                                )
                                ai_approved += 1
                                written_for_group += 1
                                active_group_saved += 1
                            continue

                        term_id = await store.upsert_candidate(
                            term=term_value,
                            meaning=meaning,
                            aliases=aliases,
                            group_id=str(gid),
                            user_id=self._speaker_to_user_id(source.get("speaker")),
                            message_id=source.get("message_id"),
                            raw_text=raw_text,
                            context=context,
                            confidence=confidence,
                            reason=reason or "daily_ai_review",
                            repeat_policy=policy,
                            source="daily_ai_review",
                            meta=meta,
                            min_count=settings.candidate_min_count,
                            observed_count=observed_count,
                            settings=settings,
                        )
                        if term_id:
                            await add_evidence_message_ref(
                                message_log,
                                group_id=str(gid),
                                source_row=source,
                                ref_owner="slang",
                                external_table="slang_terms",
                                external_id=term_id,
                                snapshot_text=raw_text,
                                meta={"source": "slang_daily_review", "slang_run_id": run_id},
                            )
                            candidates += 1
                            written_for_group += 1
                            active_group_saved += 1

                pending_stats = await self._review_pending_candidates(
                    store=store,
                    settings=settings,
                    tool_registry=tool_registry,
                    gid=str(gid),
                    user_rows=review_context_rows,
                    review_all_pending=review_all_pending,
                )
                pending_reviewed += pending_stats["pending_reviewed"]
                pending_approved += pending_stats["pending_approved"]
                pending_rejected += pending_stats["pending_rejected"]
                pending_kept += pending_stats["pending_kept"]
                pending_search_failed += pending_stats["pending_search_failed"]
                semantic_reviewed += pending_stats["semantic_reviewed"]
                semantic_approved += pending_stats["semantic_approved"]
                semantic_rejected += pending_stats["semantic_rejected"]
                semantic_kept += pending_stats["semantic_kept"]
                semantic_no_info += pending_stats["semantic_no_info"]
                semantic_failed += pending_stats["semantic_failed"]

                if review_candidates:
                    candidate_stats = await self._review_existing_candidates(
                        store=store,
                        settings=settings,
                        tool_registry=tool_registry,
                        gid=str(gid),
                        rerun_reviewed_candidates=rerun_reviewed_candidates,
                        candidate_review_filter=candidate_review_filter,
                    )
                    candidate_reviewed += candidate_stats["candidate_reviewed"]
                    candidate_approved += candidate_stats["candidate_approved"]
                    candidate_rejected += candidate_stats["candidate_rejected"]
                    candidate_kept += candidate_stats["candidate_kept"]
                    candidate_failed += candidate_stats["candidate_failed"]
                    candidate_search_failed += candidate_stats["candidate_search_failed"]
                    candidate_skipped_reviewed += candidate_stats["candidate_skipped_reviewed"]
                    ai_approved += candidate_stats["candidate_approved"]

                await finish_scan_batch(
                    message_log,
                    batch,
                    status="success",
                    scanned_count=active_group_scanned,
                    extracted_count=active_group_extracted,
                    saved_count=active_group_saved,
                    meta={"slang_run_id": run_id, "kind": "daily_ai_review"},
                )
                active_scan_batch = None

            try:
                drift_replay = await store.replay_open_drift_reviews(limit=100, apply=True)
            except Exception as exc:
                drift_replay = {"ok": False, "error": str(exc), "failed": 1}
            drift_replay_reviewed = int(drift_replay.get("reviewed") or 0)
            drift_replay_closed_same_meaning = int(drift_replay.get("closed_same_meaning") or 0)
            drift_replay_aliased = int(drift_replay.get("aliased") or 0)
            drift_replay_kept_real_drift = int(drift_replay.get("kept_real_drift") or 0)
            drift_replay_kept_unclear = int(drift_replay.get("kept_unclear") or 0)
            drift_replay_failed = int(drift_replay.get("failed") or 0)
            drift_replay_error = str(drift_replay.get("error") or "")
            if drift_replay_reviewed or drift_replay_error:
                _L.info(
                    (
                        "slang drift replay finished | task=slang_drift run={} reviewed={} "
                        "closed_same_meaning={} aliased={} kept_real_drift={} kept_unclear={} "
                        "failed={} error={}"
                    ),
                    run_id,
                    drift_replay_reviewed,
                    drift_replay_closed_same_meaning,
                    drift_replay_aliased,
                    drift_replay_kept_real_drift,
                    drift_replay_kept_unclear,
                    drift_replay_failed,
                    drift_replay_error,
                )

            await store.set_meta("last_daily_ai_review_at", store_time_label())
            await store.finish_extraction_run(
                run_id,
                status="success",
                group_count=len(groups),
                scanned_messages=scanned,
                extracted_terms=extracted,
                promoted_candidates=candidates + ai_approved + pending_approved + semantic_kept,
                meta={
                    "kind": "daily_ai_review",
                    "candidate_count": candidates,
                    "ai_approved": ai_approved,
                    "candidate_reviewed": candidate_reviewed,
                    "candidate_approved": candidate_approved,
                    "candidate_rejected": candidate_rejected,
                    "candidate_kept": candidate_kept,
                    "candidate_failed": candidate_failed,
                    "candidate_skipped_reviewed": candidate_skipped_reviewed,
                    "candidate_search_failed": candidate_search_failed,
                    "drift_replay_reviewed": drift_replay_reviewed,
                    "drift_replay_closed_same_meaning": drift_replay_closed_same_meaning,
                    "drift_replay_aliased": drift_replay_aliased,
                    "drift_replay_kept_real_drift": drift_replay_kept_real_drift,
                    "drift_replay_kept_unclear": drift_replay_kept_unclear,
                    "drift_replay_failed": drift_replay_failed,
                    "drift_replay_error": drift_replay_error,
                    "pending_reviewed": pending_reviewed,
                    "pending_approved": pending_approved,
                    "pending_rejected": pending_rejected,
                    "pending_kept": pending_kept,
                    "semantic_reviewed": semantic_reviewed,
                    "semantic_approved": semantic_approved,
                    "semantic_rejected": semantic_rejected,
                    "semantic_kept": semantic_kept,
                    "semantic_no_info": semantic_no_info,
                    "semantic_failed": semantic_failed,
                    "search_count": search_count,
                    "search_failed": search_failed,
                    "pending_search_failed": pending_search_failed,
                    "extract_batches": extract_batches_total,
                    "extract_window": extract_window_total,
                    "review_candidates": bool(review_candidates),
                    "review_all_pending": bool(review_all_pending),
                    "rerun_reviewed_candidates": bool(rerun_reviewed_candidates),
                    "candidate_review_filter": candidate_review_filter,
                },
            )
            latency_ms = int((time.monotonic() - run_started) * 1000)
            _L.info(
                (
                    "slang review finished | task=slang_review run={} latency_ms={} "
                    "groups={} scanned={} extracted={} candidates={} ai_approved={} "
                    "candidate_reviewed={} candidate_approved={} candidate_rejected={} "
                    "candidate_kept={} candidate_failed={} "
                    "candidate_skipped_reviewed={} "
                    "drift_replay_reviewed={} drift_replay_closed={} drift_replay_aliased={} "
                    "pending_reviewed={} pending_approved={} pending_rejected={} pending_kept={} "
                    "semantic_reviewed={} semantic_approved={} semantic_rejected={} "
                    "semantic_kept={} semantic_no_info={} semantic_failed={} "
                    "search_used={} search_failed={} pending_search_failed={}"
                ),
                run_id,
                latency_ms,
                len(groups),
                scanned,
                extracted,
                candidates,
                ai_approved,
                candidate_reviewed,
                candidate_approved,
                candidate_rejected,
                candidate_kept,
                candidate_failed,
                candidate_skipped_reviewed,
                drift_replay_reviewed,
                drift_replay_closed_same_meaning,
                drift_replay_aliased,
                pending_reviewed,
                pending_approved,
                pending_rejected,
                pending_kept,
                semantic_reviewed,
                semantic_approved,
                semantic_rejected,
                semantic_kept,
                semantic_no_info,
                semantic_failed,
                bool(settings.daily_ai_review_search_enabled and search_count > 0),
                search_failed,
                pending_search_failed,
            )
            return {
                "ok": True,
                "run_id": run_id,
                "groups": groups,
                "scanned": scanned,
                "extracted": extracted,
                "candidates": candidates,
                "ai_approved": ai_approved,
                "candidate_reviewed": candidate_reviewed,
                "candidate_approved": candidate_approved,
                "candidate_rejected": candidate_rejected,
                "candidate_kept": candidate_kept,
                "candidate_failed": candidate_failed,
                "candidate_skipped_reviewed": candidate_skipped_reviewed,
                "drift_replay_reviewed": drift_replay_reviewed,
                "drift_replay_closed_same_meaning": drift_replay_closed_same_meaning,
                "drift_replay_aliased": drift_replay_aliased,
                "drift_replay_kept_real_drift": drift_replay_kept_real_drift,
                "drift_replay_kept_unclear": drift_replay_kept_unclear,
                "drift_replay_failed": drift_replay_failed,
                "drift_replay_error": drift_replay_error,
                "pending_reviewed": pending_reviewed,
                "pending_approved": pending_approved,
                "pending_rejected": pending_rejected,
                "pending_kept": pending_kept,
                "semantic_reviewed": semantic_reviewed,
                "semantic_approved": semantic_approved,
                "semantic_rejected": semantic_rejected,
                "semantic_kept": semantic_kept,
                "semantic_no_info": semantic_no_info,
                "semantic_failed": semantic_failed,
                "search_count": search_count,
                "search_failed": search_failed,
                "pending_search_failed": pending_search_failed,
                "review_all_pending": bool(review_all_pending),
                "rerun_reviewed_candidates": bool(rerun_reviewed_candidates),
                "candidate_review_filter": candidate_review_filter,
            }
        except asyncio.CancelledError as exc:
            if active_scan_batch is not None:
                await finish_scan_batch(
                    message_log,
                    active_scan_batch,
                    status="abandoned",
                    scanned_count=active_group_scanned,
                    extracted_count=active_group_extracted,
                    saved_count=active_group_saved,
                    error=str(exc) or "cancelled",
                    advance_cursor=False,
                    meta={"slang_run_id": run_id, "kind": "daily_ai_review"},
                )
            raise
        except Exception as exc:
            if active_scan_batch is not None:
                await finish_scan_batch(
                    message_log,
                    active_scan_batch,
                    status="failed",
                    scanned_count=active_group_scanned,
                    extracted_count=active_group_extracted,
                    saved_count=active_group_saved,
                    error=str(exc),
                    advance_cursor=False,
                    meta={"slang_run_id": run_id, "kind": "daily_ai_review"},
                )
            await store.finish_extraction_run(
                run_id,
                status="failed",
                group_count=len(groups),
                scanned_messages=scanned,
                extracted_terms=extracted,
                promoted_candidates=candidates + ai_approved + pending_approved + semantic_kept,
                error=str(exc),
                meta={
                    "kind": "daily_ai_review",
                    "ai_approved": ai_approved,
                    "candidate_reviewed": candidate_reviewed,
                    "candidate_approved": candidate_approved,
                    "candidate_rejected": candidate_rejected,
                    "candidate_kept": candidate_kept,
                    "candidate_failed": candidate_failed,
                    "candidate_skipped_reviewed": candidate_skipped_reviewed,
                    "candidate_search_failed": candidate_search_failed,
                    "drift_replay_reviewed": drift_replay_reviewed,
                    "drift_replay_closed_same_meaning": drift_replay_closed_same_meaning,
                    "drift_replay_aliased": drift_replay_aliased,
                    "drift_replay_kept_real_drift": drift_replay_kept_real_drift,
                    "drift_replay_kept_unclear": drift_replay_kept_unclear,
                    "drift_replay_failed": drift_replay_failed,
                    "drift_replay_error": drift_replay_error,
                    "pending_reviewed": pending_reviewed,
                    "pending_approved": pending_approved,
                    "pending_rejected": pending_rejected,
                    "pending_kept": pending_kept,
                    "semantic_reviewed": semantic_reviewed,
                    "semantic_approved": semantic_approved,
                    "semantic_rejected": semantic_rejected,
                    "semantic_kept": semantic_kept,
                    "semantic_no_info": semantic_no_info,
                    "semantic_failed": semantic_failed,
                    "review_all_pending": bool(review_all_pending),
                    "rerun_reviewed_candidates": bool(rerun_reviewed_candidates),
                    "candidate_review_filter": candidate_review_filter,
                },
            )
            latency_ms = int((time.monotonic() - run_started) * 1000)
            _L.warning(
                "slang review failed | task=slang_review run={} latency_ms={} error={}",
                run_id,
                latency_ms,
                exc,
            )
            return {"ok": False, "run_id": run_id, "error": str(exc)}

    async def _assess_candidate_batch(
        self,
        items: list[SlangExtraction],
        *,
        group_id: str,
        contexts: list[str],
    ) -> list[SlangReviewAssessment]:
        call = self._resolve_review_call()
        if call is None:
            return [
                SlangReviewAssessment(
                    term=item.term,
                    meaning=item.meaning,
                    aliases=item.aliases,
                    confidence=item.confidence,
                    reason=item.reason,
                    repeat_policy=item.repeat_policy,
                    reviewed=False,
                )
                for item in items
            ]
        payload = {
            "group_id": str(group_id),
            "candidates": [
                {
                    "index": index,
                    "term": item.term,
                    "meaning": item.meaning,
                    "aliases": item.aliases,
                    "evidence": item.evidence,
                    "confidence": item.confidence,
                    "reason": item.reason,
                    "repeat_policy": item.repeat_policy,
                    "recent_context": (contexts[index] if index < len(contexts) else item.evidence)[-1200:],
                }
                for index, item in enumerate(items)
            ],
        }
        try:
            result = await call(
                [{"type": "text", "text": _BATCH_REVIEW_SYSTEM_PROMPT}],
                [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                max_tokens=max(900, min(3200, 260 * len(items) + 400)),
            )
        except Exception:
            return [
                SlangReviewAssessment(
                    term=item.term,
                    meaning=item.meaning,
                    aliases=item.aliases,
                    confidence=item.confidence,
                    reason=item.reason,
                    repeat_policy=item.repeat_policy,
                    reviewed=False,
                )
                for item in items
            ]

        data = _extract_json_object(str(result.get("text", "")))
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raw_results = []
        by_index: dict[int, dict[str, Any]] = {}
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            try:
                index = int(raw.get("index", -1))
            except Exception:
                continue
            if 0 <= index < len(items):
                by_index[index] = raw

        assessments: list[SlangReviewAssessment] = []
        for index, item in enumerate(items):
            raw = by_index.get(index)
            item_context = contexts[index] if index < len(contexts) else item.evidence
            if not raw:
                assessments.append(await self._assess_candidate_single(item, group_id=group_id, context=item_context))
                continue
            if "approved" not in raw and "decision" not in raw:
                assessments.append(await self._assess_candidate_single(item, group_id=group_id, context=item_context))
                continue
            policy = str(raw.get("repeat_policy") or item.repeat_policy)
            if policy not in VALID_REPEAT_POLICIES:
                policy = item.repeat_policy
            try:
                confidence = float(raw.get("confidence", item.confidence))
            except Exception:
                confidence = item.confidence
            confidence = max(0.0, min(1.0, confidence))
            approved = bool(raw.get("approved", False))
            decision, decision_confidence = _candidate_decision_from_raw(
                raw,
                approved=approved,
                confidence=confidence,
            )
            if decision != "approve":
                approved = False
            elif "approved" not in raw:
                approved = True
            assessments.append(
                SlangReviewAssessment(
                    approved=approved,
                    decision=decision,
                    decision_confidence=decision_confidence,
                    term=str(raw.get("term") or item.term).strip(),
                    meaning=str(raw.get("meaning") or item.meaning).strip(),
                    aliases=_split_aliases(raw.get("aliases", item.aliases)),
                    confidence=confidence,
                    reason=str(raw.get("reason") or item.reason).strip(),
                    repeat_policy=policy,  # type: ignore[arg-type]
                    is_public_meme=bool(raw.get("is_public_meme", False)),
                )
            )
        return assessments

    async def _assess_candidate_single(
        self,
        item: SlangExtraction,
        *,
        group_id: str,
        context: str,
    ) -> SlangReviewAssessment:
        return await self._assess(item, group_id=group_id, context=context, search_result="")

    async def _assess(
        self,
        item: SlangExtraction,
        *,
        group_id: str,
        context: str,
        search_result: str,
    ) -> SlangReviewAssessment:
        call = self._resolve_review_call()
        if call is None:
            return SlangReviewAssessment(
                term=item.term,
                meaning=item.meaning,
                aliases=item.aliases,
                confidence=item.confidence,
                reason=item.reason,
                repeat_policy=item.repeat_policy,
                reviewed=False,
            )
        payload = {
            "group_id": group_id,
            "candidate": {
                "term": item.term,
                "meaning": item.meaning,
                "aliases": item.aliases,
                "evidence": item.evidence,
                "confidence": item.confidence,
                "reason": item.reason,
                "repeat_policy": item.repeat_policy,
            },
            "recent_context": context[-3000:],
            "search_result": search_result[:2500],
        }
        try:
            result = await call(
                [{"type": "text", "text": _REVIEW_SYSTEM_PROMPT}],
                [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                max_tokens=700,
            )
        except Exception:
            return SlangReviewAssessment(
                term=item.term,
                meaning=item.meaning,
                aliases=item.aliases,
                confidence=item.confidence,
                reason=item.reason,
                repeat_policy=item.repeat_policy,
                reviewed=False,
            )
        data = _extract_json_object(str(result.get("text", "")))
        if not data or ("approved" not in data and "decision" not in data):
            return SlangReviewAssessment(
                term=item.term,
                meaning=item.meaning,
                aliases=item.aliases,
                confidence=item.confidence,
                reason=item.reason,
                repeat_policy=item.repeat_policy,
                reviewed=False,
            )
        policy = str(data.get("repeat_policy") or item.repeat_policy)
        if policy not in VALID_REPEAT_POLICIES:
            policy = item.repeat_policy
        try:
            confidence = float(data.get("confidence", item.confidence))
        except Exception:
            confidence = item.confidence
        confidence = max(0.0, min(1.0, confidence))
        approved = bool(data.get("approved", False))
        decision, decision_confidence = _candidate_decision_from_raw(
            data,
            approved=approved,
            confidence=confidence,
        )
        if decision != "approve":
            approved = False
        elif "approved" not in data:
            approved = True
        return SlangReviewAssessment(
            approved=approved,
            decision=decision,
            decision_confidence=decision_confidence,
            term=str(data.get("term") or item.term).strip(),
            meaning=str(data.get("meaning") or item.meaning).strip(),
            aliases=_split_aliases(data.get("aliases", item.aliases)),
            confidence=confidence,
            reason=str(data.get("reason") or item.reason).strip(),
            repeat_policy=policy,  # type: ignore[arg-type]
            is_public_meme=bool(data.get("is_public_meme", False)),
        )

    async def _search(self, tool_registry: Any, queries: list[str], *, group_id: str) -> str:
        if tool_registry is None or not hasattr(tool_registry, "get"):
            return ""
        tool = tool_registry.get("web_search")
        if tool is None:
            return ""
        ctx = ToolContext(group_id=group_id, session_id=f"group_{group_id}")
        results: list[str] = []
        for query in queries:
            try:
                result = await asyncio.wait_for(
                    tool.execute(ctx, query=query, max_results=4),
                    timeout=8.0,
                )
            except Exception:
                continue
            text = str(result or "").strip()
            if text and "搜索失败" not in text and "未找到" not in text:
                results.append(text)
        if not results:
            return ""
        return max(results, key=len)[:2500]

    @staticmethod
    def _build_search_queries(item: SlangExtraction) -> list[str]:
        term = str(item.term or "").strip()
        aliases = [alias for alias in item.aliases if normalize_term(alias)]
        base_terms = [term, *aliases[:2]]
        queries: list[str] = []
        for value in base_terms:
            if not value:
                continue
            queries.append(f"{value} 是什么梗")
            queries.append(f"{value} 梗 含义")
        seen: set[str] = set()
        result: list[str] = []
        for query in queries:
            if query in seen:
                continue
            seen.add(query)
            result.append(query)
        return result[:4]

    @staticmethod
    def _pick_source_row(evidence: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return select_slang_source_row(evidence, rows)

    @staticmethod
    def _speaker_to_user_id(speaker: str | None) -> str:
        return speaker_to_user_id(speaker)

    @staticmethod
    def _estimate_occurrences(term: str, aliases: list[str], rows: list[dict[str, Any]]) -> int:
        return estimate_slang_occurrences(term, aliases, rows)


def store_time_label() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
