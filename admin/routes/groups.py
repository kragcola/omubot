"""Group management page — view overrides, recent messages."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from admin.templates import render


def create_groups_router(
    *,
    group_config: Any = None,
    message_log: Any = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/groups")
    async def groups_page(request: Request):
        overrides: dict[str, dict[str, object]] = {}
        if group_config:
            gcfg = group_config
            overrides = {
                str(gid): {
                    "at_only": o.at_only if o.at_only is not None else gcfg.at_only,
                    "debounce_seconds": (
                        o.debounce_seconds if o.debounce_seconds is not None else gcfg.debounce_seconds
                    ),
                    "batch_size": o.batch_size if o.batch_size is not None else gcfg.batch_size,
                    "blocked_users": o.blocked_users,
                }
                for gid, o in gcfg.overrides.items()
            }
            allowed = [str(g) for g in gcfg.allowed_groups] if gcfg.allowed_groups else []
        else:
            allowed = []

        return await render("groups.html", {
            "request": request,
            "active_page": "groups",
            "overrides": overrides,
            "allowed_groups": allowed,
            "global_at_only": group_config.at_only if group_config else False,
            "global_debounce": group_config.debounce_seconds if group_config else 5.0,
            "global_batch_size": group_config.batch_size if group_config else 10,
        })

    @router.get("/admin/groups/messages")
    async def group_messages(
        group_id: str = Query(...),
        limit: int = Query(20),
    ):
        if message_log is None:
            return {"messages": []}
        try:
            rows = await message_log.query_recent(group_id, limit)
            return {"messages": [dict(r) for r in rows]}
        except Exception as e:
            return {"error": str(e), "messages": []}

    return router
