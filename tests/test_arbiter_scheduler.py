from __future__ import annotations

import asyncio

import pytest

from kernel.config import ArbiterConfig, GroupConfig
from kernel.types import TriggerContext
from services.llm.arbiter import CompletenessResult, PendingMessage
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot
from services.scheduler import GroupChatScheduler


class _FakeRuntime:
    def __init__(self) -> None:
        self._identity = IdentitySnapshot(id="test", name="测试", personality="测试", proactive="ok")

    def identity_snapshot(self) -> IdentitySnapshot:
        return self._identity


class _FakeLLM:
    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[dict[str, object]] = []
        self._delay = delay

    async def chat(self, **kwargs) -> str | None:  # type: ignore[override]
        self.calls.append(kwargs)
        if self._delay:
            await asyncio.sleep(self._delay)
        return None


class _FakeArbiter:
    def __init__(self, results: list[CompletenessResult]) -> None:
        self._results = list(results)
        self.calls: list[list[object]] = []

    async def judge_completeness(self, pending_messages, *, user_id: str = "", group_id: str | None = None):
        self.calls.append(list(pending_messages))
        if self._results:
            return self._results.pop(0)
        return CompletenessResult(complete=True, confidence=1.0, fallback=True)


def _group_config() -> GroupConfig:
    return GroupConfig(talk_value=1.0, planner_smooth=0, batch_size=100)


def _arbiter_config(**kwargs: object) -> ArbiterConfig:
    payload = ArbiterConfig(enabled=True).model_dump(mode="python")
    payload.update(kwargs)
    payload.setdefault("resolved_api_base", "https://api.deepseek.com")
    payload.setdefault("resolved_api_key", "sk-test")
    payload.setdefault("resolved_model", "deepseek-v4-flash")
    return ArbiterConfig.model_validate(payload)


@pytest.mark.asyncio
async def test_at_message_with_arbiter_enabled_does_not_fire_immediately() -> None:
    llm = _FakeLLM()
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=GroupTimeline(),
        persona_runtime=_FakeRuntime(),  # type: ignore[arg-type]
        group_config=_group_config(),
    )
    scheduler._arbiter_config = _arbiter_config(completeness_poll_interval_s=0.05)  # type: ignore[attr-defined]
    scheduler.set_arbiter(_FakeArbiter([CompletenessResult(complete=False, confidence=0.1)]))  # type: ignore[arg-type]

    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention"),
        user_id="42",
        message_text="别睡",
    )

    slot = scheduler._slots["111"]
    assert slot.running_task is None
    assert slot.arbiter_task is not None
    await scheduler.close()


@pytest.mark.asyncio
async def test_arbiter_completeness_fires_on_complete() -> None:
    llm = _FakeLLM()
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=GroupTimeline(),
        persona_runtime=_FakeRuntime(),  # type: ignore[arg-type]
        group_config=_group_config(),
    )
    scheduler._arbiter_config = _arbiter_config(completeness_poll_interval_s=0.01)  # type: ignore[attr-defined]
    scheduler.set_arbiter(_FakeArbiter([CompletenessResult(complete=True, confidence=0.95)]))  # type: ignore[arg-type]

    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention"),
        user_id="42",
        message_text="别睡",
    )

    await asyncio.sleep(0.12)
    assert len(llm.calls) == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_arbiter_timeout_fires_anyway() -> None:
    llm = _FakeLLM()
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=GroupTimeline(),
        persona_runtime=_FakeRuntime(),  # type: ignore[arg-type]
        group_config=_group_config(),
    )
    scheduler._arbiter_config = _arbiter_config(
        completeness_poll_interval_s=0.01,
        completeness_max_wait_s=0.03,
    )  # type: ignore[attr-defined]
    scheduler.set_arbiter(_FakeArbiter([CompletenessResult(complete=False, confidence=0.2)] * 8))  # type: ignore[arg-type]

    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention"),
        user_id="42",
        message_text="别睡",
    )

    await asyncio.sleep(0.08)
    assert len(llm.calls) == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_arbiter_disabled_fires_immediately() -> None:
    llm = _FakeLLM()
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=GroupTimeline(),
        persona_runtime=_FakeRuntime(),  # type: ignore[arg-type]
        group_config=_group_config(),
    )

    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention"),
        user_id="42",
        message_text="别睡",
    )

    await asyncio.sleep(0.05)
    assert len(llm.calls) == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_burst_pending_accumulates_messages() -> None:
    llm = _FakeLLM()
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=GroupTimeline(),
        persona_runtime=_FakeRuntime(),  # type: ignore[arg-type]
        group_config=_group_config(),
    )
    scheduler._arbiter_config = _arbiter_config(completeness_poll_interval_s=0.05)  # type: ignore[attr-defined]
    scheduler.set_arbiter(_FakeArbiter([CompletenessResult(complete=False, confidence=0.1)] * 6))  # type: ignore[arg-type]

    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention"),
        user_id="42",
        message_text="别睡",
    )
    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention"),
        user_id="42",
        message_text="来烤",
    )

    assert len(scheduler._slots["111"].burst_pending) == 2
    await scheduler.close()


