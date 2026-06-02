from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import re
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from imgutils.detect import detect_heads
from imgutils.metrics.ccip import ccip_batch_extract_features, ccip_difference, ccip_extract_feature
from PIL import Image

_L = logging.getLogger("ccip-sidecar")

API_VERSION = os.getenv("CCIP_API_VERSION", "2026-06-01.v1").strip() or "2026-06-01.v1"

@dataclass(frozen=True)
class CharacterEntry:
    character_id: str
    character_name: str
    embedding: np.ndarray


class CharacterRegistry:
    def __init__(self, packs_dir: Path) -> None:
        self._packs_dir = packs_dir
        self._signature: tuple[tuple[str, int, int], ...] | None = None
        self._entries: list[CharacterEntry] = []
        self._pack_count = 0
        self._registry_version = "empty"

    @property
    def entries(self) -> list[CharacterEntry]:
        self._reload_if_needed()
        return self._entries

    @property
    def pack_count(self) -> int:
        self._reload_if_needed()
        return self._pack_count

    @property
    def registry_version(self) -> str:
        self._reload_if_needed()
        return self._registry_version

    def _manifest_signature(self) -> tuple[tuple[str, int, int], ...]:
        manifests: list[tuple[str, int, int]] = []
        if not self._packs_dir.exists():
            return ()
        for pack_dir in sorted(self._packs_dir.glob("*.charpack")):
            manifest = pack_dir / "manifest.json"
            npz_path = pack_dir / "embeddings.npz"
            for path in (manifest, npz_path):
                if not path.exists():
                    continue
                stat = path.stat()
                manifests.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(manifests)

    def _reload_if_needed(self) -> None:
        signature = self._manifest_signature()
        if signature == self._signature:
            return

        entries: list[CharacterEntry] = []
        pack_names: list[str] = []
        for manifest_path, _, _ in signature:
            manifest_file = Path(manifest_path)
            pack_dir = manifest_file.parent
            npz_path = pack_dir / "embeddings.npz"
            if not npz_path.exists():
                continue
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                characters = manifest.get("characters") or []
                embeddings = np.load(npz_path)
            except (OSError, json.JSONDecodeError, ValueError):
                continue

            if not isinstance(characters, list):
                continue
            for item in characters:
                if not isinstance(item, dict):
                    continue
                character_id = str(item.get("character_id") or "").strip()
                if not character_id:
                    continue
                embedding_key = str(item.get("embedding_key") or character_id).strip()
                if not embedding_key or embedding_key not in embeddings:
                    continue
                vector = np.asarray(embeddings[embedding_key], dtype=np.float32).reshape(-1)
                if vector.size == 0:
                    continue
                character_name = str(item.get("name") or character_id).strip() or character_id
                entries.append(
                    CharacterEntry(
                        character_id=character_id,
                        character_name=character_name,
                        embedding=vector,
                    )
                )
            pack_names.append(pack_dir.name)

        version_seed = "|".join([*pack_names, str(len(entries))]).encode("utf-8")
        self._entries = entries
        self._pack_count = len(pack_names)
        self._registry_version = hashlib.sha256(version_seed).hexdigest()[:12] if version_seed else "empty"
        self._signature = signature


class RecognitionCache:
    def __init__(self, max_entries: int) -> None:
        self._max_entries = max(1, int(max_entries))
        self._registry_version = "empty"
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def reset(self, registry_version: str) -> None:
        if registry_version != self._registry_version:
            self._items.clear()
            self._registry_version = registry_version

    def get(self, image_sha256: str) -> dict[str, Any] | None:
        item = self._items.get(image_sha256)
        if item is None:
            return None
        self._items.move_to_end(image_sha256)
        return dict(item)

    def put(self, image_sha256: str, payload: dict[str, Any]) -> None:
        self._items[image_sha256] = dict(payload)
        self._items.move_to_end(image_sha256)
        while len(self._items) > self._max_entries:
            self._items.popitem(last=False)

    @property
    def size(self) -> int:
        return len(self._items)


MODEL_NAME = os.getenv("CCIP_MODEL", "ccip-caformer-24-randaug-pruned").strip()
MATCH_THRESHOLD = float(os.getenv("CCIP_MATCH_THRESHOLD", "0.17847511429108218"))
PACKS_DIR = Path(os.getenv("CCIP_PACKS_DIR", "/app/config/character_packs")).resolve()
CACHE_MAX_ENTRIES = int(os.getenv("CCIP_CACHE_MAX_ENTRIES", "1024"))
_MULTI_CHAR_ENABLED = os.getenv("CCIP_MULTI_CHAR_ENABLED", "1").strip() in ("1", "true", "yes")
IMAGE_UPLOAD = File(...)

