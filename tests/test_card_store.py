import asyncio
from collections.abc import AsyncIterator

import pytest

from services.memory.card_store import (
    CardStore,
    NewCard,
    NewCardSeries,
)


@pytest.fixture
async def store(tmp_path) -> AsyncIterator[CardStore]:
    db_path = str(tmp_path / "test_cards.db")
    s = CardStore(db_path=db_path)
    await s.init()
    try:
        yield s
    finally:
        await s.close()


# ------------------------------------------------------------------
# Init
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_init_creates_tables(store: CardStore) -> None:
    cursor = await store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_cards'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["name"] == "memory_cards"


# ------------------------------------------------------------------
# Add / Get
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_and_get_card(store: CardStore) -> None:
    cid = await store.add_card(NewCard(
        category="preference", scope="user", scope_id="123",
        content="用户偏好被称呼为帆",
    ))
    assert cid.startswith("card_")
    card = await store.get_card(cid)
    assert card is not None
    assert card.category == "preference"
    assert card.scope == "user"
    assert card.scope_id == "123"
    assert card.content == "用户偏好被称呼为帆"
    assert card.confidence == 0.7
    assert card.status == "active"
    assert card.priority == 5


@pytest.mark.asyncio
async def test_add_card_records_provenance(store: CardStore) -> None:
    cid = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="123", content="在群里提到喜欢音游"),
        source_msg_id="msg_42",
        captured_at="2026-05-29T12:34:56+08:00",
        captured_by="memo_extractor",
    )

    card = await store.get_card(cid)
    assert card is not None
    assert card.source_msg_id == "msg_42"
    assert card.captured_at == "2026-05-29T12:34:56+08:00"
    assert card.captured_by == "memo_extractor"


@pytest.mark.asyncio
async def test_add_card_invalid_category(store: CardStore) -> None:
    with pytest.raises(ValueError, match="Invalid category"):
        NewCard(category="bogus", scope="user", scope_id="1", content="x")


@pytest.mark.asyncio
async def test_add_card_invalid_scope(store: CardStore) -> None:
    with pytest.raises(ValueError, match="Invalid scope"):
        NewCard(category="fact", scope="unknown", scope_id="1", content="x")


@pytest.mark.asyncio
async def test_add_card_empty_scope_id_for_user_rejected(store: CardStore) -> None:
    with pytest.raises(ValueError, match="scope_id is required"):
        NewCard(category="fact", scope="user", scope_id="   ", content="x")


# ------------------------------------------------------------------
# Get entity cards
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_entity_cards_filter_by_scope(store: CardStore) -> None:
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="user card"))
    await store.add_card(NewCard(category="fact", scope="group", scope_id="456", content="group card"))
    user_cards = await store.get_entity_cards("user", "123")
    assert len(user_cards) == 1
    assert user_cards[0].content == "user card"
    group_cards = await store.get_entity_cards("group", "456")
    assert len(group_cards) == 1
    assert group_cards[0].content == "group card"


@pytest.mark.asyncio
async def test_get_entity_cards_filter_by_category(store: CardStore) -> None:
    await store.add_card(NewCard(category="preference", scope="user", scope_id="1", content="pref"))
    await store.add_card(NewCard(category="fact", scope="user", scope_id="1", content="fact"))
    prefs = await store.get_entity_cards("user", "1", category="preference")
    assert len(prefs) == 1
    assert prefs[0].category == "preference"


@pytest.mark.asyncio
async def test_get_entity_cards_excludes_inactive(store: CardStore) -> None:
    cid = await store.add_card(NewCard(category="fact", scope="user", scope_id="1", content="x"))
    await store.expire_card(cid)
    cards = await store.get_entity_cards("user", "1")
    assert len(cards) == 0


@pytest.mark.asyncio
async def test_get_entity_cards_empty(store: CardStore) -> None:
    cards = await store.get_entity_cards("user", "999")
    assert cards == []


# ------------------------------------------------------------------
# Update
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_card(store: CardStore) -> None:
    cid = await store.add_card(NewCard(category="fact", scope="user", scope_id="1", content="old"))
    ok = await store.update_card(cid, content="new", confidence=0.9)
    assert ok
    card = await store.get_card(cid)
    assert card.content == "new"
    assert card.confidence == 0.9


