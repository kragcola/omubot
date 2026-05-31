from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
from loguru import logger

if TYPE_CHECKING:
    from services.media.animetrace_client import AnimeTraceClient
    from services.media.character_registry_db import CharacterRegistryDB
    from services.media.recognition_cache import RecognitionCache

_L = logger.bind(channel="debug")


@dataclass(frozen=True)
class CharacterRecognition:
    matched: bool
    character_id: str | None = None
    character_name: str | None = None
    relation: str | None = None
    work: str | None = None
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
        animetrace_client: AnimeTraceClient | None = None,
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
        # Optional AnimeTrace online recognition (known anime chars), merged with
        # CCIP. None = CCIP-only.
        self._animetrace_client = animetrace_client

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
        # L2 persistent cache: full SHA-256, survives restarts, skips both engines.
        full_hash = hashlib.sha256(image_data).hexdigest()
        if self._recognition_cache is not None:
            cached = await self._recognition_cache.get(full_hash)
            if cached is not None:
                cid = cached.get("character_id")
                cname = cached.get("character_name")
                return CharacterRecognition(
                    matched=bool(cid or cname),
                    character_id=str(cid) if cid else None,
                    character_name=str(cname) if cname else None,
                    relation=(str(cached["relation"]) if cached.get("relation") else None),
                    work=(str(cached["work"]) if cached.get("work") else None),
                    cache_hit=True,
                    source=str(cached.get("source") or "recognition-cache"),
                )

        result = await self._identify_merged(image_data, media_type=media_type)

        # Persist to L2 only on a confident outcome. Don't cache a None (transient
        # AnimeTrace rate-limit/timeout) as a permanent miss.
        if result is not None and self._recognition_cache is not None:
            await self._recognition_cache.put(
                full_hash,
                character_id=result.character_id,
                character_name=result.character_name,
                relation=result.relation,
                work=result.work,
                source=result.source,
                confidence=(1.0 / (1.0 + result.difference)) if result.difference is not None else None,
            )
        return result

    async def _identify_merged(
        self,
        image_data: bytes,
        *,
        media_type: str,
    ) -> CharacterRecognition | None:
        """Run CCIP (sidecar) and AnimeTrace in parallel, then merge.

        Decision matrix:
          - CCIP hit (original/registered char, has per-bot relation) → prefer
            CCIP. This is the first-class value (the bot itself, known friends).
          - CCIP miss + AnimeTrace hit → use AnimeTrace (known anime char,
            relation=known, carries `work`).
          - both hit → still prefer CCIP's identity (registered), but borrow
            AnimeTrace's `work` for context.
          - both miss → None (caller falls back to VL).
        """
        ccip_task = asyncio.create_task(self._ccip_identify(image_data, media_type=media_type))
        if self._animetrace_client is not None:
            at_task = asyncio.create_task(
                self._animetrace_client.identify(image_data, media_type=media_type)
            )
            ccip_res, at_res = await asyncio.gather(ccip_task, at_task)
        else:
            ccip_res = await ccip_task
            at_res = None

        ccip_hit = ccip_res is not None and ccip_res.matched
        if ccip_hit:
            assert ccip_res is not None
            # Borrow AnimeTrace work for extra context if CCIP has none.
            if ccip_res.work is None and at_res is not None and at_res.work:
                from dataclasses import replace
                return replace(ccip_res, work=at_res.work)
            return ccip_res
        if at_res is not None:
            return CharacterRecognition(
                matched=True,
                character_id=None,
                character_name=at_res.character_name,
                relation="known",
                work=at_res.work or None,
                source="animetrace",
            )
        return ccip_res  # CCIP miss object (matched=False) or None

    async def _ccip_identify(
        self,
        image_data: bytes,
        *,
        media_type: str,
    ) -> CharacterRecognition | None:
        payload = await self._request_identify(image_data, media_type=media_type)
        if payload is None:
            return None
        matched = bool(payload.get("matched"))
        character_id = str(payload.get("character_id") or "").strip() or None
        # Prefer registry DB (per-bot relation), fall back to charpack manifest.
        metadata = await self._metadata_from_db(character_id) or self._metadata_for(character_id)
        remote_name = str(payload.get("character_name") or "").strip() or None
        return CharacterRecognition(
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
