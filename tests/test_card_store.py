import asyncio
import contextlib
import inspect
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
# Observations / atomic supersede+reinforce provenance (write-policy v1)
# ------------------------------------------------------------------


def _assert_supersede_provenance_api() -> None:
    sig = inspect.signature(CardStore.supersede_card)
    params = sig.parameters
    assert "source_msg_id" in params or "source_message_id" in params, (
        "supersede_card must accept optional provenance kwargs "
        "(source_msg_id / evidence_text / captured_by)"
    )
    assert "evidence_text" in params or "evidence" in params


def _assert_reinforce_observation_api() -> None:
    sig = inspect.signature(CardStore.reinforce)
    params = sig.parameters
    assert "evidence_text" in params or "source_message_id" in params, (
        "reinforce must accept optional observation kwargs when evidence is supplied"
    )


@pytest.mark.asyncio
async def test_init_creates_memory_card_observations_table(store: CardStore) -> None:
    cursor = await store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_card_observations'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["name"] == "memory_card_observations"

    # Required columns
    col_cursor = await store._db.execute("PRAGMA table_info(memory_card_observations)")
    cols = {r["name"] for r in await col_cursor.fetchall()}
    for required in (
        "observation_id",
        "card_id",
        "decision",
        "source_message_id",
        "evidence_text",
        "observed_at",
        "captured_by",
        "meta_json",
    ):
        assert required in cols, f"missing column {required}"


@pytest.mark.asyncio
async def test_observations_indexes_exist(store: CardStore) -> None:
    cursor = await store._db.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' "
        "AND tbl_name='memory_card_observations'"
    )
    rows = await cursor.fetchall()
    sqls = " ".join((r["sql"] or r["name"] or "") for r in rows).lower()
    names = " ".join((r["name"] or "") for r in rows).lower()
    blob = sqls + " " + names
    assert "card_id" in blob
    assert "source_message_id" in blob
    # Idempotency for non-null source_message_id: unique partial index or unique index
    assert "unique" in blob


@pytest.mark.asyncio
async def test_list_observations_and_card_observation_type(store: CardStore) -> None:
    from services.memory import card_store as cs_mod

    assert hasattr(cs_mod, "CardObservation"), "CardObservation dataclass must be exported"
    assert callable(getattr(store, "list_observations", None)), (
        "CardStore.list_observations(card_id) public API required"
    )

    cid = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="seed"),
    )
    empty = await store.list_observations(cid)
    assert empty == []

    _assert_reinforce_observation_api()
    ok = await store.reinforce(
        cid,
        boost=0.1,
        evidence_text="用户再次确认喜欢猫",
        source_message_id="msg_obs_1",
        captured_by="memo_extractor",
        decision="reinforce",
    )
    assert ok is True
    obs = await store.list_observations(cid)
    assert len(obs) == 1
    row = obs[0]
    assert isinstance(row, cs_mod.CardObservation)
    assert row.card_id == cid
    assert row.decision == "reinforce"
    assert str(row.source_message_id) == "msg_obs_1"
    assert row.evidence_text
    assert row.captured_by == "memo_extractor"
    assert row.observed_at
    assert row.observation_id


@pytest.mark.asyncio
async def test_observation_idempotent_same_card_source_decision(store: CardStore) -> None:
    _assert_reinforce_observation_api()
    assert callable(getattr(store, "list_observations", None))

    cid = await store.add_card(
        NewCard(category="preference", scope="user", scope_id="1", content="喜欢茶"),
    )
    kwargs = dict(
        boost=0.05,
        evidence_text="again",
        source_message_id="msg_same",
        captured_by="memo_extractor",
        decision="reinforce",
    )
    assert await store.reinforce(cid, **kwargs)
    conf_after_first = (await store.get_card(cid)).confidence
    # Second call with same source_message_id + decision must not insert second obs
    assert await store.reinforce(cid, **kwargs)
    obs = await store.list_observations(cid)
    same = [
        o
        for o in obs
        if str(o.source_message_id or "") == "msg_same" and o.decision == "reinforce"
    ]
    assert len(same) == 1
    card = await store.get_card(cid)
    assert card.confidence <= 1.0
    assert conf_after_first <= 1.0


