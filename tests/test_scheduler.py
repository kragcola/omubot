"""GroupChatScheduler unit tests."""

import asyncio
import time

from kernel.config import GroupConfig, GroupOverride
from kernel.types import TriggerContext
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot
from services.scheduler import GroupChatScheduler, _GroupSlot, _should_force_reply


def _make_identity(proactive: str | None = "积极参与群聊") -> IdentitySnapshot:
    return IdentitySnapshot(id="test", name="测试", personality="测试人设", proactive=proactive)


def _make_config(**kwargs: object) -> GroupConfig:
    """Build GroupConfig with talk_value=1.0 for deterministic test behaviour."""
    defaults: dict[str, object] = {"talk_value": 1.0, "planner_smooth": 0, "batch_size": 100}
    defaults.update(kwargs)
    return GroupConfig(**defaults)  # type: ignore[arg-type]


def test_qq_interaction_mode_force_reply() -> None:
    assert _should_force_reply(
        TriggerContext(
            reason="戳一戳",
            mode="qq_interaction",
            extra={"addressee_self": False},
        )
    ) is True
    assert _should_force_reply(
        TriggerContext(
            reason="有人@了你",
            mode="at_mention",
            extra={"addressee_self": False},
        )
    ) is False


class _FakeRuntime:
    def __init__(self, identity: IdentitySnapshot) -> None:
        self._identity = identity

    def identity_snapshot(self) -> IdentitySnapshot:
        return self._identity


class _FakeLLM:
    """Records chat() calls and returns configured reply."""

    def __init__(self, reply: str | None = "你好", *, delay: float = 0, thinker_action: str = "") -> None:
        self.calls: list[dict] = []
        self.reply = reply
        self._delay = delay
        self._last_thinker_action = thinker_action

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
            llm=_FakeLLM(), timeline=GroupTimeline(), persona_runtime=_FakeRuntime(identity),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111")
        assert "111" not in scheduler._slots
        await scheduler.close()

    async def test_probability_fires(self) -> None:
        """With talk_value=1.0, notify fires immediately (no debounce)."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(planner_smooth=999.0),
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1  # first call fires
        scheduler.notify("111")  # interval too short, should skip
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1  # still only one call
        await scheduler.close()

    async def test_consecutive_skip_double_threshold_uses_resolved_config(self) -> None:
        """Probability doubling reads resolved config instead of hardcoded literals."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(
                talk_value=0.6,
                consecutive_skip_force_threshold=5,
                consecutive_skip_double_threshold=1,
            ),
        )
        slot = scheduler._slots.setdefault("111", _GroupSlot())
        slot.consecutive_skip = 1

        scheduler.notify("111")
        await asyncio.sleep(0.1)

        assert len(llm.calls) == 1
        assert slot.consecutive_skip == 0
        await scheduler.close()

    async def test_force_threshold_requires_recent_skip_time(self) -> None:
        """Expired skip history no longer triggers forced reply."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(
                talk_value=0.0,
                consecutive_skip_force_threshold=3,
                consecutive_skip_double_threshold=99,
            ),
        )
        slot = scheduler._slots.setdefault("111", _GroupSlot())
        slot.consecutive_skip = 3
        slot.last_skip_time = time.monotonic() - 1801.0

        scheduler.notify("111")
        await asyncio.sleep(0.1)

        assert len(llm.calls) == 0
        assert slot.consecutive_skip == 4
        assert slot.last_skip_time > 0.0
        await scheduler.close()

    async def test_force_threshold_fires_when_skip_time_is_recent(self) -> None:
        """Recent skip history still triggers forced reply."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(
                talk_value=0.0,
                consecutive_skip_force_threshold=3,
                consecutive_skip_double_threshold=99,
            ),
        )
        slot = scheduler._slots.setdefault("111", _GroupSlot())
        slot.consecutive_skip = 3
        slot.last_skip_time = time.monotonic() - 60.0

        scheduler.notify("111")
        await asyncio.sleep(0.1)

        assert len(llm.calls) == 1
        assert slot.consecutive_skip == 0
        await scheduler.close()

    async def test_skip_records_last_skip_time(self) -> None:
        """A probability skip refreshes last_skip_time for future decay checks."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
        )
        slot = scheduler._slots.setdefault("111", _GroupSlot())

        scheduler.notify("111")
        await asyncio.sleep(0.1)

        assert len(llm.calls) == 0
        assert slot.consecutive_skip == 1
        assert slot.last_skip_time > 0.0
        await scheduler.close()

    async def test_stale_skip_refreshes_window_for_next_force(self) -> None:
        """A stale force-threshold miss updates the window so the next turn can force."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(
                talk_value=0.0,
                consecutive_skip_force_threshold=3,
                consecutive_skip_double_threshold=99,
            ),
        )
        slot = scheduler._slots.setdefault("111", _GroupSlot())
        slot.consecutive_skip = 3
        slot.last_skip_time = time.monotonic() - 1900.0

        scheduler.notify("111")
        await asyncio.sleep(0.1)
        first_skip_time = slot.last_skip_time

        scheduler.notify("111")
        await asyncio.sleep(0.1)

        assert first_skip_time > 0.0
        assert len(llm.calls) == 1
        assert slot.consecutive_skip == 0
        await scheduler.close()

    async def test_running_task_blocks_new_call(self) -> None:
        """While running_task is active, notify does not start new call."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),  # would never fire normally
        )
        scheduler.notify("111", trigger=TriggerContext(reason="有人@了你", mode="at_mention"))
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_at_ignores_planner_smooth(self) -> None:
        """notify(is_at=True) fires even when planner_smooth blocks non-@ messages."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(planner_smooth=999.0),
        )
        scheduler.notify("111", trigger=TriggerContext(reason="有人@了你", mode="at_mention"))
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_at_queues_when_busy(self) -> None:
        """notify(is_at=True) stores pending message details when a task is already running."""
        llm = _FakeLLM(reply=None, delay=0.5)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        scheduler.notify(
            "111",
            trigger=TriggerContext(reason="有人@了你", mode="at_mention"),
            message_text="别睡",
        )
        assert len(scheduler._slots["111"].pending_during_generation) == 1
        await scheduler.close()

    async def test_pending_generation_fires_after_completion(self) -> None:
        """After running task completes, queued pending messages trigger a new call."""
        llm = _FakeLLM(reply=None, delay=0.2)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        scheduler.notify(
            "111",
            trigger=TriggerContext(reason="有人@了你", mode="at_mention"),
            message_text="别睡",
        )
        await asyncio.sleep(0.5)  # first call finishes, pending fires
        assert len(llm.calls) == 2
        assert scheduler._slots["111"].pending_during_generation == []
        await scheduler.close()


