"""RED regressions for schedule event-replan ownership and atomicity."""

from __future__ import annotations

import copy
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from kernel.types import Identity, PromptContext
from plugins.schedule.plugin import SchedulePlugin
from plugins.schedule.story_arc import StoryArc, StoryArcStore
from plugins.schedule.types import Schedule, TimeSlot
from services.dialogue_climate.dynamics import ClimateEngine


class _FixedPromptDateTime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> _FixedPromptDateTime:
        return cls(2026, 6, 8, 10, 30, tzinfo=tz)


class _FailingScheduleStore:
    def __init__(self, schedule: Schedule) -> None:
        self.current = schedule
        self.save_calls = 0

    def save(self, _schedule: Schedule) -> None:
        self.save_calls += 1
        raise OSError("schedule write failed")


class _SavingScheduleStore:
    def __init__(self, schedule: Schedule) -> None:
        self.current = schedule
        self.saved: list[Schedule] = []

    def save(self, schedule: Schedule) -> None:
        self.saved.append(schedule)
        self.current = schedule


def _make_schedule() -> Schedule:
    return Schedule(
        date="2026-06-08",
        day_narrative="舞台剧比赛准备周进入中段。",
        theme="舞台剧推进日",
        slots=[
            TimeSlot(
                time="10:00",
                activity="practice",
                mood_hint="期待",
                location="凤凰奇幻乐园",
                description="和伙伴合练高难度转场动作",
            ),
            TimeSlot(
                time="15:00",
                activity="practice",
                mood_hint="专注",
                location="凤凰奇幻乐园",
                description="把舞台动作推进到下一段",
            ),
        ],
    )


def _make_arc(*, exam_pressure: float) -> StoryArc:
    return StoryArc(
        arc_id="stage_play_competition_week",
        title="舞台剧比赛准备周",
        stage="preparation",
        variables={
            "deadline_days_left": 5,
            "exam_pressure": exam_pressure,
            "rehearsal_progress": 0.5,
            "team_morale": 0.7,
        },
        partner_states={
            "tenma_tsukasa": {
                "kind": "fiction",
                "display_name": "天马司",
            },
        },
        event_budget={"generated_days": 2},
    )


def _prompt_context() -> PromptContext:
    return PromptContext(
        session_id="group_g1",
        group_id="g1",
        user_id="u1",
        identity=Identity(id="fengxiaomeng-v2", name="凤笑梦"),
    )


async def _started_plugin(
    *,
    schedule_store: Any,
    story_arc_store: StoryArcStore,
    climate_engine: Any = None,
    m4_owns_behavior: bool = False,
) -> SchedulePlugin:
    plugin = SchedulePlugin()
    ctx = SimpleNamespace(
        mood_engine=None,
        schedule_store=schedule_store,
        schedule_gen=None,
        timeline=None,
        schedule_event_replan_enabled=True,
        story_arc_store=story_arc_store,
        climate_engine=climate_engine,
        climate_sensor_hub=None,
        dialogue_climate_m4_enabled=m4_owns_behavior,
        affection_engine=None,
        affection_enabled=False,
        bus=None,
        calendar_service=None,
        provider_bus=None,
        runtime_state=None,
    )
    await plugin.on_startup(cast(Any, ctx))
    return plugin


