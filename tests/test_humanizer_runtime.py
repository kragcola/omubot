from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from kernel.config import GroupConfig
from services.humanization import (
    CLOCK_CURRENT_SLOT,
    REGISTER_LABEL_SLOT,
    create_humanization_state_bus,
    humanization_source,
)
from services.identity import Identity
from services.memory.timeline import GroupTimeline
from services.scheduler import GroupChatScheduler, _GroupSlot
from services.system_module import Scope


class _IdentityMgr:
    def resolve(self) -> Identity:
        return Identity(id="test", name="测试", personality="测试人设", proactive="积极")


class _HumanizerSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def delay(self, text: str, **kwargs) -> None:
        self.calls.append({"text": text, **kwargs})


def _scheduler(
    *,
    humanizer: _HumanizerSpy,
    runtime_state=None,
    mood_getter=None,
    ) -> GroupChatScheduler:
    scheduler = GroupChatScheduler(
        llm=cast(Any, SimpleNamespace()),
        timeline=GroupTimeline(),
        identity_mgr=cast(Any, _IdentityMgr()),
        group_config=GroupConfig(talk_value=1.0, planner_smooth=0),
        humanizer=humanizer,
        runtime_state=runtime_state,
        mood_getter=mood_getter,
    )
    scheduler.set_bot(cast(Any, SimpleNamespace(send_group_msg=AsyncMock())))
    slot = _GroupSlot()
    slot.last_user_id = "u1"
    scheduler._slots["100"] = slot
    return scheduler


def _seed_runtime_state(bus) -> None:
    bus.set(
        REGISTER_LABEL_SLOT,
        {"label": "quiet", "confidence": 0.88},
        scope=Scope(session_id="group_100", group_id="100", user_id="u1"),
        source=humanization_source("humanizer_runtime:test"),
        confidence=0.88,
    )
    bus.set(
        CLOCK_CURRENT_SLOT,
        {"slot_activity": "晚自习后", "slot_mood_hint": "疲惫", "energy": 0.2},
        scope=Scope(session_id="group_100", group_id="100", user_id="u1"),
        source=humanization_source("humanizer_runtime:test"),
        confidence=1.0,
    )


@pytest.mark.asyncio
async def test_scheduler_passes_runtime_register_mood_and_slot_to_humanizer() -> None:
    bus = create_humanization_state_bus()
    _seed_runtime_state(bus)
    humanizer = _HumanizerSpy()
    mood = SimpleNamespace(energy=0.3, valence=-0.2, openness=0.4)
    seen: list[tuple[str | None, str]] = []

    def mood_getter(*, group_id: str | None = None, session_id: str = ""):
        seen.append((group_id, session_id))
        return mood

    scheduler = _scheduler(humanizer=humanizer, runtime_state=bus, mood_getter=mood_getter)

    await scheduler._send_to_group("100", "我慢点回。")

    assert seen == [("100", "group_100")]
    assert humanizer.calls == [
        {
            "text": "我慢点回。",
            "group_id": "100",
            "register": {"label": "quiet", "confidence": 0.88},
            "slot": {"slot_activity": "晚自习后", "slot_mood_hint": "疲惫", "energy": 0.2},
            "mood": mood,
        }
    ]


@pytest.mark.asyncio
async def test_scheduler_degrades_without_runtime_state() -> None:
    humanizer = _HumanizerSpy()
    scheduler = _scheduler(humanizer=humanizer, runtime_state=None)

    await scheduler._send_to_group("100", "照旧发送。")

    assert humanizer.calls == [
        {
            "text": "照旧发送。",
            "group_id": "100",
            "register": None,
            "slot": None,
            "mood": None,
        }
    ]


@pytest.mark.asyncio
async def test_scheduler_humanize_skip_does_not_call_humanizer() -> None:
    humanizer = _HumanizerSpy()
    scheduler = _scheduler(humanizer=humanizer, runtime_state=None)

    await scheduler._send_to_group("100", "第一段跳过延迟。", humanize="skip")

    assert humanizer.calls == []
