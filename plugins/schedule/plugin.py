"""SchedulePlugin: 日程与心情系统。

通过 on_pre_prompt 注入当前时间和心情到 system prompt。
通过 on_bot_connect 启动日程生成器后台循环。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger
from pydantic import BaseModel, Field

from kernel.types import AmadeusPlugin, PluginContext, PromptContext
from plugins.schedule.story_arc import StoryArc, StoryArcEventCandidate, record_event_trigger
from plugins.schedule.types import Schedule


class DialogueClimateConfig(BaseModel):
    """Dialogue Climate M1 flags. Dormant until Wave 1E consumes them."""

    m1_enabled: bool = False


class ScheduleConfig(BaseModel):
    """模拟日程系统配置。"""

    enabled: bool = True
    storage_dir: str = "storage/schedule"
    generate_at_hour: int = 2
    persona_driven_enabled: bool = False
    story_arc_enabled: bool = False
    event_replan_enabled: bool = False
    dialogue_climate: DialogueClimateConfig = Field(default_factory=DialogueClimateConfig)
    mood_anomaly_chance: float = 0.05
    mood_refresh_minutes: int = 30

_L = logger.bind(channel="mood")
_L_sys = logger.bind(channel="system")


class SchedulePlugin(AmadeusPlugin):
    name = "schedule"
    description = "日程与心情：时间感知、心情注入、每日日程生成"
    version = "1.1.5"
    priority = 20

    def __init__(self) -> None:
        super().__init__()
        self._mood_engine = None
        self._schedule_store = None
        self._schedule_gen = None
        self._timeline = None
        self._schedule_started = False
        self._dialogue_climate_m1_enabled = False
        self._event_replan_enabled = False
        self._story_arc_store = None

    async def on_startup(self, ctx: PluginContext) -> None:
        self._mood_engine = ctx.mood_engine
        self._schedule_store = ctx.schedule_store
        self._schedule_gen = ctx.schedule_gen
        self._timeline = ctx.timeline
        self._dialogue_climate_m1_enabled = bool(getattr(ctx, "dialogue_climate_m1_enabled", False))
        self._event_replan_enabled = bool(getattr(ctx, "schedule_event_replan_enabled", False))
        self._story_arc_store = getattr(ctx, "story_arc_store", None)

    async def on_bot_connect(self, ctx: PluginContext, bot: Any) -> None:
        if not ctx.schedule_enabled or self._schedule_gen is None:
            return

        self._schedule_gen.start(ctx.llm_client._call)

        if not self._schedule_started:
            self._schedule_started = True
            if self._schedule_store is not None:
                today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
                loaded = self._schedule_store.load(today)
                if loaded is None:
                    _L_sys.info("today's schedule missing, generating now...")
                    await self._schedule_gen.ensure_today(ctx.llm_client._call)

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if self._schedule_gen is not None:
            await self._schedule_gen.stop()
            _L.info("schedule generator stopped")

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if self._mood_engine is None or self._schedule_store is None:
            return
        recent_count = 0
        if ctx.group_id is not None and self._timeline is not None:
            recent_count = self._timeline.recent_interaction_count(str(ctx.group_id), window_s=60.0)
        text = self._mood_engine.build_mood_block(
            self._schedule_store.current,
            recent_interaction_count=recent_count,
            group_id=ctx.group_id,
            session_id=ctx.session_id,
        )
        if text:
            profile = self._mood_engine.cached_profile(group_id=ctx.group_id, session_id=ctx.session_id)
            if profile is not None:
                _L.info(
                    "label={} energy={:.2f} valence={:+.2f} openness={:.2f} tension={:.2f}{}",
                    profile.label, profile.energy, profile.valence, profile.openness, profile.tension,
                    f" anomaly={profile.anomaly_reason!r}" if profile.anomaly_reason else "",
                )
            ctx.add_block(text=text, label="当前时间", position="dynamic", priority=10, source="schedule")
        if self._event_replan_enabled:
            replan_guidance = self._build_event_replan_guidance(ctx)
            if replan_guidance:
                ctx.add_block(
                    text=replan_guidance,
                    label="剧情约束",
                    position="dynamic",
                    priority=11,
                    source="schedule.event_replan",
                )
        guidance_builder = getattr(self._mood_engine, "build_m1_tension_guidance", None)
        if self._dialogue_climate_m1_enabled and callable(guidance_builder):
            guidance_value = guidance_builder(
                group_id=ctx.group_id,
                session_id=ctx.session_id,
                m1_enabled=True,
            )
            guidance = guidance_value if isinstance(guidance_value, str) else ""
            if guidance:
                ctx.add_block(
                    text=guidance,
                    label="对话气氛",
                    position="dynamic",
                    priority=12,
                    source="schedule.m1",
                )

    def _build_event_replan_guidance(self, ctx: PromptContext) -> str:
        if self._schedule_store is None or self._story_arc_store is None:
            return ""
        schedule = getattr(self._schedule_store, "current", None)
        if schedule is None:
            return ""
        try:
            arc = self._story_arc_store.load_active()
        except Exception as exc:
            _L.warning("event replan active arc lookup failed | err={}", exc)
            return ""
        if arc is None:
            return ""

        tension = 0.0
        if self._dialogue_climate_m1_enabled:
            resolver = getattr(self._mood_engine, "resolve_m1_tension", None)
            if callable(resolver):
                try:
                    resolved = resolver(group_id=ctx.group_id, session_id=ctx.session_id)
                    if isinstance(resolved, str | int | float):
                        tension = float(resolved)
                except Exception as exc:
                    _L.warning("event replan tension lookup failed | err={}", exc)
                    tension = 0.0

        applied = _apply_event_replan_if_needed(
            schedule,
            arc,
            tension=tension,
            now=datetime.now(ZoneInfo("Asia/Shanghai")),
        )
        if applied:
            self._save_event_replan(schedule, arc)
            return _render_event_replan_guidance(arc, applied)
        return _render_active_event_replan_guidance(arc)

    def _save_event_replan(self, schedule: Schedule, arc: StoryArc) -> None:
        if self._schedule_store is None or self._story_arc_store is None:
            return
        try:
            schedule_store = self._schedule_store
            schedule_store.save(schedule)
        except Exception as exc:
            _L.warning("event replan schedule save failed | err={}", exc)
        try:
            story_arc_store = self._story_arc_store
            story_arc_store.save(arc)
        except Exception as exc:
            _L.warning("event replan story arc save failed | arc_id={} err={}", arc.arc_id, exc)


def _apply_event_replan_if_needed(
    schedule: Schedule,
    arc: StoryArc,
    *,
    tension: float = 0.0,
    now: datetime,
) -> dict[str, Any] | None:
    pressure = _event_replan_pressure(arc)
    tension = max(0.0, min(1.0, float(tension or 0.0)))
    if tension < 0.12 and pressure < 0.85:
        return None

    candidate = StoryArcEventCandidate(
        event_id="partner_minor_setback_replan",
        event_type="partner_setback",
        severity="setback",
        salience=1.0 + tension + pressure,
        once_only=True,
        cooldown_key="partner_setback",
        cooldown_steps=3,
    )
    now_step = int(arc.event_budget.get("generated_days", 0) or 0)
    if not record_event_trigger(arc, candidate, now_step=now_step):
        return None

    partner_name = _select_fiction_partner_name(arc)
    reason = (
        f"M1 tension={tension:.2f}，互动张力升高"
        if tension >= 0.12
        else f"deadline/exam pressure={pressure:.2f}"
    )
    summary = f"{partner_name}作为 fiction 伙伴轻微扭伤，团队把舞台动作临时降难度。"
    constraint = (
        f"{summary}接下来 2-3 天的排练以低难度站位、台词复盘和稳定团队情绪为主；"
        "不要重写已经发生的日程。"
    )

    _override_schedule_slots(schedule, partner_name, now=now)
    _update_arc_for_event_replan(arc, partner_name, summary, constraint, now=now, reason=reason)
    return {
        "event_id": candidate.event_id,
        "partner_name": partner_name,
        "summary": summary,
        "constraint": constraint,
        "reason": reason,
        "remaining_days": 3,
    }


def _event_replan_pressure(arc: StoryArc) -> float:
    pressure = 0.0
    deadline = arc.variables.get("deadline_days_left")
    if isinstance(deadline, int | float):
        pressure = max(pressure, 1.0 if float(deadline) <= 1 else 0.9 if float(deadline) <= 2 else 0.0)
    exam_pressure = arc.variables.get("exam_pressure")
    if isinstance(exam_pressure, int | float):
        pressure = max(pressure, float(exam_pressure))
    return max(0.0, min(1.0, pressure))


def _select_fiction_partner_name(arc: StoryArc) -> str:
    for entity_id, state in arc.partner_states.items():
        if isinstance(state, dict) and state.get("kind") == "fiction":
            return str(state.get("display_name") or entity_id)
    return "虚构伙伴"


def _override_schedule_slots(schedule: Schedule, partner_name: str, *, now: datetime) -> None:
    current_slot = schedule.current_slot(now)
    start_index = 0
    if current_slot is not None:
        for index, slot in enumerate(schedule.slots):
            if slot is current_slot:
                start_index = index
                break
    touched = 0
    for slot in schedule.slots[start_index:]:
        if touched >= 3:
            break
        if slot.activity not in {"practice", "study", "social", "online", "rest"}:
            continue
        slot.description = _merge_replan_description(slot.description, partner_name)
        slot.mood_hint = "担心但冷静降难度"
        touched += 1


def _merge_replan_description(description: str, partner_name: str) -> str:
    base = str(description or "").strip()
    replan = f"受{partner_name}轻微扭伤影响，临时改成低难度站位和台词复盘。"
    if not base:
        return replan
    if replan in base:
        return base
    return f"{base}；{replan}"


def _update_arc_for_event_replan(
    arc: StoryArc,
    partner_name: str,
    summary: str,
    constraint: str,
    *,
    now: datetime,
    reason: str,
) -> None:
    today = now.strftime("%Y-%m-%d")
    arc.stage = "setback_replan"
    arc.last_events.append({
        "date": today,
        "source": "event_replan",
        "summary": summary,
        "reason": reason,
    })
    arc.last_events = arc.last_events[-6:]
    thread = f"{partner_name}轻微扭伤后的降难度站位怎么调整"
    if thread not in arc.open_threads:
        arc.open_threads.append(thread)
    arc.open_threads = arc.open_threads[-6:]
    arc.next_day_seed = "承接伙伴轻微扭伤后的降难度排练，安排复盘和团队情绪恢复。"
    rehearsal = arc.variables.get("rehearsal_progress")
    if isinstance(rehearsal, int | float):
        arc.variables["rehearsal_progress"] = round(max(0.0, float(rehearsal) - 0.04), 3)
    morale = arc.variables.get("team_morale")
    if isinstance(morale, int | float):
        arc.variables["team_morale"] = round(max(0.0, float(morale) - 0.05), 3)
    constraints = [
        item for item in arc.event_budget.get("active_replan_constraints", [])
        if isinstance(item, dict)
    ]
    constraints.append({
        "event_id": "partner_minor_setback_replan",
        "source": "event_replan",
        "constraint": constraint,
        "remaining_days": 3,
        "created_on": today,
    })
    arc.event_budget["active_replan_constraints"] = constraints[-3:]


def _render_event_replan_guidance(arc: StoryArc, event: dict[str, Any]) -> str:
    summary = str(event.get("summary", "") or "").strip()
    constraint = str(event.get("constraint", "") or "").strip()
    return (
        "【近端剧情约束】\n"
        f"- 刚发生：{summary}\n"
        f"- 约束：{constraint}\n"
        f"- 当前 arc stage={arc.stage}；如果被问“这周怎么了”，概括为舞台剧准备周里临时降难度、复盘与恢复团队节奏。"
    )


def _render_active_event_replan_guidance(arc: StoryArc) -> str:
    raw = arc.event_budget.get("active_replan_constraints")
    if not isinstance(raw, list):
        return ""
    lines = ["【近端剧情约束】"]
    for item in raw[:2]:
        if not isinstance(item, dict):
            continue
        remaining = int(item.get("remaining_days", 0) or 0)
        constraint = str(item.get("constraint", "") or "").strip()
        if remaining <= 0 or not constraint:
            continue
        lines.append(f"- remaining_days={remaining}：{constraint}")
    if len(lines) == 1:
        return ""
    lines.append("- 如果被问“这周怎么了”，概括为舞台剧准备周里伙伴轻微受挫、临时降难度、复盘并恢复团队节奏。")
    lines.append("- 不重写历史，只让当前回复和后续日程自然承接。")
    return "\n".join(lines)