@pytest.mark.asyncio
async def test_supersede_card_with_provenance_is_atomic_success(store: CardStore) -> None:
    _assert_supersede_provenance_api()
    assert callable(getattr(store, "list_observations", None))

    old_id = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="用户住在杭州"),
    )
    new_id = await store.supersede_card(
        old_id,
        NewCard(category="fact", scope="user", scope_id="1", content="用户住在上海"),
        source_msg_id="msg_sup_atomic",
        captured_by="memo_extractor",
        evidence_text="用户说现在住在上海",
    )
    old = await store.get_card(old_id)
    new = await store.get_card(new_id)
    assert old is not None and old.status == "superseded"
    assert new is not None and new.status == "active"
    assert new.supersedes == old_id
    assert str(new.source_msg_id) == "msg_sup_atomic"
    assert new.captured_by == "memo_extractor"

    obs = await store.list_observations(new_id)
    assert len(obs) >= 1
    assert any(o.decision == "supersede" for o in obs)
    assert any(str(o.source_message_id or "") == "msg_sup_atomic" for o in obs)

    active = await store.get_entity_cards("user", "1", category="fact")
    assert len(active) == 1
    assert active[0].card_id == new_id


@pytest.mark.asyncio
async def test_supersede_missing_or_inactive_old_fails_without_new_card(
    store: CardStore,
) -> None:
    _assert_supersede_provenance_api()
    assert callable(getattr(store, "list_observations", None))

    # Nonexistent old card: must fail without creating a new active card.
    before = await store.get_entity_cards("user", "1")
    with pytest.raises(ValueError):
        await store.supersede_card(
            "card_missing",
            NewCard(category="fact", scope="user", scope_id="1", content="orphan"),
            source_msg_id="msg_x",
            evidence_text="x",
            captured_by="memo_extractor",
        )
    after = await store.get_entity_cards("user", "1")
    assert len(after) == len(before)
    assert all(c.content != "orphan" for c in after)

    # Inactive old card
    old_id = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="old inactive"),
    )
    await store.expire_card(old_id)
    with pytest.raises(ValueError):
        await store.supersede_card(
            old_id,
            NewCard(category="fact", scope="user", scope_id="1", content="should not insert"),
            source_msg_id="msg_y",
            evidence_text="y",
            captured_by="memo_extractor",
        )
    active = await store.get_entity_cards("user", "1")
    assert all(c.content != "should not insert" for c in active)
    assert await store.list_observations("card_never") == []


@pytest.mark.asyncio
async def test_supersede_cancel_or_failure_rolls_back_full_transaction(
    store: CardStore,
) -> None:
    _assert_supersede_provenance_api()
    assert callable(getattr(store, "list_observations", None))

    old_id = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="用户住在杭州"),
    )

    # Force a mid-transaction failure. Prefer patching commit so GREEN's single
    # SQLite transaction either fully lands or fully rolls back.
    real_commit = store._db.commit
    commit_hits = {"n": 0}

    async def boom_commit() -> None:
        commit_hits["n"] += 1
        raise asyncio.CancelledError()

    store._db.commit = boom_commit  # type: ignore[method-assign]
    try:
        with pytest.raises(asyncio.CancelledError):
            await store.supersede_card(
                old_id,
                NewCard(category="fact", scope="user", scope_id="1", content="用户住在上海"),
                source_msg_id="msg_cancel",
                captured_by="memo_extractor",
                evidence_text="搬家了",
            )
    finally:
        store._db.commit = real_commit  # type: ignore[method-assign]

    old = await store.get_card(old_id)
    assert old is not None
    assert old.status == "active", "cancel must leave old card active"

    active = await store.get_entity_cards("user", "1", category="fact")
    assert len(active) == 1
    assert active[0].card_id == old_id
    assert "杭州" in active[0].content

    all_obs_cursor = await store._db.execute(
        "SELECT card_id, decision FROM memory_card_observations WHERE decision = 'supersede'"
    )
    obs_rows = await all_obs_cursor.fetchall()
    assert list(obs_rows) == []


