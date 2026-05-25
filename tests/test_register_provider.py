from __future__ import annotations

import pytest

from services.block_trace.providers import QueryContext
from services.block_trace.register_provider import RegisterProvider
from services.humanization import (
    AFFECTION_FAMILIARITY_SLOT,
    CLOCK_CURRENT_SLOT,
    REGISTER_LABEL_SLOT,
    create_humanization_state_bus,
    humanization_source,
)
from services.system_module import Scope


def _ctx(*, runtime_state=None, turn_id: str = "turn-1", group_id: str | None = "100") -> QueryContext:
    return QueryContext(
        request_id="req-register",
        session_id="group_100" if group_id else "private_100",
        user_id="u1",
        group_id=group_id,
        conversation_text="大家在轻松接梗",
        runtime_state=runtime_state,
        turn_id=turn_id,
    )


def _seed_register_state(bus, *, label: str = "playful", turn_id: str = "turn-1") -> None:
    bus.set(
        REGISTER_LABEL_SLOT,
        {"label": label, "confidence": 0.82},
        scope=Scope(session_id="group_100", group_id="100", user_id="u1"),
        source=humanization_source("register_provider:test"),
        confidence=0.82,
    )
    bus.set(
        AFFECTION_FAMILIARITY_SLOT,
        {"user_id": "u1", "familiarity": 0.35, "tier": "熟人"},
        scope=Scope(user_id="u1"),
        source=humanization_source("register_provider:test"),
        confidence=1.0,
    )
    bus.set(
        CLOCK_CURRENT_SLOT,
        {
            "date": "2026-05-25",
            "hour": 21,
            "minute": 0,
            "weekday": 0,
            "weekday_cn": "周一",
            "is_weekend": False,
            "is_holiday": False,
            "slot_time": "21:00",
            "slot_activity": "晚自习后聊天",
            "slot_mood_hint": "放松",
        },
        scope=Scope(session_id="group_100", group_id="100", user_id="u1", turn_id=turn_id),
        source=humanization_source("register_provider:test"),
        confidence=1.0,
    )


@pytest.mark.asyncio
async def test_register_provider_reads_runtime_state_and_emits_stable_block() -> None:
    bus = create_humanization_state_bus()
    _seed_register_state(bus)

    out = await RegisterProvider().provide(_ctx(runtime_state=bus))

    assert len(out) == 1
    block = out[0]
    assert block.provider == "register_provider"
    assert block.position == "stable"
    assert block.priority == 43
    assert block.metadata["target_register"] == "playful_light"
    assert "本轮语域目标：playful_light" in block.text
    assert "不要为了显得活泼而堆口头禅" in block.text
    assert "晚自习后聊天" in block.text


@pytest.mark.asyncio
async def test_register_provider_degrades_without_runtime_state() -> None:
    out = await RegisterProvider().provide(_ctx(runtime_state=None))

    assert len(out) == 1
    assert out[0].metadata["target_register"] == "neutral_daily"
    assert "不照搬任何固定人设句" in out[0].text


@pytest.mark.asyncio
async def test_register_provider_affection_can_select_close_register() -> None:
    bus = create_humanization_state_bus()
    _seed_register_state(bus, label="neutral")
    bus.set(
        AFFECTION_FAMILIARITY_SLOT,
        {"user_id": "u1", "familiarity": 0.72, "tier": "好朋友"},
        scope=Scope(user_id="u1"),
        source=humanization_source("register_provider:test"),
        confidence=1.0,
    )

    out = await RegisterProvider().provide(_ctx(runtime_state=bus))

    assert out[0].metadata["target_register"] == "casual_close"
    assert "允许自然偏爱" in out[0].text


@pytest.mark.asyncio
async def test_register_provider_requires_matching_turn_for_clock_state() -> None:
    bus = create_humanization_state_bus()
    _seed_register_state(bus, label="neutral", turn_id="turn-1")

    out = await RegisterProvider().provide(_ctx(runtime_state=bus, turn_id="turn-2"))

    assert out[0].metadata["target_register"] == "neutral_daily"
    assert "晚自习后聊天" not in out[0].text


@pytest.mark.asyncio
async def test_register_provider_handles_private_scope() -> None:
    out = await RegisterProvider().provide(_ctx(group_id=None))

    assert len(out) == 1
    assert out[0].scope == "session"
    assert out[0].group_id == ""
