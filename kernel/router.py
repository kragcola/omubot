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
from typing import Any, cast

import aiohttp
from loguru import logger as _base_logger
from nonebot import get_driver, on_message, on_notice
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupBanNoticeEvent,
    GroupMessageEvent,
    Message,
    MessageEvent,
    NoticeEvent,
)
from nonebot.rule import to_me

from kernel.bus import PluginBus
from kernel.types import (
    Content,
    ContentBlock,
    ImageRefBlock,
    MessageContext,
    PluginContext,
    TextBlock,
)
from services.humanization import AFFECTION_FAMILIARITY_SLOT
from services.humanization.qq_interactions import (
    dispatch_qq_interaction_signal,
    parse_qq_interaction_signal,
)
from services.private_conversation import (
    get_private_conversation_actor,
    log_private_transition,
)
from services.system_module import Scope

logger = _base_logger
_log_msg_in = _base_logger.bind(channel="message_in")
_log_msg_out = _base_logger.bind(channel="message_out")
_log_system = _base_logger.bind(channel="system")
_log_usage = _base_logger.bind(channel="usage")
_log_debug = _base_logger.bind(channel="debug")
_log_reply_workflow = _base_logger.bind(channel="reply_workflow")

_REPLY_PREVIEW_MAX = 50
_REPLY_PREVIEW_MAX_SELF = 200
_DIRECTED_FOLLOWUP_RE = re.compile(
    r"^(我也?)?(能|可以|可不可以|能不能)(来|去|参加|一起|加入|玩)(吗|嘛|么)?[。.!！?？~～\s]*$"
    r"|^(我也?)?可以(吗|嘛|么)?[。.!！?？~～\s]*$"
    r"|^(带上?我(吗|嘛|么)?|算我一个|我也想(来|去|参加|一起|加入|玩))[。.!！?？~～\s]*$"
)
_DIRECTED_FOLLOWUP_WINDOW_S = 180.0
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
        if isinstance(block, dict) and block.get("type") == "text"
    )


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
        if not text_parts or not "".join(text_parts).strip():
            return None

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
        if nick and normalized.startswith(nick):
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


def _reply_targets_bot(reply: object | None, self_id: str) -> bool:
    sender = getattr(reply, "sender", None)
    if sender is None:
        return False
    return str(getattr(sender, "user_id", "") or "") == str(self_id)


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


def _has_recent_assistant_reply(timeline: object, group_id: str, *, within_s: float) -> bool:
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
    timeline: object,
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
    timeline: object,
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
        if idx <= 0:
            return False
        previous = turns[idx - 1]
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
        return all(current_user_suffix in line for line in active_user_lines)
    return False


async def _render_forward_msg(forward_id: str, bot: Bot) -> str:
    try:
        result = await bot.call_api("get_forward_msg", message_id=forward_id)
    except Exception:
        logger.warning("get_forward_msg API failed | id={}", forward_id)
        return "«合并转发消息（无法获取内容）»"

    messages: list[dict[str, object]] = []
    if isinstance(result, dict):
        data = result.get("data", result)
        messages = data.get("messages", result.get("messages", []))
    if isinstance(messages, str):
        return f"«合并转发消息: {messages[:200]}»"
    if not isinstance(messages, list) or not messages:
        return "«合并转发消息（空）»"

    lines: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        sender = m.get("sender", {})
        if isinstance(sender, dict):
            uid = str(sender.get("user_id", ""))
            nick = str(sender.get("nickname", uid))
            label = f"{nick}({uid})"
        else:
            label = "未知"

        content = m.get("message", m.get("content", ""))
        if isinstance(content, list):
            parts: list[str] = []
            for seg in content:
                if isinstance(seg, dict):
                    if seg.get("type") == "text":
                        parts.append(str(seg.get("data", {}).get("text", "")))
                    elif seg.get("type") == "image":
                        url = str(seg.get("data", {}).get("url", ""))
                        fname = str(seg.get("data", {}).get("file", ""))
                        if url:
                            parts.append(f"«图片: {url[:80]}»")
                        elif fname:
                            parts.append(f"«图片: {fname}»")
                        else:
                            parts.append("«图片（无描述）»")
                    elif seg.get("type") == "face":
                        parts.append("«表情»")
                    elif seg.get("type") == "at":
                        qq = str(seg.get("data", {}).get("qq", ""))
                        parts.append(f"@{qq}")
                    elif seg.get("type") == "forward":
                        parts.append("«嵌套转发»")
                    elif seg.get("type") == "file":
                        fname = str(seg.get("data", {}).get("name", "未知文件"))
                        parts.append(f"«文件: {fname}»")
                    else:
                        parts.append(f"«{seg.get('type', '未知')}»")
            text = "".join(parts).strip()
        elif isinstance(content, str):
            text = content.strip()
        else:
            text = str(content)

        if text:
            lines.append(f"{label}: {text}")

    if not lines:
        return "«合并转发消息（无文本内容）»"

    body = "\n".join(lines)
    if len(body) > 2000:
        body = body[:2000] + "…"
    logger.info("forward_msg rendered | id={} msgs={} chars={}", forward_id, len(lines), len(body))
    return f"«合并转发消息»\n{body}"


