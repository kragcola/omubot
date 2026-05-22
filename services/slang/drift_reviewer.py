"""Semantic gate for approved-slang drift reviews."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from services.llm.llm_request import LLMRequest
from services.slang.shared_prefix import get_shared_slang_prefix
from services.slang.types import SlangTerm

SlangDriftVerdict = Literal["same_meaning", "alias_candidate", "real_drift", "unclear"]

DRIFT_VERDICTS: set[str] = {"same_meaning", "alias_candidate", "real_drift", "unclear"}
DRIFT_GATE_MIN_CONFIDENCE = 0.72
DRIFT_GATE_TIMEOUT_S = 8.0

_SYSTEM_PROMPT = """你是 Omubot 的黑话语义漂移判定器。

## 漂移判定纪律

- 你处在 slang 流水线的**第三环**：extractor 提取候选 → reviewer 复核入库 →
  **你（drift reviewer）监控已入库词条的语义迁移** → semantic 三阶段做近义聚类。
  你看到的 existing 词条已经是经过审核入库的"正式词条"，不是候选。
- 两类错判代价**不对称**：
  - **错判 real_drift**（其实只是同义改写）：触发人工漂移审核工单，浪费维护者
    时间；如果发生在高频词条上，每次新证据都拉一次工单会让队列爆掉。
  - **错判 same_meaning**（其实真的漂移了）：词条释义和群内实际用法静默分裂，
    主对话链路依然按旧释义引用词条，机器人在群里"理解错"会被察觉。
  所以默认立场是**"宁可 unclear 不可 real_drift"**：unclear 让证据再积累一段
  时间再看；real_drift 直接拉人工，是最重的判决。
- 四档 verdict 的语义递进（从最轻到最重）：
  - **same_meaning**：核心指代一致，只是同义改写、更短/更详细、补例、上下位
    表述。**绝大多数新证据都应落到这里**。
  - **alias_candidate**：新证据主要是词形变化（缩写、扩写、简称、别名），
    含义仍与现有释义一致，应该转成 aliases 而不是漂移。
  - **real_drift**：同一个词在群内出现了**不同核心指代、不同动作目标、或
    相反用法**——这是"语义已经分裂"的信号，必须人工介入。
  - **unclear**：证据不足、上下文冲突、只是搜索噪声、或你不确定。**默认避风港**。
- DRIFT_GATE_MIN_CONFIDENCE = 0.72 是工程阈值：低于这个 confidence 的判决，
  调用方会**强制降级为 unclear**。所以你输出 confidence < 0.72 的 real_drift，
  下游也不会信任——直接给 unclear 反而更诚实。
- 不要因为表述不同就判 real_drift，也不要因为 n-gram 字面重叠低就判 real_drift。
  真实漂移的信号是"同一个词在群里现在用来指 A，但词条释义说它是 B"——
  这是**指代分裂**，不是表述差异。

## 任务说明

你会收到一个已批准黑话的现有释义、一次新证据释义、别名和证据。你的任务是判断它是否真的发生了语义漂移。

只输出 JSON，不要输出 Markdown。格式：
{
  "verdict": "same_meaning|alias_candidate|real_drift|unclear",
  "confidence": 0.0,
  "reason": "50字以内理由"
}

判定：
- same_meaning：新旧释义核心指代一致，只是同义改写、更短、更详细、补充例子或上位/下位表述。
- alias_candidate：新证据主要说明词形、简称、别名变化，含义仍与现有释义一致，适合转成别名而不是漂移。
- real_drift：同一个词在群内出现了不同核心指代、不同动作目标或相反用法，应进入人工漂移审核。
- unclear：证据不足、上下文冲突、只是搜索噪声，或你不确定。

约束：
- 不要因为表述不同就判 real_drift。
- 不要因为 n-gram 字面重叠低就判 real_drift。
- 没有高把握时选择 unclear。
"""


@dataclass(slots=True)
class SlangDriftAssessment:
    verdict: SlangDriftVerdict = "unclear"
    confidence: float = 0.0
    reason: str = ""
    reviewed: bool = False
    error: str = ""

    def to_meta(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "drift_semantic_reviewed": bool(self.reviewed),
            "drift_semantic_verdict": self.verdict,
            "drift_semantic_confidence": round(max(0.0, min(1.0, float(self.confidence or 0.0))), 3),
        }
        if self.reason:
            meta["drift_semantic_reason"] = self.reason
        if self.error:
            meta["drift_semantic_error"] = self.error
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


class SlangDriftReviewer:
    """Classify meaning changes before creating visible drift reviews."""

    def __init__(self, llm_client: Any = None, *, timeout_s: float = DRIFT_GATE_TIMEOUT_S) -> None:
        self._llm_client = llm_client
        self._timeout_s = max(0.1, float(timeout_s or DRIFT_GATE_TIMEOUT_S))

    def _resolve_call(self) -> Any:
        if self._llm_client is None:
            return None
        return getattr(self._llm_client, "_call", None)

    async def review_drift(
        self,
        *,
        existing: SlangTerm,
        new_meaning: str,
        aliases: list[str],
        evidence: str,
        confidence: float,
        reason: str,
    ) -> SlangDriftAssessment:
        call = self._resolve_call()
        if call is None:
            return SlangDriftAssessment(error="llm_unavailable")
        payload = {
            "term": existing.term,
            "existing_aliases": existing.aliases,
            "new_aliases": aliases,
            "old_meaning": existing.meaning,
            "new_meaning": str(new_meaning or "").strip(),
            "evidence": str(evidence or "").strip()[:2000],
            "source_confidence": max(0.0, min(1.0, float(confidence or 0.0))),
            "source_reason": str(reason or "").strip()[:500],
        }
        try:
            request = LLMRequest(
                task="slang_drift",
                static_blocks=[get_shared_slang_prefix()],
                stable_blocks=[_SYSTEM_PROMPT],
                user_messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                max_tokens=240,
                requires_capabilities=("chat",),
            )
            result = await asyncio.wait_for(
                call(request),
                timeout=self._timeout_s,
            )
        except TimeoutError:
            return SlangDriftAssessment(reviewed=True, error="drift_semantic_timeout")
        except Exception as exc:
            return SlangDriftAssessment(reviewed=True, error=f"drift_semantic_call_failed:{exc}")

        data = _extract_json_object(str(result.get("text", "") if isinstance(result, dict) else ""))
        if not data:
            return SlangDriftAssessment(reviewed=True, error="drift_semantic_parse_failed")
        verdict = str(data.get("verdict") or "").strip().lower()
        if verdict not in DRIFT_VERDICTS:
            verdict = "unclear"
        gate_confidence = _float_value(data.get("confidence"), 0.0)
        reason_text = re.sub(r"\s+", " ", str(data.get("reason") or "").strip())[:80]
        if gate_confidence < DRIFT_GATE_MIN_CONFIDENCE:
            return SlangDriftAssessment(
                verdict="unclear",
                confidence=gate_confidence,
                reason=reason_text or f"low_confidence_{verdict}",
                reviewed=True,
            )
        return SlangDriftAssessment(
            verdict=verdict,  # type: ignore[arg-type]
            confidence=gate_confidence,
            reason=reason_text,
            reviewed=True,
        )
