"""DreamPlugin: 梦境整合代理。

周期性运行 LLM 工具循环，整理记忆卡片和表情包库。
在 bot 连接后启动后台循环，bot 关闭时停止。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from kernel.types import AmadeusPlugin, PluginContext

_L = logger.bind(channel="dream")


class DreamPlugin(AmadeusPlugin):
    name = "dream"
    description = "梦境整合：定期整理记忆卡片、清理表情包库"
    version = "1.0.0"
    priority = 150  # Background task, after business plugins

    def __init__(self) -> None:
        super().__init__()
        self._dream_agent = None
        self._started = False

    async def on_startup(self, ctx: PluginContext) -> None:
        config = ctx.config
        if not config.dream.enabled:
            _L.info("dream disabled in config, skipping")
            return

        from plugins.dream.agent import DreamAgent, setup_dream_logger

        setup_dream_logger(config.log.dir)
        self._dream_agent = DreamAgent(
            store=ctx.card_store,
            interval_hours=config.dream.interval_hours,
            max_rounds=config.dream.max_rounds,
            sticker_store=ctx.sticker_store,
            on_memo_change=lambda: ctx.prompt_builder.invalidate(),
        )

    async def on_bot_connect(self, ctx: PluginContext, bot: Any) -> None:
        if self._dream_agent is None or self._started:
            return
        self._dream_agent.start(ctx.llm_client._call)
        self._started = True
        _L.info("dream agent started")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if self._dream_agent is not None:
            await self._dream_agent.stop()
            _L.info("dream agent stopped")
