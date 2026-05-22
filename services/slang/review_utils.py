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

## 审核纪律

- 你处在 slang 流水线的第二环：extractor 先从群聊提取候选词（带置信度和证据片段）→ **你（reviewer）
  逐个复核**是否值得入库 → drift / semantic 服务后续维护词条的语义漂移和近义聚类。
- 你的输出会被自动写入群聊知识库供主 LLM 检索引用。**错误批准**会让机器人在后续对话中
  误用一个并不存在的群内黑话；**错误拒绝**只是让候选词进入人工审核队列。两类错误代价
  不对称，**疑则拒**是审核纪律的默认立场。
- 群内证据和公网搜索结果是两类独立信号：群内证据证明"该词在本群有特殊用法"，
  公网搜索证明"该词是已知网络梗"。两者并存才是高置信批准的前提；只有公网结果
  说明"它是个梗但本群可能并未真在用它"，approved 应为 false。
- repeat_policy 四档语义：
  - `understand_only`：机器人能理解此词，但不主动使用（默认；公网敏感梗、低活跃度词条用这个）。
  - `allow_rephrase`：机器人可在改述场景下使用近义形式，但不直接复读原词。
  - `allow_use`：机器人可直接使用此词与群成员对话（仅限群内自然形成的、无攻击性的高频用语）。
  - 三档之外的取值一律视为非法，由调用方降级为 `understand_only`。
- evidence 字段为空、字符数极少、或仅含候选词本身重复出现而无语境时，
  **直接 approved=false**。不要替候选编造合理性。
- confidence 是给 extractor 看的反馈信号，不是排序权重。批准时建议 0.6-0.85；
  拒绝时建议 0.1-0.4。极端值（>0.95 或 <0.05）都应有明确证据支撑。

## 任务说明

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
