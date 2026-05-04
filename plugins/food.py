"""FoodPlugin — food recommendation via /吃什么 command.

Triggered by /吃什么, /吃, /c. Recommends a food based on:
- User's stored preferences (from CardStore, user scope)
- User's location
- Current time (morning/noon/evening/night appropriateness)
- Optional taste hint from command args

Short-term rejection memory (30 min, max 5 items) avoids repeats within
a single "ordering session" but does not permanently blacklist foods.

Feedback window: after a recommendation, the next message from the same user
within 120s is intercepted as feedback. Negative feedback (不/不想/换/etc.)
triggers a re-recommendation without needing @bot or /command.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from loguru import logger

from kernel.types import AmadeusPlugin, Command, MessageContext, PluginContext
from services.llm.provider import extract_text
from services.memory.card_store import NewCard
from services.tools.context import ToolContext

_L = logger.bind(channel="food")

CST = ZoneInfo("Asia/Shanghai")

_FOOD_PROMPT_GROUP = """你是一个食物推荐助手。从提供的列表中挑选一款食物推荐给用户。

规则：
- 从提供的食物列表中挑选，不要凭自己的知识凭空推荐
- 列表格式：食物名 [品牌 | 口味 | 分类 | 主食 | 烹饪 | 温度]
- 推荐要具体（如"麦当劳巨无霸套餐"而非"汉堡"），直接使用列表中的食物名
- 考虑时段适配：早上(5-10)推荐早餐，中午(11-13)推荐午餐，下午(14-17)推荐小吃，晚上(18-21)推荐正餐，深夜(22-4)推荐夜宵
- 列表中可能包含各种类型，公正对待，不要偏好某类
- 只返回食物名称，不要描述、推荐理由、或任何其他文字
- 不要使用markdown格式"""

_FOOD_PROMPT_PRIVATE = """你是一个食物推荐助手。根据提供的食物列表和用户的口味偏好，从中挑选一款最合适的食物。

规则：
- 从提供的食物列表中挑选，不要凭自己的知识凭空推荐
- 列表格式：食物名 [品牌 | 口味 | 分类 | 主食 | 烹饪 | 温度]
- 推荐要具体（如"麦当劳巨无霸套餐"而非"汉堡"），直接使用列表中的食物名
- 考虑时段适配：早上(5-10)推荐早餐，中午(11-13)推荐午餐，下午(14-17)推荐小吃，晚上(18-21)推荐正餐，深夜(22-4)推荐夜宵
- 优先推荐符合用户偏好的食物
- 避开用户明确不喜欢的食物
- 结合用户所在地推荐合适的食物
- 列表中可能包含各种类型，公正对待
- 只返回食物名称，不要描述、推荐理由、或任何其他文字
- 不要使用markdown格式"""

# Rejection keywords: single-char must be standalone (not part of a word).
# Multi-char keywords can match anywhere.
_REJECTION_WORDS_MULTI = ["不要", "不想", "不吃", "讨厌", "拒绝", "算了", "换一个", "换别的", "再换", "pass"]
_REJECTION_CHARS = ["不", "别", "换"]

# Taste/cuisine keywords that can be used directly as search hints
# without needing LLM intent parsing.
_TASTE_HINT_WORDS: frozenset[str] = frozenset({
    "辣的", "甜的", "酸的", "咸的", "清淡", "重口味",
    "川菜", "粤菜", "湘菜", "鲁菜", "东北菜", "西北", "新疆",
    "日料", "日本料理", "韩料", "韩国料理", "泰国菜", "泰式",
    "意面", "意大利", "法餐", "西餐", "中餐",
    "面条", "米饭", "面", "粉", "米线", "米粉",
    "火锅", "烧烤", "海鲜", "鱼", "肉", "素", "素食",
    "快餐", "小吃", "炸鸡", "汉堡", "披萨", "比萨",
    "麻辣烫", "冒菜", "串串", "饺子", "馄饨", "包子",
    "粥", "汤", "凉皮", "凉面", "卤味",
    "麻辣", "酸辣", "甜辣", "咖喱",
    "煲仔饭", "炒饭", "盖饭", "烧腊",
})


def _is_rejection(text: str) -> bool:
    """Check if a message reads as rejecting a food recommendation.

    Single-char keywords (不, 别, 换) must be standalone — not part of
    another word like '级别的' or '换一个'.
    """
    for kw in _REJECTION_WORDS_MULTI:
        if kw in text:
            return True
    for ch in _REJECTION_CHARS:
        if ch in text:
            # Check this char is standalone (surrounded by nothing, spaces, or punctuation)
            idx = text.find(ch)
            while idx != -1:
                is_standalone = True
                if idx > 0 and text[idx - 1].strip() and text[idx - 1] not in ",，。！？、 ":
                    # Previous char is a Chinese character (not space/punct) → part of a word
                    prev_ok = text[idx - 1] in "你要我想吃找点来个"
                    is_standalone = prev_ok
                if idx + 1 < len(text) and text[idx + 1].strip() and text[idx + 1] not in ",，。！？、 ":
                    # Next char exists → "不" or "别" or "换" followed by content is OK
                    is_standalone = (ch == "换" and text[idx + 1] in "一个别的") or ch in ("不", "别")
                if is_standalone:
                    return True
                idx = text.find(ch, idx + 1)
    return False


# ---- intent parsing prompt (small LLM call before search) ----

_INTENT_PARSE_PROMPT = """你是一个搜索查询生成器。分析用户对食物推荐的需求，输出一个精准的搜索查询词。