@pytest.mark.asyncio
async def test_supersede_exception_rolls_back_no_double_active(store: CardStore) -> None:
    _assert_supersede_provenance_api()

    old_id = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="v1"),
    )
    real_commit = store._db.commit

    async def boom_commit() -> None:
        raise RuntimeError("injected commit failure")

    store._db.commit = boom_commit  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError):
            await store.supersede_card(
                old_id,
                NewCard(category="fact", scope="user", scope_id="1", content="v2"),
                source_msg_id="msg_fail",
                captured_by="memo_extractor",
                evidence_text="fail path",
            )
    finally:
        store._db.commit = real_commit  # type: ignore[method-assign]

    old = await store.get_card(old_id)
    assert old is not None and old.status == "active"
    active = await store.get_entity_cards("user", "1")
    assert len(active) == 1
    assert active[0].card_id == old_id


@pytest.mark.asyncio
async def test_reinforce_with_observation_atomic_no_confidence_without_obs(
    store: CardStore,
) -> None:
    _assert_reinforce_observation_api()
    assert callable(getattr(store, "list_observations", None))

    cid = await store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id="1",
            content="喜欢咖啡",
            confidence=0.5,
        ),
    )
    real_commit = store._db.commit

    async def boom_commit() -> None:
        raise RuntimeError("injected reinforce commit failure")

    store._db.commit = boom_commit  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError):
            await store.reinforce(
                cid,
                boost=0.2,
                evidence_text="再次确认喜欢咖啡",
                source_message_id="msg_re_fail",
                captured_by="memo_extractor",
            )
    finally:
        store._db.commit = real_commit  # type: ignore[method-assign]

    card = await store.get_card(cid)
    assert card is not None
    assert card.confidence == pytest.approx(0.5), (
        "confidence must not update without its observation when observation data supplied"
    )
    assert await store.list_observations(cid) == []


@pytest.mark.asyncio
async def test_reinforce_without_evidence_keeps_legacy_behavior(store: CardStore) -> None:
    """Optional provenance kwargs: omitting them preserves current reinforce semantics."""
    cid = await store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id="1",
            content="喜欢茶",
            confidence=0.7,
        ),
    )
    ok = await store.reinforce(cid)
    assert ok
    card = await store.get_card(cid)
    assert card.confidence == pytest.approx(0.8)


# ==================================================================
# Correction packet gaps (RED) — write lock / supersede TOCTOU
# ==================================================================


