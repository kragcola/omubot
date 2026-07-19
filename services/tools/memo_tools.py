"""Card tools: CardLookupTool and CardUpdateTool for typed memory card operations."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from services.memory.card_store import Card, CardStore, NewCard
from services.tools.base import Tool
from services.tools.context import ToolContext

_L = logger.bind(channel="debug")

_CATEGORY_HELP = (
    "preference（偏好）| boundary（边界）| relationship（关系）| "
    "event（事件）| promise（承诺）| fact（事实）| status（状态）"
)


class CardLookupTool(Tool):
    """Query memory cards by entity or keyword search."""

    def __init__(self, store: CardStore) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "lookup_cards"

    @property
    def description(self) -> str:
        return (
            "查询当前用户、当前群组或全局记忆卡片。"
            "scope=user 仅限当前用户，scope=group 仅限当前群，"
            "scope=global 时 scope_id 必须为 global；"
            "也可用 query 在这些可见作用域中搜索。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["user", "group", "global"],
                    "description": "卡片作用域",
                },
                "scope_id": {
                    "type": "string",
                    "description": (
                        "当前用户 QQ号（user）、当前群号（group）或 global（global）"
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "关键词搜索卡片内容，与 scope/scope_id 互斥",
                },
                "category": {
                    "type": "string",
                    "description": f"按类别过滤：{_CATEGORY_HELP}",
                },
            },
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        scope: str | None = kwargs.get("scope")
        scope_id: str | None = kwargs.get("scope_id")
        query: str | None = kwargs.get("query")
        category: str | None = kwargs.get("category")

        if query is not None:
            cards = await self._search_visible_cards(ctx, query)
            if not cards:
                return f"未找到匹配 '{query}' 的卡片。"
            lines = [f"搜索 '{query}' 结果 ({len(cards)} 条):"]
            for c in cards:
                lines.append(f"  [{c.category}] {c.content} (confidence={c.confidence:.0%})")
            return "\n".join(lines)

        if scope is not None and scope_id is not None:
            scope_text = str(scope).strip()
            scope_id_text = str(scope_id).strip()
            if not self._scope_allowed(ctx, scope_text, scope_id_text):
                return "无权查询该记忆作用域。"
            cards = await self._store.get_entity_cards(
                scope_text,
                scope_id_text,
                category=category,
            )
            if not cards:
                label = (
                    "用户"
                    if scope_text == "user"
                    else "群"
                    if scope_text == "group"
                    else "全局"
                )
                return f"【{label}记忆 / {scope_id_text}】\n暂无记录"
            lines = [
                f"【{'用户' if scope_text == 'user' else '群' if scope_text == 'group' else '全局'}"
                f"记忆 / {scope_id_text}】"
            ]
            for c in cards:
                lines.append(f"  [{c.category}] {c.content}")
            return "\n".join(lines)

        return "请提供 scope + scope_id 或 query 参数。"

    @staticmethod
    def _scope_allowed(ctx: ToolContext, scope: str, scope_id: str) -> bool:
        """Fail closed: tools may read only caller user/group plus global."""
        if scope == "global":
            return scope_id == "global"
        if scope == "user":
            return bool(ctx.user_id) and scope_id == str(ctx.user_id).strip()
        if scope == "group":
            return bool(ctx.group_id) and scope_id == str(ctx.group_id).strip()
        return False

    async def _search_visible_cards(
        self,
        ctx: ToolContext,
        query: str,
    ) -> list[Card]:
        visible: list[Card] = []
        seen: set[str] = set()

        async def add_scope(scope: str, scope_id: str) -> None:
            candidates = await self._store.search_cards(
                query,
                scope=scope,
                limit=10,
            )
            for card in candidates:
                if card.scope != scope:
                    continue
                if card.scope_id != scope_id:
                    continue
                if card.card_id in seen:
                    continue
                seen.add(card.card_id)
                visible.append(card)

        if ctx.user_id:
            await add_scope("user", str(ctx.user_id))
        if ctx.group_id:
            await add_scope("group", str(ctx.group_id))
        await add_scope("global", "global")
        return visible[:10]


class CardUpdateTool(Tool):
    """Add, update, supersede, or expire memory cards."""

    def __init__(self, store: CardStore) -> None:
        self._store = store
        self._background_tasks: set[asyncio.Task[None]] = set()

    @property
    def name(self) -> str:
        return "update_cards"

    @property
    def description(self) -> str:
        return (
            "管理记忆卡片。支持操作：add（新增卡片）、update（更新内容）、"
            "supersede（用新卡片取代旧卡片）、expire（标记过期）。"
            "只允许写当前用户或当前群的卡片，不允许通过聊天工具写全局记忆。"
            "add 需要 scope/scope_id/category/content；"
            "update 需要 card_id + 新字段值；"
            "supersede 需要 old_card_id + 新卡片信息；"
            "expire 只需要 card_id。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "update", "supersede", "expire"],
                    "description": "操作类型",
                },
                "card_id": {
                    "type": "string",
                    "description": "目标卡片 ID（update/supersede/expire 时需要）",
                },
                "scope": {
                    "type": "string",
                    "enum": ["user", "group"],
                    "description": (
                        "写入作用域：仅当前用户（user）或当前群（group）"
                    ),
                },
                "scope_id": {
                    "type": "string",
                    "description": (
                        "当前用户 QQ号或当前群号（add/supersede 时需要）"
                    ),
                },
                "category": {
                    "type": "string",
                    "description": f"卡片类别（add/supersede 时需要）：{_CATEGORY_HELP}",
                },
                "content": {
                    "type": "string",
                    "description": "卡片内容，一句话结论（add/supersede/update 时需要）",
                },
                "confidence": {
                    "type": "number",
                    "description": "置信度 0.0-1.0，默认 0.7",
                },
            },
            "required": ["action"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        action: str = kwargs["action"]
        session_id: str = getattr(ctx, "session_id", "unknown")

        if action == "add":
            scope = kwargs.get("scope")
            scope_id = kwargs.get("scope_id")
            category = kwargs.get("category")
            content = kwargs.get("content")
            if not all([scope, scope_id, category, content]):
                return "add 操作需要 scope、scope_id、category、content 参数"
            scope_text = str(scope)
            scope_id_text = str(scope_id)
            category_text = str(category)
            content_text = str(content)
            if not self._write_scope_allowed(ctx, scope_text, scope_id_text):
                return "无权写入该记忆作用域。"
            try:
                cid = await self._store.add_card(NewCard(
                    category=category_text,
                    scope=scope_text,
                    scope_id=scope_id_text,
                    content=content_text,
                    confidence=float(kwargs.get("confidence", 0.7)),
                    source=f"tool:{session_id}",
                ), captured_by=f"tool:{session_id}")
                return f"已添加卡片 {cid}"
            except ValueError as e:
                return str(e)

        if action == "update":
            card_id = kwargs.get("card_id")
            if not card_id:
                return "update 操作需要 card_id 参数"
            card_id_text = str(card_id)
            card = await self._store.get_card(card_id_text)
            if card is None or not self._write_scope_allowed(
                ctx,
                str(card.scope),
                str(card.scope_id),
            ):
                return f"未找到或无权修改卡片 {card_id_text}"
            fields: dict[str, Any] = {}
            for k in ("content", "category", "confidence", "priority"):
                if k in kwargs and kwargs[k] is not None:
                    fields[k] = kwargs[k]
            if not fields:
                return "update 操作需要至少一个要更新的字段"
            ok = await self._store.update_card(card_id_text, **fields)
            return "已更新" if ok else f"未找到卡片 {card_id_text}"

        if action == "supersede":
            old_id = kwargs.get("card_id")
            scope = kwargs.get("scope")
            scope_id = kwargs.get("scope_id")
            category = kwargs.get("category")
            content = kwargs.get("content")
            if not all([old_id, scope, scope_id, category, content]):
                return "supersede 操作需要 card_id、scope、scope_id、category、content 参数"
            old_id_text = str(old_id)
            scope_text = str(scope)
            scope_id_text = str(scope_id)
            category_text = str(category)
            content_text = str(content)
            old_card = await self._store.get_card(old_id_text)
            if (
                old_card is None
                or not self._write_scope_allowed(
                    ctx,
                    str(old_card.scope),
                    str(old_card.scope_id),
                )
                or not self._write_scope_allowed(ctx, scope_text, scope_id_text)
            ):
                return f"未找到或无权修改卡片 {old_id_text}"
            try:
                new_id = await self._store.supersede_card(old_id_text, NewCard(
                    category=category_text,
                    scope=scope_text,
                    scope_id=scope_id_text,
                    content=content_text,
                    confidence=float(kwargs.get("confidence", 0.8)),
                    source=f"tool:{session_id}",
                ))
                return f"已取代 {old_id_text}，新卡片 {new_id}"
            except ValueError as e:
                return str(e)

        if action == "expire":
            card_id = kwargs.get("card_id")
            if not card_id:
                return "expire 操作需要 card_id 参数"
            card_id_text = str(card_id)
            card = await self._store.get_card(card_id_text)
            if card is None or not self._write_scope_allowed(
                ctx,
                str(card.scope),
                str(card.scope_id),
            ):
                return f"未找到或无权修改卡片 {card_id_text}"
            ok = await self._store.expire_card(card_id_text)
            return "已过期" if ok else f"未找到卡片 {card_id_text}"

        return f"未知操作: {action}"

    @staticmethod
    def _write_scope_allowed(
        ctx: ToolContext,
        scope: str,
        scope_id: str,
    ) -> bool:
        """Chat tools may mutate only the caller user or current group."""
        normalized_scope = str(scope or "").strip()
        normalized_id = str(scope_id or "").strip()
        if normalized_scope == "user":
            return bool(ctx.user_id) and normalized_id == str(ctx.user_id).strip()
        if normalized_scope == "group":
            return bool(ctx.group_id) and normalized_id == str(ctx.group_id).strip()
        # Global writes remain reserved for trusted background/admin services
        # that call CardStore directly rather than through a chat LLM tool.
        return False
