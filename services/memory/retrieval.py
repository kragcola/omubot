"""Retrieval gate: decide which memory cards to inject into the system prompt.

Four-tier gating strategy:
  1. New session (turn 1) → full retrieval (cached)
  2. Periodic refresh (every N turns) → full retrieval (cached)
  3. Keyword match → partial cards matching conversation topic
  4. Default → minimal hint + prompt to use lookup_cards tool

Pool-aware: when GroupMemoryConfig is provided, group scope resolves to
one or more pool scope IDs via resolve_group_pools().
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from loguru import logger
from services.similarity import SimilarityBackend, SimilarityProvider, create_similarity_provider

if TYPE_CHECKING:
    from kernel.config import GroupMemoryConfig
    from services.memory.card_store import CardStore

_L = logger.bind(channel="system")

_MAX_SESSIONS = 500
_SPLIT_RE = re.compile("[，。！？、；：\"\"''（）()\\s]+")
_SEMANTIC_MATCH_THRESHOLD = 0.18


class _GateState:
    __slots__ = ("last_full_refresh_turn", "turn_count")

    def __init__(self) -> None:
        self.turn_count: int = 0
        self.last_full_refresh_turn: int = -1


class RetrievalGate:
    """Per-session gating layer that decides which cards to inject."""

    def __init__(
        self,
        card_store: CardStore,
        refresh_interval: int = 10,
        group_memory_config: GroupMemoryConfig | None = None,
        *,
        semantic_enabled: bool = False,
        semantic_backend: SimilarityBackend = "ngram",
    ) -> None:
        self._store = card_store
        self._refresh_interval = refresh_interval
        self._group_memory_config = group_memory_config
        self._sessions: dict[str, _GateState] = {}
        self._full_cache: dict[str, str] = {}
        self._semantic_enabled = semantic_enabled
        self._semantic_requested_backend: SimilarityBackend = semantic_backend
        self._semantic_active_backend: SimilarityBackend = semantic_backend
        self._semantic_provider: SimilarityProvider = create_similarity_provider(semantic_backend)
        self._semantic_hits = 0
        self._semantic_queries = 0
        self._semantic_fallbacks = 0
        self._semantic_errors = 0
        self._semantic_last_error = ""

    def set_group_memory_config(self, config: GroupMemoryConfig) -> None:
        """Update group memory config (hot-reload from admin UI)."""
        self._group_memory_config = config
        self._full_cache.clear()
        _L.info("retrieval gate | group memory config updated, cache cleared")

    def configure_semantic(
        self,
        *,
        enabled: bool,
        backend: SimilarityBackend = "ngram",
    ) -> None:
        self._semantic_enabled = bool(enabled)
        self._semantic_requested_backend = backend
        self._semantic_active_backend = backend
        self._semantic_provider = create_similarity_provider(backend)
        self._semantic_last_error = ""
        self._semantic_fallbacks = 0
        self._semantic_errors = 0
        _L.info(
            "retrieval gate | semantic {} backend={}",
            "enabled" if self._semantic_enabled else "disabled",
            backend,
        )

    def semantic_status(self) -> dict[str, Any]:
        return {
            "enabled": self._semantic_enabled,
            "requested_backend": self._semantic_requested_backend,
            "active_backend": self._semantic_active_backend,
            "queries": self._semantic_queries,
            "hits": self._semantic_hits,
            "fallbacks": self._semantic_fallbacks,
            "errors": self._semantic_errors,
            "last_error": self._semantic_last_error,
        }

    @staticmethod
    def _cache_key(scope: str, scope_ids: list[str]) -> str:
        """Build a deterministic cache key from scope + scope_ids."""
        return f"{scope}_{','.join(sorted(scope_ids))}"

    # ------------------------------------------------------------------
    # Scope resolution
    # ------------------------------------------------------------------

    def _resolve_ids(self, user_id: str, group_id: str | None) -> tuple[str, list[str]]:
        """Resolve (scope, scope_ids) with pool awareness.

        Returns ("group", [pool_id, ...]) or ("user", [user_id]) or ("global", ["global"]).
        """
        if group_id:
            if self._group_memory_config is not None:
                return ("group", self._group_memory_config.resolve_group_pools(group_id))
            return ("group", [group_id])
        if user_id:
            return ("user", [user_id])
        return ("global", ["global"])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build_memo_block(
        self,
        session_id: str,
        user_id: str,
        group_id: str | None,
        *,
        conversation_text: str = "",
    ) -> str:
        """Return the memo block text for this turn, applying gating decisions."""
        scope, scope_ids = self._resolve_ids(user_id, group_id)

        state = self._get_or_create(session_id)
        state.turn_count += 1

        # Gate 1: new session → full retrieval
        if state.turn_count == 1:
            state.last_full_refresh_turn = 1
            _L.debug("retrieval gate | session={} decision=full (new session)", session_id)
            return await self._full_retrieval(scope, scope_ids, group_id, user_id)

        # Gate 2: periodic refresh
        if state.turn_count - state.last_full_refresh_turn >= self._refresh_interval:
            state.last_full_refresh_turn = state.turn_count
            _L.debug("retrieval gate | session={} decision=full (periodic refresh turn={})",
                     session_id, state.turn_count)
            return await self._full_retrieval(scope, scope_ids, group_id, user_id)

        # Gate 3: keyword match
        if conversation_text:
            keywords = extract_keywords(conversation_text)
            if keywords:
                cards = await self._keyword_search(scope, scope_ids, keywords)
                if cards:
                    _L.debug("retrieval gate | session={} decision=keyword keywords={!r} matched={}",
                             session_id, keywords, len(cards))
                    return _format_cards(cards, scope, scope_ids, group_id, user_id, mode_label="关键词匹配")
            if self._semantic_enabled:
                cards = await self._semantic_search(scope, scope_ids, conversation_text, keywords)
                if cards:
                    self._semantic_hits += 1
                    _L.debug(
                        "retrieval gate | session={} decision=semantic backend={} matched={}",
                        session_id,
                        self._semantic_active_backend,
                        len(cards),
                    )
                    return _format_cards(cards, scope, scope_ids, group_id, user_id, mode_label="轻量语义匹配")

        # Gate 4: minimal hint
        total = await self._count_active(scope, scope_ids)
        if total > 0:
            label = "用户" if scope == "user" else "群" if scope == "group" else "全局"
            id_part = f" / {_scope_ids_display(scope_ids)}" if scope != "global" else ""
            _L.debug("retrieval gate | session={} decision=minimal total={}", session_id, total)
            return (
                f"【{label}记忆{id_part}】你有 {total} 张记忆卡片。"
                "使用 lookup_cards 工具按 scope/scope_id 或关键词查询。"
            )
        return ""

    def invalidate_entity(self, scope: str, scope_id: str) -> None:
        """Clear full-retrieval cache for one entity (called after card changes).

        For group scope, clears both the direct key and any pool-resolved keys.
        """
        key = self._cache_key(scope, [scope_id])
        if self._full_cache.pop(key, None) is not None:
            _L.debug("retrieval gate cache cleared | entity={}", key)
        # Pool-aware: if this is a group, also clear the resolved pool caches
        if scope == "group" and self._group_memory_config is not None:
            for pid in self._group_memory_config.resolve_group_pools(scope_id):
                pool_key = self._cache_key("group", [pid])
                if self._full_cache.pop(pool_key, None) is not None:
                    _L.debug("retrieval gate cache cleared | pool entity={}", pool_key)

    def invalidate_session(self, session_id: str) -> None:
        """Reset gate state for a session."""
        if self._sessions.pop(session_id, None) is not None:
            _L.debug("retrieval gate session reset | session={}", session_id)

    def rewind_turn(self, session_id: str) -> None:
        """Undo the turn count increment for a session.

        Call when build_memo_block was invoked but no actual LLM call
        followed (e.g. thinker decided 'wait').
        """
        state = self._sessions.get(session_id)
        if state is None:
            return
        if state.turn_count > 0:
            state.turn_count -= 1
            _L.debug("retrieval gate turn rewound | session={} turn_count={}", session_id, state.turn_count)

    def invalidate_all(self) -> None:
        """Clear all caches and session state."""
        self._sessions.clear()
        self._full_cache.clear()
        _L.debug("retrieval gate reset | all caches cleared")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create(self, session_id: str) -> _GateState:
        if session_id not in self._sessions:
            if len(self._sessions) >= _MAX_SESSIONS:
                oldest = next(iter(self._sessions))
                del self._sessions[oldest]
            self._sessions[session_id] = _GateState()
        return self._sessions[session_id]

    async def _full_retrieval(
        self, scope: str, scope_ids: list[str], group_id: str | None, user_id: str
    ) -> str:
        """Full entity prompt, cached by entity key."""
        cache_key = self._cache_key(scope, scope_ids)
        if cache_key in self._full_cache:
            return self._full_cache[cache_key]

        text = await self._store.build_entity_prompt_multi(scope, scope_ids)
        if group_id:
            text = f"【当前在群 #{group_id} 中对话】\n{text}"
        elif scope == "user":
            text = f"【当前私聊 @{user_id}】\n{text}"
        self._full_cache[cache_key] = text
        return text

    async def _keyword_search(
        self, scope: str, scope_ids: list[str], keywords: list[str]
    ) -> list:
        """Search cards matching any keyword, scoped to these entities + global."""
        from services.memory.card_store import Card

        scope_ids_set = set(scope_ids)
        seen: set[str] = set()
        results: list[Card] = []

        for kw in keywords:
            batch = await self._store.search_cards(kw, limit=5)
            for c in batch:
                if c.card_id in seen:
                    continue
                if c.scope in (scope, "global") and (c.scope == "global" or c.scope_id in scope_ids_set):
                    seen.add(c.card_id)
                    results.append(c)

        return results[:10]

    async def _semantic_search(
        self,
        scope: str,
        scope_ids: list[str],
        conversation_text: str,
        keywords: list[str],
    ) -> list:
        """Fallback semantic search using the configured SimilarityProvider."""
        from services.memory.card_store import Card

        self._semantic_queries += 1
        scope_ids_set = set(scope_ids)
        cards: list[Card] = []
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

        scored: list[tuple[float, Card]] = []
        signals = [conversation_text, *keywords]
        for card in cards:
            if card.scope not in (scope, "global"):
                continue
            if card.scope != "global" and card.scope_id not in scope_ids_set:
                continue
            score = max((self._semantic_score(signal, card.content) for signal in signals if signal), default=0.0)
            if score >= _SEMANTIC_MATCH_THRESHOLD:
                scored.append((score, card))

        scored.sort(key=lambda item: (-item[0], -item[1].priority, item[1].updated_at), reverse=False)
        return [card for _, card in scored[:8]]

    async def _count_active(self, scope: str, scope_ids: list[str]) -> int:
        total = 0
        for sid in scope_ids:
            cards = await self._store.get_entity_cards(scope, sid)
            total += len(cards)
        return total

    def _semantic_score(self, left: str, right: str) -> float:
        try:
            return max(0.0, min(1.0, float(self._semantic_provider.similarity(left, right))))
        except Exception as exc:
            self._semantic_errors += 1
            self._semantic_last_error = str(exc)[:180]
            if self._semantic_active_backend != "ngram":
                self._semantic_fallbacks += 1
                self._semantic_active_backend = "ngram"
                self._semantic_provider = create_similarity_provider("ngram")
                with_exception = str(exc)[:120]
                _L.warning(
                    "retrieval gate | semantic backend fallback {} -> ngram | error={}",
                    self._semantic_requested_backend,
                    with_exception,
                )
                return max(0.0, min(1.0, float(self._semantic_provider.similarity(left, right))))
            return 0.0


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _scope_ids_display(scope_ids: list[str]) -> str:
    """Short display string for scope IDs."""
    if len(scope_ids) == 1:
        return scope_ids[0]
    return ", ".join(scope_ids[:3]) + ("..." if len(scope_ids) > 3 else "")


def extract_keywords(text: str) -> list[str]:
    """Split text on punctuation, return up to 5 unique 2-8 char segments."""
    if not text or not text.strip():
        return []
    parts = [p.strip() for p in _SPLIT_RE.split(text) if p.strip()]
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        if 2 <= len(part) <= 8 and part not in seen:
            seen.add(part)
            result.append(part)
        if len(result) >= 5:
            break
    return result


def _format_cards(
    cards: list,
    scope: str,
    scope_ids: list[str],
    group_id: str | None,
    user_id: str,
    *,
    mode_label: str = "关键词匹配",
) -> str:
    from services.memory.card_store import CATEGORY_LABELS

    display = _scope_ids_display(scope_ids)
    if group_id:
        header = f"【群记忆 / #{display}（{mode_label}）】"
    elif scope == "user":
        header = f"【用户记忆 / @{display}（{mode_label}）】"
    else:
        header = f"【全局记忆（{mode_label}）】"

    lines = [header]
    for c in cards:
        cat_label = CATEGORY_LABELS.get(c.category, c.category)
        lines.append(f"[{cat_label}] {c.content}")

    if group_id:
        lines.insert(0, f"【当前在群 #{group_id} 中对话】")

    return "\n".join(lines)