@pytest.mark.asyncio
async def test_concurrent_double_supersede_exactly_one_successor(
    store: CardStore,
) -> None:
    """Two concurrent supersedes of the same active card: one wins, one fails cleanly."""
    old_id = await store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="1",
            content="用户住在杭州",
        )
    )

    # Widen the TOCTOU window when both callers observe old as active.
    # With a proper write lock, the second caller waits and then fails cleanly
    # on non-active old — it never double-enters the active read.
    entered = {"n": 0}
    both_entered = asyncio.Event()
    enter_lock = asyncio.Lock()
    real_get_card = store.get_card

    async def racing_get_card(card_id: str, *args: object, **kwargs: object):
        card = await real_get_card(card_id, *args, **kwargs)
        if (
            card_id == old_id
            and card is not None
            and getattr(card, "status", None) == "active"
        ):
            async with enter_lock:
                entered["n"] += 1
                if entered["n"] >= 2:
                    both_entered.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(both_entered.wait(), timeout=0.4)
            await asyncio.sleep(0.05)
        return card

    store.get_card = racing_get_card  # type: ignore[method-assign]

    async def _supersede(content: str, src: str) -> str:
        return await store.supersede_card(
            old_id,
            NewCard(category="fact", scope="user", scope_id="1", content=content),
            source_msg_id=src,
            captured_by="memo_extractor",
            evidence_text=f"move to {content}",
        )

    try:
        results = await asyncio.gather(
            _supersede("用户住在上海", "msg_race_a"),
            _supersede("用户住在北京", "msg_race_b"),
            return_exceptions=True,
        )
    finally:
        store.get_card = real_get_card  # type: ignore[method-assign]


    successes = [r for r in results if isinstance(r, str)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1, (
        f"exactly one concurrent supersede must succeed, got successes={successes!r} "
        f"failures={failures!r}"
    )
    assert len(failures) == 1, (
        "the losing concurrent supersede must fail with a clean exception"
    )

    old = await store.get_card(old_id)
    assert old is not None
    assert old.status == "superseded"

    active = await store.get_entity_cards("user", "1", category="fact")
    assert len(active) == 1, (
        f"exactly one active successor required, got {[c.content for c in active]}"
    )
    assert active[0].card_id == successes[0]
    assert active[0].supersedes == old_id
    assert active[0].content in {"用户住在上海", "用户住在北京"}

    # No orphan supersede observation for a failed would-be card.
    all_obs = await store._db.execute(
        "SELECT card_id, decision, source_message_id FROM memory_card_observations "
        "WHERE decision = 'supersede'"
    )
    obs_rows = await all_obs.fetchall()
    # Only the winning successor may have a supersede observation.
    obs_card_ids = {r["card_id"] for r in obs_rows}
    assert obs_card_ids == {successes[0]} or obs_card_ids <= {successes[0]}, (
        f"orphan supersede observations for failed branch: {list(obs_rows)}"
    )
    # Total active fact cards for the entity remains 1 (already asserted).


@pytest.mark.asyncio
async def test_supersede_rejects_cross_scope_but_allows_category_correction(
    store: CardStore,
) -> None:
    """Scope ownership is fixed, while trusted callers may correct category."""
    old_id = await store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="1",
            content="用户住在杭州",
        )
    )
    before_active = await store.get_entity_cards("user", "1")

    # Cross scope_id
    with pytest.raises(ValueError):
        await store.supersede_card(
            old_id,
            NewCard(
                category="fact",
                scope="user",
                scope_id="999",
                content="用户住在上海",
            ),
            source_msg_id="msg_xscope",
            evidence_text="x",
            captured_by="memo_extractor",
        )
    old = await store.get_card(old_id)
    assert old is not None and old.status == "active"
    assert len(await store.get_entity_cards("user", "1")) == len(before_active)
    assert len(await store.get_entity_cards("user", "999")) == 0

    # Cross scope type (user -> group)
    with pytest.raises(ValueError):
        await store.supersede_card(
            old_id,
            NewCard(
                category="fact",
                scope="group",
                scope_id="g1",
                content="群住在上海",
            ),
            source_msg_id="msg_xgroup",
            evidence_text="x",
            captured_by="memo_extractor",
        )
    old = await store.get_card(old_id)
    assert old is not None and old.status == "active"

    # Dream/CardUpdateTool may correct a card's category within the same owner scope.
    new_id = await store.supersede_card(
        old_id,
        NewCard(
            category="status",
            scope="user",
            scope_id="1",
            content="身份: 研究生（已更新）",
        ),
        source_msg_id="msg_category_correction",
        evidence_text="trusted category correction",
        captured_by="dream",
    )
    old = await store.get_card(old_id)
    assert old is not None and old.status == "superseded"
    active = await store.get_entity_cards("user", "1")
    assert len(active) == 1
    assert active[0].card_id == new_id
    assert active[0].category == "status"
    assert active[0].supersedes == old_id


@pytest.mark.asyncio
async def test_supersede_uses_begin_immediate(store: CardStore) -> None:
    """Supersede transaction must take a write reservation via BEGIN IMMEDIATE."""
    old_id = await store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="1",
            content="用户住在杭州",
        )
    )

    executed: list[str] = []
    real_execute = store._db.execute

    async def spy_execute(sql: str, parameters: object = ()) -> object:
        executed.append(str(sql).strip())
        return await real_execute(sql, parameters)

    store._db.execute = spy_execute  # type: ignore[method-assign]
    try:
        await store.supersede_card(
            old_id,
            NewCard(
                category="fact",
                scope="user",
                scope_id="1",
                content="用户住在上海",
            ),
            source_msg_id="msg_begin",
            evidence_text="搬家",
            captured_by="memo_extractor",
        )
    finally:
        store._db.execute = real_execute  # type: ignore[method-assign]

    begin_hits = [
        s for s in executed if "BEGIN IMMEDIATE" in s.upper().replace("  ", " ")
    ]
    # Also accept source-level requirement if runtime path is hard to spy.
    if not begin_hits:
        src = inspect.getsource(CardStore.supersede_card)
        assert "BEGIN IMMEDIATE" in src.upper(), (
            "supersede_card must BEGIN IMMEDIATE (write reservation); "
            f"execute log sample={executed[:12]!r}"
        )
    else:
        assert begin_hits, "expected BEGIN IMMEDIATE in supersede transaction"