@pytest.mark.asyncio
async def test_running_task_blocks_arbiter() -> None:
    llm = _FakeLLM(delay=0.2)
    scheduler = GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=GroupTimeline(),
        persona_runtime=_FakeRuntime(),  # type: ignore[arg-type]
        group_config=_group_config(),
    )
    scheduler._arbiter_config = _arbiter_config(completeness_poll_interval_s=0.01)  # type: ignore[attr-defined]
    scheduler.set_arbiter(_FakeArbiter([CompletenessResult(complete=True, confidence=1.0)]))  # type: ignore[arg-type]

    scheduler.notify("111", user_id="42")
    await asyncio.sleep(0.05)
    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention"),
        user_id="42",
        message_text="别睡",
    )

    slot = scheduler._slots["111"]
    assert len(slot.pending_during_generation) == 1
    assert slot.pending_during_generation[0].content == "别睡"
    assert slot.arbiter_task is None
    await scheduler.close()


# ---------------------------------------------------------------------------
# Multi-addressee routing (Path Y): a burst that @s the bot from several people
# across different topics must reply once per block, each anchored to its own @
# message — fixing the scalar covering-write where the last @ clobbered the rest.
# ---------------------------------------------------------------------------


def _topic_config():
    from kernel.config import TopicBlockConfig

    return TopicBlockConfig(enabled=True)


def _scheduler_with_topics(llm) -> GroupChatScheduler:
    return GroupChatScheduler(
        llm=llm,  # type: ignore[arg-type]
        timeline=GroupTimeline(),
        persona_runtime=_FakeRuntime(),  # type: ignore[arg-type]
        group_config=_group_config(),
        topic_block_config=_topic_config(),
    )


def test_build_block_triggers_same_topic_merges() -> None:
    """Two @s in the SAME block → one merged trigger, both addressees named."""
    scheduler = _scheduler_with_topics(_FakeLLM())
    pend = [
        PendingMessage(content="一起来", user_id="u1", timestamp=1.0,
                       target_message_id=101, block_id="b1", evidence="at_mention"),
        PendingMessage(content="对啊来嘛", user_id="u2", timestamp=2.0,
                       target_message_id=102, block_id="b1", evidence="at_mention"),
    ]
    triggers = scheduler._build_block_triggers("111", pend)
    assert len(triggers) == 1
    assert triggers[0].extra.get("block_addressees") == ["u1", "u2"]


def test_build_block_triggers_different_topics_split() -> None:
    """@s in DIFFERENT blocks → one trigger each, each anchored to its own @."""
    scheduler = _scheduler_with_topics(_FakeLLM())
    pend = [
        PendingMessage(content="雪人三项", user_id="u1", timestamp=1.0,
                       target_message_id=201, block_id="bA", evidence="at_mention"),
        PendingMessage(content="今晚吃啥", user_id="u2", timestamp=2.0,
                       target_message_id=202, block_id="bB", evidence="at_mention"),
    ]
    triggers = scheduler._build_block_triggers("111", pend)
    assert len(triggers) == 2
    # First-arrival order preserved; each anchors to its own block's @ message.
    assert triggers[0].target_message_id == 201
    assert triggers[1].target_message_id == 202
    # Single-addressee blocks carry no multi-addressee cue.
    assert "block_addressees" not in triggers[0].extra
    assert "block_addressees" not in triggers[1].extra


