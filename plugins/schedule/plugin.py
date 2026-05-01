"""SchedulePlugin: 日程与心情系统。

通过 on_pre_prompt 注入当前时间和心情到 system prompt。
通过 on_bot_connect 启动日程生成器后台循环。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from kernel.types import AmadeusPlugin, PluginContext, PromptContext

_L = logger.bind(channel="mood")
_L_sys = logger.bind(channel="system")


class SchedulePlugin(AmadeusPlugin):
    name = "schedule"
    description = "日程与心情：时间感知、心情注入、每日日程生成"
    version = "1.0.0"
    priority = 20

    def __init__(self) -> None:
        super().__init__()
        self._mood_engine = None
        self._schedule_store = None
        self._schedule_gen = None
        self._schedule_started = False

    async def on_startup(self, ctx: PluginContext) -> None:
        self._mood_engine = ctx.mood_engine
        self._schedule_store = ctx.schedule_store
        self._schedule_gen = ctx.schedule_gen

    async def on_bot_connect(self, ctx: PluginContext, bot: Any) -> None:
        if not ctx.schedule_enabled or self._schedule_gen is None:
            return

        self._schedule_gen.start(ctx.llm_client._call)

        if not self._schedule_started:
            self._schedule_started = True
            if self._schedule_store is not None:
                today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
                loaded = self._schedule_store.load(today)
                if loaded is None:
                    _L_sys.info("today's schedule missing, generating now...")
                    await self._schedule_gen.ensure_today(ctx.llm_client._call)

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if self._schedule_gen is not None:
            await self._schedule_gen.stop()
            _L.info("schedule generator stopped")

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if self._mood_engine is None or self._schedule_store is None:
            return
        text = self._mood_engine.build_mood_block(
            self._schedule_store.current,
        )
        if text:
            profile = self._mood_engine._cache
            if profile is not None:
                p, _ = profile
                _L.info(
                    "label={} energy={:.2f} valence={:+.2f} openness={:.2f} tension={:.2f}{}",
                    p.label, p.energy, p.valence, p.openness, p.tension,
                    f" anomaly={p.anomaly_reason!r}" if p.anomaly_reason else "",
                )
            ctx.add_block(text=text, label="当前时间", position="dynamic")
