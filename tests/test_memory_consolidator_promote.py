"""Tests for EpisodePromoter — D.1 promote bridge.

Covers the full decision matrix of ``EpisodePromoter.promote``:

- happy path: ``domain="episode"`` + ``state="approved"`` → new dry_run episode
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
    assert ep.episode_state == "dry_run"
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

    # Revision row recorded
    revisions = await episode_store.list_revisions(ep.episode_id)
    assert len(revisions) == 1
    rev = revisions[0]
    assert rev.action == "promote_from_candidate"
    assert rev.actor == "alice"
    assert rev.new_state == "dry_run"
    assert rev.after["consolidator_candidate_id"] == cid


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
