from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from loguru import logger
from nonebot.adapters.onebot.v11 import NoticeEvent, PokeNotifyEvent

from kernel.types import PluginContext, TriggerContext
from services.humanization.emoji_sentiment import classify_reaction_sentiment

_POKE_INBOUND_WINDOW_S = 60.0
_POKE_INBOUND_THRESHOLD = 5
_POKE_INBOUND_MUTE_S = 60.0
_POKE_INBOUND_HISTORY: dict[tuple[str, str], list[float]] = {}
_POKE_INBOUND_MUTED_UNTIL: dict[tuple[str, str], float] = {}
_INTERACTION_FREQUENCY_HISTORY_LIMIT = 16
_MENTION_INBOUND_HISTORY: dict[tuple[str, str], list[float]] = {}


@dataclass(frozen=True)
class QQInteractionSignal:
    kind: str
    group_id: str
    actor_user_id: str
    target_user_id: str = ""
    raw_message_id: int | None = None
    emoji_code: str = ""
    is_tome: bool = False


@dataclass(frozen=True)
class PokeInboundRate:
    muted: bool = False
    poke_count: int = 0


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
    _MENTION_INBOUND_HISTORY.clear()


def _dialogue_climate_m1_enabled(ctx: PluginContext) -> bool:
    return bool(getattr(ctx, "dialogue_climate_m1_enabled", False))


def _frequency_key(group_id: str, actor_user_id: str) -> tuple[str, str]:
    return str(group_id), str(actor_user_id)


def _recent_timestamps(items: list[float], *, now: float, window_s: float) -> list[float]:
    return [ts for ts in items if now - ts < window_s]


def _trim_frequency_history(items: list[float]) -> list[float]:
    if len(items) <= _INTERACTION_FREQUENCY_HISTORY_LIMIT:
        return items
    return items[-_INTERACTION_FREQUENCY_HISTORY_LIMIT:]


def _record_mention_frequency(
    *,
    group_id: str,
    actor_user_id: str,
    now: float,
) -> int:
    key = _frequency_key(group_id, actor_user_id)
    history = _recent_timestamps(
        _MENTION_INBOUND_HISTORY.get(key, []),
        now=now,
        window_s=_POKE_INBOUND_WINDOW_S,
    )
    history.append(now)
    history = _trim_frequency_history(history)
    _MENTION_INBOUND_HISTORY[key] = history
    return len(history)


def _current_mention_frequency(
    *,
    group_id: str,
    actor_user_id: str,
    now: float,
) -> int:
    key = _frequency_key(group_id, actor_user_id)
    history = _recent_timestamps(
        _MENTION_INBOUND_HISTORY.get(key, []),
        now=now,
        window_s=_POKE_INBOUND_WINDOW_S,
    )
    if history:
        _MENTION_INBOUND_HISTORY[key] = _trim_frequency_history(history)
    else:
        _MENTION_INBOUND_HISTORY.pop(key, None)
    return len(history)


def _current_poke_frequency(
    *,
    group_id: str,
    actor_user_id: str,
    now: float,
) -> int:
    key = (str(group_id), str(actor_user_id))
    history = _recent_timestamps(
        _POKE_INBOUND_HISTORY.get(key, []),
        now=now,
        window_s=_POKE_INBOUND_WINDOW_S,
    )
    if history:
        _POKE_INBOUND_HISTORY[key] = _trim_frequency_history(history)
    else:
        _POKE_INBOUND_HISTORY.pop(key, None)
    return len(history)


def _register_m1_irritation_frequency(
    ctx: PluginContext,
    *,
    group_id: str,
    mention_count: int,
    poke_count: int,
) -> bool:
    if not _dialogue_climate_m1_enabled(ctx):
        return False
    mood_engine = getattr(ctx, "mood_engine", None)
    if mood_engine is None:
        return False
    try:
        from plugins.schedule.mood import register_m1_irritation_signal

        return register_m1_irritation_signal(
            mood_engine,
            mention_count=mention_count,
            poke_count=poke_count,
            group_id=group_id,
            session_id=f"group_{group_id}",
            m1_enabled=True,
        )
    except Exception as exc:
        logger.debug("qq interaction m1 irritation skipped | err={}", exc)
        return False


