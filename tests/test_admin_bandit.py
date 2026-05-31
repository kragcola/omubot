from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.routes.api.bandit import create_bandit_router
from services.scheduler_rws import RWSBandit


class _Scheduler:
    def __init__(self) -> None:
        self.bandit = RWSBandit(theta=0.5, epsilon=0.0, learning_rate=0.1, frozen=False)

    def get_rws_bandit_state(self) -> dict[str, object]:
        return {
            "available": True,
            "theta": self.bandit.theta,
            "observations": self.bandit.observations,
        }

    def observe_rws_bandit(self, *, decision: bool, reward: float) -> dict[str, object]:
        return {"ok": True, "theta": self.bandit.observe(decision=decision, reward=reward)}


def test_bandit_router_reports_and_observes() -> None:
    app = FastAPI()
    app.include_router(create_bandit_router(scheduler=_Scheduler()), prefix="/api/admin")
    client = TestClient(app)

    state = client.get("/api/admin/bandit/rws")
    assert state.status_code == 200
    assert state.json()["available"] is True

    observed = client.post("/api/admin/bandit/rws/observe", json={"decision": True, "reward": -1})
    assert observed.status_code == 200
    assert observed.json()["ok"] is True
    # Thompson (default): a fire that earned negative reward raises theta (fire
    # less next time). The router just plumbs observe() through to the bandit.
    assert observed.json()["theta"] > 0.5