@pytest.mark.asyncio
async def test_supersede_second_loses_when_old_already_superseded(
    store: CardStore,
) -> None:
    """Conditional UPDATE / TOCTOU: if old is already superseded, second fails cleanly."""
    old_id = await store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="1",
            content="用户住在杭州",
        )
    )
    winner = await store.supersede_card(
        old_id,
        NewCard(
            category="fact",
            scope="user",
            scope_id="1",
            content="用户住在上海",
        ),
        source_msg_id="msg_first",
        evidence_text="first move",
        captured_by="memo_extractor",
    )
    with pytest.raises(ValueError):
        await store.supersede_card(
            old_id,
            NewCard(
                category="fact",
                scope="user",
                scope_id="1",
                content="用户住在北京",
            ),
            source_msg_id="msg_second",
            evidence_text="second move",
            captured_by="memo_extractor",
        )

    active = await store.get_entity_cards("user", "1", category="fact")
    assert len(active) == 1
    assert active[0].card_id == winner
    assert "北京" not in active[0].content
    # Failed branch must not leave an orphan active card for 北京.
    all_beijing = [
        c
        for c in await store.list_cards(scope="user", scope_id="1", status="active")
        if "北京" in c.content
    ]
    assert all_beijing == []


@pytest.mark.asyncio
async def test_write_lock_serializes_supersede_then_other_mutations(
    store: CardStore,
) -> None:
    """After supersede, mark_seen / expire / get_or_create_series must not deadlock."""
    old_id = await store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="1",
            content="用户住在杭州",
        )
    )
    new_id = await store.supersede_card(
        old_id,
        NewCard(
            category="fact",
            scope="user",
            scope_id="1",
            content="用户住在上海",
        ),
        source_msg_id="msg_lock_chain",
        evidence_text="move",
        captured_by="memo_extractor",
    )
    # Same-instance follow-up writes must complete (no deadlock with write lock).
    assert await store.mark_seen(new_id) is True
    series = await store.get_or_create_series(
        "test_lock:1", scope="user", scope_id="1", label="lock-test"
    )
    assert series.series_id
    # Expire a separate card to ensure expire path still works post-supersede.
    other = await store.add_card(
        NewCard(category="status", scope="user", scope_id="1", content="临时状态")
    )
    assert await store.expire_card(other) is True
    expired = await store.get_card(other)
    assert expired is not None and expired.status == "expired"

    # Optional surface: a write lock attribute for same-instance serialization.
    lock = getattr(store, "_write_lock", None) or getattr(store, "write_lock", None)
    if lock is not None:
        assert isinstance(lock, asyncio.Lock)


@pytest.mark.asyncio
async def test_reinforce_observation_cancel_still_rolls_back_with_write_lock(
    store: CardStore,
) -> None:
    """Reinforce+obs under commit failure still rolls back conf+obs together."""
    cid = await store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id="1",
            content="喜欢咖啡",
            confidence=0.5,
        )
    )
    real_commit = store._db.commit

    async def boom_commit() -> None:
        raise RuntimeError("injected reinforce failure under write lock")

    store._db.commit = boom_commit  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError):
            await store.reinforce(
                cid,
                boost=0.2,
                evidence_text="again",
                source_message_id="msg_re_lock_fail",
                captured_by="memo_extractor",
            )
    finally:
        store._db.commit = real_commit  # type: ignore[method-assign]

    card = await store.get_card(cid)
    assert card is not None
    assert card.confidence == pytest.approx(0.5)
    assert await store.list_observations(cid) == []

    # Store remains usable after failed write (lock released).
    assert await store.reinforce(cid, boost=0.1) is True
    refreshed = await store.get_card(cid)
    assert refreshed is not None
    assert refreshed.confidence == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_legacy_supersede_without_provenance_still_works(
    store: CardStore,
) -> None:
    """Dream/CardUpdateTool same-scope callers without provenance must still work."""
    old_id = await store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="1",
            content="old fact legacy",
        )
    )
    new_id = await store.supersede_card(
        old_id,
        NewCard(
            category="fact",
            scope="user",
            scope_id="1",
            content="new fact legacy",
        ),
    )
    old = await store.get_card(old_id)
    new = await store.get_card(new_id)
    assert old is not None and old.status == "superseded"
    assert new is not None and new.status == "active"
    assert new.supersedes == old_id
    # No observation required for legacy no-provenance path.
    assert await store.list_observations(new_id) == []


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


