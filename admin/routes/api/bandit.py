"""JSON API: RWS bandit state and manual observations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel


class BanditObservationPayload(BaseModel):
    decision: bool
    reward: float


def create_bandit_router(
    *,
    scheduler: Any = None,
    ctx: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _scheduler() -> Any:
        return scheduler or getattr(ctx, "scheduler", None)

    @router.get("/bandit/rws")
    async def rws_bandit_state():
        sched = _scheduler()
        if sched is None or not hasattr(sched, "get_rws_bandit_state"):
            return {"available": False}
        return sched.get_rws_bandit_state()

    @router.get("/bandit/rws/summary")
    async def rws_reward_summary():
        sched = _scheduler()
        if sched is None or not hasattr(sched, "get_rws_reward_summary"):
            return {"available": False}
        return await sched.get_rws_reward_summary()

    @router.post("/bandit/rws/observe")
    async def observe_rws_bandit(payload: BanditObservationPayload):
        sched = _scheduler()
        if sched is None or not hasattr(sched, "observe_rws_bandit"):
            return {"ok": False, "error": "RWS bandit not available"}
        return sched.observe_rws_bandit(decision=payload.decision, reward=payload.reward)

    return router
