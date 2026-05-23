"""ChatPlugin: core chat functionality — priority 0, uninstallable.

Owns all system service lifecycle. Other plugins access services via PluginContext.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import timedelta
from typing import Any

import nonebot
from loguru import logger

from kernel.config import BotConfig, load_plugin_config
from kernel.types import AmadeusPlugin, PluginContext
from services.llm.llm_request import LLMRequest

_L = logger.bind(channel="system")

_DEBUG_INTERNAL_PHRASES = (
    "pass_turn",
    "[pass_turn]",
    "结束本轮",
    "内部原因",
    "本轮未执行工具",
    "无法匹配任何工具",
)


def _sanitize_debug_reply(text: str, *, fallback: str = "这次没有执行到可用工具。") -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"(?i)\[?\s*pass[_\s-]*turn\s*\]?", "", cleaned)
    replacements = {
        "结束本轮": "",
        "内部原因": "原因",
        "本轮未执行工具": "这次没有执行到可用工具",
        "无法匹配任何工具": "现在没有合适的工具可用",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。；;：:、")
    cleaned = re.sub(r"([。！？!?]){2,}", r"\1", cleaned)
    cleaned = cleaned.strip()
    if cleaned == "这次没有执行到可用工具":
        return fallback
    if not cleaned:
        return fallback
    return cleaned


class ChatPlugin(AmadeusPlugin):
    name = "chat"
    version = "1.1.7"
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
                admin_only=True,
                hidden=True,
                passthrough_unknown=True,  # /debug <text> sends to LLM
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
                        require_args=True,
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

        logger.info(
            "debug mode | user={} {}",
            cmd_ctx.user_id,
            "private" if cmd_ctx.is_private else f"group={cmd_ctx.group_id}",
        )

        sid = f"private_{cmd_ctx.user_id}" if cmd_ctx.is_private else f"group_{cmd_ctx.group_id}"
        user_content = cmd_ctx.args if cmd_ctx.args else "请显示当前系统状态摘要"
        has_command = bool(cmd_ctx.args)

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
                    "如果没有合适工具，就直接用自然语言说明当前做不到，并给出最短解释；不要描述内部流程。"
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
                    debug_request = LLMRequest(
                        task="chat_private",
                        user_id=str(cmd_ctx.user_id or ""),
                        group_id=None if cmd_ctx.is_private else str(cmd_ctx.group_id or ""),
                        static_blocks=list(system_blocks),
                        user_messages=list(messages),
                        tools=tool_defs,
                        max_tokens=1024,
                        requires_capabilities=("chat", "tools"),
                    )
                    result = await asyncio.wait_for(
                        ctx.llm_client._call(debug_request),
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
                        reply_text = _sanitize_debug_reply(
                            text.strip() or reason,
                            fallback="这次没有执行到可用工具。",
                        )
                        _L.info("debug pass_turn | reason={!r}", reason)
                        await ctx.humanizer.delay(reply_text)
                        await cmd_ctx.bot.send(cmd_ctx.event, Message(f"调试：{reply_text}"))
                        return

                if not tool_uses:
                    reply_text = _sanitize_debug_reply(
                        _strip_markdown(text or ""),
                        fallback="调试：这次没有产出可展示的结果。",
                    )
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
            final_request = LLMRequest(
                task="chat_private",
                user_id=str(cmd_ctx.user_id or ""),
                group_id=None if cmd_ctx.is_private else str(cmd_ctx.group_id or ""),
                static_blocks=list(system_blocks),
                user_messages=list(messages),
                max_tokens=1024,
                requires_capabilities=("chat",),
            )
            result = await asyncio.wait_for(
                ctx.llm_client._call(final_request),
                timeout=60.0,
            )
            reply_text = _sanitize_debug_reply(
                _strip_markdown(result.get("text") or ""),
                fallback="调试：这次没有产出可展示的结果。",
            )
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
        """Handle /debug split — test reply segmentation on arbitrary text."""
        from nonebot.adapters.onebot.v11 import Message

        from services.llm.segmentation import ReplySegmentationConfig, segment_reply

        text = cmd_ctx.args.strip()
        cfg = getattr(getattr(self._ctx, "config", None), "reply_segmentation", None)
        if not isinstance(cfg, ReplySegmentationConfig):
            cfg = ReplySegmentationConfig()
        result = segment_reply(text, cfg)
        lines = [
            f"输入: {text}",
            f"分段数: {len(result.segments)}",
            f"策略: {result.strategy}",
            f"切分原因: {', '.join(result.break_reasons) if result.break_reasons else '无'}",
            "---",
        ]
        for i, seg in enumerate(result.segments, 1):
            lines.append(f"[{i}] {seg.text}（{seg.reason}）")

        logger.info("debug split | input_len={} segments={}", len(text), len(result.segments))
        await cmd_ctx.bot.send(cmd_ctx.event, Message("\n".join(lines).strip()))

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

        sticker_cfg = load_plugin_config("plugins/sticker/config.default.json", StickerConfig)
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

        memo_cfg = load_plugin_config("plugins/memo/config.default.json", MemoConfig)
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
        identity_path = f"{soul_dir}/identity.md"
        await identity_mgr.load_file(identity_path)
        ctx.identity_mgr = identity_mgr
        ctx.identity = identity_mgr.resolve()

        # ---- schedule system ----
        from plugins.schedule.plugin import ScheduleConfig

        schedule_cfg = load_plugin_config("plugins/schedule/config.default.json", ScheduleConfig)
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

        affection_cfg = load_plugin_config("plugins/affection/config.default.json", AffectionConfig)
        if affection_cfg.enabled:
            from plugins.affection import AffectionEngine, AffectionStore

            ctx.affection_store = AffectionStore(storage_dir=affection_cfg.storage_dir)
            await ctx.affection_store.startup()
            ctx.affection_engine = AffectionEngine(
                store=ctx.affection_store,
                score_increment=affection_cfg.score_increment,
                daily_cap=affection_cfg.daily_cap,
            )
            if ctx.group_memory_config is not None:
                ctx.affection_engine.set_group_memory_config(ctx.group_memory_config)
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

        # ---- group memory config ----
        from kernel.config import GroupMemoryConfig

        group_memory_config = GroupMemoryConfig.load("config/group-memory.json")
        ctx.group_memory_config = group_memory_config
        _L.info("group memory config loaded | mode={}", group_memory_config.memory.mode)

        # ---- retrieval gate ----
        from services.memory.retrieval import RetrievalGate

        ctx.retrieval = RetrievalGate(
            card_store=card_store,
            refresh_interval=10,
            group_memory_config=group_memory_config,
            semantic_enabled=config.memory.semantic.enabled,
            semantic_backend=config.memory.semantic.backend,
        )

        # Unified context search for Admin debugging and ContextPlugin prompt takeover.
        from services.context import ContextService

        ctx.context_service = ContextService.from_runtime(ctx, bus=ctx.bus)

        # ---- derived knowledge graph (safe, rebuildable fact layer) ----
        from services.knowledge_graph import KnowledgeGraphService

        ctx.knowledge_graph = KnowledgeGraphService("storage/knowledge_graph.db")
        await ctx.knowledge_graph.init()

        # Phase E.4 graph edge double-write — mirror doc-backed facts to
        # `doc_supports_fact` edges. Best-effort: a graph write failure
        # must never block the fact governance path (audit § E.4).
        try:
            from services.knowledge_graph.fact_graph_bridge import FactGraphBridge
            from services.knowledge_graph.graph_writer import GraphWriter

            kg_store = getattr(ctx.knowledge_graph, "_store", None)
            if kg_store is not None and getattr(kg_store, "_db", None) is not None:
                ctx.fact_graph_bridge = FactGraphBridge(GraphWriter(kg_store))
                ctx.fact_graph_bridge.attach(ctx.knowledge_graph)
        except Exception as exc:
            logger.warning("fact graph bridge attach failed | err={}", exc)

        # ---- memory consolidator (Phase C dry-run; lazy LLM/normalizer wiring) ----
        from services.episodic.store import EpisodeStore
        from services.learning_normalizer.store import LearningNormalizerStore
        from services.memory_consolidator import (
            ConsolidatorCandidatesStore,
            EpisodePromoter,
            MemoryConsolidator,
            ReflectionGenerator,
        )

        ctx.memory_consolidator_store = ConsolidatorCandidatesStore(
            "storage/consolidator_candidates.db"
        )
        await ctx.memory_consolidator_store.init()
        ctx.memory_consolidator_normalizer = LearningNormalizerStore(
            "storage/consolidator_normalizer.db"
        )
        await ctx.memory_consolidator_normalizer.init()

        # Phase D singleton — episode store + promote bridge.
        ctx.episode_store = EpisodeStore("storage/episodic.db")
        await ctx.episode_store.init()
        ctx.episode_promoter = EpisodePromoter(
            candidates_store=ctx.memory_consolidator_store,
            episode_store=ctx.episode_store,
        )
        # D.5 graph edge double-write: approved/disabled episodes mirror
        # into knowledge_graph.db as episode_supports_profile edges.
        # Best-effort — graph mirroring failure must never block the
        # episode state machine (audit § D.5).
        try:
            from services.episodic import EpisodeGraphBridge
            from services.knowledge_graph.graph_writer import GraphWriter

            kg_store = getattr(ctx.knowledge_graph, "_store", None)
            if kg_store is not None and getattr(kg_store, "_db", None) is not None:
                ctx.episode_graph_bridge = EpisodeGraphBridge(GraphWriter(kg_store))
                ctx.episode_graph_bridge.attach(ctx.episode_store)
        except Exception as exc:
            logger.warning("episode graph bridge attach failed | err={}", exc)
        ctx.memory_consolidator = None  # set after llm_client is built below

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

        dream_cfg = load_plugin_config("plugins/dream/config.default.json", DreamConfig)
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

        # ---- block trace + budget manager ----
        from services.block_trace.budget_manager import PromptBudgetManager
        from services.block_trace.store import BlockTraceStore

        trace_store = BlockTraceStore(db_path="storage/block_trace.db")
        await trace_store.init()
        ctx.block_trace_store = trace_store
        budget_mgr = PromptBudgetManager(
            trace_store,
            slang_store_getter=lambda: getattr(ctx, "slang_store", None),
            style_store_getter=lambda: getattr(ctx, "style_store", None),
            episode_store_getter=lambda: getattr(ctx, "episode_store", None),
        )

        # ---- LLM client ----
        from services.llm.client import LLMClient

        llm_tasks = ("main", "thinker", "compact", "slang", "vision")
        task_profiles = {
            task: config.llm.resolve_task_profile(task)
            for task in llm_tasks
        }
        task_profile_names = {
            task: config.llm.profile_name_for_task(task)
            for task in llm_tasks
        }
        main_profile = task_profiles["main"]
        llm = LLMClient(
            base_url=main_profile.base_url,
            api_key=main_profile.api_key,
            model=main_profile.model,
            prompt_builder=prompt_builder,
            short_term=ctx.short_term,
            tools=tools,
            api_format=main_profile.api_format or config.llm.api_format,
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
            mood_getter=(
                lambda: ctx.mood_engine.evaluate(
                    ctx.schedule_store.current if ctx.schedule_store else None,
                )
                if ctx.mood_engine
                else None
            ),
            bus=ctx.bus,
            task_profiles=task_profiles,
            group_config=config.group,
            slang_store_getter=lambda: getattr(ctx, "slang_store", None),
            budget_manager=budget_mgr,
        )
        llm.set_task_profile_names(task_profile_names)

        # ---- prompt provider bus (active mode — providers are sole injection path) ----
        from plugins.style.plugin import StyleConfig as _StyleConfig
        from services.block_trace.episode_provider import EpisodeProvider
        from services.block_trace.provider_bus import PromptProviderBus
        from services.block_trace.slang_provider import SlangProvider
        from services.block_trace.style_provider import StyleProvider

        style_cfg = load_plugin_config("plugins/style/config.default.json", _StyleConfig)
        style_global_groups = {
            str(gid).strip()
            for gid in style_cfg.global_enabled_group_ids
            if str(gid).strip()
        }

        provider_bus = PromptProviderBus(trace_store)
        provider_bus.mode = "active"
        provider_bus.register(SlangProvider(
            store_getter=lambda: getattr(ctx, "slang_store", None),
            group_config=config.group,
        ))
        provider_bus.register(StyleProvider(
            store_getter=lambda: getattr(ctx, "style_store", None),
            enabled=style_cfg.enabled,
            profile_enabled=style_cfg.profile_enabled,
            profile_max_chars=style_cfg.profile_max_chars,
            max_items=style_cfg.max_items,
            max_chars=style_cfg.max_chars,
            min_confidence=style_cfg.min_confidence,
            global_enabled_groups=style_global_groups,
        ))
        # D.4 episode recall — only ``enabled_for_prompt`` reflections
        # surface, ranked below slang/style so the budget manager trims
        # them first under pressure.
        provider_bus.register(EpisodeProvider(
            store_getter=lambda: getattr(ctx, "episode_store", None),
            top_k=3,
            enabled=True,
        ))
        llm.set_provider_bus(provider_bus)
        ctx.provider_bus = provider_bus

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

        # PR2 (2026-05-21): wire LLMClient into KnowledgeGraphService for the
        # LLM-driven graph extractor. Prior to this, the regex MVP extractor
        # leaked Chinese conjunctions/adverbs into the candidate queue. The
        # service was constructed earlier (before LLMClient existed); we
        # late-bind here so on_pre_prompt extraction routes through LLM.
        if getattr(ctx, "knowledge_graph", None) is not None:
            ctx.knowledge_graph.attach_llm_client(llm)

        # Now that llm_client + msg_log are wired, attach MemoryConsolidator.
        ctx.memory_consolidator = MemoryConsolidator(
            store=ctx.memory_consolidator_store,
            archive=ctx.msg_log,
            normalizer=ctx.memory_consolidator_normalizer,
            llm_client=llm,
        )

        # D.3 reflection generator — style/slang stores wire late
        # (StylePlugin priority=43, SlangPlugin priority=42), so we hand
        # the generator getter callables that defer ctx attribute lookup
        # until run_once() actually fires.
        ctx.reflection_generator = ReflectionGenerator(
            store=ctx.memory_consolidator_store,
            llm_client=llm,
            style_store_getter=lambda: getattr(ctx, "style_store", None),
            slang_store_getter=lambda: getattr(ctx, "slang_store", None),
        )

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
        if ctx.knowledge_graph is not None:
            await ctx.knowledge_graph.close()
        if ctx.msg_log is not None:
            await ctx.msg_log.close()
        if ctx.card_store is not None:
            await ctx.card_store.close()
        if ctx.usage_tracker is not None:
            await ctx.usage_tracker.close()
        block_trace = getattr(ctx, "block_trace_store", None)
        if block_trace is not None:
            await block_trace.close()
        if ctx.card_store is not None:
            await ctx.card_store.close()
        consolidator_store = getattr(ctx, "memory_consolidator_store", None)
        if consolidator_store is not None:
            await consolidator_store.close()
        consolidator_normalizer = getattr(ctx, "memory_consolidator_normalizer", None)
        if consolidator_normalizer is not None:
            await consolidator_normalizer.close()
        episode_store = getattr(ctx, "episode_store", None)
        if episode_store is not None:
            await episode_store.close()
        _L.info("ChatPlugin shutdown complete")
