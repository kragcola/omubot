"""Dream agent: periodic background memory card consolidation with LLM tool loop."""

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from services.media.sticker_store import StickerStore
from services.memory.card_store import CardStore, NewCard

# Type alias for the LLM API caller (matches LLMClient._call signature)
ApiCaller = Callable[..., Awaitable[dict[str, Any]]]

# Dedicated dream logger — writes only to dream log files, not the main bot log.
dream_logger = logger.bind(dream=True, channel="dream")


def setup_dream_logger(log_dir: str) -> None:
    """Add a dedicated sink for dream logs, filtered from the main bot log."""
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    logger.add(
        path / "dream_{time:YYYY-MM-DD}.log",
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
        level="DEBUG",
        filter=lambda record: record["extra"].get("dream", False),
    )

_LIST_CARDS_TOOL: dict[str, Any] = {
    "name": "list_cards",
    "description": "列出某个用户或群组的所有活跃记忆卡片，含类别、置信度、内容。",
    "input_schema": {
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
        },
        "required": ["scope", "scope_id"],
    },
}

_SEARCH_CARDS_TOOL: dict[str, Any] = {
    "name": "search_cards",
    "description": "跨实体搜索卡片内容，用于交叉验证。",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
        },
        "required": ["query"],
    },
}

_UPDATE_CARD_TOOL: dict[str, Any] = {
    "name": "update_card",
    "description": "更新一张卡片的内容、类别或置信度。",
    "input_schema": {
        "type": "object",
        "properties": {
            "card_id": {
                "type": "string",
                "description": "卡片 ID",
            },
            "content": {
                "type": "string",
                "description": "新的内容",
            },
            "category": {
                "type": "string",
                "enum": ["preference", "boundary", "relationship", "event", "promise", "fact", "status"],
                "description": "新的类别",
            },
            "confidence": {
                "type": "number",
                "description": "新的置信度 0.0-1.0",
            },
        },
        "required": ["card_id"],
    },
}

_SUPERSEDE_CARD_TOOL: dict[str, Any] = {
    "name": "supersede_card",
    "description": "用新卡片取代旧卡片。旧卡标记为 superseded，新卡生效。用于信息过时或更精确的表述。",
    "input_schema": {
        "type": "object",
        "properties": {
            "old_card_id": {
                "type": "string",
                "description": "被取代的旧卡片 ID",
            },
            "scope": {
                "type": "string",
                "enum": ["user", "group", "global"],
            },
            "scope_id": {
                "type": "string",
                "description": "作用域 ID",
            },
            "category": {
                "type": "string",
                "enum": ["preference", "boundary", "relationship", "event", "promise", "fact", "status"],
                "description": "卡片类别",
            },
            "content": {
                "type": "string",
                "description": "新的一句话结论",
            },
        },
        "required": ["old_card_id", "scope", "scope_id", "category", "content"],
    },
}

_EXPIRE_CARD_TOOL: dict[str, Any] = {
    "name": "expire_card",
    "description": "将一张卡片标记为过期（不再有效但保留记录）。",
    "input_schema": {
        "type": "object",
        "properties": {
            "card_id": {
                "type": "string",
                "description": "要过期的卡片 ID",
            },
            "reason": {
                "type": "string",
                "description": "过期原因（可选）",
            },
        },
        "required": ["card_id"],
    },
}

_LIST_ENTITIES_TOOL: dict[str, Any] = {
    "name": "list_entities",
    "description": "列出所有有记忆卡片的用户或群组 ID 列表。",
    "input_schema": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["user", "group"],
                "description": "列出用户还是群组",
            },
        },
        "required": ["scope"],
    },
}

_LIST_STICKERS_TOOL: dict[str, Any] = {
    "name": "list_stickers",
    "description": "查看当前表情包库的完整索引（含使用统计）。",
    "input_schema": {
        "type": "object",
        "properties": {},
    },
}

_DELETE_STICKER_TOOL: dict[str, Any] = {
    "name": "delete_sticker",
    "description": "删除一张表情包（文件和索引同时清除）。",
    "input_schema": {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "表情包 ID，如 stk_a1b2c3d4",
            },
        },
        "required": ["id"],
    },
}


def dream_pre_check(card_store: CardStore) -> list[str]:
    """Programmatic scan for card issues. Returns list of issue descriptions."""
    # This is a synchronous summary — the actual deep check happens in the LLM loop.
    # We return high-level guidance only.
    issues: list[str] = []
    # The LLM will do the actual inspection via list_cards / search_cards tools
    return issues


