"""RED integration: EpisodePromoter scope-safe typed refs via real stores.

Unlike tests/test_memory_consolidator_promote.py (in-memory fakes without scope
kwargs), these tests wire real temporary ConversationArchive / CardStore /
KnowledgeGraphService / EntityAliasStore / EpisodeStore and require the
production promoter to:

- request archive rows with chat_type='group', chat_id=candidate.group_id
- pass allowed_scopes for cards/facts (group/<id> + user/<uids> proven by
  scope-filtered archive rows)
- refuse unscoped enrichment when group_id is non-numeric
- never hydrate message/user/card/fact from poisoned cross-group PKs / evidence
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, cast

import pytest

from services.conversation_archive import ConversationArchive
from services.episodic import EpisodeStore
from services.knowledge_graph import KnowledgeGraphService
from services.memory.card_store import CardStore, NewCard
from services.memory.entity_alias_store import EntityAliasStore
from services.memory_consolidator import ConsolidatorCandidatesStore, EpisodePromoter

# ---------------------------------------------------------------------------
# Fixtures — real temp stores
# ---------------------------------------------------------------------------


@pytest.fixture
async def candidates_store(tmp_path: Path):
    s = ConsolidatorCandidatesStore(str(tmp_path / "consolidator.db"))
    await s.init()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
async def episode_store(tmp_path: Path):
    s = EpisodeStore(str(tmp_path / "episodic.db"))
    await s.init()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
async def archive(tmp_path: Path):
    s = ConversationArchive(db_path=str(tmp_path / "messages.db"))
    await s.init()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
async def card_store(tmp_path: Path):
    s = CardStore(db_path=str(tmp_path / "cards.db"))
    await s.init()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
async def kg(tmp_path: Path):
    s = KnowledgeGraphService(tmp_path / "kg.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
async def alias_store(tmp_path: Path):
    s = EntityAliasStore(tmp_path / "entity_aliases.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


async def _seed_approved(
    store: ConsolidatorCandidatesStore,
    *,
    group_id: str = "456",
    source_message_pks: list[Any] | None = None,
    payload: dict | None = None,
) -> str:
    run_id = await store.start_run(
        triggered_by="test", group_id=group_id, scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id,
        domain="episode",
        scope="group",
        group_id=group_id,
        source_message_pks=list(
            source_message_pks if source_message_pks is not None else [11, 12]
        ),
        payload=payload
        or {
            "situation": "group banter",
            "observed_context": "evening",
            "action_taken": "joked",
            "outcome_signal": "positive",
            "reflection": "humor works",
        },
        confidence=0.75,
    )
    await store.update_candidate_cluster(cid, "cluster_real")
    await store.decide_candidate(
        cid, state="approved", decided_by="alice", reason="lgtm",
    )
    return cid


def _promoter(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
    **ports: Any,
) -> EpisodePromoter:
    return EpisodePromoter(
        candidates_store=candidates_store,
        episode_store=episode_store,
        **ports,
    )


# ---------------------------------------------------------------------------
# Spy wrappers — assert production passes scope kwargs
# ---------------------------------------------------------------------------


class _ArchiveScopeSpy:
    """Delegates to real archive; records get_messages_by_pks call kwargs."""

    def __init__(self, inner: ConversationArchive) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    async def get_messages_by_pks(self, message_pks, **kwargs):
        self.calls.append({"message_pks": list(message_pks), **kwargs})
        return await self.inner.get_messages_by_pks(message_pks, **kwargs)


class _CardScopeSpy:
    def __init__(self, inner: CardStore) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    async def find_by_source_message_ids(self, message_ids, **kwargs):
        self.calls.append({"message_ids": list(message_ids), **kwargs})
        return await self.inner.find_by_source_message_ids(message_ids, **kwargs)


class _KgScopeSpy:
    def __init__(self, inner: KnowledgeGraphService) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    async def find_fact_ids_by_evidence_refs(self, evidence_ids, **kwargs):
        self.calls.append({"evidence_ids": list(evidence_ids), **kwargs})
        return await self.inner.find_fact_ids_by_evidence_refs(evidence_ids, **kwargs)


# ---------------------------------------------------------------------------
# Scope-safe happy path with real stores
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_real_stores_links_scoped_message_user_card_fact(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
    archive: ConversationArchive,
    card_store: CardStore,
    kg: KnowledgeGraphService,
    alias_store: EntityAliasStore,
) -> None:
    pk_user = await archive.record(
        group_id="456",
        role="user",
        speaker="小明(123)",
        content_text="今天天气不错",
        content_json=None,
        message_id=9001,
        created_at=1.0,
    )
    pk_bot = await archive.record(
        group_id="456",
        role="assistant",
        speaker="bot",
        content_text="是啊",
        content_json=None,
        message_id=9002,
        created_at=2.0,
    )
    assert pk_user and pk_bot

    card_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="456",
            content="mentioned weather",
        ),
        source_msg_id="9001",
    )
    fact = await kg.submit_fact_candidate(
        subject="用户123",
        predicate="聊到",
        object="天气",
        confidence=0.9,
        source="test",
        evidence={"id": "9001", "quote": "今天天气不错"},
        scope="group",
        scope_id="456",
        promote_directly=True,
    )
    assert fact is not None

    archive_spy = _ArchiveScopeSpy(archive)
    card_spy = _CardScopeSpy(card_store)
    kg_spy = _KgScopeSpy(kg)

    cid = await _seed_approved(
        candidates_store,
        group_id="456",
        source_message_pks=[pk_user, pk_bot],
    )
    promoter = _promoter(
        candidates_store,
        episode_store,
        message_archive=archive_spy,
        card_store=card_spy,
        knowledge_graph=kg_spy,
        entity_alias_store=alias_store,
    )

    result = await promoter.promote(cid, actor="alice")
    assert result.promoted is True
    assert not str(result.skipped_reason).startswith("create_failed")

    # Production must request archive with candidate group scope
    assert len(archive_spy.calls) == 1
    arch_call = archive_spy.calls[0]
    assert arch_call.get("chat_type") == "group"
    assert str(arch_call.get("chat_id")) == "456"

    # Cards/facts must receive allowed scopes including group + proven user
    assert len(card_spy.calls) == 1
    card_scopes = card_spy.calls[0].get("allowed_scopes")
    assert card_scopes is not None
    assert ("group", "456") in card_scopes
    assert ("user", "123") in card_scopes

    assert len(kg_spy.calls) == 1
    kg_scopes = kg_spy.calls[0].get("allowed_scopes")
    assert kg_scopes is not None
    assert ("group", "456") in kg_scopes
    assert ("user", "123") in kg_scopes

    ep = await episode_store.get_episode(result.episode_id)
    assert ep is not None
    links = list(ep.linked_memory_ids)
    assert "entity:group:qq:456" in links
    assert f"message_pk:{pk_user}" in links
    assert f"message_pk:{pk_bot}" in links
    assert "entity:user:qq:123" in links
    assert "message:9001" in links
    assert f"card:{card_id}" in links
    assert f"fact:{cast(Any, fact).fact_id}" in links

    # Alias observed from archive speaker
    resolved = await alias_store.resolve(
        alias="小明", scope="group", scope_id="456",
    )
    assert resolved == "user:qq:123"


@pytest.mark.asyncio
async def test_promote_poisoned_cross_group_pk_does_not_hydrate(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
    archive: ConversationArchive,
    card_store: CardStore,
    kg: KnowledgeGraphService,
    alias_store: EntityAliasStore,
) -> None:
    """Poisoned PK from another group must not hydrate message/user/card/fact."""
    pk_good = await archive.record(
        group_id="456",
        role="user",
        speaker="本地(111)",
        content_text="safe",
        content_json=None,
        message_id=5001,
        created_at=1.0,
    )
    pk_poison = await archive.record(
        group_id="999",
        role="user",
        speaker="毒丸(9999)",
        content_text="cross group poison",
        content_json=None,
        message_id=5002,
        created_at=2.0,
    )
    assert pk_good and pk_poison

    poison_card = await card_store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="999",
            content="poison card",
        ),
        source_msg_id="5002",
    )
    poison_fact = await kg.submit_fact_candidate(
        subject="用户9999",
        predicate="在",
        object="他群",
        confidence=0.9,
        source="test",
        evidence={"id": "5002", "quote": "poison"},
        scope="group",
        scope_id="999",
        promote_directly=True,
    )
    assert poison_fact is not None

    cid = await _seed_approved(
        candidates_store,
        group_id="456",
        source_message_pks=[pk_good, pk_poison],
    )
    promoter = _promoter(
        candidates_store,
        episode_store,
        message_archive=archive,
        card_store=card_store,
        knowledge_graph=kg,
        entity_alias_store=alias_store,
    )
    result = await promoter.promote(cid, actor="alice")
    assert result.promoted is True

    ep = await episode_store.get_episode(result.episode_id)
    assert ep is not None
    links = list(ep.linked_memory_ids)

    # message_pk refs for valid candidate pks still present
    assert f"message_pk:{pk_good}" in links
    assert f"message_pk:{pk_poison}" in links  # raw pk list is preserved

    # But hydrated refs from poison must not appear
    assert "entity:user:qq:9999" not in links
    assert "message:5002" not in links
    assert f"card:{poison_card}" not in links
    assert f"fact:{cast(Any, poison_fact).fact_id}" not in links

    # Good row may still hydrate
    assert "entity:user:qq:111" in links
    assert "message:5001" in links

    # Alias for poison speaker must not land in candidate group scope
    assert (
        await alias_store.resolve(alias="毒丸", scope="group", scope_id="456")
    ) is None


@pytest.mark.asyncio
async def test_promote_same_evidence_other_group_does_not_link_card_or_fact(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
    archive: ConversationArchive,
    card_store: CardStore,
    kg: KnowledgeGraphService,
) -> None:
    pk = await archive.record(
        group_id="456",
        role="user",
        speaker="小明(123)",
        content_text="shared mid",
        content_json=None,
        message_id=7001,
        created_at=1.0,
    )
    assert pk is not None

    local_card = await card_store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="456",
            content="local",
        ),
        source_msg_id="7001",
    )
    foreign_card = await card_store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="999",
            content="foreign",
        ),
        source_msg_id="7001",
    )
    local_fact = await kg.submit_fact_candidate(
        subject="用户123",
        predicate="说",
        object="话",
        confidence=0.9,
        source="test",
        evidence={"id": "7001", "quote": "local"},
        scope="group",
        scope_id="456",
        promote_directly=True,
    )
    foreign_fact = await kg.submit_fact_candidate(
        subject="用户999",
        predicate="说",
        object="话",
        confidence=0.9,
        source="test",
        evidence={"id": "7001", "quote": "foreign"},
        scope="group",
        scope_id="999",
        promote_directly=True,
    )
    assert local_fact is not None and foreign_fact is not None

    cid = await _seed_approved(
        candidates_store, group_id="456", source_message_pks=[pk],
    )
    promoter = _promoter(
        candidates_store,
        episode_store,
        message_archive=archive,
        card_store=card_store,
        knowledge_graph=kg,
    )
    result = await promoter.promote(cid, actor="alice")
    assert result.promoted is True
    ep = await episode_store.get_episode(result.episode_id)
    assert ep is not None
    links = list(ep.linked_memory_ids)
    assert f"card:{local_card}" in links
    assert f"card:{foreign_card}" not in links
    assert f"fact:{cast(Any, local_fact).fact_id}" in links
    assert f"fact:{cast(Any, foreign_fact).fact_id}" not in links


@pytest.mark.asyncio
async def test_promote_superseded_expired_cards_and_non_active_facts_not_linked(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
    archive: ConversationArchive,
    card_store: CardStore,
    kg: KnowledgeGraphService,
) -> None:
    pk = await archive.record(
        group_id="456",
        role="user",
        speaker="小明(123)",
        content_text="status filter",
        content_json=None,
        message_id=8001,
        created_at=1.0,
    )
    assert pk is not None

    active_card = await card_store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="456",
            content="active",
        ),
        source_msg_id="8001",
    )
    super_card = await card_store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="456",
            content="super",
        ),
        source_msg_id="8001",
    )
    await card_store.update_card(super_card, status="superseded")
    exp_card = await card_store.add_card(
        NewCard(
            category="event",
            scope="group",
            scope_id="456",
            content="exp",
        ),
        source_msg_id="8001",
    )
    await card_store.expire_card(exp_card)

    active_fact = await kg.submit_fact_candidate(
        subject="用户123",
        predicate="活",
        object="跃",
        confidence=0.9,
        source="test",
        evidence={"id": "8001", "quote": "a"},
        scope="group",
        scope_id="456",
        promote_directly=True,
    )
    dead_fact = await kg._store.add_fact(
        subject="用户123",
        predicate="死",
        object="寂",
        confidence=0.5,
        source="test",
        evidence={"id": "8001", "quote": "d"},
        status="superseded",
        scope="group",
        scope_id="456",
    )
    assert active_fact is not None

    cid = await _seed_approved(
        candidates_store, group_id="456", source_message_pks=[pk],
    )
    promoter = _promoter(
        candidates_store,
        episode_store,
        message_archive=archive,
        card_store=card_store,
        knowledge_graph=kg,
    )
    result = await promoter.promote(cid, actor="alice")
    assert result.promoted is True
    ep = await episode_store.get_episode(result.episode_id)
    assert ep is not None
    links = list(ep.linked_memory_ids)
    assert f"card:{active_card}" in links
    assert f"card:{super_card}" not in links
    assert f"card:{exp_card}" not in links
    assert f"fact:{cast(Any, active_fact).fact_id}" in links
    assert f"fact:{cast(Any, dead_fact).fact_id}" not in links


class _WorkingArchiveFake:
    """Always returns rows (no production method dependency) so enrichment is possible."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def get_messages_by_pks(self, message_pks, **kwargs):
        self.calls.append({"message_pks": list(message_pks), **kwargs})
        # If production passed scope, filter; if not, return all (unsafe)
        chat_type = kwargs.get("chat_type")
        chat_id = kwargs.get("chat_id")
        out = []
        for row in self.rows:
            if int(row["message_pk"]) not in {int(p) for p in message_pks}:
                continue
            if chat_type is not None and str(row.get("chat_type")) != str(chat_type):
                continue
            if chat_id is not None and str(row.get("chat_id")) != str(chat_id):
                continue
            out.append(row)
        # Unscoped call (no filters): return matching pks regardless of chat
        if chat_type is None and chat_id is None:
            out = [r for r in self.rows if int(r["message_pk"]) in {int(p) for p in message_pks}]
        return out


