"""Dashboard overview page — bot status, today's usage cards, alerts."""

from __future__ import annotations

import contextlib
import time
from typing import Any

from fastapi import APIRouter, Request

from admin.templates import render


def create_dashboard_router(
    *,
    usage_tracker: Any = None,
    message_log: Any = None,
    group_config: Any = None,
    admins: dict[str, str] | None = None,
    bot_start_time: float = 0.0,
) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/")
    async def dashboard(request: Request):
        uptime_s = time.time() - bot_start_time if bot_start_time else 0
        uptime_str = _fmt_uptime(uptime_s)

        today = {}
        if usage_tracker:
            with contextlib.suppress(Exception):
                today = await usage_tracker.summary_today() or {}

        return await render("dashboard.html", {
            "request": request,
            "active_page": "dashboard",
            "uptime": uptime_str,
            "today": today,
            "admin_count": len(admins or {}),
        })

    return router


def _fmt_uptime(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m"
