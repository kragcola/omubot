from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from loguru import logger
from nonebot.adapters.onebot.v11 import NoticeEvent, PokeNotifyEvent

from kernel.types import PluginContext, TriggerContext

_POKE_INBOUND_WINDOW_S = 60.0
_POKE_INBOUND_THRESHOLD = 5
_POKE_INBOUND_MUTE_S = 60.0
_POKE_INBOUND_HISTORY: dict[tuple[str, str], list[float]] = {}
_POKE_INBOUND_MUTED_UNTIL: dict[tuple[str, str], float] = {}


@dataclass(frozen=True)
class QQInteractionSignal:
    kind: str
    group_id: str
    actor_user_id: str
    target_user_id: str = ""
    raw_message_id: int | None = None
    emoji_code: str = ""
    is_tome: bool = False


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_text_value(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _reaction_emoji_code(payload: dict[str, Any]) -> str:
    direct = _first_text_value(payload, ("emoji_code", "emoji_id", "face_id", "reaction_id"))
    if direct:
        return direct
    reactions = payload.get("current_reactions") or payload.get("reactions")
    if isinstance(reactions, list) and reactions:
        first = reactions[0]
        if isinstance(first, dict):
            return _first_text_value(first, ("emoji_code", "emoji_id", "face_id", "reaction_id"))
    return ""


def parse_qq_interaction_signal(event: NoticeEvent, *, self_id: str) -> QQInteractionSignal | None:
    if isinstance(event, PokeNotifyEvent):
        if event.group_id is None or str(event.user_id) == str(self_id):
            return None
        return QQInteractionSignal(
            kind="poke",
            group_id=str(event.group_id),
            actor_user_id=str(event.user_id),
            target_user_id=str(event.target_id),
            is_tome=event.is_tome(),
        )

    payload = event.model_dump()
    notice_type = str(payload.get("notice_type", "") or "")
    # The installed OneBot v11 adapter has no typed MessageReactionEvent;
    # NapCat reaction notices arrive as extra fields on generic NoticeEvent.
    if "reaction" not in notice_type and "emoji" not in notice_type:
        return None
    group_id = _first_text_value(payload, ("group_id",))
    actor_user_id = _first_text_value(payload, ("user_id", "operator_id", "actor_user_id"))
    if not group_id or not actor_user_id or actor_user_id == str(self_id):
        return None
    target_user_id = _first_text_value(payload, ("target_id", "message_sender_id", "sender_id"))
    return QQInteractionSignal(
        kind="message_reaction",
        group_id=group_id,
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        raw_message_id=_optional_int(payload.get("message_id") or payload.get("raw_message_id")),
        emoji_code=_reaction_emoji_code(payload),
        is_tome=target_user_id == str(self_id),
    )


def reset_qq_interaction_rate_guard() -> None:
    _POKE_INBOUND_HISTORY.clear()
    _POKE_INBOUND_MUTED_UNTIL.clear()


def _poke_inbound_muted(signal: QQInteractionSignal, *, now: float) -> bool:
    if signal.kind != "poke":
        return False
    key = (signal.group_id, signal.actor_user_id)
    muted_until = _POKE_INBOUND_MUTED_UNTIL.get(key, 0.0)
    if muted_until > now:
        return True
    if muted_until:
        _POKE_INBOUND_MUTED_UNTIL.pop(key, None)

    history = [
        ts for ts in _POKE_INBOUND_HISTORY.get(key, [])
        if now - ts < _POKE_INBOUND_WINDOW_S
    ]
    history.append(now)
    if len(history) >= _POKE_INBOUND_THRESHOLD:
        _POKE_INBOUND_HISTORY[key] = []
        _POKE_INBOUND_MUTED_UNTIL[key] = now + _POKE_INBOUND_MUTE_S
        return True
    _POKE_INBOUND_HISTORY[key] = history
    return False


def _qq_interaction_enabled(ctx: PluginContext, signal: QQInteractionSignal) -> bool:
    humanization = getattr(getattr(ctx, "config", None), "humanization", None)
    qq_interactions = getattr(humanization, "qq_interactions", None)
    field = (
        "poke_inbound_response_enabled"
        if signal.kind == "poke"
        else "reaction_inbound_response_enabled"
    )
    return bool(getattr(qq_interactions, field, False))


def _qq_interaction_reason(signal: QQInteractionSignal) -> str:
    if signal.kind == "poke":
        return "QQ 戳一戳"
    if signal.emoji_code:
        return f"QQ 表情回应 {signal.emoji_code}"
    return "QQ 表情回应"


def dispatch_qq_interaction_signal(
    ctx: PluginContext,
    signal: QQInteractionSignal,
    *,
    now: float | None = None,
) -> bool:
    if not signal.is_tome or not _qq_interaction_enabled(ctx, signal):
        return False

    group_config = getattr(getattr(ctx, "config", None), "group", None)
    if group_config is not None:
        try:
            resolved = group_config.resolve(int(signal.group_id))
        except Exception as exc:
            logger.debug("qq interaction group resolve failed | group={} err={}", signal.group_id, exc)
            return False
        if not getattr(resolved, "access_allowed", True) or getattr(resolved, "presence_mode", "active") != "active":
            return False
        blocked_users = getattr(resolved, "blocked_users", set()) or set()
        actor_int = _optional_int(signal.actor_user_id)
        if actor_int is not None and actor_int in blocked_users:
            return False

    scheduler = getattr(ctx, "scheduler", None)
    if scheduler is None or scheduler.is_muted(signal.group_id):
        return False

    current_time = time.time() if now is None else now
    if _poke_inbound_muted(signal, now=current_time):
        logger.info(
            "qq interaction muted | group={} user={} kind={}",
            signal.group_id, signal.actor_user_id, signal.kind,
        )
        return False

    reason = _qq_interaction_reason(signal)
    timeline = getattr(ctx, "timeline", None)
    if timeline is not None:
        timeline.add_pending_trigger(
            signal.group_id,
            reason=reason,
            message_id=signal.raw_message_id,
            target_user_id=signal.actor_user_id,
        )
    scheduler.notify(
        signal.group_id,
        trigger=TriggerContext(
            reason=reason,
            mode="qq_interaction",
            target_message_id=signal.raw_message_id,
            target_user_id=signal.actor_user_id,
            extra={
                "kind": signal.kind,
                "actor_user_id": signal.actor_user_id,
                "target_user_id": signal.target_user_id,
                "emoji_code": signal.emoji_code,
                "is_tome": signal.is_tome,
            },
        ),
        user_id=signal.actor_user_id,
    )
    return True
