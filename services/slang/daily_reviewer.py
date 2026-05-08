"""Daily AI reviewer for search-assisted slang and meme approval."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from services.slang.extractor import SlangExtractor
from services.slang.quality import assess_candidate_quality
from services.slang.store import normalize_term
from services.slang.types import (
    VALID_REPEAT_POLICIES,
    RepeatPolicy,
    SlangExtraction,
    SlangSettings,
)
from services.tools.context import ToolContext

if TYPE_CHECKING:
    from services.slang.store import SlangStore

_REVIEW_SYSTEM_PROMPT = """你是 Omubot 的黑话/网络梗复核器。

你会收到一个群聊候选词、群内证据和可选搜索结果。请判断它是否足够可靠，可以被标记为“AI 通过”。

只输出 JSON，不要输出 Markdown。格式：
{
  "approved": true,
  "term": "标准词条",
  "meaning": "简洁解释，优先保留群内语境；如果是公网梗，说明其常见含义",
  "aliases": ["可选别名"],
  "confidence": 0.0,
  "reason": "判断依据",
  "repeat_policy": "understand_only",
  "is_public_meme": true
}

约束：
- 群内证据和搜索结果都支持时，才给高置信。
- 如果只是普通人名、作品名、品牌名、常见问候或普通词，不要批准。
- 搜索结果无法证明是梗时，approved 应为 false。
- repeat_policy 只能是 understand_only / allow_rephrase / allow_use。
"""


@dataclass
class SlangReviewAssessment:
    approved: bool = False
    term: str = ""
    meaning: str = ""
    aliases: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    repeat_policy: RepeatPolicy = "understand_only"
    is_public_meme: bool = False


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


class SlangDailyReviewer:
    """Run the daily search-assisted AI review without changing plugin contracts."""

    def __init__(self, llm_client: Any = None) -> None:
        self._llm_client = llm_client
        self._extractor = SlangExtractor(llm_client)

    async def run(
        self,
        *,
        store: SlangStore,
        message_log: Any,
        settings: SlangSettings,
        tool_registry: Any = None,
        group_id: str | None = None,
        group_filter: Callable[[str | None], bool] | None = None,
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
            },
        )
        scanned = 0
        extracted = 0
        candidates = 0
        ai_approved = 0
        search_count = 0
        search_failed = 0
        try:
            for gid in groups:
                rows = await message_log.query_recent(str(gid), limit=settings.daily_ai_recent_message_limit)
                user_rows = [row for row in rows if row.get("role") == "user" and row.get("content_text")]
                if not user_rows:
                    continue
                scanned += len(user_rows)
                extractions = await self._extractor.extract(user_rows, settings=settings)
                extracted += len(extractions)
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
                    if not quality.accepted and assessment.meaning and item.meaning and assessment.meaning != item.meaning:
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
                        and bool(search_result)
                        and assessment.approved
                        and confidence >= settings.daily_ai_auto_approve_min_confidence
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
                            ai_approved += 1
                            written_for_group += 1
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
                        candidates += 1
                        written_for_group += 1

            await store.set_meta("last_daily_ai_review_at", store_time_label())
            await store.finish_extraction_run(
                run_id,
                status="success",
                group_count=len(groups),
                scanned_messages=scanned,
                extracted_terms=extracted,
                promoted_candidates=candidates + ai_approved,
                meta={
                    "kind": "daily_ai_review",
                    "candidate_count": candidates,
                    "ai_approved": ai_approved,
                    "search_count": search_count,
                    "search_failed": search_failed,
                },
            )
            return {
                "ok": True,
                "run_id": run_id,
                "groups": groups,
                "scanned": scanned,
                "extracted": extracted,
                "candidates": candidates,
                "ai_approved": ai_approved,
                "search_count": search_count,
                "search_failed": search_failed,
            }
        except Exception as exc:
            await store.finish_extraction_run(
                run_id,
                status="failed",
                group_count=len(groups),
                scanned_messages=scanned,
                extracted_terms=extracted,
                promoted_candidates=candidates + ai_approved,
                error=str(exc),
                meta={"kind": "daily_ai_review", "ai_approved": ai_approved},
            )
            return {"ok": False, "run_id": run_id, "error": str(exc)}

    async def _assess(
        self,
        item: SlangExtraction,
        *,
        group_id: str,
        context: str,
        search_result: str,
    ) -> SlangReviewAssessment:
        if self._llm_client is None or not hasattr(self._llm_client, "_call"):
            return SlangReviewAssessment(
                term=item.term,
                meaning=item.meaning,
                aliases=item.aliases,
                confidence=item.confidence,
                reason=item.reason,
                repeat_policy=item.repeat_policy,
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
            result = await self._llm_client._call(
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
            )
        data = _extract_json_object(str(result.get("text", "")))
        policy = str(data.get("repeat_policy") or item.repeat_policy)
        if policy not in VALID_REPEAT_POLICIES:
            policy = item.repeat_policy
        try:
            confidence = float(data.get("confidence", item.confidence))
        except Exception:
            confidence = item.confidence
        return SlangReviewAssessment(
            approved=bool(data.get("approved", False)),
            term=str(data.get("term") or item.term).strip(),
            meaning=str(data.get("meaning") or item.meaning).strip(),
            aliases=_split_aliases(data.get("aliases", item.aliases)),
            confidence=max(0.0, min(1.0, confidence)),
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
        for query in queries:
            try:
                result = await asyncio.wait_for(
                    tool.execute(ctx, query=query, max_results=4),
                    timeout=8.0,
                )
            except Exception:
                return ""
            text = str(result or "").strip()
            if text and "搜索失败" not in text and "未找到" not in text:
                return text[:2500]
        return ""

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
        evidence = (evidence or "").strip()
        if evidence:
            for row in rows:
                text = str(row.get("content_text") or "")
                if evidence in text or text in evidence:
                    return row
        return rows[-1] if rows else {}

    @staticmethod
    def _speaker_to_user_id(speaker: str | None) -> str:
        if not speaker:
            return ""
        match = re.search(r"\((\d{4,})\)\s*$", str(speaker))
        return match.group(1) if match else ""

    @staticmethod
    def _estimate_occurrences(term: str, aliases: list[str], rows: list[dict[str, Any]]) -> int:
        keys = {normalize_term(term), *(normalize_term(alias) for alias in aliases)}
        keys = {key for key in keys if len(key) >= 2}
        if not keys:
            return 1
        count = 0
        for row in rows:
            text_key = normalize_term(str(row.get("content_text") or ""))
            if any(key in text_key for key in keys):
                count += 1
        return max(1, count)


def store_time_label() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
