"""JSON API: cross-group visibility management."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from services.episodic.store import EpisodeStore
from services.knowledge_graph.store import KnowledgeGraphStore
from services.learning_normalizer.store import LearningNormalizerStore
from services.slang.store import SlangStore
from services.style.store import StyleStore

# 5 store types accepted by /cross-group/enable
_VALID_STORES: tuple[str, ...] = (
    "slang",
    "style",
    "episode",
    "normalizer",
    "graph_fact",
    "graph_candidate",
)


def create_cross_group_router(*, ctx: Any = None, bus: Any = None) -> APIRouter:
    router = APIRouter(prefix="/cross-group", tags=["cross-group"])

    async def _slang_store() -> SlangStore:
        if bus is not None and hasattr(bus, "get_plugin"):
            plugin = bus.get_plugin("slang")
            if plugin is not None:
                store = getattr(plugin, "store", None)
                if store is not None:
                    return store
        store_attr = getattr(ctx, "slang_store", None) if ctx else None
        if store_attr is not None:
            return store_attr
        storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx else Path("storage")
        s = SlangStore(storage_dir / "slang.db")
        await s.init()
        return s

    async def _style_store() -> StyleStore:
        if bus is not None and hasattr(bus, "get_plugin"):
            plugin = bus.get_plugin("style")
            if plugin is not None:
                store = getattr(plugin, "store", None)
                if store is not None:
                    return store
        store_attr = getattr(ctx, "style_store", None) if ctx else None
        if store_attr is not None:
            return store_attr
        storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx else Path("storage")
        s = StyleStore(db_path=str(storage_dir / "style.db"))
        await s.init()
        return s

    async def _episodic_store() -> EpisodeStore:
        store_attr = getattr(ctx, "episode_store", None) if ctx else None
        if store_attr is not None:
            return store_attr
        storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx else Path("storage")
        s = EpisodeStore(db_path=str(storage_dir / "episodic.db"))
        await s.init()
        return s

    async def _normalizer_store() -> LearningNormalizerStore:
        store_attr = getattr(ctx, "learning_normalizer_store", None) if ctx else None
        if store_attr is not None:
            return store_attr
        storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx else Path("storage")
        s = LearningNormalizerStore(db_path=storage_dir / "learning_normalizer.db")
        await s.init()
        return s

    async def _graph_store() -> KnowledgeGraphStore:
        store_attr = getattr(ctx, "knowledge_graph_store", None) if ctx else None
        if store_attr is not None:
            return store_attr
        storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx else Path("storage")
        s = KnowledgeGraphStore(db_path=storage_dir / "knowledge_graph.db")
        await s.init()
        return s

    async def _read_json(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    def _row_to_item(
        store_name: str,
        item_id: str,
        label: str,
        detail: str,
        scope: str,
        group_id: str,
        status: str,
        confidence: float | None,
        enabled_by: str,
        enabled_at: str,
        enabled_for_groups: str,
        enabled_reason: str,
    ) -> dict[str, Any]:
        try:
            groups = json.loads(enabled_for_groups or "[]")
            if not isinstance(groups, list):
                groups = []
        except (ValueError, TypeError):
            groups = []
        return {
            "store": store_name,
            "item_id": item_id,
            "label": label,
            "detail": detail,
            "scope": scope,
            "group_id": group_id,
            "status": status,
            "confidence": confidence,
            "enabled_by": enabled_by,
            "enabled_at": enabled_at,
            "enabled_for_groups": [str(g) for g in groups if str(g).strip()],
            "enabled_reason": enabled_reason or "",
        }

    @router.get("/items")
    async def list_cross_group_items():
        """List all items with cross_group_visible=1 across 5 stores."""
        items: list[dict[str, Any]] = []

        slang = await _slang_store()
        db = slang._require_db()
        cursor = await db.execute(
            "SELECT term_id, term, meaning, scope, group_id, status, confidence, "
            "cross_group_enabled_by, cross_group_enabled_at, "
            "cross_group_enabled_for_groups, cross_group_enabled_reason "
            "FROM slang_terms WHERE cross_group_visible = 1 "
            "ORDER BY cross_group_enabled_at DESC"
        )
        for row in await cursor.fetchall():
            items.append(_row_to_item(
                "slang", row["term_id"], row["term"], row["meaning"],
                row["scope"], row["group_id"], row["status"], row["confidence"],
                row["cross_group_enabled_by"], row["cross_group_enabled_at"],
                row["cross_group_enabled_for_groups"], row["cross_group_enabled_reason"],
            ))

        style = await _style_store()
        sdb = style._require_db()
        cursor = await sdb.execute(
            "SELECT expression_id, situation, style, scope, group_id, status, confidence, "
            "cross_group_enabled_by, cross_group_enabled_at, "
            "cross_group_enabled_for_groups, cross_group_enabled_reason "
            "FROM style_expressions WHERE cross_group_visible = 1 "
            "ORDER BY cross_group_enabled_at DESC"
        )
        for row in await cursor.fetchall():
            items.append(_row_to_item(
                "style", row["expression_id"], row["situation"], row["style"],
                row["scope"], row["group_id"], row["status"], row["confidence"],
                row["cross_group_enabled_by"], row["cross_group_enabled_at"],
                row["cross_group_enabled_for_groups"], row["cross_group_enabled_reason"],
            ))

        episodic = await _episodic_store()
        edb = episodic._require_db()
        cursor = await edb.execute(
            "SELECT episode_id, situation, reflection, scope, group_id, episode_state, confidence, "
            "cross_group_enabled_by, cross_group_enabled_at, "
            "cross_group_enabled_for_groups, cross_group_enabled_reason "
            "FROM episodes WHERE cross_group_visible = 1 "
            "ORDER BY cross_group_enabled_at DESC"
        )
        for row in await cursor.fetchall():
            items.append(_row_to_item(
                "episode", row["episode_id"], row["situation"], row["reflection"],
                row["scope"], row["group_id"], row["episode_state"], row["confidence"],
                row["cross_group_enabled_by"], row["cross_group_enabled_at"],
                row["cross_group_enabled_for_groups"], row["cross_group_enabled_reason"],
            ))

        ln = await _normalizer_store()
        ldb = ln._require_db()
        cursor = await ldb.execute(
            "SELECT cluster_id, canonical_text, domain, scope, group_id, status, confidence, "
            "cross_group_enabled_by, cross_group_enabled_at, "
            "cross_group_enabled_for_groups, cross_group_enabled_reason "
            "FROM learning_normalizer_clusters WHERE cross_group_visible = 1 "
            "ORDER BY cross_group_enabled_at DESC"
        )
        for row in await cursor.fetchall():
            items.append(_row_to_item(
                "normalizer", row["cluster_id"], row["canonical_text"], row["domain"],
                row["scope"], row["group_id"], row["status"], row["confidence"],
                row["cross_group_enabled_by"], row["cross_group_enabled_at"],
                row["cross_group_enabled_for_groups"], row["cross_group_enabled_reason"],
            ))

        graph = await _graph_store()
        gdb = graph._db
        if gdb is not None:
            cursor = await gdb.execute(
                "SELECT fact_id, subject, predicate, object, scope, scope_id, status, confidence, "
                "cross_group_enabled_by, cross_group_enabled_at, "
                "cross_group_enabled_for_groups, cross_group_enabled_reason "
                "FROM graph_facts WHERE cross_group_visible = 1 "
                "ORDER BY cross_group_enabled_at DESC"
            )
            for row in await cursor.fetchall():
                label = f"{row['subject']} · {row['predicate']}"
                detail = str(row["object"])
                items.append(_row_to_item(
                    "graph_fact", row["fact_id"], label, detail,
                    row["scope"], row["scope_id"], row["status"], row["confidence"],
                    row["cross_group_enabled_by"], row["cross_group_enabled_at"],
                    row["cross_group_enabled_for_groups"], row["cross_group_enabled_reason"],
                ))

            cursor = await gdb.execute(
                "SELECT candidate_id, subject, predicate, object, scope, scope_id, status, confidence, "
                "cross_group_enabled_by, cross_group_enabled_at, "
                "cross_group_enabled_for_groups, cross_group_enabled_reason "
                "FROM extraction_candidates WHERE cross_group_visible = 1 "
                "ORDER BY cross_group_enabled_at DESC"
            )
            for row in await cursor.fetchall():
                label = f"{row['subject']} · {row['predicate']}"
                detail = str(row["object"])
                items.append(_row_to_item(
                    "graph_candidate", row["candidate_id"], label, detail,
                    row["scope"], row["scope_id"], row["status"], row["confidence"],
                    row["cross_group_enabled_by"], row["cross_group_enabled_at"],
                    row["cross_group_enabled_for_groups"], row["cross_group_enabled_reason"],
                ))

        return {"ok": True, "items": items, "total": len(items)}

    @router.post("/enable")
    async def enable_cross_group(request: Request):
        """Enable or disable cross-group visibility for an item."""
        body = await _read_json(request)
        store_name = str(body.get("store", "")).strip()
        item_id = str(body.get("item_id", "")).strip()
        visible = bool(body.get("visible", True))
        actor = str(body.get("actor", "admin")).strip() or "admin"
        reason = str(body.get("reason", "")).strip()
        raw_groups = body.get("enabled_for_groups", []) or []
        if isinstance(raw_groups, str):
            raw_groups = [raw_groups]
        enabled_for_groups = [
            str(g).strip()
            for g in raw_groups
            if isinstance(g, (str, int)) and str(g).strip()
        ]

        if store_name not in _VALID_STORES:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": f"store must be one of {list(_VALID_STORES)}"},
            )
        if not item_id:
            return JSONResponse(status_code=400, content={"ok": False, "error": "item_id is required"})
        if visible and not reason:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "reason is required when enabling cross-group visibility"},
            )

        ok: bool
        if store_name == "slang":
            store = await _slang_store()
            ok = await store.set_cross_group_visibility(
                item_id, visible=visible, actor=actor, reason=reason,
                enabled_for_groups=enabled_for_groups,
            )
        elif store_name == "style":
            sstore = await _style_store()
            ok = await sstore.set_cross_group_visibility(
                item_id, visible=visible, actor=actor, reason=reason,
                enabled_for_groups=enabled_for_groups,
            )
        elif store_name == "episode":
            estore = await _episodic_store()
            ok = await estore.set_cross_group_visibility(
                item_id, visible=visible, actor=actor, reason=reason,
                enabled_for_groups=enabled_for_groups,
            )
        elif store_name == "normalizer":
            nstore = await _normalizer_store()
            ok = await nstore.set_cross_group_visibility(
                item_id, visible=visible, actor=actor, reason=reason,
                enabled_for_groups=enabled_for_groups,
            )
        elif store_name == "graph_fact":
            gstore = await _graph_store()
            ok = await gstore.set_fact_cross_group_visibility(
                item_id, visible=visible, actor=actor, reason=reason,
                enabled_for_groups=enabled_for_groups,
            )
        else:  # graph_candidate
            gstore = await _graph_store()
            ok = await gstore.set_candidate_cross_group_visibility(
                item_id, visible=visible, actor=actor, reason=reason,
                enabled_for_groups=enabled_for_groups,
            )
        if not ok:
            return JSONResponse(status_code=404, content={"ok": False, "error": "item not found"})
        return {
            "ok": True,
            "item_id": item_id,
            "store": store_name,
            "visible": visible,
            "reason": reason,
            "enabled_for_groups": enabled_for_groups,
        }

    @router.get("/timeline")
    async def cross_group_timeline():
        """Timeline of cross-group enable/disable operations.

        Covers slang / style / episode / normalizer (all four have revision
        tables). KG (graph_facts + extraction_candidates) is intentionally
        omitted — no revision table yet. Phase A.5 graph schema will add one
        and timeline will be extended then.
        """
        entries: list[dict[str, Any]] = []
        slang = await _slang_store()
        db = slang._require_db()
        cursor = await db.execute(
            """SELECT r.term_id, r.action, r.actor, r.reason, r.created_at, t.term AS label
               FROM slang_term_revisions r
               LEFT JOIN slang_terms t ON t.term_id = r.term_id
               WHERE r.action IN ('cross_group_enable', 'cross_group_disable')
               ORDER BY r.created_at DESC LIMIT 50"""
        )
        for row in await cursor.fetchall():
            entries.append({
                "store": "slang",
                "item_id": row["term_id"],
                "action": row["action"],
                "actor": row["actor"],
                "reason": row["reason"] or "",
                "created_at": row["created_at"],
                "label": row["label"] or row["term_id"],
            })
        style = await _style_store()
        sdb = style._require_db()
        cursor = await sdb.execute(
            """SELECT r.expression_id, r.action, r.actor, r.reason, r.created_at, e.situation AS label
               FROM style_revisions r
               LEFT JOIN style_expressions e ON e.expression_id = r.expression_id
               WHERE r.action IN ('cross_group_enable', 'cross_group_disable')
               ORDER BY r.created_at DESC LIMIT 50"""
        )
        for row in await cursor.fetchall():
            entries.append({
                "store": "style",
                "item_id": row["expression_id"],
                "action": row["action"],
                "actor": row["actor"],
                "reason": row["reason"] or "",
                "created_at": row["created_at"],
                "label": row["label"] or row["expression_id"],
            })
        episodic = await _episodic_store()
        edb = episodic._require_db()
        cursor = await edb.execute(
            """SELECT r.episode_id, r.action, r.actor, r.reason, r.created_at, e.situation AS label
               FROM episode_revisions r
               LEFT JOIN episodes e ON e.episode_id = r.episode_id
               WHERE r.action IN ('cross_group_enable', 'cross_group_disable')
               ORDER BY r.created_at DESC LIMIT 50"""
        )
        for row in await cursor.fetchall():
            entries.append({
                "store": "episode",
                "item_id": row["episode_id"],
                "action": row["action"],
                "actor": row["actor"],
                "reason": row["reason"] or "",
                "created_at": row["created_at"],
                "label": row["label"] or row["episode_id"],
            })
        ln = await _normalizer_store()
        ldb = ln._require_db()
        cursor = await ldb.execute(
            """SELECT r.cluster_id, r.action, r.actor, r.reason, r.created_at, c.canonical_text AS label
               FROM learning_normalizer_revisions r
               LEFT JOIN learning_normalizer_clusters c ON c.cluster_id = r.cluster_id
               WHERE r.action IN ('cross_group_enable', 'cross_group_disable')
               ORDER BY r.created_at DESC LIMIT 50"""
        )
        for row in await cursor.fetchall():
            entries.append({
                "store": "normalizer",
                "item_id": row["cluster_id"],
                "action": row["action"],
                "actor": row["actor"],
                "reason": row["reason"] or "",
                "created_at": row["created_at"],
                "label": row["label"] or row["cluster_id"],
            })
        entries.sort(key=lambda e: e["created_at"], reverse=True)
        return {"ok": True, "entries": entries[:50]}

    @router.post("/simulate")
    async def simulate_visibility(request: Request):
        """Simulate which cross-group items a given group can see."""
        body = await _read_json(request)
        group_id = str(body.get("group_id", "")).strip()
        if not group_id:
            return JSONResponse(status_code=400, content={"ok": False, "error": "group_id is required"})
        slang = await _slang_store()
        terms = await slang.get_injectable_terms(group_id=group_id, max_terms=50, min_confidence=0.0)
        cross_group_terms = [
            {"term_id": t.term_id, "term": t.term, "group_id": t.group_id}
            for t in terms if t.group_id != group_id and t.scope == "group"
        ]
        return {"ok": True, "group_id": group_id, "cross_group_terms": cross_group_terms}

    return router
