"""NoneBot event routing → PluginBus bridge.

Registers all NoneBot event handlers (on_message, on_notice, on_bot_connect)
and bridges them to PluginBus while accessing system services via PluginContext.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import aiohttp
from loguru import logger as _base_logger
from nonebot import get_driver, on_message, on_notice
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupBanNoticeEvent,
    GroupMessageEvent,
    GroupRecallNoticeEvent,
    Message,
    MessageEvent,
    NoticeEvent,
)
from nonebot.rule import to_me

from kernel.bus import PluginBus
from kernel.types import (
    AddressingContext,
    Content,
    ContentBlock,
    ImageRefBlock,
    MessageContext,
    PluginContext,
    ReplyObligation,
    TextBlock,
)
from services.humanization import AFFECTION_FAMILIARITY_SLOT
from services.humanization.qq_interactions import (
    QQInteractionSignal,
    dispatch_qq_interaction_signal,
    parse_qq_interaction_signal,
    register_climate_mention_irritation,
)
from services.media.vision import classify_image_intent
from services.media.visual_evidence import (
    NEUTRAL_ANIMATED_PLACEHOLDER,
    NEUTRAL_IMAGE_PLACEHOLDER,
    NEUTRAL_QUOTED_IMAGE_PLACEHOLDER,
    StickerEvidence,
    VisualEvidence,
    attach_visual_sidechannel,
)
from services.name_registry import NameVariationRegistry
from services.onebot_segments import RichRenderLimits, render_onebot_segments
from services.private_conversation import (
    get_private_conversation_actor,
    log_private_transition,
)
from services.system_module import Scope
from services.upstream_filter import should_drop_message

logger = _base_logger
_log_msg_in = _base_logger.bind(channel="message_in")
_log_msg_out = _base_logger.bind(channel="message_out")
_log_system = _base_logger.bind(channel="system")
_log_debug = _base_logger.bind(channel="debug")
_log_reply_workflow = _base_logger.bind(channel="reply_workflow")

_REPLY_PREVIEW_MAX = 50
_REPLY_PREVIEW_MAX_SELF = 200
_REPLY_PREVIEW_MAX_VISUAL = 500
_DIRECTED_FOLLOWUP_RE = re.compile(
    r"^(我也?)?(能|可以|可不可以|能不能)(来|去|参加|一起|加入|玩)(吗|嘛|么)?[。.!！?？~～\s]*$"
    r"|^(我也?)?可以(吗|嘛|么)?[。.!！?？~～\s]*$"
    r"|^(带上?我(吗|嘛|么)?|算我一个|我也想(来|去|参加|一起|加入|玩))[。.!！?？~～\s]*$"
)
_DIRECTED_FOLLOWUP_WINDOW_S = 180.0


async def _invalidate_social_narrative_recall(
    ctx: PluginContext,
    event: NoticeEvent,
) -> None:
    """Invalidate factual projections only for a real group recall notice."""
    if not isinstance(event, GroupRecallNoticeEvent):
        return
    store = getattr(ctx, "social_narrative_store", None)
    invalidate = getattr(store, "invalidate_evidence", None)
    if not callable(invalidate):
        return
    try:
        await cast(Any, invalidate)(
            group_id=str(event.group_id),
            evidence_message_id=event.message_id,
        )
    except Exception as exc:
        _log_system.warning(
            "social narrative recall invalidation failed | group={} message={} err={}",
            event.group_id,
            event.message_id,
            exc,
        )
_U13_TRACE_KEY_PREFIX = "u13_double_haiku"


# ============================================================================
# Utility functions
# ============================================================================


def _session_id(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"group_{event.group_id}"
    return f"private_{event.user_id}"


def _content_to_text(content: Content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and "text" in block
    )


_SEMANTIC_TEXT_RE = re.compile(r"[0-9A-Za-z㐀-鿿぀-ヿ가-힯]")


def _has_semantic_text(text: str) -> bool:
    return bool(_SEMANTIC_TEXT_RE.search(text or ""))


def _nickname_stripped_to_empty_payload(addressing: AddressingContext) -> bool:
    """True when NoneBot stripped a nickname and only punctuation/space remains."""
    return (
        addressing.evidence == "nickname_original"
        and bool(addressing.matched_nickname)
        and not _has_semantic_text(addressing.stripped_text)
    )


def _content_has_non_text_blocks(content: Content) -> bool:
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") != "text"
        for block in content
    )


def _is_nickname_only_call(addressing: AddressingContext, content: Content) -> bool:
    if not _nickname_stripped_to_empty_payload(addressing):
        return False
    if _content_has_non_text_blocks(content):
        return False
    return not _has_semantic_text(_content_to_text(content))


def _semantic_plain_text_for_addressing(addressing: AddressingContext, plain_text: str) -> str:
    if _nickname_stripped_to_empty_payload(addressing):
        return addressing.original_text.strip() or plain_text
    return plain_text


def _extract_group_command_text(
    msg: Message,
    self_id: str,
    bot_nicknames: list[str] | tuple[str, ...] | None = None,
) -> str | None:
    """Return a command text only when the command is for the bot or naked."""
    saw_bot_at = False
    text_parts: list[str] = []

    for seg in msg:
        if seg.type == "reply":
            continue
        if seg.type == "at":
            qq = str(seg.data.get("qq", ""))
            if qq == str(self_id):
                saw_bot_at = True
                continue
            return None
        if seg.type == "text":
            text = str(seg.data.get("text", ""))
            if not text_parts and not saw_bot_at and not text.strip():
                text_parts.append(text)
                continue
            if (
                not text_parts
                and not saw_bot_at
                and not text.lstrip().startswith("/")
                and not bot_nicknames
            ):
                return None
            text_parts.append(text)
            continue
        # Images and other rich segments may appear before or after the text
        # command. They remain available on the original event for handlers.
        continue

    candidate = "".join(text_parts).strip()
    if not candidate.startswith("/"):
        if saw_bot_at:
            return None
        slash_index = candidate.find("/")
        if slash_index <= 0:
            return None
        prefix = candidate[:slash_index].strip()
        if not _is_textual_bot_mention_prefix(prefix, str(self_id), bot_nicknames or ()):
            return None
        candidate = candidate[slash_index:].strip()
    return candidate


def _is_textual_bot_mention_prefix(
    prefix: str,
    self_id: str,
    bot_nicknames: list[str] | tuple[str, ...],
) -> bool:
    normalized = prefix.strip()
    match = re.match(r"^@(?P<name>.+?)\s*\((?P<qq>\d+)\)\s*$", normalized)
    if match is not None and match.group("qq") != str(self_id):
        return False
    return _is_bot_nickname_prefix(normalized, bot_nicknames)


def _is_bot_nickname_prefix(prefix: str, bot_nicknames: list[str] | tuple[str, ...]) -> bool:
    normalized = prefix.lstrip("@").strip()
    normalized = re.sub(r"\s*\(\d+\)\s*$", "", normalized).strip()
    if not normalized:
        return False
    for nickname in bot_nicknames:
        nick = str(nickname).strip()
        if nick and re.match(rf"^{re.escape(nick)}(?=$|[\s,，。.!！?？:：、~～…])", normalized, re.IGNORECASE):
            return True
    return False


def _is_directed_followup_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if len(compact) > 24:
        return False
    return bool(_DIRECTED_FOLLOWUP_RE.match(compact))


def _message_has_other_at(msg: Message, self_id: str) -> bool:
    for seg in msg:
        if seg.type != "at":
            continue
        qq = str(seg.data.get("qq", ""))
        if qq and qq != str(self_id):
            return True
    return False


def _match_nickname_addressing(
    event: MessageEvent,
    bot_nicknames: list[str] | tuple[str, ...],
) -> str | None:
    """If the user addressed the bot by text nickname (not @), return the
    matched name.

    NoneBot's ``_check_nickname`` strips the matched nickname from
    ``event.message`` and sets ``to_me=True``, but the strip loses the only
    signal that tells the LLM *how* it was addressed — it sees "怎么是龙王"
    instead of "姆 怎么是龙王" and has no idea the user called it "姆".
    ``event.original_message`` is a deep copy made before any stripping
    (``model_validator("before")`` in ``MessageEvent.__init__``), so we can
    recover the matched nickname from it.

    Returns ``None`` when the user was NOT addressed by text nickname (e.g.
    pure @-mention, or the message didn't start with a bot name).
    """
    if not bot_nicknames:
        return None
    original = getattr(event, "original_message", None)
    if original is None or not original:
        return None
    first_seg = original[0]  # type: ignore[index]
    if getattr(first_seg, "type", "") != "text":
        return None
    first_text = str(first_seg.data.get("text", "") or "")
    if not first_text:
        return None
    # Replicate _check_nickname's matching, but keep common CJK/Latin vocative
    # punctuation too: ``emu。`` is a direct call even after NoneBot strips it to
    # just ``。`` for downstream handlers.
    regex = "|".join(
        re.escape(str(nick).strip())
        for nick in bot_nicknames
        if str(nick).strip()
    )
    if not regex:
        return None
    m = re.search(rf"^({regex})(?=$|[\s,，。.!！?？:：、~～…])", first_text, re.IGNORECASE)
    return str(m.group(1)) if m else None


def _message_ats_self(msg: Message, self_id: str) -> bool:
    """True if the message @-mentions the bot in ANY segment position.

    NoneBot's ``event.is_tome()`` only fires when the @ is the first or last
    segment (see onebot v11 ``_check_at_me``). A sandwiched @ — e.g.
    ``[image][at:bot]这是谁`` — leaves ``is_tome()`` False even though the user
    clearly addressed the bot. ``_extract_topic_block_signals`` already scans
    every segment for ``at_self``, so without this the two @-detection paths
    disagree: the message gets ``role=addressed`` (via at_self) yet falls into
    the probabilistic gray zone and can be silently skipped by the RWS roll.
    """
    if not self_id:
        return False
    return any(
        seg.type == "at" and str(seg.data.get("qq", "")) == str(self_id)
        for seg in msg
    )


def _reply_targets_bot(reply: object | None, self_id: str) -> bool:
    sender = getattr(reply, "sender", None)
    if sender is None:
        return False
    return str(getattr(sender, "user_id", "") or "") == str(self_id)


def _extract_topic_block_signals(
    event: object | None, self_id: str,
) -> dict[str, Any]:
    """Pull reply-to / @-mention structure for B1 topic-block attribution.

    All keys default empty so callers without an event (e.g. coalesced
    flush) pass nothing extra and the tracker simply uses time/text.
    """
    if event is None:
        return {}
    reply = getattr(event, "reply", None)
    reply_sender_id = str(getattr(getattr(reply, "sender", None), "user_id", "") or "")
    reply_to_message_id = getattr(reply, "message_id", None) if reply is not None else None
    at_targets: list[str] = []
    at_self = False
    try:
        for seg in event.get_message():  # type: ignore[attr-defined]
            if getattr(seg, "type", "") == "at":
                qq = str(seg.data.get("qq", "") or "")
                if qq == "all":
                    continue
                if qq == str(self_id):
                    at_self = True
                elif qq:
                    at_targets.append(qq)
    except Exception:
        pass
    return {
        "message_id": getattr(event, "message_id", None),
        "reply_to_sender_id": "" if reply_sender_id == str(self_id) else reply_sender_id,
        "reply_to_message_id": reply_to_message_id,
        "reply_to_self": _reply_targets_bot(reply, self_id),
        "at_targets": tuple(at_targets),
        "at_self": at_self,
    }


def _capture_research_group_event(ctx: PluginContext, bot: Bot, event: Any) -> None:
    policy = getattr(getattr(ctx, "config", None), "research_event_capture", None)
    group_id = str(getattr(event, "group_id", "") or "")
    if policy is None or not policy.allows_group(group_id):
        return
    capture = getattr(ctx, "research_event_capture", None)
    if capture is None:
        return

    message = getattr(event, "original_message", None) or getattr(event, "message", None)
    at_targets: list[str] = []
    has_image = False
    try:
        for segment in message or ():
            if segment.type == "image":
                has_image = True
            if segment.type != "at":
                continue
            target = str(segment.data.get("qq", "") or "")
            if target and target not in {"all", str(getattr(bot, "self_id", ""))}:
                at_targets.append(target)
    except Exception:
        pass

    try:
        if message is not None and hasattr(message, "extract_plain_text"):
            plain_text = str(message.extract_plain_text()).strip()
        else:
            plain_text = str(event.get_plaintext()).strip()
    except Exception:
        plain_text = str(getattr(event, "raw_message", "") or "").strip()
    reply = getattr(event, "reply", None)
    reply_id = getattr(reply, "message_id", None) if reply is not None else None
    try:
        event_time = datetime.fromtimestamp(float(event.time), tz=UTC)
    except (AttributeError, TypeError, ValueError, OSError):
        event_time = datetime.now(UTC)
    try:
        capture.capture_inbound(
            group_id=group_id,
            actor_id=str(getattr(event, "user_id", "") or ""),
            message_id=int(event.message_id),
            reply_to_message_id=int(reply_id) if reply_id is not None else None,
            at_targets=tuple(at_targets),
            text=plain_text or None,
            content_type=(
                "mixed"
                if plain_text and has_image
                else ("text" if plain_text else ("image" if has_image else "unknown"))
            ),
            event_time=event_time,
        )
    except Exception as exc:
        _log_debug.debug("research event capture skipped | group={} err={}", group_id, exc)


def _original_plaintext(event: MessageEvent) -> str:
    original = getattr(event, "original_message", None)
    if original is None:
        return event.get_plaintext()
    try:
        return original.extract_plain_text().strip()
    except Exception:
        return event.get_plaintext()


def _original_segments(event: MessageEvent, fallback: Message) -> Message:
    """Pre-strip message segments for echo repetition.

    NoneBot strips a matched nickname prefix from ``event.message`` (so a
    vocative like ``姆。`` becomes just ``。`` for downstream handlers). The echo
    plugin must repeat what the user actually typed, so it needs the original,
    un-stripped segments. ``event.original_message`` is the deep copy made
    before stripping; fall back to the stripped message when it is unavailable.
    """
    original = getattr(event, "original_message", None)
    if original is None or not original:
        return fallback
    return cast(Message, original)


def _resolve_addressing_context(
    event: MessageEvent,
    msg: Message,
    *,
    self_id: str,
    bot_nicknames: list[str] | tuple[str, ...],
    is_addressed: bool,
) -> AddressingContext:
    reply_sender_id = str(getattr(getattr(event.reply, "sender", None), "user_id", "") or "")
    at_targets: list[str] = []
    at_self = False
    for seg in msg:
        if seg.type != "at":
            continue
        qq = str(seg.data.get("qq", "") or "")
        if not qq or qq == "all":
            continue
        if qq == str(self_id):
            at_self = True
        else:
            at_targets.append(qq)

    original_text = _original_plaintext(event)
    stripped_text = event.get_plaintext()
    matched_nickname = _match_nickname_addressing(event, bot_nicknames)
    if at_self:
        return AddressingContext(
            addressed=True,
            target="self",
            confidence=1.0,
            evidence="at_self",
            original_text=original_text,
            stripped_text=stripped_text,
            matched_nickname=matched_nickname or "",
            at_targets=tuple(at_targets),
            reply_sender_id=reply_sender_id,
        )
    if matched_nickname:
        return AddressingContext(
            addressed=True,
            target="self",
            confidence=1.0,
            evidence="nickname_original",
            original_text=original_text,
            stripped_text=stripped_text,
            matched_nickname=matched_nickname,
            at_targets=tuple(at_targets),
            reply_sender_id=reply_sender_id,
        )
    if reply_sender_id and reply_sender_id == str(self_id):
        return AddressingContext(
            addressed=True,
            target="self",
            confidence=0.9,
            evidence="reply_to_self",
            original_text=original_text,
            stripped_text=stripped_text,
            matched_nickname="",
            at_targets=tuple(at_targets),
            reply_sender_id=reply_sender_id,
        )
    if is_addressed:
        target = "ambiguous" if at_targets else "self"
        return AddressingContext(
            addressed=True,
            target=target,
            confidence=0.72 if target == "ambiguous" else 0.8,
            evidence="adapter_fallback",
            original_text=original_text,
            stripped_text=stripped_text,
            matched_nickname="",
            at_targets=tuple(at_targets),
            reply_sender_id=reply_sender_id,
        )
    return AddressingContext(
        addressed=False,
        target="none",
        confidence=0.0,
        evidence="none",
        original_text=original_text,
        stripped_text=stripped_text,
        matched_nickname="",
        at_targets=tuple(at_targets),
        reply_sender_id=reply_sender_id,
    )


# Evidence kinds that count as the user explicitly addressing the bot for the
# purpose of M1 irritation: a protocol @self, or a head-of-message text
# nickname with a vocative boundary ("emu。", "笑梦").  Both are deliberate
# cues at the same strength; a mid-sentence character mention or a sticker
# caption resolves to other evidence (or none) and is excluded, so nickname
# spam moves tension while talking *about* the character does not.
_M1_MENTION_EVIDENCE = frozenset({"at_self", "nickname_original"})


def _addressing_triggers_climate_mention(addressing: AddressingContext) -> bool:
    return addressing.target == "self" and addressing.evidence in _M1_MENTION_EVIDENCE


async def _at_trigger_targets_self(
    *,
    rendered_message: str,
    plain_text: str,
    reply_sender_id: str,
    self_id: str,
    bot_nicknames: list[str] | tuple[str, ...],
    addressed_fallback: bool,
) -> bool:
    if not self_id:
        return addressed_fallback
    from services.group import AddresseeDetector

    detector = AddresseeDetector(bot_ids=(str(self_id),), bot_names=bot_nicknames)
    result = await detector.detect({
        "message": rendered_message,
        "text": plain_text,
        "reply_sender_id": reply_sender_id,
    })
    if str(result.target_id or "") == str(self_id):
        return True
    if result.target_id:
        return False
    return addressed_fallback


def _runtime_state_value(runtime_state: object | None, slot_id: str, scope: Scope) -> Any:
    if runtime_state is None:
        return None
    try:
        snapshot = cast(Any, runtime_state).get(slot_id, scope=scope)
    except Exception as exc:
        _log_reply_workflow.debug("runtime_state read failed | slot={} err={}", slot_id, exc)
        return None
    return snapshot.value if snapshot is not None else None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _semantic_gate_familiarity(ctx: PluginContext, user_id: str) -> float | None:
    bus = getattr(ctx, "bus", None)
    get_plugin = getattr(bus, "get_plugin", None)
    if callable(get_plugin):
        owner = get_plugin("affection")
        affection_enabled = (
            bool(getattr(owner, "enabled", False))
            if owner is not None
            else bool(getattr(ctx, "affection_enabled", False))
        )
        if not affection_enabled:
            return None
    elif not bool(getattr(ctx, "affection_enabled", False)):
        return None
    value = _runtime_state_value(
        getattr(ctx, "runtime_state", None),
        AFFECTION_FAMILIARITY_SLOT,
        Scope(user_id=str(user_id)),
    )
    raw: object | None = value.get("familiarity") if isinstance(value, dict) else getattr(value, "familiarity", None)
    return _optional_float(raw)


def _semantic_gate_mood_energy(ctx: PluginContext, group_id: str) -> float | None:
    mood_engine = getattr(ctx, "mood_engine", None)
    if mood_engine is None:
        return None
    schedule_store = getattr(ctx, "schedule_store", None)
    schedule = getattr(schedule_store, "current", None) if schedule_store is not None else None
    recent_count = 0
    timeline = getattr(ctx, "timeline", None)
    if timeline is not None:
        try:
            recent_count = int(timeline.recent_interaction_count(str(group_id), window_s=60.0))
        except Exception as exc:
            _log_reply_workflow.debug("semantic gate timeline read failed | group={} err={}", group_id, exc)
            recent_count = 0
    try:
        profile = mood_engine.evaluate(
            schedule,
            recent_interaction_count=recent_count,
            group_id=group_id,
            session_id=f"group_{group_id}",
        )
    except Exception as exc:
        _log_reply_workflow.debug("semantic gate mood read failed | group={} err={}", group_id, exc)
        return None
    return _optional_float(cast(Any, profile).energy)


def _u13_trace_request_id(group_id: str, message_id: int | str) -> str:
    return f"{_U13_TRACE_KEY_PREFIX}:group_{group_id}:{message_id}:{secrets.token_hex(3)}"


async def _record_runtime_metric(
    ctx: PluginContext,
    *,
    metric_key: str,
    group_id: str,
    amount: int = 1,
    metadata: dict[str, Any] | None = None,
) -> None:
    store = getattr(ctx, "block_trace_store", None)
    if store is None or not hasattr(store, "record_runtime_metric"):
        return
    try:
        await store.record_runtime_metric(
            metric_key=metric_key,
            group_id=group_id,
            amount=amount,
            metadata=metadata,
        )
    except Exception as exc:
        _log_debug.debug(
            "router runtime metric skipped | key={} group={} err={}",
            metric_key,
            group_id,
            exc,
        )


def _bot_pair_guard_enabled(ctx: PluginContext) -> bool:
    config = getattr(ctx, "config", None)
    pair_guard = getattr(config, "bot_pair_guard", None) if config is not None else None
    return bool(getattr(pair_guard, "enabled", False))


async def _maybe_drop_pair_guard(
    ctx: PluginContext,
    *,
    group_id: str,
    sender_id: str,
) -> bool:
    if not _bot_pair_guard_enabled(ctx):
        return False
    guard = getattr(ctx, "bot_pair_guard", None)
    if guard is None:
        return False
    try:
        if guard.is_suppressed(group_id, sender_id):
            await _record_runtime_metric(
                ctx,
                metric_key="pair_guard_suppressed",
                group_id=group_id,
                metadata={"sender_id": sender_id},
            )
            return True
        if guard.record_inbound(group_id, sender_id):
            await _record_runtime_metric(
                ctx,
                metric_key="pair_guard_inbound_recorded",
                group_id=group_id,
                metadata={"sender_id": sender_id},
            )
    except Exception as exc:
        _log_debug.debug(
            "pair guard skipped | group={} sender={} err={}",
            group_id,
            sender_id,
            exc,
        )
    return False


def _coalesce_enabled(ctx: PluginContext) -> bool:
    config = getattr(ctx, "config", None)
    coalesce = getattr(config, "coalesce", None) if config is not None else None
    return bool(getattr(coalesce, "enabled", False))


def _should_bypass_coalescer(
    *,
    trigger: object | None,
    is_addressed: bool,
) -> bool:
    return is_addressed or trigger is not None


async def _notify_group_scheduler(
    ctx: PluginContext,
    *,
    group_id: str,
    user_id: str,
    trigger: object | None,
    is_addressed: bool,
    message: Any,
    event: object | None = None,
    self_id: str = "",
) -> None:
    scheduler = getattr(ctx, "scheduler", None)
    if scheduler is None:
        return
    message_text = _content_to_text(message) if message is not None else ""
    tb = _extract_topic_block_signals(event, self_id)
    tb["is_addressed"] = is_addressed
    coalescer = getattr(ctx, "message_coalescer", None)
    bypass = _should_bypass_coalescer(trigger=trigger, is_addressed=is_addressed)
    if not _coalesce_enabled(ctx) or coalescer is None:
        scheduler.notify(
            group_id,
            trigger=cast(Any, trigger),
            user_id=user_id,
            message_text=message_text,
            **tb,
        )
        return

    if bypass:
        dropped_count = 0
        try:
            dropped_count = len(await coalescer.discard(group_id, user_id))
        except Exception as exc:
            _log_debug.debug(
                "coalescer discard skipped | group={} user={} err={}",
                group_id,
                user_id,
                exc,
            )
        await _record_runtime_metric(
            ctx,
            metric_key="coalesce_bypassed",
            group_id=group_id,
            metadata={
                "sender_id": user_id,
                "trigger_mode": str(getattr(trigger, "mode", "") or ""),
                "discarded_messages": dropped_count,
            },
        )
        scheduler.notify(
            group_id,
            trigger=cast(Any, trigger),
            user_id=user_id,
            message_text=message_text,
            **tb,
        )
        return

    async def _flush(messages: list[Any]) -> None:
        await _record_runtime_metric(
            ctx,
            metric_key="coalesce_flushed",
            group_id=group_id,
            metadata={"sender_id": user_id, "message_count": len(messages)},
        )
        merged_text = " ".join(_content_to_text(item) for item in messages if item is not None).strip()
        scheduler.notify(group_id, user_id=user_id, message_text=merged_text)

    await coalescer.enqueue(
        group_id,
        user_id,
        message,
        on_flush=_flush,
    )
    await _record_runtime_metric(
        ctx,
        metric_key="coalesce_enqueued",
        group_id=group_id,
        metadata={
            "sender_id": user_id,
            "message_type": "content" if message else "empty",
        },
    )


def _has_recent_assistant_reply(timeline: Any, group_id: str, *, within_s: float) -> bool:
    try:
        turns = timeline.get_turns(group_id)
    except Exception:
        return False
    now = time.time()
    for idx in range(len(turns) - 1, -1, -1):
        turn = turns[idx]
        if turn.get("role") != "assistant":
            continue
        try:
            turn_time = float(timeline.get_turn_time(group_id, idx))
        except Exception:
            return False
        return turn_time > 0 and now - turn_time <= within_s
    return False


def _latest_assistant_reply_info(
    timeline: Any,
    group_id: str,
    *,
    within_s: float,
) -> tuple[bool, str, float | None]:
    try:
        turns = timeline.get_turns(group_id)
    except Exception:
        return False, "", None
    now = time.time()
    for idx in range(len(turns) - 1, -1, -1):
        turn = turns[idx]
        if turn.get("role") != "assistant":
            continue
        try:
            turn_time = float(timeline.get_turn_time(group_id, idx))
        except Exception:
            return False, "", None
        elapsed = now - turn_time
        if turn_time <= 0 or elapsed > within_s:
            return False, "", elapsed
        return True, _content_to_text(turn.get("content", "")), elapsed
    return False, "", None


def _last_assistant_replied_to_user(
    timeline: Any,
    group_id: str,
    user_id: str,
    *,
    within_s: float,
) -> bool:
    """Return whether the latest assistant turn answered only the current user."""
    try:
        turns = timeline.get_turns(group_id)
    except Exception:
        return False
    now = time.time()
    current_user_suffix = f"({user_id})"
    for idx in range(len(turns) - 1, -1, -1):
        turn = turns[idx]
        if turn.get("role") != "assistant":
            continue
        try:
            turn_time = float(timeline.get_turn_time(group_id, idx))
        except Exception:
            return False
        if turn_time <= 0 or now - turn_time > within_s:
            return False
        # Walk backwards past consecutive assistant turns (pause_then_extend)
        prev_idx = idx - 1
        while prev_idx >= 0 and turns[prev_idx].get("role") == "assistant":
            prev_idx -= 1
        if prev_idx < 0:
            return False
        previous = turns[prev_idx]
        if previous.get("role") != "user":
            return False
        content = str(previous.get("content", ""))
        active_user_lines = [
            line
            for line in content.splitlines()
            if (
                line.strip()
                and "已跳过，仅作历史背景" not in line
                and "触发原因:" not in line
            )
        ]
        if not active_user_lines:
            return False
        # timeline lazy-flush merges every user's messages during a bot-silence
        # window into ONE user turn, so requiring all() lines to belong to the
        # current user mis-fires whenever anyone else spoke in that gap. The last
        # real user line is the message that actually triggered the previous
        # assistant reply — check only its ownership.
        return current_user_suffix in active_user_lines[-1]
    return False


_FORWARD_MAX_DEPTH = 3
_FORWARD_MAX_NODES = 100
_FORWARD_MAX_SEGMENTS = 1000
_FORWARD_MAX_CHARS = 2000
_FORWARD_HEADER = "«合并转发消息»\n"
_FORWARD_TRUNCATED = "«嵌套转发（内容已截断）»"
_FORWARD_CYCLE = "«嵌套转发（循环或重复，已跳过）»"
_FORWARD_UNAVAILABLE = "«嵌套转发（无法获取内容）»"
_FORWARD_EMPTY = "«嵌套转发（空）»"


class _ForwardApiBot(Protocol):
    async def call_api(self, api: str, **data: Any) -> Any: ...


@dataclass(slots=True)
class _ForwardRenderState:
    visited_ids: set[str] = field(default_factory=set)
    lines: list[str] = field(default_factory=list)
    node_count: int = 0
    segment_count: int = 0
    body_chars: int = 0
    stopped: bool = False


def _append_forward_line(state: _ForwardRenderState, line: str) -> None:
    line = line.rstrip()
    if not line or state.stopped:
        return
    separator_len = 1 if state.lines else 0
    body_limit = _FORWARD_MAX_CHARS - len(_FORWARD_HEADER)
    if state.body_chars + separator_len + len(line) <= body_limit:
        state.lines.append(line)
        state.body_chars += separator_len + len(line)
        return
    _truncate_forward_output(state, pending_line=line)


def _truncate_forward_output(state: _ForwardRenderState, *, pending_line: str = "") -> None:
    if state.stopped:
        return
    body_limit = _FORWARD_MAX_CHARS - len(_FORWARD_HEADER)
    current = "\n".join(state.lines)
    prefix_limit = max(0, body_limit - len(_FORWARD_TRUNCATED) - 1)
    if pending_line and len(current) < prefix_limit:
        separator = "\n" if current else ""
        remaining = max(0, prefix_limit - len(current) - len(separator))
        current = f"{current}{separator}{pending_line[:remaining]}"
    prefix = current[:prefix_limit].rstrip()
    body = f"{prefix}\n{_FORWARD_TRUNCATED}" if prefix else _FORWARD_TRUNCATED
    state.lines = body.splitlines()
    state.body_chars = len(body)
    state.stopped = True


def _forward_messages(result: object) -> object:
    if not isinstance(result, dict):
        return None
    data = result.get("data", result)
    if isinstance(data, dict):
        return data.get("messages", result.get("messages", []))
    return result.get("messages", [])


def _render_forward_segment_summary(seg_type: str, data: object) -> str:
    payload = data if isinstance(data, dict) else {}
    if seg_type == "text":
        return str(payload.get("text", ""))
    if seg_type == "image":
        # Forward-card summaries are user-visible text; never embed vision prose.
        return NEUTRAL_IMAGE_PLACEHOLDER
    if seg_type == "face":
        return "«表情»"
    if seg_type == "at":
        return f"@{payload.get('qq', '')}"
    if seg_type == "file":
        return f"«文件: {payload.get('name', '未知文件')}»"
    return f"«{seg_type or '未知'}»"


async def _render_nested_forward(
    data: object,
    bot: _ForwardApiBot,
    state: _ForwardRenderState,
    *,
    depth: int,
) -> None:
    if state.stopped:
        return
    if depth >= _FORWARD_MAX_DEPTH:
        _truncate_forward_output(state)
        return

    payload = data if isinstance(data, dict) else {}
    nested_id = str(payload.get("id", "") or "")
    if nested_id and nested_id in state.visited_ids:
        _append_forward_line(state, _FORWARD_CYCLE)
        return
    if nested_id:
        state.visited_ids.add(nested_id)

    if "content" in payload:
        messages = payload.get("content")
    elif nested_id:
        try:
            result = await bot.call_api("get_forward_msg", message_id=nested_id)
        except Exception:
            logger.warning("nested get_forward_msg API failed | id={}", nested_id)
            _append_forward_line(state, _FORWARD_UNAVAILABLE)
            return
        messages = _forward_messages(result)
    else:
        _append_forward_line(state, _FORWARD_UNAVAILABLE)
        return

    if not isinstance(messages, list) or not messages:
        _append_forward_line(state, _FORWARD_EMPTY)
        return
    await _render_forward_nodes(messages, bot, state, depth=depth + 1)


async def _render_forward_nodes(
    messages: list[object],
    bot: _ForwardApiBot,
    state: _ForwardRenderState,
    *,
    depth: int,
) -> None:
    for message in messages:
        if state.stopped:
            return
        if state.node_count >= _FORWARD_MAX_NODES:
            _truncate_forward_output(state)
            return
        if not isinstance(message, dict):
            continue
        state.node_count += 1

        sender = message.get("sender", {})
        if isinstance(sender, dict):
            uid = str(sender.get("user_id", ""))
            nick = str(sender.get("nickname", uid))
            label = f"{nick}({uid})"
        else:
            label = "未知"
        prefix = "  " * depth

        content = message.get("message", message.get("content", ""))
        if not isinstance(content, list):
            text = content.strip() if isinstance(content, str) else str(content)
            if text:
                _append_forward_line(state, f"{prefix}{label}: {text}")
            continue

        parts: list[str] = []
        part_chars = 0
        for segment in content:
            if state.stopped:
                return
            if state.segment_count >= _FORWARD_MAX_SEGMENTS:
                _truncate_forward_output(state)
                return
            state.segment_count += 1
            if not isinstance(segment, dict):
                continue
            seg_type = str(segment.get("type", ""))
            if seg_type != "forward":
                summary = _render_forward_segment_summary(seg_type, segment.get("data", {}))
                if not summary or (not parts and not summary.strip()):
                    continue
                body_limit = _FORWARD_MAX_CHARS - len(_FORWARD_HEADER)
                separator_len = 1 if state.lines else 0
                line_overhead = len(prefix) + len(label) + 2
                part_limit = max(0, body_limit - state.body_chars - separator_len - line_overhead)
                if part_chars + len(summary) > part_limit:
                    remaining = max(0, part_limit - part_chars)
                    if remaining:
                        parts.append(summary[:remaining])
                    preview = "".join(parts)
                    _truncate_forward_output(state, pending_line=f"{prefix}{label}: {preview}")
                    return
                parts.append(summary)
                part_chars += len(summary)
                continue

            text = "".join(parts).strip()
            if text:
                _append_forward_line(state, f"{prefix}{label}: {text}")
            parts.clear()
            part_chars = 0
            await _render_nested_forward(segment.get("data", {}), bot, state, depth=depth)

        text = "".join(parts).strip()
        if text:
            _append_forward_line(state, f"{prefix}{label}: {text}")


async def _render_forward_msg(forward_id: str, bot: _ForwardApiBot) -> str:
    normalized_id = str(forward_id)
    try:
        result = await bot.call_api("get_forward_msg", message_id=normalized_id)
    except Exception:
        logger.warning("get_forward_msg API failed | id={}", normalized_id)
        return "«合并转发消息（无法获取内容）»"

    messages = _forward_messages(result)
    if isinstance(messages, str):
        rendered = f"«合并转发消息: {messages[:200]}»"
        return rendered[:_FORWARD_MAX_CHARS]
    if not isinstance(messages, list) or not messages:
        return "«合并转发消息（空）»"

    state = _ForwardRenderState(visited_ids={normalized_id})
    await _render_forward_nodes(messages, bot, state, depth=0)
    if not state.lines:
        return "«合并转发消息（无文本内容）»"

    body = "\n".join(state.lines)
    logger.info(
        "forward_msg rendered | id={} nodes={} segments={} lines={} chars={} truncated={}",
        normalized_id,
        state.node_count,
        state.segment_count,
        len(state.lines),
        len(body),
        state.stopped,
    )
    return f"{_FORWARD_HEADER}{body}"


def _group_ingest_lock(ctx: PluginContext, group_id: str) -> asyncio.Lock:
    """Per-group ingest lock, lazily created and memoized on the context.

    The group listener runs with ``block=False`` so handlers for concurrent
    messages execute interleaved. Image messages stall their handler inside
    ``_render_message`` (download + recognition, up to a few seconds); a later
    text message can then overtake, commit to the timeline first, and fire a
    reply that doesn't yet see the image. Holding this lock across the
    render→timeline-commit section forces commits to follow message arrival
    order within a group.
    """
    locks = getattr(ctx, "group_ingest_locks", None)
    if locks is None:
        locks = {}
        ctx.group_ingest_locks = locks
    lock = locks.get(group_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[group_id] = lock
    return lock


async def _refetch_reply_image_url(bot: Bot, message_id: object) -> str | None:
    """Re-fetch a quoted message via get_msg and return its first image URL.

    Quoted-reply image segments frequently arrive with a stale/empty url; the
    authoritative message (with a fresh url) must be pulled by message_id. The
    OneBot get_msg payload's `message` is a list of raw `{type, data}` dicts
    (same shape parsed by _render_forward_msg), not a nonebot Message.
    """
    try:
        msg_data = await bot.get_msg(message_id=int(message_id))  # type: ignore[arg-type]
    except Exception:
        _log_debug.debug("get_msg refetch failed | message_id={}", message_id)
        return None
    raw = msg_data.get("message") if isinstance(msg_data, dict) else None
    if not isinstance(raw, list):
        return None
    for seg in raw:
        if isinstance(seg, dict) and seg.get("type") == "image":
            url = str(seg.get("data", {}).get("url", "") or "")
            if url:
                return url
    return None



def _format_image_sidechannel(desc: str | None = None, *, user_text: str = "") -> str:
    """Neutral placeholder for user-authored text surfaces.

    Visual observations must never be rewritten into content_text; structured
    evidence attaches to image_ref side-channel metadata instead.
    """
    del desc, user_text
    return NEUTRAL_IMAGE_PLACEHOLDER


def _neutral_image_placeholder(*, animated: bool = False) -> str:
    return NEUTRAL_ANIMATED_PLACEHOLDER if animated else NEUTRAL_IMAGE_PLACEHOLDER


async def _lookup_human_corrected_identity(
    image_sha256: str,
    *,
    visual_identity_store: Any | None,
    current_user_id: str,
    current_group_id: str | None,
) -> str | None:
    """Exact full-SHA visual-identity recall; fail closed on missing store/context.

    Persisted labels are structured system visual evidence only. Never appends
    to user-authored text. Uses VisualIdentityStore.lookup_for_context when
    available (getattr-safe for concurrent composition-root wiring).
    """
    store = visual_identity_store
    if store is None:
        return None
    lookup = getattr(store, "lookup_for_context", None)
    if not callable(lookup):
        return None
    user = str(current_user_id or "").strip()
    if not user:
        return None
    sha = str(image_sha256 or "").strip().lower()
    if len(sha) != 64:
        return None
    try:
        record = await cast(Any, lookup)(
            sha,
            current_user_id=user,
            current_group_id=current_group_id,
        )
    except Exception:
        _log_debug.debug("visual identity lookup failed | sha={}", sha[:12])
        return None
    if record is None:
        return None
    label = str(getattr(record, "entity_label", "") or "").strip()
    return label or None


def _merge_human_corrected_identity(
    evidence: VisualEvidence,
    entity_label: str,
) -> VisualEvidence:
    """Inject a trusted recognition for a human-corrected entity label.

    Returns a new VisualEvidence so desc_cache base entries stay unscoped.
    Label rides structured visual_identity / side-channel only.
    """
    from services.media.character_recognizer import CharacterRecognition

    label = str(entity_label or "").strip()
    if not label:
        return evidence
    # difference/threshold None => trusted by _is_trusted_identity (no diagnostics).
    correction = CharacterRecognition(
        matched=True,
        character_id=None,
        character_name=label,
        difference=None,
        threshold=None,
        source="user_correction",
    )
    return VisualEvidence(
        image_sha256=evidence.image_sha256,
        image_sha256_short=evidence.image_sha256_short,
        sticker=evidence.sticker,
        # An exact human correction is authoritative for this image/scope.
        # Preserve non-identity detections for count/body context, but remove
        # every machine identity candidate so a prior false positive cannot
        # appear beside the corrected label.
        recognitions=(
            correction,
            *(
                item
                for item in evidence.recognitions
                if not (item.matched and item.character_name)
            ),
        ),
        vision_description=evidence.vision_description,
    )


async def _describe_image_data(
    data: bytes,
    *,
    media_type: str = "image/jpeg",
    vision_client: Any | None = None,
    character_recognizer: Any | None = None,
    sticker_store: Any | None = None,
    desc_cache: dict[str, Any] | None = None,
    mood_engine: Any | None = None,
    mood_group_id: str | int | None = None,
    mood_session_id: str = "",
    visual_identity_store: Any | None = None,
    current_user_id: str = "",
    current_group_id: str | None = None,
) -> VisualEvidence | None:
    """Build structured visual evidence for one image (side-channel only).

    Sticker lookup, character recognition, and Qwen VL are collected as
    annotations on the same image (diagnostics stay internal). This prevents a
    legacy sticker description from short-circuiting identity evidence, while
    still preserving sticker text as a fallback or weak hint.

    Exact visual-identity recall (full SHA-256) is applied after the base
    pipeline using privacy-safe current_user_id / current_group_id. Identity
    enrichment is never stored in desc_cache so cache hits cannot leak across
    user/group boundaries.

    Centralizing this keeps quoted-reply images (`@bot 引用图 这是谁`) on the
    same recognition path as directly-posted images — previously the quoted
    branch only ran plain VL and never consulted CCIP/AnimeTrace/stickers.

    Returns VisualEvidence for image_ref side-channel attach; never injects
    prose into user-authored text.
    """
    if desc_cache is None:
        desc_cache = {}
    full_sha = hashlib.sha256(data).hexdigest()
    short_sha = full_sha[:8]

    cached = desc_cache.get(full_sha) or desc_cache.get(short_sha)
    if isinstance(cached, VisualEvidence):
        _log_debug.debug("desc cache HIT | hash={}", short_sha)
        evidence = cached
    elif isinstance(cached, str) and cached:
        # Legacy string cache entry — re-wrap as observation-only evidence.
        evidence = VisualEvidence(
            image_sha256=full_sha,
            image_sha256_short=short_sha,
            vision_description=cached,
        )
        desc_cache[full_sha] = evidence
        desc_cache[short_sha] = evidence
    else:
        sticker: StickerEvidence | None = None

        if sticker_store is not None:
            sticker_id = sticker_store.lookup_by_hash(data)
            if sticker_id is not None:
                entry = sticker_store.get(sticker_id)
                if entry is not None and entry.get("description"):
                    sticker = StickerEvidence(
                        sticker_id=sticker_id,
                        description=str(entry.get("description") or ""),
                        usage_hint=str(entry.get("usage_hint") or ""),
                        ocr_text=str(entry.get("ocr_text") or ""),
                        source=str(entry.get("source") or ""),
                    )
                    _log_debug.debug("sticker cache HIT | id={}", sticker_id)

        results = []
        if character_recognizer is not None:
            results = await character_recognizer.identify(data, media_type=media_type)
            matched = [r for r in results if r.matched and r.character_name]
            if matched:
                _log_debug.debug(
                    "character recognition HIT | count={} matches={}",
                    len(matched),
                    [
                        {
                            "id": r.character_id,
                            "difference": r.difference,
                            "threshold": r.threshold,
                        }
                        for r in matched
                    ],
                )
                # Phase 3: self/friend → transient mood nudge (first self/friend wins).
                if mood_engine is not None:
                    for r in matched:
                        if r.relation in ("self", "friend"):
                            try:
                                mood_engine.register_recognition_signal(
                                    r.relation,
                                    group_id=mood_group_id,
                                    session_id=mood_session_id,
                                )
                            except Exception:
                                _log_debug.debug("mood recognition-nudge skipped")
                            break

        vision_desc: str | None = None
        should_run_vl = (
            vision_client is not None
            and (
                bool(results)
                or sticker is None
                or sticker.weak_authority
            )
        )
        if should_run_vl and vision_client is not None:
            _log_debug.debug("desc cache MISS | hash={} -> Qwen VL", short_sha)
            vision_desc = await vision_client.describe_image(data)

        evidence = VisualEvidence(
            image_sha256=full_sha,
            image_sha256_short=short_sha,
            sticker=sticker,
            recognitions=tuple(results),
            vision_description=vision_desc,
        )
        # Cache base evidence only (no user-scoped identity).
        desc_cache[full_sha] = evidence
        desc_cache[short_sha] = evidence

    corrected = await _lookup_human_corrected_identity(
        full_sha,
        visual_identity_store=visual_identity_store,
        current_user_id=current_user_id,
        current_group_id=current_group_id,
    )
    if corrected:
        evidence = _merge_human_corrected_identity(evidence, corrected)
        _log_debug.debug(
            "visual identity HIT | sha={} label={!r}",
            short_sha,
            corrected,
        )
    return evidence


async def _render_message(
    msg: Message,
    reply: object | None = None,
    session: aiohttp.ClientSession | None = None,
    self_id: str = "",
    vision_client: Any | None = None,
    character_recognizer: Any | None = None,
    bot: Bot | None = None,
    *,
    in_group: bool = False,
    vision_enabled: bool = True,
    max_images_per_message: int = 5,
    sticker_store: Any | None = None,
    image_cache: Any | None = None,
    desc_cache: dict[str, Any] | None = None,
    mood_engine: Any | None = None,
    mood_group_id: str | int | None = None,
    mood_session_id: str = "",
    visual_identity_store: Any | None = None,
    current_user_id: str = "",
    current_group_id: str | None = None,
) -> Content:
    from kernel.qq_face import face_to_text

    if desc_cache is None:
        desc_cache = {}

    text_parts: list[str] = []
    quoted_images: list[ImageRefBlock | dict[str, Any]] = []
    image_count = 0
    # User-authored text collected first so image intent can gate side-channel
    # summaries without rewriting prose into content_text.
    user_text_for_intent = ""
    for _seg in msg:
        if getattr(_seg, "type", "") == "text":
            user_text_for_intent += str(getattr(_seg, "data", {}).get("text", "") or "")
    image_intent = classify_image_intent(user_text_for_intent)

    if reply is not None:
        reply_msg = getattr(reply, "message", None)
        sender = getattr(reply, "sender", None)
        if reply_msg and sender:
            refetched_reply_urls: dict[int, str | None] = {}

            async def resolve_reply(message_id: str) -> object | None:
                if bot is None:
                    return None
                return await bot.get_msg(message_id=int(message_id))

            async def render_forward(forward_id: str) -> str:
                if bot is None:
                    return f"«合并转发消息 #{forward_id}（未展开）»"
                return await _render_forward_msg(forward_id, bot)

            async def render_quoted_image(
                data: Mapping[str, Any],
                source_message_id: int | None,
            ) -> tuple[str, ImageRefBlock | None]:
                summary = str(data.get("summary", "") or "").strip("[]") or "图片"
                del summary  # never inject vision prose; neutral placeholder only
                url = str(data.get("url", "") or "")
                if not url and bot is not None and source_message_id is not None:
                    if source_message_id not in refetched_reply_urls:
                        try:
                            refetched_reply_urls[source_message_id] = await asyncio.wait_for(
                                _refetch_reply_image_url(bot, source_message_id),
                                timeout=reply_limits.resolve_timeout_s,
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            refetched_reply_urls[source_message_id] = None
                    url = refetched_reply_urls[source_message_id] or ""
                if not url or session is None or not vision_enabled:
                    return NEUTRAL_QUOTED_IMAGE_PLACEHOLDER, None

                image_ref: dict[str, Any] | None = None
                try:
                    async with session.get(url) as img_resp:
                        if img_resp.status != 200:
                            return NEUTRAL_QUOTED_IMAGE_PLACEHOLDER, None
                        img_data = await img_resp.read()
                        media_type = "image/jpeg"
                        if image_cache is not None:
                            file_id = str(data.get("file", "") or "").strip()
                            file_id = file_id.split(".")[0] if "." in file_id else file_id
                            if not file_id:
                                file_id = f"quoted_{hashlib.sha256(img_data).hexdigest()[:24]}"
                            image_ref = await image_cache.save_bytes(img_data, file_id=file_id)
                            if image_ref is not None:
                                from pathlib import Path

                                try:
                                    img_data = Path(image_ref["path"]).read_bytes()
                                    media_type = str(image_ref.get("media_type", media_type))
                                except Exception:
                                    _log_debug.debug(
                                        "quoted cached image read failed | file_id={}",
                                        file_id,
                                    )
                        if image_ref is None:
                            # Side-channel-only ref when disk cache is unavailable.
                            # Path is non-loadable; structured metadata still rides
                            # the image_ref for request-local system evidence.
                            image_ref = {
                                "type": "image_ref",
                                "path": f"memory://quoted/{hashlib.sha256(img_data).hexdigest()[:24]}",
                                "media_type": media_type,
                            }
                        try:
                            enrichment_timeout_s = reply_limits.image_timeout_s
                            if enrichment_timeout_s is None:
                                enrichment_timeout_s = reply_limits.resolve_timeout_s
                            evidence = await asyncio.wait_for(
                                _describe_image_data(
                                    img_data,
                                    media_type=media_type,
                                    vision_client=vision_client,
                                    character_recognizer=character_recognizer,
                                    sticker_store=sticker_store,
                                    desc_cache=desc_cache,
                                    mood_engine=mood_engine,
                                    mood_group_id=mood_group_id,
                                    mood_session_id=mood_session_id,
                                    visual_identity_store=visual_identity_store,
                                    current_user_id=current_user_id,
                                    current_group_id=current_group_id,
                                ),
                                timeout=max(0.0001, float(enrichment_timeout_s)),
                            )
                        except TimeoutError:
                            _log_debug.debug(
                                "quoted image enrichment timed out | url={}",
                                url[:80],
                            )
                            evidence = None
                        except Exception:
                            _log_debug.debug(
                                "quoted image describe failed | url={}",
                                url[:80],
                            )
                            evidence = None
                        if image_ref is not None and evidence is not None:
                            image_ref = attach_visual_sidechannel(
                                image_ref, evidence, intent=image_intent
                            )
                        # Always surface image_ref when available so side-channel
                        # (or bare ref) is not lost even if describe fails.
                        if image_ref is not None:
                            return NEUTRAL_QUOTED_IMAGE_PLACEHOLDER, image_ref  # type: ignore[return-value]
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _log_debug.debug("quoted image fetch/describe failed | url={}", url[:80])
                    if image_ref is not None:
                        return NEUTRAL_QUOTED_IMAGE_PLACEHOLDER, image_ref  # type: ignore[return-value]
                    return NEUTRAL_QUOTED_IMAGE_PLACEHOLDER, None
                # User text gets only a neutral quoted-image marker; evidence is
                # on image_ref side-channel metadata.
                return NEUTRAL_QUOTED_IMAGE_PLACEHOLDER, None

            uid = str(getattr(sender, "user_id", "") or "")
            is_reply_to_bot = bool(self_id and uid == self_id)
            cap = _REPLY_PREVIEW_MAX_SELF if is_reply_to_bot else _REPLY_PREVIEW_MAX
            has_rich_reply = any(
                getattr(seg, "type", "") in {"reply", "image", "json", "forward"}
                for seg in reply_msg
            )
            if has_rich_reply:
                cap = max(cap, _REPLY_PREVIEW_MAX_VISUAL)
            reply_limits = RichRenderLimits(
                max_chars=cap + 160,
                image_timeout_s=15.0,
                max_images=max_images_per_message,
            )
            enrichment_budget_s = reply_limits.image_timeout_s
            if enrichment_budget_s is None:
                enrichment_budget_s = reply_limits.resolve_timeout_s
            renderer_limits = replace(
                reply_limits,
                # The inner enrichment budget must expire first so a saved
                # image_ref can be returned instead of being cancelled by the
                # renderer's whole-image timeout.
                image_timeout_s=max(0.0001, float(enrichment_budget_s)) + 0.05,
            )
            rendered_reply = await render_onebot_segments(
                (),
                reply=reply,
                self_id=self_id,
                reply_resolver=resolve_reply if bot is not None else None,
                forward_renderer=render_forward if bot is not None else None,
                image_renderer=render_quoted_image,
                limits=renderer_limits,
            )
            text_parts.append(rendered_reply.text)
            quoted_images.extend(rendered_reply.images)

    image_tasks: list[tuple[asyncio.Task[ImageRefBlock | None], str]] = []

    for seg in msg:
        if seg.type == "text":
            text_parts.append(seg.data.get("text", ""))
        elif seg.type == "at":
            qq = seg.data.get("qq", "")
            text_parts.append("@我" if self_id and qq == self_id else f"@{qq}")
        elif seg.type == "face":
            face_id = seg.data.get("id", "")
            try:
                text_parts.append(face_to_text(int(face_id)))
            except (ValueError, TypeError):
                text_parts.append("«表情»")
        elif seg.type == "image" and vision_enabled and session is not None:
            sub_type = int(seg.data.get("sub_type", 0))
            label_prefix = "动画表情" if sub_type == 1 else "图片"
            if image_count < max_images_per_message:
                url = seg.data.get("url", "")
                file_id = seg.data.get("file", "")
                if url and file_id and image_cache is not None:
                    file_id = file_id.split(".")[0] if "." in file_id else file_id
                    task = asyncio.ensure_future(
                        image_cache.save(session, url=url, file_id=file_id)
                    )
                    image_tasks.append((task, label_prefix))
                    image_count += 1
                else:
                    text_parts.append(_neutral_image_placeholder(animated=sub_type == 1))
            else:
                text_parts.append(_neutral_image_placeholder(animated=sub_type == 1))
        elif seg.type == "image":
            sub_type = int(seg.data.get("sub_type", 0) or 0)
            text_parts.append(_neutral_image_placeholder(animated=sub_type == 1))
        elif seg.type == "forward":
            forward_id = seg.data.get("id", "")
            if forward_id and bot is not None:
                text_parts.append(await _render_forward_msg(forward_id, bot))

    images: list[tuple[dict[str, Any], str]] = []
    if image_tasks:
        t0 = time.perf_counter()
        tasks = [t for t, _ in image_tasks]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (_, label_prefix), r in zip(image_tasks, results, strict=True):
            if isinstance(r, BaseException) or r is None:
                animated = label_prefix == "动画表情"
                text_parts.append(_neutral_image_placeholder(animated=animated))
            else:
                images.append((dict(r), label_prefix))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _log_debug.debug(
            "render_message images | tasks={} ok={} elapsed={:.0f}ms",
            len(image_tasks), len(images), elapsed_ms,
        )

    enriched_images: list[dict[str, Any]] = []
    if images and (
        vision_client is not None
        or character_recognizer is not None
        or sticker_store is not None
        or visual_identity_store is not None
    ):
        from pathlib import Path

        for ref, label_prefix in images:
            img_path = ref["path"]
            animated = label_prefix == "动画表情"
            try:
                data = Path(img_path).read_bytes()
                # Full pipeline (desc_cache → sticker → CCIP/AnimeTrace → VL),
                # shared with the quoted-reply branch via _describe_image_data.
                # Exact visual-identity recall is applied inside with privacy
                # params (never cached into desc_cache).
                evidence = await _describe_image_data(
                    data,
                    media_type=str(ref.get("media_type", "image/jpeg")),
                    vision_client=vision_client,
                    character_recognizer=character_recognizer,
                    sticker_store=sticker_store,
                    desc_cache=desc_cache,
                    mood_engine=mood_engine,
                    mood_group_id=mood_group_id,
                    mood_session_id=mood_session_id,
                    visual_identity_store=visual_identity_store,
                    current_user_id=current_user_id,
                    current_group_id=current_group_id,
                )
                if evidence is not None:
                    ref = attach_visual_sidechannel(ref, evidence, intent=image_intent)
                enriched_images.append(ref)
                # User-authored text: neutral placeholder only (never vision prose).
                text_parts.append(_neutral_image_placeholder(animated=animated))
            except Exception:
                _log_debug.warning("auto-describe failed | path={}", img_path)
                text_parts.append(_neutral_image_placeholder(animated=animated))
                enriched_images.append(ref)
    elif images:
        for ref, label_prefix in images:
            enriched_images.append(ref)
            text_parts.append(
                _neutral_image_placeholder(animated=label_prefix == "动画表情")
            )

    text = "".join(text_parts).strip()

    if not enriched_images and not quoted_images:
        return text

    blocks: list[ContentBlock] = []
    if text:
        blocks.append(TextBlock(type="text", text=text))
    # image_ref dicts may carry extra visual side-channel keys beyond ImageRefBlock.
    blocks.extend(cast(Any, quoted_images))
    blocks.extend(cast(Any, enriched_images))
    return blocks


# ============================================================================
# Route setup
# ============================================================================


def claim_router_install(driver: Any) -> None:
    """Claim all Omubot router handlers before registering any callback."""
    marker = "_omubot_router_handlers_installed"
    if bool(getattr(driver, marker, False)):
        raise RuntimeError("application routers already installed")
    setattr(driver, marker, True)


def install_lifecycle_handlers(driver: Any, runtime: Any) -> None:
    """Register process lifecycle callbacks exactly once on a driver."""
    marker = "_omubot_application_runtime_installed"
    if bool(getattr(driver, marker, False)):
        raise RuntimeError("application lifecycle already installed")
    setattr(driver, marker, True)

    @driver.on_startup
    async def _startup() -> None:
        await runtime.start()

    @driver.on_shutdown
    async def _shutdown() -> None:
        await runtime.stop()


class ConnectionPipeline(Protocol):
    """Handle protocol connection events outside Router callback wrappers."""

    async def on_connect(self, bot: Bot) -> None: ...

    async def on_disconnect(self, bot: Bot) -> None: ...


def install_connection_handlers(driver: Any, pipeline: ConnectionPipeline) -> None:
    """Register thin connection callbacks that delegate without touching runtime state."""

    @driver.on_bot_connect
    async def _on_connect(bot: Bot) -> None:
        await pipeline.on_connect(bot)

    disconnect_hook = getattr(driver, "on_bot_disconnect", None)
    if callable(disconnect_hook):

        @disconnect_hook
        async def _on_disconnect(bot: Bot) -> None:
            await pipeline.on_disconnect(bot)


def setup_routers(
    bus: PluginBus,
    ctx: PluginContext,
    *,
    runtime: Any | None = None,
    connection_pipeline: ConnectionPipeline | None = None,
) -> None:
    """Register NoneBot event handlers that bridge to PluginBus.

    Must be called before nonebot.run().  Registers:
      - on_startup  → bus.fire_on_startup(ctx)
      - on_shutdown → bus.fire_on_shutdown(ctx)
      - on_bot_connect → history load, schedule, mute check
      - group_listener  → echo, element, timeline, scheduler
      - ban_notice      → mute/unmute
      - private_chat    → LLM chat
    """
    driver = get_driver()
    claim_router_install(driver)

    # ---- lifecycle ----

    if runtime is not None:
        install_lifecycle_handlers(driver, runtime)
    else:
        @driver.on_startup
        async def _startup() -> None:
            await bus.fire_on_startup(ctx)
            # Collect tools from all plugins and add to the shared registry
            if hasattr(ctx, "tool_registry") and ctx.tool_registry is not None:
                for tool in bus.collect_tools():
                    ctx.tool_registry.register(tool)
            # Backup scheduler: daily backup loop + hourly quick_check probe.
            backup_scheduler = getattr(ctx, "backup_scheduler", None)
            if backup_scheduler is not None:
                await backup_scheduler.start()

        @driver.on_shutdown
        async def _shutdown() -> None:
            backup_scheduler = getattr(ctx, "backup_scheduler", None)
            if backup_scheduler is not None:
                await backup_scheduler.stop()
            await bus.fire_on_shutdown(ctx)

    # ---- bot connection lifecycle ----

    if connection_pipeline is None:
        from services.routing import RuntimeConnectionPipeline

        connection_pipeline = RuntimeConnectionPipeline(ctx, bus)
    install_connection_handlers(driver, connection_pipeline)

    # ---- group listener ----

    group_listener = on_message(priority=1, block=False)

    @group_listener.handle()
    async def _collect_group_context(bot: Bot, event: GroupMessageEvent) -> None:
        from services.admin_events import publish_group_message
        from services.echo_key import build_echo_key

        if str(event.user_id) == bot.self_id:
            return
        resolved = ctx.config.group.resolve(event.group_id)
        if not ctx.config.group.allows_learning_group(event.group_id):
            return
        if event.user_id in resolved.blocked_users:
            return
        group_id = str(event.group_id)
        if await _maybe_drop_pair_guard(
            ctx,
            group_id=group_id,
            sender_id=str(event.user_id),
        ):
            return
        muted = ctx.scheduler.is_muted(group_id)
        allow_speaking = ctx.config.group.allows_active_group(event.group_id) and not muted

        msg = event.get_message()
        # Echo detection uses the ORIGINAL (pre-strip) message: NoneBot strips a
        # matched nickname prefix, so "姆。" / "emu。" both collapse to "。" in the
        # stripped segments. Keying off the stripped text would (a) conflate
        # distinct vocatives into one repeat counter and (b) make the bot repeat a
        # bare "。" instead of the full "姆。". When no nickname was stripped,
        # original == stripped, so normal echoes are unaffected.
        echo_segments = _original_segments(event, msg)
        echo_key = build_echo_key(echo_segments)
        plain_text = event.get_plaintext()
        upstream_cfg = getattr(ctx.config, "upstream_command_filter", None)
        upstream_result = should_drop_message(
            int(event.user_id),
            plain_text,
            group_id,
            enabled=bool(getattr(upstream_cfg, "enabled", False)),
            known_other_bots=getattr(getattr(ctx.config, "bot_pair_guard", None), "known_other_bots", {}) or {},
            command_patterns=list(getattr(upstream_cfg, "command_patterns", []) or []),
        )
        if upstream_result.should_drop:
            if bool(getattr(upstream_cfg, "log_drops", True)):
                _log_msg_in.info(
                    "group={} upstream_filter {}({}) | reason={}",
                    group_id,
                    getattr(event.sender, "nickname", "") or event.user_id,
                    event.user_id,
                    upstream_result.reason,
                )
            return

        # Command traffic is control-plane input, not ordinary conversation.
        # Keep the fast path after access/presence/mute/bot-loop gates, but before
        # research capture, admin activity, M1 sensors, plugin hooks, or timeline.
        command_text = _extract_group_command_text(
            msg,
            bot.self_id,
            getattr(ctx, "bot_nicknames", []),
        )
        if command_text:
            if not allow_speaking:
                return
            dispatcher = getattr(ctx, "command_dispatcher", None)
            if dispatcher is None:
                _log_debug.warning("slash command dropped: dispatcher unavailable")
                return
            is_known = getattr(dispatcher, "is_known", None)
            known_command = bool(is_known(command_text)) if callable(is_known) else True
            consumed = await dispatcher.dispatch(
                bot,
                event,
                command_text,
                is_private=False,
                user_id=str(event.user_id),
                group_id=group_id,
                plugin_ctx=ctx,
            )
            if consumed and known_command:
                ctx.scheduler.clear_pending(
                    group_id,
                    cancel_running=command_text.startswith("/debug"),
                )
            # Any leading slash extracted for this bot is command-layer traffic.
            # Fail closed even if a custom dispatcher unexpectedly declines it.
            return

        _capture_research_group_event(ctx, bot, event)
        publish_group_message(
            group_id=group_id,
            user_id=str(event.user_id),
            ts=time.time(),
            presence_mode=resolved.presence_mode,
        )
        registry = getattr(ctx, "name_registry", None)
        if isinstance(registry, NameVariationRegistry):
            registry.update_from_event(
                group_id,
                int(event.user_id),
                getattr(event.sender, "nickname", "") or str(event.user_id),
                getattr(event.sender, "card", "") or "",
            )

        is_addressed = event.is_tome()
        # is_tome() misses a sandwiched @ (e.g. [image][at:bot]这是谁) — the @ is
        # neither first nor last segment. Backfill from a full-segment scan so an
        # explicit @ always takes the rule-layer addressed path (fire) instead of
        # the probabilistic gray zone where it can lose the RWS roll.
        if not is_addressed and _message_ats_self(msg, bot.self_id):
            is_addressed = True
        if not is_addressed and getattr(ctx, "bot_nicknames", []):
            # Text nicknames are vocatives, not arbitrary substrings.  A nickname
            # buried mid-sentence is content, while a prefix nickname is an
            # addressing signal equivalent to @bot.
            is_addressed = _is_bot_nickname_prefix(plain_text, getattr(ctx, "bot_nicknames", []))

        addressing = _resolve_addressing_context(
            event,
            msg,
            self_id=str(bot.self_id),
            bot_nicknames=getattr(ctx, "bot_nicknames", ()),
            is_addressed=is_addressed,
        )
        semantic_plain_text = _semantic_plain_text_for_addressing(addressing, plain_text)

        nickname = event.sender.nickname or str(event.user_id)

        # Build MessageContext and fire bus.on_message for interceptors.  Use the
        # semantic plaintext for downstream plugins; keep the adapter-stripped text
        # in raw_message for audit because NoneBot may strip nickname vocatives.
        msg_ctx = MessageContext(
            session_id=f"group_{group_id}",
            group_id=group_id,
            user_id=str(event.user_id),
            content=semantic_plain_text,
            raw_message={
                "message_id": event.message_id,
                "echo_key": echo_key,
                "plain_text": semantic_plain_text,
                "stripped_plain_text": plain_text,
                "original_plain_text": addressing.original_text,
                "segments": msg,
                "echo_segments": echo_segments,
            },
            is_at=is_addressed,
            is_private=False,
            message_id=event.message_id,
            bot=bot,
            nickname=nickname,
            allow_speaking=allow_speaking,
            group_presence_mode=resolved.presence_mode,
            group_access_allowed=resolved.access_allowed,
        )
        # silent_learn / off groups: only run silent_safe interceptors (e.g. slang
        # learning) so they can record observations, but skip any plugin that
        # could send a message or set ctx.trigger. Active groups go through the
        # full interceptor chain below.
        if not allow_speaking:
            await bus.fire_on_message(msg_ctx, silent_mode=True)
            rendered_plain_text = msg.extract_plain_text().strip()
            silent_semantic_text = semantic_plain_text
            if addressing.evidence == "nickname_original":
                silent_semantic_text = addressing.original_text.strip() or silent_semantic_text
            silent_segments: list[object] = list(msg)
            if silent_semantic_text and silent_semantic_text != rendered_plain_text:
                restored_prefix = (
                    silent_semantic_text[:-len(rendered_plain_text)]
                    if rendered_plain_text and silent_semantic_text.endswith(rendered_plain_text)
                    else silent_semantic_text if not rendered_plain_text else ""
                )
                if restored_prefix:
                    silent_segments = []
                    semantic_inserted = False
                    for segment in msg:
                        if segment.type == "text" and not semantic_inserted:
                            data = dict(segment.data)
                            data["text"] = restored_prefix + str(data.get("text", "") or "")
                            silent_segments.append({"type": "text", "data": data})
                            semantic_inserted = True
                            continue
                        silent_segments.append(segment)
                else:
                    semantic_inserted = any(segment.type == "text" for segment in msg)
                if not semantic_inserted:
                    silent_segments.insert(0, {
                        "type": "text",
                        "data": {"text": silent_semantic_text},
                    })
            rendered_silent = await render_onebot_segments(
                silent_segments,
                reply=event.reply,
                self_id=str(bot.self_id),
            )
            silent_content = rendered_silent.text.strip()
            if silent_semantic_text and not silent_content:
                silent_content = silent_semantic_text
            if silent_content:
                preview = silent_content if len(silent_content) <= 120 else silent_content[:120] + "…"
                _log_msg_in.info("group={} silent_learn {}({}) | {}", group_id, nickname, event.user_id, preview)
                ctx.timeline.add(
                    group_id,
                    role="user",
                    speaker=f"{nickname}({event.user_id})",
                    content=silent_content,
                    message_id=event.message_id,
                )
            return

        # Dialogue Climate irritation treats a text-nickname vocative ("emu。", "笑梦") as an
        # explicit mention, same as a protocol @.  The reply-obligation path
        # already equates the two (is_addressed / addressing.evidence), so the
        # tension sensor must too — otherwise the dominant real-world form of
        # "being repeatedly cue'd" (nickname spam) never moves tension.
        if _addressing_triggers_climate_mention(addressing):
            register_climate_mention_irritation(
                ctx,
                group_id=group_id,
                actor_user_id=str(event.user_id),
                message_id=event.message_id,
            )

        if await bus.fire_on_message(msg_ctx):
            return  # consumed by an interceptor plugin

        # Build TriggerContext from plugin data or @-detection
        trigger = msg_ctx.trigger  # set by BilibiliPlugin etc.
        if trigger is None and is_addressed:
            from kernel.types import TriggerContext

            # When the message @'s another human in addition to the bot,
            # the question is likely directed at that person, not us — a
            # bare "这是谁" with a double-@ means "hey Alice, who is this",
            # and the bot should not chip in.  Hard self-addressing evidence
            # (text nickname in original_message, direct @self, reply-to-self)
            # wins before the detector sees NoneBot's stripped text.
            has_other_at = _message_has_other_at(msg, bot.self_id)

            if addressing.target == "self" and addressing.evidence in {
                "at_self",
                "nickname_original",
                "reply_to_self",
            }:
                addressee_self = True
            else:
                addressee_self = await _at_trigger_targets_self(
                    rendered_message=str(msg),
                    plain_text=plain_text,
                    reply_sender_id=str(getattr(getattr(event.reply, "sender", None), "user_id", "") or ""),
                    self_id=str(bot.self_id),
                    bot_nicknames=getattr(ctx, "bot_nicknames", ()),
                    addressed_fallback=is_addressed and not has_other_at,
                )
            matched_nickname = addressing.matched_nickname or _match_nickname_addressing(
                event, getattr(ctx, "bot_nicknames", ()),
            )
            reason = f"有人叫你「{matched_nickname}」" if matched_nickname else "有人@了你"
            obligation = ReplyObligation(
                level="must" if addressee_self else "may",
                reason="self_addressed" if addressee_self else "ambiguous_addressing",
                source=str(addressing.evidence or "router"),
                priority=100 if addressee_self else 10,
                addressing=addressing,
            )
            _log_reply_workflow.info(
                "addressing_resolved | group={} event={} target={} obligation={} "
                "evidence={} matched={} original={!r} stripped={!r}",
                group_id,
                event.message_id,
                addressing.target,
                obligation.level,
                addressing.evidence,
                matched_nickname,
                addressing.original_text[:80],
                addressing.stripped_text[:80],
            )
            trigger = TriggerContext(
                reason=reason,
                mode="at_mention",
                target_message_id=event.message_id,
                target_user_id=str(event.user_id),
                obligation=obligation,
                extra={
                    "addressee_self": addressee_self,
                    "reply_sender_id": str(getattr(getattr(event.reply, "sender", None), "user_id", "") or ""),
                    "addressing_evidence": addressing.evidence,
                    "matched_nickname": matched_nickname,
                    "addressing_original_text": addressing.original_text,
                    "addressing_stripped_text": addressing.stripped_text,
                    "nickname_stripped_to_empty_payload": _nickname_stripped_to_empty_payload(addressing),
                    "nickname_only_call": False,
                },
            )

        # Serialize render→timeline-commit per group so a slow image render can't
        # be overtaken by a faster later message that commits + fires a reply
        # before the image lands in the timeline (the "bot replies without seeing
        # the image" race). The lock is released right after the commit below;
        # the reply-workflow/notify tail stays concurrent.
        async with _group_ingest_lock(ctx, group_id):
            content = await _render_message(
                msg,
                reply=event.reply,
                session=ctx.llm_client._session,
                self_id=bot.self_id,
                vision_client=ctx.vision_client,
                character_recognizer=getattr(ctx, "character_recognizer", None),
                bot=bot,
                in_group=True,
                vision_enabled=ctx.vision_enabled,
                max_images_per_message=ctx.max_images_per_message,
                sticker_store=ctx.sticker_store,
                image_cache=ctx.image_cache,
                desc_cache=ctx.desc_cache,
                mood_engine=getattr(ctx, "mood_engine", None),
                mood_group_id=group_id,
                mood_session_id=f"group_{group_id}",
                visual_identity_store=getattr(ctx, "visual_identity_store", None),
                current_user_id=str(event.user_id),
                current_group_id=str(group_id),
            )

            if not content:
                if is_addressed:
                    timeline_content = "@我"
                    if _nickname_stripped_to_empty_payload(addressing):
                        timeline_content = addressing.original_text.strip() or timeline_content
                    if trigger is not None and trigger.mode == "at_mention":
                        trigger.extra["nickname_only_call"] = _nickname_stripped_to_empty_payload(addressing)
                        trigger.extra["semantic_text"] = timeline_content
                    _log_msg_in.info("group={} @-only (empty content)", group_id)
                    ctx.timeline.add(
                        group_id,
                        role="user",
                        speaker=f"{nickname}({event.user_id})",
                        content=timeline_content,
                        message_id=event.message_id,
                    )
                    if not muted:
                        await _notify_group_scheduler(
                            ctx,
                            group_id=group_id,
                            user_id=str(event.user_id),
                            trigger=trigger,
                            is_addressed=is_addressed,
                            message=timeline_content,
                            event=event,
                            self_id=str(bot.self_id),
                        )
                return

            nickname_only_call = _is_nickname_only_call(addressing, content)
            if nickname_only_call:
                # Preserve the original vocative as the semantic text.  NoneBot
                # has stripped the nickname from event.message, so the rendered
                # content would otherwise be just "。" and poison reply gates /
                # context retrieval.
                content = addressing.original_text.strip() or content
            if trigger is not None and trigger.mode == "at_mention":
                trigger.extra["nickname_only_call"] = nickname_only_call
                trigger.extra["semantic_text"] = _content_to_text(content)

            preview = content if isinstance(content, str) else "".join(
                str(b.get("text", ""))
                for b in content
                if isinstance(b, dict) and b.get("type") == "text" and "text" in b
            )
            if len(preview) > 120:
                preview = preview[:120] + "…"
            _log_msg_in.info("group={} {}({}) | {}", group_id, nickname, event.user_id, preview)

            ctx.timeline.add(
                group_id,
                role="user",
                speaker=f"{nickname}({event.user_id})",
                content=content,
                message_id=event.message_id,
            )
        reply_workflow_config = getattr(ctx.config, "reply_workflow", None)
        legacy_directed = _is_directed_followup_text(_content_to_text(content))
        followup_window_s = float(
            getattr(reply_workflow_config, "directed_followup_window_s", _DIRECTED_FOLLOWUP_WINDOW_S),
        )
        has_recent_assistant, last_assistant_text, assistant_elapsed_s = _latest_assistant_reply_info(
            ctx.timeline,
            group_id,
            within_s=followup_window_s,
        )
        last_assistant_to_user = _last_assistant_replied_to_user(
            ctx.timeline,
            group_id,
            str(event.user_id),
            within_s=followup_window_s,
        )
        reply_workflow_mode = "shadow"
        if reply_workflow_config is not None:
            from services.reply_workflow import workflow_mode

            reply_workflow_mode = workflow_mode(reply_workflow_config)
        semantic_result = None
        semantic_candidate_reason = ""
        semantic_effective_threshold = float(getattr(reply_workflow_config, "semantic_force_threshold", 0.78))
        semantic_trace_request_id = ""
        if reply_workflow_mode in {"shadow", "semantic"}:
            from services.reply_workflow import (
                ReplyGateFeatures,
                evaluate_group_gate_shadow,
                evaluate_semantic_gate,
                log_shadow_decision,
                semantic_gate_threshold,
                should_call_semantic_gate,
                should_consume_semantic_gate,
            )

            t0 = time.perf_counter()
            decision, classification = evaluate_group_gate_shadow(
                text=_content_to_text(content),
                has_trigger=trigger is not None,
                trigger_mode=trigger.mode if trigger is not None else "",
                is_addressed=is_addressed,
                legacy_directed=legacy_directed,
                has_recent_assistant=has_recent_assistant,
                has_other_at=_message_has_other_at(msg, bot.self_id),
                reply_to_bot=_reply_targets_bot(getattr(event, "reply", None), bot.self_id),
                last_assistant_to_user=last_assistant_to_user,
            )
            log_shadow_decision(
                decision,
                conversation=f"group_{group_id}",
                mode="group_gate_shadow",
                event_id=str(event.message_id),
                text=_content_to_text(content),
                latency_ms=(time.perf_counter() - t0) * 1000,
                extra={
                    "classification_reason": classification.reason,
                    "current_trigger": trigger.mode if trigger is not None else "none",
                },
            )
            features = ReplyGateFeatures(
                current_text=_content_to_text(content),
                current_user_id=str(event.user_id),
                has_current_trigger=trigger is not None,
                has_recent_assistant=has_recent_assistant,
                has_other_at=_message_has_other_at(msg, bot.self_id),
                reply_to_bot=_reply_targets_bot(getattr(event, "reply", None), bot.self_id),
                last_assistant_to_user=last_assistant_to_user,
                last_assistant_text=last_assistant_text,
                elapsed_since_assistant_s=assistant_elapsed_s,
            )
            should_call_gate, semantic_candidate_reason = should_call_semantic_gate(
                features,
                max_chars=int(getattr(reply_workflow_config, "semantic_max_chars", 48)),
            )
            semantic_threshold = semantic_gate_threshold(
                fixed_threshold=float(getattr(reply_workflow_config, "semantic_force_threshold", 0.78)),
                dynamic_enabled=bool(
                    getattr(getattr(ctx.config, "humanization", None), "semantic_gate_dynamic", False),
                ),
                familiarity=_semantic_gate_familiarity(ctx, str(event.user_id)),
                mood_energy=_semantic_gate_mood_energy(ctx, group_id),
            )
            semantic_effective_threshold = semantic_threshold.effective_threshold
            if reply_workflow_mode == "semantic" and should_call_gate:
                semantic_trace_request_id = _u13_trace_request_id(group_id, event.message_id)
                gate_start = time.perf_counter()
                semantic_result = await evaluate_semantic_gate(
                    features,
                    api_call=ctx.llm_client._call,
                    timeout_ms=int(getattr(reply_workflow_config, "semantic_timeout_ms", 600)),
                    user_id=str(event.user_id),
                    group_id=group_id,
                )
                consumed = should_consume_semantic_gate(
                    semantic_result,
                    threshold=semantic_effective_threshold,
                )
                from services.block_trace.llm_call_trace import record_llm_call_trace

                await record_llm_call_trace(
                    getattr(ctx, "block_trace_store", None),
                    request_id=semantic_trace_request_id,
                    task="reply_gate",
                    provider="semantic_gate",
                    session_id=f"group_{group_id}",
                    group_id=group_id,
                    user_id=str(event.user_id),
                    event_id=str(event.message_id),
                    metadata={
                        "candidate_reason": semantic_candidate_reason,
                        "consumed": consumed,
                        "effective_threshold": semantic_effective_threshold,
                        "timeout_ms": int(getattr(reply_workflow_config, "semantic_timeout_ms", 600)),
                        "correlation_key": f"group_{group_id}:{event.user_id}",
                        "result_action": getattr(semantic_result, "action", ""),
                        "result_confidence": getattr(semantic_result, "confidence", None),
                        "result_intent": getattr(semantic_result, "intent", ""),
                    },
                )
                if semantic_result is not None:
                    semantic_decision = semantic_result.to_decision(candidate_reason=semantic_candidate_reason)
                else:
                    from services.reply_workflow import ReplyGateDecision

                    semantic_decision = ReplyGateDecision(
                        action="pass",
                        source="llm_gate",
                        confidence=0.0,
                        reason="semantic_gate_failed_closed",
                        labels={"candidate_reason": semantic_candidate_reason, "consumed": False},
                    )
                log_shadow_decision(
                    semantic_decision,
                    conversation=f"group_{group_id}",
                    mode="semantic_gate",
                    event_id=str(event.message_id),
                    text=_content_to_text(content),
                    latency_ms=(time.perf_counter() - gate_start) * 1000,
                    extra={"consumed": consumed, **semantic_threshold.log_fields()},
                )
        semantic_consumed = False
        if reply_workflow_mode == "semantic":
            from services.reply_workflow import should_consume_semantic_gate

            semantic_consumed = should_consume_semantic_gate(
                semantic_result,
                threshold=semantic_effective_threshold,
            )
        correction_triggered = False
        scheduler = getattr(ctx, "scheduler", None)
        # Weak-reply P0: closing/farewell detection. A terminal token ("晚安") in an
        # ongoing two-person exchange demands a symmetric reply (Schegloff & Sacks).
        # Injected like directed_followup; scheduler bypasses the probability gate.
        # Higher priority than followup — a bare "晚安" should close, not be treated
        # as a continuation request.
        if (
            trigger is None
            and not is_addressed
            and has_recent_assistant
            and last_assistant_to_user
        ):
            from services.reply_workflow import classify_closing_intent

            if classify_closing_intent(_content_to_text(content)):
                from kernel.types import TriggerContext

                _log_reply_workflow.info(
                    "closing_intent | group={} user={}", group_id, event.user_id,
                )
                trigger = TriggerContext(
                    reason="用户在收尾告别，回一个对称的告别 token",
                    mode="closing",
                    target_message_id=event.message_id,
                    target_user_id=str(event.user_id),
                )
        # Weak-reply: greeting/opening detection. Mirror of closing — a bare "早安"
        # in an ongoing two-person exchange invites a symmetric hello. Gated on the
        # same recent-assistant condition so the bot does NOT answer every group-wide
        # morning greeting it merely overhears.
        if (
            trigger is None
            and not is_addressed
            and has_recent_assistant
            and last_assistant_to_user
        ):
            from services.reply_workflow import classify_greeting_intent

            if classify_greeting_intent(_content_to_text(content)):
                from kernel.types import TriggerContext

                _log_reply_workflow.info(
                    "greeting_intent | group={} user={}", group_id, event.user_id,
                )
                trigger = TriggerContext(
                    reason="用户在跟你打招呼，回一个对称的招呼 token",
                    mode="greeting",
                    target_message_id=event.message_id,
                    target_user_id=str(event.user_id),
                )
        arbiter = getattr(scheduler, "_arbiter", None) if scheduler is not None else None
        arbiter_config = getattr(scheduler, "_arbiter_config", None) if scheduler is not None else None
        if (
            trigger is None
            and not is_addressed
            and scheduler is not None
            and arbiter is not None
            and arbiter_config is not None
            and last_assistant_to_user
            and bool(getattr(arbiter_config, "correction_enabled", False))
            and bool(getattr(arbiter_config, "enabled", False))
        ):
            slot = scheduler.get_slot(group_id) if hasattr(scheduler, "get_slot") else None
            correction_window_s = float(getattr(arbiter_config, "correction_window_s", 30.0) or 30.0)
            last_reply_content = str(getattr(slot, "last_reply_content", "") or "")
            last_reply_time = float(getattr(slot, "last_reply_time", 0.0) or 0.0)
            if (
                last_reply_content
                and last_reply_time > 0.0
                and time.time() - last_reply_time <= correction_window_s
            ):
                correction = await arbiter.judge_correction(
                    bot_reply=last_reply_content,
                    new_message=_content_to_text(content),
                    user_id=str(event.user_id),
                    group_id=group_id,
                )
                if correction.needs_correction:
                    from kernel.types import TriggerContext

                    _log_reply_workflow.info(
                        "arbiter_c_correction | group={} user={} type={}",
                        group_id,
                        event.user_id,
                        correction.correction_type or "unknown",
                    )
                    trigger = TriggerContext(
                        reason="用户补充了改变语义的信息，请自然修正上一条回复",
                        mode="correction",
                        target_message_id=event.message_id,
                        target_user_id=str(event.user_id),
                        extra={
                            "correction_type": correction.correction_type,
                            "original_reply": last_reply_content,
                            "reply_sender_id": str(getattr(getattr(event.reply, "sender", None), "user_id", "") or ""),
                        },
                    )
                    if slot is not None:
                        slot.last_reply_content = ""
                        slot.last_reply_time = 0.0
                    correction_triggered = True
        if (
            trigger is None
            and not is_addressed
            and has_recent_assistant
            and (legacy_directed or semantic_consumed)
            and not correction_triggered
        ):
            from kernel.types import TriggerContext
            trigger = TriggerContext(
                reason="用户追问上一轮回复",
                mode="directed_followup",
                target_message_id=event.message_id,
                target_user_id=str(event.user_id),
                extra={
                    "u13_double_haiku_request_id": semantic_trace_request_id,
                    "reply_sender_id": str(getattr(getattr(event.reply, "sender", None), "user_id", "") or ""),
                } if semantic_trace_request_id else {},
            )
        if not muted:
            await _notify_group_scheduler(
                ctx,
                group_id=group_id,
                user_id=str(event.user_id),
                trigger=trigger,
                is_addressed=is_addressed,
                message=content,
                event=event,
                self_id=str(bot.self_id),
            )

    # ---- factual evidence invalidation on group recall ----

    social_narrative_recall = on_notice(priority=1, block=False)

    @social_narrative_recall.handle()
    async def _handle_social_narrative_recall(
        bot: Bot,
        event: NoticeEvent,
    ) -> None:
        del bot
        await _invalidate_social_narrative_recall(ctx, event)

    # ---- QQ inbound interaction notices ----

    qq_interaction_notice = on_notice(priority=1, block=False)

    @qq_interaction_notice.handle()
    async def _handle_qq_interaction_notice(bot: Bot, event: NoticeEvent) -> None:
        signal = parse_qq_interaction_signal(event, self_id=str(bot.self_id))
        if signal is None:
            return
        if not signal.is_tome and signal.kind == "message_reaction" and signal.raw_message_id:
            try:
                msg_data = await bot.get_msg(message_id=signal.raw_message_id)
                sender_id = str(msg_data.get("sender", {}).get("user_id", ""))
                if sender_id == str(bot.self_id):
                    signal = QQInteractionSignal(
                        kind=signal.kind,
                        group_id=signal.group_id,
                        actor_user_id=signal.actor_user_id,
                        target_user_id=str(bot.self_id),
                        raw_message_id=signal.raw_message_id,
                        emoji_code=signal.emoji_code,
                        is_tome=True,
                    )
            except Exception:
                pass
        dispatch_qq_interaction_signal(ctx, signal, now=time.time())

    # ---- group ban notice ----

    ban_notice = on_notice(priority=1, block=False)

    @ban_notice.handle()
    async def _handle_group_ban(bot: Bot, event: GroupBanNoticeEvent) -> None:
        if str(event.user_id) != bot.self_id:
            return
        group_id = str(event.group_id)
        if event.sub_type == "ban":
            until_unix = time.time() + float(getattr(event, "duration", 0) or 0)
            ctx.scheduler.mute(group_id, source="event", until_unix=until_unix)
            logger.warning("bot muted | group={} duration={}s", group_id, event.duration)
        elif event.sub_type == "lift_ban":
            ctx.scheduler.unmute(group_id)
            logger.info("bot unmuted | group={}", group_id)

    # ---- private chat ----

    private_chat = on_message(rule=to_me(), priority=10, block=True)

    @private_chat.handle()
    async def _handle_private_chat(bot: Bot, event: MessageEvent) -> None:
        # Early return for group messages — before any heavy imports.
        if isinstance(event, GroupMessageEvent):
            return

        if ctx.allowed_private_users and event.user_id not in ctx.allowed_private_users:
            return

        # Private slash traffic is also control-plane input. Consume known and
        # unknown roots before media rendering, memory state, or any LLM call.
        raw_text = event.get_plaintext().strip()
        if raw_text.startswith("/"):
            dispatcher = getattr(ctx, "command_dispatcher", None)
            if dispatcher is not None:
                command_bot: Any = bot
                is_known = getattr(dispatcher, "is_known", None)
                if callable(is_known) and not is_known(raw_text):
                    class _PrivateUnknownCommandReply:
                        async def send(self, _event: Any, message: Message) -> None:
                            await private_chat.finish(message)

                    command_bot = _PrivateUnknownCommandReply()
                await dispatcher.dispatch(
                    command_bot,
                    event,
                    raw_text,
                    is_private=True,
                    user_id=str(event.user_id),
                    group_id=None,
                    plugin_ctx=ctx,
                )
            else:
                _log_debug.warning("private slash command dropped: dispatcher unavailable")
            return

        from services.llm.client import RATE_LIMIT_BASE_DELAY, RATE_LIMIT_MAX_RETRIES, RateLimitError
        from services.tools.context import ToolContext

        reply_msg = getattr(event, "reply", None)
        user_content = await _render_message(
            event.get_message(),
            reply=reply_msg,
            session=ctx.llm_client._session,
            self_id=bot.self_id,
            vision_client=ctx.vision_client,
            character_recognizer=getattr(ctx, "character_recognizer", None),
            bot=bot,
            vision_enabled=ctx.vision_enabled,
            max_images_per_message=ctx.max_images_per_message,
            sticker_store=ctx.sticker_store,
            image_cache=ctx.image_cache,
            desc_cache=ctx.desc_cache,
            mood_engine=getattr(ctx, "mood_engine", None),
            mood_group_id=None,
            mood_session_id=f"private_{event.user_id}",
            visual_identity_store=getattr(ctx, "visual_identity_store", None),
            current_user_id=str(event.user_id),
            current_group_id=None,
        )
        if not user_content:
            # NoneBot's _check_nickname strips the nickname from the message.
            # If the user ONLY said the bot's name (e.g. "姆"), user_content
            # becomes empty — treat it as a greeting.
            if event.is_tome():
                user_content = "你好"
            else:
                return

        sid = _session_id(event)
        identity = ctx.persona_runtime.identity_snapshot()
        tool_ctx = ToolContext(bot=bot, user_id=str(event.user_id), group_id=None, session_id=sid)
        private_actor = get_private_conversation_actor(sid)

        reply_workflow_config = getattr(ctx.config, "reply_workflow", None)
        if (
            getattr(reply_workflow_config, "mode", "shadow") == "shadow"
            and getattr(reply_workflow_config, "shadow_log_private", True)
        ):
            from services.reply_workflow import log_shadow_decision, private_current_path_decision

            private_text = _content_to_text(user_content)
            t0 = time.perf_counter()
            decision = private_current_path_decision(text=private_text)
            log_shadow_decision(
                decision,
                conversation=sid,
                mode="private_actor_shadow",
                event_id=str(getattr(event, "message_id", "") or ""),
                text=private_text,
                latency_ms=(time.perf_counter() - t0) * 1000,
            )

        async def send_segment(text: str) -> None:
            await bot.send(event, Message(text))

        async with private_actor.turn(
            event_id=str(getattr(event, "message_id", "") or ""),
            user_id=str(event.user_id),
            text=_content_to_text(user_content),
        ) as turn:
            reply: object | None = None
            for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
                try:
                    reply = await ctx.llm_client.chat(
                        session_id=sid,
                        user_id=str(event.user_id),
                        user_content=user_content,
                        identity=identity,
                        group_id=None,
                        ctx=tool_ctx,
                        on_segment=send_segment,
                        force_reply=False,
                    )
                    break
                except RateLimitError:
                    if attempt >= RATE_LIMIT_MAX_RETRIES:
                        logger.error("private chat rate limit exhausted after {} retries", RATE_LIMIT_MAX_RETRIES)
                        reply = "当前请求太多，请稍后再试"
                        break
                    delay = RATE_LIMIT_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "private chat rate limited, retry {}/{} in {:.0f}s",
                        attempt + 1, RATE_LIMIT_MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                except Exception:
                    logger.exception("chat error")
                    reply = "出错了，请稍后再试"
                    break

            reply_text = ""
            if isinstance(reply, str):
                reply_text = reply.strip()
            elif reply is not None:
                reply_text = str(getattr(reply, "full_reply", reply) or "").strip()

            if not reply_text:
                thinker_action = str(getattr(ctx.llm_client, "_last_thinker_action", "") or "")
                thinker_thought = str(getattr(ctx.llm_client, "_last_thinker_thought", "") or "")
                reason = "thinker_wait" if thinker_action == "wait" else "llm_returned_no_visible_reply"
                metadata = {}
                if thinker_thought:
                    metadata["thinker_thought"] = thinker_thought
                transition = turn.mark_wait(reason, metadata=metadata or None)
                log_private_transition(transition)
                return

            transition = turn.mark_complete(
                "assistant_reply_sent",
                reply_text=reply_text,
            )
            log_private_transition(transition)
            await private_chat.finish(Message(reply_text))
