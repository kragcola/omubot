"""JSON API: dream — dream agent status and manual trigger."""

from __future__ import annotations

from typing import Any

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

        return {
            "available": True,
            "running": getattr(dream_agent, "_running", False),
            "interval_hours": getattr(dream_agent, "_interval_hours", 0),
            "max_rounds": getattr(dream_agent, "_max_rounds", 5),
        }

    @router.post("/dream/trigger")
    async def trigger_dream():
        if dream_agent is None:
            return {"ok": False, "error": "DreamAgent not available"}

        try:
            # DreamAgent.start() expects an api_call callable
            # We pass None to signal a one-shot manual trigger
            if hasattr(dream_agent, "_do_cycle"):
                import asyncio
                asyncio.create_task(dream_agent._do_cycle())
            elif hasattr(dream_agent, "start"):
                return {"ok": False, "error": "DreamAgent already has a running loop"}
            else:
                return {"ok": False, "error": "DreamAgent has no _do_cycle method"}
            return {"ok": True, "message": "Dream cycle triggered"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return router
