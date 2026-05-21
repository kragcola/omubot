"""JSON API: LearningNormalizer clusters embedded by slang/style consoles."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from fastapi import APIRouter, Query, Request

from services.learning_normalizer import LearningNormalizerStore, get_default_store


def create_learning_normalizer_router(ctx: Any = None) -> APIRouter:
    router = APIRouter()

    async def _store() -> LearningNormalizerStore:
        store = getattr(ctx, "learning_normalizer_store", None) if ctx is not None else None
        if isinstance(store, LearningNormalizerStore):
            if not store.initialized:
                await store.init()
            return store
        store = await get_default_store()
        if ctx is not None:
            with contextlib.suppress(Exception):
                ctx.learning_normalizer_store = store
        return store

    async def _sync_source_normalization(source_table: str, source_id: str, meta: dict[str, Any]) -> None:
        if not source_table or not source_id:
            return
        if source_table == "slang_terms":
            slang_store = getattr(ctx, "slang_store", None) if ctx is not None else None
            if slang_store is None:
                return
            term = await slang_store.get_term(source_id)
            if term is None:
                return
            await slang_store.update_term(
                source_id,
                meta={**term.meta, **meta},
                revision_action="normalizer_sync",
                revision_actor="admin",
                revision_reason="sync learning normalizer metadata",
                revision_meta={"normalization": meta},
            )
            return
        if source_table == "slang_pending_candidates":
            slang_store = getattr(ctx, "slang_store", None) if ctx is not None else None
            if slang_store is None:
                return
            db = slang_store._require_db()
            row = await (
                await db.execute("SELECT meta_json FROM slang_pending_candidates WHERE pending_id = ?", (source_id,))
            ).fetchone()
            if row is None:
                return
            current = _json_loads_dict(row["meta_json"])
            await db.execute(
                "UPDATE slang_pending_candidates SET meta_json = ? WHERE pending_id = ?",
                (json.dumps({**current, **meta}, ensure_ascii=False), source_id),
            )
            await db.commit()
            return
        if source_table == "style_expressions":
            style_store = getattr(ctx, "style_store", None) if ctx is not None else None
            if style_store is None:
                return
            expression = await style_store.get_expression(source_id)
            if expression is None:
                return
            await style_store.update_expression(
                source_id,
                meta={**expression.meta, **meta},
                actor="admin",
                reason="sync learning normalizer metadata",
            )

    async def _sync_item_source(item_id: str) -> None:
        store = await _store()
        item = await store.get_item(item_id)
        cluster = await store.get_cluster(item.cluster_id) if item is not None else None
        if item is None or cluster is None:
            return
        meta = {
            "normalization_cluster_id": item.cluster_id,
            "normalization_item_id": item.item_id,
            "normalized_from": cluster.canonical_text,
            "normalized_key": item.normalized_key,
            "normalization_method": "manual_adjust",
            "normalization_score": 1.0,
            "normalization_features": item.features,
            "auto_merged": False,
            "normalization_created_cluster": False,
        }
        await _sync_source_normalization(item.source_table, item.source_id, meta)

    @router.get("/learning-normalizer/clusters")
    async def list_clusters(
        domain: str = Query(""),
        scope: str = Query(""),
        group_id: str = Query(""),
        status: str = Query(""),
        search: str = Query(""),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        store = await _store()
        clusters, total = await store.list_clusters(
            domain=domain,
            scope=scope,
            group_id=group_id,
            status=status,
            search=search,
            limit=limit,
            offset=offset,
        )
        return {
            "clusters": [store.cluster_to_dict(item) for item in clusters],
            "total": total,
        }

    @router.get("/learning-normalizer/clusters/{cluster_id}/items")
    async def list_cluster_items(cluster_id: str, limit: int = Query(100, ge=1, le=300)):
        store = await _store()
        cluster = await store.get_cluster(cluster_id)
        items = await store.list_cluster_items(cluster_id, limit=limit)
        revisions = await store.list_cluster_revisions(cluster_id, limit=20)
        return {
            "cluster": store.cluster_to_dict(cluster),
            "items": [store.item_to_dict(item) for item in items],
            "revisions": [store.revision_to_dict(item) for item in revisions],
        }

    @router.post("/learning-normalizer/clusters/{cluster_id}/lock")
    async def lock_cluster(cluster_id: str, request: Request):
        store = await _store()
        body = await request.json()
        canonical_text = str(body.get("canonical_text") or "").strip()
        reason = str(body.get("reason") or "")
        try:
            ok = await store.lock_cluster(cluster_id, canonical_text, actor="admin", reason=reason)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": ok}

    @router.post("/learning-normalizer/items/{item_id}/split")
    async def split_item(item_id: str, request: Request):
        store = await _store()
        body = await request.json()
        cluster_id = await store.split_item(item_id, actor="admin", reason=str(body.get("reason") or ""))
        if cluster_id:
            await _sync_item_source(item_id)
        return {"ok": bool(cluster_id), "cluster_id": cluster_id}

    @router.post("/learning-normalizer/revisions/{revision_id}/undo")
    async def undo_revision(revision_id: str):
        store = await _store()
        revision = await store.get_revision(revision_id)
        ok = await store.undo_revision(revision_id, actor="admin")
        if ok and revision is not None and revision.item_id:
            await _sync_item_source(revision.item_id)
        return {"ok": ok}

    return router


def _json_loads_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}
