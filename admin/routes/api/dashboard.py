"""JSON API: dashboard — uptime, today stats, mood summary."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter


def create_dashboard_router(
    *,
    usage_tracker: Any = None,
    bot_start_time: float = 0.0,
    mood_engine: Any = None,
    schedule_store: Any = None,
    ctx: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _usage():
        return usage_tracker or (getattr(ctx, "usage_tracker", None) if ctx else usage_tracker)

    def _mood():
        return mood_engine or (getattr(ctx, "mood_engine", None) if ctx else mood_engine)

    def _schedule():
        return schedule_store or (getattr(ctx, "schedule_store", None) if ctx else schedule_store)

    @router.get("/dashboard")
    async def dashboard():
        uptime_seconds = time.time() - bot_start_time if bot_start_time else 0.0

        usage_summary: dict[str, Any] = {}
        tracker = _usage()
        if tracker is not None:
            try:
                usage_summary = await tracker.summary_today()
            except Exception:
                pass

        schedule = None
        schedule_obj = None
        store = _schedule()
        if store is not None:
            try:
                from datetime import datetime
                today = datetime.now().strftime("%Y-%m-%d")
                schedule_obj = store.load(today)
                if schedule_obj:
                    schedule = {
                        "theme": getattr(schedule_obj, "theme", ""),
                        "day_narrative": getattr(schedule_obj, "day_narrative", ""),
                        "slots": [
                            {
                                "time": s.time,
                                "activity": s.activity,
                                "mood_hint": s.mood_hint,
                                "location": getattr(s, "location", ""),
                            }
                            for s in (getattr(schedule_obj, "slots", None) or [])
                        ],
                    }
            except Exception:
                pass

        mood = None
        engine = _mood()
        if engine is not None:
            try:
                profile = engine.evaluate(schedule_obj)
                mood = {
                    "energy": getattr(profile, "energy", 0.5),
                    "valence": getattr(profile, "valence", 0.0),
                    "openness": getattr(profile, "openness", 0.5),
                    "tension": getattr(profile, "tension", 0.3),
                    "label": getattr(profile, "label", "平静"),
                }
            except Exception:
                pass

        return {
            "uptime_seconds": uptime_seconds,
            "usage": usage_summary,
            "mood": mood,
            "schedule": schedule,
        }

    return router
