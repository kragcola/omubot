"""Lightweight LLM-assisted slang candidate extractor."""

from __future__ import annotations

import json
import re
from typing import Any

from services.slang.quality import assess_candidate_quality, is_noise_term, normalize_slang_key
from services.slang.types import (
    VALID_REPEAT_POLICIES,
    SlangExtraction,
    SlangSettings,
)

_SYSTEM_PROMPT = """你是 Omubot 的群聊黑话候选提取器。

任务：从一批群聊文本中找出“群内约定俗成、表面含义和真实用法可能不同”的词、短语、缩写或梗。

只输出 JSON，不要输出 Markdown。格式：
{
  "terms": [
    {
      "term": "黑话词",
      "meaning": "在这个群里的实际含义",
      "aliases": ["可选别名"],
      "evidence": "能支撑判断的一句原文",
      "confidence": 0.0,
      "reason": "为什么像群内黑话",
      "repeat_policy": "understand_only"
    }
  ]
}

约束：
- 不要把普通问候、常见网络词、单纯人名、地名、作品名、品牌名当黑话，除非上下文显示它在群内有特殊含义。
- 置信度保守估计；不确定就低分。
- repeat_policy 只能是 understand_only / allow_rephrase / allow_use。
- 最多返回 8 个候选。没有候选时返回 {"terms": []}。
"""


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {"terms": loaded}
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {"terms": []}
    try:
        loaded = json.loads(match.group(0))
        return loaded if isinstance(loaded, dict) else {"terms": []}
    except Exception:
        return {"terms": []}
class SlangExtractor:
    """Extract slang candidates with the existing LLM client."""

    def __init__(self, llm_client: Any = None) -> None:
        self._llm_client = llm_client

    async def extract(
        self,
        messages: list[dict[str, Any]],
        *,
        settings: SlangSettings | None = None,
    ) -> list[SlangExtraction]:
        if self._llm_client is None or not hasattr(self._llm_client, "_call"):
            return []
        settings = settings or SlangSettings()
        body = self._format_messages(messages)
        if not body:
            return []
        try:
            call = getattr(self._llm_client, "_call_slang", self._llm_client._call)
            result = await call(
                [{"type": "text", "text": _SYSTEM_PROMPT}],
                [{"role": "user", "content": body}],
                max_tokens=900,
            )
        except Exception:
            return []

        raw = str(result.get("text", "")).strip()
        data = _extract_json_object(raw)
        terms = data.get("terms", [])
        if not isinstance(terms, list):
            return []

        extracted: list[SlangExtraction] = []
        stop_keys = {normalize_slang_key(stop) for stop in settings.stoplist}
        for item in terms:
            if not isinstance(item, dict):
                continue
            term = str(item.get("term", "")).strip()
            if is_noise_term(term):
                continue
            meaning = str(item.get("meaning", "")).strip()
            aliases = item.get("aliases", [])
            if isinstance(aliases, str):
                aliases = [part.strip() for part in re.split(r"[,，\n]", aliases) if part.strip()]
            if not isinstance(aliases, list):
                aliases = []
            quality = assess_candidate_quality(term, meaning, [str(alias) for alias in aliases])
            if not quality.accepted:
                continue
            candidate_keys = {
                normalize_slang_key(term),
                *(normalize_slang_key(alias) for alias in quality.cleaned_aliases),
            }
            if any(key and key in stop_keys for key in candidate_keys):
                continue
            policy = str(item.get("repeat_policy") or settings.repeat_policy)
            if policy not in VALID_REPEAT_POLICIES:
                policy = settings.repeat_policy
            try:
                confidence = float(item.get("confidence", 0.3))
            except Exception:
                confidence = 0.3
            extracted.append(SlangExtraction(
                term=term,
                meaning=meaning,
                aliases=quality.cleaned_aliases,
                evidence=str(item.get("evidence", "")).strip(),
                confidence=max(0.0, min(1.0, confidence)),
                reason=str(item.get("reason", "")).strip(),
                repeat_policy=policy,  # type: ignore[arg-type]
            ))
        return extracted[:8]

    @staticmethod
    def _format_messages(messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for row in messages[-120:]:
            text = str(row.get("content_text") or "").strip()
            if not text:
                continue
            speaker = str(row.get("speaker") or row.get("user_id") or "unknown")
            lines.append(f"{speaker}: {text[:500]}")
        return "\n".join(lines)