registry = CharacterRegistry(PACKS_DIR)
cache = RecognitionCache(CACHE_MAX_ENTRIES)
app = FastAPI(title="ccip-sidecar", version=API_VERSION)


def _identify(image_data: bytes) -> dict[str, Any]:
    registry_entries = registry.entries
    registry_version = registry.registry_version
    cache.reset(registry_version)

    image_sha256 = hashlib.sha256(image_data).hexdigest()
    cached = cache.get(image_sha256)
    if cached is not None:
        cached["cache_hit"] = True
        return cached

    if not registry_entries:
        payload = {
            "matched": False,
            "character_id": None,
            "character_name": None,
            "difference": None,
            "threshold": MATCH_THRESHOLD,
            "score": None,
            "cache_hit": False,
            "image_sha256": image_sha256,
            "registry_version": registry_version,
            "character_count": 0,
            "api_version": API_VERSION,
            "source": "ccip-sidecar",
        }
        cache.put(image_sha256, payload)
        return payload

    image = Image.open(io.BytesIO(image_data)).convert("RGB")
    feature = ccip_extract_feature(image, model=MODEL_NAME)
    best_entry: CharacterEntry | None = None
    best_difference: float | None = None
    for entry in registry_entries:
        difference = float(ccip_difference(feature, entry.embedding, model=MODEL_NAME))
        if best_difference is None or difference < best_difference:
            best_entry = entry
            best_difference = difference

    matched = best_entry is not None and best_difference is not None and best_difference <= MATCH_THRESHOLD
    payload = {
        "matched": matched,
        "character_id": best_entry.character_id if best_entry and matched else None,
        "character_name": best_entry.character_name if best_entry and matched else None,
        "difference": best_difference,
        "threshold": MATCH_THRESHOLD,
        "score": (1.0 / (1.0 + best_difference)) if best_difference is not None else None,
        "cache_hit": False,
        "image_sha256": image_sha256,
        "registry_version": registry_version,
        "character_count": len(registry_entries),
        "api_version": API_VERSION,
        "source": "ccip-sidecar",
    }
    cache.put(image_sha256, payload)
    return payload


def _find_nearest(feature: np.ndarray, entries: list[CharacterEntry]) -> tuple[CharacterEntry | None, float | None]:
    """Return the (entry, difference) of the nearest centroid, or (None, None)."""
    best_entry: CharacterEntry | None = None
    best_diff: float | None = None
    for entry in entries:
        diff = float(ccip_difference(feature, entry.embedding, model=MODEL_NAME))
        if best_diff is None or diff < best_diff:
            best_entry = entry
            best_diff = diff
    return best_entry, best_diff


