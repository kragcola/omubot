"""Tests for RetrievalGate: 4-tier gating strategy for memory card injection."""

import pytest

from services.memory.card_store import CardStore, NewCard
from services.memory.retrieval import RetrievalGate, extract_keywords


@pytest.fixture
async def store(tmp_path) -> CardStore:
    db_path = str(tmp_path / "test_retrieval.db")
    s = CardStore(db_path=db_path)
    await s.init()
    return s


@pytest.fixture
async def gate(store: CardStore) -> RetrievalGate:
    return RetrievalGate(card_store=store, refresh_interval=5)


# ------------------------------------------------------------------
# Gate 1: new session → full retrieval
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_session_full_retrieval(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="preference", scope="user", scope_id="123", content="喜欢音游"))
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="会弹钢琴"))
    result = await gate.build_memo_block("session_1", "123", None)
    assert "【用户记忆 / @123】" in result
    assert "喜欢音游" in result
    assert "会弹钢琴" in result


# ------------------------------------------------------------------
# Gate 2: periodic refresh
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_periodic_refresh(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="喜欢音游"))

    # Turn 1: full retrieval
    r1 = await gate.build_memo_block("s1", "123", None)
    assert "【用户记忆" in r1

    # Turn 2-5: minimal hint (no conversation_text, bypassing gate 3)
    for _ in range(4):
        r = await gate.build_memo_block("s1", "123", None)
        assert "lookup_cards" in r

    # Turn 6: periodic refresh (6 - 1 = 5 >= refresh_interval=5)
    r6 = await gate.build_memo_block("s1", "123", None)
    assert "【用户记忆" in r6


# ------------------------------------------------------------------
# Gate 3: keyword match
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keyword_match(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="preference", scope="user", scope_id="123", content="喜欢音游"))
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="养了一只猫"))

    # Turn 1 (full retrieval, establish session)
    await gate.build_memo_block("s1", "123", None)

    # Turn 2: keyword match on conversation text
    result = await gate.build_memo_block("s1", "123", None, conversation_text="音游，最近在玩")
    assert "喜欢音游" in result
    assert "养了一只猫" not in result  # only matching cards
    assert "关键词匹配" in result


@pytest.mark.asyncio
async def test_keyword_match_limit(store: CardStore, gate: RetrievalGate) -> None:
    # 3 groups of 5 cards, each group sharing a unique keyword
    for i in range(5):
        await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content=f"alpha标记{i}的内容"))
        await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content=f"beta标记{i}的内容"))
        await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content=f"gamma标记{i}的内容"))

    await gate.build_memo_block("s1", "123", None)

    # Three keywords from punctuation-split text, each matches 5 cards → up to 15, capped at 10
    result = await gate.build_memo_block("s1", "123", None, conversation_text="alpha标记。beta标记。gamma标记")

    # Count occurrences of card content markers (each "标记N" appears at most once in results)
    lines = result.split("\n")
    card_lines = [line for line in lines if line.startswith("[事实]")]
    assert len(card_lines) <= 10
    assert len(card_lines) >= 5  # at least one keyword's worth


@pytest.mark.asyncio
async def test_keyword_match_no_results(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="喜欢音游"))

    await gate.build_memo_block("s1", "123", None)

    # Keyword doesn't match any card → falls through to gate 4 (minimal)
    result = await gate.build_memo_block("s1", "123", None, conversation_text="今天天气很好")
    assert "lookup_cards" in result


# ------------------------------------------------------------------
# Gate 4: minimal hint
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_minimal_hint(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="喜欢音游"))
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="养了一只猫"))

    # Turn 1: full retrieval
    await gate.build_memo_block("s1", "123", None)

    # Turn 2: no conversation text → gate 4
    result = await gate.build_memo_block("s1", "123", None)
    assert "lookup_cards" in result
    assert "2" in result  # 2 cards


@pytest.mark.asyncio
async def test_empty_cards_first_turn(store: CardStore, gate: RetrievalGate) -> None:
    result = await gate.build_memo_block("s1", "999", None)
    assert "暂无记录" in result


@pytest.mark.asyncio
async def test_empty_cards_subsequent_turn(store: CardStore, gate: RetrievalGate) -> None:
    # Turn 1
    await gate.build_memo_block("s1", "999", None)
    # Turn 2: no cards → gate 4 returns ""
    result = await gate.build_memo_block("s1", "999", None)
    assert result == ""


# ------------------------------------------------------------------
# extract_keywords
# ------------------------------------------------------------------


def test_extract_keywords_chinese() -> None:
    keywords = extract_keywords("今天天气真好，我们去吃火锅吧！然后看电影怎么样？")
    assert len(keywords) >= 2
    assert "今天天气真好" in keywords
    assert "我们去吃火锅吧" in keywords
    assert "然后看电影怎么样" in keywords


def test_extract_keywords_empty() -> None:
    assert extract_keywords("") == []
    assert extract_keywords("   ") == []


def test_extract_keywords_too_short() -> None:
    keywords = extract_keywords("a, b, cd, ef, gh")
    # Only segments with 2-8 chars, "a" and "b" are too short
    assert all(2 <= len(k) <= 8 for k in keywords)


def test_extract_keywords_max_five() -> None:
    text = "一。二。三。四。五。六。七。八"
    keywords = extract_keywords(text)
    # Capped at 5, each segment is 1 char which is too short
    # Actually 一 is 1 char, too short. Let me use 2-char segments
    text2 = "一二。三四。五六。七八。九十。甲乙。丙丁。戊己"
    keywords = extract_keywords(text2)
    assert len(keywords) <= 5


