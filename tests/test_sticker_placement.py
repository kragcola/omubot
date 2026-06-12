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
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot, PersonaRuntime
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
                rng=lambda: 0.0,
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
    # The bot's reply text is the query signal — a morning greeting reply
    # should select the morning sticker via BM25 intent search.
    selected = client._select_post_reply_sticker(
        store,
        candidate_pool=(freq_id, morning_id),
        reply="早上好呀大家",
        session_id="group_123",
        group_id="123",
        scope=client._humanization_scope(
            session_id="group_123", group_id="123", user_id="456", turn_id="turn-1"
        ),
    )
    await client.close()
    assert selected == morning_id


async def test_post_reply_sticker_falls_back_to_pool_when_no_query(tmp_path) -> None:
    """With an empty reply and neutral valence (no query), keep prior behaviour: pool top."""
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
        reply="",
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
    scope = client._humanization_scope(
        session_id="group_123", group_id="123", user_id="456", turn_id="turn-1"
    )
    reply = "早上好呀大家"
    # Mark the top morning match as recently used; selection must pick the other.
    top = store.search_by_intent(reply, top_k=1)[0]
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
        reply=reply,
        session_id="group_123",
        group_id="123",
        scope=scope,
    )
    await client.close()
    assert selected != top
    assert selected in {morning_a, morning_b}


async def test_post_reply_sticker_weak_reply_uses_valence_class(tmp_path) -> None:
    """Weak reply (no emotion words in text) still selects by valence class.

    Regression for the 2026-06-11 mis-match: a nickname-call turn produced a
    reply with no emotion tokens. The old path keyed off ``_fallback_query``
    (scheduler trigger boilerplate), whose filler tokens cross-matched an
    unrelated sticker. The reply-as-query path falls back to the appended
    valence words ("开心 兴奋 欢呼"), which pin the upbeat class on their own.
    """
    runtime_state = create_humanization_state_bus()
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    happy_id, _ = store.add(_JPEG_B, "兴奋", "适合在表达开心、兴奋或欢呼时发送")
    sad_id, _ = store.add(_JPEG_C, "委屈", "适合在表达难过、委屈或想哭时发送")
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
        candidate_pool=(sad_id, happy_id),
        reply="在呢在呢~",  # weak reply, no emotion tokens BM25 can match
        session_id="group_123",
        group_id="123",
        scope=client._humanization_scope(
            session_id="group_123", group_id="123", user_id="456", turn_id="turn-1"
        ),
        mood_valence=0.6,  # positive → appends 开心/兴奋/欢呼
    )
    await client.close()
    assert selected == happy_id


async def test_post_reply_sticker_no_match_falls_back_to_text(tmp_path) -> None:
    """When no library sticker matches the reply above the intent floor, the
    reply stays text-only (returns None) instead of attaching an off-topic
    sticker (2026-06-12: "匹配不到合适表情降级纯文字")."""
    runtime_state = create_humanization_state_bus()
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    # Library only has emotion stickers; the reply is about an unrelated topic.
    store.add(_JPEG_B, "兴奋", "适合在表达开心、兴奋或欢呼时发送")
    store.add(_JPEG_C, "委屈", "适合在表达难过、委屈或想哭时发送")
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
        sticker_placement_config=SimpleNamespace(
            enabled=True, cooldown_ms=45_000, score_threshold=0.5, intent_relevance_floor=0.5
        ),
    )
    scope = client._humanization_scope(
        session_id="group_123", group_id="123", user_id="456", turn_id="turn-1"
    )
    # High floor + a reply whose tokens don't match any usage_hint → no sticker.
    selected = client._select_post_reply_sticker(
        store,
        candidate_pool=(),  # empty pool → no arbitrary fallback either
        reply="量子色动力学的渐近自由",
        session_id="group_123",
        group_id="123",
        scope=scope,
        mood_valence=0.0,
        intent_floor=99.0,  # nothing can clear this → must drop to text
    )
    await client.close()
    assert selected is None


async def test_post_reply_sticker_force_send_bypasses_intent_floor(tmp_path) -> None:
    """kaomoji-enforce (force_send) bypasses the intent floor: the kaomoji-as-
    sticker intent is explicit, so it picks the pool's best rather than dropping
    to text even when nothing matches well."""
    runtime_state = create_humanization_state_bus()
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    happy_id, _ = store.add(_JPEG_B, "兴奋", "适合在表达开心、兴奋或欢呼时发送")
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
        sticker_placement_config=SimpleNamespace(
            enabled=True, cooldown_ms=45_000, score_threshold=0.5, intent_relevance_floor=0.5
        ),
    )
    scope = client._humanization_scope(
        session_id="group_123", group_id="123", user_id="456", turn_id="turn-1"
    )
    selected = client._select_post_reply_sticker(
        store,
        candidate_pool=(happy_id,),
        reply="量子色动力学的渐近自由",
        session_id="group_123",
        group_id="123",
        scope=scope,
        mood_valence=0.0,
        intent_floor=99.0,
        force_send=True,  # explicit kaomoji intent → never drop图
    )
    await client.close()
    assert selected == happy_id
    biased = LLMClient._bias_query_by_valence("今天好累", -0.6)
    assert biased.startswith("今天好累")
    assert "共情" in biased and "陪伴" in biased


def test_bias_query_by_valence_appends_upbeat_terms_for_high_valence() -> None:
    biased = LLMClient._bias_query_by_valence("太棒了", 0.8)
    assert biased.startswith("太棒了")
    assert "开心" in biased and "欢呼" in biased


def test_bias_query_by_valence_neutral_is_unchanged() -> None:
    assert LLMClient._bias_query_by_valence("在吗", 0.0) == "在吗"


def _result(text: str) -> dict:
    return {
        "text": text,
        "tool_uses": [],
        "input_tokens": 120,
        "output_tokens": 20,
        "cache_read": 0,
        "cache_create": 0,
    }


async def test_chat_no_tool_branch_invokes_post_reply_sticker_hook(
    tmp_path, persona_runtime: PersonaRuntime, identity_snapshot: IdentitySnapshot
) -> None:
    """Regression: the post-reply sticker hook must fire on the normal
    no-tool terminal branch, not only on tool-exhaustion. Anchoring it solely
    on tool exhaustion left the F-cluster placement path dead for ordinary
    replies (the vast majority of turns)."""
    runtime_state = create_humanization_state_bus()
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    store.add(_JPEG_DATA, "笑哭", "开心接梗时发")
    tools = ToolRegistry()
    tools.register(SendStickerTool(store, runtime_state=runtime_state))
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=persona_runtime),
        short_term=ShortTermMemory(),
        tools=tools,
        group_timeline=GroupTimeline(),
        thinker_enabled=False,
        runtime_state=runtime_state,
        sticker_placement_config=SimpleNamespace(enabled=True, cooldown_ms=45_000),
    )
    try:
        with patch.object(
            client, "_send_post_reply_sticker_if_needed", new_callable=AsyncMock
        ) as hook, patch(
            "services.llm.client.call_api",
            new_callable=AsyncMock,
            side_effect=[_result("早上好呀~")],
        ):
            await client.chat(
                session_id="group_100",
                group_id="100",
                user_id="u1",
                user_content="早",
                identity=identity_snapshot,
            )
    finally:
        await client.close()

    hook.assert_awaited_once()
    assert hook.await_args.kwargs["already_sent"] is False