def _identify_multi(image_data: bytes) -> dict[str, Any]:
    """Multi-character CCIP: detect heads → per-crop CCIP.

    When ``CCIP_MULTI_CHAR_ENABLED`` is false (or the image has ≤1 head), falls
    back to the existing single full-image CCIP path.  Otherwise crops each
    detected head, runs batch CCIP extraction, and returns one result per crop.

    The response always includes a ``characters`` list so the caller can handle
    single and multi results uniformly.
    """
    registry_entries = registry.entries
    registry_version = registry.registry_version

    if not _MULTI_CHAR_ENABLED:
        single = _identify(image_data)
        cid = single.get("character_id")
        cname = single.get("character_name")
        return {
            "matched": bool(single.get("matched")),
            "characters": [{
                "character_id": str(cid) if cid else None,
                "character_name": str(cname) if cname else None,
                "difference": single.get("difference"),
                "matched": bool(single.get("matched")),
            }] if cid or cname else [],
            "detection_count": 0,
            "threshold": MATCH_THRESHOLD,
            "registry_version": registry_version,
            "api_version": API_VERSION,
            "source": "ccip-sidecar",
        }

    image = Image.open(io.BytesIO(image_data)).convert("RGB")
    try:
        heads: list[tuple[tuple[int, int, int, int], str, float]] = detect_heads(image)
    except Exception:
        # detection model unavailable → degrade to single full-image path
        _L.warning("detect_heads failed, falling back to single full-image CCIP")
        single = _identify(image_data)
        cid = single.get("character_id")
        cname = single.get("character_name")
        return {
            "matched": bool(single.get("matched")),
            "characters": [{
                "character_id": str(cid) if cid else None,
                "character_name": str(cname) if cname else None,
                "difference": single.get("difference"),
                "matched": bool(single.get("matched")),
            }] if cid or cname else [],
            "detection_count": 0,
            "threshold": MATCH_THRESHOLD,
            "registry_version": registry_version,
            "api_version": API_VERSION,
            "source": "ccip-sidecar",
        }

    if len(heads) <= 1:
        # Single or no character — full-image CCIP is accurate enough.
        single = _identify(image_data)
        cid = single.get("character_id")
        cname = single.get("character_name")
        return {
            "matched": bool(single.get("matched")),
            "characters": [{
                "character_id": str(cid) if cid else None,
                "character_name": str(cname) if cname else None,
                "difference": single.get("difference"),
                "matched": bool(single.get("matched")),
            }] if cid or cname else [],
            "detection_count": len(heads),
            "threshold": MATCH_THRESHOLD,
            "registry_version": registry_version,
            "api_version": API_VERSION,
            "source": "ccip-sidecar",
        }

    # Multi-character: batch CCIP on each detected head crop.
    crops: list[Image.Image] = []
    for (x0, y0, x1, y1), _label, _score in heads:
        crops.append(image.crop((int(x0), int(y0), int(x1), int(y1))))

    try:
        features = ccip_batch_extract_features(crops, model=MODEL_NAME)
    except Exception:
        _L.warning("ccip_batch_extract_features failed, degrading to per-crop inference")
        features = [ccip_extract_feature(c, model=MODEL_NAME) for c in crops]

    characters: list[dict[str, Any]] = []
    for feat, ((x0, y0, x1, y1), _label, score) in zip(features, heads, strict=True):
        best_entry, best_diff = _find_nearest(feat, registry_entries)
        matched = best_entry is not None and best_diff is not None and best_diff <= MATCH_THRESHOLD
        characters.append({
            "character_id": best_entry.character_id if best_entry and matched else None,
            "character_name": best_entry.character_name if best_entry and matched else None,
            "difference": best_diff,
            "matched": matched,
            "detection_score": float(score),
            "bbox": [float(x0), float(y0), float(x1), float(y1)],
        })

    return {
        "matched": any(c["matched"] for c in characters),
        "characters": characters,
        "detection_count": len(heads),
        "threshold": MATCH_THRESHOLD,
        "registry_version": registry_version,
        "api_version": API_VERSION,
        "source": "ccip-sidecar",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    cache.reset(registry.registry_version)
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "threshold": MATCH_THRESHOLD,
        "pack_count": registry.pack_count,
        "character_count": len(registry.entries),
        "registry_version": registry.registry_version,
        "api_version": API_VERSION,
        "cache_entries": cache.size,
    }


