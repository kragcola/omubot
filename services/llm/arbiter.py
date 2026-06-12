"""Lightweight concurrent LLM judge for burst @mention handling."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Literal, cast

import aiohttp
from loguru import logger

from kernel.config import ArbiterConfig
from services.llm.provider import create_provider
from services.llm.usage import UsageTracker

_L = logger.bind(channel="arbiter")
# Must be large enough to close the JSON object even when the model writes a
# free-text "reason" field (interruption/correction). 48 truncated mid-string
# (~3.4% invalid_json), so the JSON never closed -> wasted call + admin alert.
_MAX_TOKENS = 128

_COMPLETENESS_SYSTEM_PROMPT = """你是 QQ 群聊完整性判断器。判断用户是否说完了当前这轮话。
输出严格 JSON：{"complete": true/false, "confidence": 0.0-1.0}

判断依据（QQ 群聊语境）：
- 语义完整且可独立回应 -> complete
- 明显话说一半（"我觉得"、"就是那个"、"等一下"） -> incomplete
- 连续短消息（每条<6字）且最新一条无独立语义 -> incomplete（用户在分条打字）
- 最新消息是对前一条的补充/修正/追加条件 -> incomplete

注意：QQ 中句号(。)不代表说完，常表达语气（冷淡/强调）。不要依赖标点判断。
只输出 JSON，不要解释。"""

_INTERRUPTION_SYSTEM_PROMPT = """你是对话中断判断器。
bot 正在分段发送回复，用户发了新消息。判断 bot 是否应该中断未发送的部分。
输出严格 JSON：{"action": "continue"|"abort_unsent"|"revise", "reason": "..."}
判断依据：
- 用户新消息回答了 bot 即将问的问题 -> revise
- 用户新消息否定/修正了 bot 已发内容 -> revise
- 用户在短时间内连续追问/重复呼叫 bot（同一人多次 @ 或反复叫名，如连发"emu""emu""emu"）
  -> abort_unsent（这些是同一轮寻址，应折进一条统一回复，不要逐条复读招呼）