async def _render_message(
    msg: Message,
    reply: object | None = None,
    session: aiohttp.ClientSession | None = None,
    self_id: str = "",
    vision_client: object | None = None,
    bot: Bot | None = None,
    *,
    in_group: bool = False,
    vision_enabled: bool = True,
    max_images_per_message: int = 5,
    sticker_store: object | None = None,
    image_cache: object | None = None,
    desc_cache: dict[str, str] | None = None,
) -> Content:
    from kernel.qq_face import face_to_text

    if desc_cache is None:
        desc_cache = {}

    text_parts: list[str] = []
    image_count = 0

    if reply is not None:
        sender = getattr(reply, "sender", None)
        reply_msg = getattr(reply, "message", None)
        if sender and reply_msg:
            uid = str(getattr(sender, "user_id", "") or "")
            nick = getattr(sender, "nickname", "") or uid
            is_reply_to_bot = self_id and uid == self_id
            cap = _REPLY_PREVIEW_MAX_SELF if is_reply_to_bot else _REPLY_PREVIEW_MAX
            original = reply_msg.extract_plain_text().strip()
            if not original:
                seg_descs: list[str] = []
                for seg in reply_msg:
                    if seg.type == "image":
                        desc: str | None = None
                        url = seg.data.get("url", "")
                        if url and vision_client is not None and session is not None:
                            try:
                                async with session.get(url) as img_resp:
                                    if img_resp.status == 200:
                                        img_data = await img_resp.read()
                                        desc = await vision_client.describe_image(img_data)
                            except Exception:
                                pass
                        if desc:
                            seg_descs.append(f"[图片: {desc}]")
                        else:
                            s = seg.data.get("summary", "").strip("[]") or "图片"
                            seg_descs.append(f"[{s}]")
                    elif seg.type == "face":
                        seg_descs.append("[表情]")
                    elif seg.type == "text":
                        t = seg.data.get("text", "").strip()
                        if t:
                            seg_descs.append(t)
                original = "".join(seg_descs)
            if len(original) > cap:
                original = original[:cap] + "…"
            label = "回复 我" if is_reply_to_bot else f"回复 {nick}({uid})"
            text_parts.append(f"«{label}: {original}» ")

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
                if url and file_id:
                    file_id = file_id.split(".")[0] if "." in file_id else file_id
                    task = asyncio.ensure_future(
                        image_cache.save(session, url=url, file_id=file_id)
                    )
                    image_tasks.append((task, label_prefix))
                    image_count += 1
                else:
                    text_parts.append(f"«{label_prefix}»")
            else:
                text_parts.append(f"«{label_prefix}»")
        elif seg.type == "image":
            summary = seg.data.get("summary", "").strip("[]") or "图片"
            text_parts.append(f"«{summary}»")
        elif seg.type == "forward":
            forward_id = seg.data.get("id", "")
            if forward_id and bot is not None:
                text_parts.append(await _render_forward_msg(forward_id, bot))

    images: list[tuple[ImageRefBlock, str]] = []
    if image_tasks:
        t0 = time.perf_counter()
        tasks = [t for t, _ in image_tasks]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (_, label_prefix), r in zip(image_tasks, results, strict=True):
            if isinstance(r, BaseException) or r is None:
                text_parts.append(f"«{label_prefix}»")
            else:
                images.append((r, label_prefix))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _log_debug.debug(
            "render_message images | tasks={} ok={} elapsed={:.0f}ms",
            len(image_tasks), len(images), elapsed_ms,
        )

    if images and vision_client is not None:
        from pathlib import Path

        for i, (ref, label_prefix) in enumerate(images):
            img_path = ref["path"]
            try:
                data = Path(img_path).read_bytes()
                img_hash = hashlib.sha256(data).hexdigest()[:8]
                desc: str | None = None

                if img_hash in desc_cache:
                    desc = desc_cache[img_hash]
                    _log_debug.debug("desc cache HIT | hash={} file={}", img_hash, Path(img_path).name)

                if desc is None and sticker_store is not None:
                    sticker_id = sticker_store.lookup_by_hash(data)
                    if sticker_id is not None:
                        entry = sticker_store.get(sticker_id)
                        if entry is not None:
                            desc = entry.get("description")
                            if desc:
                                _log_debug.debug("sticker cache HIT | id={}", sticker_id)

                if desc is None:
                    _log_debug.debug("desc cache MISS | hash={} file={} -> Qwen VL", img_hash, Path(img_path).name)
                    desc = await vision_client.describe_image(data)
                    if desc:
                        desc_cache[img_hash] = desc

                if desc:
                    text_parts.append(f"«{label_prefix}{i + 1}: {desc}»")
                else:
                    text_parts.append(f"«{label_prefix}»")
            except Exception:
                _log_debug.warning("auto-describe failed | path={}", img_path)
                text_parts.append(f"«{label_prefix}»")
    elif images and vision_client is None:
        for _i, (_ref, label_prefix) in enumerate(images):
            text_parts.append(f"«{label_prefix}»")

    text = "".join(text_parts).strip()

    if not images:
        return text

    blocks: list[ContentBlock] = []
    if text:
        blocks.append(TextBlock(type="text", text=text))
    blocks.extend(ref for ref, _ in images)
    return blocks


