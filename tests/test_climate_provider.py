from __future__ import annotations

import importlib

import pytest

from services.block_trace.providers import QueryContext
from services.dialogue_climate.state import ClimateState
from services.humanization import create_humanization_state_bus


def _module():
    try:
        return importlib.import_module("services.block_trace.climate_provider")
    except ModuleNotFoundError:
        return None


def _query(*, runtime_state, user_id: str = "u1") -> QueryContext:
    return QueryContext(
        request_id="req-1",
        session_id="group_100",
        user_id=user_id,
        group_id="100",
        conversation_text="最近的对话",
        runtime_state=runtime_state,
    )


@pytest.mark.asyncio
async def test_climate_provider_emits_one_merged_relationship_and_policy_candidate() -> None:
    module = _module()
    assert module is not None, "ClimateProvider module must exist"
    bus = create_humanization_state_bus()
    snapshot = module.build_climate_turn_snapshot(
        state=ClimateState(tension=0.7, familiarity=0.8),
        group_id="100",
        user_id="u1",
        relationship_text="【与当前用户的关系】\n公开场合关系不错。",
    )
    module.write_climate_turn_snapshot(bus, snapshot, session_id="group_100")

    blocks = await module.ClimateProvider().provide(_query(runtime_state=bus))

    assert len(blocks) == 1
    block = blocks[0]
    assert block.provider == "climate_provider"
    assert block.label == "当前关系与对话气候"
    assert "公开场合关系不错" in block.text
    assert "刚被连续打扰" in block.text
    assert block.metadata["reply_bias"] == "short"
    assert block.metadata["delay_multiplier"] == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_climate_provider_rejects_stale_snapshot_for_another_user() -> None:
    module = _module()
    assert module is not None, "ClimateProvider module must exist"
    bus = create_humanization_state_bus()
    snapshot = module.build_climate_turn_snapshot(
        state=ClimateState(familiarity=0.8),
        group_id="100",
        user_id="u1",
        relationship_text="关系不错。",
    )
    module.write_climate_turn_snapshot(bus, snapshot, session_id="group_100")

    blocks = await module.ClimateProvider().provide(
        _query(runtime_state=bus, user_id="u2"),
    )

    assert blocks == []
