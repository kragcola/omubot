"""JSON API: affection — user ranking, detail, edit."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request


def create_affection_router(
    *,
    affection_store: Any = None,
    affection_engine: Any = None,
    ctx: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _get_store():
        return affection_store or getattr(ctx, "affection_store", None)

    def _get_engine():
        return affection_engine or getattr(ctx, "affection_engine", None)

    @router.get("/affection")
    async def list_affection(
        limit: int = Query(50, ge=1, le=200),
    ):
        store = _get_store()
        if store is None:
            return {"users": []}

        try:
            profiles = store.list_all()
            profiles.sort(key=lambda p: p.score, reverse=True)
            result = []
            for p in profiles[:limit]:
                result.append({
                    "user_id": p.user_id,
                    "score": p.score,
                    "tier": p.tier,
                    "total_interactions": p.total_interactions,
                    "daily_count": p.daily_count,
                    "custom_nickname": p.custom_nickname,
                    "preferred_suffix": p.preferred_suffix,
                    "last_interaction": p.last_interaction,
                })
            return {"users": result}
        except Exception as e:
            return {"users": [], "error": str(e)}

    @router.get("/affection/{user_id}")
    async def get_affection(user_id: str):
        store = _get_store()
        if store is None:
            return {"error": "AffectionStore not available"}

        try:
            profile = store.get(user_id)
            return {
                "user_id": profile.user_id,
                "score": profile.score,
                "tier": profile.tier,
                "total_interactions": profile.total_interactions,
                "daily_count": profile.daily_count,
                "daily_date": profile.daily_date,
                "custom_nickname": profile.custom_nickname,
                "group_nicknames": dict(getattr(profile, "group_nicknames", {})),
                "preferred_suffix": profile.preferred_suffix,
                "default_suffix": profile.default_suffix,
                "first_interaction": profile.first_interaction,
                "last_interaction": profile.last_interaction,
                "mood_bonus_valence": getattr(profile, "mood_bonus_valence", 0.0),
            }
        except Exception as e:
            return {"error": str(e)}

    @router.patch("/affection/{user_id}")
    async def update_affection(user_id: str, request: Request):
        store = _get_store()
        engine = _get_engine()
        if store is None:
            return {"ok": False, "error": "AffectionStore not available"}

        body = await request.json()
        profile = store.get(user_id)

        if "custom_nickname" in body and engine is not None:
            engine.set_nickname(user_id, body["custom_nickname"])
        if "preferred_suffix" in body and engine is not None:
            engine.set_suffix(user_id, body["preferred_suffix"])
        if "score" in body:
            profile.score = max(0.0, min(100.0, float(body["score"])))
            store.save(profile)

        return {"ok": True}

    return router
