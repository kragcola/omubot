from __future__ import annotations

import pytest

from services.block_trace.provider_bus import PromptProviderBus
from services.block_trace.providers import QueryContext
from services.block_trace.store import BlockTraceStore
from services.block_trace.thinker_provider import ThinkerProvider
from services.humanization import (
    THINKER_LAST_DECISION_SLOT,
    create_humanization_state_bus,
    humanization_source,
)
from services.system_module import Scope


def _qctx(*, runtime_state=None, turn_id: str = "turn-1") -> QueryContext:
    return QueryContext(
        request_id="req-thinker-provider",
        session_id="group_100",
        user_id="u1",
        group_id="100",
        conversation_text="接一下这个话题",
        runtime_state=runtime_state,
        turn_id=turn_id,
    )


def _seed_decision(bus, *, action: str = "reply", turn_id: str = "turn-1") -> None:
    bus.set(
        THINKER_LAST_DECISION_SLOT,
        {
            "action": action,
            "thought": "顺着对方的问题轻轻接一下",
            "topic_intent_label": "关心",
            "retrieve_mode": "skip",
            "rewritten_query": "",
            "sticker": False,
            "tone": "日常",
            "usage": {},
        },
        scope=Scope(session_id="group_100", group_id="100", user_id="u1", turn_id=turn_id),
        source=humanization_source("thinker_provider:test"),
        confidence=1.0,
    )


@pytest.mark.asyncio
async def test_thinker_provider_reads_per_turn_state_and_emits_block() -> None:
    bus = create_humanization_state_bus()
    _seed_decision(bus)

    out = await ThinkerProvider().provide(_qctx(runtime_state=bus))

    assert len(out) == 1
    block = out[0]
    assert block.provider == "thinker_provider"
    assert block.position == "dynamic"
    assert block.priority == 48
    assert block.metadata["retrieve_mode"] == "skip"
    assert block.metadata["topic_intent_label"] == "关心"
    assert "本轮回复意图" in block.text
    assert "不要把这些标签原样写给用户" in block.text
    assert "意图标签：关心" in block.text


@pytest.mark.asyncio
async def test_thinker_provider_skips_without_runtime_state_or_turn_id() -> None:
    provider = ThinkerProvider()

    assert await provider.provide(_qctx(runtime_state=None)) == []
    bus = create_humanization_state_bus()
    _seed_decision(bus)
    assert await provider.provide(_qctx(runtime_state=bus, turn_id="")) == []


@pytest.mark.asyncio
async def test_thinker_provider_requires_matching_turn_scope() -> None:
    bus = create_humanization_state_bus()
    _seed_decision(bus, turn_id="turn-1")

    assert await ThinkerProvider().provide(_qctx(runtime_state=bus, turn_id="turn-2")) == []


@pytest.mark.asyncio
async def test_thinker_provider_skips_non_reply_action() -> None:
    bus = create_humanization_state_bus()
    _seed_decision(bus, action="wait")

    assert await ThinkerProvider().provide(_qctx(runtime_state=bus)) == []


@pytest.mark.asyncio
async def test_thinker_provider_bus_active_outputs_prompt_block(tmp_path) -> None:
    trace_store = BlockTraceStore(db_path=str(tmp_path / "trace.db"))
    await trace_store.init()
    bus_state = create_humanization_state_bus()
    _seed_decision(bus_state)
    provider_bus = PromptProviderBus(trace_store)
    provider_bus.mode = "active"
    provider_bus.register(ThinkerProvider())

    try:
        blocks = await provider_bus.run_active(_qctx(runtime_state=bus_state))
    finally:
        await trace_store.close()

    assert len(blocks) == 1
    assert blocks[0].provider == "thinker_provider"
    assert blocks[0].source == "context"
