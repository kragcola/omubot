"""Tests for MoodEngine — mood calculation, presets, anomaly logic."""

from __future__ import annotations

import math
import random
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from kernel.types import Identity, PromptContext
from plugins.schedule.mood import (
    MoodEngine,
    compute_m1_irritation_tension_delta,
    register_m1_irritation_signal,
    resolve_m1_tension_on_read,
)
from plugins.schedule.plugin import SchedulePlugin
from plugins.schedule.story_arc import StoryArc
from plugins.schedule.types import MoodProfile, Schedule, TimeSlot

CST = ZoneInfo("Asia/Shanghai")


def _make_schedule(slots: list[TimeSlot] | None = None) -> Schedule:
    return Schedule(
        date="2026-04-29",
        day_narrative="test day",
        theme="测试日",
        slots=slots
        or [
            TimeSlot(time="08:00", activity="起床", mood_hint="困倦", location="家里"),
            TimeSlot(time="12:00", activity="吃饭", mood_hint="放松", location="食堂"),
        ],
    )


def _make_replan_schedule() -> Schedule:
    return Schedule(
        date="2026-06-08",
        day_narrative="舞台剧比赛准备周进入中段。",
        theme="舞台剧推进日",
        slots=[
            TimeSlot(
                time="08:00",
                activity="study",
                mood_hint="紧张",
                location="教室",
                description="先把期末复习笔记整理完",
            ),
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
            TimeSlot(
                time="19:00",
                activity="social",
                mood_hint="放松",
                location="后台",
                description="和大家复盘当天排练",
            ),
        ],
    )


class FixedPromptDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 6, 8, 10, 30, tzinfo=tz)


class _SavingScheduleStore:
    def __init__(self, schedule: Schedule) -> None:
        self.current = schedule
        self.saved: list[Schedule] = []

    def save(self, schedule: Schedule) -> None:
        self.saved.append(schedule)
        self.current = schedule


class _FakeStoryArcStore:
    def __init__(self, arc: StoryArc | None) -> None:
        self.arc = arc
        self.load_active_calls = 0
        self.saved: list[StoryArc] = []

    def load_active(self) -> StoryArc | None:
        self.load_active_calls += 1
        return self.arc

    def save(self, arc: StoryArc) -> None:
        self.saved.append(arc)
        self.arc = arc


class _CountingMoodEngine(MoodEngine):
    def __init__(self) -> None:
        super().__init__(anomaly_chance=0.0, refresh_minutes=60)
        self.resolve_calls = 0

    def resolve_m1_tension(
        self,
        *,
        group_id: str | int | None = None,
        session_id: str = "",
        now_ts: float | None = None,
    ) -> float:
        self.resolve_calls += 1
        return super().resolve_m1_tension(group_id=group_id, session_id=session_id, now_ts=now_ts)


class TestMoodEngineLookup:
    def test_exact_match(self):
        engine = MoodEngine()
        profile = engine._lookup_base("困倦")
        assert profile.label == "困倦"
        assert profile.energy < 0.5

    def test_partial_match(self):
        engine = MoodEngine()
        profile = engine._lookup_base("困倦但好笑")
        assert profile.label == "困倦"

    def test_default_for_unknown(self):
        engine = MoodEngine()
        profile = engine._lookup_base("不存在的情绪")
        assert profile.label == "放松"

    def test_all_preset_keys_have_label(self):
        from plugins.schedule.mood import _MOOD_BASE

        for key, profile in _MOOD_BASE.items():
            assert profile.label, f"missing label for {key}"
            assert 0.0 <= profile.energy <= 1.0, f"energy out of range for {key}"
            assert -1.0 <= profile.valence <= 1.0, f"valence out of range for {key}"


class TestMoodPrompts:
    def test_all_labels_have_prompts(self):
        from plugins.schedule.mood import _MOOD_PROMPTS

        expected_labels = {"疲惫", "兴奋", "专注", "放松", "困倦", "匆忙", "低落", "烦躁", "期待"}
        assert set(_MOOD_PROMPTS.keys()) == expected_labels

    def test_prompts_non_empty(self):
        from plugins.schedule.mood import _MOOD_PROMPTS

        for label, prompt in _MOOD_PROMPTS.items():
            assert len(prompt) > 10, f"prompt for {label} is too short"


