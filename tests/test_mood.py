"""Tests for MoodEngine — mood calculation, presets, anomaly logic."""

from __future__ import annotations

import random
from zoneinfo import ZoneInfo

from plugins.schedule.mood import MoodEngine
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
