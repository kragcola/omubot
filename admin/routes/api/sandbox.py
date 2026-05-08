"""JSON API: sandbox — simulated chat for debugging."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request


def create_sandbox_router(
    *,
    llm_client: Any = None,
    identity_mgr: Any = None,
    ctx: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _llm():
        return llm_client or (getattr(ctx, "llm_client", None) if ctx else llm_client)

    def _identity():
        return identity_mgr or (getattr(ctx, "identity_mgr", None) if ctx else identity_mgr)

    @router.post("/sandbox/chat")
    async def sandbox_chat(request: Request):
        client = _llm()
        if client is None:
            return {"error": "LLMClient not available"}

        body = await request.json()
        message = body.get("message", "").strip()
        if not message:
            return {"error": "Message is required"}

        simulate = body.get("simulate", "private")
        user_id = body.get("user_id", "sandbox_user")
        group_id = body.get("group_id")

        identity = None
        id_mgr = _identity()
        if id_mgr is not None:
            identity = id_mgr.resolve()

        try:
            from services.tools.context import ToolContext

            session_id = f"sandbox_{user_id}"
            tool_ctx = ToolContext(bot=None, user_id=user_id, group_id=group_id)

            reply = await client.chat(
                session_id=session_id,
                user_id=user_id,
                user_content=message,
                identity=identity,
                group_id=group_id,
                ctx=tool_ctx,
                on_segment=None,
                privacy_mask=simulate == "group",
                force_reply=True,
            )
            return {"reply": reply or "(no reply)"}
        except Exception as e:
            return {"error": str(e)}

    return router