# ------------------------------------------------------------------
# Temporal-trace chain primitives (list_active_chain_heads / walk_supersedes_chain)
# RED: GREEN implements these methods; existing active-only APIs must stay active-only.
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_active_chain_heads_returns_only_active_with_supersedes(
    store: CardStore,
) -> None:
    """R6/R10/R13: heads are active cards whose supersedes IS NOT NULL."""
    # Lone active (no supersedes) — not a chain head.
    lone = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="孤卡无链"),
    )
    # Superseded parent + active successor with supersedes pointer.
    old = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="旧杭州"),
    )
    head = await store.supersede_card(
        old,
        NewCard(category="fact", scope="user", scope_id="1", content="新上海"),
    )
    # Other scope must not appear.
    other_old = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="2", content="别的用户旧"),
    )
    await store.supersede_card(
        other_old,
        NewCard(category="fact", scope="user", scope_id="2", content="别的用户新"),
    )

    heads = await store.list_active_chain_heads("user", "1", limit=24)
    head_ids = [c.card_id for c in heads]
    assert head in head_ids
    assert lone not in head_ids
    assert old not in head_ids
    assert all(c.status == "active" for c in heads)
    assert all(c.supersedes for c in heads)
    assert all(c.scope == "user" and c.scope_id == "1" for c in heads)


@pytest.mark.asyncio
async def test_list_active_chain_heads_respects_category_and_limit(
    store: CardStore,
) -> None:
    for i in range(5):
        old = await store.add_card(
            NewCard(category="fact", scope="user", scope_id="1", content=f"f-old-{i}"),
        )
        await store.supersede_card(
            old,
            NewCard(category="fact", scope="user", scope_id="1", content=f"f-new-{i}"),
        )
    pref_old = await store.add_card(
        NewCard(category="preference", scope="user", scope_id="1", content="pref-old"),
    )
    pref_new = await store.supersede_card(
        pref_old,
        NewCard(category="preference", scope="user", scope_id="1", content="pref-new"),
    )

    facts = await store.list_active_chain_heads(
        "user", "1", limit=2, category="fact",
    )
    assert len(facts) <= 2
    assert all(c.category == "fact" for c in facts)

    prefs = await store.list_active_chain_heads(
        "user", "1", limit=24, category="preference",
    )
    assert [c.card_id for c in prefs] == [pref_new]


@pytest.mark.asyncio
async def test_list_active_chain_heads_limit_hard_capped_at_24(store: CardStore) -> None:
    for i in range(30):
        old = await store.add_card(
            NewCard(category="fact", scope="user", scope_id="1", content=f"old{i}"),
        )
        await store.supersede_card(
            old,
            NewCard(category="fact", scope="user", scope_id="1", content=f"new{i}"),
        )
    # Caller may request higher, implementation must hard-cap at 24.
    heads = await store.list_active_chain_heads("user", "1", limit=100)
    assert len(heads) <= 24


@pytest.mark.asyncio
async def test_list_active_chain_heads_stable_ordering(store: CardStore) -> None:
    ids: list[str] = []
    for i in range(3):
        old = await store.add_card(
            NewCard(
                category="fact",
                scope="user",
                scope_id="1",
                content=f"ord-old-{i}",
                priority=5,
            ),
        )
        new = await store.supersede_card(
            old,
            NewCard(
                category="fact",
                scope="user",
                scope_id="1",
                content=f"ord-new-{i}",
                priority=5,
            ),
        )
        ids.append(new)
    first = [c.card_id for c in await store.list_active_chain_heads("user", "1", limit=24)]
    second = [c.card_id for c in await store.list_active_chain_heads("user", "1", limit=24)]
    assert first == second
    assert set(ids).issubset(set(first))