class _WorkingCardFake:
    def __init__(self, cards: list[dict[str, Any]]) -> None:
        self.cards = cards
        self.calls: list[dict[str, Any]] = []

    async def find_by_source_message_ids(self, message_ids, **kwargs):
        self.calls.append({"message_ids": list(message_ids), **kwargs})
        allowed = kwargs.get("allowed_scopes")
        mids = {str(m) for m in message_ids}
        out = []
        for card in self.cards:
            if str(card.get("source_msg_id")) not in mids:
                continue
            if allowed is not None:
                scope_pair = (str(card.get("scope")), str(card.get("scope_id")))
                if scope_pair not in allowed:
                    continue
            out.append(card)
        return out


class _WorkingKgFake:
    def __init__(self, facts: list[tuple[str, str, str, str]]) -> None:
        # (fact_id, evidence_id, scope, scope_id)
        self.facts = facts
        self.calls: list[dict[str, Any]] = []

    async def find_fact_ids_by_evidence_refs(self, evidence_ids, **kwargs):
        self.calls.append({"evidence_ids": list(evidence_ids), **kwargs})
        allowed = kwargs.get("allowed_scopes")
        eids = {str(e) for e in evidence_ids}
        out: list[str] = []
        for fact_id, evidence_id, scope, scope_id in self.facts:
            if evidence_id not in eids:
                continue
            if allowed is not None and (scope, scope_id) not in allowed:
                continue
            out.append(fact_id)
        return out


