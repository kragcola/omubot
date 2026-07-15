"""Single-attempt outbound delivery stage for the group scheduler."""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from nonebot.adapters.onebot.v11 import ActionFailed, Message


def _action_failed_payload(error: ActionFailed) -> dict[str, Any]:
    info = error.info
    if not isinstance(info, dict):
        return {}
    nested = info.get("info")
    if isinstance(nested, dict):
        return nested
    return info


def _action_failed_retcode(error: ActionFailed) -> int | None:
    retcode = getattr(error, "retcode", None)
    if isinstance(retcode, int):
        return retcode
    info = error.info if isinstance(error.info, dict) else {}
    for payload in (_action_failed_payload(error), info):
        for key in ("retcode", "code", "status"):
            raw = payload.get(key)
            if not isinstance(raw, (int, str)):
                continue
            try:
                return int(raw)
            except (TypeError, ValueError):
                continue
    return None


def _action_failed_wording(error: ActionFailed) -> str | None:
    info = error.info if isinstance(error.info, dict) else {}
    for payload in (_action_failed_payload(error), info):
        raw = payload.get("wording") or payload.get("message")
        if raw is not None:
            return str(raw)
    return None


class DeliveryStatus(StrEnum):
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class HumanizationContext:
    group_id: str
    register: Any = None
    slot: Any = None
    mood: Any = None
    climate: Any = None
    thinking_elapsed_s: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboundDeliveryRequest:
    group_id: str
    text: str
    humanize: str = "normal"
    target_user_id: str
    actor_id: str
    humanization: HumanizationContext


@dataclass(frozen=True, slots=True)
class OutboundDeliveryResult:
    status: DeliveryStatus
    elapsed_s: float
    message_id: int | None = None
    retcode: int | None = None
    wording: str | None = None
    pair_guard_recorded: bool = False


class RuntimeOutboundDelivery:
    def __init__(
        self,
        *,
        bot: Any,
        humanizer: Any = None,
        research_capture: Any = None,
        pair_guard: Any = None,
    ) -> None:
        self._bot = bot
        self._humanizer = humanizer
        self._research_capture = research_capture
        self._pair_guard = pair_guard

    async def deliver(self, request: OutboundDeliveryRequest) -> OutboundDeliveryResult:
        if not request.text.strip():
            return OutboundDeliveryResult(
                status=DeliveryStatus.SKIPPED,
                elapsed_s=0.0,
            )

        message = Message(request.text)
        reply_to_message_id: int | None = None
        at_targets: list[str] = []
        for segment in message:
            if segment.type == "reply" and reply_to_message_id is None:
                raw_reply_id = segment.data.get("id")
                if raw_reply_id is not None:
                    reply_to_message_id = int(raw_reply_id)
            elif segment.type == "at":
                target = str(segment.data.get("qq", "") or "")
                if target and target != "all":
                    at_targets.append(target)
        research_text = message.extract_plain_text().strip() or None

        started_at = time.monotonic()
        if self._humanizer is not None and request.humanize != "skip":
            context = request.humanization
            delay_kwargs = {
                "group_id": context.group_id,
                "register": context.register,
                "slot": context.slot,
                "mood": context.mood,
            }
            if context.climate is not None:
                delay_kwargs["climate"] = context.climate
            if context.thinking_elapsed_s is not None:
                delay_kwargs["thinking_elapsed_s"] = context.thinking_elapsed_s
            await self._humanizer.delay(request.text, **delay_kwargs)

        try:
            response = await self._bot.send_group_msg(
                group_id=int(request.group_id),
                message=Message(request.text),
            )
        except ActionFailed as exc:
            return OutboundDeliveryResult(
                status=DeliveryStatus.FAILED,
                elapsed_s=time.monotonic() - started_at,
                retcode=_action_failed_retcode(exc),
                wording=_action_failed_wording(exc),
            )
        elapsed_s = time.monotonic() - started_at
        raw_message_id = response.get("message_id") if isinstance(response, dict) else None
        message_id = int(raw_message_id) if raw_message_id is not None else None

        if self._research_capture is not None:
            with contextlib.suppress(Exception):
                self._research_capture.capture_outbound(
                    group_id=request.group_id,
                    actor_id=request.actor_id,
                    message_id=message_id,
                    reply_to_message_id=reply_to_message_id,
                    at_targets=tuple(at_targets),
                    text=research_text,
                    content_type="text",
                )

        pair_guard_recorded = False
        if self._pair_guard is not None:
            with contextlib.suppress(Exception):
                pair_guard_recorded = bool(
                    self._pair_guard.record_outbound(
                        request.group_id,
                        request.target_user_id,
                    )
                )

        return OutboundDeliveryResult(
            status=DeliveryStatus.SENT,
            elapsed_s=elapsed_s,
            message_id=message_id,
            pair_guard_recorded=pair_guard_recorded,
        )