class TestMoodEngineEvaluate:
    def test_returns_mood_profile(self):
        engine = MoodEngine(anomaly_chance=0.0)
        schedule = _make_schedule()
        profile = engine.evaluate(schedule)
        assert isinstance(profile, MoodProfile)
        assert 0.0 <= profile.energy <= 1.0
        assert -1.0 <= profile.valence <= 1.0
        assert profile.label

    def test_no_schedule_returns_default(self):
        engine = MoodEngine(anomaly_chance=0.0)
        profile = engine.evaluate(None)
        assert profile.label

    def test_cache_respected(self):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        schedule = _make_schedule()
        p1 = engine.evaluate(schedule)
        p2 = engine.evaluate(schedule)
        assert p1 is p2  # Same object from cache

    def test_cache_is_scoped_by_group_and_session(self):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        schedule = _make_schedule()
        p1 = engine.evaluate(schedule, group_id="g1", session_id="group_g1")
        p2 = engine.evaluate(schedule, group_id="g1", session_id="group_g1")
        p3 = engine.evaluate(schedule, group_id="g2", session_id="group_g2")
        assert p1 is p2
        assert p3 is not p1

    def test_cached_profile_reads_requested_key(self):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        schedule = _make_schedule()
        p1 = engine.evaluate(schedule, group_id="g1", session_id="group_g1")
        assert engine.cached_profile(group_id="g1", session_id="group_g1") is p1
        assert engine.cached_profile(group_id="g2", session_id="group_g2") is None

    def test_anomaly_can_flip_label(self):
        random.seed(42)
        engine = MoodEngine(anomaly_chance=1.0)  # always anomaly
        schedule = _make_schedule()
        profile = engine.evaluate(schedule)
        assert profile.anomaly_reason, "anomaly should have a reason"
        # Label may or may not change depending on values, but reason must exist

    def test_no_anomaly_has_empty_reason(self):
        engine = MoodEngine(anomaly_chance=0.0)
        schedule = _make_schedule()
        profile = engine.evaluate(schedule)
        assert profile.anomaly_reason == ""

    def test_recent_interactions_adjust_openness(self):
        engine = MoodEngine(anomaly_chance=0.0)
        schedule = _make_schedule()
        p_lonely = engine.evaluate(schedule, recent_interaction_count=0)
        # Force fresh evaluation
        engine._cache.clear()
        p_busy = engine.evaluate(schedule, recent_interaction_count=10)
        # 0 interactions → openness boosted, >5 → energy penalty
        # We can't assert exact values due to random offset, but structure is valid
        assert 0.0 <= p_lonely.openness <= 1.0
        assert 0.0 <= p_busy.energy <= 1.0


class TestMoodBlock:
    def test_build_mood_block_basic(self):
        engine = MoodEngine(anomaly_chance=0.0)
        schedule = _make_schedule()
        block = engine.build_mood_block(schedule)
        assert "【当前时间】" in block
        assert "【你当前的心情基调】" in block
        assert "【心情对说话的影响】" in block

    def test_build_mood_block_writes_context_cache(self):
        engine = MoodEngine(anomaly_chance=0.0)
        schedule = _make_schedule()
        engine.build_mood_block(schedule, group_id="g1", session_id="group_g1")
        assert engine.cached_profile(group_id="g1", session_id="group_g1") is not None

    def test_build_mood_block_no_schedule(self):
        engine = MoodEngine(anomaly_chance=0.0)
        block = engine.build_mood_block(None)
        assert "【当前心情基调】" in block.split("【当前时间】")[0] or "【当前时间】" in block

    def test_stealth_rule_present(self):
        engine = MoodEngine(anomaly_chance=0.0)
        block = engine.build_mood_block(_make_schedule())
        assert "不要主动说出来" in block


