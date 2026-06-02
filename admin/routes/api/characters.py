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
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Body, Form, HTTPException, UploadFile
from fastapi import File as FastFile
from fastapi.responses import FileResponse

from services.media.character_pack_manifest import (
    character_embedding_key,
    effective_character_work,
    iter_manifest_characters,
    manifest_file_pack_name,
    manifest_pack,
    manifest_series,
)
from services.media.character_pack_manifest import (
    character_id as manifest_character_id,
)
from services.media.character_pack_migrator import merge_selected_character_packs

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
_FORM_CONTEXT_LABEL = Form(default="")
_FORM_PACK = Form(...)
_FORM_SERIES = Form(default="")
_FORM_REL_DEFAULT = Form(default="known")
_FORM_CHARACTERS_JSON = Form(...)
_FORM_SERIES_WORK = Form(default="")
_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def _slug(value: str) -> str:
    s = _SLUG_RE.sub("_", (value or "").strip()).strip("_")
    return s or "character"


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _npz_has_member(npz_path: Path, key: str) -> bool:
    try:
        with zipfile.ZipFile(npz_path) as zf:
            return f"{key}.npy" in set(zf.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def _pack_catalog() -> dict[str, dict[str, object]]:
    catalog: dict[str, dict[str, object]] = {}
    entries: list[tuple[str, dict[str, object]]] = []
    multi_pack_ids: set[str] = set()
    for manifest_file in sorted(_PACKS_DIR.glob("*.charpack/manifest.json")):
        manifest = _read_manifest(manifest_file)
        if manifest is None:
            continue
        fallback = manifest_file_pack_name(manifest_file)
        pack = manifest_pack(manifest, fallback=fallback)
        series = manifest_series(manifest, fallback=pack)
        characters = iter_manifest_characters(manifest)
        pack_character_count = len(characters)
        npz_path = manifest_file.parent / "embeddings.npz"
        complete_single = (
            pack_character_count == 1
            and bool(characters)
            and _npz_has_member(npz_path, character_embedding_key(characters[0]))
        )
        for item in characters:
            cid = manifest_character_id(item)
            if not cid:
                continue
            if pack_character_count > 1:
                multi_pack_ids.add(cid)
            entries.append((cid, {
                "pack": pack,
                "pack_dir": manifest_file.parent.name,
                "series": series,
                "work": effective_character_work(manifest, item),
                "pack_character_count": pack_character_count,
                "mergeable": complete_single,
            }))
    for cid, entry in entries:
        pack_count = entry.get("pack_character_count")
        is_multi = isinstance(pack_count, int) and pack_count > 1
        entry["mergeable"] = bool(entry.get("mergeable")) and cid not in multi_pack_ids
        if cid in catalog and not is_multi and cid in multi_pack_ids:
            continue
        catalog[cid] = entry
    return catalog


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
        for manifest_file in sorted(_PACKS_DIR.glob("*.charpack/manifest.json")):
            manifest = _read_manifest(manifest_file)
            if manifest is None:
                continue
            characters = iter_manifest_characters(manifest)
            if not any(manifest_character_id(item) == cid for item in characters):
                continue
            nested_samples = manifest_file.parent / "samples" / cid
            if nested_samples.is_dir():
                for path in sorted(nested_samples.glob("*.jpg")):
                    return path
            if len(characters) == 1:
                legacy_samples = manifest_file.parent / "samples"
                if legacy_samples.is_dir():
                    for path in sorted(legacy_samples.glob("*.jpg")):
                        return path
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
        catalog = _pack_catalog()
        for c in characters:
            cid = str(c.get("character_id") or "")
            c.update(catalog.get(cid, {}))
            c["has_sample"] = _sample_path(cid) is not None
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
        context_label: str = _FORM_CONTEXT_LABEL,
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
                    data={
                        "character_id": character_id,
                        "name": name,
                        "relation": relation,
                        "work": work,
                        "context_label": context_label,
                    },
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

    @router.post("/characters/build-series")
    async def build_series_pack(
        images: list[UploadFile] = _IMAGES,
        pack_name: str = _FORM_PACK,
        series: str = _FORM_SERIES,
        work: str = _FORM_SERIES_WORK,
        relation_default: str = _FORM_REL_DEFAULT,
        characters_json: str = _FORM_CHARACTERS_JSON,
    ):
        """Enroll a multi-character series pack from grouped raw images."""
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
            async with httpx.AsyncClient(timeout=240.0, trust_env=False) as client:
                resp = await client.post(
                    f"{base}/build-series-pack",
                    data={
                        "pack_name": pack_name,
                        "series": series,
                        "work": work,
                        "relation_default": relation_default,
                        "characters_json": characters_json,
                    },
                    files=files,
                )
        except httpx.HTTPError as exc:
            return {"error": f"sidecar build-series-pack failed: {exc}"}
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
            "pack": built.get("pack"),
            "series": built.get("series"),
            "character_count": built.get("character_count"),
            "embedded": built.get("embedded"),
            "total": built.get("total"),
            "samples": built.get("samples"),
            "characters": built.get("characters") or [],
            "sync": result,
        }

    @router.post("/characters/merge-series")
    async def merge_series_pack(payload: dict[str, Any] = _BODY):
        """Merge selected existing one-character packs into a series pack."""
        registry = _registry()
        if registry is None:
            return {"error": "character recognition not enabled"}
        raw_ids = payload.get("character_ids") or []
        if not isinstance(raw_ids, list):
            return {"error": "character_ids must be a list"}
        try:
            merged = merge_selected_character_packs(
                _PACKS_DIR,
                character_ids=[str(item) for item in raw_ids],
                pack_name=str(payload.get("pack_name") or ""),
                series=str(payload.get("series") or ""),
                work=str(payload.get("work") or ""),
                relation_default=str(payload.get("relation_default") or "known"),
            )
        except (ValueError, FileExistsError, OSError, RuntimeError, zipfile.BadZipFile, KeyError) as exc:
            return {"error": str(exc)}
        result = await registry.scan_and_sync(str(_PACKS_DIR))
        return {"status": "ok", **merged, "sync": result}

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
