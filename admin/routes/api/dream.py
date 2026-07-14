"""JSON API: dream — dream agent status and manual trigger."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from fastapi import APIRouter


def create_dream_router(
    *,
    dream_agent: Any = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/dream")
    async def dream_status():
        if dream_agent is None:
            return {"available": False}

        snapshot = getattr(dream_agent, "snapshot", None)
        if not callable(snapshot):
            return {"available": True}
        snapshot_call = cast(Callable[[], Mapping[str, object]], snapshot)
        return {"available": True, **dict(snapshot_call())}

    @router.post("/dream/trigger")
    async def trigger_dream():
        if dream_agent is None:
            return {"ok": False, "error": "DreamAgent not available"}

        try:
            trigger_once = getattr(dream_agent, "trigger_once", None)
            if not callable(trigger_once):
                return {"ok": False, "error": "DreamAgent has no trigger_once method"}
            trigger_once_call = cast(Callable[[], Awaitable[None]], trigger_once)
            await trigger_once_call()
            return {"ok": True, "message": "Dream cycle triggered"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return router
