"""Tests for EpisodePromoter — D.1 promote bridge.

Covers the full decision matrix of ``EpisodePromoter.promote``:

- happy path: ``domain="episode"`` + ``state="approved"`` → new approved episode
  with the full ``dry_run → candidate → approved`` audit chain
- domain skip: non-episode candidates no-op with ``skipped_reason``
- state skip: candidate not yet ``approved`` no-op
- missing candidate id no-op
- idempotency: re-promote returns existing ``episode_id``
- audit meta: revision row + ``meta_json`` contains candidate/run linkage
- D2 cancel-path: a ``CancelledError`` mid-promote leaves the
  candidate row in ``approved`` (already written by admin) and the
  episodes table empty (no half-built row).
"""

from __future__ import annotations

import asyncio

import pytest

from services.episodic import EpisodeStore
from services.memory_consolidator import (
    ConsolidatorCandidatesStore,
    EpisodePromoter,
)


@pytest.fixture
async def candidates_store(tmp_path):
    s = ConsolidatorCandidatesStore(str(tmp_path / "consolidator_candidates.db"))
    await s.init()
    yield s
    await s.close()


@pytest.fixture
async def episode_store(tmp_path):
    s = EpisodeStore(str(tmp_path / "episodic.db"))
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def promoter(candidates_store, episode_store):
    return EpisodePromoter(
        candidates_store=candidates_store,
        episode_store=episode_store,
    )


async def _seed_approved_episode_candidate(
    store: ConsolidatorCandidatesStore,
    *,
    group_id: str = "g1",
    payload: dict | None = None,
    confidence: float = 0.7,
) -> str:
    run_id = await store.start_run(
        triggered_by="test", group_id=group_id, scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id,
        domain="episode",
        scope="group",
        group_id=group_id,
        source_message_pks=[101, 102],
        payload=payload or {
            "situation": "user asked about weather",
            "observed_context": "morning, group chat",
            "action_taken": "replied with humor",
            "outcome_signal": "user laughed",
            "reflection": "humor lands when context is light",
        },
        confidence=confidence,
    )
    await store.update_candidate_cluster(cid, "cluster_xyz")
    await store.decide_candidate(
        cid, state="approved", decided_by="alice", reason="lgtm",
    )
    return cid


