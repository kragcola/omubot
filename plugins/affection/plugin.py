"""AffectionPlugin: 好感度与昵称工具。

好感度引擎由 ChatPlugin 创建（需在 PromptBuilder/LLMClient 之前初始化），
本插件通过 on_pre_prompt / on_post_reply 钩子注入好感度上下文和记录互动。
"""

from __future__ import annotations

from loguru import logger

from kernel.types import (
    AmadeusPlugin,
    PluginContext,
    PromptContext,
    ReplyContext,
)
from services.tools.base import Tool

_L = logger.bind(channel="affection")


class AffectionPlugin(AmadeusPlugin):
    name = "affection"
    description = "好感度系统：关系提示、互动记录、昵称设置"
    version = "1.0.2"
    priority = 10

    def __init__(self) -> None:
        super().__init__()
        self._engine = None

    async def on_startup(self, ctx: PluginContext) -> None:
        self._engine = ctx.affection_engine

    def register_tools(self) -> list[Tool]:
        if self._engine is None:
            return []
        from services.tools.affection_tools import SetNicknameTool
        return [SetNicknameTool(self._engine)]

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if self._engine is None:
            return
        in_group = ctx.group_id is not None and ctx.privacy_mask
        text = self._engine.build_affection_block(ctx.user_id, in_group=in_group)
        if text:
            profile = self._engine._store.get(ctx.user_id)
            tier = profile.tier
            _L.info("user={} tier={} score={:.0f} in_group={}", ctx.user_id, tier, profile.score, in_group)
            ctx.add_block(text=text, label="与当前用户的关系", position="dynamic")

    async def on_post_reply(self, ctx: ReplyContext) -> None:
        if self._engine is None or not ctx.user_id or ctx.user_id == "0":
            return
        profile = await self._engine.record_interaction(ctx.user_id)
        _L.info(
            "user={} score={:.1f} tier={} total={}",
            ctx.user_id, profile.score, profile.tier, profile.total_interactions,
        )
