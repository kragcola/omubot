"""RED contracts for production typed-ref resolver ports.

Expected production methods (currently missing → AttributeError):

- ConversationArchive.get_messages_by_pks(message_pks, *, chat_type=None, chat_id=None)
- CardStore.find_by_source_message_ids(message_ids, *, allowed_scopes=None)
- KnowledgeGraphService.find_fact_ids_by_evidence_refs(evidence_ids, *, allowed_scopes=None)

Scope isolation, active-only filtering, dedupe, and stable ordering are
encoded as behavior tests against real temporary stores.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, cast

import pytest

from services.conversation_archive import ConversationArchive
from services.knowledge_graph import KnowledgeGraphService
from services.memory.card_store import CardStore, NewCard

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def archive(tmp_path: Path):
    store = ConversationArchive(db_path=str(tmp_path / "messages.db"))
    await store.init()
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
async def card_store(tmp_path: Path):
    store = CardStore(db_path=str(tmp_path / "cards.db"))
    await store.init()
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
async def kg(tmp_path: Path):
    service = KnowledgeGraphService(tmp_path / "kg.db")
    await service.init()
    try:
        yield service
    finally:
        await service.close()


# ---------------------------------------------------------------------------
# 1) ConversationArchive.get_messages_by_pks
# ---------------------------------------------------------------------------


def test_conversation_archive_exposes_get_messages_by_pks() -> None:
    assert callable(getattr(ConversationArchive, "get_messages_by_pks", None)), (
        "ConversationArchive must expose async get_messages_by_pks"
    )
    sig = inspect.signature(ConversationArchive.get_messages_by_pks)
    params = sig.parameters
    assert "message_pks" in params or len(params) >= 2
    # Scope kwargs (keyword-only)
    assert "chat_type" in params
    assert "chat_id" in params


@pytest.mark.asyncio
async def test_get_messages_by_pks_empty_and_invalid_return_empty(
    archive: ConversationArchive,
) -> None:
    method = getattr(archive, "get_messages_by_pks", None)
    assert callable(method)
    method_fn = cast(Any, method)

    assert await method_fn([]) == []
    assert await method_fn(None) == []  # type: ignore[arg-type]
    assert await method_fn([0, -1, "x", None, 1.5]) == []  # type: ignore[list-item]


@pytest.mark.asyncio
async def test_get_messages_by_pks_pk_coercion_rejects_bool_float_with_seeded_pk1(
    archive: ConversationArchive,
) -> None:
    """Seeded PK 1 must not be selected by bool/float int() fake-greens.

    Contract for message_pks items:
    - accept positive int and full-decimal digit strings (leading zeros ok)
    - reject bool, float (including 1.0), None, non-decimal strings, 0/negative
    - non-iterable scalar input returns [] (no TypeError)
    - generators and duplicates are supported
    """
    pk1 = await archive.record(
        group_id="coercion_g",
        role="user",
        speaker="seed(1)",
        content_text="seed pk1",
        content_json=None,
        message_id=7001,
        created_at=1.0,
    )
    assert pk1 == 1, "fixture must land on message_pk=1 so fake-greens are observable"

    # Valid: positive int + full-decimal string (leading zeros normalized)
    for good in ([1], ["1"], ["01"], ["001"]):
        rows = await archive.get_messages_by_pks(good)
        assert [int(r["message_pk"]) for r in rows] == [1], f"expected hit for {good!r}"

    # Invalid coercions that Python int() would map onto 1 without a seed
    for bad in ([True], [1.5], [1.9], [1.0], [False], [0], [-1], [None], ["x"], ["1.5"], ["1e1"], ["²"], [""]):
        rows = await archive.get_messages_by_pks(bad)  # type: ignore[arg-type]
        assert rows == [], f"must reject {bad!r}, got {rows!r}"

    # Scalar non-iterable must not raise TypeError
    assert await archive.get_messages_by_pks(1) == []  # type: ignore[arg-type]
    assert await archive.get_messages_by_pks(True) == []  # type: ignore[arg-type]
    assert await archive.get_messages_by_pks(1.0) == []  # type: ignore[arg-type]

    # Generator + duplicates: keep first positive int, drop bool/float noise
    def _gen():
        yield True
        yield 1
        yield 1
        yield "01"
        yield 1.5
        yield "1"

    rows = await archive.get_messages_by_pks(_gen())
    assert [int(r["message_pk"]) for r in rows] == [1]


def test_promoter_positive_ints_rejects_bool_and_float() -> None:
    """Pure contract for promoter PK coercion (shared int() fake-green surface)."""
    from services.memory_consolidator.promoter import _positive_ints

    assert _positive_ints([1, "02", "001", 3]) == [1, 2, 3]
    assert _positive_ints([True, False, 1.0, 1.5, 1.9, None, "x", "1.5", "²", 0, -1]) == []
    assert _positive_ints([True, 1, 1.0, "01", 2, "2", False]) == [1, 2]
    # Dedupe preserves first-seen order
    assert _positive_ints([5, "05", 5, "0005"]) == [5]


@pytest.mark.asyncio
async def test_get_messages_by_pks_dedupes_and_orders_ascending(
    archive: ConversationArchive,
) -> None:
    pk_a = await archive.record(
        group_id="100",
        role="user",
        speaker="小明(1)",
        content_text="a",
        content_json=None,
        message_id=9001,
        created_at=1.0,
    )
    pk_b = await archive.record(
        group_id="100",
        role="user",
        speaker="小红(2)",
        content_text="b",
        content_json=None,
        message_id=9002,
        created_at=2.0,
    )
    assert pk_a is not None and pk_b is not None
    assert pk_a < pk_b

    rows = await archive.get_messages_by_pks([pk_b, pk_a, pk_a, pk_b])
    assert [int(r["message_pk"]) for r in rows] == [pk_a, pk_b]
    for row in rows:
        assert "message_pk" in row
        assert "chat_type" in row
        assert "chat_id" in row
        assert "role" in row
        assert "speaker" in row
        assert "message_id" in row  # platform_message_id AS message_id
    assert {int(r["message_id"]) for r in rows} == {9001, 9002}


@pytest.mark.asyncio
async def test_get_messages_by_pks_source_scope_filters_cross_group_and_private(
    archive: ConversationArchive,
) -> None:
    """Poisoned PKs from another group / private session must not leak."""
    pk_target = await archive.record(
        group_id="456",
        role="user",
        speaker="目标(10)",
        content_text="in group 456",
        content_json=None,
        message_id=111,
        created_at=1.0,
    )
    pk_other_group = await archive.record(
        group_id="999",
        role="user",
        speaker="外人(20)",
        content_text="poison other group",
        content_json=None,
        message_id=222,
        created_at=2.0,
    )
    pk_private = await archive.record(
        group_id="session:u42",
        role="user",
        speaker="私聊(42)",
        content_text="poison private",
        content_json=None,
        message_id=333,
        created_at=3.0,
    )
    assert pk_target and pk_other_group and pk_private

    rows = await archive.get_messages_by_pks(
        [pk_target, pk_other_group, pk_private],
        chat_type="group",
        chat_id="456",
    )
    assert len(rows) == 1
    assert int(rows[0]["message_pk"]) == int(pk_target)
    assert rows[0]["chat_type"] == "group"
    assert str(rows[0]["chat_id"]) == "456"
    assert int(rows[0]["message_id"]) == 111


# ---------------------------------------------------------------------------
# 2) CardStore.find_by_source_message_ids
# ---------------------------------------------------------------------------


def test_card_store_exposes_find_by_source_message_ids() -> None:
    assert callable(getattr(CardStore, "find_by_source_message_ids", None)), (
        "CardStore must expose async find_by_source_message_ids"
    )
    sig = inspect.signature(CardStore.find_by_source_message_ids)
    assert "allowed_scopes" in sig.parameters


@pytest.mark.asyncio
async def test_find_by_source_message_ids_empty_active_only_dedupe_order(
    card_store: CardStore,
) -> None:
    method = getattr(card_store, "find_by_source_message_ids", None)
    assert callable(method)
    method_fn = cast(Any, method)
    assert await method_fn([]) == []

    active_a = await card_store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="456",
            content="active A",
            priority=1,
        ),
        source_msg_id="9001",
    )
    active_b = await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="123",
            content="active B",
            priority=2,
        ),
        source_msg_id="9002",
    )
    superseded = await card_store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="456",
            content="superseded",
        ),
        source_msg_id="9001",
    )
    await card_store.update_card(superseded, status="superseded")
    expired = await card_store.add_card(
        NewCard(
            category="event",
            scope="group",
            scope_id="456",
            content="expired",
        ),
        source_msg_id="9002",
    )
    await card_store.expire_card(expired)

    # Duplicate message ids in input; superseded/expired excluded.
    cards = await card_store.find_by_source_message_ids(
        ["9002", "9001", "9001", 9002],
        allowed_scopes={("group", "456"), ("user", "123")},
    )
    card_ids = [
        c.card_id if hasattr(c, "card_id") else c["card_id"]  # type: ignore[index]
        for c in cards
    ]
    assert card_ids.count(active_a) == 1
    assert card_ids.count(active_b) == 1
    assert superseded not in card_ids
    assert expired not in card_ids
    # Stable deterministic ordering (e.g. by card_id ascending or input+card_id)
    assert card_ids == sorted(card_ids) or card_ids == [active_a, active_b] or card_ids == [
        active_b,
        active_a,
    ]
    # Re-call is stable
    again = await card_store.find_by_source_message_ids(
        ["9002", "9001", "9001", 9002],
        allowed_scopes={("group", "456"), ("user", "123")},
    )
    again_ids = [
        c.card_id if hasattr(c, "card_id") else c["card_id"]  # type: ignore[index]
        for c in again
    ]
    assert again_ids == card_ids


@pytest.mark.asyncio
async def test_find_by_source_message_ids_scope_filter_blocks_other_group(
    card_store: CardStore,
) -> None:
    """Same source_msg_id in another group must not match restricted scopes."""
    local = await card_store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="456",
            content="local group card",
        ),
        source_msg_id="shared_mid",
    )
    poison = await card_store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="999",
            content="other group card",
        ),
        source_msg_id="shared_mid",
    )
    user_card = await card_store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id="123",
            content="user scoped",
        ),
        source_msg_id="shared_mid",
    )

    allowed = {("group", "456"), ("user", "123")}
    cards = await card_store.find_by_source_message_ids(
        ["shared_mid"],
        allowed_scopes=allowed,
    )
    ids = {
        c.card_id if hasattr(c, "card_id") else c["card_id"]  # type: ignore[index]
        for c in cards
    }
    assert local in ids
    assert user_card in ids
    assert poison not in ids


# ---------------------------------------------------------------------------
# 3) KnowledgeGraphService.find_fact_ids_by_evidence_refs
# ---------------------------------------------------------------------------


def test_knowledge_graph_service_exposes_find_fact_ids_by_evidence_refs() -> None:
    assert callable(
        getattr(KnowledgeGraphService, "find_fact_ids_by_evidence_refs", None)
    ), "KnowledgeGraphService must expose async find_fact_ids_by_evidence_refs"
    sig = inspect.signature(KnowledgeGraphService.find_fact_ids_by_evidence_refs)
    assert "allowed_scopes" in sig.parameters


@pytest.mark.asyncio
async def test_find_fact_ids_by_evidence_refs_empty_active_only_dedupe_order(
    kg: KnowledgeGraphService,
) -> None:
    method = getattr(kg, "find_fact_ids_by_evidence_refs", None)
    assert callable(method)
    method_fn = cast(Any, method)
    assert await method_fn([]) == []

    active = await kg.submit_fact_candidate(
        subject="用户123",
        predicate="提到",
        object="雨",
        confidence=0.9,
        source="test",
        evidence={"id": "9001", "quote": "下雨了"},
        scope="group",
        scope_id="456",
        promote_directly=True,
    )
    assert active is not None
    active_id = cast(Any, active).fact_id

    # Same evidence also attached to a non-active fact in-scope — must not return.
    store = kg._store  # test-only access for status variants
    superseded = await store.add_fact(
        subject="用户123",
        predicate="旧关系",
        object="旧对象",
        confidence=0.5,
        source="test",
        evidence={"id": "9001", "quote": "old"},
        status="superseded",
        scope="group",
        scope_id="456",
    )
    rejected = await store.add_fact(
        subject="用户123",
        predicate="拒",
        object="绝",
        confidence=0.5,
        source="test",
        evidence={"id": "9001", "quote": "rej"},
        status="rejected",
        scope="group",
        scope_id="456",
    )
    pending = await store.add_fact(
        subject="用户123",
        predicate="待",
        object="审",
        confidence=0.5,
        source="test",
        evidence={"id": "9001", "quote": "pend"},
        status="pending",
        scope="group",
        scope_id="456",
    )

    fact_ids = await kg.find_fact_ids_by_evidence_refs(
        ["9001", "9001", 9001],
        allowed_scopes={("group", "456")},
    )
    assert fact_ids.count(active_id) == 1
    assert superseded.fact_id not in fact_ids
    assert rejected.fact_id not in fact_ids
    assert pending.fact_id not in fact_ids
    # Stable deterministic order across calls
    again = await kg.find_fact_ids_by_evidence_refs(
        ["9001", "9001", 9001],
        allowed_scopes={("group", "456")},
    )
    assert again == fact_ids


@pytest.mark.asyncio
async def test_find_fact_ids_by_evidence_refs_scope_filter_blocks_other_group(
    kg: KnowledgeGraphService,
) -> None:
    local = await kg.submit_fact_candidate(
        subject="用户1",
        predicate="在",
        object="本群",
        confidence=0.9,
        source="test",
        evidence={"id": "ev_shared", "quote": "local"},
        scope="group",
        scope_id="456",
        promote_directly=True,
    )
    poison = await kg.submit_fact_candidate(
        subject="用户2",
        predicate="在",
        object="他群",
        confidence=0.9,
        source="test",
        evidence={"id": "ev_shared", "quote": "poison"},
        scope="group",
        scope_id="999",
        promote_directly=True,
    )
    assert local is not None and poison is not None

    fact_ids = await kg.find_fact_ids_by_evidence_refs(
        ["ev_shared"],
        allowed_scopes={("group", "456"), ("user", "123")},
    )
    assert cast(Any, local).fact_id in fact_ids
    assert cast(Any, poison).fact_id not in fact_ids