@pytest.mark.asyncio
async def test_update_card_noop(store: CardStore) -> None:
    ok = await store.update_card("nonexistent", content="x")
    assert not ok


# ------------------------------------------------------------------
# Supersede
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_supersede_card(store: CardStore) -> None:
    old_id = await store.add_card(NewCard(category="fact", scope="user", scope_id="1", content="old fact"))
    new_id = await store.supersede_card(old_id, NewCard(
        category="fact", scope="user", scope_id="1", content="new fact",
    ))
    old = await store.get_card(old_id)
    assert old.status == "superseded"
    new = await store.get_card(new_id)
    assert new.status == "active"
    assert new.supersedes == old_id
    assert new.content == "new fact"


# ------------------------------------------------------------------
# Mark seen / Expire
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_seen(store: CardStore) -> None:
    cid = await store.add_card(NewCard(category="fact", scope="user", scope_id="1", content="x"))
    ok = await store.mark_seen(cid)
    assert ok
    card = await store.get_card(cid)
    assert card.last_seen_at is not None


@pytest.mark.asyncio
async def test_expire_card(store: CardStore) -> None:
    cid = await store.add_card(NewCard(category="fact", scope="user", scope_id="1", content="x"))
    ok = await store.expire_card(cid)
    assert ok
    card = await store.get_card(cid)
    assert card.status == "expired"


# ------------------------------------------------------------------
# List entities / Count
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_entities(store: CardStore) -> None:
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="a"))
    await store.add_card(NewCard(category="fact", scope="user", scope_id="456", content="b"))
    await store.add_card(NewCard(category="fact", scope="group", scope_id="789", content="c"))
    users = await store.list_entities("user")
    assert sorted(users) == ["123", "456"]
    groups = await store.list_entities("group")
    assert groups == ["789"]


@pytest.mark.asyncio
async def test_count_entity_cards(store: CardStore) -> None:
    await store.add_card(NewCard(category="preference", scope="user", scope_id="1", content="a"))
    await store.add_card(NewCard(category="fact", scope="user", scope_id="1", content="b"))
    await store.add_card(NewCard(category="fact", scope="user", scope_id="1", content="c"))
    counts = await store.count_entity_cards("user", "1")
    assert counts == {"preference": 1, "fact": 2}


# ------------------------------------------------------------------
# Search
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_cards(store: CardStore) -> None:
    await store.add_card(NewCard(category="preference", scope="user", scope_id="1", content="用户偏好被称呼为帆"))
    await store.add_card(NewCard(category="fact", scope="user", scope_id="1", content="喜欢音游"))
    results = await store.search_cards("音游")
    assert len(results) == 1
    assert results[0].content == "喜欢音游"


@pytest.mark.asyncio
async def test_search_cards_no_match(store: CardStore) -> None:
    results = await store.search_cards("nonexistent")
    assert results == []


# ------------------------------------------------------------------
# Prompt builders
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_global_index(store: CardStore) -> None:
    await store.add_card(NewCard(category="preference", scope="user", scope_id="123", content="a"))
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="b"))
    await store.add_card(NewCard(category="fact", scope="group", scope_id="456", content="c"))
    idx = await store.build_global_index()
    assert "【记忆索引】" in idx
    assert "用户 @123" in idx
    assert "偏好×1" in idx
    assert "群 #456" in idx
    assert "事实×1" in idx  # one fact per entity


@pytest.mark.asyncio
async def test_build_global_index_empty(store: CardStore) -> None:
    idx = await store.build_global_index()
    assert idx == ""


@pytest.mark.asyncio
async def test_build_entity_prompt(store: CardStore) -> None:
    await store.add_card(NewCard(category="preference", scope="user", scope_id="123", content="偏好被称呼为帆"))
    await store.add_card(NewCard(category="fact", scope="user", scope_id="123", content="喜欢音游"))
    text = await store.build_entity_prompt("user", "123")
    assert "【用户记忆 / @123】" in text
    assert "[偏好] 偏好被称呼为帆" in text
    assert "[事实] 喜欢音游" in text


@pytest.mark.asyncio
async def test_build_entity_prompt_group(store: CardStore) -> None:
    await store.add_card(NewCard(category="event", scope="group", scope_id="456", content="音游比赛"))
    text = await store.build_entity_prompt("group", "456")
    assert "【群记忆 / #456】" in text
    assert "[事件] 音游比赛" in text


