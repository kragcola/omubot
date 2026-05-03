"""GroupChatScheduler unit tests."""

import asyncio

from kernel.config import GroupConfig, GroupOverride
from services.identity import Identity
from services.memory.timeline import GroupTimeline
from services.scheduler import GroupChatScheduler


def _make_identity(proactive: str | None = "积极参与群聊") -> Identity:
    return Identity(id="test", name="测试", personality="测试人设", proactive=proactive)


def _make_config(**kwargs: object) -> GroupConfig:
    """Build GroupConfig with talk_value=1.0 for deterministic test behaviour."""
    defaults: dict[str, object] = {"talk_value": 1.0, "planner_smooth": 0, "batch_size": 100}
    defaults.update(kwargs)
    return GroupConfig(**defaults)  # type: ignore[arg-type]


class _FakeIdentityMgr:
    def __init__(self, identity: Identity) -> None:
        self._identity = identity

    def resolve(self) -> Identity:
        return self._identity


class _FakeLLM:
    """Records chat() calls and returns configured reply."""

    def __init__(self, reply: str | None = "你好", *, delay: float = 0) -> None:
        self.calls: list[dict] = []
        self.reply = reply
        self._delay = delay

    async def chat(self, **kwargs) -> str | None:  # type: ignore[override]
        self.calls.append(kwargs)
        if self._delay:
            await asyncio.sleep(self._delay)
        return self.reply


class TestNotify:
    async def test_no_proactive_skips(self) -> None:
        """notify is a no-op when identity.proactive is None."""
        identity = _make_identity(proactive=None)
        scheduler = GroupChatScheduler(
            llm=_FakeLLM(), timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(identity),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111")
        assert "111" not in scheduler._slots
        await scheduler.close()

    async def test_probability_fires(self) -> None:
        """With talk_value=1.0, notify fires immediately (no debounce)."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_talk_value_zero_skips(self) -> None:
        """talk_value=0 means never reply to non-@ messages."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 0
        await scheduler.close()

    async def test_planner_smooth_blocks(self) -> None:
        """planner_smooth prevents firing again before interval elapses."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(planner_smooth=999.0),
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1  # first call fires
        scheduler.notify("111")  # interval too short, should skip
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1  # still only one call
        await scheduler.close()

    async def test_running_task_blocks_new_call(self) -> None:
        """While running_task is active, notify does not start new call."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        scheduler.notify("111")  # while running_task is active (or just finished)
        # msg_count incremented but no new call if running_task is still set
        await scheduler.close()


class TestAtHandling:
    async def test_at_fires_immediately(self) -> None:
        """notify(is_at=True) fires immediately, skipping probability check."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),  # would never fire normally
        )
        scheduler.notify("111", is_at=True)
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_at_ignores_planner_smooth(self) -> None:
        """notify(is_at=True) fires even when planner_smooth blocks non-@ messages."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(planner_smooth=999.0),
        )
        scheduler.notify("111", is_at=True)
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_at_queues_when_busy(self) -> None:
        """notify(is_at=True) sets pending_at when a task is already running."""
        llm = _FakeLLM(reply=None, delay=0.5)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        scheduler.notify("111", is_at=True)  # should queue
        assert scheduler._slots["111"].pending_at is True
        await scheduler.close()

    async def test_pending_at_fires_after_completion(self) -> None:
        """After running task completes, pending_at triggers a new call."""
        llm = _FakeLLM(reply=None, delay=0.2)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        scheduler.notify("111", is_at=True)  # queued as pending_at
        await asyncio.sleep(0.5)  # first call finishes, pending fires
        assert len(llm.calls) == 2
        assert scheduler._slots["111"].pending_at is False
        await scheduler.close()


class TestClose:
    async def test_close_cancels_all(self) -> None:
        """close() cancels all running tasks."""
        llm = _FakeLLM(reply=None, delay=1.0)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(planner_smooth=999),
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1  # first fire
        scheduler.notify("222")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 2  # different group, can fire
        await scheduler.close()
        # After close, running tasks should be cancelled or done
        for slot in scheduler._slots.values():
            assert slot.running_task is None or slot.running_task.done()


class TestAtOnly:
    async def test_at_only_skips_non_at(self) -> None:
        """at_only=True: non-@ messages don't trigger anything."""
        llm = _FakeLLM(reply=None)
        group_config = _make_config(at_only=True, talk_value=1.0)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=group_config,
        )
        scheduler.notify("123")
        scheduler.notify("123")
        scheduler.notify("123")
        await asyncio.sleep(0.15)
        assert len(llm.calls) == 0
        await scheduler.close()

    async def test_at_only_still_fires_on_at(self) -> None:
        """at_only=True: @ messages still fire immediately."""
        llm = _FakeLLM(reply=None)
        group_config = _make_config(at_only=True, talk_value=1.0)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=group_config,
        )
        scheduler.notify("123", is_at=True)
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_per_group_at_only_override(self) -> None:
        """Group 123 is at_only, group 456 is not."""
        llm = _FakeLLM(reply=None)
        group_config = _make_config(
            at_only=False,
            overrides={123: GroupOverride(at_only=True)},
        )
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=group_config,
        )
        scheduler.notify("123")  # at_only group — skip
        scheduler.notify("456")  # normal group — fire
        await asyncio.sleep(0.15)
        assert len(llm.calls) == 1  # only group 456 fired
        assert llm.calls[0]["session_id"] == "group_456"
        await scheduler.close()


