"""JSON API: scheduler counterfactual replay reports."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from services.scheduler_replay import ReplayStore


def create_replay_router(
    *,
    store: ReplayStore | None = None,
    ctx: Any = None,
    db_path: str = "storage/scheduler_replay.db",
) -> APIRouter:
    router = APIRouter()
    replay_store = store or getattr(ctx, "scheduler_replay_store", None) or ReplayStore(db_path)

    @router.get("/replay/weekly")
    async def weekly_replay(limit: int = 20):
        try:
            runs = replay_store.list_runs(limit=limit)
        except Exception as exc:
            return {"runs": [], "error": str(exc)}
        return {"runs": runs}

    return router