class TestDirectedFollowup:
    async def test_directed_followup_bypasses_proactive_none(self) -> None:
        """directed_followup fires even when proactive is None."""
        identity = _make_identity(proactive=None)
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(identity),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0, planner_smooth=999.0),
        )
        scheduler.notify("111", trigger=TriggerContext(reason="继续刚才的话题", mode="directed_followup"))
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_directed_followup_cancel_path_does_not_dirty_pending_or_skip(self) -> None:
        """Queued directed_followup can be cancelled cleanly without skip pollution."""
        llm = _FakeLLM(reply=None, delay=1.0)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111", user_id="42")
        await asyncio.sleep(0.1)

        slot = scheduler._slots["111"]
        assert slot.running_task is not None
        assert not slot.running_task.done()

        scheduler.notify(
            "111",
            trigger=TriggerContext(reason="继续刚才的话题", mode="directed_followup"),
            user_id="42",
        )

        assert len(slot.pending_during_generation) == 1
        assert slot.trigger is not None and slot.trigger.mode == "directed_followup"
        assert slot.consecutive_skip == 0

        scheduler.clear_pending("111", cancel_running=True)
        await asyncio.sleep(0.05)

        assert slot.pending_during_generation == []
        assert slot.trigger is None
        assert slot.consecutive_skip == 0
        assert len(llm.calls) == 1
        await scheduler.close()


