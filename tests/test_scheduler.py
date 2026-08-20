"""GroupChatScheduler unit tests."""

import asyncio
import time
from typing import Any, cast

from kernel.config import GroupConfig, GroupOverride
from kernel.types import AddressingContext, ReplyObligation, TriggerContext
from services.llm.arbiter import PendingMessage
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


def test_must_obligation_overrides_legacy_addressee_flag() -> None:
    addressing = AddressingContext(
        addressed=True,
        target="self",
        confidence=1.0,
        evidence="nickname_original",
        original_text="emu。",
        stripped_text="。",
        matched_nickname="emu",
    )
    obligation = ReplyObligation(
        level="must",
        reason="self_addressed",
        source="nickname_original",
        priority=100,
        addressing=addressing,
    )
    assert _should_force_reply(
        TriggerContext(
            reason="有人叫你「emu」",
            mode="at_mention",
            obligation=obligation,
            extra={"addressee_self": False},
        )
    ) is True


async def test_source_user_content_falls_back_to_latest_pending_after_stale_anchor() -> None:
    """A delayed topic anchor must not erase the current user turn."""
    timeline = GroupTimeline()
    timeline.add("111", role="user", speaker="old(1)", content="旧话题", message_id=10)
    timeline.add("111", role="user", speaker="new(1)", content="你怎么不叫", message_id=20)
    timeline.add_pending_trigger("111", reason="旧话题 anchor", message_id=10)
    scheduler = GroupChatScheduler(
        llm=_FakeLLM(),  # type: ignore[arg-type]
        timeline=timeline,
        persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
        group_config=_make_config(),
    )

    content = scheduler._source_user_content(
        "111",
        TriggerContext(reason="continuation", mode="companion", target_message_id=999),
    )

    assert content == "你怎么不叫"
    await scheduler.close()


async def test_source_user_content_keeps_exact_target_over_newer_pending() -> None:
    """Explicit reply evidence remains authoritative when it is present."""
    timeline = GroupTimeline()
    timeline.add("111", role="user", speaker="old(1)", content="被引用的原话", message_id=10)
    timeline.add("111", role="user", speaker="new(1)", content="后来的补充", message_id=20)
    scheduler = GroupChatScheduler(
        llm=_FakeLLM(),  # type: ignore[arg-type]
        timeline=timeline,
        persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
        group_config=_make_config(),
    )

    content = scheduler._source_user_content(
        "111",
        TriggerContext(reason="reply", mode="at_mention", target_message_id="10"),  # type: ignore[arg-type]
    )

    assert content == "被引用的原话"
    await scheduler.close()


async def test_same_user_continuation_does_not_cancel_overhearer_fire() -> None:
    """A proactive overhearer reply keeps its original role during generation."""
    from kernel.config import TopicBlockConfig

    llm = _FakeLLM(reply=None, delay=0.5)
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=GroupTimeline(),
        persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
        group_config=_make_config(),
        topic_block_config=TopicBlockConfig(enabled=True),
    )
    scheduler.notify("111", user_id="42", message_text="旁观话题")
    await asyncio.sleep(0.05)
    slot = scheduler._slots["111"]
    assert slot.firing_role == "overhearer"
    assert slot.running_task is not None and not slot.running_task.done()

    scheduler.notify("111", user_id="42", message_text="新的旁观消息")
    assert slot.pending_during_generation == []
    assert not slot.running_task.cancelled()
    await scheduler.close()


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