@pytest.mark.asyncio
async def test_build_entity_prompt_empty(store: CardStore) -> None:
    text = await store.build_entity_prompt("user", "999")
    assert "暂无记录" in text


@pytest.mark.asyncio
async def test_build_entity_prompt_excludes_superseded(store: CardStore) -> None:
    old_id = await store.add_card(NewCard(category="fact", scope="user", scope_id="1", content="old"))
    await store.supersede_card(old_id, NewCard(category="fact", scope="user", scope_id="1", content="new"))
    text = await store.build_entity_prompt("user", "1")
    assert "new" in text
    assert "old" not in text


# ------------------------------------------------------------------
# Migration
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migration_from_md(tmp_path, store: CardStore) -> None:
    md_dir = tmp_path / "memories"
    users_dir = md_dir / "users"
    users_dir.mkdir(parents=True)
    (users_dir / "123.md").write_text(
        "<!-- updated: 2026-04-30 12:00 | source: test -->\n\n"
        "@123(帆) — 管理员，喜欢音游\n\n"
        "## 待整理\n"
        "- 偏好被称呼为帆\n"
        "- 最近在学Rust\n",
        encoding="utf-8",
    )
    from services.memory.migrate import migrate_md_to_cards
    n = await migrate_md_to_cards(str(md_dir), store)
    assert n == 3  # 1 body + 2 pending

    cards = await store.get_entity_cards("user", "123")
    assert len(cards) == 3
    contents = {c.content for c in cards}
    assert "偏好被称呼为帆" in contents
    assert "最近在学Rust" in contents
    assert all(c.source == "migration" for c in cards)

    # Verify renaming
    assert not (users_dir / "123.md").exists()
    assert (users_dir / "123.md.migrated").exists()


@pytest.mark.asyncio
async def test_migration_idempotent(tmp_path, store: CardStore) -> None:
    md_dir = tmp_path / "memories"
    users_dir = md_dir / "users"
    users_dir.mkdir(parents=True)
    (users_dir / "123.md").write_text(
        "<!-- updated: 2026-04-30 12:00 | source: test -->\n\n"
        "@123(帆) — 管理员\n",
        encoding="utf-8",
    )
    from services.memory.migrate import migrate_md_to_cards
    n1 = await migrate_md_to_cards(str(md_dir), store)
    assert n1 == 1
    n2 = await migrate_md_to_cards(str(md_dir), store)
    assert n2 == 0  # Already migrated


@pytest.mark.asyncio
async def test_migration_missing_dir(store: CardStore) -> None:
    from services.memory.migrate import migrate_md_to_cards
    n = await migrate_md_to_cards("/nonexistent/path", store)
    assert n == 0


# ------------------------------------------------------------------
# Concurrent adds
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_adds(store: CardStore) -> None:
    async def add_one(i: int) -> str:
        return await store.add_card(NewCard(
            category="fact", scope="user", scope_id="1", content=f"card_{i}",
        ))

    ids = await asyncio.gather(*(add_one(i) for i in range(10)))
    assert len(set(ids)) == 10  # All unique
    cards = await store.get_entity_cards("user", "1")
    assert len(cards) == 10


# ------------------------------------------------------------------
# Series CRUD
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_series_create_and_get(store: CardStore) -> None:
    s = await store.create_series(NewCardSeries(
        series_key="food_served:123", scope="user", scope_id="123",
        label="食物推荐记录",
    ))
    assert s.series_id.startswith("ser_")
    assert s.series_key == "food_served:123"
    assert s.label == "食物推荐记录"

    got = await store.get_series(s.series_id)
    assert got is not None
    assert got.series_key == "food_served:123"


@pytest.mark.asyncio
async def test_series_get_by_key(store: CardStore) -> None:
    await store.create_series(NewCardSeries(
        series_key="food_served:456", scope="user", scope_id="456",
    ))
    s = await store.get_series_by_key("food_served:456")
    assert s is not None
    assert s.scope_id == "456"


@pytest.mark.asyncio
async def test_series_get_or_create(store: CardStore) -> None:
    s1 = await store.get_or_create_series("food_served:1", "user", "1", label="test")
    s2 = await store.get_or_create_series("food_served:1", "user", "1", label="ignored")
    assert s1.series_id == s2.series_id  # Same series returned