class TestPerGroupParams:
    async def test_per_group_planner_smooth(self) -> None:
        """Group 123 has planner_smooth=999 (blocked after first fire), group 456 uses 0."""
        llm = _FakeLLM(reply=None)
        group_config = _make_config(
            planner_smooth=0,
            overrides={123: GroupOverride(planner_smooth=999)},
        )
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=group_config,
        )
        scheduler.notify("123")  # first fire: last_fire_time=0 so interval passes
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        scheduler.notify("123")  # second fire: blocked by planner_smooth=999
        scheduler.notify("456")  # group 456 fires (planner_smooth=0)
        await asyncio.sleep(0.15)
        assert len(llm.calls) == 2  # group 123 (once) + group 456 (once)
        assert llm.calls[1]["session_id"] == "group_456"
        await scheduler.close()

    async def test_per_group_talk_value(self) -> None:
        """Group 123 has talk_value=0 (never), group 456 has talk_value=1 (always)."""
        llm = _FakeLLM(reply=None)
        group_config = _make_config(
            talk_value=0.0,
            overrides={456: GroupOverride(talk_value=1.0)},
        )
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=group_config,
        )
        scheduler.notify("123")  # talk_value=0 → skip
        scheduler.notify("456")  # talk_value=1 → fire
        await asyncio.sleep(0.15)
        assert len(llm.calls) == 1
        assert llm.calls[0]["session_id"] == "group_456"
        await scheduler.close()


class TestMute:
    async def test_muted_group_skips_notify(self) -> None:
        """notify is a no-op for muted groups."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.mute("111")
        scheduler.notify("111")
        scheduler.notify("111", is_at=True)
        await asyncio.sleep(0.15)
        assert len(llm.calls) == 0
        await scheduler.close()

    async def test_muted_group_skips_trigger(self) -> None:
        """trigger is a no-op for muted groups."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.mute("111")
        scheduler.trigger("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 0
        await scheduler.close()

    async def test_unmute_resumes_scheduling(self) -> None:
        """After unmute, notify works again."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.mute("111")
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 0

        scheduler.unmute("111")
        scheduler.notify("111")
        await asyncio.sleep(0.15)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_mute_cancels_running_tasks(self) -> None:
        """Muting a group cancels its running tasks."""
        llm = _FakeLLM(reply=None, delay=1.0)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        scheduler.mute("111")
        slot = scheduler._slots["111"]
        assert slot.running_task is None
        assert slot.msg_count == 0
        assert slot.pending_at is False
        await scheduler.close()

    async def test_is_muted(self) -> None:
        """is_muted returns correct state."""
        scheduler = GroupChatScheduler(
            llm=_FakeLLM(), timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        assert not scheduler.is_muted("111")
        scheduler.mute("111")
        assert scheduler.is_muted("111")
        scheduler.unmute("111")
        assert not scheduler.is_muted("111")
        await scheduler.close()

    async def test_mute_only_affects_target_group(self) -> None:
        """Muting group 111 does not affect group 222."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.mute("111")
        scheduler.notify("111")
        scheduler.notify("222")
        await asyncio.sleep(0.15)
        assert len(llm.calls) == 1
        assert llm.calls[0]["session_id"] == "group_222"
        await scheduler.close()


class _FakeMood:
    """Minimal mood profile for testing."""

    def __init__(self, energy: float, valence: float, openness: float) -> None:
        self.energy = energy
        self.valence = valence
        self.openness = openness


class TestMood:
    def _mood_getter(self, energy: float, valence: float, openness: float):
        return lambda: _FakeMood(energy=energy, valence=valence, openness=openness)

    async def test_good_mood_boosts_reply(self) -> None:
        """Good mood (high energy/valence/openness) boosts talk_value."""
        llm = _FakeLLM(reply=None)
        # talk_value=0.2 with a very good mood → multiplier ~2.0 → threshold ~0.4
        # That's still below 1.0, so we might skip. Let's use multiple calls.
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.15, planner_smooth=0),
            mood_getter=self._mood_getter(energy=0.9, valence=0.9, openness=0.9),
        )
        # With good mood (multiplier ~2.0), threshold ≈ 0.30, should fire often
        for _ in range(30):
            scheduler.notify("111")
            await asyncio.sleep(0.01)
        # At least some should fire
        assert len(llm.calls) > 0
        await scheduler.close()

    async def test_bad_mood_suppresses_reply(self) -> None:
        """Bad mood (low energy, negative valence, low openness) suppresses talk_value."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.5, planner_smooth=0),
            mood_getter=self._mood_getter(energy=0.2, valence=-0.6, openness=0.2),
        )
        # Bad mood multiplier ~0.4, effective threshold ≈ 0.2
        # Even with 30 attempts, should fire rarely
        for _ in range(30):
            scheduler.notify("111")
            await asyncio.sleep(0.01)
        # Should fire significantly less than 30 (probably < 10)
        # With talk_value=0.5 * mood_mult ~0.4 = ~0.2 threshold, expect ~6 fires
        assert len(llm.calls) < 15  # well under the 30 attempts
        await scheduler.close()

    async def test_mood_getter_returns_none(self) -> None:
        """mood_getter returning None → multiplier 1.0, behavior unchanged."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0, planner_smooth=0),
            mood_getter=lambda: None,
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1  # talk_value=1.0 fires immediately
        await scheduler.close()

    async def test_no_mood_getter_defaults_to_one(self) -> None:
        """Without mood_getter, multiplier is 1.0 — talk_value=1.0 fires, 0.0 skips."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0, planner_smooth=0),
            # no mood_getter passed
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_mood_multiplier_is_clamped(self) -> None:
        """Even with extreme mood, threshold never exceeds 1.0."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.8, planner_smooth=0),
            mood_getter=self._mood_getter(energy=1.0, valence=1.0, openness=1.0),
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        # talk_value=0.8 * mood_mult(~2.0) = 1.6 → clamped to 1.0 → fires
        assert len(llm.calls) == 1
        await scheduler.close()


