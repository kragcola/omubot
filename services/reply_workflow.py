"""Reply workflow shadow decisions and observability helpers.

This module is deliberately side-effect light: it classifies reply/workflow
signals and formats logs, but it must not send messages or change scheduler
state. Phase 1 uses it in shadow mode only.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from loguru import logger

from services.llm.llm_request import LLMRequest

ReplyWorkflowAction = Literal["force_reply", "boost", "wait", "pass", "suppress"]
ReplyWorkflowSource = Literal[
    "rule",
    "scheduler",
    "private_current_path",
    "llm_gate",
    "proactive_intent",
]
FollowupRisk = Literal["none", "low", "medium", "high"]
FollowupKind = Literal[
    "none",
    "legacy_directed",
    "explicit_continuation",
    "ambiguous_continuation",
]
SemanticGateAction = Literal["force_reply", "pass", "suppress"]
SemanticGateIntent = Literal[
    "continue_or_expand",
    "clarify_previous",
    "unrelated",
    "other_user",
    "unclear",
]

_L = logger.bind(channel="reply_workflow")

_TRAILING_PUNCT_RE = re.compile(r"[。.!！?？~～…]+$")

_AMBIGUOUS_CONTINUATION_TEXTS = {
    "继续",
    "接着",
    "然后呢",
    "还有呢",
    "后来呢",
    "之后呢",
    "下文呢",
    "后面呢",
}

_EXPLICIT_CONTINUATION_PATTERNS = (
    re.compile(r"^(继续|接着|往下|下去)(说|讲|聊|说说|讲讲|聊聊)(一下|一点|点|些|嘛|吗|呀|吧|啦|呗)?$"),
    re.compile(r"^(多|再)(说|讲|聊)(一点|点|一些|些|几句)(嘛|吗|呀|吧|啦|呗)?$"),
    re.compile(r"^(展开|详细|细)(说|讲|聊|说说|讲讲)(一下|一点|点|嘛|吗|呀|吧|啦|呗)?$"),
    re.compile(r"^(说|讲)(长|详细|清楚)(一点|点)(的话)?$"),
)

_SEMANTIC_CANDIDATE_CHARS_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_SEMANTIC_GATE_PROMPT = """你是 Omubot 的群聊回复 gate。
判断当前这条短消息是否是在要求 bot 继续、展开或澄清上一轮 bot 回复。

只输出一行 JSON，不要解释：
{"action":"force_reply|pass|suppress","confidence":0.0,"intent":"continue_or_expand|clarify_previous|unrelated|other_user|unclear","reason":"30字以内理由"}