@pytest.mark.asyncio
async def test_burst_different_topics_fires_per_block_serially() -> None:
    """A burst spanning two topic blocks fires the bot twice, back-to-back,
    each reply quoting its own block's @ message (no last-@ clobber)."""
    llm = _FakeLLM()
    scheduler = _scheduler_with_topics(llm)
    scheduler._arbiter_config = _arbiter_config(completeness_poll_interval_s=0.01)  # type: ignore[attr-defined]
    scheduler.set_arbiter(_FakeArbiter([CompletenessResult(complete=True, confidence=1.0)]))  # type: ignore[arg-type]

    # u1 opens topic A and @s the bot; u2 opens an unrelated topic B and @s the
    # bot 1s later. Distinct speakers + dissimilar text → two blocks.
    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=201, target_user_id="u1"),
        user_id="u1", message_text="姆姆你懂雪人三项吗", message_id=201, at_self=True,
    )
    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=202, target_user_id="u2"),
        user_id="u2", message_text="姆姆今晚火锅还是烧烤", message_id=202, at_self=True,
    )
    await asyncio.sleep(0.15)
    # Both blocks fired (serial, no gap).
    assert len(llm.calls) == 2
    fired_targets = {
        c["trigger"].target_message_id for c in llm.calls if c.get("trigger") is not None
    }
    assert fired_targets == {201, 202}
    await scheduler.close()


@pytest.mark.asyncio
async def test_block_fire_queue_cleared_on_cancel() -> None:
    """D2 cancel-path: if a multi-block fire is cancelled mid-flight, the queued
    remaining blocks must NOT pollute the next run."""
    llm = _FakeLLM(delay=0.3)  # slow chat so we can cancel mid-fire
    scheduler = _scheduler_with_topics(llm)
    scheduler._arbiter_config = _arbiter_config(completeness_poll_interval_s=0.01)  # type: ignore[attr-defined]
    scheduler.set_arbiter(_FakeArbiter([CompletenessResult(complete=True, confidence=1.0)]))  # type: ignore[arg-type]

    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=301, target_user_id="u1"),
        user_id="u1", message_text="姆姆你懂雪人三项吗", message_id=301, at_self=True,
    )
    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=302, target_user_id="u2"),
        user_id="u2", message_text="姆姆今晚火锅还是烧烤", message_id=302, at_self=True,
    )
    await asyncio.sleep(0.08)  # first block fired, queue holds the second
    slot = scheduler._slots["111"]
    assert slot.block_fire_queue  # second block queued
    # Cancel the running fire (simulates shutdown / clear_pending).
    assert slot.running_task is not None
    slot.running_task.cancel()
    await asyncio.sleep(0.05)
    assert slot.block_fire_queue == []  # queue cleared, no pollution
    await scheduler.close()


