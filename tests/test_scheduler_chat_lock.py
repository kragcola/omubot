from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from kernel.config import GroupConfig
from kernel.types import TriggerContext
from services.block_trace.climate_provider import (
    build_climate_turn_snapshot,
    write_climate_turn_snapshot,
)
from services.dialogue_climate.state import ClimateState
from services.humanization import create_humanization_state_bus
from services.llm.client import RateLimitError
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot
from services.scheduler import GroupChatScheduler, _GroupSlot


class _Runtime:
    def identity_snapshot(self) -> IdentitySnapshot:
        return IdentitySnapshot(id="bot", name="bot", personality="p", proactive="on")


class _BlockingLLM:
    def __init__(self) -> None:
        self.started = 0
        self.finished = 0
        self.release = asyncio.Event()
        self.contexts: list[Any] = []

    async def chat(self, **kwargs: Any) -> str | None:  # type: ignore[override]
        self.contexts.append(kwargs["ctx"])
        self.started += 1
        await self.release.wait()
        self.finished += 1
        return "ok"


class _SlowLLM:
    async def chat(self, **kwargs: Any) -> str | None:  # type: ignore[override]
        del kwargs
        await asyncio.sleep(1.0)
        return "late"


class _CapturingLLM:
    def __init__(self) -> None:
        self.contexts: list[Any] = []

    async def chat(self, **kwargs: Any) -> str | None:  # type: ignore[override]
        self.contexts.append(kwargs["ctx"])
        return None


class _EmittingLLM:
    def __init__(self) -> None:
        self.contexts: list[Any] = []

    async def chat(self, **kwargs: Any) -> str | None:  # type: ignore[override]
        self.contexts.append(kwargs["ctx"])
        await kwargs["on_segment"]("hello")
        return None


class _ReturningLLM:
    async def chat(self, **kwargs: Any) -> str | None:  # type: ignore[override]
        del kwargs
        return "hello"


class _HumanizerSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def delay(self, text: str, **kwargs: Any) -> None:
        self.calls.append({"text": text, **kwargs})


class _MetricStore:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def record_runtime_metric(self, **kwargs: Any) -> str:
        self.events.append(kwargs)
        return "metric-1"


class _RateLimitThenSkipLLM:
    def __init__(self, on_rate_limit: Any) -> None:
        self._on_rate_limit = on_rate_limit
        self.calls: list[tuple[str, str]] = []

    async def chat(self, **kwargs: Any) -> str | None:  # type: ignore[override]
        self.calls.append((kwargs["user_id"], kwargs["ctx"].user_id))
        if len(self.calls) == 1:
            self._on_rate_limit()
            raise RateLimitError("retry")
        return None


class _BlockingMetricStore:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def record_runtime_metric(self, **kwargs: Any) -> str:
        del kwargs
        self.started.set()
        await self.release.wait()
        return "metric-1"


def _scheduler(
    llm: Any,
    *,
    humanizer: Any = None,
    runtime_state: Any = None,
) -> GroupChatScheduler:
    scheduler = GroupChatScheduler(
        llm=llm,
        timeline=GroupTimeline(),
        persona_runtime=_Runtime(),  # type: ignore[arg-type]
        group_config=GroupConfig(talk_value=1.0, planner_smooth=0.0),
        humanizer=humanizer,
        runtime_state=runtime_state,
    )
    scheduler.set_bot(cast(Any, SimpleNamespace(self_id="1", send_group_msg=AsyncMock())))
    slot = _GroupSlot()
    slot.last_user_id = "u1"
    scheduler._slots["100"] = slot
    return scheduler


def _climate_runtime_state() -> Any:
    bus = create_humanization_state_bus()
    snapshot = build_climate_turn_snapshot(
        state=ClimateState(tension=0.7),
        group_id="100",
        user_id="u1",
    )
    write_climate_turn_snapshot(bus, snapshot, session_id="group_100")
    return bus


@pytest.mark.asyncio
async def test_chat_lock_serializes_same_group_calls() -> None:
    llm = _BlockingLLM()
    scheduler = _scheduler(llm)
    slot = scheduler._slots["100"]
    task1 = asyncio.create_task(scheduler._do_chat("100"))
    await asyncio.sleep(0.01)
    task2 = asyncio.create_task(scheduler._do_chat("100"))
    await asyncio.sleep(0.05)

    assert llm.started == 1
    llm.release.set()
    await asyncio.gather(task1, task2)
    assert llm.started == 2
    assert llm.finished == 2
    assert not slot.chat_lock.locked()
    await scheduler.close()


@pytest.mark.asyncio
async def test_chat_lock_cancel_path_releases_lock() -> None:
    llm = _BlockingLLM()
    scheduler = _scheduler(llm)
    slot = scheduler._slots["100"]

    task = asyncio.create_task(scheduler._do_chat("100"))
    await asyncio.sleep(0.05)
    assert llm.started == 1

    task.cancel()
    await task

    assert not slot.chat_lock.locked()
    llm.release.set()
    await scheduler._do_chat("100")
    assert llm.started == 2
    await scheduler.close()