@pytest.mark.asyncio
async def test_promote_nonnumeric_group_keeps_message_pk_no_unscoped_enrichment(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
) -> None:
    """Non-numeric group_id may keep message_pk refs but must not enrich unscoped.

    Uses working fakes so missing production resolvers cannot mask the bug:
    current promoter still hydrates without scope when group_id is non-numeric.
    """
    archive = _WorkingArchiveFake(
        [
            {
                "message_pk": 77,
                "chat_type": "group",
                "chat_id": "g1",
                "role": "user",
                "speaker": "小明(123)",
                "message_id": 6001,
            }
        ]
    )
    card_store = _WorkingCardFake(
        [
            {
                "card_id": "card_unscoped",
                "source_msg_id": "6001",
                "scope": "group",
                "scope_id": "g1",
            }
        ]
    )
    kg = _WorkingKgFake([("fact_unscoped", "6001", "group", "g1")])

    cid = await _seed_approved(
        candidates_store,
        group_id="g1",
        source_message_pks=[77],
        payload={
            "situation": "nonnumeric",
            "observed_context": "x",
            "action_taken": "y",
            "outcome_signal": "z",
            "reflection": "r",
        },
    )
    promoter = _promoter(
        candidates_store,
        episode_store,
        message_archive=archive,
        card_store=card_store,
        knowledge_graph=kg,
    )
    result = await promoter.promote(cid, actor="alice")
    assert result.promoted is True
    ep = await episode_store.get_episode(result.episode_id)
    assert ep is not None
    links = list(ep.linked_memory_ids)
    assert "message_pk:77" in links
    assert not any(r.startswith("entity:group:") for r in links)

    # Contract: no unscoped enrichment — no card/fact links and no card/kg calls
    assert "card:card_unscoped" not in links
    assert "fact:fact_unscoped" not in links
    assert not any(r.startswith("card:") for r in links)
    assert not any(r.startswith("fact:") for r in links)
    assert card_store.calls == []
    assert kg.calls == []


