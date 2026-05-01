"""HistoryLoaderPlugin: 群聊历史加载。

在 bot 连接后加载群聊历史消息到 timeline。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from kernel.types import AmadeusPlugin, PluginContext

_L = logger.bind(channel="system")


class HistoryLoaderPlugin(AmadeusPlugin):
    name = "history_loader"
    description = "群聊历史加载：bot 连接后回填近期消息"
    version = "1.0.0"
    priority = 5  # Run early, after ChatPlugin but before other business plugins

    async def on_bot_connect(self, ctx: PluginContext, bot: Any) -> None:
        from plugins.history_loader.loader import load_group_history

        try:
            group_list = await bot.get_group_list()
            group_ids = [str(g["group_id"]) for g in group_list]
            allowed = getattr(ctx, "allowed_groups", set())
            if allowed:
                group_ids = [gid for gid in group_ids if int(gid) in allowed]
        except Exception:
            logger.exception("failed to get group list for history loading")
            return

        _L.info("loading history | groups={}", len(group_ids))
        try:
            counts = {
                gid: ctx.config.group.resolve(int(gid)).history_load_count
                for gid in group_ids
            }
            await load_group_history(
                bot=bot,
                group_ids=group_ids,
                timeline=ctx.timeline,
                count=ctx.config.group.history_load_count,
                bot_self_id=bot.self_id,
                image_cache=ctx.image_cache if getattr(ctx, "vision_enabled", False) else None,
                sticker_store=ctx.sticker_store,
                counts=counts,
            )
        except Exception:
            logger.exception("failed to load group history")
