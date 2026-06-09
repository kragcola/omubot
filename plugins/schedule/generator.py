"""ScheduleGenerator — daily LLM-driven schedule generation."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from loguru import logger

from plugins.schedule.calendar import get_day_context
from plugins.schedule.store import ScheduleStore
from plugins.schedule.story_arc import (
    FictionPartnerProfile,
    FictionPartnerState,
    StoryArc,
)
from plugins.schedule.types import ALLOWED_ACTIVITY_LABELS, Schedule, TimeSlot, normalize_activity_label

_L = logger.bind(channel="schedule")

CST = ZoneInfo("Asia/Shanghai")
ApiCaller = Callable[..., Awaitable[dict[str, Any]]]
_ACTIVITY_LABEL_TEXT = " / ".join(ALLOWED_ACTIVITY_LABELS)
_PERSONA_BRIEF_MAX_CHARS = 700
_CONTINUITY_CONTEXT_MAX_CHARS = 900
_STORY_ARC_CONTEXT_MAX_CHARS = 1200
_REFLECTION_INSIGHT_CONTEXT_MAX_CHARS = 700
_RECENT_MEMORY_CARD_LIMIT = 5
_REFLECTION_INSIGHT_CARD_LIMIT = 3
_YESTERDAY_SLOT_LIMIT = 3
_ARC_LAST_EVENTS_LIMIT = 6

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
      "activity": "{activity_labels}",
      "description": "具体、有画面感的正在做的事情",
      "mood_hint": "这个活动带来的情绪",
      "location": "地点"
    }}
  ]
}}

规则：
1. slots 覆盖 06:00 ~ 次日 02:00，8~12 个时间段
2. activity 必须严格从这 12 个枚举里选一个：{activity_labels}
3. description 必须**具体、有画面感**——是"正在做"的真实场景，不是笼统规划
3. 事件之间要有因果关系——前面的事影响后面的状态
4. 每天随机决定主题，不要重复
5. 情绪要有起伏——不能全天开心或全天疲惫
6. mood_hint 用简短中文，如"困倦""匆忙""开心""专注""疲惫""放松""期待""低落"

只输出 JSON，不要其他文字。"""


@dataclass(frozen=True)
class PersonaScheduleBrief:
    """Small persona projection used only for schedule generation."""

    identity: str = ""
    traits: tuple[str, ...] = ()
    known_facts: tuple[str, ...] = ()
    partner_context: str = ""


class StoryArcStoreLike(Protocol):
    def load_active(self) -> StoryArc | None:
        ...

    def save(self, arc: StoryArc) -> None:
        ...


class FictionPartnerStateStoreLike(Protocol):
    def ensure_cards(self, profiles: Sequence[FictionPartnerProfile]) -> list[FictionPartnerState]:
        ...

    def save(self, state: FictionPartnerState) -> None:
        ...


