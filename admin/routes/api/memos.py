"""JSON API: memos — memory card browsing by entity."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

CATEGORY_LABELS = {
    "preference": "偏好", "boundary": "边界", "relationship": "关系",
    "event": "事件", "promise": "承诺", "fact": "事实", "status": "状态",
}
SCOPE_LABELS = {"user": "私聊", "group": "群聊", "global": "全局"}


def create_memos_router(
    *,
    card_store: Any = None,
    ctx: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _store():
        return card_store or getattr(ctx, "card_store", None)

    @router.get("/memos")
    async def list_memos(
        scope: str = Query(""),
        scope_id: str = Query(""),
        kind: str = Query(""),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        store = _store()
        if store is None:
            return {"memos": [], "entities": []}

        try:
            # If scope_id is given, return cards for that entity
            if scope_id:
                scope_val = scope or "user"
                cards = await store.list_cards(
                    scope=scope_val, scope_id=scope_id,
                    limit=limit, offset=offset,
                )
                return {
                    "memos": [_card_to_dict(c) for c in cards],
                    "scope": scope_val,
                    "scope_id": scope_id,
                }

            # If scope is given but no scope_id, return entity list
            if scope:
                entities = await store.list_entities(scope)
                return {"entities": entities, "scope": scope}

            # Default: return all user-scope entities
            entities = await store.list_entities("user")
            group_entities = await store.list_entities("group")
            return {
                "entities": entities,
                "group_entities": group_entities,
            }
        except Exception as e:
            return {"memos": [], "entities": [], "error": str(e)}

    @router.get("/memos/entities")
    async def list_all_entities():
        store = _store()
        if store is None:
            return {"user_entities": [], "group_entities": []}

        try:
            user_entities = await store.list_entities("user")
            group_entities = await store.list_entities("group")
            return {
                "user_entities": user_entities,
                "group_entities": group_entities,
            }
        except Exception as e:
            return {"user_entities": [], "group_entities": [], "error": str(e)}

    @router.get("/memos/{scope}/{scope_id}/series")
    async def list_entity_series(scope: str, scope_id: str):
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
                    "card_count": len(cards),
                    "cards": [_card_to_dict(c) for c in cards],
                })
            return {"series": result}
        except Exception as e:
            return {"series": [], "error": str(e)}

    return router


def _card_to_dict(card: Any) -> dict:
    return {
        "id": card.card_id,
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
