from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from kernel.config import GroupConfig
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

    async def chat(self, **kwargs: Any) -> str | None:  # type: ignore[override]
        del kwargs
        self.started += 1
        await self.release.wait()
        self.finished += 1
        return "ok"


class _SlowLLM:
    async def chat(self, **kwargs: Any) -> str | None:  # type: ignore[override]
        del kwargs
        await asyncio.sleep(1.0)
        return "late"


def _scheduler(llm: Any) -> GroupChatScheduler:
    scheduler = GroupChatScheduler(
        llm=llm,
        timeline=GroupTimeline(),
        persona_runtime=_Runtime(),  # type: ignore[arg-type]
        group_config=GroupConfig(talk_value=1.0, planner_smooth=0.0),
    )
    scheduler.set_bot(cast(Any, SimpleNamespace(self_id="1", send_group_msg=AsyncMock())))
    slot = _GroupSlot()
    slot.last_user_id = "u1"
    scheduler._slots["100"] = slot
    return scheduler


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
