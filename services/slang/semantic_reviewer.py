"""Three-stage semantic reviewer for slang pending candidates."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from services.llm.llm_request import LLMRequest
from services.slang.shared_prefix import get_shared_slang_prefix
from services.slang.store import normalize_term
from services.slang.types import SlangPendingCandidate

SEMANTIC_REVIEW_THRESHOLDS: tuple[int, ...] = (2, 4, 8, 12, 24, 60, 100)
_REFERENCE_MEANING_THRESHOLDS = {24, 60, 100}
_SEMANTIC_STAGE_TIMEOUT_S = 8.0
_MIN_STAGE_CONFIDENCE = 0.55
_MIN_COMPARE_CONFIDENCE = 0.72

_CONTEXT_SYSTEM_PROMPT = """你是 Omubot 黑话语义复核器的第一阶段：上下文推断。

## 阶段一纪律

- 你处在 semantic 三阶段流水线的**入口**：
  **阶段一（你·上下文推断）** → 阶段二（词条裸推断）→ 阶段三（语义对比）。
  三阶段完整跑完后会得出"上下文含义 vs 字面义是否相似"的最终判决。
- 你的输出**只**会传给阶段三做对比，**不会**直接决定候选是否入库——
  阶段三对比结果才是入库依据。所以你不必"硬给一个含义"，更应该诚实地
  在没有线索时输出 no_info=true。
- 隔离纪律是这套流水线的核心：你**只看群聊证据**，**不要**用公网知识、
  网络梗常识、或对词形的字面联想来填补上下文空白——那是阶段二的职责。
  跨阶段污染会让阶段三的对比退化成"我对自己的两个判断打分"，整个三阶段
  就失去了意义。
- no_info=true 的边界：
  - 候选词在群聊证据里只出现 1-2 次，且无任何对其含义的暗示
  - 上下文里候选词只是被引用、复读、或孤立出现
  - 上下文里能看到候选词，但它显然只是普通词、人名、作品名、品牌名、
    标准短句的字面用法
  这三种情况都必须 no_info=true 且 meaning 留空。
- _MIN_STAGE_CONFIDENCE = 0.55 是工程阈值：低于此值的阶段一判断会被
  调用方视为"上下文证据不足"。所以模糊的猜测请直接给低 confidence，
  不要硬抬到 0.55 以上。

任务：你会收到一个候选词、别名和群聊证据。只根据群聊上下文推断该词在本群里的实际含义。

只输出 JSON，不要输出 Markdown。格式：
{
  "meaning": "根据上下文推断出的含义",
  "no_info": false,
  "confidence": 0.0,
  "reason": "50字以内理由"
}

约束：
- 不要使用公网搜索结果。
- 如果上下文不足以判断，或只是普通词、人名、作品名、品牌名、普通短句，no_info=true，meaning 留空。
- confidence 保守估计。
"""

_LITERAL_SYSTEM_PROMPT = """你是 Omubot 黑话语义复核器的第二阶段：词条裸推断。

## 阶段二纪律

- 你处在 semantic 三阶段流水线的**第二环**：阶段一推断了"群内上下文含义"，
  现在轮到**你（词条裸推断）**给出"词条本身的字面/公网常见含义"。
  阶段三会拿你和阶段一的两个 meaning 做对比，判断是否相似。
- 隔离纪律：你**只看候选词和别名本身**——不读群聊证据，不读阶段一结论，
  不参考调用方传入的任何上下文。这条隔离极其重要：
  - 如果你被群聊上下文污染，阶段三对比的就是"上下文含义 vs 上下文含义"
    的两个变体，永远会判 is_similar=true，整个 semantic 判决就废了。
  - 反之，如果阶段一被你的字面义污染，对比也会失去信号。
  两个阶段的独立性是三阶段判决的根。
- 候选词如果是人名、地名、作品名、品牌名、常用短句，请如实给出"它的常见
  公网含义就是它字面所指的人/物/品牌/短句"——不要试图给一个"梗解释"。
  这是阶段三判定 is_similar=true 的关键信号（用法和字面义重合 = 不是黑话）。
