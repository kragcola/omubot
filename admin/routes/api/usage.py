"""JSON API: usage — port of legacy /admin/usage/data for SPA dashboard.

The Jinja-era /admin/usage/data endpoint was the only legacy /admin/* path
still consumed by the SPA (DashboardView). This router moves the same payload
under /api/admin/usage/data so the legacy Jinja stack can be retired.

The /api/usage/* router (services/llm/usage_routes.py) covers the public
single-stat endpoints (today, month, top-users, top-groups). This router is
specifically the dashboard's combined snapshot.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query


def create_usage_router(usage_tracker: Any = None) -> APIRouter:
    router = APIRouter()

    @router.get("/usage/data")
    async def usage_data(
        period: str = Query("day"),
        date: str | None = Query(None),
    ):
        if usage_tracker is None:
            return {"error": "Usage tracker not available"}
        try:
            ts = await usage_tracker.timeseries(period=period, date=date)
            summary = (
                await usage_tracker.summary_today() if period == "day"
                else await usage_tracker.summary_month(date)
            )
            top_u = await usage_tracker.top_users(days=7, limit=10)
            top_g = await usage_tracker.top_groups(days=7, limit=10)
            by_model = await usage_tracker.usage_by_model(period=period, date=date)
        except Exception as e:
            return {"error": str(e)}

        return {
            "timeseries": ts,
            "summary": summary,
            "top_users": top_u,
            "top_groups": top_g,
            "by_model": by_model,
        }

    return router