只输出查询词本身，不要加任何标签、引号、换行或解释。

示例：
用户："我想吃辣的" → 辣的 午餐 推荐
用户："不要面" → 米饭类 午餐 推荐
用户："换个正餐级别的" → 正餐 推荐
用户："太清淡了" → 重口味 晚餐 推荐
用户："/吃什么 日料" → 日料 晚餐 推荐
用户：（无特殊需求）→ 晚餐 吃什么

规则：
- 根据时段选择合适的餐类
- 将否定需求转化为正向搜索词（"不要面"→"米饭类"）
- 将模糊需求具体化（"正餐级别"→"正餐"）"""


async def _parse_intent(
    user_text: str, period: str, taste_hint: str = "", recent_foods: list[str] | None = None,
    *, llm_call,
) -> tuple[str, str]:
    """Use LLM to parse user intent and generate a precise search query.

    Returns (search_query, requirements) where requirements is a brief
    description of user constraints for the selection LLM.
    """
    parts: list[str] = [f"当前时段：{period}"]
    if taste_hint:
        parts.append(f"用户口味：{taste_hint}")
    if recent_foods:
        parts.append(f"最近已推荐（请避开）：{'、'.join(recent_foods)}")
    parts.append(f"用户消息：{user_text}")

    system = [{"type": "text", "text": _INTENT_PARSE_PROMPT}]
    messages = [{"role": "user", "content": "\n".join(parts)}]

    try:
        result = await asyncio.wait_for(
            llm_call(system, messages, tools=None, max_tokens=128, thinking={"type": "disabled"}),
            timeout=10.0,
        )
        text = extract_text(result).strip()
        if text:
            # Strip XML-like tags that some models wrongly emit
            text = re.sub(r'</?search_query>', '', text).strip()
            text = text.replace('\n', ' ').strip()
            if text:
                return text, user_text
    except (TimeoutError, Exception):
        pass

    # Fallback: simple query building
    query = f"{period} {taste_hint if taste_hint else '吃什么'}"
    return query, taste_hint or ""


class FoodPlugin(AmadeusPlugin):
    name = "food"
    description = "食物推荐：/吃什么 根据偏好、地区、时间推荐食物"
    version = "0.1.4"
    priority = 25
    dependencies = {"web_search": ">=0.1.0"}  # noqa: RUF012

    def __init__(self) -> None:
        super().__init__()
        self._ctx: PluginContext | None = None
        # Short-term rejection memory: {user_id: [(food_name, timestamp), ...]}
        self._recent: dict[str, list[tuple[str, float]]] = {}
        self._pref_cache: dict[str, dict[str, Any]] = {}
        self._max_recent = 5
        self._recent_ttl = 1800  # 30 minutes
        # Feedback window: {user_id: (group_id, timestamp, last_food_name)}
        self._pending_feedback: dict[tuple[str, str], tuple[float, str]] = {}
        self._feedback_window = 120  # seconds
        # Concurrent feedback guard: set of user_ids with in-flight re-recs
        self._feedback_running: set[str] = set()
        # Search cache: {query: (result_text, timestamp)}
        self._search_cache: dict[str, tuple[str, float]] = {}
        self._search_cache_ttl = 1800  # 30 minutes
        # Concurrent access protection for _recent
        self._recent_lock = asyncio.Lock()
        # Hold references to background feedback tasks so they aren't GC'd
        self._feedback_tasks: set[asyncio.Task] = set()
        # Food library: loaded from JSON, used as fallback when web search fails
        self._food_library: list[dict[str, str]] = []
        self._food_library_max_items = 40  # max items to send to LLM
        # Web search toggle: disabled by default, use food library only
        self._search_enabled = False

    async def on_startup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        # Load food library
        lib_path = Path(__file__).parent / "food_library.json"
        try:
            if lib_path.exists():
                data = json.loads(lib_path.read_text("utf-8"))
                self._food_library = data
                _L.info("food library loaded | entries={}", len(data))
            else:
                _L.warning("food_library.json not found at {}", lib_path)
        except Exception:
            _L.error("failed to load food library", exc_info=True)

    def register_commands(self) -> list:
        return [
            Command(
                name="吃什么",
                handler=self._handle_eat,
                description="推荐食物：根据你的偏好、地区、当前时间随机推荐",
                usage="/吃什么 [口味倾向]  例如：/吃什么 辣的、/吃什么 日料",
                aliases=["吃", "c"],
            ),
            Command(
                name="food",
                handler=self._handle_food,
                description="管理食物偏好配置",
                usage="/food [like|dislike|location|search|info]",
                sub_commands=[
                    Command(
                        name="help",
                        handler=self._handle_food_help,
                        description="显示全部食物偏好管理指令",
                        usage="/food help",
                        aliases=["h"],
                    ),
                    Command(
                        name="search",
                        handler=self._handle_search_toggle,
                        description="开关 Web 搜索功能（默认关闭，使用本地食物库）",
                        usage="/food search on|off",
                        aliases=["搜索"],
                    ),
                    Command(
                        name="like",
                        handler=self._handle_like,
                        description="添加喜欢的食物偏好",
                        usage="/food like <食物/口味>",
                        aliases=["喜欢", "爱吃"],
                    ),
                    Command(
                        name="dislike",
                        handler=self._handle_dislike,
                        description="添加不喜欢的食物",
                        usage="/food dislike <食物/口味>",
                        aliases=["不喜欢", "不吃", "讨厌"],
                    ),
                    Command(
                        name="location",
                        handler=self._handle_location,
                        description="设置你所在的地区",
                        usage="/food location <地区名>",
                        aliases=["地区", "位置", "在哪"],
                    ),
                    Command(
                        name="info",
                        handler=self._handle_info,
                        description="查看你当前的食物偏好和地区",
                        usage="/food info",
                        aliases=["查看", "信息", "我的"],
                    ),
                ],
            ),
        ]

    # =========================================================================
    # /吃什么 — main recommendation
    # =========================================================================

    async def _handle_eat(self, cmd_ctx: Any) -> None:
        from nonebot.adapters.onebot.v11 import Message

        ctx = self._ctx
        if ctx is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("系统未就绪"))
            return

        user_id = cmd_ctx.user_id
        user_text = cmd_ctx.args.strip() if cmd_ctx.args else ""

        # 1. Clean expired recent entries
        await self._cleanup_recent()

        # 2. Read user cards
        try:
            cards = await self._read_user_prefs(user_id)
        except Exception:
            _L.warning("card read failed, using empty prefs | user={}", user_id)
            cards = {"likes": [], "dislikes": [], "location": ""}

        # First-time user: no cards at all → guide to private chat setup
        if not cards["likes"] and not cards["dislikes"] and not cards["location"]:
            _L.info("first-time user, guiding to setup | user={}", user_id)
            await cmd_ctx.bot.send(
                cmd_ctx.event,
                Message(
                    "你还从来没有告诉过我你的口味偏好呢~\n"
                    "在私聊里用 /food 指令告诉我：\n\n"
                    "/food like <食物>  — 喜欢吃什么\n"
                    "/food dislike <食物> — 不喜欢吃什么\n"
                    "/food location <地区> — 你在哪个城市\n\n"
                    "之后再用 /吃什么 我就能给你推荐啦~"
                ),
            )
            return

        reply = await self._do_recommend(user_id, user_text, is_private=cmd_ctx.is_private)
        if reply is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("脑袋空空了…等会儿再问我吧"))
            return

        await cmd_ctx.bot.send(cmd_ctx.event, Message(reply))

        # Set feedback window so follow-up messages are intercepted
        if cmd_ctx.group_id:
            food_name = reply.strip()[:20]
            self._pending_feedback[(user_id, cmd_ctx.group_id)] = (time.time(), food_name)

    # =========================================================================
    # on_message — intercept follow-up feedback after a recommendation
    # =========================================================================

    async def on_message(self, ctx: MessageContext) -> bool:
        """Intercept follow-up feedback after a food recommendation.

        When a user sends a message within the feedback window after receiving
        a recommendation, treat negative feedback as a rejection and re-recommend.

        Returns True immediately to consume the message; actual search+LLM work
        runs in a background task so the message pipeline isn't blocked.
        """
        if ctx.is_private or not ctx.group_id:
            return False

        user_id = ctx.user_id
        group_id = ctx.group_id

        pending = self._pending_feedback.get((user_id, group_id))
        if pending is None:
            return False

        pending_ts, pending_food = pending
        if time.time() - pending_ts > self._feedback_window:
            del self._pending_feedback[(user_id, group_id)]
            return False

        text = ctx.raw_message.get("plain_text", "").strip()
        if not text:
            return False

        # Check if the message reads as negative feedback
        if not _is_rejection(text):
            del self._pending_feedback[(user_id, group_id)]
            return False

        # Guard against concurrent feedback from same user
        if user_id in self._feedback_running:
            return True

        _L.info("feedback rejection | user={} food={!r} text={!r}", user_id, pending_food, text)

        # Clear window and add rejection
        del self._pending_feedback[(user_id, group_id)]
        async with self._recent_lock:
            self._recent.setdefault(user_id, []).append((pending_food, time.time()))
            if len(self._recent[user_id]) > self._max_recent:
                self._recent[user_id] = self._recent[user_id][-self._max_recent:]

        feedback_text = text
        bot = ctx.bot

        # Run search+LLM in background to avoid blocking the message pipeline
        self._feedback_running.add(user_id)
        task = asyncio.create_task(
            self._feedback_recommend(bot, group_id, user_id, feedback_text)
        )
        # Keep a reference so the task isn't GC'd before completion
        self._feedback_tasks.add(task)
        task.add_done_callback(self._feedback_tasks.discard)
        return True

    async def _feedback_recommend(
        self, bot, group_id: str, user_id: str, user_feedback: str,
    ) -> None:
        """Background task: re-recommend after rejection, then send reply."""
        from nonebot.adapters.onebot.v11 import Message

        try:
            reply = await self._do_recommend(user_id, user_feedback, bypass_cache=True)
            if reply is None:
                await bot.send_group_msg(
                    group_id=int(group_id),
                    message=Message("脑袋空空了…等会儿再问我吧"),
                )
                return

            await bot.send_group_msg(
                group_id=int(group_id),
                message=Message(reply),
            )

            food_name = reply.strip()[:20]
            self._pending_feedback[(user_id, group_id)] = (time.time(), food_name)
            await self._record_served(user_id, food_name)
        finally:
            self._feedback_running.discard(user_id)

    # =========================================================================
    # Food library filtering
    # =========================================================================

    # Maps meal period → acceptable available_time values for filtering
    _PERIOD_TIME_MAP: ClassVar[dict[str, set[str]]] = {
        "早餐": {"早餐", "不限"},
        "早午餐": {"早餐", "不限"},
        "午餐": {"不限"},
        "下午茶": {"下午茶", "不限"},
        "晚餐": {"不限"},
        "夜宵": {"夜宵", "不限"},
    }

    @staticmethod
    def _parse_exclusions(text: str) -> dict[str, set[str]]:
        """Parse exclusion patterns like 不要麦当劳, 不吃面, 换一个不辣的.

        Returns {field: {values_to_exclude}} where field is one of
        brand, staple, taste, category.
        """
        exclusions: dict[str, set[str]] = {}
        if not text:
            return exclusions

        # Match: 不要X, 不吃X, 别X, 不想吃X
        for pattern in [r"不要(\S+)", r"不吃(\S+)", r"别(\S+)", r"不想吃(\S+)", r"讨厌(\S+)"]:
            for m in re.finditer(pattern, text):
                word = m.group(1).strip("，。！？、, ")
                if not word:
                    continue
                # Try to classify the exclusion target
                # Brand names (from known brands)
                known_brands = {"麦当劳", "肯德基", "汉堡王", "华莱士", "德克士", "赛百味",
                                "必胜客", "达美乐", "塔可钟", "海底捞", "小龙坎", "呷哺呷哺",
                                "凑凑", "杨国福", "张亮", "沙县小吃", "沙县", "正新鸡排", "正新",
                                "绝味", "绝味鸭脖", "周黑鸭", "廖记", "紫燕", "久久丫",
                                "真功夫", "永和大王", "永和", "老乡鸡", "大米先生", "乡村基",
                                "吉野家", "食其家", "味千", "和府", "遇见小面", "康师傅",
                                "木屋烧烤", "很久以前", "好利来", "鲍师傅", "85度C", "奈雪",
                                "太二", "费大厨", "西贝", "云海肴", "绿茶餐厅", "外婆家",
                                "小菜园", "胖哥俩", "探鱼", "炉鱼", "半天妖", "喜家德",
                                "袁记", "吉祥馄饨", "如意馄饨", "豪客来", "萨莉亚", "好伦哥"}
                # Taste values
                taste_values = {"辣", "麻辣", "酸辣", "甜辣", "甜", "酸", "咸", "咸鲜",
                                "清淡", "咖喱", "麻酱", "苦"}
                # Staple values
                staple_map = {"面": "面", "面条": "面", "米饭": "米饭", "饭": "米饭",
                              "粉": "粉", "米线": "粉", "米粉": "粉", "汉堡": "汉堡",
                              "披萨": "披萨", "比萨": "披萨", "三明治": "三明治",
                              "饺子": "饺子馄饨", "馄饨": "饺子馄饨"}
                # Category values
                category_map = {"快餐": "快餐", "火锅": "火锅", "烧烤": "烧烤",
                                "麻辣烫": "麻辣烫", "小吃": "小吃", "面馆": "面馆",
                                "日料": "日料", "韩料": "韩料", "西餐": "西餐",
                                "正餐": "正餐", "烘焙": "烘焙", "甜品": "甜品"}

                matched = False
                # Check brands
                for brand in known_brands:
                    if brand in word or word in brand:
                        exclusions.setdefault("brand", set()).add(brand)
                        matched = True
                        break
                if matched:
                    continue
                # Check tastes
                for tv in taste_values:
                    if tv in word:
                        exclusions.setdefault("taste", set()).add(tv)
                        matched = True
                        break
                if matched:
                    continue
                # Check staples
                for kw, sv in staple_map.items():
                    if kw in word:
                        exclusions.setdefault("staple", set()).add(sv)
                        matched = True
                        break
                if matched:
                    continue
                # Check categories
                for kw, cv in category_map.items():
                    if kw in word:
                        exclusions.setdefault("category", set()).add(cv)
                        matched = True
                        break
                # Fallback: treat as name substring exclusion
                if not matched:
                    exclusions.setdefault("name", set()).add(word)

        return exclusions

    @staticmethod
    def _extract_taste_filter(text: str) -> str | None:
        """Extract a taste preference from text for coarse filtering.

        Returns a taste substring like '辣', '麻辣', '酸甜' etc., or None.
        """
        if not text:
            return None
        # Ordered by specificity (longer matches first)
        ordered = ["麻辣", "酸辣", "甜辣", "酸甜", "麻酱", "咖喱", "咸鲜", "清淡", "辣", "甜", "酸", "苦", "麻"]
        for t in ordered:
            if t in text:
                return t
        return None

    def _filter_food_library(
        self,
        period: str,
        requirements: str = "",
        user_text: str = "",
        recent_foods: list[str] | None = None,
        cards: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Filter the food library by time period, preferences, and exclusions.

        Returns up to _food_library_max_items entries suitable for LLM selection.
        """
        pool = list(self._food_library)
        if not pool:
            return []

        # 1. Period filter — keep items available at this time
        allowed_times = self._PERIOD_TIME_MAP.get(period, {"不限"})
        pool = [e for e in pool if e.get("available_time", "不限") in allowed_times]

        # 2. Parse exclusions from user_text and requirements
        combined = f"{user_text} {requirements}"
        exclusions = self._parse_exclusions(combined)

        if exclusions:
            _L.debug("exclusions parsed | {}", {k: list(v) for k, v in exclusions.items()})

            def _excluded(entry: dict) -> bool:
                for field, values in exclusions.items():
                    if field == "name":
                        if any(v in entry.get("name", "") for v in values):
                            return True
                    else:
                        entry_val = entry.get(field, "")
                        if field == "taste":
                            # Partial match: excluding "辣" also excludes "麻辣", "酸辣" etc.
                            for v in values:
                                if v in entry_val:
                                    return True
                        elif entry_val in values:
                            return True
                return False

            before = len(pool)
            pool = [e for e in pool if not _excluded(e)]
            _L.info("exclusion filter | {} → {} items", before, len(pool))

        # 3. Positive taste/category preference filtering
        taste_filter = self._extract_taste_filter(requirements) or self._extract_taste_filter(user_text)
        if taste_filter:
            before = len(pool)
            # Match tastes that contain the filter (e.g. "辣" matches "辣", "麻辣", "酸辣")
            pool_taste = [e for e in pool if taste_filter in e.get("taste", "")]
            if len(pool_taste) >= 3:  # Only apply if we have enough results
                pool = pool_taste
                _L.debug("taste filter '{}' | {} → {}", taste_filter, before, len(pool))

        # 4. Exclude recently recommended foods
        if recent_foods:
            before = len(pool)
            recent_set = {r.strip() for r in recent_foods}
            pool = [e for e in pool if e["name"] not in recent_set]
            if len(pool) < 3:
                pool = [e for e in self._food_library if e["name"] not in recent_set]
            _L.debug("recent exclusion | {} → {}", before, len(pool))

        # 5. Apply stored user preferences (private chat only, cards passed in)
        if cards:
            # Boost liked items by duplicating them (increases selection probability)
            liked = set(cards.get("likes", []))
            if liked:
                liked_items = [
                    e for e in pool
                    if any(lk in e.get("taste", "") or lk in e.get("category", "")
                           or lk in e["name"] for lk in liked)
                ]
                pool.extend(liked_items)  # Double-weight liked items
            # Exclude disliked items
            disliked = set(cards.get("dislikes", []))
            if disliked:
                pool = [
                    e for e in pool
                    if not any(dk in e.get("taste", "") or dk in e.get("category", "")
                               or dk in e["name"] for dk in disliked)
                ]

        # 6. If still too many, random-sample to keep selection LLM prompt small
        if len(pool) > self._food_library_max_items:
            random.shuffle(pool)
            pool = pool[:self._food_library_max_items]

        return pool

    def _format_library_items(self, items: list[dict[str, str]]) -> str:
        """Format filtered food library entries as a text block for LLM selection.

        Each line: 食物名 [品牌] | 口味 | 分类 | 主食 | 烹饪 | 温度
        """
        lines: list[str] = []
        for e in items:
            parts = [e["name"]]
            extras: list[str] = []
            if e.get("brand"):
                extras.append(e["brand"])
            extras.append(e["taste"])
            extras.append(e["category"])
            extras.append(e["staple"])
            extras.append(e["cooking_method"])
            extras.append(e["temperature"])
            if extras:
                parts.append("[" + " | ".join(extras) + "]")
            lines.append("".join(parts))
        return "\n".join(lines)

    # =========================================================================
    # _do_recommend — shared recommendation pipeline
    # =========================================================================

    async def _do_recommend(
        self, user_id: str, user_text: str = "", *, is_private: bool = False,
        bypass_cache: bool = False,
    ) -> str | None:
        """Intent → Search → Filter → Output pipeline.

        1. LLM parses user intent → precise search query
        2. Web search with the optimized query
        3. LLM selects best match from results
        4. Return food name only
        """
        ctx = self._ctx
        if ctx is None:
            return None

        now = datetime.now(CST)
        time_str = now.strftime("%H:%M")
        hour = now.hour
        weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]

        async with self._recent_lock:
            recent_foods = [food for food, _ts in self._recent.get(user_id, [])]

        # 1. Determine meal period
        if 5 <= hour < 10:
            period = "早餐"
        elif 10 <= hour < 11:
            period = "早午餐"
        elif 11 <= hour < 13:
            period = "午餐"
        elif 13 <= hour < 17:
            period = "下午茶"
        elif 17 <= hour < 21:
            period = "晚餐"
        else:
            period = "夜宵"

        # 2. Build search query: skip intent LLM for simple inputs
        llm_call = ctx.llm_client._call
        if not user_text:
            search_query = f"{period} 吃什么"
            requirements = ""
        elif user_text in _TASTE_HINT_WORDS:
            search_query = f"{user_text} {period} 推荐"
            requirements = user_text
        else:
            search_query, requirements = await _parse_intent(
                user_text, period, taste_hint="",
                recent_foods=recent_foods if recent_foods else None,
                llm_call=llm_call,
            )
        _L.info("intent parsed | user={} query={!r} reqs={!r}", user_id, search_query, requirements)

        # 3. Web search (only when enabled; disabled by default)
        search_text: str | None = None
        if self._search_enabled:
            if not bypass_cache:
                cached = self._search_cache.get(search_query)
                if cached is not None:
                    cached_text, cached_ts = cached
                    if time.time() - cached_ts < self._search_cache_ttl:
                        _L.info("search cache hit | query={!r}", search_query)
                        search_text = cached_text
                    else:
                        del self._search_cache[search_query]

            if search_text is None:
                query_alt = f"{period} 推荐"
                search_text = await self._try_search(search_query, query_alt)
                if search_text:
                    self._search_cache[search_query] = (search_text, time.time())
        else:
            _L.info("search disabled, using food library | user={}", user_id)

        # 4. Build LLM selection prompt
        user_parts: list[str] = []
        user_parts.append(f"当前时间：{time_str}（周{weekday}）")

        if recent_foods:
            user_parts.append(f"最近已推荐（请避开）：{'、'.join(recent_foods)}")

        if requirements and requirements != user_text:
            user_parts.append(f"用户需求：{requirements}")

        # Read user preference cards (private only) for both prompt and library filter
        cards: dict[str, Any] | None = None
        if is_private:
            try:
                cards = await self._read_user_prefs(user_id)
            except Exception:
                cards = {"likes": [], "dislikes": [], "location": ""}
            if cards.get("likes"):
                user_parts.append(f"喜欢：{'、'.join(cards['likes'][:8])}")
            if cards.get("dislikes"):
                user_parts.append(f"不喜欢：{'、'.join(cards['dislikes'][:8])}")
            if cards.get("location"):
                user_parts.append(f"地区：{cards['location']}")

        if search_text:
            user_parts.append(f"\n以下是搜索「{search_query}」的结果，请从中挑选：\n\n{search_text}")
        else:
            # Web search failed — fall back to food library
            library_items = self._filter_food_library(
                period, requirements, user_text, recent_foods, cards,
            )
            if library_items:
                formatted = self._format_library_items(library_items)
                user_parts.append(f"\n以下是食物库中的选项，请从中挑选一款：\n\n{formatted}")
                _L.info("library fallback | items={} period={}", len(library_items), period)
            else:
                user_parts.append("\n（未找到合适的食物，请根据你的知识推荐）")

        user_parts.append("请推荐一款食物。")

        system_prompt = _FOOD_PROMPT_PRIVATE if is_private else _FOOD_PROMPT_GROUP
        system_blocks = [{"type": "text", "text": system_prompt}]
        messages = [{"role": "user", "content": "\n".join(user_parts)}]

        _L.info(
            "recommend | user={} query={!r} private={} recent={} search={}",
            user_id, search_query, is_private, len(recent_foods), bool(search_text),
        )

        # 5. Call LLM to select
        try:
            result = await asyncio.wait_for(
                llm_call(system_blocks, messages, tools=None, max_tokens=128, thinking={"type": "disabled"}),
                timeout=15.0,
            )
        except TimeoutError:
            _L.warning("recommend timeout | user={}", user_id)
            return None
        except Exception:
            _L.error("recommend failed | user={}", user_id, exc_info=True)
            return None

        text = extract_text(result).strip()
        if not text:
            return None

        async with self._recent_lock:
            self._recent.setdefault(user_id, []).append((text[:20], time.time()))
            if len(self._recent[user_id]) > self._max_recent:
                self._recent[user_id] = self._recent[user_id][-self._max_recent:]

        return text

    async def _try_search(self, query: str, query_alt: str) -> str | None:
        """Search via the existing web_search tool (Bing or DuckDuckGo).

        Tries primary query first; on timeout skips the fallback query
        to avoid doubling the wait. Only tries fallback if primary
        returned empty results (not timeout).
        """
        ctx = self._ctx
        if ctx is None or ctx.tool_registry is None:
            return None

        tool = ctx.tool_registry.get("web_search")
        if tool is None:
            _L.warning("web_search tool not found")
            return None

        tool_ctx = ToolContext()

        for q in (query, query_alt):
            try:
                result = await asyncio.wait_for(
                    tool.execute(tool_ctx, query=q, max_results=5),
                    timeout=5.0,
                )
                if result and "搜索失败" not in result and "未找到" not in result:
                    _L.info("search ok | query={!r}", q)
                    return result
            except TimeoutError:
                _L.warning("search timeout | query={!r}", q)
                # Don't retry with alt query on timeout — the network is the
                # bottleneck, not the query wording.  Skip straight to LLM.
                return None
            except Exception:
                _L.warning("search error | query={!r}", q, exc_info=True)
                return None

        _L.info("all searches empty, using LLM fallback")
        return None

    # =========================================================================
    # /food — management commands (private chat only)
    # =========================================================================

    async def _require_private(self, cmd_ctx: Any) -> bool:
        """Return True if cmd_ctx is NOT private — sends guidance and blocks.

        All /food preference management is private-chat-only to prevent
        leaking personal data (likes, dislikes, location) into group chat.
        """
        if not cmd_ctx.is_private:
            from nonebot.adapters.onebot.v11 import Message
            await cmd_ctx.bot.send(
                cmd_ctx.event,
                Message("请在私聊中管理你的食物偏好~\n"
                        "在这里私聊我就可以使用 /food 指令了：\n"
                        "/food like <食物>  /  /food dislike <食物>  /  /food location <地区>"),
            )
            return True
        return False

    async def _handle_like(self, cmd_ctx: Any) -> None:
        from nonebot.adapters.onebot.v11 import Message

        if await self._require_private(cmd_ctx):
            return

        food = cmd_ctx.args.strip()
        if not food:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("用法：/food like <食物/口味>  例如 /food like 辣的"))
            return

        await self._add_preference(cmd_ctx.user_id, "likes", food)
        await cmd_ctx.bot.send(cmd_ctx.event, Message(f"记住了~你喜欢「{food}」"))

    async def _handle_dislike(self, cmd_ctx: Any) -> None:
        from nonebot.adapters.onebot.v11 import Message

        if await self._require_private(cmd_ctx):
            return

        food = cmd_ctx.args.strip()
        if not food:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("用法：/food dislike <食物/口味>  例如 /food dislike 香菜"))
            return

        await self._add_preference(cmd_ctx.user_id, "dislikes", food)
        await cmd_ctx.bot.send(cmd_ctx.event, Message(f"记住了~你不喜欢「{food}」"))

    async def _handle_location(self, cmd_ctx: Any) -> None:
        from nonebot.adapters.onebot.v11 import Message

        if await self._require_private(cmd_ctx):
            return

        ctx = self._ctx
        if ctx is None:
            return

        location = cmd_ctx.args.strip()
        if not location:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("用法：/food location <地区名>  例如 /food location 北京"))
            return

        store = ctx.card_store
        if store is None:
            await cmd_ctx.bot.send(cmd_ctx.event, Message("记忆系统未就绪"))
            return

        # Supersede old location fact cards
        old_cards = await store.get_entity_cards("user", cmd_ctx.user_id, category="fact")
        for c in old_cards:
            if "位于" in c.content or "住在" in c.content:
                await store.expire_card(c.card_id)

        await store.add_card(NewCard(
            scope="user",
            scope_id=cmd_ctx.user_id,
            category="fact",
            content=f"位于{location}",
            confidence=0.9,
            source="user_config",
        ))
        self._pref_cache.pop(cmd_ctx.user_id, None)
        _L.info("location set | user={} location={}", cmd_ctx.user_id, location)
        await cmd_ctx.bot.send(cmd_ctx.event, Message(f"记住了~你在「{location}」"))

    async def _handle_info(self, cmd_ctx: Any) -> None:
        from nonebot.adapters.onebot.v11 import Message

        if await self._require_private(cmd_ctx):
            return

        cards = await self._read_user_prefs(cmd_ctx.user_id)

        parts: list[str] = ["你的食物偏好："]
        if cards.get("likes"):
            parts.append(f"喜欢：{'、'.join(cards['likes'])}")
        else:
            parts.append("喜欢：（还没有记录）")
        if cards.get("dislikes"):
            parts.append(f"不喜欢：{'、'.join(cards['dislikes'])}")
        else:
            parts.append("不喜欢：（还没有记录）")
        if cards.get("location"):
            parts.append(f"地区：{cards['location']}")
        parts.append(f"Web 搜索：{'已开启' if self._search_enabled else '已关闭'}")

        await cmd_ctx.bot.send(cmd_ctx.event, Message("\n".join(parts)))

    async def _handle_search_toggle(self, cmd_ctx: Any) -> None:
        """Toggle web search on/off for food recommendations."""
        from nonebot.adapters.onebot.v11 import Message

        arg = cmd_ctx.args.strip().lower()
        if arg in ("on", "开", "启用", "打开"):
            self._search_enabled = True
            _L.info("search enabled | by={}", cmd_ctx.user_id)
            await cmd_ctx.bot.send(cmd_ctx.event,
                Message("Web 搜索已开启，/吃什么 将先搜索网络再结合食物库推荐"))
        elif arg in ("off", "关", "关闭", "禁用"):
            self._search_enabled = False
            _L.info("search disabled | by={}", cmd_ctx.user_id)
            await cmd_ctx.bot.send(cmd_ctx.event,
                Message("Web 搜索已关闭，/吃什么 将直接从本地食物库（1094条）推荐"))
        elif arg == "":
            status = "已开启" if self._search_enabled else "已关闭"
            await cmd_ctx.bot.send(cmd_ctx.event,
                Message(f"Web 搜索{status}\n使用 /food search on|off 切换"))
        else:
            await cmd_ctx.bot.send(cmd_ctx.event,
                Message("用法：/food search on 或 /food search off"))

    # =========================================================================
    # /food — show usage when no sub-command
    # =========================================================================

    async def _handle_food(self, cmd_ctx: Any) -> None:
        """Fallback handler — show help (private only)."""
        await self._handle_food_help(cmd_ctx)

    async def _handle_food_help(self, cmd_ctx: Any) -> None:
        """Show all food preference management commands."""
        from nonebot.adapters.onebot.v11 import Message

        if await self._require_private(cmd_ctx):
            return

        await cmd_ctx.bot.send(
            cmd_ctx.event,
            Message(
                "食物偏好管理：\n"
                "/food help — 显示此帮助\n"
                "/food search on|off — 开关 Web 搜索（默认关，用本地食物库）\n"
                "/food like <食物> — 添加喜欢的食物\n"
                "/food dislike <食物> — 添加不喜欢的食物\n"
                "/food location <地区> — 设置你的地区\n"
                "/food info — 查看当前偏好"
            ),
        )

    # =========================================================================
    # helpers
    # =========================================================================

    async def _cleanup_recent(self) -> None:
        """Remove expired entries from short-term rejection memory and search cache."""
        now = time.time()
        async with self._recent_lock:
            for uid in list(self._recent):
                self._recent[uid] = [
                    (f, t) for f, t in self._recent[uid] if now - t < self._recent_ttl
                ]
                if not self._recent[uid]:
                    del self._recent[uid]
        # Clean expired search cache entries
        for query in list(self._search_cache):
            if now - self._search_cache[query][1] > self._search_cache_ttl:
                del self._search_cache[query]

    async def _read_user_prefs(self, user_id: str) -> dict[str, Any]:
        """Read user's food-related cards from CardStore.

        Only considers cards created by this plugin (source="user_config" with
        food-specific content prefixes).  Returns {"likes": [...], "dislikes": [...], "location": ""}.
        Results are cached in self._pref_cache.
        """
        ctx = self._ctx
        if ctx is None or ctx.card_store is None:
            return {"likes": [], "dislikes": [], "location": ""}

        store = ctx.card_store
        cards = await store.get_entity_cards("user", user_id)

        likes: list[str] = []
        dislikes: list[str] = []
        location = ""

        for c in cards:
            content = c.content
            if c.category == "preference" and c.source == "user_config":
                if content.startswith("喜欢吃"):
                    likes.append(content.removeprefix("喜欢吃").strip("，。、 "))
                elif content.startswith("不喜欢吃"):
                    dislikes.append(content.removeprefix("不喜欢吃").strip("，。、 "))
                # Ignore other preference cards (from affection, memo, etc.)
            elif c.category == "fact" and c.source == "user_config" and ("位于" in content or "住在" in content):
                location = content.replace("位于", "").replace("住在", "").strip("，。、 ")

        result = {"likes": likes, "dislikes": dislikes, "location": location}
        self._pref_cache[user_id] = result
        return result

    async def _add_preference(self, user_id: str, pref_type: str, value: str) -> None:
        """Add a food preference card to CardStore."""
        ctx = self._ctx
        if ctx is None or ctx.card_store is None:
            return

        store = ctx.card_store
        prefix = "喜欢吃" if pref_type == "likes" else "不喜欢吃"
        content = f"{prefix}{value}"

        existing = await store.find_similar("user", user_id, content, threshold=0.6)
        if existing is not None:
            await store.reinforce(existing.card_id)
            _L.debug("preference reinforced | user={} type={} value={}", user_id, pref_type, value)
        else:
            await store.add_card(NewCard(
                scope="user",
                scope_id=user_id,
                category="preference",
                content=content,
                confidence=0.7,
                source="user_config",
            ))
            _L.info("preference added | user={} type={} value={}", user_id, pref_type, value)

        self._pref_cache.pop(user_id, None)

    async def _record_served(self, user_id: str, food_name: str) -> None:
        """Record a served food recommendation as an event card."""
        ctx = self._ctx
        if ctx is None or ctx.card_store is None:
            return

        store = ctx.card_store
        now = datetime.now(CST).strftime("%m-%d %H:%M")
        await store.add_card(NewCard(
            scope="user",
            scope_id=user_id,
            category="event",
            content=f"推荐了{food_name}（{now}）",
            confidence=0.5,
            source="food_plugin",
        ))

    # =========================================================================
    # on_pre_prompt — inject food context for LLM
    # =========================================================================

    async def on_pre_prompt(self, ctx: Any) -> None:
        """Inject food preference info into the system prompt (private chat only).

        Food preferences are personal information. In group chat they are only
        passed to the LLM within the /吃什么 recommendation prompt itself, never
        injected into the general conversation's system prompt.
        """
        if not ctx.is_private:
            return

        user_id = ctx.user_id
        cards = await self._read_user_prefs(user_id)
        if not cards["likes"] and not cards["dislikes"] and not cards["location"]:
            return

        parts: list[str] = []
        if cards["likes"]:
            parts.append(f"食物偏好：{'、'.join(cards['likes'][:5])}")
        if cards["dislikes"]:
            parts.append(f"不吃：{'、'.join(cards['dislikes'][:5])}")
        if cards["location"]:
            parts.append(f"所在地：{cards['location']}")

        if parts:
            ctx.add_block(
                text="[食物偏好] " + "；".join(parts),
                label="food_pref",
                position="stable",
            )
