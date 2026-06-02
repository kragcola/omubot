"""JSON API: character recognition — registry, web-based enrollment, samples.

Backs the admin SPA 角色识别 page. CCIP embeddings live in the sidecar (which
owns numpy + the model); this layer manages the per-bot semantic registry
(relation/name/aliases), enrolls characters from raw reference images by
forwarding them to the sidecar /build-pack endpoint, lands the returned
charpack on the rw config mount, and serves sample thumbnails.
"""
from __future__ import annotations

import base64
import io
import re
import zipfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Body, Form, HTTPException, UploadFile
from fastapi import File as FastFile
from fastapi.responses import FileResponse

_PACKS_DIR = Path("config/character_packs")
_UPLOAD = FastFile(...)
_IMAGES = FastFile(...)
_BODY = Body(...)
# Distinct Form() instances — FastAPI binds by marker identity, so a shared
# Form(...) across two params makes the second silently fail to bind.
_FORM_ID = Form(...)
_FORM_NAME = Form(...)
_FORM_REL = Form(default="known")
_FORM_WORK = Form(default="")
_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def _slug(value: str) -> str:
    s = _SLUG_RE.sub("_", (value or "").strip()).strip("_")
    return s or "character"


def create_characters_router(
    *,
    ctx: Any = None,
    sidecar_url: str = "http://host.docker.internal:8620",
) -> APIRouter:
    router = APIRouter()
    base = sidecar_url.rstrip("/")

    def _registry():
        return getattr(ctx, "character_registry_db", None)

    def _cache():
        return getattr(ctx, "recognition_cache", None)

    async def _sidecar_health() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
                resp = await client.get(f"{base}/health")
                resp.raise_for_status()
                return resp.json()
        except (httpx.HTTPError, ValueError):
            return {"status": "unreachable"}

    def _sample_path(character_id: str) -> Path | None:
        """First sample thumbnail for a character, if its charpack has one."""
        cid = _slug(character_id)
        samples_dir = _PACKS_DIR / f"{cid}.charpack" / "samples"
        if not samples_dir.is_dir():
            return None
        for p in sorted(samples_dir.glob("*.jpg")):
            return p
        return None

    def _land_charpack(zip_b64: str, pack_dir: str) -> None:
        """Extract a sidecar-built charpack zip onto the rw config mount."""
        raw = base64.b64decode(zip_b64)
        _PACKS_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
            if any(n.startswith("/") or ".." in n for n in names):
                raise HTTPException(status_code=400, detail="unsafe path in built pack")
            if not any(n.startswith(f"{pack_dir}/") for n in names):
                raise HTTPException(status_code=500, detail="built pack has unexpected layout")
            zf.extractall(_PACKS_DIR)

    @router.get("/characters")
    async def list_characters():
        registry = _registry()
        if registry is None:
            return {"enabled": False, "characters": [], "cache": {}, "sidecar": {}}
        characters = await registry.list_all()
        for c in characters:
            c["has_sample"] = _sample_path(str(c.get("character_id"))) is not None
        cache = _cache()
        cache_stats = await cache.stats() if cache is not None else {}
        return {
            "enabled": True,
            "characters": characters,
            "cache": cache_stats,
            "sidecar": await _sidecar_health(),
        }

    @router.get("/characters/{character_id}/sample")
    async def character_sample(character_id: str):
        path = _sample_path(character_id)
        if path is None:
            raise HTTPException(status_code=404, detail="no sample image")
        return FileResponse(str(path), media_type="image/jpeg")

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

    @router.post("/characters/build")
    async def build_character(
        images: list[UploadFile] = _IMAGES,
        character_id: str = _FORM_ID,
        name: str = _FORM_NAME,
        relation: str = _FORM_REL,
        work: str = _FORM_WORK,
    ):
        """Enroll a character from raw reference images: forward to sidecar
        /build-pack (it owns numpy + model), land the returned charpack on the
        rw config mount, sync the registry."""
        registry = _registry()
        if registry is None:
            return {"error": "character recognition not enabled"}
        payloads = [(img.filename or "image", await img.read()) for img in images]
        files = [
            ("images", (fname, data, "application/octet-stream"))
            for fname, data in payloads if data
        ]
        if not files:
            return {"error": "no images provided"}
        try:
            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
                resp = await client.post(
                    f"{base}/build-pack",
                    data={"character_id": character_id, "name": name, "relation": relation, "work": work},
                    files=files,
                )
        except httpx.HTTPError as exc:
            return {"error": f"sidecar build-pack failed: {exc}"}
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            return {"error": f"sidecar: {detail}"}
        built = resp.json()
        _land_charpack(built["charpack_zip_b64"], built["pack_dir"])
        result = await registry.scan_and_sync(str(_PACKS_DIR))
        return {
            "status": "ok",
            "character_id": built["character_id"],
            "embedded": built["embedded"],
            "total": built["total"],
            "samples": built["samples"],
            "sync": result,
        }

    @router.post("/characters/upload")
    async def upload_pack(file: UploadFile = _UPLOAD):
        """Advanced: upload a pre-built .charpack zip (manifest + npz)."""
        registry = _registry()
        if registry is None:
            return {"error": "character recognition not enabled"}
        data = await file.read()
        if not data:
            return {"error": "empty upload"}
        if not (((file.filename or "").endswith(".zip")) or zipfile.is_zipfile(io.BytesIO(data))):
            return {"error": "upload must be a .zip containing a .charpack directory"}
        _PACKS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if any(n.startswith("/") or ".." in n for n in zf.namelist()):
                    return {"error": "unsafe path in zip"}
                zf.extractall(_PACKS_DIR)
        except zipfile.BadZipFile:
            return {"error": "invalid zip"}
        result = await registry.scan_and_sync(str(_PACKS_DIR))
        return {"status": "ok", "sync": result}

    return router
