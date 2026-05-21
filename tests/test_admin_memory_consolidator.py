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
from services.memory_consolidator import (
    ConsolidatorCandidatesStore,
    RunReport,
)


@pytest.fixture
async def store(tmp_path):
    s = ConsolidatorCandidatesStore(str(tmp_path / "consolidator_candidates.db"))
    await s.init()
    yield s
    await s.close()


def _build_client(
    *, store: Any, consolidator: Any | None = None, storage_dir: Any = None,
) -> TestClient:
    ctx = SimpleNamespace(
        memory_consolidator_store=store,
        memory_consolidator=consolidator,
        storage_dir=storage_dir,
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
