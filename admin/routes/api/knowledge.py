"""JSON API: knowledge — stats, indexing, and structured search."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query


def create_knowledge_router(
    *,
    knowledge_base: Any = None,
    ctx: Any = None,
    bus: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _resolve_knowledge_base() -> Any:
        """Resolve the live knowledge service lazily.

        Admin router creation happens before plugin startup in some flows, so
        a startup-time snapshot is not enough. Prefer ctx, then bus plugin.
        """
        live = getattr(ctx, "knowledge_base", None) if ctx is not None else None
        if live is not None:
            return live
        if knowledge_base is not None:
            return knowledge_base
        plugin = bus.get_plugin("knowledge") if bus is not None and hasattr(bus, "get_plugin") else None
        if plugin is not None:
            return (
                getattr(plugin, "knowledge_base", None)
                or getattr(plugin, "_kb", None)
            )
        return None

    def _resolve_knowledge_graph() -> Any:
        return getattr(ctx, "knowledge_graph", None) if ctx is not None else None

    def _serialize_hit(hit: Any) -> dict[str, Any]:
        if hasattr(hit, "to_dict"):
            return hit.to_dict()
        if isinstance(hit, dict):
            return dict(hit)
        if isinstance(hit, (list, tuple)) and len(hit) >= 2:
            return {
                "content": hit[0],
                "source": hit[1],
                "title": str(hit[1]),
                "score": 0.0,
                "chunk_id": "",
            }
        return {
            "content": str(hit),
            "source": "",
            "title": "",
            "score": 0.0,
            "chunk_id": "",
        }

    def _base_payload(kb: Any) -> dict[str, Any]:
        if kb is None:
            return {
                "available": False,
                "entry_count": 0,
                "results": [],
                "stats": {
                    "loaded": False,
                    "chunk_count": 0,
                    "source_count": 0,
                },
            }
        stats = kb.stats() if hasattr(kb, "stats") else {
            "loaded": bool(getattr(kb, "loaded", False)),
            "chunk_count": int(getattr(kb, "chunk_count", 0) or 0),
        }
        return {
            "available": True,
            "entry_count": int(stats.get("chunk_count", getattr(kb, "chunk_count", 0)) or 0),
            "results": [],
            "stats": stats,
        }

    @router.get("/knowledge")
    async def knowledge_info(
        q: str = Query(""),
        top_k: int = Query(20, ge=1, le=50),
    ):
        kb = _resolve_knowledge_base()
        payload = _base_payload(kb)
        if kb is None:
            return payload

        # Keep the old endpoint useful as a stats refresh: if not loaded yet,
        # build the index once. Search itself does not force repeated reloads.
        if not getattr(kb, "loaded", False) and hasattr(kb, "reload"):
            try:
                payload["entry_count"] = int(kb.reload())
                if hasattr(kb, "stats"):
                    payload["stats"] = kb.stats()
            except Exception as exc:
                payload["available"] = False
                payload["error"] = f"reload_failed:{type(exc).__name__}"
                return payload

        if q.strip():
            payload["results"] = _search(kb, q, top_k=top_k)
            payload["entry_count"] = int(getattr(kb, "chunk_count", payload["entry_count"]) or 0)
        return payload

    @router.get("/knowledge/stats")
    async def knowledge_stats():
        kb = _resolve_knowledge_base()
        return _base_payload(kb)

    @router.post("/knowledge/reindex")
    async def knowledge_reindex():
        kb = _resolve_knowledge_base()
        if kb is None:
            return {"ok": False, "available": False, "entry_count": 0, "error": "knowledge_base_unavailable"}
        try:
            count = kb.reindex() if hasattr(kb, "reindex") else kb.reload()
            return {
                "ok": True,
                "available": True,
                "entry_count": count,
                "stats": kb.stats() if hasattr(kb, "stats") else {},
            }
        except Exception as exc:
            return {"ok": False, "available": True, "error": f"reindex_failed:{type(exc).__name__}"}

    @router.get("/knowledge/sources")
    async def knowledge_sources():
        kb = _resolve_knowledge_base()
        if kb is None:
            return {"available": False, "sources": []}
        if not getattr(kb, "loaded", False) and hasattr(kb, "reload"):
            kb.reload()
        sources = []
        if hasattr(kb, "sources"):
            sources = [
                source.to_dict() if hasattr(source, "to_dict") else dict(source)
                for source in kb.sources()
            ]
        return {"available": True, "sources": sources}

    @router.get("/knowledge/search")
    async def knowledge_search(
        q: str = Query(""),
        top_k: int = Query(10, ge=1, le=50),
    ):
        kb = _resolve_knowledge_base()
        if kb is None:
            return {"available": False, "results": []}
        return {
            "available": True,
            "query": q,
            "results": _search(kb, q, top_k=top_k),
        }

    @router.get("/knowledge/graph/entities")
    async def graph_entities(limit: int = Query(100, ge=1, le=500)):
        graph = _resolve_knowledge_graph()
        if graph is None:
            return {"available": False, "entities": []}
        return {"available": True, "entities": await graph.list_entities(limit=limit)}

    @router.get("/knowledge/graph/relationships")
    async def graph_relationships(limit: int = Query(100, ge=1, le=500)):
        graph = _resolve_knowledge_graph()
        if graph is None:
            return {"available": False, "relationships": []}
        return {"available": True, "relationships": await graph.list_relationships(limit=limit)}

    @router.get("/knowledge/graph/scope-risks")
    async def graph_scope_risks(limit: int = Query(100, ge=1, le=500)):
        graph = _resolve_knowledge_graph()
        if graph is None:
            return {"available": False, "relationships": []}
        if not hasattr(graph, "list_scope_risks"):
            return {"available": True, "relationships": [], "error": "scope_risk_unavailable"}
        return {"available": True, "relationships": await graph.list_scope_risks(limit=limit)}

    @router.get("/knowledge/graph/relationships/{fact_id}")
    async def graph_relationship_detail(fact_id: str):
        graph = _resolve_knowledge_graph()
        if graph is None:
            return {"available": False, "relationship": None}
        if not hasattr(graph, "get_relationship"):
            return {"available": True, "relationship": None, "error": "relationship_detail_unavailable"}
        relationship = await graph.get_relationship(fact_id)
        return {"available": True, "relationship": relationship}

    @router.post("/knowledge/graph/relationships/{fact_id}/rollback")
    async def graph_relationship_rollback(fact_id: str, note: str = ""):
        graph = _resolve_knowledge_graph()
        if graph is None:
            return {"ok": False, "available": False, "error": "knowledge_graph_unavailable"}
        if not hasattr(graph, "rollback_relationship"):
            return {"ok": False, "available": True, "error": "rollback_unavailable"}
        ok = await graph.rollback_relationship(fact_id, note=note)
        return {"ok": ok, "available": True}

    @router.post("/knowledge/graph/relationships/{fact_id}/supersede")
    async def graph_relationship_supersede(fact_id: str, payload: dict[str, Any]):
        graph = _resolve_knowledge_graph()
        if graph is None:
            return {"ok": False, "available": False, "error": "knowledge_graph_unavailable"}
        if not hasattr(graph, "supersede_relationship"):
            return {"ok": False, "available": True, "error": "supersede_unavailable"}
        fact = await graph.supersede_relationship(
            fact_id,
            subject=str(payload.get("subject") or ""),
            predicate=str(payload.get("predicate") or ""),
            object=str(payload.get("object") or ""),
            confidence=float(payload.get("confidence") or 0.85),
            source=str(payload.get("source") or "admin"),
            evidence=payload.get("evidence") if isinstance(payload.get("evidence"), dict) else None,
            note=str(payload.get("note") or ""),
        )
        if fact is None:
            return {"ok": False, "available": True, "error": "relationship_not_found_or_inactive"}
        return {"ok": True, "available": True, "fact": fact.to_dict()}

    @router.get("/knowledge/graph/candidates")
    async def graph_candidates(
        status: str = Query("pending"),
        limit: int = Query(100, ge=1, le=500),
    ):
        graph = _resolve_knowledge_graph()
        if graph is None:
            return {"available": False, "candidates": []}
        return {
            "available": True,
            "candidates": await graph.list_candidates(status=status, limit=limit),
        }

    @router.post("/knowledge/graph/candidates/{candidate_id}/approve")
    async def graph_candidate_approve(candidate_id: str):
        graph = _resolve_knowledge_graph()
        if graph is None:
            return {"ok": False, "available": False, "error": "knowledge_graph_unavailable"}
        fact = await graph.approve_candidate(candidate_id)
        if fact is None:
            return {"ok": False, "available": True, "error": "candidate_not_found_or_not_pending"}
        return {"ok": True, "available": True, "fact": fact.to_dict()}

    @router.post("/knowledge/graph/candidates/{candidate_id}/reject")
    async def graph_candidate_reject(candidate_id: str, note: str = ""):
        graph = _resolve_knowledge_graph()
        if graph is None:
            return {"ok": False, "available": False, "error": "knowledge_graph_unavailable"}
        ok = await graph.reject_candidate(candidate_id, note=note)
        return {"ok": ok, "available": True}

    def _search(kb: Any, query: str, *, top_k: int) -> list[dict[str, Any]]:
        if hasattr(kb, "search_hits"):
            return [_serialize_hit(hit) for hit in kb.search_hits(query, top_k=top_k)]
        if hasattr(kb, "search"):
            return [_serialize_hit(hit) for hit in kb.search(query, top_k=top_k)]
        if hasattr(kb, "retrieve"):
            return [
                {
                    "content": content,
                    "source": "",
                    "title": "",
                    "score": 0.0,
                    "chunk_id": "",
                }
                for content in kb.retrieve(query, top_k=top_k)
            ]
        return []

    return router
