"""Context source adapters."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Protocol, cast, runtime_checkable

from loguru import logger

from services.context.types import ContextHit, ContextProvenance, ContextScoreBreakdown
from services.knowledge_graph.provenance import (
    evidence_id_from_mapping,
    evidence_type_from_mapping,
    is_primary_evidence_mapping,
    is_primary_evidence_type,
)
from services.memory.entity_identity import (
    entity_ref_from_surface,
    make_entity_ref,
    parse_entity_key,
)
from services.memory.retrieval import RetrievalGate
from services.similarity import create_similarity_provider

_L = logger.bind(channel="system")


@runtime_checkable
class ContextRetriever(Protocol):
    """Structural type that every ContextService source must satisfy.

    Existing duck-typed sources (memory / knowledge / graph) already match this
    shape; the Protocol is purely for type-checking and to give RRF fusion a
    stable contract once a second consumer (e.g. PR5 query-mode dispatch)
    appears.
    """

    name: str

    async def search(
        self,
        query: str,
        *,
        session_id: str = "",
        user_id: str = "",
        group_id: str | None = None,
        top_k: int = 8,
    ) -> list[ContextHit]: ...

_MEMORY_REFRESH_INTERVAL = 10
_GRAPH_DIRECT_THRESHOLD = 0.18
_GRAPH_HOP_DECAY = 0.65
_GRAPH_SCOPE_WINDOW_LIMIT = 200
_GRAPH_MAX_SCOPE_WINDOWS = 8
_GRAPH_HUB_DEGREE_THRESHOLD = 8


class MemoryContextSource:
    """Read-only adapter from CardStore to ContextHit."""

    name = "memory"

    def __init__(
        self,
        card_store: Any,
        group_memory_config: Any = None,
        *,
        retrieval_gate: Any = None,
    ) -> None:
        self._store = card_store
        self._group_memory_config = group_memory_config
        self._retrieval = retrieval_gate or RetrievalGate(
            card_store=card_store,
            refresh_interval=_MEMORY_REFRESH_INTERVAL,
            group_memory_config=group_memory_config,
            semantic_enabled=True,
            semantic_backend="ngram",
        )

    async def search(
        self,
        query: str,
        *,
        session_id: str = "",
        user_id: str = "",
        group_id: str | None = None,
        top_k: int = 8,
    ) -> list[ContextHit]:
        if self._store is None:
            return []
        query_text = query.strip()
        result = await self._retrieval.retrieve_cards(
            session_id=session_id,
            user_id=user_id,
            group_id=group_id,
            conversation_text=query_text,
            top_k=top_k,
        )

        hits: list[ContextHit] = []
        for selected in result.hits:
            card = selected.card
            hits.append(ContextHit(
                id=card.card_id,
                type="memory_card",
                content=card.content,
                score=selected.source_score,
                source=card.source,
                title=f"{_scope_label(card.scope)}记忆 / {card.category}",
                scope=card.scope,
                scope_id=card.scope_id,
                status=card.status,
                retriever="card_store",
                provenance=ContextProvenance(
                    owner="memory_cards",
                    source_id=card.card_id,
                    source_message_id=str(card.source_msg_id or ""),
                    captured_at=str(card.captured_at or ""),
                    captured_by=str(card.captured_by or ""),
                    evidence_refs=(
                        (f"message:{card.source_msg_id}",)
                        if card.source_msg_id
                        else ()
                    ),
                    supersedes_id=str(card.supersedes or ""),
                    valid_from=str(card.captured_at or card.created_at or ""),
                    entity_key=_scope_owner_entity_key(card.scope, card.scope_id),
                ),
                score_breakdown=ContextScoreBreakdown(
                    source_score=selected.source_score,
                    relevance=selected.relevance,
                    importance=selected.importance,
                    confidence=selected.confidence,
                    recency=selected.recency,
                ),
                metadata={
                    "category": card.category,
                    "confidence": card.confidence,
                    "priority": card.priority,
                    "supersedes": card.supersedes,
                    "series_id": card.series_id,
                    "updated_at": card.updated_at,
                    "decision": result.decision,
                    "semantic_backend": result.semantic_backend,
                    "scope_card_count": result.total_active,
                    "matched_card_count": result.matched_active,
                    # Compatibility alias: historical consumers read card_count
                    # as the scoped active total, not the matched subset.
                    "card_count": result.total_active,
                },
            ))

        if hits:
            _L.debug(
                "context memory source | decision={} scope={} ids={} hits={} query={!r}",
                result.decision,
                result.scope,
                list(result.scope_ids),
                len(hits),
                _safe_log_query(query_text),
            )
            return hits

        if result.decision != "minimal_hint":
            _L.debug(
                "context memory source | decision=miss scope={} ids={} query={!r}",
                result.scope,
                list(result.scope_ids),
                _safe_log_query(query_text),
            )
            return []

        _L.debug(
            "context memory source | decision=minimal_hint scope={} ids={} total={} query={!r}",
            result.scope,
            list(result.scope_ids),
            result.total_active,
            _safe_log_query(query_text),
        )
        return [ContextHit(
            id=f"memory_hint:{result.scope}:{','.join(result.scope_ids)}",
            type="memory_card",
            content=(
                f"当前{_scope_label(result.scope)}作用域有 {result.total_active} 张记忆卡片。"
                "如本轮需要细节，可使用 lookup_cards 工具按 scope/scope_id 或关键词查询。"
            ),
            score=0.05,
            source="card_store",
            title=f"{_scope_label(result.scope)}记忆 / 提示",
            scope=result.scope,
            scope_id=",".join(result.scope_ids) if result.scope_ids else "global",
            status="active",
            retriever="card_store_hint",
            score_breakdown=ContextScoreBreakdown(source_score=0.05),
            metadata={
                "decision": "minimal_hint",
                "scope_card_count": result.total_active,
                "matched_card_count": result.matched_active,
                "card_count": result.total_active,
                "semantic_backend": result.semantic_backend,
            },
        )]

class KnowledgeContextSource:
    """Read-only adapter from KnowledgeService to ContextHit."""

    name = "knowledge"

    def __init__(self, knowledge_base: Any = None, *, ctx: Any = None, bus: Any = None) -> None:
        self._knowledge_base = knowledge_base
        self._ctx = ctx
        self._bus = bus

    async def search(
        self,
        query: str,
        *,
        session_id: str = "",
        user_id: str = "",
        group_id: str | None = None,
        top_k: int = 8,
    ) -> list[ContextHit]:
        del session_id, user_id, group_id
        kb = self._resolve_knowledge_base()
        if kb is None or not query.strip():
            return []
        if not getattr(kb, "loaded", False) and hasattr(kb, "reload"):
            kb.reload()
        if not hasattr(kb, "search_hits"):
            return []

        hits: list[ContextHit] = []
        for hit in kb.search_hits(query, top_k=top_k):
            hits.append(ContextHit(
                id=hit.chunk_id,
                type="doc_chunk",
                content=hit.content,
                score=float(hit.score),
                source=hit.source,
                title=hit.title,
                scope="global",
                scope_id="global",
                status="active",
                retriever="knowledge_bm25_ngram",
                provenance=ContextProvenance(
                    owner="knowledge_chunks",
                    source_id=hit.chunk_id,
                    captured_by=str(hit.source or "knowledge_base"),
                    evidence_refs=(f"document:{hit.source}",) if hit.source else (),
                    entity_key=f"doc_chunk:{hit.chunk_id}",
                ),
                score_breakdown=ContextScoreBreakdown(
                    source_score=float(hit.score),
                    relevance=float(hit.score),
                ),
                metadata=dict(hit.metadata),
            ))
        return hits

    def _resolve_knowledge_base(self) -> Any:
        live = getattr(self._ctx, "knowledge_base", None) if self._ctx is not None else None
        if live is not None:
            return live
        if self._knowledge_base is not None:
            return self._knowledge_base
        plugin = (
            self._bus.get_plugin("knowledge")
            if self._bus is not None and hasattr(self._bus, "get_plugin")
            else None
        )
        if plugin is not None:
            return (
                getattr(plugin, "knowledge_base", None)
                or getattr(plugin, "_kb", None)
            )
        return None


class GraphContextSource:
    """Read-only adapter from KnowledgeGraphService active facts."""

    name = "graph"

    def __init__(
        self,
        knowledge_graph: Any = None,
        *,
        ctx: Any = None,
        group_memory_config: Any = None,
        max_hops: int = 2,
    ) -> None:
        self._knowledge_graph = knowledge_graph
        self._ctx = ctx
        self._group_memory_config = group_memory_config
        self._max_hops = max(0, min(2, int(max_hops)))
        self._similarity = create_similarity_provider("ngram")

    async def search(
        self,
        query: str,
        *,
        session_id: str = "",
        user_id: str = "",
        group_id: str | None = None,
        top_k: int = 8,
    ) -> list[ContextHit]:
        del session_id
        graph = self._resolve_graph()
        if graph is None or not query.strip():
            return []

        allowed = self._allowed_scopes(user_id=user_id, group_id=group_id)
        primary_scope: tuple[str, str] | None = None
        if group_id:
            primary_scope = ("group", str(group_id))
        elif user_id:
            primary_scope = ("user", str(user_id))
        relationships = await self._load_relationship_window(
            graph,
            allowed,
            primary_scope=primary_scope,
        )
        scoped_items = [
            item
            for item in relationships
            if (
                str(item.get("scope") or "global"),
                str(item.get("scope_id") or "global"),
            ) in allowed
        ]
        selected = self._select_with_bounded_hops(
            scoped_items,
            query=query,
            top_k=top_k,
        )
        hits: list[ContextHit] = []
        for item, relevance, hop in selected:
            scope = str(item.get("scope") or "global")
            scope_id = str(item.get("scope_id") or "global")
            subject_entity_key = _graph_entity_key(item, "subject")
            object_entity_key = _graph_entity_key(item, "object")
            content = f"{item.get('subject', '')} --{item.get('predicate', '')}-> {item.get('object', '')}"
            confidence = float(item.get("confidence") or 0.0)
            source_score = round(
                relevance + confidence - (0.25 * hop),
                6,
            )
            fact_id = str(item.get("fact_id") or content)
            metadata = dict(item)
            metadata.update({
                "graph_hop": hop,
                "direct_match": hop == 0,
                "subject_entity_key": subject_entity_key,
                "object_entity_key": object_entity_key,
                "graph_provenance_kind": _graph_provenance_kind(item),
            })
            hits.append(ContextHit(
                id=fact_id,
                type="graph_fact",
                content=content,
                score=source_score,
                source=str(item.get("source") or "knowledge_graph"),
                title="知识图谱事实",
                scope=scope,
                scope_id=scope_id,
                status=str(item.get("status") or "active"),
                retriever="graph_ngram",
                provenance=ContextProvenance(
                    owner="graph_facts",
                    source_id=fact_id,
                    captured_at=str(item.get("created_at") or ""),
                    captured_by=str(item.get("source") or "knowledge_graph"),
                    evidence_refs=_graph_evidence_refs(item),
                    supersedes_id=str(item.get("supersedes") or ""),
                    valid_from=str(item.get("created_at") or ""),
                    entity_key=subject_entity_key,
                ),
                score_breakdown=ContextScoreBreakdown(
                    source_score=source_score,
                    relevance=relevance,
                    confidence=confidence,
                ),
                metadata=metadata,
            ))

        hits.sort(key=lambda hit: (
            int(hit.metadata.get("graph_hop", 0)),
            -hit.score,
            hit.id,
        ))
        return hits[:top_k]

    async def _load_relationship_window(
        self,
        graph: Any,
        allowed: set[tuple[str, str]],
        *,
        primary_scope: tuple[str, str] | None,
    ) -> list[dict[str, Any]]:
        scoped_loader = getattr(graph, "list_relationships_for_scopes", None)
        if callable(scoped_loader):
            preferred_scopes = [
                scope
                for scope in (primary_scope, ("global", "global"))
                if scope is not None and scope in allowed
            ]
            remaining_scopes = sorted(allowed - set(preferred_scopes))
            ordered_scopes = (
                preferred_scopes + remaining_scopes
            )[:_GRAPH_MAX_SCOPE_WINDOWS]
            return await cast(
                Awaitable[list[dict[str, Any]]],
                scoped_loader(
                    allowed_scopes=ordered_scopes,
                    limit_per_scope=_GRAPH_SCOPE_WINDOW_LIMIT,
                ),
            )
        # Compatibility for structural fakes and older graph providers.
        return await graph.list_relationships(limit=_GRAPH_SCOPE_WINDOW_LIMIT)

    def _select_with_bounded_hops(
        self,
        items: list[dict[str, Any]],
        *,
        query: str,
        top_k: int,
    ) -> list[tuple[dict[str, Any], float, int]]:
        scored = [
            (
                item,
                _graph_lexical_score(self._similarity, query, item),
            )
            for item in items
        ]
        raw_lexical_by_identity = {
            id(item): score
            for item, score in scored
        }
        direct = [
            (item, score, 0)
            for item, score in scored
            if score >= _GRAPH_DIRECT_THRESHOLD
        ]
        direct.sort(key=lambda value: (
            -value[1],
            -float(value[0].get("confidence") or 0.0),
            str(value[0].get("fact_id") or ""),
        ))
        if self._max_hops <= 0 or len(direct) < 2:
            return direct[:top_k]

        selected = list(direct)
        selected_ids = {
            str(item.get("fact_id") or id(item))
            for item, _score, _hop in selected
        }
        frontier_entities: set[str] = set()
        for item, _score, _hop in direct:
            frontier_entities.update(_graph_entity_keys(item))
        visited_entities = set(frontier_entities)
        entity_degrees = _graph_entity_degrees(items)

        expansion_cap = max(top_k * 2, 8)
        for hop in range(1, self._max_hops + 1):
            candidates: list[tuple[dict[str, Any], float, int]] = []
            for item, lexical_score in scored:
                fact_id = str(item.get("fact_id") or id(item))
                if fact_id in selected_ids:
                    continue
                if not (_graph_entity_keys(item) & frontier_entities):
                    continue
                relevance = max(
                    lexical_score,
                    _GRAPH_DIRECT_THRESHOLD * (_GRAPH_HOP_DECAY ** hop),
                )
                candidates.append((item, relevance, hop))
            candidates.sort(key=lambda value: _graph_expansion_rank(
                value,
                raw_lexical_score=raw_lexical_by_identity[id(value[0])],
                frontier_entities=frontier_entities,
                visited_entities=visited_entities,
                entity_degrees=entity_degrees,
            ))
            if not candidates:
                break

            remaining = expansion_cap - len(selected)
            if remaining <= 0:
                break
            accepted: list[tuple[dict[str, Any], float, int]] = []
            hub_fanout: dict[str, int] = {}
            hub_fanout_cap = max(3, top_k // 2)
            for candidate in candidates:
                item_keys = _graph_entity_keys(candidate[0])
                shared_hubs = {
                    key
                    for key in item_keys & frontier_entities
                    if entity_degrees.get(key, 0) >= _GRAPH_HUB_DEGREE_THRESHOLD
                }
                if any(
                    hub_fanout.get(key, 0) >= hub_fanout_cap
                    for key in shared_hubs
                ):
                    continue
                accepted.append(candidate)
                for key in shared_hubs:
                    hub_fanout[key] = hub_fanout.get(key, 0) + 1
                if len(accepted) >= remaining:
                    break
            next_entities: set[str] = set()
            for item, relevance, item_hop in accepted:
                fact_id = str(item.get("fact_id") or id(item))
                selected_ids.add(fact_id)
                selected.append((item, relevance, item_hop))
                next_entities.update(_graph_entity_keys(item) - visited_entities)
            visited_entities.update(next_entities)
            frontier_entities = next_entities

        return selected[:top_k]

    def _resolve_graph(self) -> Any:
        live = getattr(self._ctx, "knowledge_graph", None) if self._ctx is not None else None
        return live if live is not None else self._knowledge_graph

    def _allowed_scopes(self, *, user_id: str = "", group_id: str | None = None) -> set[tuple[str, str]]:
        allowed = {("global", "global")}
        if group_id:
            config = self._group_memory_config
            if config is None and self._ctx is not None:
                config = getattr(self._ctx, "group_memory_config", None)
            group_ids = (
                config.resolve_group_pools(str(group_id))
                if config is not None
                else [str(group_id)]
            )
            allowed.update(("group", scope_id) for scope_id in group_ids)
            return allowed
        if user_id:
            allowed.add(("user", str(user_id)))
        return allowed


def _scope_label(scope: str) -> str:
    if scope == "group":
        return "群"
    if scope == "user":
        return "用户"
    return "全局"


def _scope_owner_entity_key(scope: str, scope_id: str) -> str:
    clean_scope = str(scope or "global").strip().lower() or "global"
    clean_scope_id = str(scope_id or "global").strip() or "global"
    if clean_scope == "user" and clean_scope_id.isdigit():
        return make_entity_ref(
            kind="user",
            scope=clean_scope,
            scope_id=clean_scope_id,
            display=clean_scope_id,
            platform_id=clean_scope_id,
        ).entity_key
    if clean_scope == "group" and clean_scope_id.isdigit():
        return make_entity_ref(
            kind="group",
            scope=clean_scope,
            scope_id=clean_scope_id,
            display=clean_scope_id,
            platform_id=clean_scope_id,
        ).entity_key
    return entity_ref_from_surface(
        subject=clean_scope_id,
        scope=clean_scope,
        scope_id=clean_scope_id,
    ).entity_key


def _graph_evidence_refs(item: dict[str, Any]) -> tuple[str, ...]:
    """Primary typed refs only for ContextProvenance.evidence_refs.

    Uses ``is_primary_evidence_type`` from provenance.py (deny-set derived).
    ``graph_fact:*`` may remain in item metadata/audit lists but must not
    enter evidence_refs (peg treats them as empty support).
    """
    refs: list[str] = []
    raw = item.get("evidence")
    if not isinstance(raw, list):
        return ()
    for evidence in raw:
        if not isinstance(evidence, dict):
            continue
        if not is_primary_evidence_mapping(evidence):
            continue
        evidence_type = evidence_type_from_mapping(evidence)
        evidence_id = evidence_id_from_mapping(evidence)
        ref = f"{evidence_type}:{evidence_id}"
        if ref not in refs:
            refs.append(ref)
    return tuple(refs)


def _graph_provenance_kind(item: dict[str, Any]) -> str:
    """Closed metadata: primary | derived_only | none.

    Classification uses the same primary predicate as supersede-copy.
    """
    raw = item.get("evidence")
    if not isinstance(raw, list) or not raw:
        return "none"
    has_primary = False
    has_any = False
    for evidence in raw:
        if not isinstance(evidence, dict):
            continue
        eid = evidence_id_from_mapping(evidence)
        if not eid:
            continue
        has_any = True
        if is_primary_evidence_type(evidence_type_from_mapping(evidence)):
            has_primary = True
            break
    if has_primary:
        return "primary"
    if has_any:
        return "derived_only"
    return "none"


def _graph_entity_keys(item: dict[str, Any]) -> set[str]:
    return {
        key
        for key in (
            _graph_entity_key(item, "subject"),
            _graph_entity_key(item, "object"),
        )
        if key
    }


def _graph_entity_degrees(items: list[dict[str, Any]]) -> dict[str, int]:
    degrees: dict[str, int] = {}
    for item in items:
        for key in _graph_entity_keys(item):
            degrees[key] = degrees.get(key, 0) + 1
    return degrees


def _graph_expansion_rank(
    value: tuple[dict[str, Any], float, int],
    *,
    raw_lexical_score: float,
    frontier_entities: set[str],
    visited_entities: set[str],
    entity_degrees: dict[str, int],
) -> tuple[float, int, float, str]:
    item, _relevance, _hop = value
    item_keys = _graph_entity_keys(item)
    novel_keys = item_keys - visited_entities
    continuation_degree = max(
        (entity_degrees.get(key, 0) for key in novel_keys),
        default=0,
    )
    # Lexical evidence remains primary. For equal lexical scores, prefer an
    # edge that introduces a node with another bounded continuation over a
    # high-confidence leaf hanging off the same frontier hub.
    shared_frontier = bool(item_keys & frontier_entities)
    return (
        -raw_lexical_score,
        -continuation_degree if shared_frontier else 0,
        -float(item.get("confidence") or 0.0),
        str(item.get("fact_id") or ""),
    )


def _graph_entity_key(item: dict[str, Any], role: str) -> str:
    key_name = f"{role}_entity_key"
    raw_keys = [str(item.get(key_name) or "").strip()]
    nested = item.get("metadata")
    if isinstance(nested, dict):
        raw_keys.append(str(nested.get(key_name) or "").strip())
    for raw_key in raw_keys:
        parsed = parse_entity_key(raw_key)
        if parsed is not None:
            return parsed.entity_key
    surface = str(item.get(role) or "").strip()
    if not surface:
        return ""
    return entity_ref_from_surface(
        subject=surface,
        scope=str(item.get("scope") or "global"),
        scope_id=str(item.get("scope_id") or "global"),
    ).entity_key


def _graph_lexical_score(
    similarity: Any,
    query: str,
    item: dict[str, Any],
) -> float:
    subject = str(item.get("subject") or "")
    predicate = str(item.get("predicate") or "")
    object_ = str(item.get("object") or "")
    return max(
        similarity.similarity(query, value)
        for value in (
            f"{subject} {predicate} {object_}",
            subject,
            predicate,
            object_,
        )
        if value
    )


def _safe_log_query(query: str, limit: int = 80) -> str:
    text = " ".join((query or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