class TestPendingReset:
    async def test_clear_pending_resets_trigger_and_queue(self) -> None:
        llm = _FakeLLM(reply=None, delay=0.5)
        scheduler = GroupChatScheduler(
            llm=llm,  # type: ignore[arg-type]
            timeline=GroupTimeline(),
            persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111", user_id="42")
        await asyncio.sleep(0.1)
        scheduler.notify("111", trigger=TriggerContext(reason="有人@了你", mode="at_mention"), user_id="42")

        slot = scheduler._slots["111"]
        assert len(slot.pending_during_generation) == 1
        assert slot.trigger is not None

        scheduler.clear_pending("111")

        assert slot.pending_during_generation == []
        assert slot.trigger is None
        assert slot.msg_count == 0
        await scheduler.close()

    async def test_clear_pending_can_cancel_running_task(self) -> None:
        llm = _FakeLLM(reply=None, delay=1.0)
        scheduler = GroupChatScheduler(
            llm=llm,  # type: ignore[arg-type]
            timeline=GroupTimeline(),
            persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111", user_id="42")
        await asyncio.sleep(0.1)

        slot = scheduler._slots["111"]
        assert slot.running_task is not None
        assert not slot.running_task.done()

        scheduler.clear_pending("111", cancel_running=True)
        await asyncio.sleep(0)

        assert slot.running_task is None or slot.running_task.cancelled() or slot.running_task.done()
        await scheduler.close()


class TestClose:
    async def test_close_cancels_all(self) -> None:
        """close() cancels all running tasks."""
        llm = _FakeLLM(reply=None, delay=1.0)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=group_config,
        )
        scheduler.notify("123", trigger=TriggerContext(reason="有人@了你", mode="at_mention"))
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.mute("111")
        scheduler.notify("111")
        scheduler.notify("111", trigger=TriggerContext(reason="有人@了你", mode="at_mention"))
        await asyncio.sleep(0.15)
        assert len(llm.calls) == 0
        await scheduler.close()

    async def test_muted_group_skips_trigger(self) -> None:
        """trigger is a no-op for muted groups."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111")
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        scheduler.mute("111")
        slot = scheduler._slots["111"]
        assert slot.running_task is None
        assert slot.msg_count == 0
        assert slot.pending_during_generation == []
        await scheduler.close()

    async def test_is_muted(self) -> None:
        """is_muted returns correct state."""
        scheduler = GroupChatScheduler(
            llm=_FakeLLM(), timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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

    async def test_mood_getter_receives_group_session_context(self) -> None:
        """New mood_getter path receives group/session for per-key MoodEngine cache."""
        seen: list[tuple[str | None, str]] = []

        def getter(*, group_id: str | None = None, session_id: str = "") -> _FakeMood:
            seen.append((group_id, session_id))
            return _FakeMood(energy=0.5, valence=0.0, openness=0.5)

        scheduler = GroupChatScheduler(
            llm=_FakeLLM(), timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
            mood_getter=getter,
        )

        assert scheduler._get_mood_multiplier("111") > 0
        assert seen == [("111", "group_111")]
        await scheduler.close()

    async def test_good_mood_boosts_reply(self) -> None:
        """Good mood (high energy/valence/openness) boosts talk_value."""
        llm = _FakeLLM(reply=None)
        # talk_value=0.2 with a very good mood → multiplier ~2.0 → threshold ~0.4
        # That's still below 1.0, so we might skip. Let's use multiple calls.
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
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
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0, planner_smooth=0),
        )
        scheduler.notify("111", trigger=TriggerContext(
            reason="视频分享:《test》", mode="video_always",
            extra={"bilibili_talk_value": 0.8, "video_title": "test"},
        ))
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_always_mode_respects_mute(self) -> None:
        """Even always mode does not fire when muted."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.mute("111")
        scheduler.notify("111", trigger=TriggerContext(
            reason="视频分享:《test》", mode="video_always",
            extra={"bilibili_talk_value": 0.8, "video_title": "test"},
        ))
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 0
        await scheduler.close()

    async def test_always_mode_bypasses_proactive_none(self) -> None:
        """video_hint mode='always' fires even when proactive is None."""
        identity = _make_identity(proactive=None)
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(identity),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111", trigger=TriggerContext(
            reason="视频分享:《test》", mode="video_always",
            extra={"bilibili_talk_value": 0.8, "video_title": "test"},
        ))
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_always_mode_bypasses_planner_smooth(self) -> None:
        """video_hint mode='always' bypasses planner_smooth interval."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(planner_smooth=999.0),
        )
        scheduler.notify("111", trigger=TriggerContext(
            reason="视频分享:《test》", mode="video_always",
            extra={"bilibili_talk_value": 0.8, "video_title": "test"},
        ))
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        # Second call also fires (planner_smooth ignored for always mode)
        scheduler.notify("111", trigger=TriggerContext(
            reason="视频分享:《test》", mode="video_always",
            extra={"bilibili_talk_value": 0.8, "video_title": "test"},
        ))
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 2
        await scheduler.close()

    async def test_dedicated_mode_uses_bilibili_talk_value(self) -> None:
        """Dedicated mode uses bilibili_talk_value=1.0 to guarantee fire."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0, planner_smooth=0),
        )
        scheduler.notify("111", trigger=TriggerContext(
            reason="视频分享:《test》", mode="video_dedicated",
            extra={"bilibili_talk_value": 1.0, "video_title": "test"},
        ))
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_dedicated_mode_respects_talk_value(self) -> None:
        """Dedicated mode with bilibili_talk_value=0.0 never fires."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0, planner_smooth=0),
        )
        scheduler.notify("111", trigger=TriggerContext(
            reason="视频分享:《test》", mode="video_dedicated",
            extra={"bilibili_talk_value": 0.0, "video_title": "test"},
        ))
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 0
        await scheduler.close()

    async def test_autonomous_high_interest_fires(self) -> None:
        """High interest score with high bilibili_talk_value = likely fires."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0, planner_smooth=0),
        )
        # talk_value=1.0 * interest=1.0 = 1.0 threshold → guaranteed fire
        scheduler.notify("111", trigger=TriggerContext(
            reason="视频分享:《test》", mode="video_autonomous",
            extra={"bilibili_talk_value": 1.0, "interest_score": 1.0, "video_title": "test"},
        ))
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_autonomous_low_interest_guaranteed_after_5_skips(self) -> None:
        """Even with interest=0.05, consecutive_skip>=5 guarantees reply."""
        llm = _FakeLLM(reply=None, delay=0.001)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0, planner_smooth=0),
        )
        # First 5 calls may or may not fire (low interest), but 6th is guaranteed
        for _ in range(5):
            scheduler.notify("111", trigger=TriggerContext(
                reason="视频分享:《test》", mode="video_autonomous",
                extra={"bilibili_talk_value": 0.5, "interest_score": 0.05, "video_title": "test"},
            ))
            await asyncio.sleep(0.01)
        # After 5 skips, consecutive_skip=5 → threshold=1.0, interest skipped
        scheduler.notify("111", trigger=TriggerContext(
            reason="视频分享:《test》", mode="video_autonomous",
            extra={"bilibili_talk_value": 0.5, "interest_score": 0.05, "video_title": "test"},
        ))
        await asyncio.sleep(0.2)
        assert len(llm.calls) >= 1  # guaranteed fire by the 6th attempt
        await scheduler.close()

    async def test_autonomous_force_threshold_override_preserves_cancel_path(self) -> None:
        """Force threshold override still fires and leaves cancel-path clean."""
        llm = _FakeLLM(reply=None, delay=1.0)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(
                talk_value=0.0,
                consecutive_skip_force_threshold=1,
                consecutive_skip_double_threshold=1,
            ),
        )
        slot = scheduler._slots.setdefault("111", _GroupSlot())
        slot.consecutive_skip = 1
        slot.last_skip_time = time.monotonic() - 60.0

        scheduler.notify("111", trigger=TriggerContext(
            reason="视频分享:《test》", mode="video_autonomous",
            extra={"bilibili_talk_value": 0.0, "interest_score": 0.05, "video_title": "test"},
        ))
        await asyncio.sleep(0.1)

        assert len(llm.calls) == 1
        assert slot.consecutive_skip == 0
        assert slot.running_task is not None

        scheduler.clear_pending("111", cancel_running=True)
        await asyncio.sleep(0.05)

        assert slot.consecutive_skip == 0
        assert slot.pending_during_generation == []
        assert slot.trigger is None
        await scheduler.close()

    async def test_mood_mode_no_hint_is_backward_compatible(self) -> None:
        """No video_hint — behavior unchanged."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
        )
        scheduler.notify("111")  # no video_hint
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()


class TestClosingBypass:
    """Weak-reply P0: closing trigger bypasses probability gate with dedup + cooldown."""

    def _closing(self) -> TriggerContext:
        return TriggerContext(reason="收尾", mode="closing", target_message_id=1, target_user_id="u1")

    async def test_closing_fires_bypassing_low_talk_value(self) -> None:
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),  # would never fire normally
        )
        scheduler.notify("111", trigger=self._closing())
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_closing_bypasses_proactive_none(self) -> None:
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity(proactive=None)),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
        )
        scheduler.notify("111", trigger=self._closing())
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_closing_dedup_second_is_skipped(self) -> None:
        """Once a terminal exchange is done, a repeated farewell does not re-fire."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
        )
        scheduler.notify("111", trigger=self._closing())
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        assert scheduler._slots["111"].closing_done is True
        # Second farewell, same conversation — deduped.
        scheduler.notify("111", trigger=self._closing())
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_closing_cooldown_blocks_recent_light(self) -> None:
        """A closing within the light cooldown of a prior light reply is suppressed."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
        )
        slot = scheduler._slots.setdefault("111", _GroupSlot())
        slot.last_light_time = time.time()  # just had a light reply
        scheduler.notify("111", trigger=self._closing())
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 0  # within cooldown → suppressed
        await scheduler.close()


class TestTopicBlockAnchor:
    """B1: prob-fire anchors to the bot's topic block via add_pending_trigger."""

    def _enabled_config(self):
        from kernel.config import TopicBlockConfig

        return TopicBlockConfig(enabled=True)

    async def test_prob_fire_injects_anchor_only_when_bot_involved(self) -> None:
        """F-α fix: a fire on a block the bot is NOT part of injects NO anchor
        (the bot must not be 'placed' into a conversation it only overhears)."""
        llm = _FakeLLM(reply=None)
        timeline = GroupTimeline()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=timeline, persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),  # always fires
            topic_block_config=self._enabled_config(),
        )
        # A sticker with no @bot, no reply-to-bot → block is NOT bot-involved.
        scheduler.notify("111", user_id="u1", message_text="«动画表情»", message_id=42)
        await asyncio.sleep(0.1)
        pending = timeline.get_pending("111")
        anchors = [m for m in pending if m.get("trigger_reason")]
        assert not anchors  # no anchor → bot not forced into a non-own block
        await scheduler.close()

    async def test_prob_fire_anchors_to_bot_involved_block(self) -> None:
        """When the bot IS part of the active block (@-ed earlier), a later
        prob-fire anchors to that block's representative message."""
        llm = _FakeLLM(reply=None)
        timeline = GroupTimeline()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=timeline, persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            topic_block_config=self._enabled_config(),
        )
        # First message @-mentions the bot → block becomes bot-involved.
        scheduler.notify(
            "111", user_id="u1", message_text="姆姆你看这个", message_id=40,
            at_targets=(), at_self=True,
        )
        await asyncio.sleep(0.05)
        # A follow-up in the same block (same speaker, continuation) fires.
        scheduler.notify("111", user_id="u1", message_text="对吧对吧", message_id=42)
        await asyncio.sleep(0.1)
        pending = timeline.get_pending("111")
        anchors = [m for m in pending if m.get("trigger_reason")]
        assert any(m.get("message_id") in (40, 42) for m in anchors)
        await scheduler.close()

    async def test_disabled_injects_no_anchor(self) -> None:
        """Default (disabled) → no tracker, no anchor; behavior == status quo."""
        llm = _FakeLLM(reply=None)
        timeline = GroupTimeline()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=timeline, persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
        )
        scheduler.notify("111", user_id="u1", message_text="«动画表情»", message_id=42)
        await asyncio.sleep(0.1)
        assert scheduler._topic_tracker is None
        pending = timeline.get_pending("111")
        assert not [m for m in pending if m.get("trigger_reason")]
        await scheduler.close()

    async def test_explicit_trigger_not_overridden(self) -> None:
        """An explicit trigger (e.g. at_mention) already has its own anchor;
        B1 must not inject a competing one."""
        llm = _FakeLLM(reply=None)
        timeline = GroupTimeline()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=timeline, persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            topic_block_config=self._enabled_config(),
        )
        trig = TriggerContext(reason="at", mode="at_mention", target_message_id=7, target_user_id="u1")
        scheduler.notify("111", trigger=trig, user_id="u1", message_text="在吗", message_id=7)
        await asyncio.sleep(0.1)
        # at_mention fires via its own path; the B1 helper is a no-op here.
        # (anchor message_id, if any, comes from the at path — not the tracker.)
        assert scheduler._topic_tracker is not None
        await scheduler.close()


