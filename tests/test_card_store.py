import asyncio

import pytest

from services.memory.card_store import (
    CardStore,
    NewCard,
)


@pytest.fixture
async def store(tmp_path) -> CardStore:
    db_path = str(tmp_path / "test_cards.db")
    s = CardStore(db_path=db_path)
    await s.init()
    return s


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
async def test_add_card_invalid_category(store: CardStore) -> None:
    with pytest.raises(ValueError, match="Invalid category"):
        NewCard(category="bogus", scope="user", scope_id="1", content="x")


@pytest.mark.asyncio
async def test_add_card_invalid_scope(store: CardStore) -> None:
    with pytest.raises(ValueError, match="Invalid scope"):
        NewCard(category="fact", scope="unknown", scope_id="1", content="x")


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
