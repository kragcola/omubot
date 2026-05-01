"""MemoPlugin: 记忆卡片系统。

通过 on_pre_prompt 注入全局索引和实体卡片到 system prompt，
通过 on_post_reply 在回复后提取新记忆。
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger

from kernel.types import (
    AmadeusPlugin,
    PluginContext,
    PromptContext,
    ReplyContext,
)
from services.tools.base import Tool

_L = logger.bind(channel="system")


def _content_text(content: object) -> str:
    """Extract plain text from a Content value."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return " ".join(parts)
    return str(content)


class MemoPlugin(AmadeusPlugin):
    name = "memo"
    description = "记忆系统：卡片索引、实体记忆注入、对话后提取"
    version = "1.0.0"
    priority = 30

    def __init__(self) -> None:
        super().__init__()
        self._card_store = None
        self._retrieval = None
        self._memo_extractor = None
        self._pending_extractions: set[asyncio.Task[None]] = set()
        # Cache for global index (TTL-based)
        self._index_cache: tuple[str, float] | None = None
        self._index_ttl: float = 300.0  # 5 minutes

    async def on_startup(self, ctx: PluginContext) -> None:
        self._card_store = ctx.card_store
        self._retrieval = ctx.retrieval
        self._memo_extractor = ctx.memo_extractor

    def register_tools(self) -> list[Tool]:
        if self._card_store is None:
            return []
        from services.tools.memo_tools import CardLookupTool, CardUpdateTool
        return [CardLookupTool(self._card_store), CardUpdateTool(self._card_store)]

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if self._card_store is None:
            return

        # Global index (stable — rarely changes)
        index_text = await self._build_global_index()
        if index_text:
            ctx.add_block(
                text=index_text,
                label="全局索引",
                position="stable",
            )

        # Entity memo (dynamic — per-session gating)
        if self._retrieval is not None and ctx.session_id:
            memo_text = await self._retrieval.build_memo_block(
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                group_id=ctx.group_id,
                conversation_text=ctx.conversation_text,
            )
        elif self._card_store is not None:
            if ctx.group_id:
                body = await self._card_store.build_entity_prompt("group", ctx.group_id)
                memo_text = f"【当前在群 #{ctx.group_id} 中对话】\n{body}"
            else:
                body = await self._card_store.build_entity_prompt("user", ctx.user_id)
                memo_text = f"【当前私聊 @{ctx.user_id}】\n{body}"
        else:
            memo_text = ""

        if memo_text:
            ctx.add_block(
                text=memo_text,
                label="记忆卡片",
                position="dynamic",
            )

    async def on_post_reply(self, ctx: ReplyContext) -> None:
        if self._memo_extractor is None or not ctx.user_id or ctx.user_id == "0":
            return
        task = asyncio.create_task(
            self._memo_extractor.extract_after_turn(
                user_id=ctx.user_id,
                group_id=ctx.group_id,
                user_msg="",  # User message text not available in ReplyContext
                bot_reply=ctx.reply_content,
            )
        )
        self._pending_extractions.add(task)
        task.add_done_callback(self._pending_extractions.discard)

    async def _build_global_index(self) -> str:
        """Build the global card index with a short-lived in-memory cache."""
        now = time.monotonic()
        if self._index_cache is not None:
            cached_text, cached_at = self._index_cache
            if now - cached_at < self._index_ttl:
                return cached_text
        if self._card_store is None:
            return ""
        index_text = await self._card_store.build_global_index()
        self._index_cache = (index_text, now)
        return index_text