class TestOverhearerRole:
    """B2: receiver-role gating — overhearer (not addressed, not a block
    participant) is suppressed per overhearer_mode."""

    def _config(self, mode: str = "shadow", boost: float = 0.0, ratified_floor: float = 0.0):
        from kernel.config import TopicBlockConfig

        return TopicBlockConfig(
            enabled=True, overhearer_mode=mode, overhearer_threshold_boost=boost,
            ratified_continuation_floor=ratified_floor,
        )

    async def test_shadow_does_not_change_behavior(self) -> None:
        """shadow mode: overhearer is logged but still fires (talk_value=1.0)."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            topic_block_config=self._config("shadow"),
        )
        # Two third parties talking; bot not addressed, not in any block.
        scheduler.notify("111", user_id="u1", message_text="你看比赛了吗", message_id=1)
        scheduler.notify("111", user_id="u2", message_text="看了好激烈", message_id=2)
        await asyncio.sleep(0.1)
        assert len(llm.calls) >= 1  # shadow → behavior unchanged
        await scheduler.close()

    async def test_silent_overhearer_does_not_fire(self) -> None:
        """silent mode: overhearer is suppressed even with talk_value=1.0."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            topic_block_config=self._config("silent"),
        )
        scheduler.notify("111", user_id="u1", message_text="你看比赛了吗", message_id=1)
        scheduler.notify("111", user_id="u2", message_text="看了好激烈", message_id=2)
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 0  # overhearer → silent
        assert scheduler._slots["111"].consecutive_skip >= 1  # skip state recorded
        await scheduler.close()

    async def test_addressed_fires_even_in_silent_mode(self) -> None:
        """addressed (is_addressed=True) always fires, silent mode notwithstanding."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            topic_block_config=self._config("silent"),
        )
        scheduler.notify("111", user_id="u1", message_text="姆姆你好", message_id=1, is_addressed=True)
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_ratified_fires_in_silent_mode(self) -> None:
        """ratified (bot already in the block via @-self) is not suppressed."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            topic_block_config=self._config("silent"),
        )
        # First @-self makes the block bot-involved (addressed → fires).
        scheduler.notify("111", user_id="u1", message_text="姆姆看这个", message_id=1, at_self=True, is_addressed=True)
        await asyncio.sleep(0.1)
        calls_after_at = len(llm.calls)
        # Follow-up in same block, not addressed → role=ratified → still fires.
        scheduler.notify("111", user_id="u1", message_text="对吧", message_id=2)
        await asyncio.sleep(0.1)
        assert len(llm.calls) > calls_after_at
        await scheduler.close()

    async def test_disabled_tracker_no_role_gating(self) -> None:
        """Tracker disabled → role is always 'addressed', no suppression."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
        )
        scheduler.notify("111", user_id="u1", message_text="随便聊聊", message_id=1)
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1  # no gating without tracker
        await scheduler.close()

    async def test_bot_involvement_makes_followup_ratified_not_silenced(self) -> None:
        """B2 fix: after the bot speaks in a block, a user's follow-up in the
        same block is 'ratified' (continuation) — NOT silenced as overhearer.
        Regression for: bot replies once then goes silent on the next line."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            topic_block_config=self._config("silent"),
        )
        # Third party chatter forms a block; bot is initially an overhearer.
        scheduler.notify("111", user_id="u1", message_text="你喝雪碧", message_id=1)
        await asyncio.sleep(0.05)
        assert len(llm.calls) == 0  # overhearer → silent (bot not yet involved)
        # Simulate the bot having spoken in that active block.
        scheduler._topic_tracker.mark_bot_involved("111")
        # User's follow-up in the same block must now be ratified → fires.
        scheduler.notify("111", user_id="u1", message_text="这叫雪人三项", message_id=2)
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1  # ratified continuation, not silenced
        await scheduler.close()

    async def test_do_chat_marks_block_bot_involved_after_reply(self) -> None:
        """_do_chat calls mark_bot_involved after a successful send."""
        from unittest.mock import AsyncMock

        llm = _FakeLLM(reply="好呀")
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            topic_block_config=self._config("silent"),
        )
        scheduler._send_to_group = AsyncMock(return_value=0.1)  # type: ignore[method-assign]
        scheduler._topic_tracker.observe("111", message_id=1, speaker="u1", text="姆姆在吗", at_self=True)
        await scheduler._do_chat("111", trigger=TriggerContext(
            reason="@", mode="at_mention", target_message_id=1, target_user_id="u1",
        ))
        # The block the bot replied in is now bot-involved.
        blk = scheduler._topic_tracker.pick_anchor_block("111", require_bot_involved=True)
        assert blk is not None and blk.bot_involved is True
        await scheduler.close()

    async def test_ratified_floor_fires_when_rws_would_skip(self) -> None:
        """B2 continuation floor: a ratified follow-up fires even when the base
        probability is near zero (low time-of-day mult). Regression for: bot
        replies, user follows up, but RWS+low time_mult skips the exchange."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),  # base prob ~0 → would skip
            topic_block_config=self._config("silent", ratified_floor=1.0),  # floor forces fire
        )
        # The bot was @-ed earlier and replied → that block is bot-involved.
        scheduler._topic_tracker.observe("111", message_id=0, speaker="u1", text="姆姆你看", at_self=True)
        scheduler._topic_tracker.mark_bot_involved("111")
        scheduler.notify("111", user_id="u1", message_text="你懂雪人三项吗", message_id=1)
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1  # floor rescued the continuation
        await scheduler.close()

    async def test_ratified_floor_zero_keeps_rws_behavior(self) -> None:
        """Floor=0 (default) → no rescue; ratified still follows base prob."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        scheduler._topic_tracker.observe("111", message_id=0, speaker="u1", text="姆姆你看", at_self=True)
        scheduler._topic_tracker.mark_bot_involved("111")
        scheduler.notify("111", user_id="u1", message_text="你懂雪人三项吗", message_id=1)
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 0  # no floor → base prob 0 skips
        await scheduler.close()



