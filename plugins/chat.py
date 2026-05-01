"""ChatPlugin: core chat functionality — priority 0, uninstallable.

Owns all system service lifecycle. Other plugins access services via PluginContext.
"""

from __future__ import annotations

import os
import time
from datetime import timedelta
from typing import Any

import nonebot
from loguru import logger

from kernel.config import BotConfig
from kernel.types import AmadeusPlugin, PluginContext

_L = logger.bind(channel="system")


class ChatPlugin(AmadeusPlugin):
    name = "chat"
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
            ),
        ]

    async def _handle_debug(self, cmd_ctx: Any) -> None:
        """Handle /debug command: admin-only debug mode with live state."""
        from nonebot.adapters.onebot.v11 import Message

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

        from services.llm.client import _build_debug_block

        sid = f"private_{cmd_ctx.user_id}" if cmd_ctx.is_private else f"group_{cmd_ctx.group_id}"

        # Build the debug system block
        debug_text = await _build_debug_block(
            user_id=cmd_ctx.user_id,
            session_id=sid,
            mood_engine=ctx.mood_engine,
            affection_engine=ctx.affection_engine,
            schedule_store=ctx.schedule_store,
            card_store=ctx.card_store,
            short_term=ctx.short_term,
            message_log=ctx.msg_log,
        )
        user_content = cmd_ctx.args if cmd_ctx.args else "请显示当前系统状态摘要"

        # Single-turn LLM call: no thinker, no tool loop
        system_blocks = [
            {"type": "text", "text": debug_text},
            {"type": "text", "text": (
                "你是调试助手，不是聊天机器人。基于上面的实时状态数据如实回答用户的问题。\n"
                "格式约束：QQ 不支持 Markdown。禁止 ``` 代码块、** 加粗、` 行内代码、- 列表。使用纯文本。"
            )},
        ]
        messages = [{"role": "user", "content": user_content}]

        try:
            result = await ctx.llm_client._call(system_blocks, messages, max_tokens=2048)
            reply_text = (result.get("text") or "").strip()
            if reply_text:
                await ctx.humanizer.delay(reply_text)
                await cmd_ctx.bot.send(cmd_ctx.event, Message(reply_text))
        except Exception:
            logger.exception("debug command LLM call failed")
            await cmd_ctx.bot.send(cmd_ctx.event, Message("调试查询失败，请稍后重试"))

    async def on_startup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        config: BotConfig = ctx.config

        # ---- config-derived globals ----
        ctx.bot_start_time = time.time()

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
        if config.sticker.enabled:
            from services.media.sticker_store import StickerStore
            ctx.sticker_store = StickerStore(
                storage_dir=config.sticker.storage_dir,
                max_count=config.sticker.max_count,
            )
        else:
            ctx.sticker_store = None

        # ---- card store ----
        from services.memory.card_store import CardStore

        card_store = CardStore(db_path="storage/memory_cards.db")
        await card_store.init(migrate_from_md=config.memo.dir)
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
        if config.schedule.enabled:
            from plugins.schedule import MoodEngine, ScheduleGenerator, ScheduleStore

            ctx.schedule_store = ScheduleStore(storage_dir=config.schedule.storage_dir)
            await ctx.schedule_store.startup()
            ctx.mood_engine = MoodEngine(
                anomaly_chance=config.schedule.mood_anomaly_chance,
                refresh_minutes=config.schedule.mood_refresh_minutes,
            )
            ctx.schedule_gen = ScheduleGenerator(
                store=ctx.schedule_store,
                generate_at_hour=config.schedule.generate_at_hour,
                identity_name=ctx.identity.name,
            )
            from plugins.schedule.calendar import set_self_name
            set_self_name(ctx.identity.name)
            _L.info("schedule system initialized | dir={}", config.schedule.storage_dir)
        else:
            ctx.schedule_store = None
            ctx.mood_engine = None
            ctx.schedule_gen = None
        ctx.schedule_enabled = config.schedule.enabled

        # ---- affection system ----
        if config.affection.enabled:
            from plugins.affection import AffectionEngine, AffectionStore

            ctx.affection_store = AffectionStore(storage_dir=config.affection.storage_dir)
            await ctx.affection_store.startup()
            ctx.affection_engine = AffectionEngine(
                store=ctx.affection_store,
                score_increment=config.affection.score_increment,
                daily_cap=config.affection.daily_cap,
            )
            _L.info("affection system initialized | dir={}", config.affection.storage_dir)
        else:
            ctx.affection_store = None
            ctx.affection_engine = None
        ctx.affection_enabled = config.affection.enabled

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
        ctx.dream = None
        ctx.dream_enabled = config.dream.enabled

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

        ctx.scheduler = GroupChatScheduler(
            llm=llm,
            timeline=ctx.timeline,
            identity_mgr=identity_mgr,
            group_config=config.group,
            humanizer=humanizer,
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
