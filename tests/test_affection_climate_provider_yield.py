from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from kernel.types import Identity, PluginContext, PromptContext
from plugins.affection.plugin import AffectionPlugin
from services.block_trace.climate_provider import (
    build_climate_turn_snapshot,
    write_climate_turn_snapshot,
)
from services.dialogue_climate.state import ClimateState
from services.humanization import create_humanization_state_bus


class _AffectionEngine:
    def __init__(self) -> None:
        self.build_calls = 0

    def build_affection_block(self, *_args, **_kwargs) -> str:
        self.build_calls += 1
        return "legacy affection block"


@pytest.mark.asyncio
async def test_affection_plugin_yields_only_when_climate_candidate_is_valid() -> None:
    runtime_state = create_humanization_state_bus()
    snapshot = build_climate_turn_snapshot(
        state=ClimateState(familiarity=0.8),
        group_id="100",
        user_id="u1",
        relationship_text="【与当前用户的关系】\n关系不错。",
    )
    write_climate_turn_snapshot(runtime_state, snapshot, session_id="group_100")
    engine = _AffectionEngine()
    plugin = AffectionPlugin()
    await plugin.on_startup(cast(PluginContext, SimpleNamespace(
        affection_engine=engine,
        group_memory_config=None,
        provider_bus=SimpleNamespace(has_provider=lambda name: name == "climate"),
        runtime_state=runtime_state,
    )))
    prompt_ctx = PromptContext(
        session_id="group_100",
        group_id="100",
        user_id="u1",
        identity=Identity(id="test", name="Bot"),
    )

    await plugin.on_pre_prompt(prompt_ctx)

    assert prompt_ctx.blocks == []
    assert engine.build_calls == 0
