from __future__ import annotations

import pytest

from services.block_trace.catchphrase_provider import CatchphraseProvider
from services.block_trace.provider_bus import PromptProviderBus
from services.block_trace.providers import QueryContext
from services.block_trace.store import BlockTraceStore
from services.humanization import (
    REGISTER_LABEL_SLOT,
    REGISTER_RECENT_USED_SLOT,
    create_humanization_state_bus,
    humanization_source,
)
from services.learning_normalizer import LearningNormalizerStore
from services.system_module import Scope


@pytest.fixture
async def normalizer(tmp_path) -> LearningNormalizerStore:
    store = LearningNormalizerStore(tmp_path / "learning_normalizer.db")
    await store.init()
    yield store
    await store.close()


def _ctx(*, runtime_state=None, group_id: str | None = "100") -> QueryContext:
    return QueryContext(
        request_id="req-catchphrase",
        session_id="group_100" if group_id else "private_100",
        user_id="u1",
        group_id=group_id,
        conversation_text="大家在轻松聊天",
        runtime_state=runtime_state,
    )


async def _seed(
    store: LearningNormalizerStore,
    text: str,
    source_id: str,
    *,
    group_id: str = "100",
):
    return await store.attach_candidate(
        domain="catchphrase",
        scope="group",
        group_id=group_id,
        raw_text=text,
        source_table="catchphrase_pool",
        source_id=source_id,
    )


@pytest.mark.asyncio
async def test_catchphrase_provider_reads_normalizer_clusters(normalizer: LearningNormalizerStore) -> None:
    first = await _seed(normalizer, "坏了坏了", "c1")
    await _seed(normalizer, "这下舒服了", "c2")
    bus = create_humanization_state_bus()

    out = await CatchphraseProvider(lambda: normalizer).provide(_ctx(runtime_state=bus))

    assert len(out) == 1
    block = out[0]
    assert block.provider == "catchphrase_provider"
    assert block.position == "dynamic"
    assert block.priority == 46
    assert "坏了坏了" in block.text
    assert "不要为了显得有梗而硬套" in block.text
    assert first.cluster_id in block.evidence_refs
    recent = bus.get(
        REGISTER_RECENT_USED_SLOT,
        scope=Scope(session_id="group_100", group_id="100", user_id="u1"),
    )
    assert recent is not None
    assert first.cluster_id in recent.value["catchphrase_cluster_ids"]


@pytest.mark.asyncio
async def test_catchphrase_provider_skips_without_store_or_group(normalizer: LearningNormalizerStore) -> None:
    await _seed(normalizer, "坏了坏了", "c1")

    assert await CatchphraseProvider(lambda: None).provide(_ctx()) == []
    assert await CatchphraseProvider(lambda: normalizer).provide(_ctx(group_id=None)) == []


@pytest.mark.asyncio
async def test_catchphrase_provider_suppresses_recent_cluster(normalizer: LearningNormalizerStore) -> None:
    first = await _seed(normalizer, "坏了坏了", "c1")
    second = await _seed(normalizer, "这下舒服了", "c2")
    bus = create_humanization_state_bus()
    bus.set(
        REGISTER_RECENT_USED_SLOT,
        {"catchphrase_cluster_ids": [first.cluster_id]},
        scope=Scope(session_id="group_100", group_id="100", user_id="u1"),
        source=humanization_source("catchphrase:test"),
        confidence=1.0,
    )

    out = await CatchphraseProvider(lambda: normalizer, max_items=1).provide(_ctx(runtime_state=bus))

    assert len(out) == 1
    assert first.cluster_id not in out[0].evidence_refs
    assert second.cluster_id in out[0].evidence_refs
    assert "这下舒服了" in out[0].text


@pytest.mark.asyncio
async def test_catchphrase_provider_serious_register_is_conservative(normalizer: LearningNormalizerStore) -> None:
    await _seed(normalizer, "坏了坏了", "c1")
    await _seed(normalizer, "这下舒服了", "c2")
    bus = create_humanization_state_bus()
    bus.set(
        REGISTER_LABEL_SLOT,
        {"label": "serious", "confidence": 0.9},
        scope=Scope(session_id="group_100", group_id="100", user_id="u1"),
        source=humanization_source("catchphrase:test"),
        confidence=0.9,
    )

    out = await CatchphraseProvider(lambda: normalizer, max_items=2).provide(_ctx(runtime_state=bus))

    assert len(out) == 1
    assert out[0].metadata["catchphrase_count"] == 1
    assert "当前语域偏克制" in out[0].text


@pytest.mark.asyncio
async def test_catchphrase_provider_bus_active_returns_prompt_block(
    normalizer: LearningNormalizerStore,
    tmp_path,
) -> None:
    await _seed(normalizer, "坏了坏了", "c1")
    trace_store = BlockTraceStore(tmp_path / "trace.db")
    await trace_store.init()
    try:
        bus = PromptProviderBus(trace_store)
        bus.mode = "active"
        bus.register(CatchphraseProvider(lambda: normalizer))

        blocks = await bus.run_active(_ctx())
    finally:
        await trace_store.close()

    assert len(blocks) == 1
    assert blocks[0].provider == "catchphrase_provider"
    assert blocks[0].source == "slang"
