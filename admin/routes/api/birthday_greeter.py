"""Admin API routes for birthday greeter configuration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request


def create_birthday_router(*, ctx: Any = None) -> APIRouter:
    router = APIRouter(tags=["birthday"])

    def _greeter():
        return getattr(ctx, "birthday_greeter", None) if ctx else None

    @router.get("/birthday/members")
    async def list_members() -> dict[str, Any]:
        greeter = _greeter()
        if greeter is None:
            return {"ok": False, "error": "Birthday greeter not initialized"}
        return {"ok": True, "members": greeter.members}

    @router.post("/birthday/members")
    async def add_member(request: Request) -> dict[str, Any]:
        greeter = _greeter()
        if greeter is None:
            return {"ok": False, "error": "Birthday greeter not initialized"}
        body = await request.json()
        qq = str(body.get("qq", "")).strip()
        name = str(body.get("name", "")).strip()
        birthday_mmdd = str(body.get("birthday_mmdd", "")).strip()
        groups = body.get("groups", [])
        if not qq or not birthday_mmdd:
            return {"ok": False, "error": "qq and birthday_mmdd are required"}
        if not isinstance(groups, list):
            groups = [str(groups)]
        groups = [str(g).strip() for g in groups if str(g).strip()]
        greeter.add_member(qq=qq, name=name, birthday_mmdd=birthday_mmdd, groups=groups)
        return {"ok": True}

    @router.delete("/birthday/members/{qq}")
    async def remove_member(qq: str) -> dict[str, Any]:
        greeter = _greeter()
        if greeter is None:
            return {"ok": False, "error": "Birthday greeter not initialized"}
        removed = greeter.remove_member(qq)
        return {"ok": removed}

    @router.get("/birthday/log")
    async def get_log() -> dict[str, Any]:
        greeter = _greeter()
        if greeter is None:
            return {"ok": False, "error": "Birthday greeter not initialized"}
        return {"ok": True, "sent_log": greeter.sent_log}

    return router
