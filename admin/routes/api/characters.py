"""JSON API: character recognition — registry list/edit, pack upload/reload, stats.

Backs the admin SPA 角色识别 page. The CCIP embeddings live in the sidecar;
this layer manages the per-bot semantic registry (relation/name/aliases),
charpack uploads to ./config/character_packs, and recognition-cache stats.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Body, UploadFile
from fastapi import File as FastFile

_PACKS_DIR = Path("config/character_packs")
_UPLOAD = FastFile(...)
_BODY = Body(...)


def create_characters_router(
    *,
    ctx: Any = None,
    sidecar_url: str = "http://host.docker.internal:8620",
) -> APIRouter:
    router = APIRouter()

    def _registry():
        return getattr(ctx, "character_registry_db", None)

    def _cache():
        return getattr(ctx, "recognition_cache", None)

    async def _sidecar_health() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
                resp = await client.get(f"{sidecar_url.rstrip('/')}/health")
                resp.raise_for_status()
                return resp.json()
        except (httpx.HTTPError, ValueError):
            return {"status": "unreachable"}

    @router.get("/characters")
    async def list_characters():
        registry = _registry()
        if registry is None:
            return {"enabled": False, "characters": [], "cache": {}, "sidecar": {}}
        characters = await registry.list_all()
        cache = _cache()
        cache_stats = await cache.stats() if cache is not None else {}
        return {
            "enabled": True,
            "characters": characters,
            "cache": cache_stats,
            "sidecar": await _sidecar_health(),
        }

    @router.patch("/characters/{character_id}")
    async def update_character(character_id: str, payload: dict[str, Any] = _BODY):
        registry = _registry()
        if registry is None:
            return {"error": "character recognition not enabled"}
        ok = await registry.update(
            character_id,
            name=payload.get("name"),
            relation=payload.get("relation"),
            aliases=payload.get("aliases"),
        )
        if not ok:
            return {"error": f"character {character_id!r} not found"}
        return {"status": "ok", "character": await registry.get(character_id)}

    @router.post("/characters/reload")
    async def reload_packs():
        registry = _registry()
        if registry is None:
            return {"error": "character recognition not enabled"}
        result = await registry.scan_and_sync(str(_PACKS_DIR))
        return {"status": "ok", "sync": result}

    @router.post("/characters/upload")
    async def upload_pack(file: UploadFile = _UPLOAD):
        registry = _registry()
        if registry is None:
            return {"error": "character recognition not enabled"}
        data = await file.read()
        if not data:
            return {"error": "empty upload"}
        _PACKS_DIR.mkdir(parents=True, exist_ok=True)
        name = (file.filename or "upload").rsplit("/", 1)[-1]
        # Accept a zipped .charpack (manifest.json + embeddings.npz inside).
        if name.endswith(".zip") or zipfile.is_zipfile(io.BytesIO(data)):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    if any(n.startswith("/") or ".." in n for n in zf.namelist()):
                        return {"error": "unsafe path in zip"}
                    zf.extractall(_PACKS_DIR)
            except zipfile.BadZipFile:
                return {"error": "invalid zip"}
        else:
            return {"error": "upload must be a .zip containing a .charpack directory"}
        result = await registry.scan_and_sync(str(_PACKS_DIR))
        return {"status": "ok", "sync": result}

    return router