- 用户新消息是无关闲聊 -> continue
- 用户新消息是纯补充不矛盾 -> continue
只输出 JSON，不要解释。"""

_CORRECTION_SYSTEM_PROMPT = """你是回复修正判断器。bot 刚回复完，用户又发了新消息。判断 bot 是否需要修正刚才的回复。
输出严格 JSON：{"needs_correction": true/false, "correction_type": "retract"|"amend"|"acknowledge"|null}
判断依据：
- 用户说"不是"/"我是说"/"等等" -> needs_correction=true, type=amend
- 用户补充了改变语义的关键信息 -> needs_correction=true, type=amend
- 用户只是继续聊天/换话题 -> needs_correction=false
- 用户表示 bot 理解错误 -> needs_correction=true, type=retract
只输出 JSON，不要解释。"""


@dataclass(frozen=True, slots=True)
class PendingMessage:
    content: str
    user_id: str
    timestamp: float
    # Routing identity (multi-addressee). NOT serialized into the arbiter LLM
    # payload — see judge_completeness. Defaults keep existing call sites valid.
    target_message_id: int | None = None
    block_id: str = ""
    evidence: str = ""
    obligation_level: str = ""


@dataclass(frozen=True, slots=True)
class CompletenessResult:
    complete: bool
    confidence: float
    fallback: bool = False


@dataclass(frozen=True, slots=True)
class InterruptionResult:
    action: Literal["continue", "abort_unsent", "revise"]
    reason: str = ""
    fallback: bool = False


@dataclass(frozen=True, slots=True)
class CorrectionResult:
    needs_correction: bool
    correction_type: Literal["retract", "amend", "acknowledge"] | None = None
    fallback: bool = False


class ArbiterClient:
    """Lightweight concurrent LLM judge for real-time control signals."""

    def __init__(
        self,
        config: ArbiterConfig,
        http_session: aiohttp.ClientSession,
        *,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        self._config = config
        self._session = http_session
        self._usage_tracker = usage_tracker

    async def judge_completeness(
        self,
        pending_messages: list[PendingMessage],
        *,
        user_id: str = "",
        group_id: str | None = None,
    ) -> CompletenessResult:
        fallback = CompletenessResult(complete=True, confidence=1.0, fallback=True)
        if not self._enabled_for_group(group_id):
            return fallback
        payload = {
            # Only the semantic fields go to the judge; routing identity
            # (target_message_id/block_id/evidence/obligation_level) is internal.
            "pending_messages": [
                {"content": m.content, "user_id": m.user_id, "timestamp": m.timestamp}
                for m in pending_messages
            ],
        }
        raw = await self._call_json(
            system_prompt=_COMPLETENESS_SYSTEM_PROMPT,
            payload=payload,
            user_id=user_id,
            group_id=group_id,
        )
        if raw is None:
            return fallback
        try:
            complete = bool(raw.get("complete", True))
            confidence = _clamp_confidence(raw.get("confidence", 1.0))
            return CompletenessResult(complete=complete, confidence=confidence, fallback=False)
        except Exception:
            _L.warning("arbiter completeness parse failed | payload={!r}", raw)
            return fallback

    async def judge_interruption(
        self,
        *,
        already_sent: list[str],
        unsent: list[str],
        new_messages: list[str],
        user_id: str = "",
        group_id: str | None = None,
    ) -> InterruptionResult:
        fallback = InterruptionResult(action="continue", fallback=True)
        if not self._enabled_for_group(group_id) or not bool(self._config.interruption_enabled):
            return fallback
        raw = await self._call_json(
            system_prompt=_INTERRUPTION_SYSTEM_PROMPT,
            payload={
                "already_sent": already_sent,
                "unsent": unsent,
                "new_messages": new_messages,
            },
            user_id=user_id,
            group_id=group_id,
        )
        if raw is None:
            return fallback
        action = str(raw.get("action", "continue") or "continue").strip()
        if action not in {"continue", "abort_unsent", "revise"}:
            _L.warning("arbiter interruption invalid action | action={!r}", action)
            return fallback
        return InterruptionResult(
            action=cast(Literal["continue", "abort_unsent", "revise"], action),
            reason=str(raw.get("reason", "") or ""),
            fallback=False,
        )

    async def judge_correction(
        self,
        *,
        bot_reply: str,
        new_message: str,
        user_id: str = "",
        group_id: str | None = None,
    ) -> CorrectionResult:
        fallback = CorrectionResult(needs_correction=False, fallback=True)
        if not self._enabled_for_group(group_id) or not bool(self._config.correction_enabled):
            return fallback
        raw = await self._call_json(
            system_prompt=_CORRECTION_SYSTEM_PROMPT,
            payload={
                "bot_reply": bot_reply,
                "new_message": new_message,
            },
            user_id=user_id,
            group_id=group_id,
        )
        if raw is None:
            return fallback
        correction_type = raw.get("correction_type")
        if correction_type is not None:
            correction_type = str(correction_type or "").strip() or None
        if correction_type not in {None, "retract", "amend", "acknowledge"}:
            _L.warning("arbiter correction invalid type | type={!r}", correction_type)
            return fallback
        return CorrectionResult(
            needs_correction=bool(raw.get("needs_correction", False)),
            correction_type=cast(Literal["retract", "amend", "acknowledge"] | None, correction_type),
            fallback=False,
        )

    def _enabled_for_group(self, group_id: str | None) -> bool:
        if not bool(self._config.enabled):
            return False
        groups = {str(gid).strip() for gid in (self._config.runtime_groups or []) if str(gid).strip()}
        return not groups or str(group_id or "").strip() in groups

    async def _call_json(
        self,
        *,
        system_prompt: str,
        payload: dict[str, Any],
        user_id: str,
        group_id: str | None,
    ) -> dict[str, Any] | None:
        body, headers, provider = self._build_request(system_prompt=system_prompt, payload=payload)
        elapsed_s = 0.0
        error: str | None = None
        try:
            start = time.monotonic()
            async with (
                asyncio.timeout(max(0.05, float(self._config.timeout_ms) / 1000.0)),
                self._session.post(provider.request_url(), json=body, headers=headers) as resp,
            ):
                resp.raise_for_status()
                response_payload = await resp.json()
            elapsed_s = time.monotonic() - start
            text = _extract_response_text(response_payload)
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("arbiter response is not a JSON object")
            await self._record_usage(
                user_id=user_id,
                group_id=group_id,
                input_tokens=int(_usage_value(response_payload, "prompt_tokens")),
                output_tokens=int(_usage_value(response_payload, "completion_tokens")),
                elapsed_s=elapsed_s,
                error=None,
            )
            return data
        except TimeoutError:
            error = f"timeout>{self._config.timeout_ms}ms"
        except json.JSONDecodeError as exc:
            error = f"invalid_json:{exc}"
        except aiohttp.ClientError as exc:
            error = f"client_error:{exc}"
        except Exception as exc:
            error = str(exc)
        if elapsed_s <= 0.0:
            elapsed_s = 0.0
        _L.warning("arbiter fallback | group={} user={} error={}", group_id, user_id, error)
        await self._record_usage(
            user_id=user_id,
            group_id=group_id,
            input_tokens=0,
            output_tokens=0,
            elapsed_s=elapsed_s,
            error=error,
        )
        return None

    def _build_request(
        self,
        *,
        system_prompt: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str], Any]:
        provider = create_provider("deepseek", self._config.resolved_api_base, self._config.resolved_api_key)
        body, headers, _request_meta = provider.build_request(
            system_blocks=[{"type": "text", "text": system_prompt}],
            messages=[{"role": "user", "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}],
            tools=None,
            max_tokens=_MAX_TOKENS,
            model=self._config.resolved_model,
            thinking={"type": "disabled"},
            request_options={},
        )
        body["stream"] = False
        body.pop("stream_options", None)
        return body, headers, provider

    async def _record_usage(
        self,
        *,
        user_id: str,
        group_id: str | None,
        input_tokens: int,
        output_tokens: int,
        elapsed_s: float,
        error: str | None,
    ) -> None:
        tracker = self._usage_tracker
        if tracker is None:
            return
        await tracker.record(
            call_type="arbiter",
            user_id=user_id or None,
            group_id=group_id,
            model=self._config.resolved_model or "arbiter",
            provider_kind="deepseek",
            input_tokens=input_tokens,
            cache_read_tokens=0,
            cache_create_tokens=0,
            output_tokens=output_tokens,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=input_tokens,
            reasoning_replay_tokens=0,
            tool_rounds=0,
            elapsed_s=elapsed_s,
            error=error,
        )


def _usage_value(payload: dict[str, Any], key: str) -> int:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0
    raw = usage.get(key, 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _extract_response_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("arbiter response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("arbiter response choice invalid")
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts).strip()
    text = first.get("text")
    if isinstance(text, str):
        return text.strip()
    raise ValueError("arbiter response missing text content")


def _clamp_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0