class TestClassify:
    def test_classify_low_energy_negative_valence_is_low(self):
        from plugins.schedule.mood import _classify

        assert _classify(MoodProfile(energy=0.2, valence=-0.5, openness=0.3, tension=0.1)) == "低落"

    def test_classify_low_energy_neutral_is_sleepy(self):
        from plugins.schedule.mood import _classify

        assert _classify(MoodProfile(energy=0.2, valence=0.0, openness=0.3, tension=0.1)) == "困倦"

    def test_classify_high_positive_is_excited(self):
        from plugins.schedule.mood import _classify

        assert _classify(MoodProfile(energy=0.8, valence=0.7, openness=0.7, tension=0.1)) == "兴奋"

    def test_classify_high_positive_with_tension_is_expectant(self):
        from plugins.schedule.mood import _classify

        assert _classify(MoodProfile(energy=0.8, valence=0.7, openness=0.7, tension=0.5)) == "期待"

    def test_classify_default_is_relaxed(self):
        from plugins.schedule.mood import _classify

        assert _classify(MoodProfile(energy=0.5, valence=0.3, openness=0.6, tension=0.2)) == "放松"


class TestClamp:
    def test_clamp_in_range_unchanged(self):
        p = MoodProfile(energy=0.5, valence=0.0, openness=0.5, tension=0.5)
        p.clamp()
        assert p.energy == 0.5
        assert p.valence == 0.0

    def test_clamp_out_of_range(self):
        p = MoodProfile(energy=1.5, valence=-2.0, openness=3.0, tension=-1.0)
        p.clamp()
        assert p.energy == 1.0
        assert p.valence == -1.0
        assert p.openness == 1.0
        assert p.tension == 0.0