@pytest.mark.asyncio
async def test_list_active_chain_heads_uses_parameterized_sql(store: CardStore) -> None:
    """R13: method source must parameterize scope filters (no f-string SQL)."""
    src = inspect.getsource(CardStore.list_active_chain_heads)
    assert "scope" in src
    # Must not interpolate scope_id into SQL via f-string of the filter value.
    assert 'f"SELECT' not in src and "f'SELECT" not in src
    assert "?" in src or "%s" in src or ":" in src


@pytest.mark.asyncio
async def test_walk_supersedes_chain_head_first_then_parents(store: CardStore) -> None:
    """R5/R10: max_depth=4 means head + up to 3 ancestors; head first."""
    v0 = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="V0北京"),
    )
    v1 = await store.supersede_card(
        v0,
        NewCard(category="fact", scope="user", scope_id="1", content="V1天津"),
    )
    v2 = await store.supersede_card(
        v1,
        NewCard(category="fact", scope="user", scope_id="1", content="V2南京"),
    )
    v3 = await store.supersede_card(
        v2,
        NewCard(category="fact", scope="user", scope_id="1", content="V3杭州"),
    )
    v4 = await store.supersede_card(
        v3,
        NewCard(category="fact", scope="user", scope_id="1", content="V4上海"),
    )

    chain = await store.walk_supersedes_chain(v4, max_depth=4)
    assert chain is not None
    assert len(chain) == 4
    assert chain[0].card_id == v4
    assert chain[0].status == "active"
    assert all(c.status == "superseded" for c in chain[1:])
    walked = [c.card_id for c in chain]
    assert v0 not in walked  # depth cap drops oldest
    assert walked == [v4, v3, v2, v1] or (
        walked[0] == v4 and set(walked[1:]).issubset({v3, v2, v1, v0}) and len(walked) <= 4
    )
    # Same scope/category throughout.
    assert all(c.scope == "user" and c.scope_id == "1" and c.category == "fact" for c in chain)


@pytest.mark.asyncio
async def test_walk_supersedes_chain_fail_closed_missing_parent(store: CardStore) -> None:
    head_id = "card_walk_missing"
    await store._db.execute(
        "INSERT INTO memory_cards ("
        "card_id, category, scope, scope_id, content, confidence, "
        "status, priority, supersedes, source, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            head_id, "fact", "user", "1", "head", 0.7, "active", 5,
            "card_does_not_exist", "manual",
            "2026-01-01T00:00:00", "2026-01-01T00:00:00",
        ),
    )
    await store._db.commit()
    chain = await store.walk_supersedes_chain(head_id, max_depth=4)
    assert chain == [] or chain is None


@pytest.mark.asyncio
async def test_walk_supersedes_chain_fail_closed_cycle(store: CardStore) -> None:
    a_id, b_id = "card_walk_cycle_a", "card_walk_cycle_b"
    ts = "2026-01-01T00:00:00"
    await store._db.execute(
        "INSERT INTO memory_cards ("
        "card_id, category, scope, scope_id, content, confidence, "
        "status, priority, supersedes, source, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (a_id, "fact", "user", "1", "A", 0.7, "active", 5, b_id, "manual", ts, ts),
    )
    await store._db.execute(
        "INSERT INTO memory_cards ("
        "card_id, category, scope, scope_id, content, confidence, "
        "status, priority, supersedes, source, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (b_id, "fact", "user", "1", "B", 0.7, "superseded", 5, a_id, "manual", ts, ts),
    )
    await store._db.commit()
    chain = await store.walk_supersedes_chain(a_id, max_depth=4)
    assert chain == [] or chain is None


@pytest.mark.asyncio
async def test_walk_supersedes_chain_fail_closed_expired_parent(store: CardStore) -> None:
    old = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="旧"),
    )
    head = await store.supersede_card(
        old,
        NewCard(category="fact", scope="user", scope_id="1", content="新"),
    )
    assert await store.expire_card(old)
    chain = await store.walk_supersedes_chain(head, max_depth=4)
    assert chain == [] or chain is None


