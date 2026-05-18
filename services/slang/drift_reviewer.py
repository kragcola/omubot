"""Semantic gate for approved-slang drift reviews."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from services.llm.llm_request import LLMRequest
from services.slang.types import SlangTerm

SlangDriftVerdict = Literal["same_meaning", "alias_candidate", "real_drift", "unclear"]

DRIFT_VERDICTS: set[str] = {"same_meaning", "alias_candidate", "real_drift", "unclear"}
DRIFT_GATE_MIN_CONFIDENCE = 0.72
DRIFT_GATE_TIMEOUT_S = 8.0

_SYSTEM_PROMPT = """你是 Omubot 的黑话语义漂移判定器。

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
                static_blocks=[_SYSTEM_PROMPT],
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
