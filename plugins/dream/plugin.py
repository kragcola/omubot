"""DreamPlugin: 梦境整合代理。

周期性运行 LLM 工具循环，整理记忆卡片和表情包库。
在 bot 连接后启动后台循环，bot 关闭时停止。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from loguru import logger
from pydantic import BaseModel

from kernel.types import AmadeusPlugin, PluginContext
from services.media.sticker_store import StickerStore
from services.memory.card_store import CardStore, NewCard

if TYPE_CHECKING:
    from services.system_module import RuntimeStateBus


class DreamConfig(BaseModel):
    """Dream 整理配置。"""

    enabled: bool = False
    life_reflection_enabled: bool = False
    interval_hours: int = 24
    max_rounds: int = 15

# Type alias for the LLM API caller (matches LLMClient._call signature)
ApiCaller = Callable[..., Awaitable[dict[str, Any]]]

# Dedicated dream logger — writes only to dream log files, not the main bot log.
dream_logger = logger.bind(dream=True, channel="dream")
_CST = ZoneInfo("Asia/Shanghai")
_REFLECTION_CARD_LIMIT = 3
_REFLECTION_MESSAGE_LIMIT = 12
_REFLECTION_CONTEXT_MAX_CHARS = 2400


@dataclass(frozen=True, slots=True)
class LifeReflectionCardDraft:
    category: str
    scope: str
    scope_id: str
    content: str
    confidence: float = 0.74


@dataclass(slots=True)
class LifeReflectionDraft:
    cards: list[LifeReflectionCardDraft] = field(default_factory=list)
    last_event_summary: str = ""
    open_threads: list[str] = field(default_factory=list)
    next_day_seed: str = ""


def _truncate_reflection_text(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _build_life_reflection_context(
    *,
    schedule: Any | None,
    arc: Any | None,
    group_id: str,
    recent_messages: list[dict[str, Any]],
    tension_metrics: dict[str, float],
) -> str:
    lines = ["【今天过得怎样：reflection 输入】"]
    if schedule is not None:
        lines.append(f"- 日期：{getattr(schedule, 'date', '')}")
        theme = str(getattr(schedule, "theme", "") or "").strip()
        if theme:
            lines.append(f"- 今日主题：{theme}")
        narrative = str(getattr(schedule, "day_narrative", "") or "").strip()
        if narrative:
            lines.append(f"- 今日基调：{_truncate_reflection_text(narrative, 160)}")
        slots = list(getattr(schedule, "slots", []) or [])
        if slots:
            lines.append("- 今日 slots：")
            for slot in slots[:6]:
                slot_time = str(getattr(slot, "time", "") or "").strip()
                activity = str(getattr(slot, "activity", "") or "").strip()
                description = str(getattr(slot, "description", "") or "").strip()
                mood_hint = str(getattr(slot, "mood_hint", "") or "").strip()
                lines.append(
                    "  - "
                    f"{slot_time} [{activity}] "
                    f"{_truncate_reflection_text(description, 90)} / "
                    f"{_truncate_reflection_text(mood_hint, 50)}"
                )

    if arc is not None:
        title = str(getattr(arc, "title", "") or getattr(arc, "arc_id", "") or "").strip()
        stage = str(getattr(arc, "stage", "") or "").strip()
        if title or stage:
            lines.append(f"- active arc：{title} stage={stage}")
        goals = [str(item).strip() for item in list(getattr(arc, "goals", []) or []) if str(item).strip()]
        if goals:
            lines.append("- goals：" + "；".join(goals[:3]))
        threads = [
            str(item).strip()
            for item in list(getattr(arc, "open_threads", []) or [])
            if str(item).strip()
        ]
        if threads:
            lines.append("- open_threads：" + "；".join(threads[:3]))
        seed = str(getattr(arc, "next_day_seed", "") or "").strip()
        if seed:
            lines.append(f"- next_day_seed：{_truncate_reflection_text(seed, 160)}")

    if group_id and group_id != "global":
        lines.append(f"- group_id：{group_id}")
    if recent_messages:
        lines.append("- 今日群聊片段：")
        for row in recent_messages[-_REFLECTION_MESSAGE_LIMIT:]:
            speaker = str(row.get("speaker") or row.get("role") or "").strip()
            text = str(row.get("content_text") or "").strip()
            if text:
                lines.append(f"  - {speaker}: {_truncate_reflection_text(text, 80)}")

    numeric_metrics = [
        f"{key}={value:.3g}"
        for key, value in sorted(tension_metrics.items())
        if isinstance(value, (int, float))
    ]
    if numeric_metrics:
        lines.append("- 互动张力指标：" + "；".join(numeric_metrics))
    lines.append("- 输出 1-3 条经历洞察卡；不要把私聊内容写进群叙事；不要虚构真人线下行为。")
    context = "\n".join(line for line in lines if line.strip())
    return _truncate_reflection_text(context, _REFLECTION_CONTEXT_MAX_CHARS)


def _strip_json_fence(text: str) -> str:
    text = str(text or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_life_reflection_result(text: str) -> LifeReflectionDraft | None:
    try:
        payload = json.loads(_strip_json_fence(text))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    cards: list[LifeReflectionCardDraft] = []
    raw_cards = payload.get("cards")
    if isinstance(raw_cards, list):
        for item in raw_cards:
            if not isinstance(item, dict):
                continue
            scope = str(item.get("scope", "global") or "global").strip()
            if scope not in {"global", "group"}:
                continue
            scope_id = str(item.get("scope_id", "") or "").strip()
            if scope == "global":
                scope_id = scope_id or "global"
            if scope == "group" and not scope_id:
                continue
            category = str(item.get("category", "event") or "event").strip()
            if category not in {"event", "status", "relationship"}:
                continue
            content = _truncate_reflection_text(str(item.get("content", "") or ""), 240)
            if not content:
                continue
            raw_confidence = item.get("confidence", 0.74)
            try:
                confidence = float(raw_confidence)
            except (TypeError, ValueError):
                confidence = 0.74
            confidence = max(0.1, min(1.0, confidence))
            cards.append(LifeReflectionCardDraft(
                category=category,
                scope=scope,
                scope_id=scope_id,
                content=content,
                confidence=confidence,
            ))
            if len(cards) >= _REFLECTION_CARD_LIMIT:
                break

    threads: list[str] = []
    raw_threads = payload.get("open_threads")
    if isinstance(raw_threads, list):
        for item in raw_threads:
            thread = _truncate_reflection_text(str(item or ""), 120)
            if thread and thread not in threads:
                threads.append(thread)
            if len(threads) >= 3:
                break

    return LifeReflectionDraft(
        cards=cards,
        last_event_summary=_truncate_reflection_text(str(payload.get("last_event_summary", "") or ""), 180),
        open_threads=threads,
        next_day_seed=_truncate_reflection_text(str(payload.get("next_day_seed", "") or ""), 180),
    ) if cards else None


def _apply_life_reflection_to_arc(arc: Any, draft: LifeReflectionDraft) -> None:
    today = datetime.now(_CST).strftime("%Y-%m-%d")
    if draft.last_event_summary:
        last_events = [dict(item) for item in list(getattr(arc, "last_events", []) or []) if isinstance(item, dict)]
        last_events.append({
            "date": today,
            "source": "dream_reflection",
            "summary": draft.last_event_summary,
        })
        arc.last_events = last_events[-6:]

    if draft.open_threads:
        existing = [
            str(item).strip()
            for item in list(getattr(arc, "open_threads", []) or [])
            if str(item).strip()
        ]
        for thread in draft.open_threads:
            if thread and thread not in existing:
                existing.append(thread)
        arc.open_threads = existing[-6:]

    if draft.next_day_seed:
        arc.next_day_seed = draft.next_day_seed


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
        runtime_state: RuntimeStateBus | None = None,
        vision_client: Any | None = None,
        ocr_backfill_per_run: int = 10,
        life_reflection_enabled: bool = False,
        schedule_store: Any | None = None,
        story_arc_store: Any | None = None,
        message_log: Any | None = None,
        mood_engine: Any | None = None,
    ) -> None:
        self._store = store
        self._interval_hours = interval_hours
        self._max_rounds = max_rounds
        self._sticker_store = sticker_store
        self._on_memo_change = on_memo_change
        self._runtime_state = runtime_state
        self._vision_client = vision_client
        self._ocr_backfill_per_run = max(0, int(ocr_backfill_per_run))
        self._life_reflection_enabled = bool(life_reflection_enabled)
        self._schedule_store = schedule_store
        self._story_arc_store = story_arc_store
        self._message_log = message_log
        self._mood_engine = mood_engine
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
        """Run immediately on start, then sleep → run → repeat."""
        interval_s = self._interval_hours * 3600
        await self._run(api_call)
        while True:
            await asyncio.sleep(interval_s)
            await self._run(api_call)

    def _on_loop_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        if exc := task.exception():
            dream_logger.error("dream loop crashed: {}", exc)

    def _cleanup_runtime_state(self) -> int:
        if self._runtime_state is None:
            return 0
        return self._runtime_state.clear_stale_per_session(max_age=timedelta(minutes=30))

    async def _backfill_sticker_ocr(self) -> int:
        """Backfill OCR text for stickers missing the ocr_text key (rate-limited).

        Legacy stickers stored before the OCR pass have no ocr_text key. Each run
        enriches up to ocr_backfill_per_run of them via a single VL rich-description
        call (reusing emit_emotion_tag's overwrite path), so the whole library is
        covered over a few dream cycles without a VL call burst.
        """
        store = self._sticker_store
        if store is None or self._vision_client is None or self._ocr_backfill_per_run <= 0:
            return 0
        from pathlib import Path

        from services.media.sticker_capture import emit_emotion_tag, sticker_media_type

        pending = [
            sid
            for sid, entry in store.list_all().items()
            if "ocr_text" not in (entry or {})
        ]
        if not pending:
            return 0

        done = 0
        for sticker_id in pending[: self._ocr_backfill_per_run]:
            file_path = store.resolve_path(sticker_id)
            if file_path is None or not Path(file_path).exists():
                continue
            try:
                image_data = Path(file_path).read_bytes()
                await emit_emotion_tag(
                    store,
                    sticker_id,
                    image_data=image_data,
                    vision_client=self._vision_client,
                    media_type=sticker_media_type(file_path),
                    overwrite=True,
                )
                done += 1
            except Exception as exc:
                dream_logger.debug("ocr backfill skipped | id={} err={}", sticker_id, exc)
        if done:
            dream_logger.info("dream ocr backfill | enriched={}", done)
        return done

    async def _run(self, api_call: ApiCaller) -> None:
        """Run the dream agent with a tool loop for card consolidation."""
        self._running = True
        t0 = time.time()
        try:
            dream_logger.info("dream starting")
            cleaned_state = self._cleanup_runtime_state()
            if cleaned_state:
                dream_logger.info("dream cleaned stale humanization state | count={}", cleaned_state)
            await self._backfill_sticker_ocr()
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

                # Build assistant message — preserve thinking blocks for DeepSeek
                assistant_content: list[dict[str, Any]] = []
                for tb in result.get("thinking_blocks", []):
                    assistant_content.append(tb)
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
            reflection_writes = await self._run_life_reflection(api_call)
            if (card_writes > 0 or sticker_deletes > 0) and self._on_memo_change:
                self._on_memo_change()
            if reflection_writes > 0 and self._on_memo_change:
                self._on_memo_change()
        except Exception:
            dream_logger.exception("dream failed")
        finally:
            self._running = False

    async def _run_life_reflection(self, api_call: ApiCaller) -> int:
        if not self._life_reflection_enabled:
            return 0
        schedule = self._load_today_schedule()
        arc = self._load_active_story_arc()
        if schedule is None and arc is None:
            return 0
        group_id = self._select_reflection_group_id(arc)
        recent_messages = await self._load_reflection_messages(group_id)
        context = _build_life_reflection_context(
            schedule=schedule,
            arc=arc,
            group_id=group_id,
            recent_messages=recent_messages,
            tension_metrics=self._reflection_tension_metrics(group_id),
        )
        if not context:
            return 0

        system = [{
            "type": "text",
            "text": (
                "你是 Dream 夜间反思层。根据今天的日程、剧情弧和群聊片段，"
                "生成 1-3 条经历洞察卡，并给 active story arc 一个轻量更新。"
                "只输出 JSON，不要输出解释。JSON 结构："
                "{\"cards\":[{\"scope\":\"global|group\",\"scope_id\":\"...\","
                "\"category\":\"event|status|relationship\",\"content\":\"一句经历洞察\","
                "\"confidence\":0.74}],\"last_event_summary\":\"...\","
                "\"open_threads\":[\"...\"],\"next_day_seed\":\"...\"}。"
                "红线：不要虚构真人线下行为；私聊内容不得进入群叙事；"
                "fiction 伙伴可作为虚构角色关系反思。"
            ),
        }]
        messages = [{"role": "user", "content": context}]
        result = await api_call(system, messages, tools=None, max_tokens=1024)
        draft = _parse_life_reflection_result(result.get("text", ""))
        if draft is None:
            dream_logger.warning("life reflection skipped | invalid JSON")
            return 0
        return await self._commit_life_reflection(draft, arc)

    def _load_today_schedule(self) -> Any | None:
        store = self._schedule_store
        if store is None:
            return None
        today = datetime.now(_CST).strftime("%Y-%m-%d")
        try:
            return store.load(today, update_current=False)
        except TypeError:
            try:
                return store.load(today)
            except Exception as exc:
                dream_logger.warning("life reflection schedule load failed | err={}", exc)
                return None
        except Exception as exc:
            dream_logger.warning("life reflection schedule load failed | err={}", exc)
            return None

    def _load_active_story_arc(self) -> Any | None:
        store = self._story_arc_store
        if store is None:
            return None
        try:
            return store.load_active()
        except Exception as exc:
            dream_logger.warning("life reflection story arc load failed | err={}", exc)
            return None

    def _select_reflection_group_id(self, arc: Any | None) -> str:
        if arc is not None:
            value = getattr(arc, "variables", {}).get("reflection_group_id")
            if value:
                return str(value)
        return "global"

    async def _load_reflection_messages(self, group_id: str) -> list[dict[str, Any]]:
        msg_log = self._message_log
        if msg_log is None or not group_id or group_id == "global":
            return []
        query_recent = getattr(msg_log, "query_recent", None)
        if query_recent is None:
            return []
        try:
            rows = await query_recent(group_id, limit=_REFLECTION_MESSAGE_LIMIT)
        except Exception as exc:
            dream_logger.warning("life reflection message lookup failed | group={} err={}", group_id, exc)
            return []
        return [dict(row) for row in rows if isinstance(row, dict)]

    def _reflection_tension_metrics(self, group_id: str) -> dict[str, float]:
        if self._mood_engine is None or not group_id or group_id == "global":
            return {}
        metrics = getattr(self._mood_engine, "m1_tension_metrics", None)
        if metrics is None:
            return {}
        try:
            return dict(metrics(group_id=group_id, session_id=f"group_{group_id}"))
        except Exception:
            return {}

    async def _commit_life_reflection(self, draft: LifeReflectionDraft, arc: Any | None) -> int:
        writes = 0
        for card in draft.cards[:_REFLECTION_CARD_LIMIT]:
            await self._store.add_card(
                NewCard(
                    category=card.category,
                    scope=card.scope,
                    scope_id=card.scope_id,
                    content=card.content,
                    confidence=card.confidence,
                    source="dream_reflection",
                    priority=6,
                ),
                captured_by="dream_reflection",
            )
            writes += 1
        if arc is not None:
            _apply_life_reflection_to_arc(arc, draft)
            store = self._story_arc_store
            if store is not None:
                store.save(arc)
        if writes:
            dream_logger.info("life reflection completed | cards={}", writes)
        return writes

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
                return f"（无{'用户' if scope == 'user' else '群组'}）"
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


class DreamPlugin(AmadeusPlugin):
    name = "dream"
    description = "梦境整合：定期整理记忆卡片、清理表情包库"
    version = "1.1.3"
    priority = 150  # Background task, after business plugins

    def __init__(self) -> None:
        super().__init__()
        self._dream_agent = None
        self._event_boundary_detector = None
        self._started = False
        self._bot: Any = None

    async def on_startup(self, ctx: PluginContext) -> None:
        from kernel.config import load_plugin_config

        dream_cfg = load_plugin_config("plugins/dream/config.default.json", DreamConfig)
        if not dream_cfg.enabled:
            _L = logger.bind(channel="dream")
            _L.info("dream disabled in config, skipping")
            return

        setup_dream_logger(ctx.config.log.dir)
        self._dream_agent = DreamAgent(
            store=ctx.card_store,
            interval_hours=dream_cfg.interval_hours,
            max_rounds=dream_cfg.max_rounds,
            sticker_store=ctx.sticker_store,
            on_memo_change=lambda: ctx.prompt_builder.invalidate(),
            runtime_state=ctx.runtime_state,
            vision_client=getattr(ctx, "vision_client", None),
            life_reflection_enabled=dream_cfg.life_reflection_enabled,
            schedule_store=getattr(ctx, "schedule_store", None),
            story_arc_store=getattr(ctx, "story_arc_store", None),
            message_log=getattr(ctx, "msg_log", None),
            mood_engine=getattr(ctx, "mood_engine", None),
        )

    async def on_bot_connect(self, ctx: PluginContext, bot: Any) -> None:
        self._bot = bot
        if self._dream_agent is None or self._started:
            return
        self._dream_agent.start(ctx.llm_client._call)
        self._started = True
        _L = logger.bind(channel="dream")
        _L.info("dream agent started")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if self._dream_agent is not None:
            await self._dream_agent.stop()
            _L = logger.bind(channel="dream")
            _L.info("dream agent stopped")

    async def on_tick(self, ctx: PluginContext) -> None:
        from services import learning_settings
        from services.memory_consolidator.event_boundary import EventBoundaryDetector

        _L = logger.bind(channel="dream")

        birthday_greeter = getattr(ctx, "birthday_greeter", None)
        if birthday_greeter is not None and self._bot is not None:
            try:
                llm_client = getattr(ctx, "llm_client", None)
                greeted = await birthday_greeter.check_and_greet(self._bot, llm_client=llm_client)
                if greeted:
                    _L.info("birthday_greeter sent wishes | qq={}", greeted)
            except Exception as exc:
                _L.warning("birthday_greeter failed | err={}", exc)

        settings = learning_settings.load(getattr(ctx, "storage_dir", "storage"))
        consolidator_cfg = settings.get("consolidator", {})
        if not consolidator_cfg.get("auto_enabled", False):
            return
        consolidator = getattr(ctx, "memory_consolidator", None)
        if consolidator is None:
            return
        msg_log = getattr(ctx, "msg_log", None)
        if msg_log is None:
            return
        _L = logger.bind(channel="dream")
        try:
            group_ids = await msg_log.list_group_ids() if hasattr(msg_log, "list_group_ids") else []
            if self._event_boundary_detector is None:
                self._event_boundary_detector = EventBoundaryDetector()
            if _ebr_enabled():
                mood_engine = getattr(ctx, "mood_engine", None)
                ebr_hits = 0
                for gid in group_ids[:5]:
                    triggered, reason = await self._event_boundary_detector.detect(
                        group_id=str(gid),
                        message_log=msg_log,
                        mood_engine=mood_engine,
                    )
                    if not triggered:
                        continue
                    await consolidator.run_once(
                        group_id=str(gid),
                        triggered_by=f"event_boundary:{reason}",
                        max_batches=1,
                        batch_size=30,
                    )
                    ebr_hits += 1
                if ebr_hits:
                    _L.info("consolidator event-boundary tick completed | groups={}", ebr_hits)
            interval_s = int(consolidator_cfg.get("interval_minutes", 360)) * 60
            now = time.monotonic()
            if not hasattr(self, "_last_consolidator_monotonic"):
                self._last_consolidator_monotonic: float = 0.0
            if now - self._last_consolidator_monotonic < interval_s:
                return
            self._last_consolidator_monotonic = now
            for gid in group_ids[:5]:
                await consolidator.run_once(
                    group_id=str(gid),
                    triggered_by="periodic_tick",
                    max_batches=1,
                    batch_size=30,
                )
            _L.info("consolidator periodic tick completed | groups={}", len(group_ids[:5]))
        except Exception as exc:
            _L.warning("consolidator periodic tick failed | err={}", exc)


def _ebr_enabled() -> bool:
    raw = os.getenv("EBR_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}
