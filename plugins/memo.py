"""MemoPlugin: 记忆卡片系统。

通过 on_pre_prompt 注入全局索引和实体卡片到 system prompt，
通过 on_post_reply 在回复后提取新记忆。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger
from pydantic import BaseModel

from kernel.types import (
    AmadeusPlugin,
    PluginContext,
    PromptContext,
    ReplyContext,
)
from services.memory.card_store import CardStore, NewCard
from services.tools.base import Tool


class MemoConfig(BaseModel):
    """备忘录系统配置。"""

    dir: str = "storage/memories"
    user_max_chars: int = 300
    group_max_chars: int = 500
    index_max_lines: int = 200
    history_enabled: bool = True

_L = logger.bind(channel="system")

_EXTRACT_SYSTEM = """你是一个信息观察助手。分析以下一轮对话，提取关于用户的需要记住的新事实。

规则：
- 只提取事实性信息：称呼偏好、性格特点、兴趣爱好、身份背景、重要观点、关系变化
- 每条一行，格式："[category] 事实描述"
- category 必须是以下之一：
  preference（偏好/喜好/称呼）| boundary（边界/不喜欢的）| relationship（关系/角色）
  | event（发生的重要事情）| promise（承诺/答应了什么）| fact（一般事实/背景/兴趣）| status（当前状态/情绪）
- 不要记流水账（如"用户打了招呼"、"用户问了问题"）
- 不要重复显而易见的信息（如"用户正在和助手聊天"）
- 如果没有值得记录的新信息，输出"无"（就一个字）
- 最多输出3条，宁缺毋滥

示例输出：
[preference] 用户偏好被称呼为"帆酱"
[fact] 用户喜欢玩音游，最近在玩啤酒烧烤
[relationship] 用户是群管理员，负责维护秩序"""



class MemoExtractor:
    """Extract observations after each conversation turn, writing typed cards to CardStore."""

    def __init__(
        self,
        card_store: CardStore,
        api_call: Any,
    ) -> None:
        self._store = card_store
        self._call = api_call

    async def extract_after_turn(
        self,
        user_id: str,
        group_id: str | None,
        user_msg: str,
        bot_reply: str,
    ) -> None:
        """Extract key facts from a turn and write typed cards.

        Runs as a fire-and-forget background task — failures are logged
        but never surface to the user.
        """
        if not user_id or user_id == "0":
            return

        user_msg_clean = user_msg[:300].replace("\n", " ")
        bot_reply_clean = bot_reply[:300].replace("\n", " ")
        conversation = (
            f"用户({user_id}): {user_msg_clean}\n"
            f"助手: {bot_reply_clean}"
        )

        try:
            result = await self._call(
                [{"type": "text", "text": _EXTRACT_SYSTEM}],
                [{"role": "user", "content": conversation}],
                max_tokens=256,
            )
        except Exception:
            _L.debug("memo extractor LLM call failed | user={}", user_id)
            return

        text: str = result.get("text", "").strip()
        if not text or text == "无":
            return

        written = 0
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("[") or "]" not in line:
                continue
            bracket_end = line.index("]")
            category = line[1:bracket_end].strip()
            content = line[bracket_end + 1:].strip()
            if not content or content == "无":
                continue
            try:
                await self._store.add_card(NewCard(
                    category=category,
                    scope="user",
                    scope_id=user_id,
                    content=content,
                    confidence=0.6,
                    source="extractor",
                ))
                written += 1
            except (ValueError, Exception):
                _L.warning("memo extractor add_card failed | user={} category={}", user_id, category)

        if written:
            _L.info(
                "cards extracted | user={} count={}",
                user_id, written,
            )


class MemoPlugin(AmadeusPlugin):
    name = "memo"
    description = "记忆系统：卡片索引、实体记忆注入、对话后提取"
    version = "1.1.2"
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
                user_msg=ctx.user_msg,
                bot_reply=ctx.reply_content,
            )
        )

        scope = "group" if ctx.group_id else "user"
        scope_id = ctx.group_id if ctx.group_id else ctx.user_id

        def _on_extraction_done(t: asyncio.Task[None]) -> None:
            self._pending_extractions.discard(t)
            self._index_cache = None
            if self._retrieval is not None:
                self._retrieval.invalidate_entity(scope, scope_id)

        self._pending_extractions.add(task)
        task.add_done_callback(_on_extraction_done)

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
