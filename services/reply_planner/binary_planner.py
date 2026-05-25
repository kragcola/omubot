"""Reasoning-first binary reply planner."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Literal

from loguru import logger

from services.group.addressee import AddresseeResult, addressee_gate
from services.llm.llm_request import LLMRequest

BinaryReplyAction = Literal["reply", "no_reply"]
_L = logger.bind(channel="binary_planner")
_SYSTEM_PROMPT = """你是 Omubot 的群聊二分类回复规划器。
先判断这条消息是否值得 bot 回，再给出二选一决策。
只输出 JSON，不要解释额外文字：
{"reasoning":"先说明关键依据，40字以内","decision":"reply|no_reply","confidence":0.0,"reason":"20字以内"}
规则：
- 明确 @ bot、引用 bot、追问 bot 上文 → reply。
- 对其他人说话、闲聊旁支、无明确接话价值 → no_reply。
- 不确定时 reply，避免误沉默。
- register/context 只作辅助，不要改变人格设定。"""


@dataclass(frozen=True, slots=True)
class BinaryPlannerFeatures:
    current_text: str
    current_user_id: str = ""
    group_id: str = ""
    register_label: str = "neutral"
    context: str = ""
    addressee_id: str | None = None
    bot_id: str = ""
    reply_to_bot: bool = False
    recent_assistant_text: str = ""
    mood_label: Any = "neutral"
    affection_stage: Any = "acquaint"


@dataclass(frozen=True, slots=True)
class BinaryPlanDecision:
    action: BinaryReplyAction
    confidence: float
    reasoning: str
    reason: str
    parse_mode: str = "direct"
    raw_text: str = ""

    @classmethod
    def fail_open(cls, reason: str, *, raw_text: str = "") -> BinaryPlanDecision:
        return cls("reply", 0.0, "planner fallback", reason, "fallback", raw_text)


@dataclass(slots=True)
class NoReplyCounter:
    consecutive: int = 0

    def observe(self, action: BinaryReplyAction) -> int:
        self.consecutive = self.consecutive + 1 if action == "no_reply" else 0
        return self.consecutive


class BinaryPlanner:
    def __init__(self, *, timeout_ms: int = 800, no_reply_counter: NoReplyCounter | None = None) -> None:
        self.timeout_ms = max(1, int(timeout_ms))
        self.no_reply_counter = no_reply_counter

    async def plan(
        self, features: BinaryPlannerFeatures, *, api_call: Callable[[LLMRequest], Awaitable[dict[str, Any]]]
    ) -> BinaryPlanDecision:
        gated = mood_addressee_gate(features)
        if gated is not None:
            return self._record(gated)
        request = build_binary_planner_request(features)
        try:
            result = await asyncio.wait_for(api_call(request), timeout=self.timeout_ms / 1000)
        except TimeoutError as exc:
            _L.warning("binary planner failed open | error_type={} error={}", type(exc).__name__, str(exc)[:160])
            return self._record(BinaryPlanDecision.fail_open("planner_timeout"))
        except Exception as exc:
            _L.warning("binary planner failed open | error_type={} error={}", type(exc).__name__, str(exc)[:160])
            return self._record(BinaryPlanDecision.fail_open("planner_call_failed"))
        return self._record(parse_binary_planner_output(str(result.get("text", "") or "")))

    def _record(self, decision: BinaryPlanDecision) -> BinaryPlanDecision:
        if self.no_reply_counter is not None:
            self.no_reply_counter.observe(decision.action)
        return decision


def build_binary_planner_request(features: BinaryPlannerFeatures) -> LLMRequest:
    if _label(features.affection_stage) == "stranger" and _label(features.register_label) != "neutral":
        features = replace(features, register_label="neutral")
    payload = {
        "current_text": _truncate(features.current_text, 120),
        "current_user_id": features.current_user_id,
        "register_label": features.register_label,
        "context": _truncate(features.context, 260),
        "addressee_id": features.addressee_id,
        "bot_id": features.bot_id,
        "reply_to_bot": features.reply_to_bot,
        "recent_assistant_text": _truncate(features.recent_assistant_text, 180),
    }
    return LLMRequest(
        task="reply_gate",
        user_id=features.current_user_id,
        group_id=features.group_id or None,
        static_blocks=[_SYSTEM_PROMPT],
        user_messages=[{
            "role": "user",
            "content": "请做 reply/no_reply 二分类：\n" + json.dumps(payload, ensure_ascii=False),
        }],
        max_tokens=128,
        requires_capabilities=("chat", "json"),
    )


def mood_addressee_gate(features: BinaryPlannerFeatures) -> BinaryPlanDecision | None:
    should_suppress = addressee_gate(
        AddresseeResult(features.addressee_id, 1.0 if features.addressee_id else 0.0, "planner_features"),
        bot_ids=(features.bot_id,),
        mood_label=features.mood_label,
        reply_to_bot=features.reply_to_bot,
    )
    return BinaryPlanDecision("no_reply", 0.92, "gate", "cold_not_self", "gate") if should_suppress else None


def parse_binary_planner_output(text: str) -> BinaryPlanDecision:
    raw = text or ""
    parsed = _loads_json(_extract_fenced_json(raw) or raw)
    mode = "direct"
    if parsed is None:
        embedded = _extract_first_json_object(raw)
        parsed = _loads_json(embedded or "")
        mode = "embedded" if parsed is not None else "fallback"
    if not isinstance(parsed, dict):
        return BinaryPlanDecision.fail_open("planner_parse_failed", raw_text=raw)
    decision = str(parsed.get("decision") or parsed.get("action") or "").strip().lower()
    if decision not in {"reply", "no_reply"}:
        return BinaryPlanDecision.fail_open("invalid_decision", raw_text=raw)
    return BinaryPlanDecision(
        action=decision,  # type: ignore[arg-type]
        confidence=_clamp(parsed.get("confidence")),
        reasoning=_normalize(parsed.get("reasoning"), 80),
        reason=_normalize(parsed.get("reason"), 40) or decision,
        parse_mode=mode,
        raw_text=raw,
    )


def no_reply_threshold(consecutive: int) -> int:
    count = max(0, int(consecutive))
    return 3 if count >= 5 else 2 if count >= 3 else 1


def _loads_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None


def _extract_fenced_json(text: str) -> str | None:
    match = re.search(r"```(?:json|JSON)?\s*(.*?)```", text or "", flags=re.DOTALL)
    return match.group(1).strip() if match else None


def _extract_first_json_object(text: str) -> str | None:
    start = -1
    depth = 0
    in_string = False
    escape = False
    for idx, char in enumerate(text):
        if start < 0:
            if char == "{":
                start = idx
                depth = 1
            continue
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return None


def _clamp(value: Any) -> float:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        raw = 0.0
    return max(0.0, min(1.0, raw))


def _normalize(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _truncate(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def _label(value: Any | None) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("label") or value.get("stage") or value.get("mood") or "").strip().lower()
    return str(
        getattr(value, "label", "") or getattr(value, "stage", "") or getattr(value, "mood", "") or value
    ).strip().lower()