判定规则：
- 当前消息明确承接上一轮 bot 回复，且指向 bot → force_reply。
- 当前消息是在说自己的事、对其他人说话、或只是群友闲聊 → pass。
- 有明显其他 @、不应打扰、或语义明确不是给 bot → suppress。
- 不确定时 pass。"""


@dataclass(frozen=True)
class ReplyGateDecision:
    """A normalized reply workflow decision.

    In Phase 1 this is only logged. Later phases may let group/private actors
    consume a subset of these actions.
    """

    action: ReplyWorkflowAction
    source: ReplyWorkflowSource
    confidence: float
    reason: str
    prob_delta: float = 0.0
    wait_seconds: float | None = None
    labels: dict[str, Any] = field(default_factory=dict)

    def log_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "action": self.action,
            "source": self.source,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 3),
            "reason": self.reason,
        }
        if self.prob_delta:
            fields["prob_delta"] = round(self.prob_delta, 3)
        if self.wait_seconds is not None:
            fields["wait_seconds"] = round(max(0.0, self.wait_seconds), 3)
        fields.update(self.labels)
        return fields


@dataclass(frozen=True)
class FollowupClassification:
    matched: bool
    kind: FollowupKind
    risk: FollowupRisk
    normalized_text: str
    reason: str


@dataclass(frozen=True)
class ReplyGateFeatures:
    """Small, bounded context used by the semantic reply gate."""

    current_text: str
    current_user_id: str
    has_current_trigger: bool = False
    has_recent_assistant: bool = False
    has_other_at: bool = False
    reply_to_bot: bool = False
    last_assistant_to_user: bool = False
    last_assistant_text: str = ""
    elapsed_since_assistant_s: float | None = None
    candidate_reason: str = "unchecked"


@dataclass(frozen=True)
class SemanticGateResult:
    """Parsed semantic gate result."""

    action: SemanticGateAction
    confidence: float
    intent: SemanticGateIntent
    reason: str
    parse_mode: str = "direct"
    raw_text: str = ""

    def to_decision(self, *, candidate_reason: str = "") -> ReplyGateDecision:
        labels: dict[str, Any] = {
            "intent": self.intent,
            "parse_mode": self.parse_mode,
        }
        if candidate_reason:
            labels["candidate_reason"] = candidate_reason
        return ReplyGateDecision(
            action=self.action,
            source="llm_gate",
            confidence=self.confidence,
            reason=self.reason or "semantic_gate",
            labels=labels,
        )


def is_shadow_mode(config: object | None) -> bool:
    """Return whether reply workflow observation should run."""
    return getattr(config, "mode", "shadow") == "shadow"


def workflow_mode(config: object | None) -> str:
    """Return the effective reply workflow mode.

    ``rules`` is accepted only for backward compatibility and is deliberately
    treated as shadow because regex-based behavior consumption was too brittle.
    """
    mode = str(getattr(config, "mode", "shadow") or "shadow").strip().lower()
    if mode == "rules":
        return "shadow"
    if mode in {"off", "shadow", "semantic"}:
        return mode
    return "shadow"


def normalize_followup_text(text: str) -> str:
    compact = re.sub(r"\s+", "", text or "")
    compact = compact.strip()
    while True:
        updated = _TRAILING_PUNCT_RE.sub("", compact).strip()
        if updated == compact:
            return compact
        compact = updated


def classify_followup_text(text: str, *, legacy_directed: bool = False) -> FollowupClassification:
    """Classify short continuation/follow-up utterances without changing behavior."""
    normalized = normalize_followup_text(text)
    if not normalized:
        return FollowupClassification(False, "none", "none", normalized, "empty_text")
    if len(normalized) > 24:
        return FollowupClassification(False, "none", "none", normalized, "too_long")
    if legacy_directed:
        return FollowupClassification(True, "legacy_directed", "low", normalized, "legacy_directed_followup")
    if normalized in _AMBIGUOUS_CONTINUATION_TEXTS:
        return FollowupClassification(
            True,
            "ambiguous_continuation",
            "high",
            normalized,
            "short_ambiguous_continuation",
        )
    if any(pattern.match(normalized) for pattern in _EXPLICIT_CONTINUATION_PATTERNS):
        return FollowupClassification(
            True,
            "explicit_continuation",
            "medium",
            normalized,
            "explicit_continuation_request",
        )
    return FollowupClassification(False, "none", "none", normalized, "no_followup_signal")


def should_call_semantic_gate(features: ReplyGateFeatures, *, max_chars: int = 48) -> tuple[bool, str]:
    text = normalize_followup_text(features.current_text)
    if features.has_current_trigger:
        return False, "current_trigger"
    if not text:
        return False, "empty_text"
    if len(text) > max(1, max_chars):
        return False, "too_long"
    if not _SEMANTIC_CANDIDATE_CHARS_RE.search(text):
        return False, "no_text_signal"
    if features.has_other_at:
        return False, "has_other_at"
    if not features.has_recent_assistant:
        return False, "no_recent_assistant"
    if not (features.reply_to_bot or features.last_assistant_to_user):
        return False, "not_targeted_to_bot"
    return True, "short_contextual_candidate"


def should_consume_semantic_gate(result: SemanticGateResult | None, *, threshold: float) -> bool:
    if result is None:
        return False
    return result.action == "force_reply" and result.confidence >= threshold


def _coerce_confidence(value: Any) -> float:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        raw = 0.0
    return max(0.0, min(1.0, raw))


def _normalize_reason(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:30]


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
                in_string = False
                escape = False
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


def _extract_fenced_json(text: str) -> str | None:
    for match in re.finditer(r"```(?:json|JSON)?\s*(.*?)```", text, flags=re.DOTALL):
        candidate = match.group(1).strip()
        if candidate:
            return candidate
    return None


def _semantic_result_from_data(data: Any, *, raw_text: str, parse_mode: str) -> SemanticGateResult | None:
    if not isinstance(data, dict):
        return None
    action = str(data.get("action", "pass")).strip().lower()
    if action not in {"force_reply", "pass", "suppress"}:
        action = "pass"
    intent = str(data.get("intent", "unclear")).strip().lower()
    if intent not in {
        "continue_or_expand",
        "clarify_previous",
        "unrelated",
        "other_user",
        "unclear",
    }:
        intent = "unclear"
    return SemanticGateResult(
        action=action,  # type: ignore[arg-type]
        confidence=_coerce_confidence(data.get("confidence", 0.0)),
        intent=intent,  # type: ignore[arg-type]
        reason=_normalize_reason(data.get("reason", "semantic_gate")),
        parse_mode=parse_mode,
        raw_text=raw_text,
    )


def parse_semantic_gate_output(text: str) -> SemanticGateResult | None:
    stripped = (text or "").strip()
    if not stripped:
        return None

    candidates: list[tuple[str, str]] = [("direct", stripped)]
    fenced = _extract_fenced_json(stripped)
    if fenced:
        candidates.append(("fenced", fenced))
    embedded = _extract_first_json_object(stripped)
    if embedded:
        candidates.append(("embedded", embedded))

    for parse_mode, candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        result = _semantic_result_from_data(loaded, raw_text=stripped, parse_mode=parse_mode)
        if result is not None:
            return result
    return None


def _truncate_context_text(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def build_semantic_gate_messages(features: ReplyGateFeatures) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    system = [{"type": "text", "text": _SEMANTIC_GATE_PROMPT}]
    payload = {
        "current_text": _truncate_context_text(features.current_text, 80),
        "current_user_id": features.current_user_id,
        "reply_to_bot": features.reply_to_bot,
        "last_assistant_to_user": features.last_assistant_to_user,
        "has_other_at": features.has_other_at,
        "elapsed_since_assistant_s": features.elapsed_since_assistant_s,
        "last_assistant_text": _truncate_context_text(features.last_assistant_text, 180),
    }
    messages = [{
        "role": "user",
        "content": "请判断这条消息是否应让 bot 继续回复：\n" + json.dumps(payload, ensure_ascii=False),
    }]
    return system, messages


async def evaluate_semantic_gate(
    features: ReplyGateFeatures,
    *,
    api_call: Callable[
        [LLMRequest],
        Awaitable[dict[str, Any]],
    ],
    timeout_ms: int = 600,
    user_id: str = "",
    group_id: str | None = None,
) -> SemanticGateResult | None:
    system, messages = build_semantic_gate_messages(features)
    static_blocks: list[str | dict[str, Any]] = list(system)
    request = LLMRequest(
        task="reply_gate",
        user_id=user_id,
        group_id=group_id,
        static_blocks=static_blocks,
        user_messages=list(messages),
        max_tokens=96,
        requires_capabilities=("chat",),
    )
    try:
        result = await asyncio.wait_for(
            api_call(request),
            timeout=max(0.001, timeout_ms / 1000),
        )
    except TimeoutError as exc:
        _L.warning(
            "semantic gate failed closed | error_type={} timeout_ms={} error={}",
            type(exc).__name__,
            timeout_ms,
            str(exc)[:160],
        )
        return None
    except Exception as exc:
        _L.warning(
            "semantic gate failed closed | error_type={} timeout_ms={} error={}",
            type(exc).__name__,
            timeout_ms,
            str(exc)[:160],
        )
        return None
    return parse_semantic_gate_output(str(result.get("text", "") or ""))


def evaluate_group_gate_shadow(
    *,
    text: str,
    has_trigger: bool,
    trigger_mode: str = "",
    is_addressed: bool = False,
    legacy_directed: bool = False,
    has_recent_assistant: bool = False,
    has_other_at: bool = False,
    reply_to_bot: bool = False,
    last_assistant_to_user: bool = False,
) -> tuple[ReplyGateDecision, FollowupClassification]:
    """Evaluate the future group gate decision in shadow mode.

    The decision is advisory only. Current scheduler behavior remains the
    source of truth until a later phase explicitly consumes this result.
    """
    classification = classify_followup_text(text, legacy_directed=legacy_directed)
    labels = {
        "followup_kind": classification.kind,
        "followup_risk": classification.risk,
        "has_recent_assistant": has_recent_assistant,
        "has_other_at": has_other_at,
        "reply_to_bot": reply_to_bot,
        "last_assistant_to_user": last_assistant_to_user,
        "is_addressed": is_addressed,
    }

    if has_trigger:
        return (
            ReplyGateDecision(
                action="force_reply",
                source="rule",
                confidence=1.0,
                reason=f"current_trigger:{trigger_mode or 'unknown'}",
                labels=labels,
            ),
            classification,
        )

    if classification.kind == "legacy_directed":
        return (
            ReplyGateDecision(
                action="force_reply",
                source="rule",
                confidence=0.95,
                reason="legacy_directed_followup_would_force",
                labels=labels,
            ),
            classification,
        )

    if classification.kind == "explicit_continuation":
        if has_recent_assistant and not has_other_at and (reply_to_bot or last_assistant_to_user):
            confidence = 0.9 if reply_to_bot else 0.82
            return (
                ReplyGateDecision(
                    action="force_reply",
                    source="rule",
                    confidence=confidence,
                    reason="explicit_continuation_with_recent_assistant",
                    labels=labels,
                ),
                classification,
            )
        return (
            ReplyGateDecision(
                action="boost",
                source="rule",
                confidence=0.55,
                reason="explicit_continuation_missing_context_constraint",
                prob_delta=0.25,
                labels=labels,
            ),
            classification,
        )

    if classification.kind == "ambiguous_continuation":
        if reply_to_bot and has_recent_assistant and not has_other_at:
            return (
                ReplyGateDecision(
                    action="boost",
                    source="rule",
                    confidence=0.65,
                    reason="ambiguous_continuation_replying_to_bot",
                    prob_delta=0.2,
                    labels=labels,
                ),
                classification,
            )
        return (
            ReplyGateDecision(
                action="pass",
                source="rule",
                confidence=0.72,
                reason="ambiguous_continuation_not_safe_to_force",
                labels=labels,
            ),
            classification,
        )

    return (
        ReplyGateDecision(
            action="pass",
            source="rule",
            confidence=0.5,
            reason="no_group_gate_signal",
            labels=labels,
        ),
        classification,
    )


def scheduler_shadow_decision(
    *,
    action: ReplyWorkflowAction,
    reason: str,
    threshold: float | None = None,
    mood_mult: float | None = None,
    time_mult: float | None = None,
    msg_count: int = 0,
    skips: int = 0,
    trigger_mode: str = "none",
) -> ReplyGateDecision:
    labels: dict[str, Any] = {
        "trigger_mode": trigger_mode,
        "msg_count": msg_count,
        "skips": skips,
    }
    if threshold is not None:
        labels["threshold"] = round(threshold, 3)
    if mood_mult is not None:
        labels["mood_mult"] = round(mood_mult, 3)
    if time_mult is not None:
        labels["time_mult"] = round(time_mult, 3)
    confidence = 1.0 if action in {"force_reply", "suppress"} else 0.7
    return ReplyGateDecision(
        action=action,
        source="scheduler",
        confidence=confidence,
        reason=reason,
        labels=labels,
    )


def private_current_path_decision(*, text: str) -> ReplyGateDecision:
    labels = {"text_len": len(text or "")}
    return ReplyGateDecision(
        action="force_reply",
        source="private_current_path",
        confidence=1.0,
        reason="private_message_currently_enters_llm_directly",
        labels=labels,
    )


def preview_text(text: str, limit: int = 48) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def log_shadow_decision(
    decision: ReplyGateDecision,
    *,
    conversation: str,
    mode: str,
    event_id: str = "",
    text: str = "",
    latency_ms: float = 0.0,
    extra: dict[str, Any] | None = None,
) -> None:
    fields = decision.log_fields()
    if extra:
        fields.update(extra)
    if text:
        fields["text_preview"] = preview_text(text)
    _L.info(
        "reply_workflow | conversation={} event_id={} mode={} action={} source={} "
        "confidence={:.2f} latency_ms={:.2f} reason={} fields={}",
        conversation,
        event_id,
        mode,
        decision.action,
        decision.source,
        decision.confidence,
        latency_ms,
        decision.reason,
        fields,
    )
