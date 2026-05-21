"""Tests for ConsolidatorCandidatesStore.update_candidate_payload — D.2 admin edit.

Covers:

- happy path: edit payload of dry_run candidate, projection drops unknown keys
- queued state also editable
- approved state rejected with ValueError (post-decision frozen)
- rejected state rejected with ValueError
- missing candidate returns None (404 surface)
- revision row is written with before/after diff
- D2 cancel-path: cancel mid-update leaves candidate & revision tables clean
"""

from __future__ import annotations

import asyncio

import pytest

from services.memory_consolidator import ConsolidatorCandidatesStore


@pytest.fixture
async def store(tmp_path):
    s = ConsolidatorCandidatesStore(str(tmp_path / "consolidator_candidates.db"))
    await s.init()
    yield s
    await s.close()


async def _seed_episode_candidate(
    store: ConsolidatorCandidatesStore,
    *,
    state: str = "dry_run",
    payload: dict | None = None,
) -> str:
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id, domain="episode", scope="group", group_id="g1",
        source_message_pks=[1],
        payload=payload or {
            "situation": "user asked about deploys",
            "observed_context": "morning",
            "action_taken": "explained step-by-step",
            "outcome_signal": "user said thanks",
            "reflection": "step-by-step works for technical asks",
        },
        confidence=0.5,
    )
    if state == "queued":
        await store.decide_candidate(
            cid, state="queued", decided_by="alice", reason="",
        )
    elif state == "approved":
        await store.decide_candidate(
            cid, state="approved", decided_by="alice", reason="",
        )
    elif state == "rejected":
        await store.decide_candidate(
            cid, state="rejected", decided_by="alice", reason="",
        )
    return cid


@pytest.mark.asyncio
async def test_update_payload_dry_run_happy_path(store):
    cid = await _seed_episode_candidate(store)
    new_payload = {
        "situation": "edited situation",
        "observed_context": "edited context",
        "action_taken": "edited action",
        "outcome_signal": "edited outcome",
        "reflection": "edited reflection — admin补改了 LLM 漏写的反思",
        "rogue_unknown_field": "should be dropped silently",
    }
    refreshed = await store.update_candidate_payload(
        cid, payload=new_payload, actor="alice",
        reason="补充 LLM 漏写的 reflection",
    )
    assert refreshed is not None
    assert refreshed.payload["situation"] == "edited situation"
    assert refreshed.payload["reflection"].startswith("edited reflection")
    # normalize_payload must drop unknown fields
    assert "rogue_unknown_field" not in refreshed.payload


@pytest.mark.asyncio
async def test_update_payload_queued_allowed(store):
    cid = await _seed_episode_candidate(store, state="queued")
    refreshed = await store.update_candidate_payload(
        cid, payload={"situation": "queued edit"}, actor="alice",
    )
    assert refreshed is not None
    assert refreshed.state == "queued"
    assert refreshed.payload["situation"] == "queued edit"


@pytest.mark.asyncio
async def test_update_payload_approved_forbidden(store):
    cid = await _seed_episode_candidate(store, state="approved")
    with pytest.raises(ValueError, match="forbidden"):
        await store.update_candidate_payload(
            cid, payload={"situation": "x"}, actor="alice",
        )


@pytest.mark.asyncio
async def test_update_payload_rejected_forbidden(store):
    cid = await _seed_episode_candidate(store, state="rejected")
    with pytest.raises(ValueError, match="forbidden"):
        await store.update_candidate_payload(
            cid, payload={"situation": "x"}, actor="alice",
        )


@pytest.mark.asyncio
async def test_update_payload_missing_candidate_returns_none(store):
    result = await store.update_candidate_payload(
        "cand_does_not_exist", payload={"situation": "x"}, actor="alice",
    )
    assert result is None


@pytest.mark.asyncio
async def test_update_payload_records_revision(store):
    cid = await _seed_episode_candidate(store)
    await store.update_candidate_payload(
        cid, payload={"situation": "v2"}, actor="alice",
        reason="补 reflection",
    )
    revisions = await store.list_candidate_revisions(cid)
    assert len(revisions) == 1
    rev = revisions[0]
    assert rev.action == "payload_edit"
    assert rev.actor == "alice"
    assert rev.reason == "补 reflection"
    assert rev.before["payload"]["situation"] == "user asked about deploys"
    assert rev.after["payload"]["situation"] == "v2"
    assert rev.meta["domain"] == "episode"


@pytest.mark.asyncio
async def test_update_payload_revisions_empty_for_unedited(store):
    cid = await _seed_episode_candidate(store)
    revisions = await store.list_candidate_revisions(cid)
    assert revisions == []


@pytest.mark.asyncio
async def test_update_payload_cancel_path_leaves_state_clean(store):
    """D2 cancel-path: external cancellation must not leave half-state.

    The store commits the payload UPDATE + revision INSERT in a single
    transaction. If the coroutine is cancelled before the commit, the
    candidate row should remain at the original payload and no
    revision row should appear.
    """
    cid = await _seed_episode_candidate(store)

    async def _cancelled_edit():
        # Wrap update_candidate_payload in a wait_for that times out
        # immediately to simulate shutdown cancellation.
        await asyncio.wait_for(
            store.update_candidate_payload(
                cid, payload={"situation": "cancelled"}, actor="alice",
            ),
            timeout=0.0,
        )

    with pytest.raises(asyncio.TimeoutError):
        await _cancelled_edit()

    refreshed = await store.get_candidate(cid)
    assert refreshed is not None
    # Original payload preserved (the edit may have been cancelled before
    # or after commit; what matters is the store remains consistent —
    # if the commit landed before cancel, revision row must exist).
    revisions = await store.list_candidate_revisions(cid)
    if refreshed.payload["situation"] == "cancelled":
        # commit landed before cancel — revision must accompany it
        assert len(revisions) == 1
    else:
        # commit was cancelled — no revision should leak
        assert refreshed.payload["situation"] == "user asked about deploys"
        assert revisions == []
