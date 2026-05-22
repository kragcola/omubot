"""JSON API: dashboard — uptime, today stats, mood summary."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from services.llm.llm_pipelines import (
    build_cache_pipelines_payload,
    fold_recent_into_pipelines,
)


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

        # Compute the dashboard-canonical cache-hit ratio using the same
        # numerator/denominator the new ``/dashboard/cache-pipelines``
        # endpoint uses, so the hero "Cache 命中" KPI and the per-pipeline
        # panel cannot drift. Falls back to None when the bot has not made
        # any LLM call today (or all today's calls produced no
        # prompt_cache_hit/miss tokens — e.g. legacy rows from before the
        # spine migration).
        if isinstance(usage_summary, dict):
            hit = int(usage_summary.get("prompt_cache_hit_tokens", 0) or 0)
            miss = int(usage_summary.get("prompt_cache_miss_tokens", 0) or 0)
            denom = hit + miss
            usage_summary["cache_hit_pct"] = (hit / denom) if denom > 0 else None

        schedule = None
        schedule_obj = None
        store = _schedule()
        if store is not None:
            try:
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

    @router.get("/dashboard/cache-pipelines")
    async def cache_pipelines(period: str = Query("day")):
        """Per-pipeline cache hit rate for the dashboard panel."""
        if period not in {"day", "week", "month"}:
            return {"error": f"unsupported period: {period}"}

        tracker = _usage()
        if tracker is None:
            return {"error": "Usage tracker not available"}

        try:
            rows = await tracker.cache_hit_by_call_type(period=period)
            recent_per_call_type = await tracker.recent_calls_per_pipeline(
                period=period, limit=5,
            )
            recent_overall = await tracker.recent_calls_overall(
                period=period, limit=5,
            )
        except Exception as exc:
            return {"error": str(exc)[:200]}

        payload = build_cache_pipelines_payload(rows)
        payload = fold_recent_into_pipelines(
            payload, recent_per_call_type, recent_overall, limit=5,
        )

        return {
            "period": period,
            "generated_at": datetime.now(UTC).isoformat(),
            **payload,
        }

    return router