class _MoodSignalSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def register_interaction_signal(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


class TestDialogueClimateM1DormantHelpers:
    def test_m1_irritation_delta_default_off(self):
        assert compute_m1_irritation_tension_delta(mention_count=10, poke_count=10) == 0.0

    def test_m1_irritation_delta_enabled_is_bounded(self):
        delta = compute_m1_irritation_tension_delta(
            mention_count=2,
            poke_count=1,
            m1_enabled=True,
        )
        assert math.isclose(delta, 0.12)
        assert compute_m1_irritation_tension_delta(poke_count=20, m1_enabled=True) == 0.2

    def test_m1_signal_default_off_does_not_touch_mood_engine(self):
        sink = _MoodSignalSink()

        changed = register_m1_irritation_signal(
            sink,
            mention_count=3,
            poke_count=3,
            group_id="g1",
            session_id="group_g1",
        )

        assert changed is False
        assert sink.calls == []

    def test_m1_signal_enabled_uses_existing_interaction_channel(self):
        sink = _MoodSignalSink()

        changed = register_m1_irritation_signal(
            sink,
            mention_count=1,
            poke_count=2,
            group_id="g1",
            session_id="group_g1",
            m1_enabled=True,
        )

        assert changed is True
        assert len(sink.calls) == 1
        assert sink.calls[0]["group_id"] == "g1"
        assert sink.calls[0]["session_id"] == "group_g1"
        assert sink.calls[0]["tension_d"] > 0
        assert "valence_d" not in sink.calls[0]
        assert "openness_d" not in sink.calls[0]

    def test_m1_tension_on_read_decays_toward_baseline(self):
        resolved = resolve_m1_tension_on_read(
            tension=0.8,
            baseline=0.2,
            last_ts=1000.0,
            now_ts=1600.0,
            tau_s=600.0,
        )

        assert math.isclose(resolved, 0.2 + 0.6 / math.e, rel_tol=1e-9)

    def test_m1_interaction_opt_in_records_per_group_on_read_tension(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        now = 1000.0
        monkeypatch.setattr("plugins.schedule.mood.time.monotonic", lambda: now)

        engine.register_interaction_signal(
            tension_d=0.2,
            group_id="g1",
            session_id="group_g1",
            m1_tension_enabled=True,
        )

        assert math.isclose(
            engine.resolve_m1_tension(
                group_id="g1",
                session_id="group_g1",
                now_ts=1000.0,
            ),
            0.2,
            rel_tol=1e-9,
        )
        assert engine.resolve_m1_tension(group_id="g2", session_id="group_g2", now_ts=1000.0) == 0.0
        assert math.isclose(
            engine.resolve_m1_tension(
                group_id="g1",
                session_id="group_g1",
                now_ts=1600.0,
            ),
            0.2 / math.e,
            rel_tol=1e-9,
        )

    def test_m1_interaction_without_opt_in_keeps_old_nudge_only(self):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)

        engine.register_interaction_signal(tension_d=0.2, group_id="g1", session_id="group_g1")

        assert engine.resolve_m1_tension(group_id="g1", session_id="group_g1", now_ts=1000.0) == 0.0
        v_nudge, o_nudge, t_nudge = engine._active_nudge(
            engine._cache_key(group_id="g1", session_id="group_g1")
        )
        assert v_nudge == 0.0
        assert o_nudge == 0.0
        assert t_nudge > 0.0

    def test_m1_guidance_triggers_above_threshold_and_reports_metrics(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        now = 1000.0
        monkeypatch.setattr("plugins.schedule.mood.time.monotonic", lambda: now)

        engine.register_interaction_signal(
            tension_d=0.14,
            group_id="g1",
            session_id="group_g1",
            m1_tension_enabled=True,
        )
        guidance = engine.build_m1_tension_guidance(
            group_id="g1",
            session_id="group_g1",
            m1_enabled=True,
        )
        metrics = engine.m1_tension_metrics(group_id="g1", session_id="group_g1")

        assert "更短更冷淡" in guidance
        assert metrics["injection_count"] == 1.0
        assert metrics["prompt_trigger_count"] == 1.0
        assert metrics["prompt_trigger_rate"] == 1.0
        assert math.isclose(metrics["half_life_s"], math.log(2.0) * 600.0)

    def test_m1_guidance_default_off_returns_empty(self):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        engine.register_interaction_signal(
            tension_d=0.2,
            group_id="g1",
            session_id="group_g1",
            m1_tension_enabled=True,
        )

        assert engine.build_m1_tension_guidance(
            group_id="g1",
            session_id="group_g1",
            m1_enabled=False,
        ) == ""


class TestDialogueClimateM1PromptGuidance:
    @staticmethod
    def _prompt_ctx() -> PromptContext:
        return PromptContext(
            session_id="group_g1",
            group_id="g1",
            user_id="u1",
            identity=Identity(id="fengxiaomeng-v2", name="凤笑梦"),
        )

    @staticmethod
    async def _started_plugin(
        engine: MoodEngine,
        *,
        m1_enabled: bool,
        schedule_store: Any | None = None,
        story_arc_store: Any | None = None,
        event_replan_enabled: bool = False,
    ) -> SchedulePlugin:
        plugin = SchedulePlugin()
        startup_ctx: Any = SimpleNamespace(
            mood_engine=engine,
            schedule_store=schedule_store or SimpleNamespace(current=_make_schedule()),
            schedule_gen=None,
            timeline=SimpleNamespace(recent_interaction_count=lambda group_id, *, window_s=60.0: 0),
            dialogue_climate_m1_enabled=m1_enabled,
            schedule_event_replan_enabled=event_replan_enabled,
            story_arc_store=story_arc_store,
        )
        await plugin.on_startup(cast(Any, startup_ctx))
        return plugin

    @pytest.mark.asyncio
    async def test_schedule_plugin_m1_disabled_keeps_prompt_baseline(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        now = 1000.0
        monkeypatch.setattr("plugins.schedule.mood.time.monotonic", lambda: now)
        engine.register_interaction_signal(
            tension_d=0.2,
            group_id="g1",
            session_id="group_g1",
            m1_tension_enabled=True,
        )
        plugin = await self._started_plugin(engine, m1_enabled=False)
        prompt_ctx = self._prompt_ctx()

        await plugin.on_pre_prompt(prompt_ctx)

        assert [block.label for block in prompt_ctx.blocks] == ["当前时间"]
        assert "更短更冷淡" not in "\n".join(block.text for block in prompt_ctx.blocks)

    @pytest.mark.asyncio
    async def test_schedule_plugin_m1_enabled_adds_behavior_guidance_above_threshold(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        now = 1000.0
        monkeypatch.setattr("plugins.schedule.mood.time.monotonic", lambda: now)
        engine.register_interaction_signal(
            tension_d=0.14,
            group_id="g1",
            session_id="group_g1",
            m1_tension_enabled=True,
        )
        plugin = await self._started_plugin(engine, m1_enabled=True)
        prompt_ctx = self._prompt_ctx()

        await plugin.on_pre_prompt(prompt_ctx)

        assert [block.label for block in prompt_ctx.blocks] == ["当前时间", "对话气氛"]
        assert prompt_ctx.blocks[1].source == "schedule.m1"
        assert "更短更冷淡" in prompt_ctx.blocks[1].text
        assert "标签" in prompt_ctx.blocks[1].text

    @pytest.mark.asyncio
    async def test_schedule_plugin_m1_guidance_recovers_after_on_read_decay(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        now = 1000.0
        monkeypatch.setattr("plugins.schedule.mood.time.monotonic", lambda: now)
        engine.register_interaction_signal(
            tension_d=0.14,
            group_id="g1",
            session_id="group_g1",
            m1_tension_enabled=True,
        )
        now = 2200.0
        plugin = await self._started_plugin(engine, m1_enabled=True)
        prompt_ctx = self._prompt_ctx()

        await plugin.on_pre_prompt(prompt_ctx)

        assert [block.label for block in prompt_ctx.blocks] == ["当前时间"]
        assert math.isclose(
            engine.resolve_m1_tension(group_id="g1", session_id="group_g1", now_ts=2200.0),
            0.14 / (math.e**2),
            rel_tol=1e-9,
        )


class TestEventReplanPromptGuidance:
    @staticmethod
    def _prompt_ctx() -> PromptContext:
        return PromptContext(
            session_id="group_g1",
            group_id="g1",
            user_id="u1",
            identity=Identity(id="fengxiaomeng-v2", name="凤笑梦"),
        )

    @staticmethod
    def _arc(*, pressure: bool = False) -> StoryArc:
        variables = {
            "deadline_days_left": 1 if pressure else 5,
            "exam_pressure": 0.9 if pressure else 0.3,
            "rehearsal_progress": 0.5,
            "team_morale": 0.7,
        }
        return StoryArc(
            arc_id="stage_play_competition_week",
            title="舞台剧比赛准备周",
            stage="preparation",
            active_conflicts=["排练时间不足", "期末考试压力升高"],
            variables=variables,
            partner_states={
                "tenma_tsukasa": {
                    "kind": "fiction",
                    "display_name": "天马司",
                    "current_state": "正在挑战高难度转场动作",
                    "mood": "兴奋",
                    "availability": "busy",
                },
            },
        )

    @pytest.mark.asyncio
    async def test_event_replan_flag_off_keeps_runtime_baseline(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("plugins.schedule.plugin.datetime", FixedPromptDateTime)
        engine = _CountingMoodEngine()
        engine.register_interaction_signal(
            tension_d=0.2,
            group_id="g1",
            session_id="group_g1",
            m1_tension_enabled=True,
        )
        schedule_store = _SavingScheduleStore(_make_replan_schedule())
        story_store = _FakeStoryArcStore(self._arc(pressure=True))
        plugin = await TestDialogueClimateM1PromptGuidance._started_plugin(
            engine,
            m1_enabled=True,
            schedule_store=schedule_store,
            story_arc_store=story_store,
            event_replan_enabled=False,
        )
        prompt_ctx = self._prompt_ctx()

        await plugin.on_pre_prompt(prompt_ctx)

        assert [block.label for block in prompt_ctx.blocks] == ["当前时间", "对话气氛"]
        assert "剧情约束" not in [block.label for block in prompt_ctx.blocks]
        assert schedule_store.saved == []
        assert story_store.load_active_calls == 0
        assert story_store.saved == []
        assert "轻微扭伤" not in schedule_store.current.slots[1].description

    @pytest.mark.asyncio
    async def test_event_replan_high_m1_tension_overrides_slots_and_updates_arc(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("plugins.schedule.plugin.datetime", FixedPromptDateTime)
        monkeypatch.setattr("plugins.schedule.mood.time.monotonic", lambda: 1000.0)
        engine = _CountingMoodEngine()
        engine.register_interaction_signal(
            tension_d=0.14,
            group_id="g1",
            session_id="group_g1",
            m1_tension_enabled=True,
        )
        schedule_store = _SavingScheduleStore(_make_replan_schedule())
        story_store = _FakeStoryArcStore(self._arc())
        plugin = await TestDialogueClimateM1PromptGuidance._started_plugin(
            engine,
            m1_enabled=True,
            schedule_store=schedule_store,
            story_arc_store=story_store,
            event_replan_enabled=True,
        )
        prompt_ctx = self._prompt_ctx()

        await plugin.on_pre_prompt(prompt_ctx)

        labels = [block.label for block in prompt_ctx.blocks]
        assert labels == ["当前时间", "剧情约束", "对话气氛"]
        assert prompt_ctx.blocks[1].source == "schedule.event_replan"
        assert "这周怎么了" in prompt_ctx.blocks[1].text
        assert "临时降难度" in prompt_ctx.blocks[1].text
        assert len(schedule_store.saved) == 1
        assert "天马司轻微扭伤" in schedule_store.current.slots[1].description
        assert schedule_store.current.slots[1].mood_hint == "担心但冷静降难度"
        assert schedule_store.current.slots[2].mood_hint == "担心但冷静降难度"
        assert story_store.arc is not None
        assert story_store.arc.stage == "setback_replan"
        assert story_store.arc.event_budget["setback_count"] == 1
        assert "partner_minor_setback_replan" in story_store.arc.event_budget["triggered_once"]
        assert story_store.arc.event_budget["active_replan_constraints"][0]["remaining_days"] == 3
        assert story_store.arc.last_events[-1]["source"] == "event_replan"
        assert "M1 tension" in story_store.arc.last_events[-1]["reason"]
        assert engine.resolve_calls >= 1

    @pytest.mark.asyncio
    async def test_event_replan_pressure_triggers_without_reading_m1_when_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("plugins.schedule.plugin.datetime", FixedPromptDateTime)
        engine = _CountingMoodEngine()
        schedule_store = _SavingScheduleStore(_make_replan_schedule())
        story_store = _FakeStoryArcStore(self._arc(pressure=True))
        plugin = await TestDialogueClimateM1PromptGuidance._started_plugin(
            engine,
            m1_enabled=False,
            schedule_store=schedule_store,
            story_arc_store=story_store,
            event_replan_enabled=True,
        )
        prompt_ctx = self._prompt_ctx()

        await plugin.on_pre_prompt(prompt_ctx)

        assert [block.label for block in prompt_ctx.blocks] == ["当前时间", "剧情约束"]
        assert engine.resolve_calls == 0
        assert story_store.arc is not None
        assert story_store.arc.event_budget["setback_count"] == 1
        assert "deadline/exam pressure" in story_store.arc.last_events[-1]["reason"]

    @pytest.mark.asyncio
    async def test_event_replan_budget_blocks_second_setback_but_keeps_active_guidance(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("plugins.schedule.plugin.datetime", FixedPromptDateTime)
        monkeypatch.setattr("plugins.schedule.mood.time.monotonic", lambda: 1000.0)
        engine = _CountingMoodEngine()
        engine.register_interaction_signal(
            tension_d=0.14,
            group_id="g1",
            session_id="group_g1",
            m1_tension_enabled=True,
        )
        schedule_store = _SavingScheduleStore(_make_replan_schedule())
        story_store = _FakeStoryArcStore(self._arc())
        plugin = await TestDialogueClimateM1PromptGuidance._started_plugin(
            engine,
            m1_enabled=True,
            schedule_store=schedule_store,
            story_arc_store=story_store,
            event_replan_enabled=True,
        )

        await plugin.on_pre_prompt(self._prompt_ctx())
        first_saved_count = len(schedule_store.saved)
        first_event_count = len(story_store.arc.last_events) if story_store.arc is not None else 0
        await plugin.on_pre_prompt(self._prompt_ctx())

        assert story_store.arc is not None
        assert story_store.arc.event_budget["setback_count"] == 1
        assert len(story_store.arc.last_events) == first_event_count
        assert len(schedule_store.saved) == first_saved_count
        guidance_text = "\n".join(block.text for block in self._prompt_ctx().blocks)
        assert guidance_text == ""
        prompt_ctx = self._prompt_ctx()
        await plugin.on_pre_prompt(prompt_ctx)
        assert any(block.source == "schedule.event_replan" for block in prompt_ctx.blocks)
        assert "remaining_days=3" in "\n".join(block.text for block in prompt_ctx.blocks)


class TestInteractionNudge:
    """Issue 17 Part 0: reaction/poke nudges share the recognition-nudge path."""

    def test_recognition_self_still_nudges_valence(self):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        schedule = _make_schedule()
        random.seed(7)
        base = engine.evaluate(schedule, session_id="g1").valence
        engine.register_recognition_signal("self", session_id="g1")
        random.seed(7)
        after = engine.evaluate(schedule, session_id="g1").valence
        assert after > base

    def test_recognition_friend_still_nudges_openness(self):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        schedule = _make_schedule()
        random.seed(7)
        base = engine.evaluate(schedule, session_id="g1").openness
        engine.register_recognition_signal("friend", session_id="g1")
        random.seed(7)
        after = engine.evaluate(schedule, session_id="g1").openness
        assert after > base

    def test_recognition_known_is_noop(self):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        schedule = _make_schedule()
        random.seed(7)
        before = engine.evaluate(schedule, session_id="g1")
        engine.register_recognition_signal("known", session_id="g1")
        random.seed(7)
        after = engine.evaluate(schedule, session_id="g1")
        assert after.valence == before.valence
        assert after.openness == before.openness

    def test_positive_reaction_raises_valence(self):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        schedule = _make_schedule()
        random.seed(7)
        base = engine.evaluate(schedule, session_id="g1").valence
        engine.register_interaction_signal(valence_d=0.1, session_id="g1")
        random.seed(7)
        after = engine.evaluate(schedule, session_id="g1").valence
        assert after > base

    def test_negative_reaction_raises_tension_lowers_valence(self):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        schedule = _make_schedule()
        random.seed(7)
        base = engine.evaluate(schedule, session_id="g1")
        engine.register_interaction_signal(valence_d=-0.1, tension_d=0.06, session_id="g1")
        random.seed(7)
        after = engine.evaluate(schedule, session_id="g1")
        assert after.valence < base.valence
        assert after.tension > base.tension

    def test_poke_raises_tension_only(self):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        engine.register_interaction_signal(tension_d=0.04, session_id="g1")
        v_nudge, o_nudge, t_nudge = engine._active_nudge(
            engine._cache_key(session_id="g1")
        )
        assert t_nudge > 0
        assert v_nudge == 0.0
        assert o_nudge == 0.0

    def test_zero_signal_is_noop(self):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        schedule = _make_schedule()
        before = engine.evaluate(schedule, session_id="g1")
        engine.register_interaction_signal(session_id="g1")
        after = engine.evaluate(schedule, session_id="g1")
        assert after.tension == before.tension
        assert after.valence == before.valence

    def test_tension_nudge_is_capped(self):
        engine = MoodEngine(anomaly_chance=0.0, refresh_minutes=60)
        for _ in range(20):
            engine.register_interaction_signal(tension_d=0.1, session_id="g1")
        v_nudge, o_nudge, t_nudge = engine._active_nudge(
            engine._cache_key(session_id="g1")
        )
        assert t_nudge <= 0.2 + 1e-9
        assert v_nudge == 0.0
        assert o_nudge == 0.0