class DreamAgent:
    def __init__(
        self,
        store: CardStore,
        interval_hours: int = 24,
        max_rounds: int = 15,
        sticker_store: StickerStore | None = None,
        on_memo_change: Callable[[], None] | None = None,
    ) -> None:
        self._store = store
        self._interval_hours = interval_hours
        self._max_rounds = max_rounds
        self._sticker_store = sticker_store
        self._on_memo_change = on_memo_change
        self._running: bool = False
        self._loop_task: asyncio.Task[None] | None = None

    def start(self, api_call: ApiCaller) -> None:
        """Start the independent background dream loop."""
        if self._loop_task is not None:
            return
        self._loop_task = asyncio.create_task(self._loop(api_call))
        self._loop_task.add_done_callback(self._on_loop_done)
        dream_logger.info("dream loop started | interval={}h", self._interval_hours)

    async def stop(self) -> None:
        """Cancel the background loop and wait for it to finish."""
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None
            dream_logger.info("dream loop stopped")

    async def _loop(self, api_call: ApiCaller) -> None:
        """Sleep → run → repeat. First run waits a full interval."""
        interval_s = self._interval_hours * 3600
        while True:
            await asyncio.sleep(interval_s)
            await self._run(api_call)

    def _on_loop_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        if exc := task.exception():
            dream_logger.error("dream loop crashed: {}", exc)

    async def _run(self, api_call: ApiCaller) -> None:
        """Run the dream agent with a tool loop for card consolidation."""
        self._running = True
        t0 = time.time()
        try:
            dream_logger.info("dream starting")
            index_text = await self._store.build_global_index()

            sticker_section = ""
            if self._sticker_store:
                sticker_section = (
                    "\n\n5. 表情包库整理：用 list_stickers 查看完整索引，"
                    "审查 send_count 低且 created_at 久远的表情包（LRU 候选），"
                    "综合判断是否淘汰（独特/有价值的可以保留），"
                    f"用 delete_sticker 删除不需要的。库存上限 {self._sticker_store.max_count} 张。"
                    "如果发现 description 或 usage_hint 明显不准确，可以删除该表情包。"
                )

            system = [{"type": "text", "text": (
                "你是记忆整理助手。当前记忆卡片索引：\n"
                f"{index_text}\n\n"
                "请依次处理：\n"
                "1. 用 list_entities 找到有卡片的实体，用 list_cards 查看每个实体的卡片。\n"
                "2. 合并重复/高度相似的卡片（用 supersede_card 取代旧卡）。\n"
                "3. 重新分类：检查每张卡片的 category 是否准确，"
                "不准确的用 update_card 修正。特别是 migration 来源的卡片可能全是 fact 类别。\n"
                "4. 个人情报归位：如果群卡片中包含关于某个人的信息"
                "（性格、偏好、背景、身份、观点、与他人的关系），"
                "用 supersede_card 将该信息迁移到该用户（scope=user），并从群卡片中过期。"
                "判断标准：信息跟着人走（换群也成立）→ user；"
                "只在本群语境下有意义 → group。\n"
                "5. 交叉验证：用 search_cards 搜索相关联的信息，检查矛盾或过时内容。\n"
                "6. 过期清理：过时或明显错误的信息用 expire_card 标记过期。\n"
                f"{sticker_section}"
                "卡片类别：preference（偏好/称呼）/ boundary（边界/不喜欢的）/ "
                "relationship（关系/角色）/ event（重要事件）/ promise（承诺）/ "
                "fact（一般事实/背景/兴趣）/ status（当前状态/情绪）\n\n"
                f"限制：最多 {self._max_rounds} 轮工具调用。如果没有问题需要处理，直接回复即可。"
            )}]

            tools = [
                _LIST_CARDS_TOOL, _SEARCH_CARDS_TOOL, _LIST_ENTITIES_TOOL,
                _UPDATE_CARD_TOOL, _SUPERSEDE_CARD_TOOL, _EXPIRE_CARD_TOOL,
            ]
            if self._sticker_store:
                tools.extend([_LIST_STICKERS_TOOL, _DELETE_STICKER_TOOL])
            messages: list[dict[str, Any]] = [
                {"role": "user", "content": "请开始整理记忆卡片。"},
            ]

            card_reads = 0
            card_writes = 0
            sticker_deletes = 0

            for round_i in range(self._max_rounds):
                result = await api_call(system, messages, tools, 2048)

                text: str = result["text"].strip()
                tool_uses: list[Any] = result["tool_uses"]

                if not tool_uses:
                    dream_logger.info(
                        "dream finished | rounds={} reads={} writes={} sticker_del={} elapsed={:.1f}s",
                        round_i + 1, card_reads, card_writes, sticker_deletes, time.time() - t0,
                    )
                    break

                # Build assistant message with text + tool_use blocks
                assistant_content: list[dict[str, Any]] = []
                if text:
                    assistant_content.append({"type": "text", "text": text})
                for tu in tool_uses:
                    assistant_content.append({
                        "type": "tool_use", "id": tu.id,
                        "name": tu.name, "input": tu.input,
                    })
                messages.append({"role": "assistant", "content": assistant_content})

                # Execute tools and collect results
                tool_results: list[dict[str, Any]] = []
                for tu in tool_uses:
                    result_msg = await self._execute_tool(tu.name, tu.input)
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id, "content": result_msg,
                    })
                    if tu.name in ("list_cards", "search_cards", "list_entities"):
                        card_reads += 1
                    elif tu.name in ("update_card", "supersede_card", "expire_card"):
                        card_writes += 1
                    elif tu.name == "delete_sticker":
                        sticker_deletes += 1
                    dream_logger.debug(
                        "tool {} | id={} | result={}",
                        tu.name, tu.input.get("card_id", tu.input.get("scope_id", "?")), result_msg[:120],
                    )
                messages.append({"role": "user", "content": tool_results})
            else:
                dream_logger.warning(
                    "dream exhausted max rounds | reads={} writes={} sticker_del={} elapsed={:.1f}s",
                    card_reads, card_writes, sticker_deletes, time.time() - t0,
                )

            dream_logger.info("dream completed")
            if (card_writes > 0 or sticker_deletes > 0) and self._on_memo_change:
                self._on_memo_change()
        except Exception:
            dream_logger.exception("dream failed")
        finally:
            self._running = False

    async def _execute_tool(self, name: str, inp: dict[str, Any]) -> str:
        """Execute a dream tool call and return the result string."""
        if name == "list_cards":
            scope = inp.get("scope", "")
            scope_id = str(inp.get("scope_id", ""))
            if not scope or not scope_id:
                return "缺少 scope 或 scope_id 参数"
            cards = await self._store.get_entity_cards(scope, scope_id)
            if not cards:
                return "（空）"
            lines = [f"{scope}/{scope_id} ({len(cards)} 张卡片):"]
            for c in cards:
                lines.append(f"  {c.card_id} [{c.category}] conf={c.confidence:.0%} src={c.source} | {c.content}")
            return "\n".join(lines)

        if name == "search_cards":
            query = inp.get("query", "")
            if not query:
                return "缺少 query 参数"
            cards = await self._store.search_cards(query, limit=15)
            if not cards:
                return f"未找到匹配 '{query}' 的卡片"
            lines = [f"搜索 '{query}' ({len(cards)} 条):"]
            for c in cards:
                lines.append(f"  {c.card_id} [{c.category}] {c.scope}/{c.scope_id} | {c.content}")
            return "\n".join(lines)

        if name == "list_entities":
            scope = inp.get("scope", "")
            if scope not in ("user", "group"):
                return "scope 必须是 user 或 group"
            ids = await self._store.list_entities(scope)
            if not ids:
                return f"（无{ '用户' if scope == 'user' else '群组' }）"
            return "\n".join(ids)

        if name == "update_card":
            card_id = inp.get("card_id", "")
            if not card_id:
                return "缺少 card_id 参数"
            fields: dict[str, Any] = {}
            for k in ("content", "category", "confidence"):
                if k in inp and inp[k] is not None:
                    fields[k] = inp[k]
            if not fields:
                return "没有要更新的字段"
            ok = await self._store.update_card(card_id, **fields)
            return f"已更新 {card_id}" if ok else f"未找到卡片 {card_id}"

        if name == "supersede_card":
            old_id = inp.get("old_card_id", "")
            scope = inp.get("scope", "")
            scope_id = str(inp.get("scope_id", ""))
            category = inp.get("category", "")
            content = inp.get("content", "")
            if not all([old_id, scope, scope_id, category, content]):
                return "缺少 old_card_id、scope、scope_id、category 或 content 参数"
            try:
                new_id = await self._store.supersede_card(old_id, NewCard(
                    category=category,
                    scope=scope,
                    scope_id=scope_id,
                    content=content,
                    confidence=0.8,
                    source="dream",
                ))
                return f"已取代 {old_id} → {new_id}"
            except ValueError as e:
                return str(e)

        if name == "expire_card":
            card_id = inp.get("card_id", "")
            if not card_id:
                return "缺少 card_id 参数"
            ok = await self._store.expire_card(card_id)
            return f"已过期 {card_id}" if ok else f"未找到卡片 {card_id}"

        if name == "list_stickers":
            if self._sticker_store is None:
                return "表情包系统未启用"
            return json.dumps(self._sticker_store.list_all(), ensure_ascii=False, indent=2)

        if name == "delete_sticker":
            if self._sticker_store is None:
                return "表情包系统未启用"
            sticker_id = inp.get("id", "")
            if not sticker_id:
                return "缺少 id 参数"
            if self._sticker_store.remove(sticker_id):
                return f"已删除: {sticker_id}"
            return f"未找到: {sticker_id}"

        return f"未知工具: {name}"
