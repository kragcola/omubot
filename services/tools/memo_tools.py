"""Card tools: CardLookupTool and CardUpdateTool for typed memory card operations."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from services.memory.card_store import CardStore, NewCard
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
            "查询用户或群组的记忆卡片。"
            "用 scope + scope_id 查某实体的所有卡片（如 scope=user, scope_id=QQ号）；"
            "用 query 关键词搜索卡片内容，返回匹配的卡片列表。"
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
                    "description": "作用域 ID：QQ号（user时）或群号（group时）",
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
            cards = await self._store.search_cards(query, limit=10)
            if not cards:
                return f"未找到匹配 '{query}' 的卡片。"
            lines = [f"搜索 '{query}' 结果 ({len(cards)} 条):"]
            for c in cards:
                lines.append(f"  [{c.category}] {c.content} (confidence={c.confidence:.0%})")
            return "\n".join(lines)

        if scope is not None and scope_id is not None:
            cards = await self._store.get_entity_cards(scope, scope_id, category=category)
            if not cards:
                label = "用户" if scope == "user" else "群" if scope == "group" else "全局"
                return f"【{label}记忆 / {scope_id}】\n暂无记录"
            lines = [f"【{'用户' if scope == 'user' else '群' if scope == 'group' else '全局'}记忆 / {scope_id}】"]
            for c in cards:
                lines.append(f"  [{c.category}] {c.content}")
            return "\n".join(lines)

        return "请提供 scope + scope_id 或 query 参数。"


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
                    "enum": ["user", "group", "global"],
                    "description": "作用域（add/supersede 时需要）",
                },
                "scope_id": {
                    "type": "string",
                    "description": "作用域 ID（add/supersede 时需要）",
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
            try:
                cid = await self._store.add_card(NewCard(
                    category=category,
                    scope=scope,
                    scope_id=str(scope_id),
                    content=content,
                    confidence=float(kwargs.get("confidence", 0.7)),
                    source=f"tool:{session_id}",
                ))
                return f"已添加卡片 {cid}"
            except ValueError as e:
                return str(e)

        if action == "update":
            card_id = kwargs.get("card_id")
            if not card_id:
                return "update 操作需要 card_id 参数"
            fields: dict[str, Any] = {}
            for k in ("content", "category", "confidence", "priority"):
                if k in kwargs and kwargs[k] is not None:
                    fields[k] = kwargs[k]
            if not fields:
                return "update 操作需要至少一个要更新的字段"
            ok = await self._store.update_card(card_id, **fields)
            return "已更新" if ok else f"未找到卡片 {card_id}"

        if action == "supersede":
            old_id = kwargs.get("card_id")
            scope = kwargs.get("scope")
            scope_id = kwargs.get("scope_id")
            category = kwargs.get("category")
            content = kwargs.get("content")
            if not all([old_id, scope, scope_id, category, content]):
                return "supersede 操作需要 card_id、scope、scope_id、category、content 参数"
            try:
                new_id = await self._store.supersede_card(old_id, NewCard(
                    category=category,
                    scope=scope,
                    scope_id=str(scope_id),
                    content=content,
                    confidence=float(kwargs.get("confidence", 0.8)),
                    source=f"tool:{session_id}",
                ))
                return f"已取代 {old_id}，新卡片 {new_id}"
            except ValueError as e:
                return str(e)

        if action == "expire":
            card_id = kwargs.get("card_id")
            if not card_id:
                return "expire 操作需要 card_id 参数"
            ok = await self._store.expire_card(card_id)
            return "已过期" if ok else f"未找到卡片 {card_id}"

        return f"未知操作: {action}"