- 候选词如果是公网梗（出处明显、含义稳定），给出公网常见含义即可，不必
  保守。但如果候选词在公网搜索里只是低频专有名词或没有稳定含义，
  confidence 应给低分。
- _MIN_STAGE_CONFIDENCE = 0.55 是工程阈值：低于此值的阶段二判断会被
  视为"裸义不可知"，下游会更倾向 no_info 或 unclear。

任务：你会收到一个候选词和别名。只根据词条本身推断它的常见字面含义或公网常见含义，不要使用群聊上下文。

只输出 JSON，不要输出 Markdown。格式：
{
  "meaning": "词条本身最可能的常见含义",
  "confidence": 0.0,
  "reason": "50字以内理由"
}

约束：
- 不要使用公网搜索结果。
- 不确定时给低 confidence。
"""

_COMPARE_SYSTEM_PROMPT = """你是 Omubot 黑话语义复核器的第三阶段：语义对比。

## 阶段三纪律

- 你处在 semantic 三阶段流水线的**判决环**：阶段一给出"群内上下文含义"
  → 阶段二给出"字面/公网常见含义" → **你（语义对比）做最终判决**。
- 你的输入**只有两个 meaning 字符串**——阶段一的上下文含义和阶段二的字面义。
  你**看不到**原始群聊证据、看不到候选词本身、看不到搜索结果。
  这是设计上的隔离：让对比阶段不被原始噪声污染，**只**判断两段释义的相似度。
- is_similar 的判定阈值：
  - **true（不算群内黑话）**：两段释义的核心指代一致 + 应用场景一致。
    典型案例：候选词的字面义和上下文用法重合（例如人名就是指那个人）。
  - **false（更像群内黑话）**：核心指代或应用场景**任意一个**明显不同。
    典型案例：字面义是"鸽子"但上下文用来指"放别人鸽子的人"——指代变了。
  只要有一个维度明显不同就 false，不要因为"两个释义都没那么准"而判 true。
- _MIN_COMPARE_CONFIDENCE = 0.72 是工程阈值：低于此 confidence 的对比判决，
  调用方会**强制视为 unclear**（无法判定）。所以模糊的对比请如实给低分，
  不要硬抬。低分 + unclear 对下游而言是诚实信号；高分 + 错判对下游是噪声。
- 不要用公网知识、不要查搜索结果、不要回到原始上下文——你的工作就是
  对两段已经写好的释义做语义近似度判断。

任务：比较"上下文含义"和"词条裸含义"是否足够相似。

只输出 JSON，不要输出 Markdown。格式：
{
  "is_similar": false,
  "confidence": 0.0,
  "reason": "50字以内理由"
}