@pytest.mark.asyncio
async def test_series_cards(store: CardStore) -> None:
    s = await store.create_series(NewCardSeries(
        series_key="food_served:1", scope="user", scope_id="1",
    ))
    await store.add_card(NewCard(
        category="event", scope="user", scope_id="1",
        content="推荐了麦当劳", series_id=s.series_id,
    ))
    await store.add_card(NewCard(
        category="event", scope="user", scope_id="1",
        content="推荐了肯德基", series_id=s.series_id,
    ))
    cards = await store.get_series_cards(s.series_id)
    assert len(cards) == 2
    assert all(c.series_id == s.series_id for c in cards)


@pytest.mark.asyncio
async def test_list_entity_series(store: CardStore) -> None:
    await store.create_series(NewCardSeries(
        series_key="food_served:1", scope="user", scope_id="1",
    ))
    await store.create_series(NewCardSeries(
        series_key="food_pref:1", scope="user", scope_id="1",
    ))
    series = await store.list_entity_series("user", "1")
    assert len(series) == 2


@pytest.mark.asyncio
async def test_series_cards_excludes_expired(store: CardStore) -> None:
    s = await store.create_series(NewCardSeries(
        series_key="food_served:1", scope="user", scope_id="1",
    ))
    cid = await store.add_card(NewCard(
        category="event", scope="user", scope_id="1",
        content="推荐了麦当劳", series_id=s.series_id,
    ))
    await store.expire_card(cid)
    cards = await store.get_series_cards(s.series_id)
    assert len(cards) == 0
    cards_all = await store.get_series_cards(s.series_id, status="expired")
    assert len(cards_all) == 1


# ------------------------------------------------------------------
# find_similar / reinforce
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_similar_match(store: CardStore) -> None:
    await store.add_card(NewCard(
        category="preference", scope="user", scope_id="1",
        content="喜欢吃辣的", source="user_config",
    ))
    found = await store.find_similar("user", "1", "喜欢吃辣的，重口味")
    assert found is not None
    assert "喜欢吃辣的" in found.content


@pytest.mark.asyncio
async def test_find_similar_no_match(store: CardStore) -> None:
    await store.add_card(NewCard(
        category="preference", scope="user", scope_id="1",
        content="喜欢吃辣的",
    ))
    found = await store.find_similar("user", "1", "不喜欢吃甜的")
    assert found is None


@pytest.mark.asyncio
async def test_find_similar_threshold(store: CardStore) -> None:
    await store.add_card(NewCard(
        category="preference", scope="user", scope_id="1",
        content="喜欢吃辣的", confidence=0.3,
    ))
    found = await store.find_similar("user", "1", "喜欢吃辣的", threshold=0.6)
    assert found is None  # confidence 0.3 < threshold 0.6


@pytest.mark.asyncio
async def test_find_similar_short_content(store: CardStore) -> None:
    found = await store.find_similar("user", "1", "x")
    assert found is None


@pytest.mark.asyncio
async def test_reinforce(store: CardStore) -> None:
    cid = await store.add_card(NewCard(
        category="preference", scope="user", scope_id="1",
        content="喜欢吃辣的", confidence=0.7,
    ))
    ok = await store.reinforce(cid)
    assert ok
    card = await store.get_card(cid)
    assert card.confidence == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_reinforce_cap(store: CardStore) -> None:
    cid = await store.add_card(NewCard(
        category="preference", scope="user", scope_id="1",
        content="喜欢吃辣的", confidence=0.95,
    ))
    await store.reinforce(cid)
    card = await store.get_card(cid)
    assert card.confidence == 1.0


@pytest.mark.asyncio
async def test_reinforce_nonexistent(store: CardStore) -> None:
    ok = await store.reinforce("card_nonexistent")
    assert not ok


# ------------------------------------------------------------------
# Series + series_id on cards
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_with_series_id(store: CardStore) -> None:
    s = await store.create_series(NewCardSeries(
        series_key="test:1", scope="user", scope_id="1",
    ))
    cid = await store.add_card(NewCard(
        category="event", scope="user", scope_id="1",
        content="test", series_id=s.series_id,
    ))
    card = await store.get_card(cid)
    assert card.series_id == s.series_id


