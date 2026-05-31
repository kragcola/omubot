from __future__ import annotations

import hashlib
import io
import json
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from imgutils.metrics.ccip import ccip_difference, ccip_extract_feature
from PIL import Image

API_VERSION = os.getenv("CCIP_API_VERSION", "2026-05-31.v1").strip() or "2026-05-31.v1"

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