class _GateLLM:
    """Test double whose calls advance only when the test releases them."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._started: list[asyncio.Event] = []
        self._release: list[asyncio.Event] = []

    async def chat(self, **kwargs) -> None:  # type: ignore[override]
        started = asyncio.Event()
        release = asyncio.Event()
        self.calls.append(kwargs)
        self._started.append(started)
        self._release.append(release)
        started.set()
        await release.wait()

    async def wait_started(self, index: int) -> None:
        async def wait_for_call() -> None:
            while len(self._started) <= index:
                await asyncio.sleep(0)
            await self._started[index].wait()

        await asyncio.wait_for(wait_for_call(), timeout=1.0)

    def release(self, index: int) -> None:
        self._release[index].set()


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
        assert len(slot.pending_direct_triggers) == 1
        assert slot.trigger is not None and slot.trigger.mode == "directed_followup"
        assert slot.consecutive_skip == 0

        scheduler.clear_pending("111", cancel_running=True)
        await asyncio.sleep(0.05)

        assert slot.pending_during_generation == []
        assert slot.pending_direct_triggers == []
        assert slot.trigger is None
        assert slot.consecutive_skip == 0
        assert len(llm.calls) == 1
        await scheduler.close()

    async def test_queued_ratified_continuation_is_not_rewritten_as_at_mention(self) -> None:
        """Arbiter-A owns only actual @ bursts, never a queued continuation."""
        from types import SimpleNamespace

        llm = _FakeLLM(reply=None, delay=0.08)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler._arbiter_config = SimpleNamespace(enabled=True, runtime_groups=[])  # type: ignore[assignment]
        scheduler.set_arbiter(object())  # type: ignore[arg-type]
        scheduler.notify(
            "111",
            trigger=TriggerContext(reason="继续刚才的话题", mode="directed_followup"),
            user_id="u1",
            message_text="先说这个",
            message_id=1,
        )
        await asyncio.sleep(0.01)
        scheduler.notify(
            "111",
            trigger=TriggerContext(
                reason="同一话题继续", mode="ratified_continuation", target_message_id=2,
                target_user_id="u1", extra={"block_id": "b1"},
            ),
            user_id="u1",
            message_text="接着说",
            message_id=2,
        )
        await asyncio.sleep(0.2)

        assert len(llm.calls) == 2
        followup = llm.calls[1]["trigger"]
        assert followup is not None and followup.mode == "ratified_continuation"
        await scheduler.close()


class TestPendingReset:
    async def test_same_user_continuation_cancels_before_first_segment_and_refires(self) -> None:
        """A follow-up arriving during generation must replace stale context."""
        llm = _FakeLLM(reply=None, delay=0.5)
        scheduler = GroupChatScheduler(
            llm=llm,  # type: ignore[arg-type]
            timeline=GroupTimeline(),
            persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111", user_id="42", message_text="？")
        await asyncio.sleep(0.05)
        slot = scheduler._slots["111"]
        assert slot.running_task is not None and not slot.running_task.done()

        scheduler.notify("111", user_id="42", message_text="你怎么不叫")
        assert len(slot.pending_during_generation) == 1
        await asyncio.sleep(0.15)

        assert len(llm.calls) == 2
        assert slot.pending_during_generation == []
        await scheduler.close()

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

    async def test_mute_unmute_old_chat_cannot_clear_replacement_task(self) -> None:
        """A cancelled pre-mute chat must not detach an immediate replacement."""
        llm = _GateLLM()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
        )

        try:
            scheduler.notify(
                "111",
                trigger=TriggerContext(reason="旧触发", mode="directed_followup"),
                user_id="u1",
                message_text="旧问题",
                message_id=1,
            )
            await llm.wait_started(0)
            slot = scheduler._slots["111"]
            previous = slot.running_task
            assert previous is not None

            scheduler.mute("111", source="test")
            scheduler.unmute("111")
            scheduler.notify(
                "111",
                trigger=TriggerContext(reason="新触发", mode="directed_followup"),
                user_id="u2",
                message_text="新问题",
                message_id=2,
            )
            replacement = slot.running_task
            assert replacement is not None and replacement is not previous

            # Let the cancelled task run its finally block before checking the
            # post-unmute task identity.
            await asyncio.sleep(0)
            assert slot.running_task is replacement
            await llm.wait_started(1)
            llm.release(1)
        finally:
            await scheduler.close()

    async def test_mute_unmute_old_arbiter_cannot_clear_replacement_burst(self) -> None:
        """A cancelled pre-mute Arbiter-A task must not consume a new @ burst."""
        from types import SimpleNamespace

        scheduler = GroupChatScheduler(
            llm=_FakeLLM(reply=None), timeline=GroupTimeline(),  # type: ignore[arg-type]
            persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
        )
        scheduler._arbiter_config = SimpleNamespace(  # type: ignore[assignment]
            enabled=True,
            runtime_groups=[],
            completeness_poll_interval_s=60.0,
            completeness_max_wait_s=60.0,
            completeness_confidence_threshold=1.0,
        )
        scheduler.set_arbiter(object())  # type: ignore[arg-type]

        try:
            scheduler.notify(
                "111",
                trigger=TriggerContext(reason="旧@", mode="at_mention"),
                user_id="u1",
                message_text="旧的@",
                message_id=1,
                at_self=True,
            )
            slot = scheduler._slots["111"]
            previous = slot.arbiter_task
            assert previous is not None
            await asyncio.sleep(0)

            scheduler.mute("111", source="test")
            scheduler.unmute("111")
            scheduler.notify(
                "111",
                trigger=TriggerContext(reason="新@", mode="at_mention"),
                user_id="u2",
                message_text="新的@",
                message_id=2,
                at_self=True,
            )
            replacement = slot.arbiter_task
            assert replacement is not None and replacement is not previous

            await asyncio.sleep(0)
            assert slot.arbiter_task is replacement
            assert [message.content for message in slot.burst_pending] == ["新的@"]
        finally:
            await scheduler.close()

    async def test_mute_rejects_late_segment_from_cancel_suppressing_provider(self) -> None:
        """A detached chat cannot send after a provider swallows cancellation."""
        from unittest.mock import AsyncMock

        class _CancellationSuppressingLLM:
            def __init__(self) -> None:
                self.started = asyncio.Event()
                self.cancelled = asyncio.Event()
                self.late_segment_allowed: bool | None = None

            async def chat(self, **kwargs) -> str:
                self.started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    on_segment = kwargs["on_segment"]
                    assert on_segment is not None
                    self.late_segment_allowed = await on_segment("晚到的分段")
                    self.cancelled.set()
                    return ""

        async def _sent(*_args, sent_event=None, **_kwargs) -> float:
            if sent_event is not None:
                sent_event.set()
            return 0.1

        llm = _CancellationSuppressingLLM()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
        )
        scheduler._bot = object()  # type: ignore[assignment]
        send = AsyncMock(side_effect=_sent)
        scheduler._send_to_group = send  # type: ignore[method-assign]

        try:
            scheduler.notify(
                "111",
                trigger=TriggerContext(reason="旧触发", mode="directed_followup"),
                user_id="u1",
                message_text="旧问题",
                message_id=1,
            )
            await asyncio.wait_for(llm.started.wait(), timeout=1.0)

            scheduler.mute("111", source="test")
            await asyncio.wait_for(llm.cancelled.wait(), timeout=1.0)

            assert llm.late_segment_allowed is False
            send.assert_not_awaited()
        finally:
            await scheduler.close()

    async def test_live_slot_accepts_stream_segment_from_wait_for_child_task(self) -> None:
        """A live _fire task must accept its LLM child's streamed segment."""
        from unittest.mock import AsyncMock

        class _StreamingLLM:
            def __init__(self) -> None:
                self.finished = asyncio.Event()
                self.segment_allowed: bool | None = None

            async def chat(self, **kwargs) -> str:
                on_segment = kwargs["on_segment"]
                assert on_segment is not None
                segment_task = asyncio.create_task(on_segment("正常流式分段"))
                self.segment_allowed = await segment_task
                self.finished.set()
                return ""

        async def _sent(*_args, sent_event=None, **_kwargs) -> float:
            if sent_event is not None:
                sent_event.set()
            return 0.1

        llm = _StreamingLLM()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
        )
        scheduler._bot = object()  # type: ignore[assignment]
        send = AsyncMock(side_effect=_sent)
        scheduler._send_to_group = send  # type: ignore[method-assign]

        try:
            scheduler.notify(
                "111",
                trigger=TriggerContext(reason="正常流", mode="directed_followup"),
                user_id="u1",
                message_text="请正常回复",
                message_id=1,
            )
            await asyncio.wait_for(llm.finished.wait(), timeout=1.0)

            assert llm.segment_allowed is True
            send.assert_awaited_once()
        finally:
            await scheduler.close()

    async def test_replacement_slot_rejects_late_segment_from_cancel_suppressing_provider(self) -> None:
        """An old provider cannot send into or detach an unmuted replacement."""
        from unittest.mock import AsyncMock

        class _CancellationSuppressingLLM:
            def __init__(self) -> None:
                self.old_started = asyncio.Event()
                self.replacement_started = asyncio.Event()
                self.allow_old_callback = asyncio.Event()
                self.old_callback_done = asyncio.Event()
                self.release_replacement = asyncio.Event()
                self.late_segment_allowed: bool | None = None
                self.calls = 0

            async def chat(self, **kwargs) -> str:
                call = self.calls
                self.calls += 1
                if call == 0:
                    self.old_started.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        await self.allow_old_callback.wait()
                        on_segment = kwargs["on_segment"]
                        assert on_segment is not None
                        self.late_segment_allowed = await on_segment("旧任务的晚到分段")
                        self.old_callback_done.set()
                        return ""
                self.replacement_started.set()
                await self.release_replacement.wait()
                return ""

        async def _sent(*_args, sent_event=None, **_kwargs) -> float:
            if sent_event is not None:
                sent_event.set()
            return 0.1

        llm = _CancellationSuppressingLLM()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
        )
        scheduler._bot = object()  # type: ignore[assignment]
        send = AsyncMock(side_effect=_sent)
        scheduler._send_to_group = send  # type: ignore[method-assign]

        try:
            scheduler.notify(
                "111",
                trigger=TriggerContext(reason="旧触发", mode="directed_followup"),
                user_id="u1",
                message_text="旧问题",
                message_id=1,
            )
            await asyncio.wait_for(llm.old_started.wait(), timeout=1.0)
            slot = scheduler._slots["111"]

            scheduler.mute("111", source="test")
            scheduler.unmute("111")
            scheduler.notify(
                "111",
                trigger=TriggerContext(reason="新触发", mode="directed_followup"),
                user_id="u2",
                message_text="新问题",
                message_id=2,
            )
            replacement = slot.running_task
            assert replacement is not None

            llm.allow_old_callback.set()
            await asyncio.wait_for(llm.old_callback_done.wait(), timeout=1.0)

            assert llm.late_segment_allowed is False
            send.assert_not_awaited()
            assert slot.running_task is replacement
            await asyncio.wait_for(llm.replacement_started.wait(), timeout=1.0)
        finally:
            llm.allow_old_callback.set()
            llm.release_replacement.set()
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

    async def test_close_does_not_refire_pending_same_user_continuation(self) -> None:
        """Shutdown cannot turn a cancelled continuation into a new chat call."""
        llm = _FakeLLM(reply=None, delay=1.0)
        scheduler = GroupChatScheduler(
            llm=llm,  # type: ignore[arg-type]
            timeline=GroupTimeline(),
            persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        scheduler.notify("111", user_id="42", message_text="？")
        await asyncio.sleep(0.05)
        slot = scheduler._slots["111"]
        assert len(llm.calls) == 1

        scheduler.notify("111", user_id="42", message_text="你怎么不叫")
        assert len(slot.pending_during_generation) == 1

        await scheduler.close()

        assert len(llm.calls) == 1
        assert slot.running_task is None or slot.running_task.done()
        assert slot.pending_during_generation == []
        assert slot.block_fire_queue == []


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

    async def test_mute_drops_direct_continuation_queued_by_cancelled_chat(self) -> None:
        """Mute must not let a queued focused turn escape a cancelled reply."""
        llm = _GateLLM()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        try:
            scheduler.notify(
                "111",
                trigger=TriggerContext(
                    reason="先回答", mode="directed_followup", target_message_id=1, target_user_id="u1",
                ),
                user_id="u1",
                message_text="先说这个",
                message_id=1,
            )
            await llm.wait_started(0)
            scheduler.notify(
                "111",
                trigger=TriggerContext(
                    reason="继续", mode="ratified_continuation", target_message_id=2, target_user_id="u1",
                ),
                user_id="u1",
                message_text="还想听",
                message_id=2,
            )
            slot = scheduler._slots["111"]
            running = slot.running_task
            assert running is not None
            assert len(slot.pending_direct_triggers) == 1

            scheduler.mute("111")
            await asyncio.gather(running, return_exceptions=True)

            assert len(llm.calls) == 1
            assert slot.pending_direct_triggers == []
        finally:
            await scheduler.close()

    async def test_mute_cancels_deferred_addressed_fire_before_unmute(self) -> None:
        """Unmuting cannot revive an addressed wait that mute invalidated."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        try:
            slot = scheduler._slots.setdefault("111", _GroupSlot())
            slot.wait_defer_task = asyncio.create_task(
                scheduler._deferred_addressed_fire(
                    "111",
                    TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=1),
                    0.05,
                )
            )
            await asyncio.sleep(0)

            scheduler.mute("111")
            scheduler.unmute("111")
            await asyncio.sleep(0.1)

            assert len(llm.calls) == 0
            assert slot.wait_defer_task is None
        finally:
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

    async def test_do_chat_records_exact_block_reply_time_after_successful_send(self) -> None:
        """A successful send records its timestamp only on the firing block."""
        from unittest.mock import AsyncMock

        llm = _FakeLLM(reply="好呀")
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            topic_block_config=self._config("silent"),
        )
        async def _sent(*_args, sent_event=None, **_kwargs) -> float:
            if sent_event is not None:
                sent_event.set()
            return 0.1

        scheduler._send_to_group = AsyncMock(side_effect=_sent)  # type: ignore[method-assign]
        block = scheduler._topic_tracker.observe("111", message_id=1, speaker="u1", text="姆姆在吗")
        slot = scheduler._slots.setdefault("111", _GroupSlot())
        slot.firing_block_id = block.block_id
        await scheduler._do_chat("111", trigger=TriggerContext(
            reason="@", mode="at_mention", target_message_id=1, target_user_id="u1",
            extra={"block_id": block.block_id},
        ))
        assert block.bot_involved is True
        assert block.last_bot_reply_at > 0.0
        await scheduler.close()

    async def test_must_trigger_sends_fallback_when_llm_fails(self) -> None:
        """An obligated call must remain visible when the provider is unavailable."""
        from unittest.mock import AsyncMock

        class _FailingLLM(_FakeLLM):
            async def chat(self, **_kwargs: Any) -> str | None:  # type: ignore[override]
                raise RuntimeError("provider unavailable")

        llm = _FailingLLM()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
        )
        sent: list[str] = []

        async def _sent(_group_id: str, text: str, *, sent_event=None, **_kwargs: Any) -> float:
            sent.append(text)
            if sent_event is not None:
                sent_event.set()
            return 0.0

        scheduler._send_to_group = AsyncMock(side_effect=_sent)  # type: ignore[method-assign]
        scheduler._slots.setdefault("111", _GroupSlot())
        obligation = ReplyObligation(level="must", reason="self_addressed", source="nickname_original")

        await scheduler._do_chat(
            "111",
            trigger=TriggerContext(
                reason="有人叫你「姆」",
                mode="at_mention",
                obligation=obligation,
                target_message_id=42,
                target_user_id="u1",
                extra={"addressee_self": True},
            ),
        )

        assert sent == ["[CQ:reply,id=42]我在，刚才没接上，再喊我一下？"]
        await scheduler.close()

    async def test_proactive_trigger_does_not_emit_failure_fallback(self) -> None:
        """Provider errors must not turn ordinary overhearing into a reply."""
        from unittest.mock import AsyncMock

        class _FailingLLM(_FakeLLM):
            async def chat(self, **_kwargs: Any) -> str | None:  # type: ignore[override]
                raise RuntimeError("provider unavailable")

        scheduler = GroupChatScheduler(
            llm=_FailingLLM(), timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
        )
        sent: list[str] = []

        async def _sent(_group_id: str, text: str, **_kwargs: Any) -> float:
            sent.append(text)
            return 0.0

        scheduler._send_to_group = AsyncMock(side_effect=_sent)  # type: ignore[method-assign]
        scheduler._slots.setdefault("111", _GroupSlot())

        await scheduler._do_chat("111")

        assert sent == []
        await scheduler.close()

    async def test_must_trigger_sends_fallback_when_llm_times_out(self) -> None:
        """A timed-out obligated call must still leave a visible acknowledgement."""
        from unittest.mock import AsyncMock

        class _TimedOutLLM(_FakeLLM):
            async def chat(self, **_kwargs: Any) -> str | None:  # type: ignore[override]
                raise TimeoutError("provider timeout")

        scheduler = GroupChatScheduler(
            llm=_TimedOutLLM(), timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
        )
        sent: list[str] = []

        async def _sent(_group_id: str, text: str, *, sent_event=None, **_kwargs: Any) -> float:
            sent.append(text)
            if sent_event is not None:
                sent_event.set()
            return 0.0

        scheduler._send_to_group = AsyncMock(side_effect=_sent)  # type: ignore[method-assign]
        scheduler._slots.setdefault("111", _GroupSlot())
        obligation = ReplyObligation(level="must", reason="self_addressed", source="nickname_original")

        await scheduler._do_chat(
            "111",
            trigger=TriggerContext(
                reason="有人叫你「姆」",
                mode="at_mention",
                obligation=obligation,
                target_message_id=43,
                target_user_id="u1",
                extra={"addressee_self": True},
            ),
        )

        assert sent == ["[CQ:reply,id=43]我在，刚才没接上，再喊我一下？"]
        await scheduler.close()

    async def test_cancelled_streaming_reply_records_exact_block_after_visible_segment(self) -> None:
        """A delivered stream segment remains a real block reply after cancellation."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        class _StreamingUntilCancelledLLM:
            def __init__(self) -> None:
                self.segment_sent = asyncio.Event()
                self.block = asyncio.Event()

            async def chat(self, **kwargs) -> None:  # type: ignore[override]
                on_segment = kwargs["on_segment"]
                assert on_segment is not None
                assert await on_segment("already delivered first segment") is True
                self.segment_sent.set()
                await self.block.wait()

        llm = _StreamingUntilCancelledLLM()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            topic_block_config=self._config("silent"),
        )
        scheduler.set_bot(cast(Any, SimpleNamespace(self_id="bot")))

        async def _sent(*_args, sent_event=None, **_kwargs) -> float:
            if sent_event is not None:
                sent_event.set()
            return 0.1

        scheduler._send_to_group = AsyncMock(side_effect=_sent)  # type: ignore[method-assign]
        assert scheduler._topic_tracker is not None
        block = scheduler._topic_tracker.observe("111", message_id=1, speaker="u1", text="mum are you there")
        slot = scheduler._slots.setdefault("111", _GroupSlot())
        slot.firing_block_id = block.block_id
        task = asyncio.create_task(scheduler._do_chat("111", trigger=TriggerContext(
            reason="@", mode="at_mention", target_message_id=1, target_user_id="u1",
            extra={"block_id": block.block_id},
        )))
        try:
            await asyncio.wait_for(llm.segment_sent.wait(), timeout=1.0)
            delivered_at = block.last_bot_reply_at
            assert delivered_at > 0.0

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

            assert block.bot_involved is True
            assert block.last_bot_reply_at >= delivered_at
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
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

    async def test_ratified_floor_zero_degrades_to_companion_rescue(self) -> None:
        """Floor=0 (default) + ratified follow-up that the base prob would skip
        now degrades to a companion weak-reply (the "宝宝降级" fix) instead of
        SILENCE. A ratified continuation is a message that should be SEEN; rather
        than dropping it on a probability miss, we fire a companion-mode trigger
        so the thinker can pick a short ack/sticker."""
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
        assert len(llm.calls) == 1  # companion rescue fired, not SILENCE
        rescue_trigger = llm.calls[0]["trigger"]
        assert rescue_trigger is not None and rescue_trigger.mode == "companion"
        await scheduler.close()

    async def test_ratified_companion_rescue_respects_light_cooldown(self) -> None:
        """The companion rescue is rate-limited by the shared light cooldown so
        it does not become "reply to every follow-up": a recent light reply
        suppresses the rescue, leaving the continuation SILENT."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        scheduler._topic_tracker.observe("111", message_id=0, speaker="u1", text="姆姆你看", at_self=True)
        scheduler._topic_tracker.mark_bot_involved("111")
        # Pre-create the slot with a recent light reply → cooldown active.
        from services.scheduler import _GroupSlot

        slot = scheduler._slots.setdefault("111", _GroupSlot())
        slot.last_light_time = time.time()
        scheduler.notify("111", user_id="u1", message_text="你懂雪人三项吗", message_id=2)
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 0  # within cooldown → no rescue, stays silent
        await scheduler.close()

    async def test_current_uninvolved_block_is_not_rescued_by_other_ratified_block(self) -> None:
        """A bot-involved block A must not ratify an unrelated current block B."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        assert scheduler._topic_tracker is not None
        block_a = scheduler._topic_tracker.observe(
            "111", message_id=1, speaker="u1", text="大狗叫得很响", at_self=True,
        )
        scheduler._topic_tracker.mark_bot_involved("111", block_id=block_a.block_id)

        # This message opens block B. Before the fix, _receiver_role() selected
        # block A globally and incorrectly sent a companion rescue for B.
        scheduler.notify("111", user_id="u2", message_text="今天的天气真好", message_id=2)
        await asyncio.sleep(0.1)

        assert len(llm.calls) == 0
        await scheduler.close()

    async def test_probability_reply_records_the_current_uninvolved_block_only(self) -> None:
        """A delivered ordinary reply starts continuity for its own topic, not A."""
        from unittest.mock import AsyncMock

        async def _sent(*_args, sent_event=None, **_kwargs) -> float:
            if sent_event is not None:
                sent_event.set()
            return 0.1

        llm = _FakeLLM(reply="收到")
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            topic_block_config=self._config("shadow", ratified_floor=0.0),
        )
        scheduler._send_to_group = AsyncMock(side_effect=_sent)  # type: ignore[method-assign]
        assert scheduler._topic_tracker is not None
        block_a = scheduler._topic_tracker.observe(
            "111", message_id=1, speaker="u1", text="大狗叫得很响", at_self=True,
        )
        scheduler._topic_tracker.mark_bot_replied(
            "111", now=time.monotonic() - 100.0, block_id=block_a.block_id,
        )
        block_b = scheduler._topic_tracker.observe(
            "111", message_id=2, speaker="u2", text="今天的天气真好",
        )
        previous_a_reply_at = block_a.last_bot_reply_at

        scheduler.notify("111", user_id="u2", message_text="天气确实很好", message_id=3)
        await asyncio.sleep(0.1)

        assert len(llm.calls) == 1
        assert block_b.last_bot_reply_at > 0.0
        assert block_a.last_bot_reply_at == previous_a_reply_at
        await scheduler.close()

    async def test_long_gap_same_ratified_block_uses_focused_current_anchor(self) -> None:
        """A 10-minute same-block continuation must not become companion/sticker-only."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        assert scheduler._topic_tracker is not None
        block = scheduler._topic_tracker.observe(
            "111", message_id=1, speaker="u1", text="大狗叫得很响", at_self=True,
        )
        scheduler._topic_tracker.mark_bot_replied(
            "111", now=time.monotonic() - 586.0, block_id=block.block_id,
        )

        scheduler.notify("111", user_id="u1", message_text="大狗还在叫吗", message_id=2)
        await asyncio.sleep(0.1)

        assert len(llm.calls) == 1
        trigger = llm.calls[0]["trigger"]
        assert trigger is not None
        assert trigger.mode == "ratified_continuation"
        assert trigger.target_message_id == 2
        assert trigger.target_user_id == "u1"
        assert trigger.extra["block_id"] == block.block_id
        assert llm.calls[0]["force_reply"] is True
        assert "不要把上文里别的" in scheduler._focused_trigger_reason(trigger)
        assert "轻轻应一声" not in trigger.reason
        await scheduler.close()

    async def test_long_gap_same_block_keeps_continuation_mode_when_probability_fires(self) -> None:
        """A probability hit must not strip the same-block continuation anchor."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        assert scheduler._topic_tracker is not None
        block = scheduler._topic_tracker.observe(
            "111", message_id=1, speaker="u1", text="大狗叫得很响", at_self=True,
        )
        scheduler._topic_tracker.mark_bot_replied(
            "111", now=time.monotonic() - 586.0, block_id=block.block_id,
        )

        scheduler.notify("111", user_id="u1", message_text="大狗还在叫吗", message_id=2)
        await asyncio.sleep(0.1)

        assert len(llm.calls) == 1
        trigger = llm.calls[0]["trigger"]
        assert trigger is not None and trigger.mode == "ratified_continuation"
        assert trigger.target_message_id == 2
        await scheduler.close()

    async def test_long_gap_same_block_bypasses_proactive_none_without_global_rescue(self) -> None:
        """An active same-block continuation remains answerable with proactive off."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity(proactive=None)),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        assert scheduler._topic_tracker is not None
        block = scheduler._topic_tracker.observe(
            "111", message_id=1, speaker="u1", text="大狗叫得很响", at_self=True,
        )
        scheduler._topic_tracker.mark_bot_replied(
            "111", now=time.monotonic() - 586.0, block_id=block.block_id,
        )

        scheduler.notify("111", user_id="u1", message_text="大狗还在叫吗", message_id=2)
        await asyncio.sleep(0.1)

        assert len(llm.calls) == 1
        trigger = llm.calls[0]["trigger"]
        assert trigger is not None and trigger.mode == "ratified_continuation"
        await scheduler.close()

    async def test_internal_long_gap_continuation_survives_cancel_and_remerge(self) -> None:
        """A real queued candidate keeps its focused trigger after remerge."""
        llm = _FakeLLM(reply=None, delay=0.15)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        assert scheduler._topic_tracker is not None
        block = scheduler._topic_tracker.observe(
            "111", message_id=1, speaker="u1", text="大狗叫得很响", at_self=True,
        )
        scheduler._topic_tracker.mark_bot_replied(
            "111", now=time.monotonic() - 586.0, block_id=block.block_id,
        )
        scheduler.notify(
            "111",
            trigger=TriggerContext(
                reason="先回答", mode="directed_followup", target_message_id=2, target_user_id="u1",
            ),
            user_id="u1",
            message_text="大狗怎么了",
            message_id=2,
        )
        await asyncio.sleep(0.02)
        scheduler.notify("111", user_id="u1", message_text="大狗还在叫吗", message_id=3)
        await asyncio.sleep(0.25)

        assert len(llm.calls) == 2
        trigger = llm.calls[-1]["trigger"]
        assert trigger is not None and trigger.mode == "ratified_continuation"
        assert trigger.target_message_id == 3
        assert trigger.extra["block_id"] == block.block_id
        await scheduler.close()

    async def test_cancelled_internal_long_gap_continuation_clears_pending_trigger(self) -> None:
        """Shutdown/cancel cannot let an internal continuation leak into a later chat."""
        llm = _FakeLLM(reply=None, delay=1.0)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        assert scheduler._topic_tracker is not None
        block = scheduler._topic_tracker.observe(
            "111", message_id=1, speaker="u1", text="大狗叫得很响", at_self=True,
        )
        scheduler._topic_tracker.mark_bot_replied(
            "111", now=time.monotonic() - 586.0, block_id=block.block_id,
        )
        scheduler.notify(
            "111",
            trigger=TriggerContext(
                reason="先回答", mode="directed_followup", target_message_id=2, target_user_id="u1",
            ),
            user_id="u1",
            message_text="大狗怎么了",
            message_id=2,
        )
        await asyncio.sleep(0.02)
        scheduler.notify("111", user_id="u1", message_text="大狗还在叫吗", message_id=3)
        slot = scheduler._slots["111"]
        assert len(slot.pending_direct_triggers) == 1
        assert slot.pending_direct_triggers[0].mode == "ratified_continuation"

        scheduler.clear_pending("111", cancel_running=True)
        await asyncio.sleep(0.1)

        assert len(llm.calls) == 1
        assert slot.pending_during_generation == []
        assert slot.pending_direct_triggers == []
        assert slot.trigger is None
        await scheduler.close()

    async def test_long_gap_continuation_survives_block_queue_priority(self) -> None:
        """A serial @ block must not erase a newer focused continuation."""
        llm = _GateLLM()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        assert scheduler._topic_tracker is not None
        block = scheduler._topic_tracker.observe(
            "111", message_id=1, speaker="u1", text="大狗叫得很响", at_self=True,
        )
        scheduler._topic_tracker.mark_bot_replied(
            "111", now=time.monotonic() - 586.0, block_id=block.block_id,
        )

        try:
            scheduler.notify(
                "111",
                trigger=TriggerContext(
                    reason="先回答", mode="directed_followup", target_message_id=2, target_user_id="u1",
                ),
                user_id="u1",
                message_text="大狗怎么了",
                message_id=2,
            )
            await llm.wait_started(0)
            slot = scheduler._slots["111"]
            # The first real segment was already visible, so this is queued rather
            # than cancelling the reply it follows.
            slot.first_segment_sent = True
            slot.block_fire_queue.append(
                TriggerContext(
                    reason="另一个@", mode="at_mention", target_message_id=30,
                    target_user_id="u2", extra={"block_id": "other"},
                )
            )
            scheduler.notify("111", user_id="u1", message_text="大狗还在叫吗", message_id=3)

            llm.release(0)
            await llm.wait_started(1)
            assert llm.calls[1]["trigger"].mode == "at_mention"
            llm.release(1)
            await llm.wait_started(2)

            trigger = llm.calls[2]["trigger"]
            assert trigger is not None and trigger.mode == "ratified_continuation"
            assert trigger.target_message_id == 3
            assert trigger.extra["block_id"] == block.block_id
            assert llm.calls[2]["force_reply"] is True
        finally:
            await scheduler.close()

    async def test_mixed_at_and_long_gap_pending_keep_individual_trigger_modes(self) -> None:
        """Arbiter-A receives only @ turns; a queued continuation stays focused."""
        from types import SimpleNamespace

        class _AlwaysComplete:
            async def judge_completeness(self, *_args, **_kwargs):
                return SimpleNamespace(complete=True, confidence=1.0, fallback=False)

        llm = _GateLLM()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        scheduler._arbiter_config = SimpleNamespace(  # type: ignore[assignment]
            enabled=True,
            runtime_groups=[],
            completeness_poll_interval_s=0.01,
            completeness_max_wait_s=1.0,
            completeness_confidence_threshold=0.5,
        )
        scheduler.set_arbiter(_AlwaysComplete())  # type: ignore[arg-type]
        assert scheduler._topic_tracker is not None
        block = scheduler._topic_tracker.observe(
            "111", message_id=1, speaker="u1", text="大狗叫得很响", at_self=True,
        )
        scheduler._topic_tracker.mark_bot_replied(
            "111", now=time.monotonic() - 586.0, block_id=block.block_id,
        )

        try:
            scheduler.notify(
                "111",
                trigger=TriggerContext(
                    reason="先回答", mode="directed_followup", target_message_id=2, target_user_id="u1",
                ),
                user_id="u1",
                message_text="大狗怎么了",
                message_id=2,
            )
            await llm.wait_started(0)
            slot = scheduler._slots["111"]
            slot.first_segment_sent = True
            scheduler.notify(
                "111",
                trigger=TriggerContext(
                    reason="有人@了你", mode="at_mention", target_message_id=3, target_user_id="u2",
                ),
                user_id="u2",
                message_text="姆姆看看这个",
                message_id=3,
                at_self=True,
            )
            scheduler.notify("111", user_id="u1", message_text="大狗还在叫吗", message_id=4)

            llm.release(0)
            await llm.wait_started(1)
            at_trigger = llm.calls[1]["trigger"]
            assert at_trigger is not None and at_trigger.mode == "at_mention"
            assert at_trigger.target_message_id == 3
            llm.release(1)
            await llm.wait_started(2)

            continuation = llm.calls[2]["trigger"]
            assert continuation is not None and continuation.mode == "ratified_continuation"
            assert continuation.target_message_id == 4
            assert continuation.extra["block_id"] == block.block_id
            assert llm.calls[2]["force_reply"] is True
        finally:
            await scheduler.close()

    async def test_long_gap_continuation_waits_for_pending_arbiter_at(self) -> None:
        """An already queued @ keeps priority over a later focused continuation."""
        from types import SimpleNamespace

        class _AlwaysComplete:
            def __init__(self) -> None:
                self.pending_batches: list[list[PendingMessage]] = []

            async def judge_completeness(self, pending, *_args, **_kwargs):
                self.pending_batches.append(list(pending))
                return SimpleNamespace(complete=True, confidence=1.0, fallback=False)

        llm = _GateLLM()
        arbiter = _AlwaysComplete()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        scheduler._arbiter_config = SimpleNamespace(  # type: ignore[assignment]
            enabled=True,
            runtime_groups=[],
            completeness_poll_interval_s=0.01,
            completeness_max_wait_s=1.0,
            completeness_confidence_threshold=0.5,
        )
        scheduler.set_arbiter(arbiter)  # type: ignore[arg-type]
        assert scheduler._topic_tracker is not None
        block = scheduler._topic_tracker.observe(
            "111", message_id=1, speaker="u1", text="大狗叫得很响", at_self=True,
        )
        scheduler._topic_tracker.mark_bot_replied(
            "111", now=time.monotonic() - 300.0, block_id=block.block_id,
        )

        try:
            scheduler.notify(
                "111",
                trigger=TriggerContext(
                    reason="有人@了你", mode="at_mention", target_message_id=2, target_user_id="u2",
                ),
                user_id="u2",
                message_text="姆姆看看这个",
                message_id=2,
                at_self=True,
            )
            slot = scheduler._slots["111"]
            assert slot.arbiter_task is not None and not slot.arbiter_task.done()

            scheduler.notify(
                "111",
                user_id="u1",
                message_text="大狗还在叫吗",
                message_id=3,
                reply_to_sender_id="u1",
                reply_to_message_id=1,
            )
            assert len(llm.calls) == 0
            assert slot.pending_during_generation == []
            assert len(slot.pending_direct_triggers) == 1

            await llm.wait_started(0)
            at_trigger = llm.calls[0]["trigger"]
            assert at_trigger is not None and at_trigger.mode == "at_mention"
            assert at_trigger.target_message_id == 2
            assert len(arbiter.pending_batches) == 1
            assert [msg.evidence for msg in arbiter.pending_batches[0]] == ["at_mention"]
            llm.release(0)

            await llm.wait_started(1)
            continuation = llm.calls[1]["trigger"]
            assert continuation is not None and continuation.mode == "ratified_continuation"
            assert continuation.target_message_id == 3
            assert continuation.extra["block_id"] == block.block_id
            assert llm.calls[1]["force_reply"] is True
            assert llm.calls[1]["must_emit"] is False
            llm.release(1)
        finally:
            await scheduler.close()

    async def test_explicit_continuation_waits_for_pending_arbiter_at(self) -> None:
        """An explicit focused continuation cannot overtake Arbiter-A's @."""
        from types import SimpleNamespace

        class _AlwaysComplete:
            async def judge_completeness(self, *_args, **_kwargs):
                return SimpleNamespace(complete=True, confidence=1.0, fallback=False)

        llm = _GateLLM()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        scheduler._arbiter_config = SimpleNamespace(  # type: ignore[assignment]
            enabled=True,
            runtime_groups=[],
            completeness_poll_interval_s=0.01,
            completeness_max_wait_s=1.0,
            completeness_confidence_threshold=0.5,
        )
        scheduler.set_arbiter(_AlwaysComplete())  # type: ignore[arg-type]

        try:
            scheduler.notify(
                "111",
                trigger=TriggerContext(
                    reason="有人@了你", mode="at_mention", target_message_id=2, target_user_id="u2",
                ),
                user_id="u2",
                message_text="姆姆看看这个",
                message_id=2,
                at_self=True,
            )
            slot = scheduler._slots["111"]
            assert slot.arbiter_task is not None and not slot.arbiter_task.done()

            scheduler.notify(
                "111",
                trigger=TriggerContext(
                    reason="继续", mode="ratified_continuation", target_message_id=3, target_user_id="u1",
                ),
                user_id="u1",
                message_text="大狗还在叫吗",
                message_id=3,
            )
            await asyncio.sleep(0)
            assert len(llm.calls) == 0
            assert len(slot.pending_direct_triggers) == 1

            await llm.wait_started(0)
            at_trigger = llm.calls[0]["trigger"]
            assert at_trigger is not None and at_trigger.mode == "at_mention"
            assert at_trigger.target_message_id == 2
            assert at_trigger.reason == "有人@了你"
            llm.release(0)

            await llm.wait_started(1)
            continuation = llm.calls[1]["trigger"]
            assert continuation is not None and continuation.mode == "ratified_continuation"
            assert continuation.target_message_id == 3
            assert llm.calls[1]["force_reply"] is True
            llm.release(1)
        finally:
            await scheduler.close()

    def test_long_gap_eligibility_has_strict_minimum_and_bounded_maximum(self) -> None:
        """Only a delivered reply in this block can cross the 180s upgrade gate."""
        from kernel.config import TopicBlockConfig
        from services.group.topic_block import TopicBlock

        scheduler = GroupChatScheduler(
            llm=_FakeLLM(reply=None), timeline=GroupTimeline(),  # type: ignore[arg-type]
            persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=TopicBlockConfig(
                enabled=True,
                ratified_continuation_min_gap_seconds=180.0,
                ratified_continuation_window_seconds=600.0,
            ),
        )
        replied = TopicBlock(block_id="reply", bot_involved=True, last_bot_reply_at=100.0)
        inbound_only = TopicBlock(block_id="inbound", bot_involved=True)
        wrong_topic = TopicBlock(block_id="other", bot_involved=False, last_bot_reply_at=100.0)

        assert scheduler._ratified_continuation_age_seconds(replied, now=280.0) is None
        assert scheduler._ratified_continuation_age_seconds(replied, now=280.001) is not None
        assert scheduler._ratified_continuation_age_seconds(replied, now=700.0) == 600.0
        assert scheduler._ratified_continuation_age_seconds(replied, now=700.001) is None
        assert scheduler._ratified_continuation_age_seconds(inbound_only, now=400.0) is None
        assert scheduler._ratified_continuation_age_seconds(wrong_topic, now=400.0) is None

    async def test_long_gap_continuation_scores_as_original_untriggered_turn(self) -> None:
        """RWS sees mode=none; only its resolved fire uses the synthetic mode."""
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        assert scheduler._topic_tracker is not None
        block = scheduler._topic_tracker.observe(
            "111", message_id=1, speaker="u1", text="大狗叫得很响", at_self=True,
        )
        scheduler._topic_tracker.mark_bot_replied(
            "111", now=time.monotonic() - 300.0, block_id=block.block_id,
        )
        seen_triggers: list[TriggerContext | None] = []

        def capture_rws(*_args, **kwargs):
            seen_triggers.append(kwargs["trigger"])
            return None

        scheduler._maybe_compute_rws = capture_rws  # type: ignore[method-assign]
        scheduler.notify("111", user_id="u1", message_text="大狗还在叫吗", message_id=2)
        await asyncio.sleep(0.1)

        assert seen_triggers == [None]
        assert len(llm.calls) == 1
        assert llm.calls[0]["trigger"].mode == "ratified_continuation"
        assert llm.calls[0]["force_reply"] is True
        assert llm.calls[0]["must_emit"] is False
        await scheduler.close()

    async def test_long_gap_continuation_queues_behind_other_users_addressed_reply(self) -> None:
        """A force candidate must not be dropped merely because another user is active."""
        llm = _GateLLM()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=0.0),
            topic_block_config=self._config("silent", ratified_floor=0.0),
        )
        assert scheduler._topic_tracker is not None
        block = scheduler._topic_tracker.observe(
            "111", message_id=1, speaker="u1", text="大狗叫得很响", at_self=True,
        )
        scheduler._topic_tracker.mark_bot_replied(
            "111", now=time.monotonic() - 300.0, block_id=block.block_id,
        )

        try:
            scheduler.notify(
                "111",
                trigger=TriggerContext(
                    reason="先回答", mode="directed_followup", target_message_id=2, target_user_id="u1",
                ),
                user_id="u1",
                message_text="大狗怎么了",
                message_id=2,
            )
            await llm.wait_started(0)
            slot = scheduler._slots["111"]
            assert slot.firing_role == "addressed"
            running = slot.running_task
            scheduler.notify(
                "111",
                user_id="u2",
                message_text="大狗还在叫吗",
                message_id=3,
                reply_to_sender_id="u1",
                reply_to_message_id=1,
            )
            assert running is not None and not running.cancelled()

            llm.release(0)
            await llm.wait_started(1)
            trigger = llm.calls[1]["trigger"]
            assert trigger is not None and trigger.mode == "ratified_continuation"
            assert trigger.target_message_id == 3
            assert trigger.target_user_id == "u2"
            assert trigger.extra["block_id"] == block.block_id
            assert llm.calls[1]["force_reply"] is True
            llm.release(1)
        finally:
            await scheduler.close()

    async def test_long_gap_continuation_queues_behind_overhearer_reply(self) -> None:
        """A force candidate also survives an unrelated probability reply."""
        llm = _GateLLM()
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(talk_value=1.0),
            topic_block_config=self._config("shadow", ratified_floor=0.0),
        )
        assert scheduler._topic_tracker is not None
        block = scheduler._topic_tracker.observe(
            "111", message_id=1, speaker="u1", text="大狗叫得很响", at_self=True,
        )
        scheduler._topic_tracker.mark_bot_replied(
            "111", now=time.monotonic() - 300.0, block_id=block.block_id,
        )
        # Keep the old block eligible for its explicit reply edge, but make the
        # next ordinary message open a separate, overheard block.
        block.last_active = time.monotonic() - 121.0

        try:
            scheduler.notify("111", user_id="u9", message_text="今天下雨，路很滑", message_id=2)
            await llm.wait_started(0)
            slot = scheduler._slots["111"]
            assert slot.firing_role == "overhearer"
            running = slot.running_task
            scheduler.notify(
                "111",
                user_id="u2",
                message_text="大狗还在叫吗",
                message_id=3,
                reply_to_sender_id="u1",
                reply_to_message_id=1,
            )
            assert running is not None and not running.cancelled()

            llm.release(0)
            await llm.wait_started(1)
            trigger = llm.calls[1]["trigger"]
            assert trigger is not None and trigger.mode == "ratified_continuation"
            assert trigger.target_message_id == 3
            assert trigger.target_user_id == "u2"
            assert trigger.extra["block_id"] == block.block_id
            assert llm.calls[1]["force_reply"] is True
            llm.release(1)
        finally:
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

    def test_self_echo_directive_added_when_just_replied(self) -> None:
        s = self._scheduler(topic_block_config=self._config())
        slot = _GroupSlot()
        slot.last_reply_content = "这个表情被抓拍得好到位，我就是这副表情"
        slot.last_reply_time = time.time()
        trig = TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=1, target_user_id="u1")
        out = s._focused_trigger_reason(trig, slot)
        assert "不要把上文里别的" in out  # base directive still present
        assert "你刚刚已经主动说过" in out  # self-echo exception appended
        assert "别再复述一遍" in out

    def test_self_echo_directive_skipped_outside_window(self) -> None:
        s = self._scheduler(topic_block_config=self._config())
        slot = _GroupSlot()
        slot.last_reply_content = "刚说过的话"
        slot.last_reply_time = time.time() - 999.0  # far outside 15s window
        trig = TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=1, target_user_id="u1")
        out = s._focused_trigger_reason(trig, slot)
        assert "你刚刚已经主动说过" not in out

    def test_self_echo_directive_skipped_when_no_prior_reply(self) -> None:
        s = self._scheduler(topic_block_config=self._config())
        slot = _GroupSlot()  # last_reply_content empty, last_reply_time 0
        trig = TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=1, target_user_id="u1")
        out = s._focused_trigger_reason(trig, slot)
        assert "你刚刚已经主动说过" not in out

    def test_self_echo_directive_absent_when_slot_not_passed(self) -> None:
        # Backward-compat: callers that don't pass slot get the base directive only.
        s = self._scheduler(topic_block_config=self._config())
        trig = TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=1, target_user_id="u1")
        out = s._focused_trigger_reason(trig)
        assert "不要把上文里别的" in out
        assert "你刚刚已经主动说过" not in out


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

    async def test_must_obligation_passes_must_emit_to_chat(self) -> None:
        llm = _FakeLLM(reply=None)
        scheduler = GroupChatScheduler(
            llm=llm, timeline=GroupTimeline(), persona_runtime=_FakeRuntime(_make_identity()),  # type: ignore[arg-type]
            group_config=_make_config(),
        )
        obligation = ReplyObligation(
            level="must",
            reason="self_addressed",
            source="nickname_original",
            priority=100,
        )
        scheduler.notify(
            "111",
            trigger=TriggerContext(
                reason="有人叫你「emu」",
                mode="at_mention",
                obligation=obligation,
                extra={"addressee_self": False},
            ),
            is_addressed=True,
        )
        await asyncio.sleep(0.1)
        assert len(llm.calls) == 1
        assert llm.calls[0]["force_reply"] is True
        assert llm.calls[0]["must_emit"] is True
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
