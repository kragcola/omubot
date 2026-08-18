"""ScheduleGenerator — daily LLM-driven schedule generation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

from loguru import logger

from kernel.background_tasks import (
    BackgroundTaskSupervisor,
    RestartPolicy,
    ShutdownPolicy,
    TaskKind,
    TaskSpec,
)
from plugins.schedule.store import ScheduleStore
from plugins.schedule.story_arc import (
    FictionPartnerProfile,
    FictionPartnerState,
    JournalEventRecord,
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
_SCHEDULE_REPAIR_PROMPT = (
    "上一次日程输出无法解析。请立刻重新输出一个完整、有效的 JSON 对象，"
    "只保留 date、theme、day_narrative、slots 字段；不要解释、不要 Markdown 代码块。"
)

_LOCAL_BILLING_FALLBACK_WEEKDAY = (
    ("06:30", "rest", "把闹钟按掉后再缓一会儿", "困倦", "家中"),
    ("07:30", "meal", "简单吃点东西，把今天要做的事排成顺序", "清醒", "家中"),
    ("08:30", "study", "先处理最需要专注的一小段学习任务", "专注", "书桌"),
    ("12:00", "meal", "午饭时把上午的进度放下，慢慢补充体力", "放松", "餐桌"),
    ("13:00", "rest", "留一段不安排输入的午后空档", "平静", "房间"),
    ("15:00", "practice", "把一个需要反复练习的动作拆成几轮完成", "投入", "练习室"),
    ("18:30", "meal", "晚饭时回看今天已经完成的部分", "踏实", "餐桌"),
    ("20:00", "leisure", "做一件不追求效率的小事，让注意力慢下来", "轻松", "客厅"),
    ("22:30", "hobby", "整理兴趣素材，给明天留一个容易接手的起点", "满足", "书桌"),
    ("00:30", "sleep", "关掉灯，把未完成的事情留给明天", "安定", "卧室"),
)
_LOCAL_BILLING_FALLBACK_WEEKEND = (
    ("07:30", "rest", "不急着起床，先把睡意慢慢放下", "松弛", "家中"),
    ("09:00", "meal", "吃一顿不赶时间的早午餐", "满足", "餐桌"),
    ("10:30", "errand", "处理一件积着的小事，让空间重新顺手", "轻快", "附近"),
    ("12:30", "meal", "午饭后暂时不安排复杂任务", "放松", "餐桌"),
    ("14:00", "hobby", "把喜欢的素材摊开，挑一小部分慢慢整理", "投入", "书桌"),
    ("16:30", "social", "和熟悉的人聊几句，交换近况和小发现", "温暖", "线上"),
    ("18:30", "meal", "晚饭时把今天最喜欢的片段记下来", "愉快", "餐桌"),
    ("20:00", "leisure", "看一段轻松的内容，给一天收尾", "舒展", "客厅"),
    ("22:30", "rest", "整理下周的第一步，不把计划写得过满", "安心", "书桌"),
    ("00:30", "sleep", "在安静里结束这一天", "安定", "卧室"),
)

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


class WorldbookScheduleGovernanceBridgeLike(Protocol):
    """Narrow bridge surface used by the schedule generator."""

    async def prepare_fresh(self, schedule: Schedule) -> Any:
        ...

    async def resume_pending_source(self, schedule_date: str) -> Any:
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
        local_billing_fallback_enabled: bool = False,
        task_supervisor: BackgroundTaskSupervisor | None = None,
        calendar_service: Any | None = None,
        worldbook_runtime: Any | None = None,
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
        self._local_billing_fallback_enabled = bool(local_billing_fallback_enabled)
        self._task: asyncio.Task[None] | None = None
        self._task_supervisor = task_supervisor
        self._calendar_service = calendar_service
        self._worldbook_runtime = worldbook_runtime
        self._worldbook_governance_bridge: WorldbookScheduleGovernanceBridgeLike | None = None

    def set_worldbook_runtime(self, runtime: Any | None) -> None:
        self._worldbook_runtime = runtime

    def set_worldbook_governance_bridge(
        self,
        bridge: WorldbookScheduleGovernanceBridgeLike | None,
    ) -> None:
        """Attach the only permitted Worldbook schedule write path.

        The bridge is installed after Runtime v2 has opened its explicit
        governance source. Until then, Worldbook projection refuses to create
        an unmarked schedule source.
        """
        self._worldbook_governance_bridge = bridge

    def set_worldbook_story_arc_store(self, store: Any | None) -> None:
        """Attach the ledger provisioned by the Worldbook lifecycle.

        Worldbook schedule projection is an independent gate from the legacy
        ``schedule.story_arc_enabled`` flag, so late plugin startup must be able
        to supply the shared store without rebuilding the schedule generator.
        """
        self._story_arc_store = store
        self._story_arc_enabled = store is not None

    def start(self, api_call: ApiCaller) -> None:
        if self._task is not None:
            return
        if self._task_supervisor is None:
            self._task = asyncio.create_task(self._loop(api_call))
        else:
            self._task = self._task_supervisor.spawn(
                TaskSpec(
                    name="schedule.generator",
                    owner="plugins.schedule",
                    kind=TaskKind.PERIODIC,
                    restart=RestartPolicy.ON_FAILURE,
                    shutdown=ShutdownPolicy.CANCEL,
                    max_restarts=3,
                    backoff_seconds=5.0,
                    max_backoff_seconds=60.0,
                ),
                lambda: self._loop(api_call),
            )
        _L.info("schedule generator started | generate_at={}:00 CST", self._generate_at_hour)

    async def ensure_today(self, api_call: ApiCaller) -> bool:
        """Generate today's schedule immediately if it doesn't exist.

        Returns True if a new schedule was generated, False if it already existed.
        """
        today_str = datetime.now(CST).strftime("%Y-%m-%d")
        existing = self._store.load(today_str)
        if existing is not None:
            _L.info("today's schedule already exists for {} — no generation needed", today_str)
            await self.resume_worldbook_governance(today_str)
            return False
        _L.info("today's schedule missing for {} — generating now", today_str)
        try:
            return await self._generate(api_call)
        except Exception:
            _L.exception("on-demand schedule generation failed for {}", today_str)
            return False

    async def stop(self) -> None:
        if self._task and not self._task.done():
            if self._task_supervisor is not None:
                await self._task_supervisor.stop_owner("plugins.schedule")
            else:
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

    def _day_context(self, now: datetime) -> Any | None:
        getter = getattr(self._calendar_service, "get_day_context", None)
        if callable(getter):
            return getter(now)
        return None

    async def _generate(self, api_call: ApiCaller) -> bool:
        now = datetime.now(CST)
        today_str = now.strftime("%Y-%m-%d")

        # Don't regenerate if today's schedule already exists
        existing = self._store.load(today_str)
        if existing is not None:
            _L.info("schedule already exists for {} — skipping generation", today_str)
            return False

        system = [{
            "type": "text",
            "text": _SCHEDULE_SYSTEM_PROMPT.format(
                name=self._identity_name,
                activity_labels=_ACTIVITY_LABEL_TEXT,
            ),
        }]

        day_ctx = self._day_context(now)
        raw_day_type = getattr(
            day_ctx,
            "day_type",
            "school_day" if now.weekday() < 5 else "weekend",
        )
        day_type_cn = {
            "school_day": "上学日",
            "weekend": "周末",
            "holiday": "节假日",
            "makeup_day": "调休日（周末补课）",
        }.get(str(raw_day_type), str(raw_day_type))

        user_parts = [
            f"请生成 {today_str} 的日程。",
            "",
            f"今日信息：{_weekday_cn(now.weekday())}，{day_type_cn}",
        ]
        holiday_name = str(getattr(day_ctx, "holiday_name", "") or "")
        special_day = str(getattr(day_ctx, "special_day", "") or "")
        birthdays = list(getattr(day_ctx, "birthdays", ()) or ())
        if holiday_name:
            user_parts.append(f"正在放{holiday_name}假。")
        if special_day:
            user_parts.append(f"今天是{special_day}。")
        if birthdays:
            for b in birthdays:
                wxs_tag = "（W×S成员）" if b.is_wxs_member else ""
                user_parts.append(f"今天是{b.name_cn}（{b.group}）的生日！{wxs_tag}")
        if bool(getattr(day_ctx, "is_makeup_day", False)):
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
        active_arc = self._load_active_story_arc(today_str)
        if active_arc is not None:
            arc_text = _render_story_arc_context(active_arc)
            if arc_text:
                user_parts.extend(["", arc_text])
        worldbook_text = self._build_worldbook_schedule_context("\n".join(user_parts))
        if worldbook_text:
            user_parts.extend(["", worldbook_text])

        messages = [{"role": "user", "content": "\n".join(user_parts)}]

        _L.info("generating schedule for {} ...", today_str)
        try:
            result = await api_call(system, messages, tools=None, max_tokens=4096)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._local_billing_fallback_enabled or not _is_billing_provider_error(exc):
                raise
            _L.warning(
                "schedule provider billing error; using local fallback | date={} error_type={}",
                today_str,
                type(exc).__name__,
            )
            schedule = _build_local_billing_fallback(
                now=now,
                identity_name=self._identity_name,
                day_context=day_ctx,
            )
        else:
            text = _extract_text(result)
            schedule = _parse_schedule(text, today_str)
            if schedule is None:
                _L.warning("schedule JSON parse failed; retrying once | raw={}", text[:500])
                retry_result = await api_call(
                    system,
                    [*messages, {"role": "user", "content": _SCHEDULE_REPAIR_PROMPT}],
                    tools=None,
                    max_tokens=4096,
                )
                retry_text = _extract_text(retry_result)
                schedule = _parse_schedule(retry_text, today_str)
                if schedule is None:
                    _L.error(
                        "failed to parse schedule JSON after retry | raw={} retry_raw={}",
                        text[:300],
                        retry_text[:500],
                    )
                    return False

        if self._worldbook_schedule_enabled():
            if not await self._persist_governed_worldbook_schedule(schedule):
                return False
        else:
            self._store.save(schedule)
            self._update_story_arc_after_schedule(active_arc, schedule)
        _L.info("schedule generated | date={} theme={} slots={}", schedule.date, schedule.theme, len(schedule.slots))
        return True

    async def resume_worldbook_governance(self, schedule_date: str) -> bool:
        """Resume only a marker-bearing v2 schedule source after restart."""
        if not self._worldbook_schedule_enabled():
            return False
        bridge = self._worldbook_governance_bridge
        if bridge is None:
            return False
        try:
            outcome = await bridge.resume_pending_source(schedule_date)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _L.warning(
                "worldbook schedule governance resume failed | date={} error={}",
                schedule_date,
                type(exc).__name__,
            )
            return False
        status = str(getattr(outcome, "status", "") or "")
        if status not in {"proposed", "committed", "rejected"}:
            _L.warning(
                "worldbook schedule governance resume blocked | date={} status={}",
                schedule_date,
                status or "unknown",
            )
            return False
        return True

    async def _persist_governed_worldbook_schedule(self, schedule: Schedule) -> bool:
        """Persist only through the bound governed bridge.

        In Worldbook projection mode an unmarked schedule cannot later become a
        governed proposal without crossing the legacy cutover boundary.  A
        missing or failed bridge therefore leaves no schedule source behind.
        """
        bridge = self._worldbook_governance_bridge
        if bridge is None:
            _L.warning(
                "worldbook schedule projection has no governance bridge; "
                "refusing unmarked source | date={}",
                schedule.date,
            )
            return False
        try:
            outcome = await bridge.prepare_fresh(schedule)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _L.warning(
                "worldbook schedule governance prepare failed; "
                "no reducer fallback | date={} error={}",
                schedule.date,
                type(exc).__name__,
            )
            return False
        status = str(getattr(outcome, "status", "") or "")
        if status in {"proposed", "committed", "rejected"}:
            return True
        _L.warning(
            "worldbook schedule governance did not create a proposal; "
            "no reducer fallback | date={} status={}",
            schedule.date,
            status or "unknown",
        )
        return False

    def _build_worldbook_schedule_context(self, conversation_text: str) -> str:
        runtime = self._worldbook_runtime
        if runtime is None:
            return ""
        project = getattr(runtime, "project_schedule", None)
        if not callable(project):
            return ""
        try:
            result = project(conversation_text=conversation_text)
        except Exception as exc:
            _L.warning("worldbook schedule projection failed | error={}", exc)
            return ""
        return str(getattr(result, "text", "") or "").strip()

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
            cards = await search_cards(
                "",
                scope="global",
                limit=_RECENT_MEMORY_CARD_LIMIT,
            )
        except Exception as exc:
            _L.warning("recent memory card lookup failed | error={}", exc)
            return []
        return [
            card
            for card in list(cards or [])
            if str(getattr(card, "source", "") or "") != "dream_reflection"
        ][:_RECENT_MEMORY_CARD_LIMIT]

    async def _build_reflection_insight_context(self) -> str:
        return _render_reflection_insight_context(await self._load_recent_reflection_insight_cards())

    async def _load_recent_reflection_insight_cards(self) -> list[Any]:
        # Dream reflections are derived from generated schedules. Reinjecting
        # them here creates a synthetic schedule -> memory -> schedule loop.
        return []

    def _load_active_story_arc(self, on_date: str | None = None) -> StoryArc | None:
        if not self._story_arc_enabled or self._story_arc_store is None:
            return None
        if self._worldbook_schedule_enabled():
            return self._load_worldbook_main_arc(on_date)
        load_active: Any = self._story_arc_store.load_active
        arc: StoryArc | None
        try:
            try:
                arc = load_active(on_date=on_date)
            except TypeError:
                arc = load_active()
        except Exception as exc:
            _L.warning("active story arc lookup failed | error={}", exc)
            return None
        if arc is None:
            ensure_seeded: Any = getattr(self._story_arc_store, "ensure_seeded", None)
            if callable(ensure_seeded) and on_date:
                try:
                    arc = cast(StoryArc | None, cast(Any, ensure_seeded)(on_date))
                except Exception as exc:
                    _L.warning("story arc seed factory failed | date={} error={}", on_date, exc)
                    return None
        if arc is None:
            return None
        return self._sync_fiction_partner_states(arc)

    def _load_worldbook_main_arc(self, on_date: str | None) -> StoryArc | None:
        runtime = self._worldbook_runtime
        if runtime is None or self._story_arc_store is None:
            return None
        try:
            view = runtime.ledger.load_stack(on_date=on_date)
        except Exception as exc:
            _L.warning("worldbook story ledger lookup failed | error={}", exc)
            return None
        main = getattr(view, "main", None)
        if isinstance(main, dict):
            arc_id = str(main.get("arc_id") or "")
            load = getattr(self._story_arc_store, "load", None)
            if arc_id and callable(load):
                try:
                    loaded = cast(StoryArc | None, cast(Any, load)(arc_id))
                except Exception as exc:
                    _L.warning(
                        "worldbook main arc load failed | arc_id={} error={}",
                        arc_id,
                        exc,
                    )
                    return None
                if loaded is not None:
                    return self._sync_fiction_partner_states(loaded)
        if tuple(getattr(view, "sides", ()) or ()) or tuple(
            getattr(view, "ambient", ()) or ()
        ):
            _L.warning(
                "worldbook story ledger has no explicit main; refusing legacy fallback"
            )
            return None
        ensure_seeded = getattr(self._story_arc_store, "ensure_seeded", None)
        if callable(ensure_seeded) and on_date:
            try:
                seeded = cast(StoryArc | None, cast(Any, ensure_seeded)(on_date))
            except Exception as exc:
                _L.warning(
                    "worldbook story arc seed failed | date={} error={}",
                    on_date,
                    exc,
                )
                return None
            if seeded is not None:
                return self._sync_fiction_partner_states(seeded)
        return None

    def _sync_fiction_partner_states(self, arc: StoryArc) -> StoryArc:
        if self._partner_state_store is None or not self._fiction_partner_profiles:
            return arc
        try:
            states = self._partner_state_store.ensure_cards(self._fiction_partner_profiles)
        except Exception as exc:
            _L.warning("fiction partner state sync failed | arc_id={} error={}", arc.arc_id, exc)
            return arc
        state_updates = {state.entity_id: state.to_arc_state() for state in states}

        def apply(latest: StoryArc) -> None:
            latest.partner_states.update(state_updates)

        update: Any = getattr(self._story_arc_store, "update", None)
        if callable(update):
            try:
                return cast(StoryArc, cast(Any, update)(arc.arc_id, apply))
            except Exception as exc:
                _L.warning("story arc partner state update failed | arc_id={} error={}", arc.arc_id, exc)
                return arc
        apply(arc)
        try:
            if self._story_arc_store is not None:
                self._story_arc_store.save(arc)
        except Exception as exc:
            _L.warning("story arc partner state save failed | arc_id={} error={}", arc.arc_id, exc)
        return arc

    def _update_story_arc_after_schedule(self, arc: StoryArc | None, schedule: Schedule) -> None:
        if arc is None or not self._story_arc_enabled or self._story_arc_store is None:
            return
        if self._worldbook_schedule_enabled():
            _L.warning(
                "worldbook schedule projection has no governance bridge; "
                "refusing direct reducer write | date={}",
                schedule.date,
            )
            return
        changed = False

        def apply(latest: StoryArc) -> None:
            nonlocal changed
            changed = update_story_arc_after_schedule(latest, schedule)

        committed: StoryArc | None = None
        update: Any = getattr(self._story_arc_store, "update", None)
        if callable(update):
            try:
                committed = cast(StoryArc, cast(Any, update)(arc.arc_id, apply))
            except Exception as exc:
                _L.warning("story arc schedule update failed | arc_id={} error={}", arc.arc_id, exc)
                return
        else:
            apply(arc)
            try:
                self._story_arc_store.save(arc)
                committed = arc
            except Exception as exc:
                _L.warning("story arc schedule update save failed | arc_id={} error={}", arc.arc_id, exc)
                return
        if changed and committed is not None:
            self._save_partner_states_from_arc(committed)

    def _worldbook_schedule_enabled(self) -> bool:
        runtime = self._worldbook_runtime
        config = getattr(runtime, "config", None)
        return bool(
            runtime is not None
            and getattr(config, "enabled", False)
            and getattr(config, "schedule_projection_enabled", False)
        )

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


def update_story_arc_after_schedule(arc: StoryArc, schedule: Schedule) -> bool:
    summary = _summarize_generated_schedule(schedule)
    generated_dates = set(_list_str(arc.event_budget.get("generated_schedule_dates")))
    if schedule.date in generated_dates:
        return False
    for event in arc.last_events:
        if str(event.get("date", "") or "") != schedule.date:
            continue
        same_schedule = (
            event.get("source") == "schedule_generator"
            or (
                str(event.get("theme", "") or "") == schedule.theme
                and str(event.get("summary", "") or "") == summary
            )
        )
        if same_schedule:
            generated_dates.add(schedule.date)
            arc.event_budget["generated_schedule_dates"] = sorted(generated_dates)[-32:]
            return False
    if summary:
        arc_scope = str(getattr(arc, "scope", "") or "").strip()
        subject_kind = "fiction" if arc_scope == "fiction" else "self"
        privacy = "public" if arc_scope == "fiction" else "unknown"
        event = JournalEventRecord(
            date=schedule.date,
            source="schedule_generator",
            summary=summary,
            subject_kind=subject_kind,  # type: ignore[arg-type]
            privacy=privacy,  # type: ignore[arg-type]
            salience=0.35,
            event_id=f"schedule_generator:{schedule.date}",
        ).to_dict()
        event["theme"] = schedule.theme
        arc.last_events.append(event)
        arc.last_events = arc.last_events[-_ARC_LAST_EVENTS_LIMIT:]
    arc.next_day_seed = _build_next_day_seed(schedule, arc)
    _update_arc_variables(arc)
    _update_arc_partner_states(arc, schedule, summary)
    _decay_replan_constraints(arc)
    generated_dates.add(schedule.date)
    arc.event_budget["generated_schedule_dates"] = sorted(generated_dates)[-32:]
    return True


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


def _is_billing_provider_error(exc: BaseException) -> bool:
    """Recognize only explicit provider-billing failures for local fallback."""
    if getattr(exc, "status", None) == 402:
        return True
    message = str(exc).lower()
    return "insufficient balance" in message or ("http 402" in message and "balance" in message)


def _build_local_billing_fallback(
    *,
    now: datetime,
    identity_name: str,
    day_context: Any | None,
) -> Schedule:
    """Build a deterministic, non-factual schedule when billing blocks the LLM.

    This is deliberately limited to provider balance failures.  It contains no
    external claims and still flows through the normal Worldbook governance bridge.
    """
    raw_day_type = str(
        getattr(day_context, "day_type", "school_day" if now.weekday() < 5 else "weekend")
        or ""
    )
    weekend = raw_day_type in {"weekend", "holiday"} or now.weekday() >= 5
    templates = _LOCAL_BILLING_FALLBACK_WEEKEND if weekend else _LOCAL_BILLING_FALLBACK_WEEKDAY
    variant = now.toordinal() % 3
    weekday_themes = ("把事情拆小的一天", "先稳住节奏的一天", "完成一段再休息的一天")
    weekend_themes = ("慢慢收拢的一天", "留出呼吸的一天", "把喜欢的事放回手边的一天")
    themes = weekend_themes if weekend else weekday_themes
    day_label = "周末" if weekend else "工作日"
    narrative = (
        f"{identity_name}在{day_label}里把安排压到可执行的大小，"
        "先完成眼前一件，再给下一件留出余地。"
    )
    slots = [
        TimeSlot(
            time=time,
            activity=activity,
            description=description,
            mood_hint=mood_hint,
            location=location,
        )
        for time, activity, description, mood_hint, location in templates
    ]
    return Schedule(
        date=now.strftime("%Y-%m-%d"),
        day_narrative=narrative,
        theme=themes[variant],
        generated_at=now.isoformat(),
        slots=slots,
    )


def _parse_schedule(text: str, date_str: str) -> Schedule | None:
    """Parse a valid schedule object from direct, fenced, or embedded JSON."""
    data = _extract_schedule_json_object(text)
    if data is None:
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


def _extract_schedule_json_object(text: str) -> dict[str, Any] | None:
    """Find the first JSON object that actually carries schedule slots.

    ``json.JSONDecoder.raw_decode`` preserves JSON syntax semantics while
    allowing a provider to add a short prose preface/suffix. We deliberately do
    not repair malformed JSON: a truncated or ambiguous payload must retry and
    otherwise leave the current schedule untouched.
    """
    body = (text or "").strip()
    if not body:
        return None
    candidates = [body]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(r"```(?:json|JSON)?\s*(.*?)```", body, flags=re.DOTALL)
        if match.group(1).strip()
    )
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(loaded, dict) and isinstance(loaded.get("slots"), list):
            return loaded

    start = 0
    while True:
        start = body.find("{", start)
        if start < 0:
            return None
        try:
            loaded, _end = decoder.raw_decode(body[start:])
        except json.JSONDecodeError:
            start += 1
            continue
        if isinstance(loaded, dict) and isinstance(loaded.get("slots"), list):
            return loaded
        start += 1


def _weekday_cn(wd: int) -> str:
    return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][wd]
