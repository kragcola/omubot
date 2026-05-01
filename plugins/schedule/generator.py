"""ScheduleGenerator — daily LLM-driven schedule generation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from plugins.schedule.calendar import get_day_context
from plugins.schedule.store import ScheduleStore
from plugins.schedule.types import Schedule, TimeSlot

_L = logger.bind(channel="schedule")

CST = ZoneInfo("Asia/Shanghai")
ApiCaller = Callable[..., Awaitable[dict[str, Any]]]

_SCHEDULE_SYSTEM_PROMPT = """你是一个日程生成器。你需要以{name}的身份，生成一份详细的、沉浸式的每日日程。

日程需结合真实日期生成，日期类型影响全天安排：
- 上学日（周一至周五）：以学校课程 + 课后活动为主线
- 周末：自由安排——睡懒觉、外出、休闲活动
- 节假日（春节、国庆等）：休假模式，安排节日相关活动
- 调休日：虽然是周六日但要补课/补班，心情略带无奈
- 角色生日：当天日程必须包含庆祝活动
- 特殊节日（七夕、圣诞等）：可安排相应的节日活动

你必须输出一个有效的 JSON 对象，格式如下：
{{
  "date": "YYYY-MM-DD",
  "theme": "一天的主题",
  "day_narrative": "一句话概括这一天的基调",
  "slots": [
    {{
      "time": "HH:MM",
      "activity": "具体、有画面感的正在做的事情",
      "mood_hint": "这个活动带来的情绪",
      "location": "地点"
    }}
  ]
}}

规则：
1. slots 覆盖 06:00 ~ 次日 02:00，8~12 个时间段
2. 每个 activity 必须**具体、有画面感**——是"正在做"的真实场景，不是笼统规划
3. 事件之间要有因果关系——前面的事影响后面的状态
4. 每天随机决定主题，不要重复
5. 情绪要有起伏——不能全天开心或全天疲惫
6. mood_hint 用简短中文，如"困倦""匆忙""开心""专注""疲惫""放松""期待""低落"

只输出 JSON，不要其他文字。"""


class ScheduleGenerator:
    """Daily background task that generates schedules via LLM."""

    def __init__(
        self,
        store: ScheduleStore,
        generate_at_hour: int = 2,
        identity_name: str = "Bot",
    ) -> None:
        self._store = store
        self._generate_at_hour = generate_at_hour
        self._identity_name = identity_name
        self._task: asyncio.Task[None] | None = None

    def start(self, api_call: ApiCaller) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(api_call))
        _L.info("schedule generator started | generate_at={}:00 CST", self._generate_at_hour)

    async def ensure_today(self, api_call: ApiCaller) -> bool:
        """Generate today's schedule immediately if it doesn't exist.

        Returns True if a new schedule was generated, False if it already existed.
        """
        today_str = datetime.now(CST).strftime("%Y-%m-%d")
        existing = self._store.load(today_str)
        if existing is not None:
            _L.info("today's schedule already exists for {} — no generation needed", today_str)
            return False
        _L.info("today's schedule missing for {} — generating now", today_str)
        try:
            await self._generate(api_call)
            return True
        except Exception:
            _L.exception("on-demand schedule generation failed for {}", today_str)
            return False

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _loop(self, api_call: ApiCaller) -> None:
        while True:
            sleep_s = self._seconds_until_next_run()
            _L.info("schedule generator sleeping for {:.0f}s (next={}:00 CST)", sleep_s, self._generate_at_hour)
            await asyncio.sleep(sleep_s)
            try:
                await self._generate(api_call)
            except Exception:
                _L.exception("schedule generation failed")

    async def _generate(self, api_call: ApiCaller) -> None:
        now = datetime.now(CST)
        today_str = now.strftime("%Y-%m-%d")

        # Don't regenerate if today's schedule already exists
        existing = self._store.load(today_str)
        if existing is not None:
            _L.info("schedule already exists for {} — skipping generation", today_str)
            return

        system = [{"type": "text", "text": _SCHEDULE_SYSTEM_PROMPT.format(name=self._identity_name)}]

        day_ctx = get_day_context(now)
        day_type_cn = {
            "school_day": "上学日",
            "weekend": "周末",
            "holiday": "节假日",
            "makeup_day": "调休日（周末补课）",
        }.get(day_ctx.day_type, day_ctx.day_type)

        user_parts = [
            f"请生成 {today_str} 的日程。",
            "",
            f"今日信息：{_weekday_cn(now.weekday())}，{day_type_cn}",
        ]
        if day_ctx.holiday_name:
            user_parts.append(f"正在放{day_ctx.holiday_name}假。")
        if day_ctx.special_day:
            user_parts.append(f"今天是{day_ctx.special_day}。")
        if day_ctx.has_birthday:
            for b in day_ctx.birthdays:
                wxs_tag = "（W×S成员）" if b.is_wxs_member else ""
                user_parts.append(f"今天是{b.name_cn}（{b.group}）的生日！{wxs_tag}")
        if day_ctx.is_makeup_day:
            user_parts.append("虽然是周末但因为调休要上课，心情略带无奈。")

        messages = [{"role": "user", "content": "\n".join(user_parts)}]

        _L.info("generating schedule for {} ...", today_str)
        result = await api_call(system, messages, tools=None, max_tokens=4096)

        text = _extract_text(result)
        schedule = _parse_schedule(text, today_str)
        if schedule is None:
            _L.error("failed to parse schedule JSON | raw={}", text[:500])
            return

        self._store.save(schedule)
        _L.info("schedule generated | date={} theme={} slots={}", schedule.date, schedule.theme, len(schedule.slots))

    def _seconds_until_next_run(self) -> int:
        """Seconds until the next generate_at_hour CST."""
        now = datetime.now(CST)
        target = now.replace(hour=self._generate_at_hour, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        return max(1, int((target - now).total_seconds()))


import contextlib  # noqa: E402


def _extract_text(result: dict[str, Any]) -> str:
    """Extract text from _call_api return value.

    Falls back to thinking blocks when the model outputs JSON inside
    thinking (DeepSeek V4 thinking mode may do this).
    """
    text: str = result.get("text", "")
    if text.strip():
        return text
    # Fallback: DeepSeek thinking mode may put the real output in thinking blocks
    for tb in result.get("thinking_blocks", []):
        if tb.get("type") == "thinking":
            t = tb.get("thinking", "").strip()
            if t:
                return t
    return ""


def _parse_schedule(text: str, date_str: str) -> Schedule | None:
    """Parse a JSON schedule from LLM output. Strips markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        # Remove markdown code fences
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    try:
        slots = [TimeSlot(**s) for s in data["slots"]]
        return Schedule(
            date=date_str,
            day_narrative=data.get("day_narrative", ""),
            theme=data.get("theme", ""),
            generated_at=datetime.now(CST).isoformat(),
            slots=slots,
        )
    except (KeyError, TypeError):
        return None


def _weekday_cn(wd: int) -> str:
    return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][wd]
