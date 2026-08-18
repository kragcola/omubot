"""SchedulePlugin: 日程与心情系统。

通过 on_pre_prompt 注入当前时间和心情到 system prompt。
通过 on_bot_connect 启动日程生成器后台循环。
"""

from __future__ import annotations

import contextlib
import copy
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from loguru import logger
from pydantic import BaseModel, Field

from kernel.types import AmadeusPlugin, PluginContext, PromptContext, ReplyContext
from plugins.schedule.story_arc import (
    JournalEventRecord,
    StoryArc,
    StoryArcEventCandidate,
    record_event_trigger,
)
from plugins.schedule.types import Schedule


class DialogueClimateConfig(BaseModel):
    """Dialogue Climate runtime flags."""

    m2_enabled: bool = False
    m3_sensors_enabled: bool = False
    m4_policy_enabled: bool = False


class ScheduleConfig(BaseModel):
    """模拟日程系统配置。"""

    enabled: bool = True
    storage_dir: str = "storage/schedule"
    generate_at_hour: int = 2
    persona_driven_enabled: bool = False
    story_arc_enabled: bool = False
    event_replan_enabled: bool = False
    local_billing_fallback_enabled: bool = False
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
        self._mood_engine: Any = None
        self._schedule_store = None
        self._schedule_gen = None
        self._timeline = None
        self._root_ctx: PluginContext | None = None
        self._schedule_started = False
        self._event_replan_enabled = False
        self._climate_sensor_hub = None
        self._climate_engine = None
        self._m4_policy_enabled = False
        self._affection_engine: Any = None
        self._affection_enabled = False
        self._bus: Any = None
        self._calendar_service = None
        self._story_arc_store = None
        self._provider_bus: Any = None
        self._runtime_state: Any = None

    async def on_startup(self, ctx: PluginContext) -> None:
        self._root_ctx = ctx
        self._mood_engine = ctx.mood_engine
        self._schedule_store = ctx.schedule_store
        self._schedule_gen = ctx.schedule_gen
        self._timeline = ctx.timeline
        self._event_replan_enabled = bool(getattr(ctx, "schedule_event_replan_enabled", False))
        self._story_arc_store = getattr(ctx, "story_arc_store", None)
        self._provider_bus = getattr(ctx, "provider_bus", None)
        self._runtime_state = getattr(ctx, "runtime_state", None)
        self._climate_sensor_hub = getattr(ctx, "climate_sensor_hub", None)
        self._climate_engine = getattr(ctx, "climate_engine", None)
        self._m4_policy_enabled = bool(getattr(ctx, "dialogue_climate_m4_enabled", False))
        self._affection_engine = getattr(ctx, "affection_engine", None)
        self._affection_enabled = bool(
            getattr(ctx, "affection_enabled", self._affection_engine is not None)
        )
        self._bus = getattr(ctx, "bus", None)
        self._calendar_service = getattr(ctx, "calendar_service", None)

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
                else:
                    resume = getattr(
                        self._schedule_gen,
                        "resume_worldbook_governance",
                        None,
                    )
                    if callable(resume):
                        await cast(Callable[[str], Awaitable[Any]], resume)(today)

    async def on_shutdown(self, ctx: PluginContext) -> None:
        del ctx
        if not self._schedule_started or self._schedule_gen is None:
            self._root_ctx = None
            return
        self._schedule_started = False
        try:
            await self._schedule_gen.stop()
        finally:
            self._root_ctx = None

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if self._mood_engine is None or self._schedule_store is None:
            return
        recent_count = 0
        if ctx.group_id is not None and self._timeline is not None:
            recent_count = self._timeline.recent_interaction_count(str(ctx.group_id), window_s=60.0)
        climate_provider_ready = self._climate_provider_ready(ctx)
        text = self._mood_engine.build_mood_block(
            self._schedule_store.current,
            recent_interaction_count=recent_count,
            group_id=ctx.group_id,
            session_id=ctx.session_id,
            include_mood_guidance=not climate_provider_ready,
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
        if not climate_provider_ready:
            self._feed_climate_sensors(ctx)
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
        self._maybe_inject_climate_block(ctx)

    async def on_post_reply(self, ctx: ReplyContext) -> None:
        """M3 feedback loop: a reply to this user happened → small climate nudge.

        Closes the perceive→act→feedback loop (design master §4). Uses only the
        fields ReplyContext actually carries (F6: user_id/group_id/elapsed_ms —
        no register/length). No-op unless the hub is wired + m3 enabled.
        """
        hub = self._climate_sensor_hub
        if hub is None or not getattr(hub, "enabled", False):
            return
        engine = getattr(hub, "_engine", None)
        if engine is None:
            return
        with contextlib.suppress(Exception):  # never break the post-reply chain
            # A completed reply to this user is a small positive interaction:
            # nudge openness up slightly (we engaged), keyed per-(group, user).
            engine.register_signal(
                dim="openness",
                delta=0.05,
                source="post_reply",
                group_id=str(ctx.group_id or ""),
                user_id=str(ctx.user_id or ""),
            )

    def _maybe_inject_climate_block(self, ctx: PromptContext) -> bool:
        """M4: synthesize ClimatePolicy from the resolved ClimateState and inject
        a single "对话气候" block. Returns True when it took over (so the caller
        skips the legacy M1 block — climate supersedes it). No-op + False unless
        m4_policy_enabled and the engine is live. Best-effort.
        """
        if not self._m4_policy_enabled:
            return False
        if self._climate_provider_ready(ctx):
            return True
        engine = self._climate_engine
        if engine is None or not getattr(engine, "enabled", False):
            return False
        try:
            from services.dialogue_climate.policy import synthesize

            state = engine.resolve(group_id=ctx.group_id, user_id=ctx.user_id)
            policy = synthesize(state)
            if policy.guidance:
                ctx.add_block(
                    text=policy.guidance,
                    label="对话气候",
                    position="dynamic",
                    priority=12,
                    source="schedule.m4_climate",
                )
            return True  # climate owns the affect block this turn (supersedes M1)
        except Exception as exc:  # never break the prompt path
            _L.debug("climate policy block failed | err={}", exc)
            return False

    def _climate_provider_ready(self, ctx: PromptContext) -> bool:
        provider_bus = self._provider_bus
        has_provider = getattr(provider_bus, "has_provider", None)
        if not callable(has_provider) or not bool(has_provider("climate")):
            return False
        try:
            from services.block_trace.climate_provider import has_climate_prompt_candidate

            return has_climate_prompt_candidate(
                self._runtime_state,
                session_id=ctx.session_id,
                group_id=ctx.group_id,
                user_id=ctx.user_id,
            )
        except Exception:
            return False

    def _feed_climate_sensors(self, ctx: PromptContext) -> None:
        """M3: feed Schedule/Circadian/Interaction/Calendar signals per reply.

        No-op unless the SensorHub is wired and m3_sensors_enabled (the hub gates
        internally). Per-(group, user) keyed. Irritation is fed on its own
        @/poke path (qq_interactions); Message on the classifier path. Here we
        contribute mood dims + local hour + per-user familiarity + rich day
        context. Best-effort — never raise into the prompt path.
        """
        hub = self._climate_sensor_hub
        if hub is None or not getattr(hub, "enabled", False):
            return
        try:
            from services.dialogue_climate.sensors import SensorInput

            now = datetime.now()
            profile = self._mood_engine.cached_profile(group_id=ctx.group_id, session_id=ctx.session_id)
            familiarity = self._resolve_familiarity(ctx.user_id)
            is_holiday, self_birthday = self._resolve_day_context(now)
            data = SensorInput(
                group_id=str(ctx.group_id or ""),
                user_id=str(ctx.user_id or ""),
                mood_energy=getattr(profile, "energy", None) if profile else None,
                mood_valence=getattr(profile, "valence", None) if profile else None,
                mood_openness=getattr(profile, "openness", None) if profile else None,
                mood_tension=getattr(profile, "tension", None) if profile else None,
                hour=now.hour,
                familiarity=familiarity,
                is_holiday=is_holiday,
                has_self_birthday=self_birthday,
            )
            hub.collect(data)
        except Exception as exc:  # never break the reply path
            _L.debug("climate sensor feed failed | err={}", exc)

    def _resolve_familiarity(self, user_id: str) -> float | None:
        """Per-user familiarity from AffectionEngine (None when unavailable)."""
        get_plugin = getattr(self._bus, "get_plugin", None)
        if callable(get_plugin):
            owner = get_plugin("affection")
            affection_enabled = (
                bool(getattr(owner, "enabled", False))
                if owner is not None
                else self._affection_enabled
            )
            if not affection_enabled:
                return None
        elif not self._affection_enabled:
            return None
        engine = self._affection_engine
        if engine is None or not user_id or user_id == "0":
            return None
        fn = getattr(engine, "familiarity_score", None)
        if not callable(fn):
            return None
        try:
            return float(cast(Any, fn)(user_id))
        except Exception:
            return None

    def _resolve_day_context(self, now: Any) -> tuple[bool, bool]:
        """Rich day context (calendar_context) → (is_holiday, is_self_birthday)."""
        service = self._calendar_service
        if service is None:
            return False, False
        fn = getattr(service, "get_day_context", None)
        if not callable(fn):
            return False, False
        try:
            dc = fn(now)
            return bool(getattr(dc, "is_holiday", False)), bool(getattr(dc, "is_self_birthday", False))
        except Exception:
            return False, False

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
        if self._schedule_has_governance_intent(schedule):
            return _render_active_event_replan_guidance(arc)

        tension = 0.0
        engine = self._climate_engine
        if (
            self._m4_policy_enabled
            and engine is not None
            and bool(getattr(engine, "enabled", False))
        ):
            try:
                tension = float(
                    engine.resolve(
                        group_id=ctx.group_id,
                        user_id=ctx.user_id,
                    ).tension
                )
            except Exception as exc:
                _L.warning("event replan climate tension lookup failed | err={}", exc)
                tension = 0.0

        original_schedule = copy.deepcopy(schedule)
        candidate_schedule = copy.deepcopy(schedule)
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        update = getattr(self._story_arc_store, "update", None)
        if callable(update):
            applied: dict[str, Any] | None = None
            schedule_saved = False

            def mutate(latest: StoryArc) -> None:
                nonlocal applied, schedule_saved
                applied = _apply_event_replan_if_needed(
                    candidate_schedule,
                    latest,
                    tension=tension,
                    now=now,
                )
                if applied:
                    schedule_store = cast(Any, self._schedule_store)
                    schedule_store.save(candidate_schedule)
                    schedule_saved = True

            try:
                committed_arc = cast(StoryArc, update(arc.arc_id, mutate))
            except Exception as exc:
                if schedule_saved:
                    self._restore_event_replan_schedule(original_schedule)
                _L.warning(
                    "event replan commit failed | arc_id={} err={}",
                    arc.arc_id,
                    exc,
                )
                return _render_active_event_replan_guidance(arc)
            if applied:
                return _render_event_replan_guidance(committed_arc, applied)
            return _render_active_event_replan_guidance(committed_arc)

        candidate_arc = copy.deepcopy(arc)
        applied = _apply_event_replan_if_needed(
            candidate_schedule,
            candidate_arc,
            tension=tension,
            now=now,
        )
        if applied:
            if not self._save_event_replan(
                candidate_schedule,
                candidate_arc,
                original_schedule=original_schedule,
            ):
                return _render_active_event_replan_guidance(arc)
            return _render_event_replan_guidance(candidate_arc, applied)
        return _render_active_event_replan_guidance(arc)

    def _schedule_has_governance_intent(self, schedule: Schedule) -> bool:
        """Keep event replanning away from immutable governed sources."""
        if self._worldbook_schedule_projection_enabled():
            # Worldbook schedule projection has one write path: the attached
            # governance bridge.  A missing marker is not permission to treat
            # an old or malformed source as a mutable legacy schedule.
            return True
        if self._schedule_store is None:
            return False
        load_snapshot = getattr(self._schedule_store, "load_governance_snapshot", None)
        if not callable(load_snapshot):
            return False
        try:
            snapshot = load_snapshot(schedule.date)
        except Exception as exc:
            _L.warning(
                "event replan governance snapshot lookup failed; skipping replan | "
                "date={} err={}",
                schedule.date,
                type(exc).__name__,
            )
            return True
        return snapshot is not None and getattr(snapshot, "governance_intent", None) is not None

    def _worldbook_schedule_projection_enabled(self) -> bool:
        """Read the late-mounted Worldbook schedule gate from PluginContext."""
        runtime = getattr(self._root_ctx, "worldbook_runtime", None)
        config = getattr(runtime, "config", None)
        return bool(
            getattr(config, "enabled", False)
            and getattr(config, "schedule_projection_enabled", False)
        )

    def _save_event_replan(
        self,
        schedule: Schedule,
        arc: StoryArc,
        *,
        original_schedule: Schedule,
    ) -> bool:
        if self._schedule_store is None or self._story_arc_store is None:
            return False
        try:
            schedule_store = self._schedule_store
            schedule_store.save(schedule)
        except Exception as exc:
            _L.warning("event replan schedule save failed | err={}", exc)
            return False
        try:
            story_arc_store = self._story_arc_store
            story_arc_store.save(arc)
        except Exception as exc:
            self._restore_event_replan_schedule(original_schedule)
            _L.warning("event replan story arc save failed | arc_id={} err={}", arc.arc_id, exc)
            return False
        return True

    def _restore_event_replan_schedule(self, schedule: Schedule) -> None:
        try:
            schedule_store = cast(Any, self._schedule_store)
            schedule_store.save(schedule)
        except Exception as exc:
            _L.error("event replan schedule rollback failed | err={}", exc)


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
        f"Dialogue Climate tension={tension:.2f}，互动张力升高"
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
    privacy = (
        "public"
        if str(getattr(arc, "scope", "") or "").strip() == "fiction"
        else "unknown"
    )
    event = JournalEventRecord(
        date=today,
        source="event_replan",
        summary=summary,
        subject_kind="fiction",
        privacy=privacy,  # type: ignore[arg-type]
        salience=0.95,
        event_id=f"event_replan:{today}",
    ).to_dict()
    event["reason"] = reason
    arc.last_events.append(event)
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