@pytest.mark.asyncio
async def test_re_promote_with_real_stores_is_idempotent(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
    archive: ConversationArchive,
    card_store: CardStore,
    kg: KnowledgeGraphService,
    alias_store: EntityAliasStore,
) -> None:
    pk = await archive.record(
        group_id="456",
        role="user",
        speaker="小明(123)",
        content_text="idem",
        content_json=None,
        message_id=4001,
        created_at=1.0,
    )
    assert pk is not None
    await card_store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="456",
            content="idem card",
        ),
        source_msg_id="4001",
    )
    await kg.submit_fact_candidate(
        subject="用户123",
        predicate="幂",
        object="等",
        confidence=0.9,
        source="test",
        evidence={"id": "4001", "quote": "idem"},
        scope="group",
        scope_id="456",
        promote_directly=True,
    )

    cid = await _seed_approved(
        candidates_store, group_id="456", source_message_pks=[pk],
    )
    promoter = _promoter(
        candidates_store,
        episode_store,
        message_archive=archive,
        card_store=card_store,
        knowledge_graph=kg,
        entity_alias_store=alias_store,
    )
    first = await promoter.promote(cid, actor="alice")
    assert first.promoted is True
    first_ep = await episode_store.get_episode(first.episode_id)
    assert first_ep is not None
    first_links = list(first_ep.linked_memory_ids)
    # First promote must have succeeded with typed enrichment (not exception-swallowed min refs)
    assert "entity:user:qq:123" in first_links
    assert "message:4001" in first_links
    assert any(r.startswith("card:") for r in first_links)
    assert any(r.startswith("fact:") for r in first_links)

    second = await promoter.promote(cid, actor="alice")
    assert second.episode_id == first.episode_id
    assert second.skipped_reason == "already_promoted"
    second_ep = await episode_store.get_episode(second.episode_id)
    assert second_ep is not None
    assert list(second_ep.linked_memory_ids) == first_links
    assert len(second_ep.linked_memory_ids) == len(set(second_ep.linked_memory_ids))

    episodes = await episode_store.list_episodes(group_id="456")
    matched = [
        e for e in episodes if str(e.meta.get("consolidator_candidate_id", "")) == cid
    ]
    assert len(matched) == 1


def test_promoter_source_requests_scope_kwargs_on_ports() -> None:
    """Static guard: production promoter must pass scope filters (not bare pks only)."""
    source = inspect.getsource(EpisodePromoter)
    # GREEN will add chat_type/chat_id and allowed_scopes; RED asserts they appear.
    assert "chat_type" in source, (
        "EpisodePromoter must pass chat_type when hydrating archive rows"
    )
    assert "allowed_scopes" in source, (
        "EpisodePromoter must pass allowed_scopes for card/fact resolvers"
    )
