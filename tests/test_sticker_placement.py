from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from services.humanization import (
    STICKER_RECENT_USED_SLOT,
    create_humanization_state_bus,
    humanization_source,
)
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.media.sticker_store import StickerStore
from services.memory.short_term import ShortTermMemory
from services.persona import PersonaRuntime
from services.tools.context import ToolContext
from services.tools.registry import ToolRegistry
from services.tools.sticker_tools import SendStickerTool

_JPEG_DATA = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"jpeg-payload-a"


async def _client(tmp_path) -> tuple[LLMClient, str, MagicMock]:
    runtime_state = create_humanization_state_bus()
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    sticker_id, _ = store.add(_JPEG_DATA, "笑哭", "开心接梗时发")
    bot = MagicMock()
    bot.send_group_msg = AsyncMock()
    tools = ToolRegistry()
    tools.register(SendStickerTool(store, runtime_state=runtime_state))
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=PersonaRuntime()),
        short_term=ShortTermMemory(),
        tools=tools,
        thinker_enabled=False,
        runtime_state=runtime_state,
        sticker_placement_config=SimpleNamespace(enabled=True, cooldown_ms=45_000),
    )
    return client, sticker_id, bot


async def test_post_reply_sticker_sends_when_thinker_requests_and_tool_not_used(tmp_path) -> None:
    client, _sticker_id, bot = await _client(tmp_path)
    try:
        with patch("nonebot.adapters.onebot.v11.MessageSegment.image", return_value=MagicMock()):
            sent = await client._send_post_reply_sticker_if_needed(
                reply="哈哈太好笑了。明天见。",
                thinker_decision=SimpleNamespace(sticker=True),
                session_id="group_123",
                group_id="123",
                user_id="456",
                turn_id="turn-1",
                ctx=ToolContext(bot=bot, user_id="456", group_id="123", session_id="group_123"),
                already_sent=False,
            )
    finally:
        await client.close()

    assert sent is True
    bot.send_group_msg.assert_awaited_once()


async def test_post_reply_sticker_skips_when_already_sent(tmp_path) -> None:
    client, _sticker_id, bot = await _client(tmp_path)
    try:
        with patch("nonebot.adapters.onebot.v11.MessageSegment.image", return_value=MagicMock()):
            sent = await client._send_post_reply_sticker_if_needed(
                reply="好的",
                thinker_decision=SimpleNamespace(sticker=True),
                session_id="group_123",
                group_id="123",
                user_id="456",
                turn_id="turn-1",
                ctx=ToolContext(bot=bot, user_id="456", group_id="123", session_id="group_123"),
                already_sent=True,
            )
    finally:
        await client.close()

    assert sent is False
    bot.send_group_msg.assert_not_awaited()


_JPEG_B = b"\xff\xd8\xff\xe0" + b"\x11" * 64 + b"jpeg-payload-b"
_JPEG_C = b"\xff\xd8\xff\xe0" + b"\x22" * 64 + b"jpeg-payload-c"


async def test_post_reply_sticker_picks_contextual_match_over_frequency_top(tmp_path) -> None:
    """The provider decides *whether*; selection decides *which* by topic.

    A frequency-fair pool would surface the most-used sticker, which may be
    off-topic.  When the recent user message gives a query, the post-reply
    path must run a BM25 intent search across the library and prefer the
    semantically matching sticker instead of the pool's top entry.
    """
    runtime_state = create_humanization_state_bus()
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    # Off-topic but high-frequency sticker (would be candidate_pool[0]).
    freq_id, _ = store.add(_JPEG_DATA, "吃饭", "饿了想吃东西时发")
    # On-topic morning-greeting sticker.
    morning_id, _ = store.add(_JPEG_B, "早安", "早上向朋友问候早上好时发送")
    bot = MagicMock()
    bot.send_group_msg = AsyncMock()
    tools = ToolRegistry()
    tools.register(SendStickerTool(store, runtime_state=runtime_state))
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=PersonaRuntime()),
        short_term=ShortTermMemory(),
        tools=tools,
        thinker_enabled=False,
        runtime_state=runtime_state,
        sticker_placement_config=SimpleNamespace(enabled=True, cooldown_ms=45_000),
    )
    # Seed a recent user message so _fallback_query yields a morning intent.
    client._short_term.add("group_123", "user", "早上好呀大家")
    selected = client._select_post_reply_sticker(
        store,
        candidate_pool=(freq_id, morning_id),
        session_id="group_123",
        group_id="123",
        scope=client._humanization_scope(
            session_id="group_123", group_id="123", user_id="456", turn_id="turn-1"
        ),
    )
    await client.close()
    assert selected == morning_id


