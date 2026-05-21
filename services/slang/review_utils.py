"""Shared review utilities for slang AI assessment.

Used by both the backlog reviewer (existing candidates) and extraction
(if inline review is ever re-added). Keeps the LLM prompt, JSON parsing,
web search, and assessment dataclass in one place.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from services.llm.llm_request import LLMRequest
from services.slang.shared_prefix import get_shared_slang_prefix
from services.slang.store import normalize_term
from services.slang.types import (
    VALID_REPEAT_POLICIES,
    RepeatPolicy,
    SlangExtraction,
)
from services.tools.context import ToolContext

_REVIEW_SYSTEM_PROMPT = """你是 Omubot 的黑话/网络梗复核器。

你会收到一个群聊候选词、群内证据和可选搜索结果。请判断它是否足够可靠，可以被标记为"AI 通过"。

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


def build_search_queries(item: SlangExtraction) -> list[str]:
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


async def run_web_search(
    tool_registry: Any,
    queries: list[str],
    *,
    group_id: str,
) -> str:
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


async def assess_with_llm(
    llm_client: Any,
    item: SlangExtraction,
    *,
    group_id: str,
    context: str,
    search_result: str,
) -> SlangReviewAssessment:
    """Call the review LLM and return the parsed assessment."""
    if llm_client is None or not hasattr(llm_client, "_call"):
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
        request = LLMRequest(
            task="slang_review",
            static_blocks=[get_shared_slang_prefix()],
            stable_blocks=[_REVIEW_SYSTEM_PROMPT],
            user_messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            max_tokens=700,
            requires_capabilities=("chat",),
        )
        result = await llm_client._call(request)
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
