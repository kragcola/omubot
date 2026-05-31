"""JSON API: stickers — list, detail, update, delete."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse


def create_stickers_router(
    *,
    sticker_store: Any = None,
    ctx: Any = None,
) -> APIRouter:
    router = APIRouter()

    def _store():
        return sticker_store or getattr(ctx, "sticker_store", None)

    @router.get("/stickers/{sticker_id}/image")
    async def sticker_image(sticker_id: str):
        store = _store()
        if store is None:
            return {"error": "StickerStore not available"}
        path = store.resolve_path(sticker_id)
        if path is None or not path.exists():
            return {"error": "Sticker not found"}
        media_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        ext = path.suffix.lower()
        return FileResponse(str(path), media_type=media_types.get(ext, "application/octet-stream"))

    @router.get("/stickers")
    async def list_stickers():
        store = _store()
        if store is None:
            return {"stickers": []}

        try:
            all_items = store.list_all()
            stickers = []
            for sid, info in all_items.items():
                stickers.append({
                    "id": sid,
                    "description": info.get("description", ""),
                    "usage_hint": info.get("usage_hint", ""),
                    "ocr_text": info.get("ocr_text", ""),
                    "send_count": info.get("send_count", 0),
                    "source": info.get("source", ""),
                })
            stickers.sort(key=lambda s: s.get("send_count", 0), reverse=True)
            return {"stickers": stickers}
        except Exception as e:
            return {"stickers": [], "error": str(e)}

    @router.get("/stickers/{sticker_id}")
    async def get_sticker(sticker_id: str):
        store = _store()
        if store is None:
            return {"error": "StickerStore not available"}

        try:
            info = store.get(sticker_id)
            if info is None:
                return {"error": "Sticker not found"}
            return {
                "id": sticker_id,
                "description": info.get("description", ""),
                "usage_hint": info.get("usage_hint", ""),
                "ocr_text": info.get("ocr_text", ""),
                "send_count": info.get("send_count", 0),
                "source": info.get("source", ""),
            }
        except Exception as e:
            return {"error": str(e)}

    @router.patch("/stickers/{sticker_id}")
    async def update_sticker(sticker_id: str, request: Request):
        store = _store()
        if store is None:
            return {"ok": False, "error": "StickerStore not available"}

        body = await request.json()
        desc = body.get("description") or body.get("desc")
        hint = body.get("usage_hint") or body.get("hint")

        ok = store.update(sticker_id, description=desc, usage_hint=hint)
        return {"ok": ok}

    @router.delete("/stickers/{sticker_id}")
    async def delete_sticker(sticker_id: str):
        store = _store()
        if store is None:
            return {"ok": False, "error": "StickerStore not available"}

        ok = store.remove(sticker_id)
        return {"ok": ok}

    return router
