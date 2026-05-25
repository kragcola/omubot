from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from kernel.types import PluginContext
from services.humanization import (
    HUMANIZATION_CONTRACT,
    HUMANIZATION_MODULE_ID,
    LAST_METRICS_SLOT,
    REGISTER_LABEL_SLOT,
    REGISTER_RECENT_USED_SLOT,
    STICKER_RECENT_USED_SLOT,
    create_humanization_state_bus,
    humanization_source,
)
from services.system_module import ModuleContract, Scope, SourceRef, StateSlotDefinition, StateSlotOwnershipError


def _scope(turn_id: str = "t1") -> Scope:
    return Scope(session_id="group_100", group_id="100", user_id="u1", turn_id=turn_id)


def test_humanization_contract_owns_expected_slots_and_context_accepts_bus() -> None:
    bus = create_humanization_state_bus()
    ctx = PluginContext(runtime_state=bus, humanization_contract=HUMANIZATION_CONTRACT)

    assert HUMANIZATION_CONTRACT.id == HUMANIZATION_MODULE_ID
    assert bus.owners[REGISTER_LABEL_SLOT] == HUMANIZATION_MODULE_ID
    assert bus.owners[REGISTER_RECENT_USED_SLOT] == HUMANIZATION_MODULE_ID
    assert bus.owners[STICKER_RECENT_USED_SLOT] == HUMANIZATION_MODULE_ID
    assert bus.owners[LAST_METRICS_SLOT] == HUMANIZATION_MODULE_ID
    assert ctx.runtime_state is bus
    assert ctx.humanization_contract is HUMANIZATION_CONTRACT


def test_humanization_state_bus_enforces_owner() -> None:
    bus = create_humanization_state_bus()

    bus.set(
        REGISTER_LABEL_SLOT,
        {"label": "quiet"},
        scope=_scope(),
        source=humanization_source("register_classifier:test"),
        confidence=0.8,
    )
    snapshot = bus.get(REGISTER_LABEL_SLOT, scope=_scope())
    assert snapshot is not None
    assert snapshot.value == {"label": "quiet"}

    with pytest.raises(StateSlotOwnershipError):
        bus.set(
            REGISTER_LABEL_SLOT,
            {"label": "bad"},
            scope=_scope(),
            source=SourceRef("runtime.someone_else", "register_classifier:test"),
            confidence=0.8,
        )


def test_humanization_state_bus_clears_expired_slots() -> None:
    bus = create_humanization_state_bus()
    past = datetime.now() - timedelta(seconds=1)

    bus.set(
        REGISTER_RECENT_USED_SLOT,
        {"phrases": ["坏了"]},
        scope=_scope(),
        source=humanization_source("catchphrase:test"),
        confidence=1.0,
        decay_at=past,
    )

    assert bus.get(REGISTER_RECENT_USED_SLOT, scope=_scope()) is None


@pytest.mark.asyncio
async def test_humanization_state_cancel_path_does_not_dirty_write() -> None:
    bus = create_humanization_state_bus()

    async def delayed_write() -> None:
        await asyncio.sleep(60)
        bus.set(  # pragma: no cover - must remain unreachable
            REGISTER_LABEL_SLOT,
            {"label": "playful"},
            scope=_scope(),
            source=humanization_source("cancelled:test"),
            confidence=1.0,
        )

    task = asyncio.create_task(delayed_write())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert bus.get(REGISTER_LABEL_SLOT, scope=_scope()) is None


def test_humanization_state_per_turn_slots_are_cleared_by_turn_scope() -> None:
    bus = create_humanization_state_bus()
    turn_scope = _scope("t1")
    other_turn = _scope("t2")

    bus.set(
        LAST_METRICS_SLOT,
        {"score": 0.72},
        scope=turn_scope,
        source=humanization_source("scorer:test"),
        confidence=0.9,
    )
    bus.set(
        LAST_METRICS_SLOT,
        {"score": 0.88},
        scope=other_turn,
        source=humanization_source("scorer:test"),
        confidence=0.9,
    )

    bus.clear_per_turn(scope=turn_scope)

    assert bus.get(LAST_METRICS_SLOT, scope=turn_scope) is None
    assert bus.get(LAST_METRICS_SLOT, scope=other_turn) is not None


def test_humanization_state_bus_rejects_multiple_owners() -> None:
    duplicate_owner = ModuleContract(
        id="runtime.duplicate_humanization",
        group="runtime",
        state_owns=(
            StateSlotDefinition(
                id=REGISTER_LABEL_SLOT,
                schema="omubot.state.duplicate_register.v1",
            ),
        ),
    )

    with pytest.raises(StateSlotOwnershipError):
        create_humanization_state_bus(extra_contracts=(duplicate_owner,))
