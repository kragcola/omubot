"""Register classifier for Part 1 humanization state."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from services.humanization.contract import REGISTER_LABEL_SLOT
from services.humanization.state import humanization_source
from services.llm.llm_request import LLMRequest
from services.system_module import RuntimeStateBus, Scope

RegisterLabel = Literal["neutral", "quiet", "playful", "affectionate", "serious", "distant"]
VALID_REGISTER_LABELS: set[str] = {"neutral", "quiet", "playful", "affectionate", "serious", "distant"}
_MAX_WINDOW = 5

_SYSTEM_PROMPT = """你是 Omubot 的语域(register)分类器。

根据最近几条群聊，判断 bot 下一句应使用的语域。只输出 JSON：
{"label":"neutral","confidence":0.0,"reason":"一句话原因","evidence":"最关键原文"}

label 只能是：
- neutral：普通日常
- quiet：低能量、轻声、少打扰
- playful：轻松、接梗、俏皮但不过火
- affectionate：亲近、安抚、带一点偏爱
- serious：认真解释、处理问题
- distant：礼貌疏离、降低亲密感

不确定时选 neutral，confidence 保守估计。
"""


@dataclass(frozen=True, slots=True)
class RegisterDecision:
    label: RegisterLabel = "neutral"
    confidence: float = 0.0
    reason: str = ""
    evidence: str = ""
    window_size: int = 0

    def to_state_value(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "reason": self.reason,
            "evidence": self.evidence,
            "window_size": self.window_size,
        }


class RegisterClassifier:
    """Classify recent conversation register with the existing LLM spine."""

    def __init__(self, llm_client: Any = None) -> None:
        self._llm_client = llm_client

    async def classify(self, messages: list[dict[str, Any]]) -> RegisterDecision:
        window = _format_window(messages)
        if not window or self._llm_client is None or not hasattr(self._llm_client, "_call"):
            return RegisterDecision(window_size=_window_size(messages))

        request = LLMRequest(
            task="thinker",
            static_blocks=[_SYSTEM_PROMPT],
            user_messages=[{"role": "user", "content": window}],
            max_tokens=220,
            requires_capabilities=("chat",),
        )
        try:
            result = await self._llm_client._call(request)
        except Exception:
            return RegisterDecision(window_size=_window_size(messages))
        return _decision_from_json(str(result.get("text", "")), window_size=_window_size(messages))

    async def classify_and_write(
        self,
        messages: list[dict[str, Any]],
        *,
        bus: RuntimeStateBus,
        scope: Scope,
    ) -> RegisterDecision:
        decision = await self.classify(messages)
        bus.set(
            REGISTER_LABEL_SLOT,
            decision.to_state_value(),
            scope=scope,
            source=humanization_source("register_classifier:classify"),
            confidence=decision.confidence,
        )
        return decision


def _window_size(messages: list[dict[str, Any]]) -> int:
    return min(len([row for row in messages if _message_text(row)]), _MAX_WINDOW)


def _format_window(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in messages[-_MAX_WINDOW:]:
        text = _message_text(row)
        if not text:
            continue
        speaker = str(row.get("speaker") or row.get("role") or row.get("user_id") or "unknown").strip()
        lines.append(f"{speaker}: {text[:300]}")
    return "\n".join(lines)


def _message_text(row: dict[str, Any]) -> str:
    return str(row.get("content_text") or row.get("content") or "").strip()


def _decision_from_json(text: str, *, window_size: int) -> RegisterDecision:
    data = _extract_json_object(text)
    label = str(data.get("label") or "neutral").strip().lower()
    if label not in VALID_REGISTER_LABELS:
        label = "neutral"
    try:
        confidence = float(data.get("confidence", 0.0))
    except Exception:
        confidence = 0.0
    return RegisterDecision(
        label=label,  # type: ignore[arg-type]
        confidence=max(0.0, min(1.0, confidence)),
        reason=str(data.get("reason") or "").strip()[:240],
        evidence=str(data.get("evidence") or "").strip()[:240],
        window_size=window_size,
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        loaded = json.loads(stripped)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    if not match:
        return {}
    try:
        loaded = json.loads(match.group(0))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}