# ------------------------------------------------------------------
# Rewind turn (thinker wait)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewind_turn_preserves_full_retrieval(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="喜欢音游"))

    # Turn 1: full retrieval (but simulate thinker wait by rewinding)
    r1 = await gate.build_memo_block("s1", "123", None)
    assert "【用户记忆" in r1  # full retrieval

    gate.rewind_turn("s1")

    # Turn 1 again: should still be full retrieval
    r2 = await gate.build_memo_block("s1", "123", None)
    assert "【用户记忆" in r2  # full retrieval again


@pytest.mark.asyncio
async def test_rewind_turn_noop_on_unknown(store: CardStore, gate: RetrievalGate) -> None:
    gate.rewind_turn("nonexistent")  # no session yet — should not raise


# ------------------------------------------------------------------
# Cache invalidation
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalidate_entity(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="fact", scope="group", scope_id="456", content="音游比赛"))

    # First session: full retrieval, cached under "group_456"
    r1 = await gate.build_memo_block("s1", "123", "456")
    assert "音游比赛" in r1

    # Add a new card
    await store.add_card(NewCard(category="fact", scope="group", scope_id="456", content="新比赛"))

    # Second session: still gets cached (stale) result
    r2 = await gate.build_memo_block("s2", "123", "456")
    assert "新比赛" not in r2

    # Invalidate entity → cache cleared
    gate.invalidate_entity("group", "456")

    # Third session: fresh retrieval
    r3 = await gate.build_memo_block("s3", "123", "456")
    assert "新比赛" in r3


@pytest.mark.asyncio
async def test_invalidate_session(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="喜欢音游"))

    # Turn 1 and 2
    await gate.build_memo_block("s1", "123", None)
    r2 = await gate.build_memo_block("s1", "123", None)
    assert "lookup_cards" in r2  # minimal after turn 1

    # Invalidate → session state deleted
    gate.invalidate_session("s1")

    # Now acts like turn 1 again
    r3 = await gate.build_memo_block("s1", "123", None)
    assert "【用户记忆" in r3  # full retrieval


@pytest.mark.asyncio
async def test_invalidate_all(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="喜欢音游"))

    # Establish cache and session state
    await gate.build_memo_block("s1", "123", None)
    await gate.build_memo_block("s2", "123", None)

    gate.invalidate_all()

    # Fresh retrieval after invalidation
    r = await gate.build_memo_block("s3", "123", None)
    assert "喜欢音游" in r


# ------------------------------------------------------------------
# Full cache reuse
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_cache_reuse(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="喜欢音游"))

    r1 = await gate.build_memo_block("s1", "123", None)
    r2 = await gate.build_memo_block("s2", "123", None)
    assert r1 == r2


# ------------------------------------------------------------------
# Keyword search: dedup, scoping, global
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keyword_search_dedup(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="alpha和beta相关"))

    await gate.build_memo_block("s1", "123", None)

    result = await gate.build_memo_block("s1", "123", None, conversation_text="alpha。beta")
    # Card matches both keywords, should appear only once
    assert result.count("alpha和beta相关") == 1


@pytest.mark.asyncio
async def test_keyword_search_scoped(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="用户123的卡片"))
    await store.add_card(NewCard(category="fact", scope="user", scope_id="456", content="用户456的卡片"))

    await gate.build_memo_block("s1", "123", None)

    result = await gate.build_memo_block("s1", "123", None, conversation_text="卡片")
    assert "用户123的卡片" in result
    assert "用户456的卡片" not in result


@pytest.mark.asyncio
async def test_global_scope_cards_included(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="fact", scope="global", scope_id="global", content="全局卡片内容"))

    await gate.build_memo_block("s1", "123", None)

    result = await gate.build_memo_block("s1", "123", None, conversation_text="全局卡片内容")
    assert "全局卡片内容" in result


# ------------------------------------------------------------------
# Session limit
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_limit(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="测试卡片"))

    # Fill up to max sessions (500) — full_cache means only 1 DB hit
    for i in range(500):
        await gate.build_memo_block(f"session_{i}", "123", None)

    # Session 500 triggers eviction of the oldest (session_0)
    await gate.build_memo_block("session_500", "123", None)

    # session_0 should be treated as a new session (old state evicted)
    result = await gate.build_memo_block("session_0", "123", None)
    assert "【用户记忆" in result


# ------------------------------------------------------------------
# Group chat context
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_context_full_retrieval(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="event", scope="group", scope_id="456", content="音游比赛"))

    result = await gate.build_memo_block("s1", "123", "456")
    assert "【当前在群 #456 中对话】" in result
    assert "音游比赛" in result


@pytest.mark.asyncio
async def test_group_context_minimal_hint(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="event", scope="group", scope_id="456", content="音游比赛"))

    await gate.build_memo_block("s1", "123", "456")

    result = await gate.build_memo_block("s1", "123", "456")
    assert "lookup_cards" in result
    assert "群" in result


@pytest.mark.asyncio
async def test_private_chat_context(store: CardStore, gate: RetrievalGate) -> None:
    await store.add_card(NewCard(category="preference", scope="user", scope_id="999", content="私聊偏好"))

    result = await gate.build_memo_block("s1", "999", None)
    assert "【当前私聊 @999】" in result
    assert "私聊偏好" in result