@pytest.mark.asyncio
async def test_promote_creates_episode(
    promoter: EpisodePromoter,
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    cid = await _seed_approved_episode_candidate(candidates_store)

    result = await promoter.promote(cid, actor="alice")
    assert result.promoted is True
    assert result.skipped_reason == ""
    assert result.episode_id

    episodes = await episode_store.list_episodes(group_id="g1")
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.episode_id == result.episode_id
    assert ep.episode_state == "approved"
    assert ep.source == "consolidator"
    assert ep.situation == "user asked about weather"
    assert ep.observed_context == "morning, group chat"
    assert ep.action_taken == "replied with humor"
    assert ep.outcome_signal == "user laughed"
    assert ep.reflection == "humor lands when context is light"
    assert ep.confidence == 0.7
    assert ep.scope == "group"
    assert ep.group_id == "g1"

    # Audit meta carries the candidate linkage
    assert ep.meta["consolidator_candidate_id"] == cid
    assert ep.meta["normalizer_cluster_id"] == "cluster_xyz"
    assert ep.meta["promoted_by"] == "alice"
    assert ep.meta["source_message_pks"] == [101, 102]

    # The creation revision and both state-machine transitions stay auditable.
    revisions = await episode_store.list_revisions(ep.episode_id)
    assert len(revisions) == 3
    by_action = {revision.action: revision for revision in revisions}
    assert set(by_action) == {
        "promote_from_candidate",
        "state_dry_run_to_candidate",
        "state_candidate_to_approved",
    }
    creation = by_action["promote_from_candidate"]
    assert creation.actor == "alice"
    assert creation.new_state == "dry_run"
    assert creation.after["consolidator_candidate_id"] == cid
    assert by_action["state_dry_run_to_candidate"].new_state == "candidate"
    assert by_action["state_candidate_to_approved"].new_state == "approved"


@pytest.mark.asyncio
async def test_promote_global_scope_preserved(
    promoter: EpisodePromoter,
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    run_id = await candidates_store.start_run(
        triggered_by="test", group_id="", scope="global",
    )
    cid = await candidates_store.record_candidate(
        run_id=run_id,
        domain="episode",
        scope="global",
        group_id="",
        source_message_pks=[],
        payload={"situation": "global lesson"},
        confidence=0.8,
    )
    await candidates_store.decide_candidate(
        cid, state="approved", decided_by="admin",
    )

    result = await promoter.promote(cid, actor="admin")
    assert result.promoted

    ep = await episode_store.get_episode(result.episode_id)
    assert ep is not None
    assert ep.scope == "global"


@pytest.mark.asyncio
async def test_promote_skips_non_episode_domain(
    promoter: EpisodePromoter,
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    run_id = await candidates_store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await candidates_store.record_candidate(
        run_id=run_id,
        domain="slang",
        scope="group",
        group_id="g1",
        source_message_pks=[],
        payload={"term": "yyds", "meaning": "best"},
        confidence=0.7,
    )
    await candidates_store.decide_candidate(
        cid, state="approved", decided_by="admin",
    )

    result = await promoter.promote(cid, actor="admin")
    assert result.promoted is False
    assert result.skipped_reason == "domain=slang"
    assert result.episode_id == ""

    episodes = await episode_store.list_episodes()
    assert episodes == []


@pytest.mark.asyncio
async def test_promote_skips_non_approved_state(
    promoter: EpisodePromoter,
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    run_id = await candidates_store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await candidates_store.record_candidate(
        run_id=run_id,
        domain="episode",
        scope="group",
        group_id="g1",
        source_message_pks=[],
        payload={"situation": "x"},
        confidence=0.5,
    )

    # Still in dry_run — must not promote
    result = await promoter.promote(cid)
    assert result.promoted is False
    assert result.skipped_reason == "state=dry_run"
    assert result.episode_id == ""

    # Reject path — must not promote either
    await candidates_store.decide_candidate(
        cid, state="rejected", decided_by="admin",
    )
    result_rejected = await promoter.promote(cid)
    assert result_rejected.promoted is False
    assert result_rejected.skipped_reason == "state=rejected"

    episodes = await episode_store.list_episodes()
    assert episodes == []


@pytest.mark.asyncio
async def test_promote_returns_skipped_for_unknown_candidate(
    promoter: EpisodePromoter,
    episode_store: EpisodeStore,
):
    result = await promoter.promote("cand_not_in_db")
    assert result.promoted is False
    assert result.skipped_reason == "candidate_not_found"

    episodes = await episode_store.list_episodes()
    assert episodes == []


@pytest.mark.asyncio
async def test_promote_idempotent_on_repeat(
    promoter: EpisodePromoter,
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    cid = await _seed_approved_episode_candidate(candidates_store)
    first = await promoter.promote(cid, actor="alice")
    assert first.promoted is True

    second = await promoter.promote(cid, actor="alice")
    assert second.promoted is False
    assert second.skipped_reason == "already_promoted"
    assert second.episode_id == first.episode_id

    episodes = await episode_store.list_episodes(group_id="g1")
    assert len(episodes) == 1


@pytest.mark.asyncio
async def test_promote_cancel_path_leaves_episodes_empty(
    promoter: EpisodePromoter,
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    """D2 cancel-path regression.

    The admin already wrote ``state="approved"`` via decide_candidate
    before promote runs. If promote is cancelled mid-flight (shutdown,
    request abort), the candidate row must remain ``approved`` (it is the
    source of truth) and the episodes table must contain zero half-built
    rows — promote must not partially populate state.
    """
    cid = await _seed_approved_episode_candidate(candidates_store)

    async def _raise_cancel(**kwargs):
        raise asyncio.CancelledError()

    # Patch create_episode to simulate a cancel landing inside the await
    episode_store.create_episode = _raise_cancel  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await promoter.promote(cid, actor="alice")

    # Candidate row already-written state survives unchanged
    candidate = await candidates_store.get_candidate(cid)
    assert candidate is not None
    assert candidate.state == "approved"
    assert candidate.decided_by == "alice"

    # Episodes table is empty — no partial row
    db = episode_store._require_db()
    async with db.execute("SELECT COUNT(*) FROM episodes") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 0


@pytest.mark.asyncio
async def test_promote_create_failure_returns_skipped_reason(
    promoter: EpisodePromoter,
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    """A non-cancel failure inside ``create_episode`` is swallowed: the
    promoter logs at WARN and returns a ``skipped_reason`` carrying the
    exception type. The candidate stays approved (admin's source of
    truth) and the episodes table remains empty.
    """
    cid = await _seed_approved_episode_candidate(candidates_store)

    async def _boom(**kwargs):
        raise RuntimeError("disk full")

    episode_store.create_episode = _boom  # type: ignore[method-assign]

    result = await promoter.promote(cid, actor="alice")
    assert result.promoted is False
    assert result.episode_id == ""
    assert result.skipped_reason.startswith("create_failed:RuntimeError")

    candidate = await candidates_store.get_candidate(cid)
    assert candidate is not None
    assert candidate.state == "approved"

    episodes = await episode_store.list_episodes()
    assert episodes == []


@pytest.mark.asyncio
async def test_promote_idempotent_beyond_recent_200_window(
    promoter: EpisodePromoter,
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    """Durable idempotency: already-promoted target older than 200 newer
    episodes must still be found; repeat promote creates no duplicate.

    Replaces the former recent-200 Python scan with a store-owned
    source/meta lookup (database-wide).
    """
    cid = await _seed_approved_episode_candidate(candidates_store)
    first = await promoter.promote(cid, actor="alice")
    assert first.promoted is True
    assert first.episode_id

    # Make the target deterministically older than the noise rows. This keeps
    # the regression independent of same-second timestamp tie ordering.
    db = episode_store._require_db()
    await db.execute(
        "UPDATE episodes SET updated_at = ? WHERE episode_id = ?",
        ("2000-01-01T00:00:00+08:00", first.episode_id),
    )
    await db.commit()

    # Bury the promoted row under >200 newer episodes so a recent-200
    # list_episodes scan would miss it.
    for i in range(210):
        await episode_store.create_episode(
            situation=f"noise situation {i}",
            group_id="g1",
            source="consolidator",
            confidence=0.5,
            meta={"noise": True, "i": i},
        )

    recent = await episode_store.list_episodes(group_id="g1", limit=200)
    assert all(ep.episode_id != first.episode_id for ep in recent)

    second = await promoter.promote(cid, actor="alice")
    assert second.promoted is False
    assert second.skipped_reason == "already_promoted"
    assert second.episode_id == first.episode_id

    # Exactly one episode carries this consolidator_candidate_id.
    all_eps = await episode_store.list_episodes(group_id="g1", limit=500, offset=0)
    matched = [
        ep
        for ep in all_eps
        if str(ep.meta.get("consolidator_candidate_id", "")) == cid
    ]
    assert len(matched) == 1
    assert matched[0].episode_id == first.episode_id

    # Store-owned lookup must find it without a Python list window.
    found = await episode_store.find_by_source_meta(
        source="consolidator",
        meta_key="consolidator_candidate_id",
        meta_value=cid,
    )
    assert found is not None
    assert found.episode_id == first.episode_id


@pytest.mark.asyncio
async def test_source_meta_lookup_rejects_path_injection_and_skips_invalid_json(
    episode_store: EpisodeStore,
) -> None:
    expected = await episode_store.create_episode(
        situation="source reference lookup",
        source="consolidator",
        meta={"consolidator_candidate_id": "cand_safe"},
    )
    corrupt = await episode_store.create_episode(
        situation="corrupt metadata must not abort lookup",
        source="consolidator",
        meta={"noise": True},
    )
    db = episode_store._require_db()
    await db.execute(
        "UPDATE episodes SET meta_json = ? WHERE episode_id = ?",
        ("{not-json", corrupt.episode_id),
    )
    await db.commit()

    found = await episode_store.find_by_source_meta(
        source="consolidator",
        meta_key="consolidator_candidate_id",
        meta_value="cand_safe",
    )

    assert found is not None
    assert found.episode_id == expected.episode_id
    with pytest.raises(ValueError, match="invalid meta_key"):
        await episode_store.find_by_source_meta(
            source="consolidator",
            meta_key="x') OR 1=1 --",
            meta_value="cand_safe",
        )


# ---------------------------------------------------------------------------
# Typed provenance links + UID-bound alias observations (contract tests)
#
# Expected EpisodePromoter constructor gains optional keyword dependencies
# (all default None for compatibility):
#   message_archive      — async get_messages_by_pks(message_pks)
#   card_store           — async find_by_source_message_ids(message_ids)
#   knowledge_graph      — async find_fact_ids_by_evidence_refs(evidence_ids)
#   entity_alias_store   — async observe(...)
#
# These tests intentionally assert the RED contract against current production
# code: they must fail at TypeError (unexpected keyword) or missing links/meta
# assertions, never at syntax/import time.
# ---------------------------------------------------------------------------


class _FakeMessageArchive:
    """In-memory async fake for message_archive.get_messages_by_pks."""

    def __init__(self, rows_by_pk: dict[int, dict] | None = None) -> None:
        self.rows_by_pk = dict(rows_by_pk or {})
        self.calls: list[list[int]] = []
        self.scope_calls: list[dict] = []
        self.raise_exc: BaseException | None = None

    async def get_messages_by_pks(self, message_pks, *, chat_type=None, chat_id=None):
        pks = list(message_pks or [])
        self.calls.append(pks)
        self.scope_calls.append({"message_pks": pks, "chat_type": chat_type, "chat_id": chat_id})
        if self.raise_exc is not None:
            raise self.raise_exc
        rows = []
        for pk in pks:
            try:
                key = int(pk)
            except (TypeError, ValueError):
                continue
            row = self.rows_by_pk.get(key)
            if row is None:
                continue
            # Unit-test seed rows may omit chat scope fields; only filter when present.
            if "chat_type" in row and chat_type is not None and str(row.get("chat_type", "")) != str(chat_type):
                continue
            if "chat_id" in row and chat_id is not None and str(row.get("chat_id", "")) != str(chat_id):
                continue
            rows.append(row)
        return rows


class _FakeCardStore:
    """In-memory async fake for card_store.find_by_source_message_ids."""

    def __init__(self, cards_by_message_id: dict[int, list] | None = None) -> None:
        self.cards_by_message_id = {
            int(k): list(v) for k, v in (cards_by_message_id or {}).items()
        }
        self.calls: list[list] = []
        self.scope_calls: list[dict] = []
        self.raise_exc: BaseException | None = None

    async def find_by_source_message_ids(self, message_ids, *, allowed_scopes=None):
        mids = list(message_ids or [])
        self.calls.append(mids)
        self.scope_calls.append({"message_ids": mids, "allowed_scopes": allowed_scopes})
        if self.raise_exc is not None:
            raise self.raise_exc
        out: list = []
        seen: set[str] = set()
        for mid in mids:
            try:
                key = int(mid)
            except (TypeError, ValueError):
                continue
            for card in self.cards_by_message_id.get(key, []):
                card_id = (
                    card.get("card_id")
                    if isinstance(card, dict)
                    else getattr(card, "card_id", None)
                )
                if card_id is None or str(card_id) in seen:
                    continue
                if allowed_scopes is not None:
                    scope = (
                        card.get("scope")
                        if isinstance(card, dict)
                        else getattr(card, "scope", None)
                    )
                    scope_id = (
                        card.get("scope_id")
                        if isinstance(card, dict)
                        else getattr(card, "scope_id", None)
                    )
                    if (
                        scope is not None
                        and scope_id is not None
                        and (str(scope), str(scope_id))
                        not in {(str(s), str(i)) for s, i in allowed_scopes}
                    ):
                        continue
                seen.add(str(card_id))
                out.append(card)
        return out


class _FakeKnowledgeGraph:
    """In-memory async fake for knowledge_graph.find_fact_ids_by_evidence_refs."""

    def __init__(self, facts_by_evidence: dict[str, list[str]] | None = None) -> None:
        self.facts_by_evidence = {
            str(k): list(v) for k, v in (facts_by_evidence or {}).items()
        }
        self.calls: list[list] = []
        self.scope_calls: list[dict] = []
        self.raise_exc: BaseException | None = None

    async def find_fact_ids_by_evidence_refs(self, evidence_ids, *, allowed_scopes=None):
        eids = list(evidence_ids or [])
        self.calls.append(eids)
        self.scope_calls.append({"evidence_ids": eids, "allowed_scopes": allowed_scopes})
        if self.raise_exc is not None:
            raise self.raise_exc
        out: list[str] = []
        seen: set[str] = set()
        for eid in eids:
            for fact_id in self.facts_by_evidence.get(str(eid), []):
                if fact_id in seen:
                    continue
                seen.add(fact_id)
                out.append(fact_id)
        return out


class _FakeEntityAliasStore:
    """In-memory async fake for entity_alias_store.observe(...)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.raise_exc: BaseException | None = None

    async def observe(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.raise_exc is not None:
            raise self.raise_exc
        return {"outcome": "created", **kwargs}


class _CardObj:
    def __init__(self, card_id: str) -> None:
        self.card_id = card_id


async def _seed_numeric_group_candidate(
    store: ConsolidatorCandidatesStore,
    *,
    group_id: str = "456",
    source_message_pks: list | None = None,
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
            source_message_pks
            if source_message_pks is not None
            else [11, 12]
        ),
        payload=payload or {
            "situation": "group banter about weather",
            "observed_context": "evening group chat",
            "action_taken": "joked lightly",
            "outcome_signal": "positive reaction",
            "reflection": "light humor works here",
        },
        confidence=0.75,
    )
    await store.update_candidate_cluster(cid, "cluster_prov")
    await store.decide_candidate(
        cid, state="approved", decided_by="alice", reason="lgtm",
    )
    return cid


def _promoter_with_ports(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
    **ports,
) -> EpisodePromoter:
    return EpisodePromoter(
        candidates_store=candidates_store,
        episode_store=episode_store,
        **ports,
    )


@pytest.mark.asyncio
async def test_promote_numeric_group_always_links_group_and_message_pks(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    """Numeric group_id 456 + source_message_pks [11,12] → stable dedup order.

    Always produces: entity:group:qq:456, message_pk:11, message_pk:12
    even with all optional ports omitted.
    """
    cid = await _seed_numeric_group_candidate(candidates_store)
    promoter = _promoter_with_ports(candidates_store, episode_store)

    result = await promoter.promote(cid, actor="alice")
    assert result.promoted is True
    assert result.skipped_reason == ""
    assert not str(result.skipped_reason).startswith("create_failed")

    ep = await episode_store.get_episode(result.episode_id)
    assert ep is not None
    assert ep.linked_memory_ids == [
        "entity:group:qq:456",
        "message_pk:11",
        "message_pk:12",
    ]
    assert ep.meta["linked_ref_version"] == 1
    assert ep.meta["linked_ref_counts"]["entity"] == 1
    assert ep.meta["linked_ref_counts"]["message_pk"] == 2
    # Existing audit meta remains
    assert ep.meta["consolidator_candidate_id"] == cid
    assert ep.meta["source_message_pks"] == [11, 12]
    assert ep.meta["promoted_by"] == "alice"


@pytest.mark.asyncio
async def test_promote_archive_user_speaker_adds_entity_message_and_alias(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    """Archive user row speaker=小明(123) message_id=9001 adds entity+message+alias.

    Assistant rows and bare nick without trailing numeric uid never create
    user entity/alias.
    """
    archive = _FakeMessageArchive(
        {
            11: {
                "message_pk": 11,
                "role": "user",
                "speaker": "小明(123)",
                "message_id": 9001,
            },
            12: {
                "message_pk": 12,
                "role": "assistant",
                "speaker": "bot",
                "message_id": 9002,
            },
            13: {
                "message_pk": 13,
                "role": "user",
                "speaker": "路人甲",  # bare nick, no trailing (uid)
                "message_id": 9003,
            },
        }
    )
    alias_store = _FakeEntityAliasStore()
    cid = await _seed_numeric_group_candidate(
        candidates_store,
        source_message_pks=[11, 12, 13],
    )
    promoter = _promoter_with_ports(
        candidates_store,
        episode_store,
        message_archive=archive,
        entity_alias_store=alias_store,
    )

    result = await promoter.promote(cid, actor="alice")
    assert result.promoted is True
    assert not str(result.skipped_reason).startswith("create_failed")

    ep = await episode_store.get_episode(result.episode_id)
    assert ep is not None
    links = list(ep.linked_memory_ids)
    assert "entity:group:qq:456" in links
    assert "message_pk:11" in links
    assert "message_pk:12" in links
    assert "message_pk:13" in links
    assert "entity:user:qq:123" in links
    assert "message:9001" in links
    # assistant / bare-nick must not invent user entities
    assert "entity:user:qq:9002" not in links
    assert not any(
        ref.startswith("entity:user:") and ref != "entity:user:qq:123"
        for ref in links
    )
    # platform message id for assistant may still be linked if present; only
    # user-bound message:9001 is required. Bare nick has message_id but no uid.
    assert "message:9003" not in links or "entity:user:" not in [
        r for r in links if r.startswith("entity:user:") and r != "entity:user:qq:123"
    ]

    assert len(archive.calls) == 1
    assert sorted(archive.calls[0]) == [11, 12, 13]

    assert len(alias_store.calls) == 1
    obs = alias_store.calls[0]
    assert obs["entity_key"] == "user:qq:123"
    assert obs["alias"] == "小明"
    assert obs["scope"] == "group"
    assert obs["scope_id"] in (456, "456")
    assert obs["source"] == "archive_speaker"
    assert obs["confidence"] == 0.5

    assert ep.meta["linked_ref_version"] == 1
    counts = ep.meta["linked_ref_counts"]
    assert counts["entity"] >= 2  # group + user
    assert counts["message_pk"] == 3
    assert counts.get("message", 0) >= 1


@pytest.mark.asyncio
async def test_promote_card_and_kg_resolvers_add_typed_refs(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    """Card resolver uses platform message ids; KG uses card ids + message ids."""
    archive = _FakeMessageArchive(
        {
            11: {
                "message_pk": 11,
                "role": "user",
                "speaker": "小明(123)",
                "message_id": 9001,
            },
            12: {
                "message_pk": 12,
                "role": "user",
                "speaker": "小红(4567)",
                "message_id": 9002,
            },
        }
    )
    card_store = _FakeCardStore(
        {
            9001: [{"card_id": "card_alpha"}],
            9002: [_CardObj("card_beta")],
        }
    )
    kg = _FakeKnowledgeGraph(
        {
            "card_alpha": ["fact_aa"],
            "card_beta": ["fact_bb"],
            "9001": ["fact_m1"],
            "9002": ["fact_m2"],
        }
    )
    cid = await _seed_numeric_group_candidate(candidates_store)
    promoter = _promoter_with_ports(
        candidates_store,
        episode_store,
        message_archive=archive,
        card_store=card_store,
        knowledge_graph=kg,
    )

    result = await promoter.promote(cid, actor="alice")
    assert result.promoted is True
    assert not str(result.skipped_reason).startswith("create_failed")

    ep = await episode_store.get_episode(result.episode_id)
    assert ep is not None
    links = list(ep.linked_memory_ids)
    assert "card:card_alpha" in links
    assert "card:card_beta" in links
    assert "fact:fact_aa" in links
    assert "fact:fact_bb" in links
    assert "fact:fact_m1" in links
    assert "fact:fact_m2" in links

    assert len(card_store.calls) == 1
    assert sorted(int(x) for x in card_store.calls[0]) == [9001, 9002]

    assert len(kg.calls) == 1
    evidence = {str(x) for x in kg.calls[0]}
    # card ids plus platform message ids as evidence ids
    assert "card_alpha" in evidence
    assert "card_beta" in evidence
    assert "9001" in evidence or 9001 in kg.calls[0]
    assert "9002" in evidence or 9002 in kg.calls[0]

    assert ep.meta["linked_ref_version"] == 1
    counts = ep.meta["linked_ref_counts"]
    assert counts.get("card", 0) == 2
    assert counts.get("fact", 0) == 4


@pytest.mark.asyncio
async def test_promote_provenance_meta_preserves_existing_and_counts_by_kind(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    cid = await _seed_numeric_group_candidate(candidates_store)
    promoter = _promoter_with_ports(candidates_store, episode_store)

    result = await promoter.promote(cid, actor="bob")
    assert result.promoted is True

    ep = await episode_store.get_episode(result.episode_id)
    assert ep is not None
    assert ep.meta["linked_ref_version"] == 1
    assert isinstance(ep.meta["linked_ref_counts"], dict)
    # Existing meta remains
    assert ep.meta["consolidator_candidate_id"] == cid
    assert ep.meta["consolidator_run_id"]
    assert ep.meta["normalizer_cluster_id"] == "cluster_prov"
    assert ep.meta["source_message_pks"] == [11, 12]
    assert ep.meta["promoted_by"] == "bob"
    # Counts match linked ids by kind
    counts = ep.meta["linked_ref_counts"]
    assert counts["entity"] == 1
    assert counts["message_pk"] == 2


@pytest.mark.asyncio
async def test_re_promote_provenance_is_idempotent_no_duplicate_links_or_alias(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    archive = _FakeMessageArchive(
        {
            11: {
                "message_pk": 11,
                "role": "user",
                "speaker": "小明(123)",
                "message_id": 9001,
            },
            12: {
                "message_pk": 12,
                "role": "user",
                "speaker": "小明(123)",
                "message_id": 9004,
            },
        }
    )
    alias_store = _FakeEntityAliasStore()
    cid = await _seed_numeric_group_candidate(candidates_store)
    promoter = _promoter_with_ports(
        candidates_store,
        episode_store,
        message_archive=archive,
        entity_alias_store=alias_store,
    )

    first = await promoter.promote(cid, actor="alice")
    assert first.promoted is True
    first_ep = await episode_store.get_episode(first.episode_id)
    assert first_ep is not None
    first_links = list(first_ep.linked_memory_ids)
    first_alias_calls = len(alias_store.calls)
    assert first_alias_calls >= 1

    second = await promoter.promote(cid, actor="alice")
    assert second.episode_id == first.episode_id
    assert second.skipped_reason == "already_promoted"
    # No second alias observe on re-promote
    assert len(alias_store.calls) == first_alias_calls

    second_ep = await episode_store.get_episode(second.episode_id)
    assert second_ep is not None
    assert list(second_ep.linked_memory_ids) == first_links
    # No duplicate refs inside the list
    assert len(second_ep.linked_memory_ids) == len(set(second_ep.linked_memory_ids))

    episodes = await episode_store.list_episodes(group_id="456")
    matched = [
        e
        for e in episodes
        if str(e.meta.get("consolidator_candidate_id", "")) == cid
    ]
    assert len(matched) == 1


@pytest.mark.asyncio
async def test_promote_global_or_nonnumeric_group_skips_group_entity(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    """Global / nonnumeric group_id must not invent entity:group:qq:*.

    message_pk refs remain when source_message_pks are present.
    """
    # nonnumeric group
    cid_non = await _seed_approved_episode_candidate(
        candidates_store,
        group_id="g1",
        payload={
            "situation": "nonnumeric group",
            "observed_context": "x",
            "action_taken": "y",
            "outcome_signal": "z",
            "reflection": "r",
        },
    )
    promoter = _promoter_with_ports(candidates_store, episode_store)
    result_non = await promoter.promote(cid_non, actor="alice")
    assert result_non.promoted is True
    ep_non = await episode_store.get_episode(result_non.episode_id)
    assert ep_non is not None
    assert not any(r.startswith("entity:group:") for r in ep_non.linked_memory_ids)
    assert "message_pk:101" in ep_non.linked_memory_ids
    assert "message_pk:102" in ep_non.linked_memory_ids

    # global scope, empty group, no message pks
    run_id = await candidates_store.start_run(
        triggered_by="test", group_id="", scope="global",
    )
    cid_global = await candidates_store.record_candidate(
        run_id=run_id,
        domain="episode",
        scope="global",
        group_id="",
        source_message_pks=[7],
        payload={"situation": "global lesson"},
        confidence=0.8,
    )
    await candidates_store.decide_candidate(
        cid_global, state="approved", decided_by="admin",
    )
    result_g = await promoter.promote(cid_global, actor="admin")
    assert result_g.promoted is True
    ep_g = await episode_store.get_episode(result_g.episode_id)
    assert ep_g is not None
    assert not any(r.startswith("entity:group:") for r in ep_g.linked_memory_ids)
    assert ep_g.linked_memory_ids == ["message_pk:7"]


@pytest.mark.asyncio
async def test_promote_optional_resolvers_raise_still_succeeds_minimum_refs(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    """Each optional resolver may raise; promote still succeeds with minimum refs."""
    archive = _FakeMessageArchive(
        {
            11: {
                "message_pk": 11,
                "role": "user",
                "speaker": "小明(123)",
                "message_id": 9001,
            },
            12: {
                "message_pk": 12,
                "role": "user",
                "speaker": "小红(999)",
                "message_id": 9002,
            },
        }
    )
    archive.raise_exc = RuntimeError("archive down")
    card_store = _FakeCardStore({9001: [{"card_id": "c1"}]})
    card_store.raise_exc = RuntimeError("card down")
    kg = _FakeKnowledgeGraph({"c1": ["f1"]})
    kg.raise_exc = RuntimeError("kg down")
    alias_store = _FakeEntityAliasStore()
    alias_store.raise_exc = RuntimeError("alias down")

    cid = await _seed_numeric_group_candidate(candidates_store)
    promoter = _promoter_with_ports(
        candidates_store,
        episode_store,
        message_archive=archive,
        card_store=card_store,
        knowledge_graph=kg,
        entity_alias_store=alias_store,
    )

    result = await promoter.promote(cid, actor="alice")
    assert result.promoted is True
    assert result.skipped_reason == ""
    assert not str(result.skipped_reason).startswith("create_failed")

    ep = await episode_store.get_episode(result.episode_id)
    assert ep is not None
    # Minimum deterministic group + message_pk refs always present
    assert ep.linked_memory_ids == [
        "entity:group:qq:456",
        "message_pk:11",
        "message_pk:12",
    ]
    assert ep.meta["linked_ref_version"] == 1
    assert ep.meta["linked_ref_counts"]["entity"] == 1
    assert ep.meta["linked_ref_counts"]["message_pk"] == 2


@pytest.mark.asyncio
async def test_promote_source_message_pks_dedup_and_skip_invalid(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    """Duplicates/invalid source_message_pks must not produce duplicate/invalid refs."""
    run_id = await candidates_store.start_run(
        triggered_by="test", group_id="456", scope="group",
    )
    # Store layer may coerce ints; also inject dirty pks via direct SQL if needed.
    cid = await candidates_store.record_candidate(
        run_id=run_id,
        domain="episode",
        scope="group",
        group_id="456",
        source_message_pks=[11, 11, 12, 0, -3],
        payload={
            "situation": "dedup pks",
            "observed_context": "x",
            "action_taken": "y",
            "outcome_signal": "z",
            "reflection": "r",
        },
        confidence=0.6,
    )
    await candidates_store.decide_candidate(
        cid, state="approved", decided_by="alice",
    )

    # Force additional dirty JSON values that normalize_linked_refs would drop
    # if they ever reached linked_memory_ids (empty/non-positive).
    candidate = await candidates_store.get_candidate(cid)
    assert candidate is not None
    # If store already filtered invalid ints, still assert promote path is safe.
    dirty_pks = [*list(candidate.source_message_pks), 11, 0, -1]
    # Patch candidate row payload path by re-writing source_message_pks JSON.
    db = candidates_store._require_db()  # type: ignore[attr-defined]
    import json as _json

    await db.execute(
        "UPDATE consolidator_candidates SET source_message_pks = ? WHERE candidate_id = ?",
        (_json.dumps(dirty_pks, ensure_ascii=False), cid),
    )
    await db.commit()

    promoter = _promoter_with_ports(candidates_store, episode_store)
    result = await promoter.promote(cid, actor="alice")
    assert result.promoted is True
    assert not str(result.skipped_reason).startswith("create_failed")

    ep = await episode_store.get_episode(result.episode_id)
    assert ep is not None
    links = list(ep.linked_memory_ids)
    assert links.count("message_pk:11") == 1
    assert links.count("message_pk:12") == 1
    assert "message_pk:0" not in links
    assert "message_pk:-1" not in links
    assert "message_pk:-3" not in links
    assert "entity:group:qq:456" in links
    # stable order: group entity first, then message_pks ascending first-seen
    msg_pks = [r for r in links if r.startswith("message_pk:")]
    assert msg_pks == ["message_pk:11", "message_pk:12"]


@pytest.mark.asyncio
async def test_episode_promoter_constructor_accepts_optional_ports_default_none(
    candidates_store: ConsolidatorCandidatesStore,
    episode_store: EpisodeStore,
):
    """Existing constructor usage remains compatible; optional ports default None."""
    # Two-arg style used by existing tests / bootstrap must still work.
    plain = EpisodePromoter(
        candidates_store=candidates_store,
        episode_store=episode_store,
    )
    assert plain is not None

    # Explicit None ports must also be accepted (forward-compat wiring).
    with_none = EpisodePromoter(
        candidates_store=candidates_store,
        episode_store=episode_store,
        message_archive=None,
        card_store=None,
        knowledge_graph=None,
        entity_alias_store=None,
    )
    assert with_none is not None

    cid = await _seed_approved_episode_candidate(candidates_store)
    result = await plain.promote(cid, actor="alice")
    assert result.promoted is True