@pytest.mark.asyncio
async def test_event_replan_schedule_save_failure_leaves_story_arc_uncommitted(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("plugins.schedule.plugin.datetime", _FixedPromptDateTime)
    story_store = StoryArcStore(tmp_path / "story_arcs")
    await story_store.startup()
    story_store.save(_make_arc(exam_pressure=0.9))
    before = story_store.load("stage_play_competition_week")
    assert before is not None
    schedule_store = _FailingScheduleStore(_make_schedule())
    plugin = await _started_plugin(
        schedule_store=schedule_store,
        story_arc_store=story_store,
    )

    guidance = plugin._build_event_replan_guidance(_prompt_context())

    after = story_store.load("stage_play_competition_week")
    assert after is not None
    assert schedule_store.save_calls == 1
    assert {
        "revision": after.revision,
        "stage": after.stage,
        "event_budget": after.event_budget,
        "guidance": guidance,
    } == {
        "revision": before.revision,
        "stage": before.stage,
        "event_budget": copy.deepcopy(before.event_budget),
        "guidance": "",
    }


class _FailingArcPersistStore(StoryArcStore):
    """Persist schedule first, then fail Arc commit so restore must run."""

    def __init__(self, root: Any) -> None:
        super().__init__(root)
        self.fail_next_save = False
        self.save_attempts = 0

    def _save_unlocked(self, arc: StoryArc) -> None:  # type: ignore[override]
        self.save_attempts += 1
        if self.fail_next_save:
            self.fail_next_save = False
            raise OSError("story arc write failed")
        super()._save_unlocked(arc)


@pytest.mark.asyncio
async def test_event_replan_arc_commit_failure_restores_original_schedule(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("plugins.schedule.plugin.datetime", _FixedPromptDateTime)
    story_store = _FailingArcPersistStore(tmp_path / "story_arcs_fail")
    await story_store.startup()
    original_arc = _make_arc(exam_pressure=0.9)
    story_store.save(original_arc)
    before = story_store.load("stage_play_competition_week")
    assert before is not None
    original_schedule = _make_schedule()
    schedule_store = _SavingScheduleStore(original_schedule)
    plugin = await _started_plugin(
        schedule_store=schedule_store,
        story_arc_store=story_store,
    )
    story_store.fail_next_save = True

    guidance = plugin._build_event_replan_guidance(_prompt_context())

    after = story_store.load("stage_play_competition_week")
    assert after is not None
    assert story_store.save_attempts >= 1
    assert len(schedule_store.saved) >= 2, "replan save + restore must both persist"
    restored = schedule_store.current
    assert restored is not None
    assert [slot.description for slot in restored.slots] == [
        slot.description for slot in original_schedule.slots
    ]
    assert [slot.mood_hint for slot in restored.slots] == [
        slot.mood_hint for slot in original_schedule.slots
    ]
    assert {
        "revision": after.revision,
        "stage": after.stage,
        "event_budget": after.event_budget,
        "guidance": guidance,
    } == {
        "revision": before.revision,
        "stage": before.stage,
        "event_budget": copy.deepcopy(before.event_budget),
        "guidance": "",
    }


@pytest.mark.asyncio
async def test_event_replan_ignores_shadow_climate_but_keeps_fiction_pressure_trigger(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("plugins.schedule.plugin.datetime", _FixedPromptDateTime)
    climate_engine = ClimateEngine(m2_enabled=True)
    assert climate_engine.register_signal(
        dim="tension",
        delta=1.0,
        source="m3_sensor_test",
        group_id="g1",
        user_id="u1",
    )
    assert climate_engine.resolve(group_id="g1", user_id="u1").tension >= 0.12

    low_story_store = StoryArcStore(tmp_path / "low_pressure_arcs")
    await low_story_store.startup()
    low_story_store.save(_make_arc(exam_pressure=0.3))
    low_schedule_store = _SavingScheduleStore(_make_schedule())
    low_plugin = await _started_plugin(
        schedule_store=low_schedule_store,
        story_arc_store=low_story_store,
        climate_engine=climate_engine,
        m4_owns_behavior=False,
    )

    low_guidance = low_plugin._build_event_replan_guidance(_prompt_context())

    high_story_store = StoryArcStore(tmp_path / "high_pressure_arcs")
    await high_story_store.startup()
    high_story_store.save(_make_arc(exam_pressure=0.9))
    high_schedule_store = _SavingScheduleStore(_make_schedule())
    high_plugin = await _started_plugin(
        schedule_store=high_schedule_store,
        story_arc_store=high_story_store,
        climate_engine=climate_engine,
        m4_owns_behavior=False,
    )

    high_guidance = high_plugin._build_event_replan_guidance(_prompt_context())

    low_arc = low_story_store.load("stage_play_competition_week")
    high_arc = high_story_store.load("stage_play_competition_week")
    assert low_arc is not None
    assert high_arc is not None
    high_reason = str(high_arc.last_events[-1].get("reason", "")) if high_arc.last_events else ""
    assert {
        "low": {
            "guidance": low_guidance,
            "schedule_saves": len(low_schedule_store.saved),
            "stage": low_arc.stage,
            "event_budget": low_arc.event_budget,
        },
        "high": {
            "guidance_present": bool(high_guidance),
            "schedule_saves": len(high_schedule_store.saved),
            "stage": high_arc.stage,
            "setback_count": high_arc.event_budget.get("setback_count"),
            "pressure_reason": "deadline/exam pressure" in high_reason,
        },
    } == {
        "low": {
            "guidance": "",
            "schedule_saves": 0,
            "stage": "preparation",
            "event_budget": {"generated_days": 2},
        },
        "high": {
            "guidance_present": True,
            "schedule_saves": 1,
            "stage": "setback_replan",
            "setback_count": 1,
            "pressure_reason": True,
        },
    }
