from __future__ import annotations

import asyncio

import pytest

from kernel.config import ArbiterConfig, GroupConfig
from kernel.types import TriggerContext
from services.llm.arbiter import CompletenessResult
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