# ============================================================================
# Route setup
# ============================================================================


def setup_routers(bus: PluginBus, ctx: PluginContext) -> None:
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

    # ---- lifecycle ----

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

    # ---- bot connect ----

    @driver.on_bot_connect
    async def _on_connect(bot: Bot) -> None:
        ctx.bot = bot
        protocol_connections = getattr(ctx, "protocol_connections", None)
        if protocol_connections is not None and hasattr(protocol_connections, "record_connected"):
            protocol_connections.record_connected(bot)

        protocol_trace = getattr(ctx, "protocol_trace", None)
        if (
            protocol_trace is not None
            and hasattr(protocol_trace, "wrap_bot")
            and protocol_trace.wrap_bot(bot)
        ):
            _log_system.info("protocol trace wrapper installed | self_id={}", bot.self_id)

        ctx.llm_client._bot_self_id = bot.self_id
        ctx.state_board.bot_self_id = bot.self_id
        ctx.prompt_builder.build_static(ctx.identity_mgr.resolve(), bot_self_id=bot.self_id)
        ctx.scheduler.set_bot(bot)

        # B2 shadow compare — flag-gated; defaults off so this is a no-op.
        persona_v2_cfg = getattr(ctx.config, "persona_v2", None)
        if persona_v2_cfg is not None and persona_v2_cfg.shadow_compare:
            from services.persona.shadow import ShadowCompareEngine

            v1_identity = ctx.identity_mgr.resolve()
            pb = ctx.prompt_builder
            shadow_engine = ShadowCompareEngine(
                cfg=persona_v2_cfg,
                v1_static_text=pb.static_block.get("text", "") if pb.static_block else "",
                v1_identity=v1_identity,
                v1_instruction_text=getattr(pb, "_instruction", "") or "",
                v1_admins=getattr(pb, "_admins", None),
                v1_proactive=v1_identity.proactive or "",
                v1_bot_self_id=str(bot.self_id),
            )
            try:
                await shadow_engine.run_once()
            except Exception as exc:
                _base_logger.bind(channel="persona_shadow").warning(
                    "shadow compare unexpected error: {}", exc
                )
            ctx.shadow_engine = shadow_engine

        # B3 runtime cutover — flag-gated; defaults off so this is a no-op.
        runtime_selector = None
        if persona_v2_cfg is not None and persona_v2_cfg.runtime_consume:
            from services.persona.runtime import load_pending_freeze
            from services.persona.runtime_selector import (
                PersonaRuntimeSelector,
                join_static_blocks,
            )

            bundle = load_pending_freeze(persona_v2_cfg.persona_id)
            v2_text = ""
            if bundle is not None and bundle.ok:
                v2_text = join_static_blocks(bundle)
            runtime_selector = PersonaRuntimeSelector(
                cfg=persona_v2_cfg,
                bundle=bundle,
                v2_static_text=v2_text,
            )
            if bundle is None or not bundle.ok:
                level = "warning" if persona_v2_cfg.fallback_on_compile_error else "error"
                getattr(_base_logger.bind(channel="persona_runtime"), level)(
                    "v2 bundle unavailable | bundle_ok={} errors={}",
                    bundle is not None and bundle.ok,
                    tuple(bundle.errors) if bundle else (),
                )
        ctx.prompt_builder.set_runtime_selector(runtime_selector)
        ctx.runtime_selector = runtime_selector

        # Track whether this is the first connect (vs reconnect)
        is_first_connect = not getattr(ctx, "startup_triggered", False)
        if is_first_connect:
            ctx.startup_triggered = True

        # Notify plugins AFTER startup_triggered is set (plugins check it for one-shot ops)
        await bus.fire_on_bot_connect(ctx, bot)

        if is_first_connect:
            bus.start_tick_loop(ctx)

        # Wire usage alert
        admin_ids = list(ctx.admins.keys())
        if admin_ids and ctx.config.llm.usage.enabled:

            async def _alert_admins(msg: str) -> None:
                for admin_id in admin_ids:
                    try:
                        await bot.send_private_msg(user_id=int(admin_id), message=msg)
                    except Exception:
                        _log_usage.warning("failed to send usage alert to admin {}", admin_id)

            ctx.usage_tracker.set_alert(
                alert_fn=_alert_admins,
                cache_hit_warn=ctx.config.compact.cache_hit_warn,
                slow_threshold_s=ctx.config.llm.usage.slow_threshold_s,
                cache_alert_window_m=ctx.config.compact.cache_alert_window_m,
                cache_alert_cooldown_m=ctx.config.compact.cache_alert_cooldown_m,
            )

        try:
            group_list: list[dict[str, object]] = await bot.get_group_list()
            group_inventory: dict[str, dict[str, object]] = {}
            for item in group_list:
                gid = str(item.get("group_id", "") or "").strip()
                if not gid:
                    continue
                group_inventory[gid] = dict(item)
            ctx.group_inventory = group_inventory
            group_ids = list(group_inventory)
            group_cfg = getattr(ctx.config, "group", None)
            if group_cfg is not None and hasattr(group_cfg, "allows_learning_group"):
                group_ids = [gid for gid in group_ids if group_cfg.allows_learning_group(gid)]
        except Exception:
            logger.exception("failed to get group list")
            return
        _log_system.info(
            "group inventory refreshed | total={} learning={}",
            len(getattr(ctx, "group_inventory", {}) or {}),
            len(group_ids),
        )

        if not is_first_connect:
            _log_system.info("reconnected, skipping first-connect setup")

        # Check bot mute status in each group
        muted_count = 0
        for gid in group_ids:
            try:
                info: dict[str, object] = await bot.get_group_member_info(
                    group_id=int(gid), user_id=int(bot.self_id),
                )
                raw = info.get("shut_up_timestamp") or 0
                shut_until = int(str(raw))
                if shut_until > time.time():
                    ctx.scheduler.mute(gid)
                    muted_count += 1
            except Exception:
                _log_debug.debug("failed to query mute status | group={}", gid)
        if muted_count:
            logger.info("muted in {} group(s) at startup", muted_count)

        logger.info("Bot 就绪，开始接收消息 ✓")

    if hasattr(driver, "on_bot_disconnect"):

        @driver.on_bot_disconnect  # type: ignore[attr-defined]
        async def _on_disconnect(bot: Bot) -> None:
            protocol_connections = getattr(ctx, "protocol_connections", None)
            if protocol_connections is not None and hasattr(protocol_connections, "record_disconnected"):
                protocol_connections.record_disconnected(bot)
            current_bot = getattr(ctx, "bot", None)
            if current_bot is bot or str(getattr(current_bot, "self_id", "")) == str(getattr(bot, "self_id", "")):
                ctx.bot = None
            _log_system.warning("bot disconnected | self_id={}", getattr(bot, "self_id", "unknown"))

    # ---- group listener ----

    group_listener = on_message(priority=1, block=False)

    @group_listener.handle()
    async def _collect_group_context(bot: Bot, event: GroupMessageEvent) -> None:
        from plugins.echo import build_echo_key
        from services.admin_events import publish_group_message

        if str(event.user_id) == bot.self_id:
            return
        resolved = ctx.config.group.resolve(event.group_id)
        publish_group_message(
            group_id=str(event.group_id),
            user_id=str(event.user_id),
            ts=time.time(),
            presence_mode=resolved.presence_mode,
        )
        if not ctx.config.group.allows_learning_group(event.group_id):
            return
        if event.user_id in resolved.blocked_users:
            return
        group_id = str(event.group_id)
        muted = ctx.scheduler.is_muted(group_id)
        allow_speaking = ctx.config.group.allows_active_group(event.group_id) and not muted

        msg = event.get_message()
        echo_key = build_echo_key(msg)
        plain_text = event.get_plaintext()

        is_addressed = event.is_tome()
        if not is_addressed and getattr(ctx, "bot_nicknames", []) and any(
            nick in plain_text for nick in ctx.bot_nicknames
        ):
            is_addressed = True

        nickname = event.sender.nickname or str(event.user_id)

        # Build MessageContext and fire bus.on_message for interceptors
        msg_ctx = MessageContext(
            session_id=f"group_{group_id}",
            group_id=group_id,
            user_id=str(event.user_id),
            content=plain_text,
            raw_message={
                "message_id": event.message_id,
                "echo_key": echo_key,
                "plain_text": plain_text,
                "segments": msg,
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
            if plain_text:
                preview = plain_text if len(plain_text) <= 120 else plain_text[:120] + "…"
                _log_msg_in.info("group={} silent_learn {}({}) | {}", group_id, nickname, event.user_id, preview)
                ctx.timeline.add(
                    group_id,
                    role="user",
                    speaker=f"{nickname}({event.user_id})",
                    content=plain_text,
                    message_id=event.message_id,
                )
            return

        if await bus.fire_on_message(msg_ctx):
            return  # consumed by an interceptor plugin

        # Build TriggerContext from plugin data or @-detection
        trigger = msg_ctx.trigger  # set by BilibiliPlugin etc.
        if trigger is None and is_addressed:
            from kernel.types import TriggerContext

            addressee_self = await _at_trigger_targets_self(
                rendered_message=str(msg),
                plain_text=plain_text,
                reply_sender_id=str(getattr(getattr(event.reply, "sender", None), "user_id", "") or ""),
                self_id=str(bot.self_id),
                bot_nicknames=getattr(ctx, "bot_nicknames", ()),
                addressed_fallback=is_addressed,
            )
            trigger = TriggerContext(
                reason="有人@了你",
                mode="at_mention",
                target_message_id=event.message_id,
                target_user_id=str(event.user_id),
                extra={"addressee_self": addressee_self},
            )

        # Check slash commands before timeline/scheduler.
        # Cancel any pending debounce so a previous message's thinker doesn't
        # fire while the user is interactively debugging.
        command_text = _extract_group_command_text(
            msg,
            bot.self_id,
            getattr(ctx, "bot_nicknames", []),
        )
        if (
            command_text
            and hasattr(ctx, "command_dispatcher")
            and ctx.command_dispatcher is not None
            and await ctx.command_dispatcher.dispatch(
                bot, event, command_text,
                is_private=False,
                user_id=str(event.user_id),
                group_id=group_id,
                plugin_ctx=ctx,
            )
        ):
            ctx.scheduler.clear_pending(group_id, cancel_running=command_text.startswith("/debug"))
            return

        content = await _render_message(
            msg,
            reply=event.reply,
            session=ctx.llm_client._session,
            self_id=bot.self_id,
            vision_client=ctx.vision_client,
            bot=bot,
            in_group=True,
            vision_enabled=ctx.vision_enabled,
            max_images_per_message=ctx.max_images_per_message,
            sticker_store=ctx.sticker_store,
            image_cache=ctx.image_cache,
            desc_cache=ctx.desc_cache,
        )

        if not content:
            if is_addressed:
                _log_msg_in.info("group={} @-only (empty content)", group_id)
                ctx.timeline.add(
                    group_id,
                    role="user",
                    speaker=f"{nickname}({event.user_id})",
                    content="@我",
                    message_id=event.message_id,
                )
                if not muted:
                    ctx.scheduler.notify(group_id, trigger=trigger, user_id=str(event.user_id))
            return

        preview = content if isinstance(content, str) else "".join(
            b["text"] for b in content
            if isinstance(b, dict) and b.get("type") == "text"  # type: ignore[union-attr]
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
        if (
            trigger is None
            and not is_addressed
            and has_recent_assistant
            and (legacy_directed or semantic_consumed)
        ):
            from kernel.types import TriggerContext
            trigger = TriggerContext(
                reason="用户追问上一轮回复",
                mode="directed_followup",
                target_message_id=event.message_id,
                target_user_id=str(event.user_id),
                extra={
                    "u13_double_haiku_request_id": semantic_trace_request_id,
                } if semantic_trace_request_id else {},
            )
        if not muted:
            ctx.scheduler.notify(group_id, trigger=trigger, user_id=str(event.user_id))

    # ---- QQ inbound interaction notices ----

    qq_interaction_notice = on_notice(priority=1, block=False)

    @qq_interaction_notice.handle()
    async def _handle_qq_interaction_notice(bot: Bot, event: NoticeEvent) -> None:
        signal = parse_qq_interaction_signal(event, self_id=str(bot.self_id))
        if signal is None:
            return
        dispatch_qq_interaction_signal(ctx, signal, now=time.time())

    # ---- group ban notice ----

    ban_notice = on_notice(priority=1, block=False)

    @ban_notice.handle()
    async def _handle_group_ban(bot: Bot, event: GroupBanNoticeEvent) -> None:
        if str(event.user_id) != bot.self_id:
            return
        group_id = str(event.group_id)
        if event.sub_type == "ban":
            ctx.scheduler.mute(group_id)
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

        from services.llm.client import RATE_LIMIT_BASE_DELAY, RATE_LIMIT_MAX_RETRIES, RateLimitError
        from services.tools.context import ToolContext

        if ctx.allowed_private_users and event.user_id not in ctx.allowed_private_users:
            return

        reply_msg = getattr(event, "reply", None)
        user_content = await _render_message(
            event.get_message(),
            reply=reply_msg,
            session=ctx.llm_client._session,
            self_id=bot.self_id,
            vision_client=ctx.vision_client,
            bot=bot,
            vision_enabled=ctx.vision_enabled,
            max_images_per_message=ctx.max_images_per_message,
            sticker_store=ctx.sticker_store,
            image_cache=ctx.image_cache,
            desc_cache=ctx.desc_cache,
        )
        if not user_content:
            # NoneBot's _check_nickname strips the nickname from the message.
            # If the user ONLY said the bot's name (e.g. "姆"), user_content
            # becomes empty — treat it as a greeting.
            if event.is_tome():
                user_content = "你好"
            else:
                return

        # Check slash commands before LLM processing
        raw_text = event.get_plaintext().strip()
        if (
            raw_text
            and hasattr(ctx, "command_dispatcher")
            and ctx.command_dispatcher is not None
            and await ctx.command_dispatcher.dispatch(
                bot, event, raw_text,
                is_private=True,
                user_id=str(event.user_id),
                group_id=None,
                plugin_ctx=ctx,
            )
        ):
            return

        sid = _session_id(event)
        identity = ctx.identity_mgr.resolve()
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
