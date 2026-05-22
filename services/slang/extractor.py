"""Lightweight LLM-assisted slang candidate extractor."""

from __future__ import annotations

import json
import re
from typing import Any

from services.llm.llm_request import LLMRequest
from services.slang.quality import assess_candidate_quality, is_noise_term, normalize_slang_key
from services.slang.shared_prefix import get_shared_slang_prefix
from services.slang.types import (
    VALID_REPEAT_POLICIES,
    SlangExtraction,
    SlangSettings,
)

_SYSTEM_PROMPT = """你是 Omubot 的群聊黑话候选提取器。

## 提取纪律

- 你处在 slang 流水线的**第一环**：**你（extractor）从群聊里挑候选**
  → reviewer 逐个复核是否真的入库 → drift 监控已入库词条的语义漂移 →
  semantic 三阶段做语义近义聚类。你的输出会被自动写入候选队列，每个候选
  都会触发一次 reviewer LLM 调用。
- 两类错误代价**不对称**：
  - **假阳**（把普通词标成候选）：每多一个噪声候选，就要多消耗一次
    reviewer LLM 调用预算 + 占用人工审核队列容量。每天群聊有数百条消息，
    噪声膨胀会直接打爆下游。
  - **假阴**（错过真黑话）：错过的真黑话只是等下次定时抽样再挑出来——
    群内真正高频用的黑话不会因为漏一次就消失。
  所以默认立场是**保守提取**：宁可漏，不可滥。
- confidence 取值惯例：
  - **0.6+**：群内有明显特殊用法，且证据语境清晰（同一个词在群里被反复
    用作非字面义；或与字面义形成明显反差）。
  - **0.3-0.6**：低频但有一定特异性的词，给 reviewer 留判断空间。
  - **≤0.3**：只是低频但无群内特异性的普通词。这些通常会被 reviewer 直接
    拒绝，所以更应保守不出。
- evidence 字段必须是**包含候选词的真句原文**——不是"这是个梗"这种总结，
  也不是只有候选词重复出现的拼接。reviewer 在没有真实语境时会直接拒绝。
- repeat_policy 默认 `understand_only`，三档语义：
  - `understand_only`：机器人能理解此词，但不主动使用（默认；公网敏感梗用这个）。
  - `allow_rephrase`：机器人可在改述场景下使用近义形式。
  - `allow_use`：机器人可直接使用此词与群成员对话（仅限群内自然形成且无攻击性）。
- 8 个候选硬上限的工程理由：单次 LLM 输出 token 预算约 900 token，
  加上 reviewer 队列吞吐能力（每候选 1 次 LLM 调用）的约束，超过 8 个会
  让队列堆积；且单次群聊抽样里超过 8 个真黑话候选的概率极低，多出来的
  通常是噪声。

## 任务说明

任务：从一批群聊文本中找出"群内约定俗成、表面含义和真实用法可能不同"的词、短语、缩写或梗。

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
            request = LLMRequest(
                task="slang",
                static_blocks=[get_shared_slang_prefix()],
                stable_blocks=[_SYSTEM_PROMPT],
                user_messages=[{"role": "user", "content": body}],
                max_tokens=900,
                requires_capabilities=("chat",),
            )
            result = await self._llm_client._call(request)
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
            if normalize_slang_key(term) in stop_keys:
                continue
            aliases = item.get("aliases", [])
            if isinstance(aliases, str):
                aliases = [part.strip() for part in re.split(r"[,，\n]", aliases) if part.strip()]
            if not isinstance(aliases, list):
                aliases = []
            quality = assess_candidate_quality(term, meaning, [str(alias) for alias in aliases])
            if not quality.accepted:
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
