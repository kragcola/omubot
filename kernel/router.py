"""NoneBot event routing → PluginBus bridge.

Registers all NoneBot event handlers (on_message, on_notice, on_bot_connect)
and bridges them to PluginBus while accessing system services via PluginContext.
"""

from __future__ import annotations

import asyncio
import hashlib
import time

import aiohttp
from loguru import logger as _base_logger
from nonebot import get_driver, on_message, on_notice
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupBanNoticeEvent,
    GroupMessageEvent,
    Message,
    MessageEvent,
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

logger = _base_logger
_log_msg_in = _base_logger.bind(channel="message_in")
_log_msg_out = _base_logger.bind(channel="message_out")
_log_system = _base_logger.bind(channel="system")
_log_usage = _base_logger.bind(channel="usage")
_log_debug = _base_logger.bind(channel="debug")

_REPLY_PREVIEW_MAX = 50
_REPLY_PREVIEW_MAX_SELF = 200
_DEBUG_PREFIX = "/debug"


# ============================================================================
# Utility functions
# ============================================================================


def _session_id(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"group_{event.group_id}"
    return f"private_{event.user_id}"


def _check_debug_prefix(content: Content) -> tuple[Content, bool]:
    if isinstance(content, str):
        idx = content.find(_DEBUG_PREFIX)
        if idx >= 0:
            remainder = content[idx + len(_DEBUG_PREFIX):].lstrip()
            if remainder:
                return remainder, True
        return content, False
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            text = first.get("text", "")
            if isinstance(text, str):
                idx = text.find(_DEBUG_PREFIX)
                if idx >= 0:
                    stripped = text[idx + len(_DEBUG_PREFIX):].lstrip()
                    if stripped:
                        new_first = {**first, "text": stripped}
                        return [new_first, *content[1:]], True
        return content, False
    return content, False


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

    @driver.on_shutdown
    async def _shutdown() -> None:
        await bus.fire_on_shutdown(ctx)

    # ---- bot connect ----

    @driver.on_bot_connect
    async def _on_connect(bot: Bot) -> None:
        ctx.llm_client._bot_self_id = bot.self_id
        ctx.state_board.bot_self_id = bot.self_id
        ctx.prompt_builder.build_static(ctx.identity_mgr.resolve(), bot_self_id=bot.self_id)
        ctx.scheduler.set_bot(bot)

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
            group_ids = [str(g["group_id"]) for g in group_list]
            if ctx.allowed_groups:
                group_ids = [gid for gid in group_ids if int(gid) in ctx.allowed_groups]
        except Exception:
            logger.exception("failed to get group list")
            return

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

        # Evaluate history for each group — catch up on missed messages (first connect only)
        if is_first_connect:
            for gid in group_ids:
                if ctx.timeline.get_turns(gid) or ctx.timeline.get_pending(gid):
                    ctx.scheduler.trigger(gid)

    # ---- group listener ----

    group_listener = on_message(priority=1, block=False)

    @group_listener.handle()
    async def _collect_group_context(bot: Bot, event: GroupMessageEvent) -> None:
        from plugins.echo.plugin import build_echo_key

        if ctx.allowed_groups and event.group_id not in ctx.allowed_groups:
            return
        if str(event.user_id) == bot.self_id:
            return
        if ctx.scheduler.is_muted(str(event.group_id)):
            return
        resolved = ctx.config.group.resolve(event.group_id)
        if event.user_id in resolved.blocked_users:
            return

        msg = event.get_message()
        echo_key = build_echo_key(msg)
        plain_text = event.get_plaintext()

        is_addressed = event.is_tome()
        if not is_addressed and getattr(ctx, "bot_nicknames", []) and any(
            nick in plain_text for nick in ctx.bot_nicknames
        ):
            is_addressed = True

        group_id = str(event.group_id)
        nickname = event.sender.nickname or str(event.user_id)

        # Build MessageContext and fire bus.on_message for interceptors
        msg_ctx = MessageContext(
            session_id=f"group_{group_id}",
            group_id=group_id,
            user_id=str(event.user_id),
            content="",  # rendered later
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
        )
        if await bus.fire_on_message(msg_ctx):
            return  # consumed by an interceptor plugin

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
                ctx.scheduler.notify(group_id, is_at=is_addressed)
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
        ctx.scheduler.notify(group_id, is_at=is_addressed)

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
        from services.llm.client import _RATE_LIMIT_BASE_DELAY, _RATE_LIMIT_MAX_RETRIES, RateLimitError
        from services.tools.context import ToolContext

        if isinstance(event, GroupMessageEvent):
            return
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
            return

        user_content, force_reply = _check_debug_prefix(user_content)
        if force_reply:
            user_id_str = str(event.user_id)
            if user_id_str not in ctx.admins:
                logger.warning("debug denied (not admin) | user={}", user_id_str)
                force_reply = False
            else:
                logger.info("debug mode | user={} session=private_{}", user_id_str, user_id_str)

        sid = _session_id(event)
        identity = ctx.identity_mgr.resolve()
        tool_ctx = ToolContext(bot=bot, user_id=str(event.user_id), group_id=None, session_id=sid)

        async def send_segment(text: str) -> None:
            await bot.send(event, Message(text))

        reply: str | None = None
        for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
            try:
                reply = await ctx.llm_client.chat(
                    session_id=sid,
                    user_id=str(event.user_id),
                    user_content=user_content,
                    identity=identity,
                    group_id=None,
                    ctx=tool_ctx,
                    on_segment=send_segment,
                    force_reply=force_reply,
                )
                break
            except RateLimitError:
                if attempt >= _RATE_LIMIT_MAX_RETRIES:
                    logger.error("private chat rate limit exhausted after {} retries", _RATE_LIMIT_MAX_RETRIES)
                    reply = "当前请求太多，请稍后再试"
                    break
                delay = _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "private chat rate limited, retry {}/{} in {:.0f}s",
                    attempt + 1, _RATE_LIMIT_MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
            except Exception:
                logger.exception("chat error")
                reply = "出错了，请稍后再试"
                break

        if reply:
            await private_chat.finish(Message(reply))