def register_m1_mention_irritation(
    ctx: PluginContext,
    *,
    group_id: str,
    actor_user_id: str,
    now: float | None = None,
) -> bool:
    """Record an explicit @bot mention into the M1 irritation frequency sensor."""
    current_time = time.time() if now is None else now
    if not _dialogue_climate_m1_enabled(ctx):
        return False
    mention_count = _record_mention_frequency(
        group_id=group_id,
        actor_user_id=actor_user_id,
        now=current_time,
    )
    poke_count = _current_poke_frequency(
        group_id=group_id,
        actor_user_id=actor_user_id,
        now=current_time,
    )
    return _register_m1_irritation_frequency(
        ctx,
        group_id=group_id,
        mention_count=mention_count,
        poke_count=poke_count,
    )


def _record_poke_inbound_rate(signal: QQInteractionSignal, *, now: float) -> PokeInboundRate:
    if signal.kind != "poke":
        return PokeInboundRate()
    key = (signal.group_id, signal.actor_user_id)
    muted_until = _POKE_INBOUND_MUTED_UNTIL.get(key, 0.0)
    if muted_until > now:
        return PokeInboundRate(muted=True, poke_count=_POKE_INBOUND_THRESHOLD)
    elif muted_until:
        _POKE_INBOUND_MUTED_UNTIL.pop(key, None)

    history = _recent_timestamps(
        _POKE_INBOUND_HISTORY.get(key, []),
        now=now,
        window_s=_POKE_INBOUND_WINDOW_S,
    )
    history.append(now)
    poke_count = len(history)
    if poke_count >= _POKE_INBOUND_THRESHOLD:
        _POKE_INBOUND_HISTORY[key] = []
        _POKE_INBOUND_MUTED_UNTIL[key] = now + _POKE_INBOUND_MUTE_S
        return PokeInboundRate(muted=True, poke_count=poke_count)
    _POKE_INBOUND_HISTORY[key] = history
    return PokeInboundRate(muted=False, poke_count=poke_count)


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


# Issue 17 Part 0: mood nudge magnitudes. Positive reaction → valence+,
# negative → valence-/tension+, poke → tension+. All ride the MoodEngine
# 30-min decay + 0.2 per-dimension cap, so they nudge rather than dominate.
_REACTION_POSITIVE_VALENCE = 0.10
_REACTION_NEGATIVE_VALENCE = 0.12
_REACTION_NEGATIVE_TENSION = 0.06
_POKE_TENSION = 0.04


def _apply_mood_nudge(
    ctx: PluginContext,
    signal: QQInteractionSignal,
    *,
    now: float,
    poke_count: int = 0,
) -> None:
    """Feed an inbound to-me interaction into the mood engine as a transient nudge.

    Reactions map through ``classify_reaction_sentiment``; pokes raise tension.
    Best-effort: a missing/erroring mood engine never blocks dispatch. Uses the
    same cache key as the group mood read (``group_{gid}``) so the nudge lands
    on the profile the reply path will evaluate.
    """
    mood_engine = getattr(ctx, "mood_engine", None)
    if mood_engine is None:
        return
    valence_d = 0.0
    tension_d = 0.0
    if signal.kind == "message_reaction":
        polarity, intensity = classify_reaction_sentiment(signal.emoji_code)
        if polarity == "positive":
            valence_d = intensity * _REACTION_POSITIVE_VALENCE
        elif polarity == "negative":
            valence_d = -intensity * _REACTION_NEGATIVE_VALENCE
            tension_d = intensity * _REACTION_NEGATIVE_TENSION
        else:  # neutral
            return
    elif signal.kind == "poke":
        if _dialogue_climate_m1_enabled(ctx):
            mention_count = _current_mention_frequency(
                group_id=signal.group_id,
                actor_user_id=signal.actor_user_id,
                now=now,
            )
            if _register_m1_irritation_frequency(
                ctx,
                group_id=signal.group_id,
                mention_count=mention_count,
                poke_count=max(0, int(poke_count or 0)),
            ):
                return
        tension_d = _POKE_TENSION
    else:
        return
    if not (valence_d or tension_d):
        return
    try:
        mood_engine.register_interaction_signal(
            valence_d=valence_d,
            tension_d=tension_d,
            group_id=signal.group_id,
            session_id=f"group_{signal.group_id}",
        )
    except Exception as exc:
        logger.debug("qq interaction mood nudge skipped | err={}", exc)


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

    # Mood nudge fires for any authorized to-me interaction, including pokes
    # that get rate-muted below: being poke-spammed is exactly when tension
    # should rise even though we suppress the reply. The 0.2 cap bounds it.
    poke_rate = _record_poke_inbound_rate(signal, now=current_time)

    _apply_mood_nudge(ctx, signal, now=current_time, poke_count=poke_rate.poke_count)

    if poke_rate.muted:
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