class TestVideoHint:
    async def test_always_mode_fires_immediately(self) -> None:
        """video_hint mode='always' fires immediately, bypassing probability."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0, planner_smooth=0),
        )
        scheduler.notify("111", video_hint={"mode": "always", "bilibili_talk_value": 0.8, "video_title": "test"})
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_always_mode_respects_mute(self) -> None:
        """Even always mode does not fire when muted."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.mute("111")
        scheduler.notify("111", video_hint={"mode": "always", "bilibili_talk_value": 0.8, "video_title": "test"})
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 0
        await scheduler.close()

    async def test_always_mode_bypasses_proactive_none(self) -> None:
        """video_hint mode='always' fires even when proactive is None."""
        identity = _make_identity(proactive=None)
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(identity),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111", video_hint={"mode": "always", "bilibili_talk_value": 0.8, "video_title": "test"})
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_always_mode_bypasses_planner_smooth(self) -> None:
        """video_hint mode='always' bypasses planner_smooth interval."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(planner_smooth=999.0),
        )
        scheduler.notify("111", video_hint={"mode": "always", "bilibili_talk_value": 0.8, "video_title": "test"})
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        # Second call also fires (planner_smooth ignored for always mode)
        scheduler.notify("111", video_hint={"mode": "always", "bilibili_talk_value": 0.8, "video_title": "test"})
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 2
        await scheduler.close()

    async def test_dedicated_mode_uses_bilibili_talk_value(self) -> None:
        """Dedicated mode uses bilibili_talk_value=1.0 to guarantee fire."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0, planner_smooth=0),
        )
        scheduler.notify("111", video_hint={
            "mode": "dedicated", "bilibili_talk_value": 1.0, "video_title": "test",
        })
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_dedicated_mode_respects_talk_value(self) -> None:
        """Dedicated mode with bilibili_talk_value=0.0 never fires."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0, planner_smooth=0),
        )
        scheduler.notify("111", video_hint={
            "mode": "dedicated", "bilibili_talk_value": 0.0, "video_title": "test",
        })
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 0
        await scheduler.close()

    async def test_autonomous_high_interest_fires(self) -> None:
        """High interest score with high bilibili_talk_value = likely fires."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0, planner_smooth=0),
        )
        # talk_value=1.0 * interest=1.0 = 1.0 threshold → guaranteed fire
        scheduler.notify("111", video_hint={
            "mode": "autonomous", "bilibili_talk_value": 1.0,
            "interest_score": 1.0, "video_title": "test",
        })
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_autonomous_low_interest_rarely_fires(self) -> None:
        """Low interest score makes firing much less likely."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0, planner_smooth=0),
        )
        for _ in range(30):
            scheduler.notify("111", video_hint={
                "mode": "autonomous", "bilibili_talk_value": 0.5,
                "interest_score": 0.05, "video_title": "test",
            })
            await asyncio.sleep(0.01)
        # With threshold = 0.5 * 0.05 = 0.025, 30 shots → expected ~0.75 fires
        # Should fire very few times
        assert len(llm.calls) < 5
        await scheduler.close()

    async def test_mood_mode_no_hint_is_backward_compatible(self) -> None:
        """No video_hint — behavior unchanged."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), identity_mgr=_FakeIdentityMgr(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
        )
        scheduler.notify("111")  # no video_hint
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()