async def test_post_reply_sticker_falls_back_to_pool_when_no_query(tmp_path) -> None:
    """With no recent user message (no query), keep prior behaviour: pool top."""
    runtime_state = create_humanization_state_bus()
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    a_id, _ = store.add(_JPEG_DATA, "笑哭", "开心接梗时发")
    b_id, _ = store.add(_JPEG_C, "无语", "无语时发")
    tools = ToolRegistry()
    tools.register(SendStickerTool(store, runtime_state=runtime_state))
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=PersonaRuntime()),
        short_term=ShortTermMemory(),
        tools=tools,
        thinker_enabled=False,
        runtime_state=runtime_state,
        sticker_placement_config=SimpleNamespace(enabled=True, cooldown_ms=45_000),
    )
    selected = client._select_post_reply_sticker(
        store,
        candidate_pool=(a_id, b_id),
        session_id="group_999",
        group_id="999",
        scope=client._humanization_scope(
            session_id="group_999", group_id="999", user_id="456", turn_id="turn-1"
        ),
    )
    await client.close()
    assert selected == a_id


async def test_post_reply_sticker_skips_recently_used_contextual(tmp_path) -> None:
    """A semantic match already used recently in this scope is skipped."""
    runtime_state = create_humanization_state_bus()
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    freq_id, _ = store.add(_JPEG_DATA, "吃饭", "饿了想吃东西时发")
    morning_a, _ = store.add(_JPEG_B, "早安", "早上向朋友问候早上好时发送")
    morning_b, _ = store.add(_JPEG_C, "早上好", "清晨问候早上好时发送的可爱表情")
    tools = ToolRegistry()
    tools.register(SendStickerTool(store, runtime_state=runtime_state))
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=PersonaRuntime()),
        short_term=ShortTermMemory(),
        tools=tools,
        thinker_enabled=False,
        runtime_state=runtime_state,
        sticker_placement_config=SimpleNamespace(enabled=True, cooldown_ms=45_000),
    )
    client._short_term.add("group_123", "user", "早上好呀大家")
    scope = client._humanization_scope(
        session_id="group_123", group_id="123", user_id="456", turn_id="turn-1"
    )
    # Mark the top morning match as recently used; selection must pick the other.
    top = store.search_by_intent("早上好呀大家", top_k=1)[0]
    client._runtime_state.set(
        STICKER_RECENT_USED_SLOT,
        {"sticker_ids": [top]},
        scope=scope,
        source=humanization_source("test:recent_used"),
        confidence=1.0,
    )
    selected = client._select_post_reply_sticker(
        store,
        candidate_pool=(freq_id, morning_a, morning_b),
        session_id="group_123",
        group_id="123",
        scope=scope,
    )
    await client.close()
    assert selected != top
    assert selected in {morning_a, morning_b}


def test_bias_query_by_valence_appends_empathetic_terms_for_low_valence() -> None:
    biased = LLMClient._bias_query_by_valence("今天好累", -0.6)
    assert biased.startswith("今天好累")
    assert "共情" in biased and "陪伴" in biased


def test_bias_query_by_valence_appends_upbeat_terms_for_high_valence() -> None:
    biased = LLMClient._bias_query_by_valence("太棒了", 0.8)
    assert biased.startswith("太棒了")
    assert "开心" in biased and "欢呼" in biased


def test_bias_query_by_valence_neutral_is_unchanged() -> None:
    assert LLMClient._bias_query_by_valence("在吗", 0.0) == "在吗"