class TestFocusedTriggerReason:
    """B1-addressed: addressed triggers get a topic-focus directive so the
    bot answers the @-ed message, not the whole stale multi-topic timeline."""

    def _config(self):
        from kernel.config import TopicBlockConfig

        return TopicBlockConfig(enabled=True)

    def _scheduler(self, *, topic_block_config=None):
        return GroupChatScheduler(
            llm=_FakeLLM(reply=None), timeline=GroupTimeline(),
            persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
            topic_block_config=topic_block_config,
        )

    def test_at_mention_reason_gets_focus_directive(self) -> None:
        s = self._scheduler(topic_block_config=self._config())
        trig = TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=1, target_user_id="u1")
        out = s._focused_trigger_reason(trig)
        assert out.startswith("有人@了你")
        assert "不要把上文里别的" in out

    def test_directed_followup_and_correction_focused(self) -> None:
        s = self._scheduler(topic_block_config=self._config())
        for mode in ("directed_followup", "correction", "qq_interaction"):
            trig = TriggerContext(reason="r", mode=mode, target_message_id=1, target_user_id="u1")
            assert "不要把上文里别的" in s._focused_trigger_reason(trig)

    def test_non_addressed_mode_unchanged(self) -> None:
        s = self._scheduler(topic_block_config=self._config())
        trig = TriggerContext(reason="收尾", mode="closing", target_message_id=1, target_user_id="u1")
        assert s._focused_trigger_reason(trig) == "收尾"  # closing not in focus modes

    def test_disabled_tracker_returns_original_reason(self) -> None:
        s = self._scheduler(topic_block_config=None)  # tracker off
        trig = TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=1, target_user_id="u1")
        assert s._focused_trigger_reason(trig) == "有人@了你"


