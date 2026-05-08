"""Unified context retrieval service."""

from __future__ import annotations

import time
from collections import Counter, deque
from typing import Any

from services.context.packing import pack_context_hits
from services.context.sources import GraphContextSource, KnowledgeContextSource, MemoryContextSource
from services.context.types import ContextHit, ContextPack


class ContextService:
    """Aggregate context hits from memory cards and document knowledge."""

    def __init__(self, sources: list[Any] | None = None) -> None:
        self._sources = list(sources or [])
        self._recent: deque[dict[str, Any]] = deque(maxlen=80)

    @classmethod
    def from_runtime(cls, ctx: Any, *, bus: Any = None) -> ContextService:
        sources: list[Any] = []
        card_store = getattr(ctx, "card_store", None)
        if card_store is not None:
            sources.append(MemoryContextSource(
                card_store,
                group_memory_config=getattr(ctx, "group_memory_config", None),
            ))
        sources.append(KnowledgeContextSource(ctx=ctx, bus=bus or getattr(ctx, "bus", None)))
        sources.append(GraphContextSource(ctx=ctx))
        return cls(sources)

    async def search(
        self,
        query: str,
        *,
        session_id: str = "",
        user_id: str = "",
        group_id: str | None = None,
        top_k: int = 10,
        types: set[str] | None = None,
        type_caps: dict[str, int] | None = None,
    ) -> list[ContextHit]:
        all_hits: list[ContextHit] = []
        errors: list[str] = []
        source_timings: dict[str, float] = {}
        for source in self._sources:
            try:
                t_source = time.perf_counter()
                source_hits = await _search_source(
                    source,
                    query,
                    session_id=session_id,
                    user_id=user_id,
                    group_id=group_id,
                    top_k=top_k,
                )
                source_timings[source.name] = (time.perf_counter() - t_source) * 1000
            except Exception as exc:
                errors.append(f"{source.name}:{type(exc).__name__}")
                continue
            all_hits.extend(hit for hit in source_hits if types is None or hit.type in types)

        deduped = _dedupe_hits(all_hits)
        deduped.sort(key=lambda hit: (-hit.score, hit.type, hit.id))
        result = _apply_type_caps(deduped, type_caps)[:top_k]
        self._record(
            query,
            session_id,
            user_id,
            group_id,
            result,
            error=";".join(errors),
            source_timings=source_timings,
        )
        return result

    async def build_prompt_context(
        self,
        query: str,
        *,
        session_id: str = "",
        user_id: str = "",
        group_id: str | None = None,
        top_k: int = 10,
        max_chars: int = 2400,
        type_caps: dict[str, int] | None = None,
    ) -> ContextPack:
        hits = await self.search(
            query,
            session_id=session_id,
            user_id=user_id,
            group_id=group_id,
            top_k=top_k,
            type_caps=type_caps,
        )
        pack = pack_context_hits(hits, max_chars=max_chars)
        self._record_pack(query, session_id, user_id, group_id, pack)
        return pack

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(self._recent)[-limit:]

    def metrics(self, limit: int = 80) -> dict[str, Any]:
        items = self.recent(limit=limit)
        total = len(items)
        miss_count = sum(1 for item in items if int(item.get("hit_count", 0) or 0) == 0)
        duplicate_total = sum(int(item.get("duplicate_count", 0) or 0) for item in items)
        hit_total = sum(int(item.get("hit_count", 0) or 0) for item in items)
        pack_chars = [
            int(item.get("pack_chars", 0) or 0)
            for item in items
            if int(item.get("pack_chars", 0) or 0) > 0
        ]
        type_counts: Counter[str] = Counter()
        source_counts: Counter[str] = Counter()
        for item in items:
            type_counts.update(dict(item.get("hit_type_counts", {}) or {}))
            source_counts.update(dict(item.get("hit_source_counts", {}) or {}))
        return {
            "total_queries": total,
            "miss_count": miss_count,
            "miss_rate": (miss_count / total) if total else 0.0,
            "hit_count": hit_total,
            "duplicate_hits": duplicate_total,
            "duplicate_rate": (duplicate_total / hit_total) if hit_total else 0.0,
            "avg_pack_chars": (sum(pack_chars) / len(pack_chars)) if pack_chars else 0.0,
            "max_pack_chars": max(pack_chars) if pack_chars else 0,
            "omitted_total": sum(int(item.get("omitted_count", 0) or 0) for item in items),
            "hit_type_counts": dict(type_counts),
            "hit_source_counts": dict(source_counts),
            "recent": items[-20:],
        }

    def _record(
        self,
        query: str,
        session_id: str,
        user_id: str,
        group_id: str | None,
        hits: list[ContextHit],
        *,
        error: str = "",
        source_timings: dict[str, float] | None = None,
    ) -> None:
        self._recent.append({
            "created_at": time.time(),
            "query": query,
            "session_id": session_id,
            "user_id": user_id,
            "group_id": group_id,
            "hit_count": len(hits),
            "hits": [hit.to_dict() for hit in hits[:12]],
            "hit_type_counts": dict(Counter(hit.type for hit in hits)),
            "hit_source_counts": dict(Counter(hit.source for hit in hits)),
            "duplicate_count": _count_duplicate_hits(hits),
            "pack_chars": 0,
            "omitted_count": 0,
            "error": error,
            "source_timings_ms": {
                name: round(elapsed, 2)
                for name, elapsed in (source_timings or {}).items()
            },
        })

    def _record_pack(
        self,
        query: str,
        session_id: str,
        user_id: str,
        group_id: str | None,
        pack: ContextPack,
    ) -> None:
        if not self._recent:
            return
        item = self._recent[-1]
        if (
            item.get("query") != query
            or item.get("session_id") != session_id
            or item.get("user_id") != user_id
            or item.get("group_id") != group_id
        ):
            return
        item["pack_chars"] = len(pack.text)
        item["omitted_count"] = pack.omitted_count
        item["duplicate_count"] = _count_duplicate_hits(pack.hits)