判定：
- is_similar=true 表示上下文含义和裸含义接近，不应当作为群内黑话批准。
- is_similar=false 表示群内用法与表面含义明显不同，更像群内黑话/梗。
- 不确定时降低 confidence；不要用搜索结果覆盖语义对比。
"""


@dataclass(slots=True)
class SlangSemanticAssessment:
    """Result of one threshold-gated semantic inference attempt."""

    reviewed: bool = False
    forced: bool = False
    complete: bool = False
    no_info: bool = False
    threshold: int = 0
    count: int = 0
    include_reference_meaning: bool = False
    context_meaning: str = ""
    context_confidence: float = 0.0
    context_reason: str = ""
    literal_meaning: str = ""
    literal_confidence: float = 0.0
    literal_reason: str = ""
    is_similar: bool | None = None
    compare_confidence: float = 0.0
    compare_reason: str = ""
    confidence: float = 0.0
    reason: str = ""
    error: str = ""

    def to_meta(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "semantic_review": bool(self.reviewed),
            "semantic_force_review": bool(self.forced),
            "semantic_inference_complete": bool(self.complete),
            "last_semantic_inference_count": int(self.threshold or 0),
            "semantic_count": int(self.count or 0),
            "semantic_threshold": int(self.threshold or 0),
            "semantic_reference_meaning_used": bool(self.include_reference_meaning),
        }
        if self.context_meaning:
            meta["semantic_context_meaning"] = self.context_meaning
        if self.literal_meaning:
            meta["semantic_literal_meaning"] = self.literal_meaning
        if self.is_similar is not None:
            meta["semantic_is_similar"] = bool(self.is_similar)
        if self.compare_reason:
            meta["semantic_compare_reason"] = self.compare_reason
        if self.reason:
            meta["semantic_reason"] = self.reason
        if self.error:
            meta["semantic_error"] = self.error
        meta["semantic_confidence"] = round(max(0.0, min(1.0, float(self.confidence or 0.0))), 3)
        meta["semantic_context_confidence"] = round(
            max(0.0, min(1.0, float(self.context_confidence or 0.0))),
            3,
        )
        meta["semantic_literal_confidence"] = round(
            max(0.0, min(1.0, float(self.literal_confidence or 0.0))),
            3,
        )
        meta["semantic_compare_confidence"] = round(
            max(0.0, min(1.0, float(self.compare_confidence or 0.0))),
            3,
        )
        if self.no_info:
            meta["semantic_no_info"] = True
        if self.error:
            meta["semantic_failed"] = True
        return meta


def _extract_json_object(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    try:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", value, flags=re.S)
    if not match:
        return {}
    try:
        loaded = json.loads(match.group(0))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "是", "相似"}


def _threshold_for_count(count: int, last_count: int) -> int | None:
    threshold = 0
    for candidate in SEMANTIC_REVIEW_THRESHOLDS:
        if count >= candidate:
            threshold = candidate
    if threshold <= 0 or threshold <= last_count:
        return None
    return threshold


def _recent_context(rows: list[dict[str, Any]], *, limit: int = 12) -> str:
    lines: list[str] = []
    for row in rows[-limit:]:
        text = str(row.get("content_text") or "").strip()
        if not text:
            continue
        speaker = str(row.get("speaker") or row.get("user_id") or "unknown").strip()
        lines.append(f"{speaker}: {text[:500]}")
    return "\n".join(lines)


class SlangSemanticReviewer:
    """Infer whether a candidate is genuinely group slang without changing DB schema."""

    def __init__(self, llm_client: Any = None, *, stage_timeout_s: float = _SEMANTIC_STAGE_TIMEOUT_S) -> None:
        self._llm_client = llm_client
        self._stage_timeout_s = max(0.1, float(stage_timeout_s or _SEMANTIC_STAGE_TIMEOUT_S))

    @staticmethod
    def next_threshold(count: int, last_count: int) -> int | None:
        return _threshold_for_count(count, last_count)

    def threshold_for_pending(self, pending: SlangPendingCandidate) -> int | None:
        count = max(0, int(pending.count or 0))
        last_count = _int_value(pending.meta.get("last_semantic_inference_count"), 0)
        return self.next_threshold(count, last_count)

    async def review_pending(
        self,
        pending: SlangPendingCandidate,
        *,
        group_id: str,
        user_rows: list[dict[str, Any]],
        force: bool = False,
    ) -> SlangSemanticAssessment:
        count = max(0, int(pending.count or 0))
        threshold = self.threshold_for_pending(pending)
        if threshold is None:
            if not force:
                return SlangSemanticAssessment(
                    reviewed=False,
                    threshold=0,
                    count=count,
                    reason="semantic_threshold_not_reached",
                )
            threshold = max(1, count)
        result = SlangSemanticAssessment(
            reviewed=True,
            forced=bool(force),
            threshold=threshold,
            count=count,
            include_reference_meaning=threshold in _REFERENCE_MEANING_THRESHOLDS,
        )
        payload = self._build_payload(
            group_id=group_id,
            term=pending.term,
            aliases=pending.aliases,
            evidence=pending.evidence,
            context=_recent_context(user_rows) or pending.evidence,
            threshold=threshold,
            count=count,
            reference_meaning=pending.meaning if threshold in _REFERENCE_MEANING_THRESHOLDS else "",
        )
        try:
            context_data = await self._call_stage(_CONTEXT_SYSTEM_PROMPT, payload, max_tokens=300)
            if not context_data:
                return self._failed(result, "context_parse_failed")
            result.context_meaning = str(context_data.get("meaning") or "").strip()
            result.context_confidence = _float_value(context_data.get("confidence"), 0.0)
            result.context_reason = str(context_data.get("reason") or "").strip()
            if _bool_value(context_data.get("no_info")) or not result.context_meaning:
                return self._no_info(result, result.context_reason or "context_no_info")
            if result.context_confidence < _MIN_STAGE_CONFIDENCE:
                return self._no_info(result, result.context_reason or "context_low_confidence")

            literal_data = await self._call_stage(_LITERAL_SYSTEM_PROMPT, payload, max_tokens=260)
            if not literal_data:
                return self._failed(result, "literal_parse_failed")
            result.literal_meaning = str(literal_data.get("meaning") or "").strip()
            result.literal_confidence = _float_value(literal_data.get("confidence"), 0.0)
            result.literal_reason = str(literal_data.get("reason") or "").strip()
            if not result.literal_meaning or result.literal_confidence < _MIN_STAGE_CONFIDENCE:
                return self._no_info(result, result.literal_reason or "literal_low_confidence")

            compare_payload = {
                **payload,
                "context_inference": {
                    "meaning": result.context_meaning,
                    "confidence": result.context_confidence,
                    "reason": result.context_reason,
                },
                "literal_inference": {
                    "meaning": result.literal_meaning,
                    "confidence": result.literal_confidence,
                    "reason": result.literal_reason,
                },
            }
            compare_data = await self._call_stage(_COMPARE_SYSTEM_PROMPT, compare_payload, max_tokens=220)
            if not compare_data or "is_similar" not in compare_data:
                return self._failed(result, "compare_parse_failed")
            result.is_similar = _bool_value(compare_data.get("is_similar"))
            result.compare_confidence = _float_value(compare_data.get("confidence"), 0.0)
            result.compare_reason = str(compare_data.get("reason") or "").strip()
            result.confidence = min(result.context_confidence, result.literal_confidence, result.compare_confidence)
            result.reason = result.compare_reason
            if result.compare_confidence < _MIN_COMPARE_CONFIDENCE:
                return self._no_info(result, result.compare_reason or "compare_low_confidence")
            result.complete = True
            return result
        except TimeoutError:
            return self._failed(result, "semantic_review_timeout")
        except Exception as exc:
            return self._failed(result, str(exc)[:200] or "semantic_review_failed")

    def _build_payload(
        self,
        *,
        group_id: str,
        term: str,
        aliases: list[str],
        evidence: str,
        context: str,
        threshold: int,
        count: int,
        reference_meaning: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "group_id": str(group_id),
            "term": str(term or "").strip(),
            "term_key": normalize_term(term),
            "aliases": [str(alias).strip() for alias in aliases if str(alias).strip()],
            "evidence": str(evidence or "").strip()[:1200],
            "recent_context": str(context or "").strip()[-3000:],
            "threshold": int(threshold),
            "count": int(count),
        }
        if reference_meaning:
            payload["existing_meaning_reference"] = str(reference_meaning or "").strip()[:800]
        return payload

    async def _call_stage(
        self,
        system_prompt: str,
        payload: dict[str, Any],
        *,
        max_tokens: int,
    ) -> dict[str, Any]:
        call = self._resolve_call()
        if call is None:
            return {}
        request = LLMRequest(
            task="slang_semantic",
            static_blocks=[get_shared_slang_prefix()],
            stable_blocks=[system_prompt],
            user_messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            max_tokens=max_tokens,
            requires_capabilities=("chat",),
        )
        result = await asyncio.wait_for(
            call(request),
            timeout=self._stage_timeout_s,
        )
        return _extract_json_object(str(result.get("text", "")))

    def _resolve_call(self) -> Any:
        if self._llm_client is None:
            return None
        return getattr(self._llm_client, "_call", None)

    @staticmethod
    def _no_info(result: SlangSemanticAssessment, reason: str) -> SlangSemanticAssessment:
        result.no_info = True
        result.reason = str(reason or "semantic_no_info").strip()
        values = [
            value
            for value in (result.context_confidence, result.literal_confidence, result.compare_confidence)
            if value > 0
        ]
        result.confidence = min(values) if values else 0.0
        return result

    @staticmethod
    def _failed(result: SlangSemanticAssessment, error: str) -> SlangSemanticAssessment:
        result.error = str(error or "semantic_review_failed").strip()
        result.reason = result.error
        return result