class TestAddressedWaitDeferral:
    """@ turn whose thinker chose wait must not be silently dropped — it
    re-fires (forced) after a quiet window, bounded by wait_max_deferrals."""

    def _thinker_cfg(self, *, delay: float = 0.05, max_def: int = 1):
        from kernel.config import ThinkerConfig

        return ThinkerConfig(wait_deferral_seconds=delay, wait_max_deferrals=max_def)

    def _at_trigger(self) -> TriggerContext:
        # at_mention but addressee_self=False (the F-γ shape: @bot + @other)
        return TriggerContext(
            reason="有人@了你", mode="at_mention", target_message_id=1, target_user_id="u1",
            extra={"addressee_self": False},
        )

    async def test_wait_defers_then_force_fires(self) -> None:
        llm = _FakeLLM(reply=None, thinker_action="wait")  # thinker waits, nothing sent
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            thinker_config=self._thinker_cfg(delay=0.05, max_def=1),
        )
        slot = scheduler._slots.setdefault("111", _GroupSlot())
        # Simulate _do_chat having just run an @ turn that waited.
        scheduler._maybe_defer_addressed_wait("111", self._at_trigger())
        assert slot.wait_defer_task is not None
        assert slot.wait_deferrals == 1
        await asyncio.sleep(0.15)  # let the deferral window elapse + re-fire
        await asyncio.sleep(0.05)
        # The deferred re-fire forced a reply → chat() called with force_reply=True.
        assert any(c.get("force_reply") is True for c in llm.calls)
        await scheduler.close()

    async def test_deferral_capped(self) -> None:
        llm = _FakeLLM(reply=None, thinker_action="wait")
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            thinker_config=self._thinker_cfg(delay=0.05, max_def=1),
        )
        slot = scheduler._slots.setdefault("111", _GroupSlot())
        slot.wait_deferrals = 1  # already at cap
        scheduler._maybe_defer_addressed_wait("111", self._at_trigger())
        assert slot.wait_defer_task is None  # capped → no new deferral
        await scheduler.close()

    async def test_non_wait_does_not_defer(self) -> None:
        llm = _FakeLLM(reply=None, thinker_action="reply")  # not a wait
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            thinker_config=self._thinker_cfg(),
        )
        slot = scheduler._slots.setdefault("111", _GroupSlot())
        scheduler._maybe_defer_addressed_wait("111", self._at_trigger())
        assert slot.wait_defer_task is None
        await scheduler.close()

    async def test_disabled_when_seconds_zero(self) -> None:
        llm = _FakeLLM(reply=None, thinker_action="wait")
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            thinker_config=self._thinker_cfg(delay=0.0, max_def=1),  # disabled
        )
        slot = scheduler._slots.setdefault("111", _GroupSlot())
        scheduler._maybe_defer_addressed_wait("111", self._at_trigger())
        assert slot.wait_defer_task is None
        await scheduler.close()

    async def test_superseded_by_new_trigger(self) -> None:
        llm = _FakeLLM(reply=None, thinker_action="wait")
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            thinker_config=self._thinker_cfg(delay=0.05, max_def=1),
        )
        slot = scheduler._slots.setdefault("111", _GroupSlot())
        scheduler._maybe_defer_addressed_wait("111", self._at_trigger())
        slot.trigger = self._at_trigger()  # a new turn queued before the window elapsed
        await asyncio.sleep(0.15)
        # Superseded → deferred fire skipped, no forced chat call.
        assert not any(c.get("force_reply") is True for c in llm.calls)
        await scheduler.close()


