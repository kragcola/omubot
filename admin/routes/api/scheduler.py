"""JSON API: scheduler — slot state, mute/unmute."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter


def create_scheduler_router(
    *,
    scheduler: Any = None,
    ctx: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _sched():
        return scheduler or getattr(ctx, "scheduler", None)

    @router.get("/scheduler")
    async def get_slots():
        s = _sched()
        if s is None:
            return {"slots": {}}

        try:
            slots = s.get_all_slots()
            return {"slots": slots}
        except Exception as e:
            return {"slots": {}, "error": str(e)}

    @router.post("/scheduler/{group_id}/mute")
    async def mute_group(group_id: str):
        s = _sched()
        if s is None:
            return {"ok": False, "error": "Scheduler not available"}

        try:
            s.mute(group_id)
            return {"ok": True, "group_id": group_id, "muted": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @router.post("/scheduler/{group_id}/unmute")
    async def unmute_group(group_id: str):
        s = _sched()
        if s is None:
            return {"ok": False, "error": "Scheduler not available"}

        try:
            s.unmute(group_id)
            return {"ok": True, "group_id": group_id, "muted": False}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @router.get("/scheduler/mute_state")
    async def get_mute_state():
        s = _sched()
        if s is None:
            return {"groups": {}}

        try:
            getter = getattr(s, "get_mute_state", None)
            groups = getter() if callable(getter) else {}
            return {"groups": groups}
        except Exception as e:
            return {"groups": {}, "error": str(e)}

    return router
