from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.routes.api.replay import create_replay_router
from services.scheduler_replay import ReplayJudgement, ReplaySample, ReplayStore


def test_replay_router_returns_weekly_runs(tmp_path) -> None:
    store = ReplayStore(tmp_path / "replay.db")
    sample = ReplaySample(
        group_id="100",
        message_id=1,
        created_at=1000,
        actual_decision="reply",
        counterfactual_decision="skip",
        context="hello",
    )
    store.record_run(
        run_id="run-1",
        group_id="100",
        judgements=[ReplayJudgement(sample=sample, label="real_better")],
    )
    app = FastAPI()
    app.include_router(create_replay_router(store=store), prefix="/api/admin")

    resp = TestClient(app).get("/api/admin/replay/weekly")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["runs"][0]["run_id"] == "run-1"
    assert payload["runs"][0]["summary"]["real_better"] == 1