@app.post("/identify")
async def identify(image: UploadFile = IMAGE_UPLOAD) -> dict[str, Any]:
    image_data = await image.read()
    if not image_data:
        raise HTTPException(status_code=400, detail="image is empty")
    try:
        return _identify(image_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ccip identify failed: {exc}") from exc


@app.post("/identify-multi")
async def identify_multi(image: UploadFile = IMAGE_UPLOAD) -> dict[str, Any]:
    """Multi-character identification: detect heads → per-crop CCIP.

    Returns a ``characters`` list with one entry per detected head.  Use
    ``CCIP_MULTI_CHAR_ENABLED=0`` to disable detection and fall back to the
    single full-image CCIP result (same output shape, one-element list).
    """
    image_data = await image.read()
    if not image_data:
        raise HTTPException(status_code=400, detail="image is empty")
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _identify_multi, image_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ccip identify-multi failed: {exc}") from exc


def _embed(image_data: bytes) -> dict[str, Any]:
    image = Image.open(io.BytesIO(image_data)).convert("RGB")
    feature = ccip_extract_feature(image, model=MODEL_NAME)
    vector = np.asarray(feature, dtype=np.float32).reshape(-1)
    return {
        "embedding": vector.tolist(),
        "dim": int(vector.size),
        "model": MODEL_NAME,
        "api_version": API_VERSION,
        "source": "ccip-sidecar",
    }


@app.post("/embed")
async def embed(image: UploadFile = IMAGE_UPLOAD) -> dict[str, Any]:
    """Extract a CCIP feature vector for one image. Used by
    tools/build_character_pack.py to produce embeddings.npz — the model lives
    only in the sidecar, so pack-building borrows it over HTTP."""
    image_data = await image.read()
    if not image_data:
        raise HTTPException(status_code=400, detail="image is empty")
    try:
        return _embed(image_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ccip embed failed: {exc}") from exc


_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_VALID_RELATIONS = {"self", "friend", "known"}
_IMAGES = File(...)
# Each Form() must be a distinct instance — FastAPI tracks parameter binding by
# the marker object's identity, so sharing one Form(...) across two params makes
# the second silently fail to bind (it falls back to a default).
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
_SAMPLE_MAX = 256  # px, longest edge of stored sample thumbnails


def _slug(value: str) -> str:
    s = _SLUG_RE.sub("_", value.strip()).strip("_")
    return s or "character"


def _relation(value: str, *, default: str = "known") -> str:
    value = str(value or "").strip()
    return value if value in _VALID_RELATIONS else default


def _aliases(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _matches_prefix(filename: str, prefix: str) -> bool:
    name = Path(filename or "").name
    if name == prefix:
        return True
    return any(name.startswith(f"{prefix}{sep}") for sep in ("_", "-", "."))


def _embed_character_images(
    images: list[bytes],
    *,
    sample_count: int = 3,
) -> tuple[np.ndarray, int, list[bytes]]:
    vectors: list[np.ndarray] = []
    samples: list[bytes] = []
    embedded = 0
    for raw in images:
        try:
            image = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:
            continue
        feature = ccip_extract_feature(image, model=MODEL_NAME)
        vec = np.asarray(feature, dtype=np.float32).reshape(-1)
        if vec.size == 0:
            continue
        vectors.append(vec)
        embedded += 1
        if len(samples) < sample_count:
            thumb = image.copy()
            thumb.thumbnail((_SAMPLE_MAX, _SAMPLE_MAX))
            sbuf = io.BytesIO()
            thumb.save(sbuf, format="JPEG", quality=82)
            samples.append(sbuf.getvalue())
    if not vectors:
        raise ValueError("no image produced a valid embedding")
    return np.mean(np.stack(vectors), axis=0).astype(np.float32), embedded, samples


def _build_pack(
    images: list[bytes],
    *,
    character_id: str,
    name: str,
    relation: str,
    work: str = "",
    context_label: str = "",
    sample_count: int = 3,
) -> dict[str, Any]:
    """Embed images → mean vector → charpack zip bytes (manifest + npz +
    downscaled samples). The sidecar owns numpy/model; the config mount is
    read-only here, so we return the zip for the bot to land on disk."""
    cid = _slug(character_id)
    mean_vec, embedded, samples = _embed_character_images(images, sample_count=sample_count)
    rel = _relation(relation)
    char_entry: dict[str, Any] = {
        "character_id": cid,
        "name": name.strip() or cid,
        "embedding_key": cid,
        "relation": rel,
        "aliases": [],
    }
    if work.strip():
        char_entry["work"] = work.strip()
    if context_label.strip():
        char_entry["context_label"] = context_label.strip()
    manifest = {
        "pack": cid,
        "relation_default": rel,
        "characters": [char_entry],
    }
    if work.strip():
        manifest["work"] = work.strip()
    npz_buf = io.BytesIO()
    np.savez(npz_buf, **{cid: mean_vec})

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        root = f"{cid}.charpack"
        zf.writestr(f"{root}/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr(f"{root}/embeddings.npz", npz_buf.getvalue())
        for idx, sample in enumerate(samples):
            zf.writestr(f"{root}/samples/{idx}.jpg", sample)
    return {
        "charpack_zip_b64": base64.b64encode(zip_buf.getvalue()).decode("ascii"),
        "pack_dir": f"{cid}.charpack",
        "character_id": cid,
        "embedded": embedded,
        "total": len(images),
        "samples": len(samples),
        "dim": int(mean_vec.size),
        "api_version": API_VERSION,
    }


def _build_series_pack(
    image_files: list[tuple[str, bytes]],
    *,
    pack_name: str,
    series: str,
    work: str,
    relation_default: str,
    characters_json: str,
    sample_count: int = 3,
) -> dict[str, Any]:
    pack = _slug(pack_name)
    series_slug = _slug(series) if series.strip() else pack
    rel_default = _relation(relation_default)
    try:
        character_defs = json.loads(characters_json)
    except json.JSONDecodeError as exc:
        raise ValueError("characters_json must be a JSON array") from exc
    if not isinstance(character_defs, list) or not character_defs:
        raise ValueError("characters_json must be a non-empty JSON array")

    vectors: dict[str, np.ndarray] = {}
    characters: list[dict[str, Any]] = []
    per_character: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_images = 0
    total_embedded = 0
    total_samples = 0
    samples_by_character: dict[str, list[bytes]] = {}

    for raw in character_defs:
        if not isinstance(raw, dict):
            raise ValueError("each characters_json item must be an object")
        raw_cid = str(raw.get("character_id") or "").strip()
        if not raw_cid:
            raise ValueError("each character must include character_id")
        cid = _slug(raw_cid)
        if cid in seen:
            raise ValueError(f"duplicate character_id: {cid}")
        seen.add(cid)

        prefix = str(raw.get("file_prefix") or raw_cid).strip() or cid
        matched = [payload for filename, payload in image_files if _matches_prefix(filename, prefix)]
        if not matched:
            raise ValueError(f"no images matched character {cid} with prefix {prefix!r}")

        mean_vec, embedded, samples = _embed_character_images(matched, sample_count=sample_count)
        vectors[cid] = mean_vec
        samples_by_character[cid] = samples
        total_images += len(matched)
        total_embedded += embedded
        total_samples += len(samples)

        entry: dict[str, Any] = {
            "character_id": cid,
            "name": str(raw.get("name") or cid).strip() or cid,
            "embedding_key": cid,
            "aliases": _aliases(raw.get("aliases")),
        }
        raw_relation = str(raw.get("relation") or "").strip()
        if raw_relation:
            rel = _relation(raw_relation, default=rel_default)
            if rel != rel_default:
                entry["relation"] = rel
        raw_work = str(raw.get("work") or "").strip()
        if raw_work and raw_work != work.strip():
            entry["work"] = raw_work
        raw_context_label = str(raw.get("context_label") or "").strip()
        if raw_context_label:
            entry["context_label"] = raw_context_label
        characters.append(entry)
        per_character.append({
            "character_id": cid,
            "embedded": embedded,
            "total": len(matched),
            "samples": len(samples),
        })

    manifest: dict[str, Any] = {
        "pack": pack,
        "series": series_slug,
        "relation_default": rel_default,
        "characters": characters,
    }
    if work.strip():
        manifest["work"] = work.strip()

    npz_buf = io.BytesIO()
    np.savez(npz_buf, **vectors)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        root = f"{pack}.charpack"
        zf.writestr(f"{root}/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr(f"{root}/embeddings.npz", npz_buf.getvalue())
        for cid, samples in samples_by_character.items():
            for idx, sample in enumerate(samples):
                zf.writestr(f"{root}/samples/{cid}/{idx}.jpg", sample)

    return {
        "charpack_zip_b64": base64.b64encode(zip_buf.getvalue()).decode("ascii"),
        "pack_dir": f"{pack}.charpack",
        "pack": pack,
        "series": series_slug,
        "character_count": len(characters),
        "embedded": total_embedded,
        "total": total_images,
        "samples": total_samples,
        "characters": per_character,
        "dim": int(next(iter(vectors.values())).size),
        "api_version": API_VERSION,
    }


@app.post("/build-pack")
async def build_pack(
    images: list[UploadFile] = _IMAGES,
    character_id: str = _FORM_ID,
    name: str = _FORM_NAME,
    relation: str = _FORM_REL,
    work: str = _FORM_WORK,
    context_label: str = _FORM_CONTEXT_LABEL,
) -> dict[str, Any]:
    """Build a charpack from raw reference images. Returns the zip as base64
    for the caller (bot admin route) to land on the rw config mount — the
    sidecar's own config mount is read-only."""
    payloads = [await img.read() for img in images]
    payloads = [p for p in payloads if p]
    if not payloads:
        raise HTTPException(status_code=400, detail="no images provided")
    try:
        return _build_pack(
            payloads,
            character_id=character_id,
            name=name,
            relation=relation,
            work=work,
            context_label=context_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"build-pack failed: {exc}") from exc


@app.post("/build-series-pack")
async def build_series_pack(
    images: list[UploadFile] = _IMAGES,
    pack_name: str = _FORM_PACK,
    series: str = _FORM_SERIES,
    work: str = _FORM_SERIES_WORK,
    relation_default: str = _FORM_REL_DEFAULT,
    characters_json: str = _FORM_CHARACTERS_JSON,
) -> dict[str, Any]:
    """Build a multi-character charpack. Images are grouped by each
    character's ``file_prefix`` (or ``character_id``), then mean-pooled into one
    centroid per character."""
    image_files = [(img.filename or "image", await img.read()) for img in images]
    image_files = [(name, payload) for name, payload in image_files if payload]
    if not image_files:
        raise HTTPException(status_code=400, detail="no images provided")
    try:
        return _build_series_pack(
            image_files,
            pack_name=pack_name,
            series=series,
            work=work,
            relation_default=relation_default,
            characters_json=characters_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"build-series-pack failed: {exc}") from exc
