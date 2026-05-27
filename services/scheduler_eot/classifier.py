"""End-of-turn probability classifier for scheduler RWS."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from services.llm.llm_request import LLMRequest

_SYSTEM_PROMPT = """你是 Omubot 的群聊开口时机分类器。
只输出 JSON：
{"probability":0.0,"reason":"20字以内"}
probability 表示 bot 现在接话的时机是否成熟。
明确追问、点名、需要 bot 补充时接近 1；多人互相热聊、话题未收束时接近 0。"""


@dataclass(frozen=True, slots=True)
class EOTDecision:
    probability: float = 0.5
    reason: str = "fallback"
    parse_mode: str = "fallback"
    raw_text: str = ""


class EOTCache:
    def __init__(self, *, ttl_s: float = 30.0, min_interval_s: float = 30.0) -> None:
        self.ttl_s = max(1.0, float(ttl_s))
        self.min_interval_s = max(0.0, float(min_interval_s))
        self._items: dict[str, tuple[float, EOTDecision]] = {}
        self._last_call: dict[str, float] = {}

    def get(self, group_id: str, *, now: float | None = None) -> EOTDecision | None:
        current = time.monotonic() if now is None else now
        item = self._items.get(str(group_id))
        if item is None:
            return None
        ts, decision = item
        if current - ts > self.ttl_s:
            self._items.pop(str(group_id), None)
            return None
        return decision

    def can_call(self, group_id: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        last = self._last_call.get(str(group_id), 0.0)
        return current - last >= self.min_interval_s

    def reserve_call(self, group_id: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        if not self.can_call(group_id, now=current):
            return False
        self._last_call[str(group_id)] = current
        return True

    def put(self, group_id: str, decision: EOTDecision, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        self._last_call[str(group_id)] = current
        self._items[str(group_id)] = (current, decision)


class EOTClassifier:
    def __init__(self, *, timeout_ms: int = 1200) -> None:
        self.timeout_ms = max(1, int(timeout_ms))

    async def classify(
        self,
        messages: list[dict[str, Any]],
        *,
        group_id: str,
        api_call: Callable[[LLMRequest], Awaitable[dict[str, Any]]],
    ) -> EOTDecision:
        request = build_eot_request(messages, group_id=group_id)
        try:
            result = await asyncio.wait_for(api_call(request), timeout=self.timeout_ms / 1000)
        except TimeoutError:
            return EOTDecision(reason="eot_timeout")
        except Exception:
            return EOTDecision(reason="eot_call_failed")
        return parse_eot_output(str(result.get("text", "") or ""))


def build_eot_request(messages: list[dict[str, Any]], *, group_id: str) -> LLMRequest:
    recent = [_message_text(row) for row in messages[-5:]]
    payload = {"recent_messages": [text for text in recent if text][-5:]}
    return LLMRequest(
        task="scheduler_eot",
        group_id=str(group_id),
        static_blocks=[_SYSTEM_PROMPT],
        user_messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        max_tokens=96,
        requires_capabilities=("chat", "json"),
    )


def parse_eot_output(text: str) -> EOTDecision:
    raw = text or ""
    parsed = _loads_json(_extract_fenced_json(raw) or raw)
    mode = "direct"
    if parsed is None:
        parsed = _loads_json(_extract_first_json_object(raw) or "")
        mode = "embedded" if parsed is not None else "fallback"
    if not isinstance(parsed, dict):
        return EOTDecision(raw_text=raw)
    return EOTDecision(
        probability=_clamp(parsed.get("probability", parsed.get("p", 0.5))),
        reason=_normalize(parsed.get("reason"), 40) or "parsed",
        parse_mode=mode,
        raw_text=raw,
    )


def _message_text(row: dict[str, Any]) -> str:
    content = row.get("content", "")
    if isinstance(content, str):
        return _normalize(content, 240)
    if isinstance(content, list):
        parts = [str(block.get("text", "")) for block in content if isinstance(block, dict)]
        return _normalize(" ".join(parts), 240)
    return _normalize(str(content), 240)


def _loads_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None


def _extract_fenced_json(text: str) -> str | None:
    match = re.search(r"```(?:json|JSON)?\s*(.*?)```", text or "", flags=re.DOTALL)
    return match.group(1).strip() if match else None


def _extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    return text[start:end + 1] if start >= 0 and end > start else None


def _clamp(value: object) -> float:
    try:
        raw = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raw = 0.5
    return max(0.0, min(1.0, raw))


def _normalize(value: object, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]
