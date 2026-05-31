from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
from loguru import logger

if TYPE_CHECKING:
    from services.media.character_registry_db import CharacterRegistryDB
    from services.media.recognition_cache import RecognitionCache

_L = logger.bind(channel="debug")


@dataclass(frozen=True)
class CharacterRecognition:
    matched: bool
    character_id: str | None = None
    character_name: str | None = None
    relation: str | None = None
    difference: float | None = None
    threshold: float | None = None
    cache_hit: bool = False
    registry_version: str | None = None
    api_version: str | None = None
    source: str = "ccip-sidecar"


@dataclass(frozen=True)
class _CharacterMetadata:
    name: str
    relation: str


class CharacterRecognizer:
    def __init__(
        self,
        *,
        base_url: str,
        packs_dir: str | Path,
        timeout_seconds: float = 5.0,
        registry_db: CharacterRegistryDB | None = None,
        recognition_cache: RecognitionCache | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._packs_dir = Path(packs_dir)
        self._timeout_seconds = max(0.5, float(timeout_seconds))
        self._signature: tuple[tuple[str, int, int], ...] | None = None
        self._catalog: dict[str, _CharacterMetadata] = {}
        # Optional DB-backed metadata + persistent cache. When present, relation/
        # name come from the registry DB (per-bot, admin-editable) rather than the
        # charpack manifest, and identify() short-circuits on a persistent hit.
        self._registry_db = registry_db
        self._recognition_cache = recognition_cache

    def _catalog_signature(self) -> tuple[tuple[str, int, int], ...]:
        manifests: list[tuple[str, int, int]] = []
        if not self._packs_dir.exists():
            return ()
        for manifest in sorted(self._packs_dir.glob("*.charpack/manifest.json")):
            stat = manifest.stat()
            manifests.append((str(manifest), stat.st_mtime_ns, stat.st_size))
        return tuple(manifests)

    def _reload_catalog_if_needed(self) -> None:
        signature = self._catalog_signature()
        if signature == self._signature:
            return

        catalog: dict[str, _CharacterMetadata] = {}
        for manifest_path, _, _ in signature:
            try:
                payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            characters = payload.get("characters") or []
            if not isinstance(characters, list):
                continue
            for item in characters:
                if not isinstance(item, dict):
                    continue
                character_id = str(item.get("character_id") or "").strip()
                if not character_id:
                    continue
                name = str(item.get("name") or character_id).strip() or character_id
                relation = str(item.get("relation") or "known").strip() or "known"
                catalog[character_id] = _CharacterMetadata(name=name, relation=relation)

        self._catalog = catalog
        self._signature = signature

    async def _request_identify(
        self,
        image_data: bytes,
        *,
        media_type: str = "image/jpeg",
    ) -> dict[str, Any] | None:
        form = aiohttp.FormData()
        form.add_field(
            "image",
            image_data,
            filename="image",
            content_type=media_type,
        )
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(f"{self._base_url}/identify", data=form) as response,
            ):
                if response.status >= 400:
                    body = await response.text()
                    _L.warning(
                        "character recognition failed | status={} body={}",
                        response.status,
                        body[:200],
                    )
                    return None
                payload = await response.json()
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as exc:
            _L.warning("character recognition request failed: {}", exc)
            return None
        return payload if isinstance(payload, dict) else None

    def _metadata_for(self, character_id: str | None) -> _CharacterMetadata | None:
        if not character_id:
            return None
        self._reload_catalog_if_needed()
        return self._catalog.get(character_id)

    async def _metadata_from_db(self, character_id: str | None) -> _CharacterMetadata | None:
        if not character_id or self._registry_db is None:
            return None
        row = await self._registry_db.get(character_id)
        if row is None:
            return None
        return _CharacterMetadata(
            name=str(row.get("name") or character_id),
            relation=str(row.get("relation") or "known"),
        )

    async def identify(
        self,
        image_data: bytes,
        *,
        media_type: str = "image/jpeg",
    ) -> CharacterRecognition | None:
        # L2 persistent cache: full SHA-256, survives restarts, skips sidecar.
        full_hash = hashlib.sha256(image_data).hexdigest()
        if self._recognition_cache is not None:
            cached = await self._recognition_cache.get(full_hash)
            if cached is not None:
                cid = cached.get("character_id")
                return CharacterRecognition(
                    matched=bool(cid),
                    character_id=str(cid) if cid else None,
                    character_name=(str(cached["character_name"]) if cached.get("character_name") else None),
                    relation=(str(cached["relation"]) if cached.get("relation") else None),
                    cache_hit=True,
                    source=str(cached.get("source") or "recognition-cache"),
                )

        payload = await self._request_identify(image_data, media_type=media_type)
        if payload is None:
            return None

        matched = bool(payload.get("matched"))
        character_id = str(payload.get("character_id") or "").strip() or None
        # Prefer registry DB (per-bot relation), fall back to charpack manifest.
        metadata = await self._metadata_from_db(character_id) or self._metadata_for(character_id)
        remote_name = str(payload.get("character_name") or "").strip() or None
        result = CharacterRecognition(
            matched=matched,
            character_id=character_id,
            character_name=(metadata.name if metadata else remote_name),
            relation=(metadata.relation if metadata else None),
            difference=float(payload["difference"]) if payload.get("difference") is not None else None,
            threshold=float(payload["threshold"]) if payload.get("threshold") is not None else None,
            cache_hit=bool(payload.get("cache_hit")),
            registry_version=str(payload.get("registry_version") or "").strip() or None,
            api_version=str(payload.get("api_version") or "").strip() or None,
            source=str(payload.get("source") or "ccip-sidecar"),
        )

        # Persist to L2 (store misses too — a known-not-a-character image stays cheap).
        if self._recognition_cache is not None:
            await self._recognition_cache.put(
                full_hash,
                character_id=result.character_id,
                character_name=result.character_name,
                relation=result.relation,
                source=result.source,
                confidence=(1.0 / (1.0 + result.difference)) if result.difference is not None else None,
            )
        return result
