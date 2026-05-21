"""JSON API: block-trace — prompt block trace inspection and management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from services.block_trace.store import BlockTraceStore


def create_block_trace_router(
    *,
    ctx: Any = None,
    bus: Any = None,
) -> APIRouter:
    router = APIRouter(prefix="/block-trace", tags=["block-trace"])

    def _resolve_store() -> BlockTraceStore | None:
        if ctx is not None:
            return getattr(ctx, "block_trace_store", None)
        return None

    @router.get("/recent")
    async def recent(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
        store = _resolve_store()
        if store is None:
            return {"ok": False, "error": "BlockTraceStore not available"}
        traces = await store.recent(limit=limit)
        return {"ok": True, "traces": [t.to_dict() for t in traces]}

    @router.get("/request/{request_id}")
    async def by_request(request_id: str) -> dict[str, Any]:
        store = _resolve_store()
        if store is None:
            return {"ok": False, "error": "BlockTraceStore not available"}
        traces = await store.list_for_request(request_id)
        return {"ok": True, "traces": [t.to_dict() for t in traces]}

    @router.get("/stats")
    async def stats() -> dict[str, Any]:
        store = _resolve_store()
        if store is None:
            return {"ok": False, "error": "BlockTraceStore not available"}
        data = await store.stats()
        return {"ok": True, **data}

    @router.get("/search")
    async def search(
        source: str = Query(...),
        source_id: str = Query(...),
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        store = _resolve_store()
        if store is None:
            return {"ok": False, "error": "BlockTraceStore not available"}
        traces = await store.find_by_source_ref(
            source=source, source_id=source_id, limit=limit,
        )
        return {"ok": True, "traces": [t.to_dict() for t in traces]}

    @router.post("/prune")
    async def prune(
        keep_days: int = Query(7, ge=1, le=90),
    ) -> dict[str, Any]:
        store = _resolve_store()
        if store is None:
            return {"ok": False, "error": "BlockTraceStore not available"}
        deleted = await store.prune(keep_days=keep_days)
        return {"ok": True, "deleted": deleted}

    @router.get("/alignment")
    async def alignment(limit: int = Query(500, ge=1, le=2000)) -> dict[str, Any]:
        """Source-by-source provider vs plugin alignment report.

        Aggregates recent traces and counts traces emitted by providers
        (provider name ending with "_provider") versus plugin-native paths
        ("_plugin" suffix or empty). Used to verify that active-mode rollout
        is fully on the provider path with no plugin double-injection.
        """
        store = _resolve_store()
        if store is None:
            return {"ok": False, "error": "BlockTraceStore not available"}
        traces = await store.recent(limit=limit)
        rows: dict[str, dict[str, int]] = {}
        for t in traces:
            row = rows.setdefault(
                t.source or "unknown",
                {"provider": 0, "plugin": 0, "accepted": 0, "trimmed": 0,
                 "rejected": 0, "shadow_only": 0},
            )
            if t.provider.endswith("_provider"):
                row["provider"] += 1
            else:
                row["plugin"] += 1
            row[t.decision] = row.get(t.decision, 0) + 1
        # Mode hint: derive from provider trace count vs plugin trace count.
        provider_total = sum(r["provider"] for r in rows.values())
        plugin_total = sum(r["plugin"] for r in rows.values())
        if provider_total == 0 and plugin_total > 0:
            mode = "plugin_only"
        elif provider_total > 0 and plugin_total == 0:
            mode = "active"
        elif provider_total > 0 and plugin_total > 0:
            mode = "shadow_or_overlap"
        else:
            mode = "empty"
        return {
            "ok": True,
            "sample_size": len(traces),
            "mode": mode,
            "by_source": [
                {"source": src, **counts}
                for src, counts in sorted(rows.items())
            ],
        }

    return router
