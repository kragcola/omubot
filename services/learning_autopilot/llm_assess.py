"""Shared LLM assessment for autopilot reviewers (non-slang nouns)."""

from __future__ import annotations

import json
import logging
from typing import Any

from .base import CandidateItem, ReviewVerdict

logger = logging.getLogger(__name__)

_SYSTEM_PROMPTS: dict[str, str] = {
    "slang": (
        "你是一个群聊黑话/梗审核员。判断以下词条是否是真实的群聊黑话或梗。\n"
        "评估标准：\n"
        "1. 是否是群内真实使用的黑话/缩写/梗（不是通用词汇）\n"
        "2. 释义是否准确、完整\n"
        "3. 是否有足够的群内使用证据\n"
        "回复 JSON: {\"decision\": \"approved|rejected|kept\", \"confidence\": 0.0-1.0, \"reason\": \"...\"}"
    ),
    "style": (
        "你是一个语言风格审核员。判断以下表达模式是否适合注入到 AI 角色的 prompt 中。\n"
        "评估标准：\n"
        "1. 是否是真实的语言风格/表达习惯（不是一次性用语）\n"
        "2. 是否符合角色人设（不违和、不出戏）\n"
        "3. 是否有足够的使用频率支撑\n"
        "回复 JSON: {\"decision\": \"approved|rejected|kept\", \"confidence\": 0.0-1.0, \"reason\": \"...\"}"
    ),
    "episode": (
        "你是一个情节记忆审核员。判断以下情节是否值得长期记入 AI 角色的记忆 prompt。\n"
        "评估标准：\n"
        "1. 是否是有意义的互动事件（不是日常寒暄）\n"
        "2. 是否对未来对话有参考价值\n"
        "3. 是否足够具体和可辨识\n"
        "回复 JSON: {\"decision\": \"approved|rejected|kept\", \"confidence\": 0.0-1.0, \"reason\": \"...\"}"
    ),
    "fact": (
        "你是一个知识图谱审核员。判断以下事实三元组是否正确且值得保留。\n"
        "评估标准：\n"
        "1. 事实是否正确（基于上下文证据）\n"
        "2. 主谓宾关系是否清晰无歧义\n"
        "3. 是否对理解用户/群组有价值\n"
        "回复 JSON: {\"decision\": \"approved|rejected|kept\", \"confidence\": 0.0-1.0, \"reason\": \"...\"}"
    ),
    "graph_relation": (
        "你是一个关系图谱审核员。判断以下实体关系是否正确且有意义。\n"
        "评估标准：\n"
        "1. 关系是否真实存在（有证据支撑）\n"
        "2. 关系描述是否准确\n"
        "3. 是否对理解社交网络有价值\n"
        "回复 JSON: {\"decision\": \"approved|rejected|kept\", \"confidence\": 0.0-1.0, \"reason\": \"...\"}"
    ),
}

_TASK_MAP: dict[str, str] = {
    "slang": "slang_review",
    "style": "style_review",
    "episode": "episode_review",
    "fact": "fact_review",
    "graph_relation": "graph_review",
}


async def assess_candidate(
    llm_client: Any,
    item: CandidateItem,
) -> ReviewVerdict:
    if llm_client is None or not hasattr(llm_client, "_call"):
        return ReviewVerdict(decision="kept", confidence=0.5, reason="LLM unavailable")

    system_prompt = _SYSTEM_PROMPTS.get(item.domain, _SYSTEM_PROMPTS["fact"])
    task_name = _TASK_MAP.get(item.domain, "graph_review")

    payload = {
        "candidate_id": item.id,
        "domain": item.domain,
        "content": item.content,
        "context": item.context[-2000:] if item.context else "",
        "group_id": item.group_id,
        "confidence": item.confidence,
    }

    try:
        from services.llm.llm_request import LLMRequest
        request = LLMRequest(
            task=task_name,
            static_blocks=[],
            stable_blocks=[system_prompt],
            user_messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            max_tokens=300,
            requires_capabilities=("chat",),
        )
        result = await llm_client._call(request)
    except Exception as exc:
        logger.warning("autopilot assess_candidate failed for %s/%s: %s", item.domain, item.id, exc)
        return ReviewVerdict(decision="kept", confidence=0.5, reason=f"LLM error: {exc}")

    return _parse_verdict(result)


def _parse_verdict(result: Any) -> ReviewVerdict:
    text = ""
    if isinstance(result, dict):
        text = str(result.get("text") or result.get("content") or "")
    elif isinstance(result, str):
        text = result
    else:
        text = str(result)

    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
            decision = str(data.get("decision", "kept")).lower()
            if decision not in ("approved", "rejected", "kept"):
                decision = "kept"
            return ReviewVerdict(
                decision=decision,
                confidence=float(data.get("confidence", 0.5)),
                reason=str(data.get("reason", "")),
                improved_content=str(data.get("improved_content", "")),
            )
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    return ReviewVerdict(decision="kept", confidence=0.5, reason="Failed to parse LLM response")