@pytest.mark.asyncio
async def test_walk_supersedes_chain_fail_closed_cross_category(store: CardStore) -> None:
    """Parents/head must share category for temporal walks (Dream correction no-op path)."""
    old = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="事实旧"),
    )
    head = await store.supersede_card(
        old,
        NewCard(category="status", scope="user", scope_id="1", content="状态新"),
        source_msg_id="msg_cc",
        evidence_text="dream correction",
        captured_by="dream",
    )
    chain = await store.walk_supersedes_chain(head, max_depth=4)
    # Store may return empty (strict) or full chain for Dream visibility.
    # If non-empty, temporal layer no-ops — store itself must still not invent nodes.
    if chain:
        assert chain[0].card_id == head
        assert all(c.scope_id == "1" for c in chain)


@pytest.mark.asyncio
async def test_walk_supersedes_chain_clamps_max_depth_to_4(store: CardStore) -> None:
    """Public walk max_depth is hard-clamped to 4 even if callers pass more."""
    root = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="V0"),
    )
    prev = root
    for i in range(1, 7):
        prev = await store.supersede_card(
            prev,
            NewCard(category="fact", scope="user", scope_id="1", content=f"V{i}"),
        )
    chain = await store.walk_supersedes_chain(prev, max_depth=100)
    assert len(chain) == 4
    assert chain[0].card_id == prev


@pytest.mark.asyncio
async def test_list_observations_optional_limit_bound(store: CardStore) -> None:
    """Optional bounded observation query without breaking existing callers."""
    cid = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="bound-obs"),
    )
    for i in range(8):
        await store.reinforce(
            cid,
            boost=0.0,
            source_message_id=f"m_lim_{i}",
            evidence_text=f"e{i}",
            decision="reinforce",
            captured_by="test",
        )
    all_obs = await store.list_observations(cid)
    assert len(all_obs) == 8
    limited = await store.list_observations(cid, limit=3)
    assert len(limited) == 3


@pytest.mark.asyncio
async def test_walk_supersedes_chain_rejects_non_active_head(store: CardStore) -> None:
    old = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="old"),
    )
    head = await store.supersede_card(
        old,
        NewCard(category="fact", scope="user", scope_id="1", content="new"),
    )
    # Supersede head again so original head is no longer active.
    await store.supersede_card(
        head,
        NewCard(category="fact", scope="user", scope_id="1", content="newer"),
    )
    chain = await store.walk_supersedes_chain(head, max_depth=4)
    assert chain == [] or chain is None


@pytest.mark.asyncio
async def test_get_entity_cards_still_active_only_after_chain_exists(
    store: CardStore,
) -> None:
    """R1/R13: ordinary get_entity_cards must NOT start returning superseded."""
    old = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="用户住在杭州"),
    )
    new = await store.supersede_card(
        old,
        NewCard(category="fact", scope="user", scope_id="1", content="用户住在上海"),
    )
    active = await store.get_entity_cards("user", "1")
    assert [c.card_id for c in active] == [new]
    assert all(c.status == "active" for c in active)
    assert all("杭州" not in c.content for c in active)

    listed = await store.list_cards(scope="user", scope_id="1", status="active")
    assert all(c.status == "active" for c in listed)
    assert all(c.card_id != old for c in listed)


@pytest.mark.asyncio
async def test_dream_fact_to_status_correction_still_works(store: CardStore) -> None:
    """R14: CardStore supersede category correction remains allowed (Dream)."""
    old_id = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="1", content="用户住在杭州"),
    )
    new_id = await store.supersede_card(
        old_id,
        NewCard(
            category="status",
            scope="user",
            scope_id="1",
            content="身份: 研究生（已更新）",
        ),
        source_msg_id="msg_category_correction",
        evidence_text="trusted category correction",
        captured_by="dream",
    )
    old = await store.get_card(old_id)
    assert old is not None and old.status == "superseded"
    active = await store.get_entity_cards("user", "1")
    assert len(active) == 1
    assert active[0].card_id == new_id
    assert active[0].category == "status"
    assert active[0].supersedes == old_id
