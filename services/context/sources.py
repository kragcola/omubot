"""Context source adapters."""

from __future__ import annotations

from typing import Any

from loguru import logger

from services.context.types import ContextHit
from services.memory.retrieval import extract_keywords
from services.similarity import create_similarity_provider

_L = logger.bind(channel="system")

_MAX_MEMORY_SESSIONS = 500
_MEMORY_REFRESH_INTERVAL = 10
_MEMORY_SEMANTIC_THRESHOLD = 0.18


class _MemorySessionState:
    __slots__ = ("last_full_refresh_turn", "turn_count")

    def __init__(self) -> None:
        self.turn_count = 0
        self.last_full_refresh_turn = -1


class MemoryContextSource:
    """Read-only adapter from CardStore to ContextHit."""

    name = "memory"

    def __init__(self, card_store: Any, group_memory_config: Any = None) -> None:
        self._store = card_store
        self._group_memory_config = group_memory_config
        self._sessions: dict[str, _MemorySessionState] = {}
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
        if self._store is None:
            return []
        scope, scope_ids = self._resolve_ids(user_id, group_id)
        query_text = query.strip()

        decision = "keyword"
        cards: list[Any] = []
        if session_id:
            state = self._get_or_create(session_id)
            state.turn_count += 1
            if state.turn_count == 1:
                state.last_full_refresh_turn = 1
                decision = "full_new_session"
                cards = await self._all_scoped_cards(scope, scope_ids)
            elif state.turn_count - state.last_full_refresh_turn >= _MEMORY_REFRESH_INTERVAL:
                state.last_full_refresh_turn = state.turn_count
                decision = "full_periodic"
                cards = await self._all_scoped_cards(scope, scope_ids)
        elif not query_text:
            decision = "full_empty_query"
            cards = await self._all_scoped_cards(scope, scope_ids)

        if not cards and query_text:
            keywords = extract_keywords(query_text)
            cards = await self._keyword_search(scope, scope_ids, keywords)
            if cards:
                decision = "keyword"
            else:
                cards = await self._semantic_search(scope, scope_ids, query_text, keywords)
                if cards:
                    decision = "semantic_ngram"

        hits: list[ContextHit] = []
        seen: set[str] = set()
        for card in cards:
            if card.card_id in seen:
                continue
            seen.add(card.card_id)
            score = round(float(card.confidence) + (float(card.priority) / 10.0), 6)
            hits.append(ContextHit(
                id=card.card_id,
                type="memory_card",
                content=card.content,
                score=score,
                source=card.source,
                title=f"{_scope_label(card.scope)}记忆 / {card.category}",
                scope=card.scope,
                scope_id=card.scope_id,
                status=card.status,
                retriever="card_store",
                metadata={
                    "category": card.category,
                    "confidence": card.confidence,
                    "priority": card.priority,
                    "supersedes": card.supersedes,
                    "series_id": card.series_id,
                    "updated_at": card.updated_at,
                    "decision": decision,
                },
            ))

        hits.sort(key=lambda hit: (-hit.score, hit.id))
        if hits:
            result = hits[:top_k]
            _L.debug(
                "context memory source | decision={} scope={} ids={} hits={} query={!r}",
                decision,
                scope,
                scope_ids,
                len(result),
                _safe_log_query(query_text),
            )
            return result

        if not session_id:
            _L.debug(
                "context memory source | decision=miss scope={} ids={} query={!r}",
                scope,
                scope_ids,
                _safe_log_query(query_text),
            )
            return []

        total = await self._count_active(scope, scope_ids)
        if total <= 0:
            _L.debug(
                "context memory source | decision=miss scope={} ids={} query={!r}",
                scope,
                scope_ids,
                _safe_log_query(query_text),
            )
            return []

        _L.debug(
            "context memory source | decision=minimal_hint scope={} ids={} total={} query={!r}",
            scope,
            scope_ids,
            total,
            _safe_log_query(query_text),
        )
        return [ContextHit(
            id=f"memory_hint:{scope}:{','.join(scope_ids)}",
            type="memory_card",
            content=(
                f"当前{_scope_label(scope)}作用域有 {total} 张记忆卡片。"
                "如本轮需要细节，可使用 lookup_cards 工具按 scope/scope_id 或关键词查询。"
            ),
            score=0.05,
            source="card_store",
            title=f"{_scope_label(scope)}记忆 / 提示",
            scope=scope,
            scope_id=",".join(scope_ids) if scope_ids else "global",
            status="active",
            retriever="card_store_hint",
            metadata={"decision": "minimal_hint", "card_count": total},
        )]

    def _resolve_ids(self, user_id: str, group_id: str | None) -> tuple[str, list[str]]:
        if group_id:
            if self._group_memory_config is not None:
                return ("group", self._group_memory_config.resolve_group_pools(str(group_id)))
            return ("group", [str(group_id)])
        if user_id:
            return ("user", [str(user_id)])
        return ("global", ["global"])

    def _get_or_create(self, session_id: str) -> _MemorySessionState:
        if session_id not in self._sessions:
            if len(self._sessions) >= _MAX_MEMORY_SESSIONS:
                oldest = next(iter(self._sessions))
                del self._sessions[oldest]
            self._sessions[session_id] = _MemorySessionState()
        return self._sessions[session_id]

    async def _all_scoped_cards(self, scope: str, scope_ids: list[str]) -> list[Any]:
        cards: list[Any] = []
        seen: set[str] = set()
        for sid in scope_ids:
            for card in await self._store.get_entity_cards(scope, sid):
                if card.card_id not in seen:
                    seen.add(card.card_id)
                    cards.append(card)
        for card in await self._store.get_entity_cards("global", "global"):
            if card.card_id not in seen:
                seen.add(card.card_id)
                cards.append(card)
        cards.sort(key=lambda c: (c.priority, c.confidence, c.updated_at), reverse=True)
        return cards

    async def _keyword_search(self, scope: str, scope_ids: list[str], keywords: list[str]) -> list[Any]:
        if not keywords:
            return []
        allowed = self._allowed(scope, scope_ids)
        cards: list[Any] = []
        seen: set[str] = set()
        for keyword in keywords:
            for card in await self._store.search_cards(keyword, limit=8):
                if card.card_id in seen or (card.scope, card.scope_id) not in allowed:
                    continue
                seen.add(card.card_id)
                cards.append(card)
        cards.sort(key=lambda c: (c.priority, c.confidence, c.updated_at), reverse=True)
        return cards

    async def _semantic_search(
        self,
        scope: str,
        scope_ids: list[str],
        query: str,
        keywords: list[str],
    ) -> list[Any]:
        cards = await self._all_scoped_cards(scope, scope_ids)
        signals = [query, *keywords]
        scored: list[tuple[float, Any]] = []
        for card in cards:
            score = max(
                (self._similarity.similarity(signal, card.content) for signal in signals if signal),
                default=0.0,
            )
            if score >= _MEMORY_SEMANTIC_THRESHOLD:
                scored.append((score, card))
        scored.sort(key=lambda item: (item[0], item[1].priority, item[1].confidence, item[1].updated_at), reverse=True)
        return [card for _, card in scored]

    async def _count_active(self, scope: str, scope_ids: list[str]) -> int:
        return len(await self._all_scoped_cards(scope, scope_ids))

    @staticmethod
    def _allowed(scope: str, scope_ids: list[str]) -> set[tuple[str, str]]:
        allowed = {(scope, scope_id) for scope_id in scope_ids}
        allowed.add(("global", "global"))
        return allowed


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
    ) -> None:
        self._knowledge_graph = knowledge_graph
        self._ctx = ctx
        self._group_memory_config = group_memory_config
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
        relationships = await graph.list_relationships(limit=200)
        hits: list[ContextHit] = []
        for item in relationships:
            scope = str(item.get("scope") or "global")
            scope_id = str(item.get("scope_id") or "global")
            if (scope, scope_id) not in allowed:
                continue
            text = f"{item.get('subject', '')} {item.get('predicate', '')} {item.get('object', '')}"
            score = self._similarity.similarity(query, text)
            if score < 0.18:
                continue
            content = f"{item.get('subject', '')} --{item.get('predicate', '')}-> {item.get('object', '')}"
            hits.append(ContextHit(
                id=str(item.get("fact_id") or content),
                type="graph_fact",
                content=content,
                score=round(score + float(item.get("confidence") or 0.0), 6),
                source=str(item.get("source") or "knowledge_graph"),
                title="知识图谱事实",
                scope=scope,
                scope_id=scope_id,
                status=str(item.get("status") or "active"),
                retriever="graph_ngram",
                metadata=dict(item),
            ))

        hits.sort(key=lambda hit: (-hit.score, hit.id))
        return hits[:top_k]

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


def _safe_log_query(query: str, limit: int = 80) -> str:
    text = " ".join((query or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