def _dedupe_hits(hits: list[ContextHit]) -> list[ContextHit]:
    seen: set[tuple[str, str]] = set()
    deduped: list[ContextHit] = []
    for hit in hits:
        key = (hit.type, hit.id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(hit)
    return deduped


async def _search_source(
    source: Any,
    query: str,
    *,
    session_id: str,
    user_id: str,
    group_id: str | None,
    top_k: int,
) -> list[ContextHit]:
    try:
        return await source.search(
            query,
            session_id=session_id,
            user_id=user_id,
            group_id=group_id,
            top_k=top_k,
        )
    except TypeError as exc:
        if "session_id" not in str(exc):
            raise
        return await source.search(
            query,
            user_id=user_id,
            group_id=group_id,
            top_k=top_k,
        )


def _apply_type_caps(hits: list[ContextHit], type_caps: dict[str, int] | None) -> list[ContextHit]:
    if not type_caps:
        return hits
    counts: Counter[str] = Counter()
    out: list[ContextHit] = []
    for hit in hits:
        cap = int(type_caps.get(hit.type, 0) or 0)
        if cap > 0 and counts[hit.type] >= cap:
            continue
        counts[hit.type] += 1
        out.append(hit)
    return out


def _count_duplicate_hits(hits: list[ContextHit]) -> int:
    seen_identity: set[tuple[str, str]] = set()
    seen_content: set[tuple[str, str]] = set()
    duplicates = 0
    for hit in hits:
        identity_key = (hit.type, hit.id)
        content_key = (hit.type, hit.content.strip())
        if identity_key in seen_identity or content_key in seen_content:
            duplicates += 1
        seen_identity.add(identity_key)
        seen_content.add(content_key)
    return duplicates
