"""Tests for ConsolidatorCandidatesStore: schema, CRUD, decision gate."""

from __future__ import annotations

import pytest

from services.memory_consolidator import (
    Candidate,
    ConsolidatorCandidatesStore,
    ScanRun,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "consolidator_candidates.db")


@pytest.fixture
async def store(db_path):
    s = ConsolidatorCandidatesStore(db_path)
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_init_creates_tables(store: ConsolidatorCandidatesStore):
    db = store._require_db()
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) as cur:
        tables = [row["name"] for row in await cur.fetchall()]
    assert "consolidator_runs" in tables
    assert "consolidator_candidates" in tables


@pytest.mark.asyncio
async def test_init_uses_delete_journal_mode(store: ConsolidatorCandidatesStore):
    db = store._require_db()
    async with db.execute("PRAGMA journal_mode") as cur:
        row = await cur.fetchone()
    assert row is not None
    mode = str(row[0]).lower()
    assert mode == "delete"


@pytest.mark.asyncio
async def test_start_and_finish_run(store: ConsolidatorCandidatesStore):
    run_id = await store.start_run(
        triggered_by="test",
        group_id="g1",
        scope="group",
        meta={"max_batches": 1},
    )
    assert run_id.startswith("run_")
    run = await store.get_run(run_id)
    assert isinstance(run, ScanRun)
    assert run.status == "running"
    assert run.scope == "group"
    assert run.meta == {"max_batches": 1}

    await store.finish_run(
        run_id, status="done", scanned_count=12, candidates_count=3,
    )
    refreshed = await store.get_run(run_id)
    assert refreshed is not None
    assert refreshed.status == "done"
    assert refreshed.scanned_count == 12
    assert refreshed.candidates_count == 3
    assert refreshed.finished_at > 0


@pytest.mark.asyncio
async def test_start_run_rejects_invalid_scope(store: ConsolidatorCandidatesStore):
    with pytest.raises(ValueError):
        await store.start_run(
            triggered_by="test", group_id="g1", scope="bogus",  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_record_candidate_clamps_confidence(
    store: ConsolidatorCandidatesStore,
):
    run_id = await store.start_run(triggered_by="t", group_id="g1")
    cid = await store.record_candidate(
        run_id=run_id,
        domain="slang",
        scope="group",
        group_id="g1",
        source_message_pks=[1, 2, 3],
        payload={"term": "yyds", "meaning": "best ever"},
        confidence=2.5,  # over 1.0 → clamped
    )
    candidate = await store.get_candidate(cid)
    assert isinstance(candidate, Candidate)
    assert candidate.confidence == 1.0
    assert candidate.state == "dry_run"
    assert candidate.source_message_pks == [1, 2, 3]
    assert candidate.payload["term"] == "yyds"


@pytest.mark.asyncio
async def test_record_candidate_rejects_invalid_domain(
    store: ConsolidatorCandidatesStore,
):
    run_id = await store.start_run(triggered_by="t", group_id="g1")
    with pytest.raises(ValueError):
        await store.record_candidate(
            run_id=run_id,
            domain="not_a_domain",  # type: ignore[arg-type]
            scope="group",
            group_id="g1",
            source_message_pks=[],
            payload={},
            confidence=0.5,
        )


@pytest.mark.asyncio
async def test_list_candidates_filter_by_domain_and_scope(
    store: ConsolidatorCandidatesStore,
):
    run_id = await store.start_run(triggered_by="t", group_id="g1")
    await store.record_candidate(
        run_id=run_id, domain="slang", scope="group", group_id="g1",
        source_message_pks=[1], payload={"term": "a"}, confidence=0.5,
    )
    await store.record_candidate(
        run_id=run_id, domain="style", scope="group", group_id="g1",
        source_message_pks=[1], payload={"expression": "b"}, confidence=0.5,
    )
    await store.record_candidate(
        run_id=run_id, domain="fact", scope="global", group_id="",
        source_message_pks=[2], payload={"subject": "x"}, confidence=0.5,
    )
    slang_only = await store.list_candidates(domain="slang")
    assert len(slang_only) == 1
    assert slang_only[0].domain == "slang"

    global_only = await store.list_candidates(scope="global")
    assert len(global_only) == 1
    assert global_only[0].scope == "global"

    with pytest.raises(ValueError):
        await store.list_candidates(domain="not_a_domain")


@pytest.mark.asyncio
async def test_decide_candidate_enforces_transition(
    store: ConsolidatorCandidatesStore,
):
    run_id = await store.start_run(triggered_by="t", group_id="g1")
    cid = await store.record_candidate(
        run_id=run_id, domain="fact", scope="group", group_id="g1",
        source_message_pks=[], payload={"subject": "s"}, confidence=0.4,
    )
    ok = await store.decide_candidate(
        cid, state="approved", decided_by="admin", reason="looks good",
    )
    assert ok is True
    refreshed = await store.get_candidate(cid)
    assert refreshed is not None
    assert refreshed.state == "approved"
    assert refreshed.decided_by == "admin"
    assert refreshed.decision_reason == "looks good"

    # decision is sticky — second decide from approved is rejected
    with pytest.raises(ValueError):
        await store.decide_candidate(
            cid, state="rejected", decided_by="admin",
        )


@pytest.mark.asyncio
async def test_decide_candidate_returns_false_for_unknown(
    store: ConsolidatorCandidatesStore,
):
    ok = await store.decide_candidate(
        "cand_does_not_exist", state="approved", decided_by="admin",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_update_candidate_cluster(store: ConsolidatorCandidatesStore):
    run_id = await store.start_run(triggered_by="t", group_id="g1")
    cid = await store.record_candidate(
        run_id=run_id, domain="slang", scope="group", group_id="g1",
        source_message_pks=[], payload={"term": "x"}, confidence=0.5,
    )
    await store.update_candidate_cluster(cid, "cluster_42")
    refreshed = await store.get_candidate(cid)
    assert refreshed is not None
    assert refreshed.normalizer_cluster_id == "cluster_42"
