"""JSON API: schedule — mood profile, daily schedule."""

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter

CST = ZoneInfo("Asia/Shanghai")


def create_schedule_router(
    *,
    mood_engine: Any = None,
    schedule_store: Any = None,
    talk_schedule: Any = None,
    dream_agent: Any = None,
    ctx: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _mood():
        return mood_engine or getattr(ctx, "mood_engine", None)

    def _sched_store():
        return schedule_store or getattr(ctx, "schedule_store", None)

    def _get_time_multiplier():
        """Resolve time multiplier — try ctx then direct import."""
        ts = talk_schedule or getattr(ctx, "talk_schedule", None)
        if ts is not None:
            try:
                return ts.get_time_multiplier()
            except Exception:
                pass
        try:
            from services.talk_schedule import get_time_multiplier
            return get_time_multiplier()
        except Exception:
            return None

    def _dream():
        return dream_agent or getattr(ctx, "dream", None)

    @router.get("/mood")
    async def get_mood():
        me = _mood()
        if me is None:
            return {"error": "MoodEngine not available"}

        try:
            # MoodEngine.evaluate() requires a schedule (can be None)
            today = datetime.now(CST).strftime("%Y-%m-%d")
            schedule = None
            ss = _sched_store()
            if ss is not None:
                with contextlib.suppress(Exception):
                    schedule = ss.load(today)

            profile = me.evaluate(schedule=schedule)
            return {
                "energy": getattr(profile, "energy", 0.5),
                "valence": getattr(profile, "valence", 0.0),
                "openness": getattr(profile, "openness", 0.5),
                "tension": getattr(profile, "tension", 0.3),
                "label": getattr(profile, "label", "平静"),
                "prompt": me.mood_prompt(profile) if hasattr(me, "mood_prompt") else "",
            }
        except Exception as e:
            return {"error": str(e)}

    @router.get("/schedule")
    async def get_schedule():
        today = datetime.now(CST).strftime("%Y-%m-%d")

        result: dict[str, Any] = {"date": today, "schedule": None}

        ss = _sched_store()
        result["store_available"] = ss is not None
        if ss is not None:
            try:
                schedule = ss.load(today)
                if schedule:
                    result["schedule"] = {
                        "theme": getattr(schedule, "theme", ""),
                        "day_narrative": getattr(schedule, "day_narrative", ""),
                        "slots": [
                            {
                                "time": s.time,
                                "activity": s.activity,
                                "mood_hint": s.mood_hint,
                                "location": getattr(s, "location", ""),
                            }
                            for s in (schedule.slots or [])
                        ],
                    }
            except Exception as e:
                result["error"] = str(e)

        result["time_multiplier"] = _get_time_multiplier() or 1.0

        dream = _dream()
        if dream is not None:
            with contextlib.suppress(Exception):
                result["dream"] = {
                    "running": getattr(dream, "_running", False),
                    "interval_hours": getattr(dream, "_interval_hours", 0),
                }

        return result

    return router