# ---------------------------------------------------------------------------
# Deterministic cancel-and-remerge (interrupt-merge): a same-block / same-user @
# arriving mid-generation BEFORE the first segment is sent cancels the in-flight
# fire so the burst re-merges into ONE unified reply (the three-"emu" defect).
# No debounce — purely event-driven on the addressee-identity match.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_user_burst_cancels_and_remerges_into_one_reply() -> None:
    """Same user @s three times back-to-back; the first fire is cancelled before
    its first segment and the burst re-merges into a single Arbiter-A fire."""
    llm = _FakeLLM(delay=0.3)  # slow chat: still generating when @#2/#3 arrive
    scheduler = _scheduler_with_topics(llm)
    scheduler._arbiter_config = _arbiter_config(completeness_poll_interval_s=0.01)  # type: ignore[attr-defined]
    scheduler.set_arbiter(  # type: ignore[arg-type]
        _FakeArbiter([
            CompletenessResult(complete=True, confidence=0.95),  # fires on @#1
            CompletenessResult(complete=True, confidence=0.95),  # fires on remerge
        ])
    )

    # @#1 fires (single message judged complete).
    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=501, target_user_id="u1"),
        user_id="u1", message_text="emu", message_id=501, at_self=True,
    )
    await asyncio.sleep(0.12)  # let @#1 fire; running_task now live, no segment yet
    slot = scheduler._slots["111"]
    assert slot.running_task is not None and not slot.running_task.done()
    assert not slot.first_segment_sent
    first_task = slot.running_task

    # @#2 and @#3 from the SAME user mid-generation → cancel-and-remerge.
    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=502, target_user_id="u1"),
        user_id="u1", message_text="emu", message_id=502, at_self=True,
    )
    assert first_task.cancelled() or first_task.cancelling() or len(slot.pending_during_generation) >= 1
    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=503, target_user_id="u1"),
        user_id="u1", message_text="emu", message_id=503, at_self=True,
    )
    # Let the cancel propagate → finally re-merges → Arbiter-A re-fires once.
    await asyncio.sleep(0.5)
    # Exactly two chat() calls total: the cancelled @#1 and the unified remerge.
    # The cancelled one produced no reply; the remerge is the single real reply.
    assert len(llm.calls) == 2
    await scheduler.close()


@pytest.mark.asyncio
async def test_first_segment_sent_blocks_cancel_remerge() -> None:
    """Once the first segment is out ('已发段不撤回'), a same-user @ must NOT
    cancel the running fire — it only queues for the post-emission path."""
    llm = _FakeLLM(delay=0.3)
    scheduler = _scheduler_with_topics(llm)
    scheduler._arbiter_config = _arbiter_config(completeness_poll_interval_s=0.01)  # type: ignore[attr-defined]
    scheduler.set_arbiter(_FakeArbiter([CompletenessResult(complete=True, confidence=0.95)]))  # type: ignore[arg-type]

    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=601, target_user_id="u1"),
        user_id="u1", message_text="emu", message_id=601, at_self=True,
    )
    await asyncio.sleep(0.12)
    slot = scheduler._slots["111"]
    running = slot.running_task
    assert running is not None and not running.done()
    # Simulate the first visible segment having already been sent.
    slot.first_segment_sent = True

    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=602, target_user_id="u1"),
        user_id="u1", message_text="emu", message_id=602, at_self=True,
    )
    # Guard held: the running fire is NOT cancelled; the @ only queues.
    assert not running.cancelled()
    assert len(slot.pending_during_generation) == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_different_block_burst_does_not_cancel() -> None:
    """A mid-generation @ in a DIFFERENT block from a DIFFERENT user must not
    cancel — it's a separate addressee, handled by the finally re-fire path."""
    llm = _FakeLLM(delay=0.3)
    scheduler = _scheduler_with_topics(llm)
    scheduler._arbiter_config = _arbiter_config(completeness_poll_interval_s=0.01)  # type: ignore[attr-defined]
    scheduler.set_arbiter(_FakeArbiter([CompletenessResult(complete=True, confidence=0.95)]))  # type: ignore[arg-type]

    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=701, target_user_id="u1"),
        user_id="u1", message_text="姆姆你懂雪人三项吗", message_id=701, at_self=True,
    )
    await asyncio.sleep(0.12)
    slot = scheduler._slots["111"]
    running = slot.running_task
    assert running is not None and not running.done()

    scheduler.notify(
        "111",
        trigger=TriggerContext(reason="有人@了你", mode="at_mention", target_message_id=702, target_user_id="u2"),
        user_id="u2", message_text="姆姆今晚火锅还是烧烤", message_id=702, at_self=True,
    )
    # Different addressee → no cancel; just queued for the finally re-fire.
    assert not running.cancelled()
    assert len(slot.pending_during_generation) == 1
    await scheduler.close()
