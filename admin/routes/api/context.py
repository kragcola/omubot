"""JSON API: unified context debugging."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query


def create_context_router(
    *,
    ctx: Any = None,
    bus: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _context_service() -> Any:
        service = getattr(ctx, "context_service", None) if ctx is not None else None
        if service is not None:
            return service
        if ctx is None:
            return None
        from services.context import ContextService

        service = ContextService.from_runtime(ctx, bus=bus)
        ctx.context_service = service
        return service

    @router.get("/context/search")
    async def context_search(
        q: str = Query(""),
        user_id: str = Query(""),
        group_id: str | None = Query(None),
        top_k: int = Query(10, ge=1, le=30),
        max_chars: int | None = Query(
            None,
            ge=300,
            le=12000,
            description="legacy 字符截断；不传则用 ContextService 的多桶 token 预算（PR4 默认）",
        ),
    ):
        service = _context_service()
        if service is None:
            return {
                "available": False,
                "query": q,
                "hits": [],
                "pack": {"text": "", "hits": [], "omitted_count": 0},
            }

        pack_kwargs: dict[str, Any] = {
            "user_id": user_id,
            "group_id": group_id,
            "top_k": top_k,
        }
        if max_chars is not None:
            pack_kwargs["max_chars"] = max_chars
        pack = await service.build_prompt_context(q, **pack_kwargs)
        return {
            "available": True,
            "query": q,
            "user_id": user_id,
            "group_id": group_id,
            "hits": [hit.to_dict() for hit in pack.hits],
            "pack": pack.to_dict(),
        }

    @router.get("/context/recent")
    async def context_recent(limit: int = Query(20, ge=1, le=80)):
        service = _context_service()
        if service is None:
            return {"available": False, "items": []}
        return {"available": True, "items": service.recent(limit=limit)}

    @router.get("/context/metrics")
    async def context_metrics(limit: int = Query(80, ge=1, le=200)):
        service = _context_service()
        if service is None:
            return {"available": False, "metrics": {}}
        if hasattr(service, "metrics"):
            return {"available": True, "metrics": service.metrics(limit=limit)}
        return {"available": True, "metrics": {"recent": service.recent(limit=limit)}}

    return router