@pytest.mark.asyncio
async def test_chat_lock_timeout_releases_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.scheduler._CHAT_LOCK_LLM_TIMEOUT_S", 0.01)
    scheduler = _scheduler(_SlowLLM())
    slot = scheduler._slots["100"]

    await scheduler._do_chat("100")

    assert not slot.chat_lock.locked()
    await scheduler.close()


@pytest.mark.asyncio
async def test_do_chat_attaches_proactive_reply_run() -> None:
    llm = _CapturingLLM()
    scheduler = _scheduler(llm)

    await scheduler._do_chat("100")

    assert len(llm.contexts) == 1
    assert "reply_run" in llm.contexts[0].extra
    run = llm.contexts[0].extra["reply_run"]
    assert run.origin == "proactive"
    assert run.group_id == "100"
    assert run.user_id == "u1"
    assert run.outcome == "skipped"
    assert [record.stage for record in run.records] == [
        "input",
        "decision",
        "generation_started",
        "generation_finished",
        "skipped",
    ]
    await scheduler.close()


@pytest.mark.asyncio
async def test_do_chat_attaches_triggered_reply_run() -> None:
    llm = _CapturingLLM()
    scheduler = _scheduler(llm)

    await scheduler._do_chat("100", trigger=TriggerContext(mode="at_mention"))

    run = llm.contexts[0].extra["reply_run"]
    assert run.origin == "triggered"
    assert run.trigger_mode == "at_mention"
    assert run.outcome == "skipped"
    await scheduler.close()


@pytest.mark.asyncio
async def test_cancelled_reply_run_records_sanitized_terminal_metric() -> None:
    llm = _BlockingLLM()
    scheduler = _scheduler(llm)
    metric_store = _MetricStore()
    scheduler._block_trace_store = metric_store

    task = asyncio.create_task(scheduler._do_chat("100"))
    await asyncio.sleep(0.05)
    task.cancel()
    await task

    run = llm.contexts[0].extra["reply_run"]
    assert run.outcome == "cancelled"
    assert run.records[-1].stage == "cancelled"
    assert len(metric_store.events) == 1
    event = metric_store.events[0]
    assert event["metric_key"] == "reply_run"
    metadata = event["metadata"]
    assert metadata["outcome"] == "cancelled"
    assert not ({"text", "content", "reply", "thought"} & set(metadata))
    await scheduler.close()


@pytest.mark.asyncio
async def test_rate_limit_retry_refreshes_latest_user_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler_ref: list[GroupChatScheduler] = []

    def update_user() -> None:
        scheduler_ref[0]._slots["100"].last_user_id = "u2"

    llm = _RateLimitThenSkipLLM(update_user)
    scheduler = _scheduler(llm)
    scheduler_ref.append(scheduler)
    monkeypatch.setattr("services.scheduler.RATE_LIMIT_BASE_DELAY", 0.0)

    await scheduler._do_chat("100")

    assert llm.calls == [("u1", "u1"), ("u2", "u2")]
    await scheduler.close()


@pytest.mark.asyncio
async def test_blocked_reply_run_metric_cannot_hold_slot_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _BlockingLLM()
    scheduler = _scheduler(llm)
    slot = scheduler._slots["100"]
    metric_store = _BlockingMetricStore()
    scheduler._block_trace_store = metric_store
    monkeypatch.setattr(
        "services.scheduler._REPLY_RUN_METRIC_TIMEOUT_S",
        0.01,
        raising=False,
    )
    task = asyncio.create_task(scheduler._do_chat("100"))
    slot.running_task = task
    await asyncio.sleep(0.01)
    task.cancel()
    await metric_store.started.wait()

    try:
        assert slot.running_task is None
        done, _ = await asyncio.wait({task}, timeout=0.1)
        assert task in done
    finally:
        metric_store.release.set()
        await task
        await scheduler.close()


@pytest.mark.asyncio
async def test_muted_segment_without_send_is_not_marked_delivered() -> None:
    llm = _EmittingLLM()
    scheduler = _scheduler(llm)
    bot = SimpleNamespace(self_id="1", send_group_msg=AsyncMock())
    scheduler.set_bot(cast(Any, bot))
    scheduler.mute("100")

    await scheduler._do_chat("100")

    bot.send_group_msg.assert_not_awaited()
    run = llm.contexts[0].extra["reply_run"]
    assert run.delivered_segments == 0
    assert run.outcome != "completed"
    await scheduler.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("llm", [_EmittingLLM(), _ReturningLLM()])
async def test_do_chat_first_or_only_segment_consumes_climate_humanizer_policy(llm: Any) -> None:
    humanizer = _HumanizerSpy()
    scheduler = _scheduler(
        llm,
        humanizer=humanizer,
        runtime_state=_climate_runtime_state(),
    )

    await scheduler._do_chat("100")

    assert len(humanizer.calls) == 1
    assert humanizer.calls[0]["climate"]["delay_multiplier"] == pytest.approx(0.85)
    assert humanizer.calls[0]["thinking_elapsed_s"] >= 0.0
    await scheduler.close()
