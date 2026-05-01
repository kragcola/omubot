"""Usage admin routes: stats pages + JSON API for Chart.js."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from admin.templates import render


def create_usage_admin_router(usage_tracker: Any = None) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/usage")
    async def usage_page(request: Request):
        return await render("usage.html", {
            "request": request,
            "active_page": "usage",
        })

    @router.get("/admin/usage/data")
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
