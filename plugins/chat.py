"""ChatPlugin: core chat functionality — priority 0, uninstallable.

Owns all system service lifecycle. Other plugins access services via PluginContext.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import timedelta
from typing import Any

import nonebot
from loguru import logger

from kernel.config import BotConfig, load_plugin_config
from kernel.types import AmadeusPlugin, PluginContext

_L = logger.bind(channel="system")


class ChatPlugin(AmadeusPlugin):
    name = "chat"
    version = "1.1.4"
    description = "Core chat: LLM client, group scheduler, memory, tools, identity"
    priority = 0

    def __init__(self) -> None:
        super().__init__()
        self._ctx: PluginContext | None = None

    def register_commands(self) -> list:
        from kernel.types import Command
        return [
            Command(
                name="debug",
                handler=self._handle_debug,
                description="进入调试模式：跳过 thinker，注入实时状态数据，用纯文本回答",
                usage="/debug [可选问题]",
                sub_commands=[
                    Command(
                        name="save",
                        handler=self._handle_debug_save,
                        description="保存最近图片到表情包库",
                        usage="/debug save [描述]",
                        aliases=["保存", "收录", "添加表情"],
                    ),
                    Command(
                        name="send",
                        handler=self._handle_debug_send,
                        description="发送表情包（指定ID或随机）",
                        usage="/debug send [stk_id|gif]",
                        aliases=["发", "发送"],
                    ),
                    Command(
                        name="split",
                        handler=self._handle_debug_split,
                        description="测试文本分段效果",
                        usage="/debug split <文本>",
                        aliases=["分段", "分割"],
                    ),
                ],
            ),
        ]

    async def _handle_debug(self, cmd_ctx: Any) -> None:
        """Handle /debug command: admin-only debug mode with live state and tool execution."""
        from nonebot.adapters.onebot.v11 import Message

        from services.llm.client import (
            _PASS_TURN_TOOL,
            _build_debug_block,
            _strip_markdown,
            _to_anthropic_tools,
        )
        from services.tools.context import ToolContext

        ctx = self._ctx
        if ctx is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("系统未就绪"))
            return

        if cmd_ctx.user_id not in ctx.admins:
            logger.warning("debug denied (not admin) | user={}", cmd_ctx.user_id)
            await cmd_ctx.bot.send(cmd_ctx.event, Message("无权限"))
            return

        logger.info(
            "debug mode | user={} {}",
            cmd_ctx.user_id,
            "private" if cmd_ctx.is_private else f"group={cmd_ctx.group_id}",
        )

        sid = f"private_{cmd_ctx.user_id}" if cmd_ctx.is_private else f"group_{cmd_ctx.group_id}"
        user_content = cmd_ctx.args if cmd_ctx.args else "请显示当前系统状态摘要"
        has_command = bool(cmd_ctx.args)

        # If the first token is a lowercase ASCII word, it's likely a mistyped
        # subcommand — show available subcommands rather than sending to LLM.
        if has_command:
            first_token = cmd_ctx.args.split()[0]
            _known_subs = {"save", "send", "split", "保存", "收录", "添加表情", "发", "发送", "分段", "分割"}
            if first_token.isascii() and first_token.islower() and first_token not in _known_subs:
                await cmd_ctx.bot.send(
                    cmd_ctx.event,
                    Message(
                        f"未知子命令: /debug {first_token}\n"
                        "可用: /debug save, /debug send, /debug split\n"
                        "或输入中文问题进入 LLM 调试模式"
                    ),
                )
                return

        # Build tool context once
        tool_ctx_obj = ToolContext(
            bot=cmd_ctx.bot,
            user_id=str(cmd_ctx.user_id),
            group_id=str(cmd_ctx.group_id) if not cmd_ctx.is_private else None,
        )

        # ---- LLM path ----
        if has_command:
            # Include sticker library so the LLM can pick sticker IDs
            sticker_view = ""
            if ctx.sticker_store is not None:
                sticker_view = ctx.sticker_store.format_prompt_view()
            system_blocks = [
                {"type": "text", "text": (
                    "你是工具执行器。用户指令=工具调用。\n"
                    "规则：绝不输出文字。你的每次回复必须是 tool_use，不能是 text。\n"
                    "发送表情→send_sticker(sticker_id)  查卡→lookup_cards\n"
                    "设置昵称→set_nickname  更新记忆→update_card  管理表情→manage_sticker\n"
                    "如果用户的指令无法匹配任何工具，调用 pass_turn(reason='原因')。"
                )},
            ]
            if sticker_view:
                system_blocks.append({"type": "text", "text": sticker_view})
        else:
            # No command: full state dump for inspection
            _L.info("debug building state dump | user={}", cmd_ctx.user_id)
            debug_text = await asyncio.wait_for(
                _build_debug_block(
                    user_id=cmd_ctx.user_id,
                    session_id=sid,
                    mood_engine=ctx.mood_engine,
                    affection_engine=ctx.affection_engine,
                    schedule_store=ctx.schedule_store,
                    card_store=ctx.card_store,
                    short_term=ctx.short_term,
                    message_log=ctx.msg_log,
                ),
                timeout=15.0,
            )
            _L.info("debug state dump ready | chars={}", len(debug_text))
            system_blocks = [
                {"type": "text", "text": debug_text},
                {"type": "text", "text": (
                    "你是调试助手。基于上面的实时状态数据如实回答用户的问题。\n"
                    "格式约束：QQ 不支持 Markdown。禁止代码块、加粗、行内代码。使用纯文本。"
                )},
            ]
        messages: list[Any] = [{"role": "user", "content": user_content}]

        # Build tool definitions — all registered tools available
        tool_defs: list[dict[str, Any]] | None = None
        if not ctx.tool_registry.empty:
            tool_defs = _to_anthropic_tools(ctx.tool_registry.to_openai_tools())
        tool_defs = [*(tool_defs or []), _PASS_TURN_TOOL]
        _L.info(
            "debug tool_defs | count={} names={}",
            len(tool_defs), [t["name"] for t in tool_defs],
        )

        MAX_TOOL_ROUNDS = 5

        try:
            for _round_i in range(MAX_TOOL_ROUNDS):
                _L.info("debug API call round={}", _round_i)
                try:
                    result = await asyncio.wait_for(
                        ctx.llm_client._call(system_blocks, messages, tools=tool_defs),
                        timeout=60.0,
                    )
                except TimeoutError:
                    _L.error("debug API call timed out | round={}", _round_i)
                    await cmd_ctx.bot.send(cmd_ctx.event, Message("调试: API 调用超时 (60s)"))
                    return
                text: str = result.get("text", "")
                tool_uses: list[Any] = result.get("tool_uses", [])
                _L.info(
                    "debug API response | round={} text_len={} tool_count={} names={}",
                    _round_i, len(text), len(tool_uses),
                    [tu.name for tu in tool_uses],
                )

                # pass_turn: skip action, just reply with text (if any)
                if any(tu.name == "pass_turn" for tu in tool_uses):
                    used_tools = [tu for tu in tool_uses if tu.name != "pass_turn"]
                    if not used_tools:
                        reason = ""
                        for tu in tool_uses:
                            if tu.name == "pass_turn":
                                reason = tu.input.get("reason", "")
                        reply_text = text.strip() or reason or "pass_turn (no reason)"
                        _L.info("debug pass_turn | reason={!r}", reason)
                        await ctx.humanizer.delay(reply_text)
                        await cmd_ctx.bot.send(cmd_ctx.event, Message(f"[pass_turn] {reply_text}"))
                        return

                if not tool_uses:
                    reply_text = _strip_markdown(text or "")
                    if reply_text.strip():
                        _L.info("debug reply | len={} text={!r}", len(reply_text), reply_text[:120])
                        await ctx.humanizer.delay(reply_text)
                        await cmd_ctx.bot.send(cmd_ctx.event, Message(reply_text))
                    return

                # Build assistant content with tool_use blocks
                assistant_content: list[dict[str, Any]] = []
                for tb in result.get("thinking_blocks", []):
                    assistant_content.append(tb)
                if text:
                    assistant_content.append({"type": "text", "text": text})
                for tu in tool_uses:
                    assistant_content.append({
                        "type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input,
                    })
                messages.append({"role": "assistant", "content": assistant_content})

                # Execute tools
                _L.info("debug executing tools | count={} names={}", len(tool_uses), [tu.name for tu in tool_uses])
                call_results = await asyncio.gather(
                    *[ctx.tool_registry.call(tu.name, json.dumps(tu.input), ctx=tool_ctx_obj)
                      for tu in tool_uses],
                    return_exceptions=True,
                )
                call_results = [
                    r if isinstance(r, str) else f"Tool error: {r}" for r in call_results
                ]
                _L.info(
                    "debug tool results | names={} results={}",
                    [tu.name for tu in tool_uses],
                    [r[:200] for r in call_results],
                )
                tool_results: list[dict[str, Any]] = []
                for tu, rtext in zip(tool_uses, call_results, strict=True):
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id, "content": rtext,
                    })
                messages.append({"role": "user", "content": tool_results})

            # Tool loop exhausted — final reply
            _L.debug("debug tool loop exhausted, calling final API")
            result = await asyncio.wait_for(
                ctx.llm_client._call(system_blocks, messages),
                timeout=60.0,
            )
            reply_text = _strip_markdown(result.get("text") or "")
            if reply_text.strip():
                _L.info("debug reply (final) | len={} text={!r}", len(reply_text), reply_text[:120])
                await ctx.humanizer.delay(reply_text)
                await cmd_ctx.bot.send(cmd_ctx.event, Message(reply_text))
        except TimeoutError:
            _L.error("debug API call timed out (final)")
            await cmd_ctx.bot.send(cmd_ctx.event, Message("调试: API 调用超时 (60s)"))
        except Exception:
            logger.exception("debug command LLM call failed")
            await cmd_ctx.bot.send(cmd_ctx.event, Message("调试查询失败，请稍后重试"))

    async def _handle_debug_save(self, cmd_ctx: Any) -> None:
        """Handle /debug save — save recent image as sticker (no LLM)."""
        import re as _re_cq
        from pathlib import Path as _Path

        from nonebot.adapters.onebot.v11 import Message

        from services.tools.context import ToolContext
        from services.tools.sticker_tools import SaveStickerTool

        ctx = self._ctx
        if ctx is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("系统未就绪"))
            return
        if cmd_ctx.user_id not in ctx.admins:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("无权限"))
            return

        store = ctx.sticker_store
        if store is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("表情包库未启用"))
            return

        superusers: set[str] = (
            set(getattr(ctx.config, "admins", {}).keys())
            | nonebot.get_driver().config.superusers
        )

        # 1. Extract images from the current message event
        image_paths: list[str] = []
        raw_msg = cmd_ctx.event.get_message()
        _seg_types: list[str] = []
        for seg in raw_msg:
            _seg_types.append(seg.type)
            if seg.type == "image":
                url = seg.data.get("url", "")
                file_uniq = seg.data.get("file", "")
                if url and ctx.image_cache:
                    try:
                        ref = await ctx.image_cache.save(
                            ctx.llm_client._session, url=url, file_id=file_uniq,
                        )
                        if ref is not None:
                            image_paths.append(ref["path"])
                    except Exception as e:
                        logger.warning("debug save: image download failed | url={} err={}", url[:80], e)
            elif seg.type in ("mface", "market_face"):
                key = seg.data.get("key") or seg.data.get("file_unique") or seg.data.get("id", "")
                summary = seg.data.get("summary", "")
                if key and ctx.sticker_store:
                    try:
                        resp = await cmd_ctx.bot.call_api("get_image", file=key)
                        file_data = resp.get("file", "")
                        if file_data.startswith("base64://"):
                            import base64 as _b64
                            raw = _b64.b64decode(file_data[len("base64://"):])
                        elif file_data.startswith("file://"):
                            p = _Path(file_data[len("file://"):])
                            raw = p.read_bytes() if p.exists() else b""
                        else:
                            p = _Path(file_data)
                            raw = p.read_bytes() if p.exists() else b""
                        if raw:
                            tmp_path = ctx.sticker_store.storage_dir / f"_tmp_mface_{key}.tmp"
                            tmp_path.write_bytes(raw)
                            image_paths.append(str(tmp_path))
                            logger.info(
                                "debug save: mface downloaded | key={} summary={!r} size={}",
                                key, summary, len(raw),
                            )
                    except Exception as e:
                        logger.warning(
                            "debug save: mface download failed | key={} summary={!r} err={}",
                            key, summary, e,
                        )

        raw_str = getattr(cmd_ctx.event, "raw_message", "")
        for m in _re_cq.finditer(r"\[(?:mface|market_face):([^\]]+)\]", raw_str):
            params = dict(p.split("=", 1) for p in m.group(1).split(",") if "=" in p)
            key = params.get("key") or params.get("file_unique") or params.get("id", "")
            summary = params.get("summary", "")
            if key and ctx.sticker_store:
                try:
                    resp = await cmd_ctx.bot.call_api("get_image", file=key)
                    file_data = resp.get("file", "")
                    if file_data.startswith("base64://"):
                        import base64 as _b64
                        raw = _b64.b64decode(file_data[len("base64://"):])
                    elif file_data.startswith("file://"):
                        p = _Path(file_data[len("file://"):])
                        raw = p.read_bytes() if p.exists() else b""
                    else:
                        p = _Path(file_data)
                        raw = p.read_bytes() if p.exists() else b""
                    if raw:
                        tmp_path = ctx.sticker_store.storage_dir / f"_tmp_mface_{key}.tmp"
                        tmp_path.write_bytes(raw)
                        image_paths.append(str(tmp_path))
                        logger.info(
                            "debug save: mface from raw_message | key={} summary={!r} size={}",
                            key, summary, len(raw),
                        )
                except Exception as e:
                    logger.warning(
                        "debug save: mface raw_message download failed | key={} err={}",
                        key, e,
                    )
        logger.info("debug save: segment scan | types={} image_count={}", _seg_types, len(image_paths))

        # 2. Fall back to timeline if no images in current message
        if not image_paths:
            group_id = str(cmd_ctx.group_id) if cmd_ctx.group_id else None
            if group_id and ctx.timeline:
                for msg in reversed(ctx.timeline.get_pending(group_id)):
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "image_ref":
                                p = block.get("path")
                                if p:
                                    image_paths.append(p)
                                    break
                    if image_paths:
                        break
                if not image_paths:
                    for turn in reversed(ctx.timeline.get_turns(group_id)):
                        content = turn.get("content", "")
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "image_ref":
                                    p = block.get("path")
                                    if p:
                                        image_paths.append(p)
                                        break
                        if image_paths:
                            break

        if not image_paths:
            await cmd_ctx.bot.send(
                cmd_ctx.event,
                Message("未找到图片（请先发送图片再使用此命令，或将图片与命令放在同一条消息中）"),
            )
            return

        user_desc = cmd_ctx.args.strip()

        tool_ctx_obj = ToolContext(
            bot=cmd_ctx.bot,
            user_id=str(cmd_ctx.user_id),
            group_id=str(cmd_ctx.group_id) if cmd_ctx.group_id else None,
        )
        results: list[str] = []
        for idx, image_path in enumerate(image_paths):
            tool_ctx_obj.extra["image_tags"] = {f"img:{idx + 1}": image_path}
            image_data = _Path(image_path).read_bytes()

            description: str | None = None
            usage_hint = "通用聊天表情"

            vision_client = ctx.vision_client
            if vision_client is not None:
                if idx > 0:
                    await asyncio.sleep(1.5)
                try:
                    desc = await vision_client.describe_image(image_data)
                    if desc:
                        description = desc
                        usage_hint = desc
                        logger.info("debug save_sticker vision desc | path={} desc={!r}", image_path, desc)
                except Exception as e:
                    logger.warning("debug save_sticker vision failed | path={} err={}", image_path, e)

            if description is None:
                description = "通用聊天表情"

            if user_desc:
                description = f"{user_desc}。{description}"
                usage_hint = user_desc

            tool = SaveStickerTool(store, superusers)
            result = await tool.execute(
                tool_ctx_obj,
                image_tag=f"img:{idx + 1}",
                description=description,
                usage_hint=usage_hint,
                requested_by=str(cmd_ctx.user_id),
            )
            results.append(result)
            logger.info("debug direct save_sticker | path={} result={}", image_path, result)

        summary = f"已处理 {len(image_paths)} 张图片：\n" + "\n".join(f"  {r}" for r in results)
        await cmd_ctx.bot.send(cmd_ctx.event, Message(summary))

    async def _handle_debug_send(self, cmd_ctx: Any) -> None:
        """Handle /debug send — send a sticker by ID or at random."""
        import random as _random
        import re as _re

        from nonebot.adapters.onebot.v11 import Message

        from services.tools.context import ToolContext
        from services.tools.sticker_tools import SendStickerTool

        ctx = self._ctx
        if ctx is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("系统未就绪"))
            return
        if cmd_ctx.user_id not in ctx.admins:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("无权限"))
            return

        store = ctx.sticker_store
        if store is None or not store.list_all():
            await cmd_ctx.bot.send(cmd_ctx.event, Message("表情包库为空，无法发送"))
            return

        args = cmd_ctx.args.strip()
        tool_ctx_obj = ToolContext(
            bot=cmd_ctx.bot,
            user_id=str(cmd_ctx.user_id),
            group_id=str(cmd_ctx.group_id) if cmd_ctx.group_id else None,
        )

        # stk_id in args → send specific
        match = _re.search(r"stk_[a-f0-9]{8}", args)
        if match:
            stk_id = match.group(0)
            tool = SendStickerTool(store)
            result = await tool.execute(tool_ctx_obj, sticker_id=stk_id)
            logger.info("debug direct send_sticker (by id) | id={} result={}", stk_id, result)
            await cmd_ctx.bot.send(cmd_ctx.event, Message(f"[send_sticker] {result}"))
            return

        # Filter by format
        want_gif = any(kw in args for kw in ("gif", "GIF", "动图", "动态"))
        all_stickers = store.list_all()

        if want_gif:
            candidates = {sid: e for sid, e in all_stickers.items() if e.get("file", "").endswith(".gif")}
            if not candidates:
                await cmd_ctx.bot.send(cmd_ctx.event, Message("库中没有动图表情包"))
                return
        else:
            candidates = all_stickers

        stk_id = _random.choice(list(candidates.keys()))
        tool = SendStickerTool(store)
        result = await tool.execute(tool_ctx_obj, sticker_id=stk_id)
        logger.info("debug direct send_sticker | id={} result={}", stk_id, result)
        await cmd_ctx.bot.send(cmd_ctx.event, Message(f"[send_sticker] {result}"))

    async def _handle_debug_split(self, cmd_ctx: Any) -> None:
        """Handle /debug split — test _split_naturally on arbitrary text."""
        from nonebot.adapters.onebot.v11 import Message

        from services.llm.client import _split_naturally

        text = cmd_ctx.args.strip()
        if not text:
            await cmd_ctx.bot.send(
                cmd_ctx.event,
                Message("用法: /debug split <文本>\n示例: /debug split 感觉像在看超高清的童话舞台剧！"),
            )
            return

        segments = _split_naturally(text)
        output = f"输入: {text}\n分段数: {len(segments)}\n---\n"
        for i, seg in enumerate(segments, 1):
            output += f"[{i}] {seg}\n"

        logger.info("debug split | input_len={} segments={}", len(text), len(segments))
        await cmd_ctx.bot.send(cmd_ctx.event, Message(output.strip()))

    async def on_startup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        config: BotConfig = ctx.config

        # ---- config-derived globals ----
        ctx.bot_start_time = time.time()

        # Prefer NoneBot's nickname config (NICKNAME env var), fall back to BOT_NICKNAMES.
        nb_nicknames = nonebot.get_driver().config.nickname
        if nb_nicknames:
            ctx.bot_nicknames = list(nb_nicknames)
        else:
            import json as _json
            raw = os.environ.get("BOT_NICKNAMES", "[]")
            try:
                ctx.bot_nicknames = _json.loads(raw)
            except _json.JSONDecodeError:
                ctx.bot_nicknames = []

        ctx.allowed_groups = set(config.group.allowed_groups)
        ctx.allowed_private_users = set(config.allowed_private_users)
        ctx.admins = dict(config.admins)

        # ---- anti-detect / humanizer ----
        from services.humanizer import Humanizer, set_humanizer

        humanizer = Humanizer(
            enabled=config.anti_detect.enabled,
            min_delay=config.anti_detect.min_delay,
            max_delay=config.anti_detect.max_delay,
            char_delay=config.anti_detect.char_delay,
        )
        set_humanizer(humanizer)
        ctx.humanizer = humanizer

        # ---- vision / image cache ----
        from services.media.image_cache import ImageCache

        image_cache = ImageCache(
            cache_dir=config.vision.cache_dir,
            max_dimension=config.vision.max_dimension,
        )
        await image_cache.cleanup(max_age=timedelta(hours=config.vision.cache_max_age_hours))
        ctx.image_cache = image_cache
        ctx.vision_enabled = config.vision.enabled
        ctx.max_images_per_message = config.vision.max_images_per_message

        # ---- sticker store ----
        from plugins.sticker import StickerConfig

        sticker_cfg = load_plugin_config("plugins/sticker.toml", StickerConfig)
        if sticker_cfg.enabled:
            from services.media.sticker_store import StickerStore
            ctx.sticker_store = StickerStore(
                storage_dir=sticker_cfg.storage_dir,
                max_count=sticker_cfg.max_count,
            )
        else:
            ctx.sticker_store = None

        # ---- card store ----
        from plugins.memo import MemoConfig
        from services.memory.card_store import CardStore

        memo_cfg = load_plugin_config("plugins/memo.toml", MemoConfig)
        card_store = CardStore(db_path="storage/memory_cards.db")
        await card_store.init(migrate_from_md=memo_cfg.dir)
        ctx.card_store = card_store

        # ---- short term memory ----
        from services.memory.short_term import ShortTermMemory

        ctx.short_term = ShortTermMemory()

        # ---- identity ----
        from services.identity import IdentityManager

        identity_mgr = IdentityManager()
        soul_dir = config.soul.dir
        await identity_mgr.load_file(f"{soul_dir}/identity.md")
        ctx.identity_mgr = identity_mgr
        ctx.identity = identity_mgr.resolve()

        # ---- schedule system ----
        from plugins.schedule.plugin import ScheduleConfig

        schedule_cfg = load_plugin_config("plugins/schedule/plugin.toml", ScheduleConfig)
        if schedule_cfg.enabled:
            from plugins.schedule import MoodEngine, ScheduleGenerator, ScheduleStore

            ctx.schedule_store = ScheduleStore(storage_dir=schedule_cfg.storage_dir)
            await ctx.schedule_store.startup()
            ctx.mood_engine = MoodEngine(
                anomaly_chance=schedule_cfg.mood_anomaly_chance,
                refresh_minutes=schedule_cfg.mood_refresh_minutes,
            )
            ctx.schedule_gen = ScheduleGenerator(
                store=ctx.schedule_store,
                generate_at_hour=schedule_cfg.generate_at_hour,
                identity_name=ctx.identity.name,
            )
            from plugins.schedule.calendar import set_self_name
            set_self_name(ctx.identity.name)
            _L.info("schedule system initialized | dir={}", schedule_cfg.storage_dir)
        else:
            ctx.schedule_store = None
            ctx.mood_engine = None
            ctx.schedule_gen = None
        ctx.schedule_enabled = schedule_cfg.enabled

        # ---- affection system ----
        from plugins.affection.plugin import AffectionConfig

        affection_cfg = load_plugin_config("plugins/affection/plugin.toml", AffectionConfig)
        if affection_cfg.enabled:
            from plugins.affection import AffectionEngine, AffectionStore

            ctx.affection_store = AffectionStore(storage_dir=affection_cfg.storage_dir)
            await ctx.affection_store.startup()
            ctx.affection_engine = AffectionEngine(
                store=ctx.affection_store,
                score_increment=affection_cfg.score_increment,
                daily_cap=affection_cfg.daily_cap,
            )
            _L.info("affection system initialized | dir={}", affection_cfg.storage_dir)
        else:
            ctx.affection_store = None
            ctx.affection_engine = None
        ctx.affection_enabled = affection_cfg.enabled

        # ---- message log ----
        from services.memory.message_log import MessageLog

        message_log = MessageLog(db_path="storage/messages.db")
        await message_log.init()
        ctx.msg_log = message_log

        # ---- timeline ----
        from services.memory.timeline import GroupTimeline

        ctx.timeline = GroupTimeline(message_log=message_log)

        # ---- state board ----
        from services.memory.state_board import GroupStateBoard

        ctx.state_board = GroupStateBoard(message_log=message_log, bot_self_id="")

        # ---- retrieval gate ----
        from services.memory.retrieval import RetrievalGate

        ctx.retrieval = RetrievalGate(card_store=card_store, refresh_interval=10)

        # ---- instruction ----
        from services.llm.prompt_builder import load_instruction

        instruction = load_instruction(config.soul.dir)

        # ---- prompt builder ----
        from services.llm.prompt_builder import PromptBuilder

        prompt_builder = PromptBuilder(
            instruction=instruction,
            admins=config.admins,
            state_board=ctx.state_board,
            retrieval_gate=ctx.retrieval,
        )
        prompt_builder.build_static(ctx.identity, bot_self_id="")
        ctx.prompt_builder = prompt_builder

        # ---- dream agent (created by DreamPlugin) ----
        from plugins.dream import DreamConfig

        dream_cfg = load_plugin_config("plugins/dream.toml", DreamConfig)
        ctx.dream = None
        ctx.dream_enabled = dream_cfg.enabled

        # ---- usage tracker ----
        from services.llm.usage import UsageTracker

        usage_tracker = UsageTracker(db_path="storage/usage.db")
        if config.llm.usage.enabled:
            await usage_tracker.init()
        ctx.usage_tracker = usage_tracker

        # ---- tool registry ----
        from services.tools.registry import ToolRegistry

        tools = ToolRegistry()
        # Tools are registered by individual plugins via bus.collect_tools()
        ctx.tool_registry = tools

        # ---- LLM client ----
        from services.llm.client import LLMClient

        llm = LLMClient(
            base_url=config.llm.base_url,
            api_key=config.llm.api_key,
            model=config.llm.model,
            prompt_builder=prompt_builder,
            short_term=ctx.short_term,
            tools=tools,
            max_context_tokens=config.llm.context.max_context_tokens,
            compact_ratio=config.compact.ratio,
            compress_ratio=config.compact.compress_ratio,
            max_compact_failures=config.compact.max_failures,
            group_timeline=ctx.timeline,
            card_store=card_store,
            on_compact=None,
            image_cache=image_cache if ctx.vision_enabled else None,
            message_log=message_log,
            affection_engine=ctx.affection_engine,
            thinker_enabled=config.thinker.enabled,
            thinker_max_tokens=config.thinker.max_tokens,
            bus=ctx.bus,
        )

        # ---- memo extractor ----
        from plugins.memo import MemoExtractor
        memo_extractor = MemoExtractor(
            card_store=card_store,
            api_call=llm._call,
        )
        ctx.memo_extractor = memo_extractor
        if config.llm.usage.enabled:
            llm._usage_tracker = usage_tracker

        ctx.llm_client = llm

        # ---- usage API routes ----
        if config.llm.usage.enabled:
            from services.llm.usage_routes import create_usage_router
            app = nonebot.get_app()
            app.include_router(create_usage_router(usage_tracker))

        # ---- desc cache (for vision) ----
        ctx.desc_cache: dict[str, str] = {}

        # ---- scheduler ----
        from services.scheduler import GroupChatScheduler
        from services.talk_schedule import TalkSchedule

        talk_schedule = TalkSchedule("config/talk_schedule.json")

        ctx.scheduler = GroupChatScheduler(
            llm=llm,
            timeline=ctx.timeline,
            identity_mgr=identity_mgr,
            group_config=config.group,
            humanizer=humanizer,
            talk_schedule=talk_schedule,
            mood_getter=(
                lambda: ctx.mood_engine.evaluate(
                    ctx.schedule_store.current if ctx.schedule_store else None,
                )
                if ctx.mood_engine
                else None
            ),
        )

        _L.info("ChatPlugin startup complete")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if ctx.schedule_gen is not None:
            await ctx.schedule_gen.stop()
        if ctx.llm_client is not None:
            await ctx.llm_client.close()
        if ctx.scheduler is not None:
            await ctx.scheduler.close()
        if ctx.msg_log is not None:
            await ctx.msg_log.close()
        if ctx.usage_tracker is not None:
            await ctx.usage_tracker.close()
        _L.info("ChatPlugin shutdown complete")