@pytest.mark.asyncio
async def test_card_without_series_id(store: CardStore) -> None:
    cid = await store.add_card(NewCard(
        category="fact", scope="user", scope_id="1", content="no series",
    ))
    card = await store.get_card(cid)
    assert card.series_id is None


@pytest.mark.asyncio
async def test_update_card_series_id(store: CardStore) -> None:
    s = await store.create_series(NewCardSeries(
        series_key="test:1", scope="user", scope_id="1",
    ))
    cid = await store.add_card(NewCard(
        category="event", scope="user", scope_id="1", content="test",
    ))
    ok = await store.update_card(cid, series_id=s.series_id)
    assert ok
    card = await store.get_card(cid)
    assert card.series_id == s.series_id


@pytest.mark.asyncio
async def test_series_table_created(store: CardStore) -> None:
    cursor = await store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='card_series'"
    )
    row = await cursor.fetchone()
    assert row is not None


# ------------------------------------------------------------------
# Backfill food series
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_food_series(tmp_path) -> None:
    """Old food cards (series_id=NULL) get assigned to series on init."""
    db_path = str(tmp_path / "backfill.db")
    s = CardStore(db_path=db_path)
    await s.init()

    try:
        insert_card_sql = (
            "INSERT INTO memory_cards ("
            "card_id, category, scope, scope_id, content, confidence, "
            "status, priority, source, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        ts = "2026-01-01T00:00:00"

        # Food event cards (old _record_served)
        for i in range(3):
            await s._db.execute(
                insert_card_sql,
                (
                    f"old_event_{i}", "event", "user", "100", f"推荐了食物{i}",
                    0.5, "active", 5, "food_plugin", ts, ts,
                ),
            )
        # Food preference cards (old _add_preference with source=food_plugin)
        await s._db.execute(
            insert_card_sql,
            (
                "old_pref_1", "preference", "user", "100", "喜欢吃辣的",
                0.7, "active", 5, "food_plugin", ts, ts,
            ),
        )
        # Early served recommendation cards were accidentally stored as preferences.
        await s._db.execute(
            insert_card_sql,
            (
                "old_served_pref_1", "preference", "user", "100", "推荐了热汤面（05-04 22:27）",
                0.5, "active", 5, "food_plugin", ts, ts,
            ),
        )
        # Preference with source=user_config (content-matched)
        await s._db.execute(
            insert_card_sql,
            (
                "old_pref_2", "preference", "user", "100", "不喜欢吃甜的",
                0.7, "active", 5, "user_config", ts, ts,
            ),
        )
        # Non-food card - should be untouched
        await s._db.execute(
            insert_card_sql,
            (
                "other_card", "fact", "user", "100", "not food",
                0.7, "active", 5, "manual", ts, ts,
            ),
        )
        await s._db.commit()
    finally:
        await s.close()

    # Re-init triggers backfill
    s2 = CardStore(db_path=db_path)
    await s2.init()

    try:
        cards = await s2.get_entity_cards("user", "100")
        event_cards = [c for c in cards if c.category == "event"]
        pref_cards = [c for c in cards if c.category == "preference"]
        other_cards = [c for c in cards if c.source == "manual"]

        # Event cards -> food_served series
        assert len(event_cards) == 4
        assert all(c.series_id is not None for c in event_cards)
        served_series = await s2.get_series_by_key("food_served:100")
        assert served_series is not None
        assert served_series.label == "食物推荐记录"
        assert all(c.series_id == served_series.series_id for c in event_cards)
        assert any(c.card_id == "old_served_pref_1" for c in event_cards)

        # Preference cards -> food_pref series
        assert len(pref_cards) == 2
        assert all(c.series_id is not None for c in pref_cards)
        pref_series = await s2.get_series_by_key("food_pref:100")
        assert pref_series is not None
        assert pref_series.label == "食物口味偏好"
        assert all(c.series_id == pref_series.series_id for c in pref_cards)

        # Non-food card untouched
        assert len(other_cards) == 1
        assert other_cards[0].series_id is None
    finally:
        await s2.close()

    # Idempotent: re-init again should not duplicate
    s3 = CardStore(db_path=db_path)
    await s3.init()
    try:
        series_list = await s3.list_entity_series("user", "100")
        assert len(series_list) == 2  # food_served + food_pref
    finally:
        await s3.close()
