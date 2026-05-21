"""Admin API tests for /api/admin/memory_consolidator."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api.memory_consolidator import (
    create_memory_consolidator_router,
)
from services.episodic import EpisodeStore
from services.memory_consolidator import (
    ConsolidatorCandidatesStore,
    EpisodePromoter,
    ReflectionRunReport,
    RunReport,
)


@pytest.fixture
async def store(tmp_path):
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


def _build_client(
    *,
    store: Any,
    consolidator: Any | None = None,
    storage_dir: Any = None,
    episode_promoter: Any | None = None,
    reflection_generator: Any | None = None,
) -> TestClient:
    ctx = SimpleNamespace(
        memory_consolidator_store=store,
        memory_consolidator=consolidator,
        storage_dir=storage_dir,
        episode_promoter=episode_promoter,
        reflection_generator=reflection_generator,
    )
    app = FastAPI()
    app.include_router(
        create_memory_consolidator_router(ctx=ctx),
        prefix="/api/admin",
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_get_runs_empty(store):
    client = _build_client(store=store)
    resp = client.get("/api/admin/memory_consolidator/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"] == []
    assert body["count"] == 0


@pytest.mark.asyncio
async def test_get_runs_lists_recorded(store):
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    await store.finish_run(
        run_id, status="done", scanned_count=4, candidates_count=2,
    )
    client = _build_client(store=store)
    resp = client.get("/api/admin/memory_consolidator/runs")
    body = resp.json()
    assert body["ok"] is True
    assert len(body["data"]) == 1
    assert body["data"][0]["run_id"] == run_id
    assert body["data"][0]["status"] == "done"


@pytest.mark.asyncio
async def test_get_run_candidates(store):
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id, domain="slang", scope="group", group_id="g1",
        source_message_pks=[1, 2],
        payload={"term": "yyds", "meaning": "best"},
        confidence=0.7,
    )
    client = _build_client(store=store)
    resp = client.get(
        f"/api/admin/memory_consolidator/runs/{run_id}/candidates",
        params={"domain": "slang"},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["count"] == 1
    assert body["data"][0]["candidate_id"] == cid
    assert body["data"][0]["domain"] == "slang"
    assert body["run"]["run_id"] == run_id


@pytest.mark.asyncio
async def test_get_run_candidates_404_for_unknown_run(store):
    client = _build_client(store=store)
    resp = client.get(
        "/api/admin/memory_consolidator/runs/run_does_not_exist/candidates"
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["ok"] is False


@pytest.mark.asyncio
async def test_list_candidates_global(store):
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    await store.record_candidate(
        run_id=run_id, domain="fact", scope="group", group_id="g1",
        source_message_pks=[],
        payload={"subject": "x", "predicate": "y", "object": "z"},
        confidence=0.5,
    )
    client = _build_client(store=store)
    resp = client.get(
        "/api/admin/memory_consolidator/candidates",
        params={"domain": "fact"},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["count"] == 1
    assert body["data"][0]["domain"] == "fact"


@pytest.mark.asyncio
async def test_list_candidates_rejects_invalid_filter(store):
    client = _build_client(store=store)
    resp = client.get(
        "/api/admin/memory_consolidator/candidates",
        params={"domain": "not_a_domain"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False


@pytest.mark.asyncio
async def test_post_runs_returns_503_when_consolidator_unwired(store):
    client = _build_client(store=store, consolidator=None)
    resp = client.post(
        "/api/admin/memory_consolidator/runs", json={"group_id": "g1"},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["ok"] is False


@pytest.mark.asyncio
async def test_post_runs_requires_group_id(store):
    fake_consolidator = MagicMock()
    fake_consolidator.run_once = AsyncMock()
    client = _build_client(store=store, consolidator=fake_consolidator)
    resp = client.post(
        "/api/admin/memory_consolidator/runs", json={},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_runs_invokes_consolidator(store):
    fake_consolidator = MagicMock()
    fake_consolidator.run_once = AsyncMock(
        return_value=RunReport(
            run_id="run_abcdef", scanned=10, candidates=3, status="done",
        )
    )
    client = _build_client(store=store, consolidator=fake_consolidator)
    resp = client.post(
        "/api/admin/memory_consolidator/runs",
        json={
            "group_id": "g1",
            "scope": "group",
            "max_batches": 2,
            "batch_size": 30,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["run_id"] == "run_abcdef"
    assert body["data"]["candidates"] == 3
    fake_consolidator.run_once.assert_awaited_once()
    kwargs = fake_consolidator.run_once.await_args.kwargs
    assert kwargs["group_id"] == "g1"
    assert kwargs["max_batches"] == 2
    assert kwargs["batch_size"] == 30


@pytest.mark.asyncio
async def test_decide_candidate_updates_state(store):
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id, domain="slang", scope="group", group_id="g1",
        source_message_pks=[],
        payload={"term": "x"}, confidence=0.5,
    )
    client = _build_client(store=store)
    resp = client.post(
        f"/api/admin/memory_consolidator/candidates/{cid}/decide",
        json={"state": "approved", "decided_by": "alice", "reason": "good"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["new_state"] == "approved"

    refreshed = await store.get_candidate(cid)
    assert refreshed is not None
    assert refreshed.state == "approved"
    assert refreshed.decided_by == "alice"


@pytest.mark.asyncio
async def test_decide_candidate_404_for_unknown(store):
    client = _build_client(store=store)
    resp = client.post(
        "/api/admin/memory_consolidator/candidates/cand_nope/decide",
        json={"state": "approved"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_decide_candidate_rejects_invalid_state(store):
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id, domain="slang", scope="group", group_id="g1",
        source_message_pks=[],
        payload={"term": "x"}, confidence=0.5,
    )
    client = _build_client(store=store)
    resp = client.post(
        f"/api/admin/memory_consolidator/candidates/{cid}/decide",
        json={"state": "weird"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_decide_candidate_promotes_episode_on_approve(
    store, episode_store,
):
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id, domain="episode", scope="group", group_id="g1",
        source_message_pks=[7, 8],
        payload={
            "situation": "user asked about deploys",
            "reflection": "explain step by step",
        },
        confidence=0.8,
    )
    promoter = EpisodePromoter(
        candidates_store=store, episode_store=episode_store,
    )
    client = _build_client(store=store, episode_promoter=promoter)

    resp = client.post(
        f"/api/admin/memory_consolidator/candidates/{cid}/decide",
        json={"state": "approved", "decided_by": "alice"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    promote = body["data"].get("promote")
    assert promote is not None
    assert promote["promoted"] is True
    assert promote["episode_id"]
    assert promote["skipped_reason"] == ""

    episodes = await episode_store.list_episodes(group_id="g1")
    assert len(episodes) == 1
    assert episodes[0].episode_id == promote["episode_id"]
    assert episodes[0].source == "consolidator"
    assert episodes[0].meta["consolidator_candidate_id"] == cid
    assert episodes[0].meta["promoted_by"] == "alice"


@pytest.mark.asyncio
async def test_decide_candidate_no_promote_for_non_episode(
    store, episode_store,
):
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id, domain="slang", scope="group", group_id="g1",
        source_message_pks=[],
        payload={"term": "x"}, confidence=0.5,
    )
    promoter = EpisodePromoter(
        candidates_store=store, episode_store=episode_store,
    )
    client = _build_client(store=store, episode_promoter=promoter)

    resp = client.post(
        f"/api/admin/memory_consolidator/candidates/{cid}/decide",
        json={"state": "approved"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    # Non-episode approval must not invoke promote — no promote key
    assert "promote" not in body["data"]

    episodes = await episode_store.list_episodes()
    assert episodes == []


@pytest.mark.asyncio
async def test_decide_candidate_no_promote_when_rejecting_episode(
    store, episode_store,
):
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id, domain="episode", scope="group", group_id="g1",
        source_message_pks=[],
        payload={"situation": "x"}, confidence=0.5,
    )
    promoter = EpisodePromoter(
        candidates_store=store, episode_store=episode_store,
    )
    client = _build_client(store=store, episode_promoter=promoter)

    resp = client.post(
        f"/api/admin/memory_consolidator/candidates/{cid}/decide",
        json={"state": "rejected"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "promote" not in body["data"]

    episodes = await episode_store.list_episodes()
    assert episodes == []


@pytest.mark.asyncio
async def test_patch_payload_dry_run_succeeds(store):
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id, domain="episode", scope="group", group_id="g1",
        source_message_pks=[],
        payload={
            "situation": "old",
            "observed_context": "",
            "action_taken": "",
            "outcome_signal": "",
            "reflection": "",
        },
        confidence=0.5,
    )
    client = _build_client(store=store)
    resp = client.patch(
        f"/api/admin/memory_consolidator/candidates/{cid}/payload",
        json={
            "actor": "alice",
            "reason": "补 reflection",
            "payload": {
                "situation": "new",
                "reflection": "edited reflection",
                "rogue_unknown_field": "dropped",
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["payload"]["situation"] == "new"
    assert body["data"]["payload"]["reflection"] == "edited reflection"
    assert "rogue_unknown_field" not in body["data"]["payload"]

    refreshed = await store.get_candidate(cid)
    assert refreshed is not None
    assert refreshed.payload["situation"] == "new"


@pytest.mark.asyncio
async def test_patch_payload_post_decision_400(store):
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id, domain="episode", scope="group", group_id="g1",
        source_message_pks=[],
        payload={"situation": "x"}, confidence=0.5,
    )
    await store.decide_candidate(
        cid, state="approved", decided_by="alice", reason="",
    )
    client = _build_client(store=store)
    resp = client.patch(
        f"/api/admin/memory_consolidator/candidates/{cid}/payload",
        json={"payload": {"situation": "post-decision-edit"}},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert "forbidden" in body["error"]


@pytest.mark.asyncio
async def test_patch_payload_404_for_unknown(store):
    client = _build_client(store=store)
    resp = client.patch(
        "/api/admin/memory_consolidator/candidates/cand_nope/payload",
        json={"payload": {"situation": "x"}},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_payload_requires_payload_object(store):
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id, domain="episode", scope="group", group_id="g1",
        source_message_pks=[],
        payload={"situation": "x"}, confidence=0.5,
    )
    client = _build_client(store=store)
    # missing payload
    resp = client.patch(
        f"/api/admin/memory_consolidator/candidates/{cid}/payload",
        json={"actor": "alice"},
    )
    assert resp.status_code == 400
    # payload is wrong type
    resp = client.patch(
        f"/api/admin/memory_consolidator/candidates/{cid}/payload",
        json={"payload": "not-a-dict"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_candidate_revisions_empty(store):
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id, domain="episode", scope="group", group_id="g1",
        source_message_pks=[],
        payload={"situation": "x"}, confidence=0.5,
    )
    client = _build_client(store=store)
    resp = client.get(
        f"/api/admin/memory_consolidator/candidates/{cid}/revisions"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["data"] == []


@pytest.mark.asyncio
async def test_get_candidate_revisions_after_edit(store):
    run_id = await store.start_run(
        triggered_by="test", group_id="g1", scope="group",
    )
    cid = await store.record_candidate(
        run_id=run_id, domain="episode", scope="group", group_id="g1",
        source_message_pks=[],
        payload={"situation": "before"}, confidence=0.5,
    )
    await store.update_candidate_payload(
        cid, payload={"situation": "after"}, actor="alice", reason="补",
    )
    client = _build_client(store=store)
    resp = client.get(
        f"/api/admin/memory_consolidator/candidates/{cid}/revisions"
    )
    body = resp.json()
    assert body["count"] == 1
    rev = body["data"][0]
    assert rev["action"] == "payload_edit"
    assert rev["actor"] == "alice"
    assert rev["before"]["payload"]["situation"] == "before"
    assert rev["after"]["payload"]["situation"] == "after"
    assert rev["meta"]["domain"] == "episode"


@pytest.mark.asyncio
async def test_get_candidate_revisions_404_for_unknown(store):
    client = _build_client(store=store)
    resp = client.get(
        "/api/admin/memory_consolidator/candidates/cand_nope/revisions"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_reflect_returns_503_when_unwired(store):
    client = _build_client(store=store, reflection_generator=None)
    resp = client.post(
        "/api/admin/memory_consolidator/reflect", json={"group_id": "g1"},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["ok"] is False


@pytest.mark.asyncio
async def test_post_reflect_rejects_invalid_scope(store):
    fake_gen = MagicMock()
    fake_gen.run_once = AsyncMock()
    client = _build_client(store=store, reflection_generator=fake_gen)
    resp = client.post(
        "/api/admin/memory_consolidator/reflect",
        json={"scope": "weird"},
    )
    assert resp.status_code == 400
    fake_gen.run_once.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_reflect_invokes_generator(store):
    fake_gen = MagicMock()
    fake_gen.run_once = AsyncMock(
        return_value=ReflectionRunReport(
            run_id="run_xyz",
            signals_total=3,
            signals_skipped_dedup=1,
            candidates=2,
            failures=0,
            status="done",
        )
    )
    client = _build_client(store=store, reflection_generator=fake_gen)
    resp = client.post(
        "/api/admin/memory_consolidator/reflect",
        json={
            "group_id": "g1",
            "scope": "group",
            "max_signals": 5,
            "triggered_by": "alice",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["run_id"] == "run_xyz"
    assert body["data"]["candidates"] == 2
    assert body["data"]["signals_skipped_dedup"] == 1
    fake_gen.run_once.assert_awaited_once()
    kwargs = fake_gen.run_once.await_args.kwargs
    assert kwargs["group_id"] == "g1"
    assert kwargs["scope"] == "group"
    assert kwargs["max_signals"] == 5
    assert kwargs["triggered_by"] == "alice"


@pytest.mark.asyncio
async def test_post_reflect_clamps_max_signals(store):
    fake_gen = MagicMock()
    fake_gen.run_once = AsyncMock(
        return_value=ReflectionRunReport(
            run_id="run_clamped",
            signals_total=0,
            signals_skipped_dedup=0,
            candidates=0,
            failures=0,
            status="done",
        )
    )
    client = _build_client(store=store, reflection_generator=fake_gen)
    resp = client.post(
        "/api/admin/memory_consolidator/reflect",
        json={"max_signals": 9999},
    )
    assert resp.status_code == 200
    kwargs = fake_gen.run_once.await_args.kwargs
    assert kwargs["max_signals"] == 50  # clamped to upper bound
