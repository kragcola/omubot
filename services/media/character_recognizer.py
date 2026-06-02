from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
from loguru import logger

from services.media.character_pack_manifest import (
    character_id,
    effective_character_relation,
    effective_character_work,
    iter_manifest_characters,
)

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
    work: str | None = None


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
        multi_char_enabled: bool = True,
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
        # Multi-character detection: head detection + per-crop CCIP via the sidecar
        # /identify-multi endpoint. When False, falls back to single full-image CCIP.
        self._multi_char_enabled = bool(multi_char_enabled)

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

            for item in iter_manifest_characters(payload):
                cid = character_id(item)
                if not cid:
                    continue
                name = str(item.get("name") or cid).strip() or cid
                relation = effective_character_relation(payload, item)
                work = effective_character_work(payload, item)
                catalog[cid] = _CharacterMetadata(name=name, relation=relation, work=work)

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

    async def _request_identify_multi(
        self,
        image_data: bytes,
        *,
        media_type: str = "image/jpeg",
    ) -> dict[str, Any] | None:
        """POST to the sidecar /identify-multi endpoint."""
        form = aiohttp.FormData()
        form.add_field("image", image_data, filename="image", content_type=media_type)
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds + 3.0)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(f"{self._base_url}/identify-multi", data=form) as response,
            ):
                if response.status >= 400:
                    body = await response.text()
                    _L.warning(
                        "character recognition multi failed | status={} body={}",
                        response.status,
                        body[:200],
                    )
                    return None
                payload = await response.json()
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as exc:
            _L.warning("character recognition multi request failed: {}", exc)
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
    ) -> list[CharacterRecognition]:
        """Identify characters in an image.

        When ``multi_char_enabled`` is True (the default), uses the sidecar
        /identify-multi endpoint for head-detection + per-crop CCIP, yielding
        one ``CharacterRecognition`` per detected character.  Falls back to the
        existing single full-image CCIP + AnimeTrace merge otherwise.

        Returns an empty list when no characters are recognized.  Callers that
        used to check ``result is None`` should check ``not result`` or
        ``len(result) == 0`` instead.
        """
        full_hash = hashlib.sha256(image_data).hexdigest()

        if self._multi_char_enabled:
            # Multi-char path: skip L2 cache (single-sha256 key can't represent
            # multi-character results correctly; no caching is better than wrong
            # caching). See D4 in the migration plan.
            return await self._identify_multi_path(image_data, media_type=media_type)

        # --- legacy single-character path (L2 cache + CCIP + AnimeTrace) ---
        if self._recognition_cache is not None:
            cached = await self._recognition_cache.get(full_hash)
            if cached is not None:
                cid = cached.get("character_id")
                cname = cached.get("character_name")
                return [CharacterRecognition(
                    matched=bool(cid or cname),
                    character_id=str(cid) if cid else None,
                    character_name=str(cname) if cname else None,
                    relation=(str(cached["relation"]) if cached.get("relation") else None),
                    work=(str(cached["work"]) if cached.get("work") else None),
                    cache_hit=True,
                    source=str(cached.get("source") or "recognition-cache"),
                )]

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
        return [result] if result is not None else []

    async def _identify_multi_path(
        self,
        image_data: bytes,
        *,
        media_type: str = "image/jpeg",
    ) -> list[CharacterRecognition]:
        """Call sidecar /identify-multi, parse ``characters`` list, enrich each
        entry with metadata from the registry DB and/or charpack manifest."""
        payload = await self._request_identify_multi(image_data, media_type=media_type)
        if payload is None:
            return []

        results: list[CharacterRecognition] = []
        for item in payload.get("characters") or []:
            if not isinstance(item, dict):
                continue
            character_id = str(item.get("character_id") or "").strip() or None
            matched = bool(item.get("matched"))
            metadata = await self._metadata_from_db(character_id) or self._metadata_for(character_id)
            manifest_meta = self._metadata_for(character_id)
            work = manifest_meta.work if manifest_meta else None
            remote_name = str(item.get("character_name") or "").strip() or None
            results.append(CharacterRecognition(
                matched=matched,
                character_id=character_id,
                character_name=(metadata.name if metadata else remote_name),
                relation=(metadata.relation if metadata else None),
                work=work,
                difference=float(item["difference"]) if item.get("difference") is not None else None,
                threshold=float(payload["threshold"]) if payload.get("threshold") is not None else None,
                cache_hit=False,
                registry_version=str(payload.get("registry_version") or "").strip() or None,
                api_version=str(payload.get("api_version") or "").strip() or None,
                source=str(payload.get("source") or "ccip-sidecar"),
            ))
        return results

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

        Latency: both engines are launched together so AnimeTrace's ~1.8s online
        call overlaps CCIP's ~0.8s local call. But when CCIP returns a hit whose
        work will NOT be borrowed (self/friend, or known-with-manifest-work), the
        AnimeTrace result is irrelevant — we cancel it and return immediately
        rather than blocking the whole message ingest on the slow online call.
        """
        ccip_task = asyncio.create_task(self._ccip_identify(image_data, media_type=media_type))
        at_task: asyncio.Task[Any] | None = None
        if self._animetrace_client is not None:
            at_task = asyncio.create_task(
                self._animetrace_client.identify(image_data, media_type=media_type)
            )

        ccip_res = await ccip_task
        ccip_hit = ccip_res is not None and ccip_res.matched

        # Short-circuit: a CCIP hit only consults AnimeTrace to borrow `work`, and
        # only for relation=known characters lacking a manifest work. Any other
        # hit (self/friend, or known with manifest work) is already final — don't
        # wait on the slow online call.
        borrow_possible = (
            ccip_hit
            and ccip_res is not None
            and ccip_res.relation == "known"
            and ccip_res.work is None
        )
        if ccip_hit and not borrow_possible:
            if at_task is not None:
                at_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await at_task
            assert ccip_res is not None
            return ccip_res

        at_res = await at_task if at_task is not None else None

        if ccip_hit:
            assert ccip_res is not None
            # Borrow AnimeTrace's work for extra context ONLY for relation=known
            # characters that lack a manifest work. For self/friend the identity
            # is definitively ours — AnimeTrace's (often wrong) work would mislabel
            # the series (e.g. tagging 凤笑梦 as がっこうぐらし！), so never borrow.
            if (
                ccip_res.work is None
                and ccip_res.relation == "known"
                and at_res is not None
                and at_res.work
            ):
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
        # `work` (出处/series) is intrinsic to the character, not per-bot, so it
        # always comes from the charpack manifest catalog — never the DB.
        manifest_meta = self._metadata_for(character_id)
        work = manifest_meta.work if manifest_meta else None
        remote_name = str(payload.get("character_name") or "").strip() or None
        return CharacterRecognition(
            matched=matched,
            character_id=character_id,
            character_name=(metadata.name if metadata else remote_name),
            relation=(metadata.relation if metadata else None),
            work=work,
            difference=float(payload["difference"]) if payload.get("difference") is not None else None,
            threshold=float(payload["threshold"]) if payload.get("threshold") is not None else None,
            cache_hit=bool(payload.get("cache_hit")),
            registry_version=str(payload.get("registry_version") or "").strip() or None,
            api_version=str(payload.get("api_version") or "").strip() or None,
            source=str(payload.get("source") or "ccip-sidecar"),
        )
