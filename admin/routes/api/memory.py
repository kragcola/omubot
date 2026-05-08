"""JSON API: memory — cards CRUD, entities, pool config."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request


def create_memory_router(
    *,
    card_store: Any = None,
    group_memory_config: Any = None,
    retrieval_gate: Any = None,
    ctx: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _store():
        return card_store or (getattr(ctx, "card_store", None) if ctx else card_store)

    @router.get("/memory/cards")
    async def list_cards(
        scope: str = Query(""),
        scope_id: str = Query(""),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        store = _store()
        if store is None:
            return {"cards": [], "total": 0}

        try:
            kwargs: dict[str, Any] = {"limit": limit, "offset": offset}
            if scope:
                kwargs["scope"] = scope
            if scope_id:
                kwargs["scope_id"] = scope_id
            cards = await store.list_cards(**kwargs)
            return {
                "cards": [_card_to_dict(c) for c in cards],
                "total": len(cards),
            }
        except Exception as e:
            return {"cards": [], "total": 0, "error": str(e)}

    @router.get("/memory/cards/{card_id}")
    async def get_card(card_id: str):
        store = _store()
        if store is None:
            return {"error": "CardStore not available"}

        card = await store.get_card(card_id)
        if card is None:
            return {"error": "Card not found"}
        return _card_to_dict(card)

    @router.post("/memory/cards")
    async def create_card(request: Request):
        store = _store()
        if store is None:
            return {"ok": False, "error": "CardStore not available"}

        from services.memory.card_store import NewCard

        body = await request.json()
        scope = str(body.get("scope", "user"))
        scope_id = str(body.get("scope_id", "")).strip()
        if scope in {"user", "group"} and not scope_id:
            return {"ok": False, "error": "scope_id is required for user/group cards"}
        if scope == "global" and not scope_id:
            scope_id = "global"
        try:
            card = NewCard(
                category=body.get("category", "fact"),
                scope=scope,
                scope_id=scope_id,
                content=body.get("content", ""),
                confidence=body.get("confidence", 0.7),
                priority=body.get("priority", 5),
                source=body.get("source", "manual"),
                series_id=body.get("series_id"),
            )
            card_id = await store.add_card(card)
            if retrieval_gate is not None:
                retrieval_gate.invalidate_entity(card.scope, card.scope_id)
            return {"ok": True, "card_id": card_id}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @router.patch("/memory/cards/{card_id}")
    async def update_card(card_id: str, request: Request):
        store = _store()
        if store is None:
            return {"ok": False, "error": "CardStore not available"}

        body = await request.json()
        fields = {k: v for k, v in body.items() if k in (
            "content", "category", "confidence", "priority", "scope", "scope_id", "status", "series_id",
        )}
        if not fields:
            return {"ok": False, "error": "No valid fields"}

        ok = await store.update_card(card_id, **fields)
        if ok and retrieval_gate is not None:
            card = await store.get_card(card_id)
            if card:
                retrieval_gate.invalidate_entity(card.scope, card.scope_id)
        return {"ok": ok}

    @router.post("/memory/cards/{card_id}/expire")
    async def expire_card(card_id: str):
        store = _store()
        if store is None:
            return {"ok": False, "error": "CardStore not available"}

        ok = await store.expire_card(card_id)
        if ok and retrieval_gate is not None:
            card = await store.get_card(card_id)
            if card:
                retrieval_gate.invalidate_entity(card.scope, card.scope_id)
        return {"ok": ok}

    @router.get("/memory/entities")
    async def list_entities(scope: str = Query("user")):
        store = _store()
        if store is None:
            return {"entities": []}

        entities = await store.list_entities(scope)
        return {"entities": entities}

    @router.get("/memory/config")
    async def get_config():
        if group_memory_config is None:
            return {"config": {}}
        return {
            "version": getattr(group_memory_config, "version", 1),
            "memory": getattr(getattr(group_memory_config, "memory", None), "mode", "per_group"),
            "pools": getattr(getattr(group_memory_config, "memory", None), "pools", {}),
        }

    @router.post("/memory/config")
    async def save_config(request: Request):
        body = await request.json()
        raw = body.get("config_json", body.get("content", ""))
        if not raw:
            return {"ok": False, "error": "No config content"}

        import json
        from pathlib import Path

        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            formatted = json.dumps(data, ensure_ascii=False, indent=2)
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"JSON parse error: {e}"}

        config_path = Path("config/group-memory.json")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = config_path.with_suffix(".tmp")
        tmp.write_text(formatted, encoding="utf-8")
        tmp.replace(config_path)

        if group_memory_config is not None:
            from kernel.config import GroupMemoryConfig
            try:
                new_cfg = GroupMemoryConfig.load(str(config_path))
                for field_name in ("version", "memory", "nickname", "card_ttl_days"):
                    setattr(group_memory_config, field_name, getattr(new_cfg, field_name))
                if retrieval_gate is not None:
                    retrieval_gate.set_group_memory_config(group_memory_config)
            except Exception:
                pass

        return {"ok": True, "message": "配置已保存，热加载生效"}

    @router.get("/memory/series")
    async def list_all_series():
        store = _store()
        if store is None:
            return {"series": []}
        try:
            series_list = await store.list_all_series()
            result = []
            for s in series_list:
                result.append({
                    "series_id": s.series_id,
                    "series_key": s.series_key,
                    "label": s.label or s.series_key,
                    "source": s.source,
                    "created_at": s.created_at,
                })
            return {"series": result}
        except Exception as e:
            return {"series": [], "error": str(e)}

    @router.get("/memory/series/{scope}/{scope_id}")
    async def list_series(scope: str, scope_id: str):
        store = _store()
        if store is None:
            return {"series": []}
        try:
            series_list = await store.list_entity_series(scope, scope_id)
            result = []
            for s in series_list:
                cards = await store.get_series_cards(s.series_id)
                result.append({
                    "series_id": s.series_id,
                    "series_key": s.series_key,
                    "label": s.label or s.series_key,
                    "source": s.source,
                    "card_count": len(cards),
                    "created_at": s.created_at,
                })
            return {"series": result}
        except Exception as e:
            return {"series": [], "error": str(e)}

    @router.get("/memory/series/{series_id}/cards")
    async def get_series_cards(series_id: str):
        store = _store()
        if store is None:
            return {"cards": []}
        try:
            cards = await store.get_series_cards(series_id)
            return {"cards": [_card_to_dict(c) for c in cards]}
        except Exception as e:
            return {"cards": [], "error": str(e)}

    return router


CATEGORY_LABELS = {
    "preference": "偏好", "boundary": "边界", "relationship": "关系",
    "event": "事件", "promise": "承诺", "fact": "事实", "status": "状态",
}
SCOPE_LABELS = {"user": "私聊", "group": "群聊", "global": "全局"}


def _card_to_dict(card: Any) -> dict:
    return {
        "card_id": card.card_id,
        "category": card.category,
        "category_label": CATEGORY_LABELS.get(card.category, card.category),
        "scope": card.scope,
        "scope_label": SCOPE_LABELS.get(card.scope, card.scope),
        "scope_id": card.scope_id,
        "content": card.content,
        "confidence": card.confidence,
        "status": card.status,
        "priority": card.priority,
        "source": card.source,
        "series_id": card.series_id,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
    }