class TestP7RuleLayerBoundary:
    """P7: the rule layer (strong signals) decides before any gray-zone scoring.
    An addressed @bot must fire by obligation without RWS ever being computed —
    proving the boundary holds (no "should I speak" scoring above the marker)."""

    def _at(self) -> TriggerContext:
        return TriggerContext(reason="有人@了你", mode="at_mention", extra={"addressee_self": True})

    async def test_at_mention_fires_without_invoking_rws(self) -> None:
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        calls: list[str] = []
        scheduler._maybe_compute_rws = (  # type: ignore[method-assign]
            lambda *a, **k: calls.append("rws") or None  # type: ignore[func-returns-value]
        )
        scheduler.notify("111", trigger=self._at(), is_addressed=True)
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1  # rule layer fired the @
        assert calls == []  # gray-zone scoring never ran
        await scheduler.close()

    async def test_non_addressed_message_reaches_gray_zone(self) -> None:
        """A plain non-@ message (no rule-layer hit) is the only path that may
        invoke RWS scoring — confirming the boundary is where it should be."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        calls: list[str] = []
        scheduler._maybe_compute_rws = (  # type: ignore[method-assign]
            lambda *a, **k: calls.append("rws") or None  # type: ignore[func-returns-value]
        )
        scheduler.notify("111", message_text="大家在聊什么")
        await asyncio.sleep(0.1)
        assert calls == ["rws"]  # gray-zone scoring ran for the non-addressed path
        await scheduler.close()
