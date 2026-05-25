from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from services.block_trace.provider_bus import PromptProviderBus
from services.block_trace.providers import QueryContext
from services.block_trace.sticker_register_provider import StickerRegisterProvider
from services.block_trace.store import BlockTraceStore
from services.humanization import (
    STICKER_RECENT_USED_SLOT,
    create_humanization_state_bus,
    humanization_source,
)
from services.media.sticker_store import StickerStore
from services.system_module import Scope

_JPEG_DATA_A = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"sticker-register-a"
_JPEG_DATA_B = b"\xff\xd8\xff\xe0" + b"\x01" * 64 + b"sticker-register-b"


def _qctx(*, runtime_state=None, group_id: str | None = "100") -> QueryContext:
    return QueryContext(
        request_id="req-sticker-register",
        session_id="group_100" if group_id else "private_u1",
        user_id="u1",
        group_id=group_id,
        conversation_text="这个梗可以配个图",
        runtime_state=runtime_state,
    )


def _scope() -> Scope:
    return Scope(session_id="group_100", group_id="100", user_id="u1")


def _seed_recent(bus, sticker_ids: list[str], *, decay_at: datetime | None = None) -> None:
    bus.set(
        STICKER_RECENT_USED_SLOT,
        {"sticker_ids": sticker_ids, "updated_at": "2026-05-25T12:00:00"},
        scope=_scope(),
        source=humanization_source("sticker_register_provider:test"),
        confidence=1.0,
        decay_at=decay_at,
    )


@pytest.fixture
def sticker_store(tmp_path: Path) -> StickerStore:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    store.add(_JPEG_DATA_A, "刚发过的图", "接梗时用")
    store.add(_JPEG_DATA_B, "备用图", "换图时用")
    return store


@pytest.mark.asyncio
async def test_sticker_register_provider_marks_recent_stickers(
    sticker_store: StickerStore,
) -> None:
    bus = create_humanization_state_bus()
    recent_id = next(iter(sticker_store.list_all()))
    _seed_recent(bus, [recent_id])

    out = await StickerRegisterProvider(store_getter=lambda: sticker_store).provide(_qctx(runtime_state=bus))

    assert len(out) == 1
    block = out[0]
    assert block.provider == "sticker_register_provider"
    assert block.source == "sticker"
    assert block.priority == 47
    assert block.position == "dynamic"
    assert block.evidence_refs == (recent_id,)
    assert "近期已用" in block.text
    assert "建议换一个" in block.text
    assert "其它候选" in block.text


@pytest.mark.asyncio
async def test_sticker_register_provider_skips_without_bus_or_recent() -> None:
    provider = StickerRegisterProvider()

    assert await provider.provide(_qctx(runtime_state=None)) == []
    bus = create_humanization_state_bus()
    assert await provider.provide(_qctx(runtime_state=bus)) == []


@pytest.mark.asyncio
async def test_sticker_register_provider_skips_expired_recent_state() -> None:
    bus = create_humanization_state_bus()
    _seed_recent(bus, ["stk_expired"], decay_at=datetime.now() - timedelta(seconds=1))

    out = await StickerRegisterProvider().provide(_qctx(runtime_state=bus))

    assert out == []


@pytest.mark.asyncio
async def test_sticker_register_provider_bus_active_outputs_prompt_block(
    tmp_path: Path,
) -> None:
    trace_store = BlockTraceStore(db_path=str(tmp_path / "trace.db"))
    await trace_store.init()
    bus_state = create_humanization_state_bus()
    _seed_recent(bus_state, ["stk_recent"])
    provider_bus = PromptProviderBus(trace_store)
    provider_bus.mode = "active"
    provider_bus.register(StickerRegisterProvider())

    try:
        blocks = await provider_bus.run_active(_qctx(runtime_state=bus_state))
    finally:
        await trace_store.close()

    assert len(blocks) == 1
    assert blocks[0].provider == "sticker_register_provider"
    assert blocks[0].source == "sticker"