class ScheduleGenerator:
    """Daily background task that generates schedules via LLM."""

    def __init__(
        self,
        store: ScheduleStore,
        generate_at_hour: int = 2,
        identity_name: str = "Bot",
        persona_driven_enabled: bool = False,
        persona_brief: PersonaScheduleBrief | None = None,
        memory_card_store: Any | None = None,
        story_arc_enabled: bool = False,
        story_arc_store: StoryArcStoreLike | None = None,
        partner_state_store: FictionPartnerStateStoreLike | None = None,
        fiction_partner_profiles: tuple[FictionPartnerProfile, ...] = (),
        event_replan_enabled: bool = False,
    ) -> None:
        self._store = store
        self._generate_at_hour = generate_at_hour
        self._identity_name = identity_name
        self._persona_driven_enabled = persona_driven_enabled
        self._persona_brief = persona_brief
        self._memory_card_store = memory_card_store
        self._story_arc_enabled = story_arc_enabled
        self._story_arc_store = story_arc_store
        self._partner_state_store = partner_state_store
        self._fiction_partner_profiles = fiction_partner_profiles
        self._event_replan_enabled = bool(event_replan_enabled)
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

        system = [{
            "type": "text",
            "text": _SCHEDULE_SYSTEM_PROMPT.format(
                name=self._identity_name,
                activity_labels=_ACTIVITY_LABEL_TEXT,
            ),
        }]

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
        if self._persona_driven_enabled:
            persona_text = _render_persona_schedule_brief(self._persona_brief)
            if persona_text:
                user_parts.extend(["", persona_text])
            continuity_text = await self._build_continuity_context(now)
            if continuity_text:
                user_parts.extend(["", continuity_text])
        if self._event_replan_enabled:
            reflection_text = await self._build_reflection_insight_context()
            if reflection_text:
                user_parts.extend(["", reflection_text])
        active_arc = self._load_active_story_arc()
        if active_arc is not None:
            arc_text = _render_story_arc_context(active_arc)
            if arc_text:
                user_parts.extend(["", arc_text])

        messages = [{"role": "user", "content": "\n".join(user_parts)}]

        _L.info("generating schedule for {} ...", today_str)
        result = await api_call(system, messages, tools=None, max_tokens=4096)

        text = _extract_text(result)
        schedule = _parse_schedule(text, today_str)
        if schedule is None:
            _L.error("failed to parse schedule JSON | raw={}", text[:500])
            return

        self._store.save(schedule)
        self._update_story_arc_after_schedule(active_arc, schedule)
        _L.info("schedule generated | date={} theme={} slots={}", schedule.date, schedule.theme, len(schedule.slots))

    def _seconds_until_next_run(self) -> int:
        """Seconds until the next generate_at_hour CST."""
        now = datetime.now(CST)
        target = now.replace(hour=self._generate_at_hour, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        return max(1, int((target - now).total_seconds()))

    async def _build_continuity_context(self, now: datetime) -> str:
        yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday = self._store.load(yesterday_str, update_current=False)
        memory_cards = await self._load_recent_memory_cards()
        return _render_continuity_context(yesterday, memory_cards)

    async def _load_recent_memory_cards(self) -> list[Any]:
        if self._memory_card_store is None:
            return []
        search_cards = getattr(self._memory_card_store, "search_cards", None)
        if search_cards is None:
            return []
        try:
            cards = await search_cards("", limit=_RECENT_MEMORY_CARD_LIMIT)
        except Exception as exc:
            _L.warning("recent memory card lookup failed | error={}", exc)
            return []
        return list(cards or [])[:_RECENT_MEMORY_CARD_LIMIT]

    async def _build_reflection_insight_context(self) -> str:
        return _render_reflection_insight_context(await self._load_recent_reflection_insight_cards())

    async def _load_recent_reflection_insight_cards(self) -> list[Any]:
        if self._memory_card_store is None:
            return []
        search_cards = getattr(self._memory_card_store, "search_cards", None)
        if search_cards is None:
            return []
        try:
            cards = await search_cards("经历洞察", limit=_REFLECTION_INSIGHT_CARD_LIMIT)
        except Exception as exc:
            _L.warning("reflection insight card lookup failed | error={}", exc)
            return []
        filtered = []
        for card in list(cards or []):
            source = str(getattr(card, "source", "") or "")
            content = str(getattr(card, "content", "") or "")
            if source == "dream_reflection" or "经历洞察" in content:
                filtered.append(card)
            if len(filtered) >= _REFLECTION_INSIGHT_CARD_LIMIT:
                break
        return filtered

    def _load_active_story_arc(self) -> StoryArc | None:
        if not self._story_arc_enabled or self._story_arc_store is None:
            return None
        try:
            arc = self._story_arc_store.load_active()
        except Exception as exc:
            _L.warning("active story arc lookup failed | error={}", exc)
            return None
        if arc is None:
            return None
        self._sync_fiction_partner_states(arc)
        return arc

    def _sync_fiction_partner_states(self, arc: StoryArc) -> None:
        if self._partner_state_store is None or not self._fiction_partner_profiles:
            return
        try:
            states = self._partner_state_store.ensure_cards(self._fiction_partner_profiles)
        except Exception as exc:
            _L.warning("fiction partner state sync failed | arc_id={} error={}", arc.arc_id, exc)
            return
        for state in states:
            arc.partner_states[state.entity_id] = state.to_arc_state()
        try:
            if self._story_arc_store is not None:
                self._story_arc_store.save(arc)
        except Exception as exc:
            _L.warning("story arc partner state save failed | arc_id={} error={}", arc.arc_id, exc)

    def _update_story_arc_after_schedule(self, arc: StoryArc | None, schedule: Schedule) -> None:
        if arc is None or not self._story_arc_enabled or self._story_arc_store is None:
            return
        update_story_arc_after_schedule(arc, schedule)
        self._save_partner_states_from_arc(arc)
        try:
            self._story_arc_store.save(arc)
        except Exception as exc:
            _L.warning("story arc schedule update save failed | arc_id={} error={}", arc.arc_id, exc)

    def _save_partner_states_from_arc(self, arc: StoryArc) -> None:
        if self._partner_state_store is None:
            return
        for entity_id, state in arc.partner_states.items():
            if not isinstance(state, dict) or state.get("kind") != "fiction":
                continue
            try:
                self._partner_state_store.save(FictionPartnerState.from_dict({
                    "entity_id": entity_id,
                    **state,
                }))
            except Exception as exc:
                _L.warning("fiction partner state update save failed | entity_id={} error={}", entity_id, exc)


def _render_persona_schedule_brief(brief: PersonaScheduleBrief | None) -> str:
    if brief is None:
        return ""

    lines: list[str] = ["【persona 日程短要点】"]
    if brief.identity:
        lines.append(f"- 身份定位：{brief.identity}")
    if brief.traits:
        lines.append("- 性格关键词：" + "；".join(brief.traits[:5]))
    if brief.known_facts:
        for fact in brief.known_facts[:3]:
            lines.append(f"- 已知事实：{fact}")
    if brief.partner_context:
        lines.append(f"- 伙伴近况：{brief.partner_context}")
    lines.append("- 分层预留：伙伴和团体关系按 fiction 处理；真人/群友事实按 factual 处理，本层不虚构真人线下行为。")
    lines.append("- 使用边界：只用以上短要点影响当天安排，不复述或展开完整人设。")

    text = "\n".join(lines)
    if len(text) <= _PERSONA_BRIEF_MAX_CHARS:
        return text
    return text[: _PERSONA_BRIEF_MAX_CHARS - 1].rstrip() + "…"


def _render_continuity_context(yesterday: Schedule | None, memory_cards: list[Any]) -> str:
    if yesterday is None and not memory_cards:
        return ""

    lines: list[str] = [
        "【跨天连续与最近记忆】",
        "- 生成要求：今天要承接昨日状态或最近记忆卡形成跨天因果；不要重复昨日主题；只做自然变奏，不逐字复述记忆。",
    ]
    if yesterday is not None:
        summary = _summarize_schedule(yesterday)
        if summary:
            lines.append(f"- 昨日日程摘要：{summary}")
        if yesterday.theme:
            lines.append(f"- 昨日主题：{yesterday.theme}")

    if memory_cards:
        lines.append("- 最近记忆卡：")
        for card in memory_cards[:_RECENT_MEMORY_CARD_LIMIT]:
            content = str(getattr(card, "content", "") or "").strip()
            if not content:
                continue
            category = str(getattr(card, "category", "") or "memory").strip()
            lines.append(f"  - [{category}] {_truncate_line(content, 90)}")

    text = "\n".join(lines)
    if len(text) <= _CONTINUITY_CONTEXT_MAX_CHARS:
        return text
    return text[: _CONTINUITY_CONTEXT_MAX_CHARS - 1].rstrip() + "…"


def _render_reflection_insight_context(cards: list[Any]) -> str:
    if not cards:
        return ""

    lines: list[str] = [
        "【昨日经历洞察】",
        "- 生成要求：把 Dream 反思出的经历洞察带回今天的安排；让昨天发生过的事留下自然痕迹，不逐字复述。",
    ]
    for card in cards[:_REFLECTION_INSIGHT_CARD_LIMIT]:
        content = str(getattr(card, "content", "") or "").strip()
        if not content:
            continue
        category = str(getattr(card, "category", "") or "event").strip()
        scope = str(getattr(card, "scope", "") or "").strip()
        scope_id = str(getattr(card, "scope_id", "") or "").strip()
        scope_text = f"{scope}/{scope_id}".strip("/") if scope or scope_id else "global"
        lines.append(f"  - [{category} {scope_text}] {_truncate_line(content, 120)}")
    lines.append("- 边界：经历洞察只影响日程取舍和情绪余波，不生成真人线下行为。")

    text = "\n".join(lines)
    if len(text) <= _REFLECTION_INSIGHT_CONTEXT_MAX_CHARS:
        return text
    return text[: _REFLECTION_INSIGHT_CONTEXT_MAX_CHARS - 1].rstrip() + "…"


def _render_story_arc_context(arc: StoryArc | None) -> str:
    if arc is None:
        return ""

    lines: list[str] = [
        "【当前剧情弧】",
        "- 生成要求：今天必须在这条主线内做每日变奏，承接 stage/goals/conflicts/partner_states；不要每天随机新主题。",
    ]
    title = arc.title or arc.arc_id
    lines.append(f"- 主线：{title}（stage={arc.stage}，scope={arc.scope}）")
    if arc.goals:
        lines.append("- 目标：" + "；".join(_truncate_line(goal, 48) for goal in arc.goals[:3]))
    if arc.active_conflicts:
        lines.append("- 冲突：" + "；".join(_truncate_line(conflict, 48) for conflict in arc.active_conflicts[:3]))
    if arc.variables:
        variables = [
            f"{key}={value}"
            for key, value in sorted(arc.variables.items())
            if isinstance(value, str | int | float | bool)
        ]
        if variables:
            lines.append("- 变量：" + "；".join(variables[:5]))
    if arc.partner_states:
        lines.append("- 伙伴状态（fiction，不是真人 factual）：")
        for state in list(arc.partner_states.values())[:4]:
            if not isinstance(state, dict):
                continue
            display = str(state.get("display_name") or state.get("entity_id") or "伙伴")
            mood = str(state.get("mood") or "平稳")
            availability = str(state.get("availability") or "normal")
            current_state = _truncate_line(str(state.get("current_state") or ""), 60)
            lines.append(f"  - {display}：{availability} / {mood}；{current_state}")
    if arc.open_threads:
        lines.append("- 未收束线索：" + "；".join(_truncate_line(thread, 44) for thread in arc.open_threads[:3]))
    if arc.last_events:
        recent = []
        for event in arc.last_events[-3:]:
            summary = _truncate_line(str(event.get("summary", "") or ""), 54)
            date = str(event.get("date", "") or "")
            if summary:
                recent.append(f"{date} {summary}".strip())
        if recent:
            lines.append("- 最近事件：" + "；".join(recent))
    if arc.next_day_seed:
        lines.append(f"- 今日种子：{_truncate_line(arc.next_day_seed, 90)}")
    constraints = _active_replan_constraints(arc)
    if constraints:
        lines.append("- 近端剧情约束（只影响未来 2-3 天，不重写历史）：")
        for constraint in constraints[:2]:
            text = _truncate_line(str(constraint.get("constraint", "") or ""), 96)
            remaining = int(constraint.get("remaining_days", 0) or 0)
            if text:
                lines.append(f"  - remaining_days={remaining}；{text}")
    lines.append("- 红线：伙伴状态只当虚构角色演绎；不要生成或暗示任何真人线下行为。")

    text = "\n".join(lines)
    if len(text) <= _STORY_ARC_CONTEXT_MAX_CHARS:
        return text
    return text[: _STORY_ARC_CONTEXT_MAX_CHARS - 1].rstrip() + "…"


def update_story_arc_after_schedule(arc: StoryArc, schedule: Schedule) -> None:
    summary = _summarize_generated_schedule(schedule)
    if summary:
        arc.last_events.append({
            "date": schedule.date,
            "theme": schedule.theme,
            "summary": summary,
        })
        arc.last_events = arc.last_events[-_ARC_LAST_EVENTS_LIMIT:]
    arc.next_day_seed = _build_next_day_seed(schedule, arc)
    _update_arc_variables(arc)
    _update_arc_partner_states(arc, schedule, summary)
    _decay_replan_constraints(arc)


def _summarize_generated_schedule(schedule: Schedule) -> str:
    pieces: list[str] = []
    if schedule.theme:
        pieces.append(f"主题《{schedule.theme}》")
    if schedule.day_narrative:
        pieces.append(_truncate_line(schedule.day_narrative, 70))
    practice_or_study = [
        slot for slot in schedule.slots
        if slot.activity in {"practice", "study", "social"} and slot.description
    ]
    if practice_or_study:
        slot = practice_or_study[0]
        pieces.append(f"{slot.time} {slot.description}")
    return "；".join(_truncate_line(piece, 90) for piece in pieces if piece)


def _build_next_day_seed(schedule: Schedule, arc: StoryArc) -> str:
    conflict = arc.active_conflicts[0] if arc.active_conflicts else "主线推进"
    theme = schedule.theme or "今天的安排"
    return f"承接 {schedule.date}《{theme}》，明天继续处理：{conflict}"


def _update_arc_variables(arc: StoryArc) -> None:
    if not arc.variables:
        return
    days = int(arc.event_budget.get("generated_days", 0) or 0) + 1
    arc.event_budget["generated_days"] = days
    for key, value in list(arc.variables.items()):
        if not isinstance(value, int | float):
            continue
        number = float(value)
        if key == "deadline_days_left":
            arc.variables[key] = max(0, int(number) - 1)
        elif key == "rehearsal_progress":
            arc.variables[key] = round(min(1.0, number + 0.08), 3)
        elif key == "exam_pressure":
            arc.variables[key] = round(min(1.0, number + 0.03), 3)
        elif key == "team_morale":
            arc.variables[key] = round(max(0.0, min(1.0, number + 0.02)), 3)


def _update_arc_partner_states(arc: StoryArc, schedule: Schedule, summary: str) -> None:
    if not summary or not arc.partner_states:
        return
    theme = schedule.theme or "今日主线"
    conflict = arc.active_conflicts[0] if arc.active_conflicts else "主线推进"
    has_practice = any(slot.activity == "practice" for slot in schedule.slots)
    has_study = any(slot.activity == "study" for slot in schedule.slots)
    changed = 0
    for entity_id, state in list(arc.partner_states.items()):
        if not isinstance(state, dict) or state.get("kind") != "fiction":
            continue
        current = dict(state)
        display = str(current.get("display_name") or entity_id)
        events = _list_str(current.get("recent_events"))
        events.append(_truncate_line(f"{schedule.date} {summary}", 100))
        current["kind"] = "fiction"
        current["recent_events"] = events[-4:]
        current["current_state"] = _truncate_line(
            f"受《{theme}》影响，正在配合{conflict}调整自己的安排。",
            90,
        )
        if has_practice and changed == 0:
            current["mood"] = "投入但压力上升"
            current["availability"] = "busy"
        elif has_study and changed <= 1:
            current["mood"] = "紧张但配合"
            current["availability"] = "limited"
        else:
            current["mood"] = str(current.get("mood") or "平稳")
            current["availability"] = str(current.get("availability") or "normal")
        current["display_name"] = display
        arc.partner_states[entity_id] = current
        changed += 1
        if changed >= 2:
            break


def _active_replan_constraints(arc: StoryArc) -> list[dict[str, Any]]:
    raw = arc.event_budget.get("active_replan_constraints")
    if not isinstance(raw, list):
        return []
    active: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        remaining = int(item.get("remaining_days", 0) or 0)
        constraint = str(item.get("constraint", "") or "").strip()
        if remaining > 0 and constraint:
            copied = dict(item)
            copied["remaining_days"] = remaining
            copied["constraint"] = constraint
            active.append(copied)
    return active


def _decay_replan_constraints(arc: StoryArc) -> None:
    active: list[dict[str, Any]] = []
    for item in _active_replan_constraints(arc):
        remaining = int(item.get("remaining_days", 0) or 0) - 1
        if remaining <= 0:
            continue
        copied = dict(item)
        copied["remaining_days"] = remaining
        active.append(copied)
    if active:
        arc.event_budget["active_replan_constraints"] = active
    else:
        arc.event_budget.pop("active_replan_constraints", None)


def _summarize_schedule(schedule: Schedule) -> str:
    parts: list[str] = []
    if schedule.theme:
        parts.append(f"主题《{schedule.theme}》")
    if schedule.day_narrative:
        parts.append(_truncate_line(schedule.day_narrative, 80))
    slot_summaries: list[str] = []
    for slot in schedule.slots:
        if not slot.description:
            continue
        mood = f" / {slot.mood_hint}" if slot.mood_hint else ""
        slot_summaries.append(f"{slot.time} {slot.description}{mood}")
        if len(slot_summaries) >= _YESTERDAY_SLOT_LIMIT:
            break
    if slot_summaries:
        parts.append("关键片段：" + "；".join(_truncate_line(s, 80) for s in slot_summaries))
    return "；".join(parts)


def _truncate_line(text: str, limit: int) -> str:
    stripped = " ".join(str(text or "").split())
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 1].rstrip() + "…"


def _list_str(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


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
        slots: list[TimeSlot] = []
        for raw_slot in data["slots"]:
            if not isinstance(raw_slot, dict):
                return None
            activity = normalize_activity_label(raw_slot.get("activity", ""))
            if not activity:
                return None
            slots.append(TimeSlot(
                time=str(raw_slot.get("time", "") or ""),
                activity=activity,
                mood_hint=str(raw_slot.get("mood_hint", "") or ""),
                location=str(raw_slot.get("location", "") or ""),
                description=str(raw_slot.get("description", "") or ""),
            ))
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
