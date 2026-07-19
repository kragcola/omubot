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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.memory.card_eligibility import (
    DEFAULT_CARD_ELIGIBILITY_POLICY,
    CardEligibilityPolicy,
    filter_cards_for_recall,
    filter_scored_cards_for_recall,
    is_card_eligible_for_recall,
)
from services.memory.visibility import filter_cards_for_group_recall
from services.similarity import SimilarityBackend, SimilarityProvider, create_similarity_provider

if TYPE_CHECKING:
    from kernel.config import GroupMemoryConfig
    from services.memory.card_store import CardStore

_L = logger.bind(channel="system")

_MAX_SESSIONS = 500
_SPLIT_RE = re.compile("[，。！？、；：\"\"''（）()\\s]+")
_SEMANTIC_MATCH_THRESHOLD = 0.18
_RECENCY_HALF_LIFE_DAYS = 30.0
_RELEVANCE_WEIGHT = 0.45
_IMPORTANCE_WEIGHT = 0.25
_RECENCY_WEIGHT = 0.15
_CONFIDENCE_WEIGHT = 0.15


class _GateState:
    __slots__ = ("last_full_refresh_turn", "turn_count")

    def __init__(self) -> None:
        self.turn_count: int = 0
        self.last_full_refresh_turn: int = -1


@dataclass(frozen=True, slots=True)
class MemoryRetrievalHit:
    """One card selected by the authoritative memory planner."""

    card: Any
    relevance: float | None
    importance: float
    recency: float
    confidence: float
    source_score: float


@dataclass(frozen=True, slots=True)
class MemoryRetrievalResult:
    """Structured planner output shared by legacy memo and ContextService.

    Count semantics (always scope+global **eligible** active cards for recall):

    * ``total_active`` — all active cards visible to the resolved scope plus
      global that pass Card category time eligibility, regardless of
      keyword/semantic/full/minimal decision.
    * ``matched_active`` — pre-``top_k`` eligible candidates selected by this
      query/planner path (full ⇒ equals ``total_active``; keyword/semantic ⇒
      match set size; minimal/miss ⇒ 0).
    """

    decision: str
    hits: tuple[MemoryRetrievalHit, ...]
    scope: str
    scope_ids: tuple[str, ...]
    total_active: int
    matched_active: int
    semantic_backend: str


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
        now_provider: Callable[[], datetime] | None = None,
        card_eligibility: CardEligibilityPolicy | None = None,
    ) -> None:
        self._store = card_store
        self._refresh_interval = refresh_interval
        self._group_memory_config = group_memory_config
        self._sessions: dict[str, _GateState] = {}
        self._full_cache: dict[str, list[Any]] = {}
        self._semantic_enabled = semantic_enabled
        self._semantic_requested_backend: SimilarityBackend = semantic_backend
        self._semantic_active_backend: SimilarityBackend = semantic_backend
        self._semantic_provider: SimilarityProvider = create_similarity_provider(semantic_backend)
        self._semantic_hits = 0
        self._semantic_queries = 0
        self._semantic_fallbacks = 0
        self._semantic_errors = 0
        self._semantic_last_error = ""
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._card_eligibility = (
            card_eligibility
            if card_eligibility is not None
            else DEFAULT_CARD_ELIGIBILITY_POLICY
        )
        self._activate_semantic_provider(semantic_backend)

    def set_card_eligibility(self, policy: CardEligibilityPolicy) -> None:
        """Hot-reload eligibility policy and drop full-retrieval cache."""
        self._card_eligibility = policy
        self._full_cache.clear()
        _L.info(
            "retrieval gate | card eligibility updated enabled={}",
            policy.enabled,
        )

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
        self._semantic_last_error = ""
        self._semantic_fallbacks = 0
        self._semantic_errors = 0
        self._activate_semantic_provider(backend)
        _L.info(
            "retrieval gate | semantic {} backend={}",
            "enabled" if self._semantic_enabled else "disabled",
            backend,
        )

    def semantic_status(self) -> dict[str, Any]:
        degraded = bool(
            self._semantic_enabled
            and self._semantic_active_backend != self._semantic_requested_backend
        )
        return {
            "enabled": self._semantic_enabled,
            "requested_backend": self._semantic_requested_backend,
            "active_backend": self._semantic_active_backend,
            "healthy": not self._semantic_enabled or not degraded,
            "degraded": degraded,
            "queries": self._semantic_queries,
            "hits": self._semantic_hits,
            "fallbacks": self._semantic_fallbacks,
            "errors": self._semantic_errors,
            "last_error": self._semantic_last_error,
        }

    def _activate_semantic_provider(self, backend: SimilarityBackend) -> None:
        provider = create_similarity_provider(backend)
        self._semantic_active_backend = backend
        self._semantic_provider = provider
        # Prefer explicit health fields when present; otherwise treat the
        # unfinished embedding stub as unavailable so we degrade before query.
        if hasattr(provider, "available"):
            available = bool(provider.available)
            reason = str(
                getattr(provider, "unavailable_reason", None) or "backend unavailable"
            )
        elif backend == "embedding":
            available = False
            reason = "embedding similarity backend is not installed/enabled"
        else:
            available = True
            reason = ""
        if not self._semantic_enabled or available:
            return
        self._semantic_errors += 1
        self._semantic_fallbacks += 1
        self._semantic_last_error = reason[:180]
        self._semantic_active_backend = "ngram"
        self._semantic_provider = create_similarity_provider("ngram")
        _L.warning(
            "retrieval gate | semantic backend unavailable, fallback {} -> ngram | error={}",
            backend,
            self._semantic_last_error,
        )

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
        result = await self.retrieve_cards(
            session_id=session_id,
            user_id=user_id,
            group_id=group_id,
            conversation_text=conversation_text,
        )
        return _format_retrieval_result(
            result,
            group_id=group_id,
            user_id=user_id,
        )

    async def retrieve_cards(
        self,
        *,
        session_id: str = "",
        user_id: str = "",
        group_id: str | None = None,
        conversation_text: str = "",
        top_k: int = 10,
    ) -> MemoryRetrievalResult:
        """Plan one memory retrieval and return structured, explainable hits."""
        scope, scope_ids = self._resolve_ids(user_id, group_id)
        query_text = conversation_text.strip()
        speaker_user_id = str(user_id or "").strip()

        if session_id:
            state = self._get_or_create(session_id)
            state.turn_count += 1
            if state.turn_count == 1:
                state.last_full_refresh_turn = 1
                cards = await self._full_retrieval_cards(
                    scope,
                    scope_ids,
                    group_id=group_id,
                    speaker_user_id=speaker_user_id,
                )
                _L.debug(
                    "retrieval gate | session={} decision=full_new_session matched={}",
                    session_id,
                    len(cards),
                )
                return self._result(
                    decision="full_new_session",
                    cards=cards,
                    scope=scope,
                    scope_ids=scope_ids,
                    top_k=top_k,
                )
            if state.turn_count - state.last_full_refresh_turn >= self._refresh_interval:
                state.last_full_refresh_turn = state.turn_count
                cards = await self._full_retrieval_cards(
                    scope,
                    scope_ids,
                    group_id=group_id,
                    speaker_user_id=speaker_user_id,
                )
                _L.debug(
                    "retrieval gate | session={} decision=full_periodic turn={} matched={}",
                    session_id,
                    state.turn_count,
                    len(cards),
                )
                return self._result(
                    decision="full_periodic",
                    cards=cards,
                    scope=scope,
                    scope_ids=scope_ids,
                    top_k=top_k,
                )
        elif not query_text:
            cards = await self._full_retrieval_cards(
                scope,
                scope_ids,
                group_id=group_id,
                speaker_user_id=speaker_user_id,
            )
            return self._result(
                decision="full_empty_query",
                cards=cards,
                scope=scope,
                scope_ids=scope_ids,
                top_k=top_k,
            )

        keywords = extract_keywords(query_text) if query_text else []
        if keywords:
            cards = await self._keyword_search(
                scope,
                scope_ids,
                keywords,
                group_id=group_id,
                speaker_user_id=speaker_user_id,
            )
            if cards:
                _L.debug(
                    "retrieval gate | session={} decision=keyword keywords={!r} matched={}",
                    session_id,
                    keywords,
                    len(cards),
                )
                total = await self._count_active(
                    scope,
                    scope_ids,
                    group_id=group_id,
                    speaker_user_id=speaker_user_id,
                )
                return self._result(
                    decision="keyword",
                    cards=cards,
                    scope=scope,
                    scope_ids=scope_ids,
                    top_k=top_k,
                    relevance=1.0,
                    total_active=total,
                    matched_active=len(cards),
                )

        if query_text and self._semantic_enabled:
            scored = await self._semantic_search(
                scope,
                scope_ids,
                query_text,
                keywords,
                group_id=group_id,
                speaker_user_id=speaker_user_id,
            )
            if scored:
                self._semantic_hits += 1
                _L.debug(
                    "retrieval gate | session={} decision=semantic backend={} matched={}",
                    session_id,
                    self._semantic_active_backend,
                    len(scored),
                )
                total = await self._count_active(
                    scope,
                    scope_ids,
                    group_id=group_id,
                    speaker_user_id=speaker_user_id,
                )
                return self._scored_result(
                    decision=f"semantic_{self._semantic_active_backend}",
                    scored=scored,
                    scope=scope,
                    scope_ids=scope_ids,
                    top_k=top_k,
                    total_active=total,
                )

        total = await self._count_active(
            scope,
            scope_ids,
            group_id=group_id,
            speaker_user_id=speaker_user_id,
        )
        decision = "minimal_hint" if total > 0 and session_id else "miss"
        _L.debug(
            "retrieval gate | session={} decision={} total={}",
            session_id,
            decision,
            total,
        )
        return MemoryRetrievalResult(
            decision=decision,
            hits=(),
            scope=scope,
            scope_ids=tuple(scope_ids),
            total_active=total,
            matched_active=0,
            semantic_backend=self._semantic_active_backend,
        )

    def invalidate_entity(self, scope: str, scope_id: str) -> None:
        """Clear full-retrieval cache for one entity (called after card changes).

        For group scope, clears both the direct key and any pool-resolved keys.
        """
        if scope == "global":
            self._full_cache.clear()
            _L.debug("retrieval gate cache cleared | global card changed")
            return
        target_ids = {str(scope_id)}
        if scope == "group" and self._group_memory_config is not None:
            target_ids.update(
                str(item)
                for item in self._group_memory_config.resolve_group_pools(scope_id)
            )
        for key in list(self._full_cache):
            prefix, _, raw_ids = key.partition("_")
            if prefix != scope:
                continue
            cached_ids = set(raw_ids.split(",")) if raw_ids else set()
            if cached_ids & target_ids:
                del self._full_cache[key]
                _L.debug("retrieval gate cache cleared | entity={}", key)

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

    def _now(self) -> datetime:
        return self._now_provider()

    def _eligible(self, card: Any) -> bool:
        return is_card_eligible_for_recall(
            card,
            policy=self._card_eligibility,
            now=self._now(),
        )

    def _filter_eligible(self, cards: list[Any]) -> list[Any]:
        """Apply read-time eligibility; re-filter even cached full sets."""
        return filter_cards_for_recall(
            cards,
            policy=self._card_eligibility,
            now=self._now(),
        )

    async def _full_retrieval_cards(
        self,
        scope: str,
        scope_ids: list[str],
        *,
        group_id: str | None = None,
        speaker_user_id: str = "",
    ) -> list[Any]:
        """Full scoped card set, cached without request-specific prompt headers.

        The cache stores **raw** active cards from the store. Eligibility is
        always re-applied on read so a card that ages past its category TTL
        cannot be served after it becomes ineligible at read time.

        Group chat additionally merges the current speaker's same_group-visible
        user facts (visibility ≠ storage scope). Speaker-specific filtering is
        applied on read and is not cached under the bare group key.
        """
        cache_key = self._cache_key(scope, scope_ids)
        if cache_key in self._full_cache:
            cards = self._filter_eligible(list(self._full_cache[cache_key]))
        else:
            cards = []
            seen: set[str] = set()
            for sid in scope_ids:
                for card in await self._store.get_entity_cards(scope, sid):
                    if card.card_id in seen:
                        continue
                    seen.add(card.card_id)
                    cards.append(card)
            for card in await self._store.get_entity_cards("global", "global"):
                if card.card_id in seen:
                    continue
                seen.add(card.card_id)
                cards.append(card)
            cards.sort(
                key=lambda card: (card.priority, card.confidence, card.updated_at),
                reverse=True,
            )
            self._full_cache[cache_key] = list(cards)
            cards = self._filter_eligible(cards)

        if scope == "group" and group_id and speaker_user_id:
            cards = await self._merge_speaker_same_group_user_facts(
                cards,
                group_id=group_id,
                speaker_user_id=speaker_user_id,
                group_pool_ids=scope_ids,
            )
        return cards

    async def _merge_speaker_same_group_user_facts(
        self,
        base_cards: list[Any],
        *,
        group_id: str,
        speaker_user_id: str,
        group_pool_ids: list[str],
    ) -> list[Any]:
        """Append current speaker's same_group-visible user cards (fail closed).

        Does **not** merge all user cards — only the speaker, and only cards
        that pass ``card_visible_in_group_recall``.
        """
        if not speaker_user_id:
            return list(base_cards)
        user_cards = await self._store.get_entity_cards("user", speaker_user_id)
        user_cards = self._filter_eligible(user_cards)
        allowed = filter_cards_for_group_recall(
            user_cards,
            group_id=group_id,
            speaker_user_id=speaker_user_id,
            group_pool_ids=group_pool_ids,
        )
        if not allowed:
            return list(base_cards)
        seen = {c.card_id for c in base_cards}
        merged = list(base_cards)
        for card in allowed:
            if card.card_id in seen:
                continue
            # Defense: only user-scope speaker cards from the helper.
            if str(getattr(card, "scope", "")) != "user":
                continue
            seen.add(card.card_id)
            merged.append(card)
        merged.sort(
            key=lambda card: (card.priority, card.confidence, card.updated_at),
            reverse=True,
        )
        return merged

    def _result(
        self,
        *,
        decision: str,
        cards: list[Any],
        scope: str,
        scope_ids: list[str],
        top_k: int,
        relevance: float | None = None,
        total_active: int | None = None,
        matched_active: int | None = None,
    ) -> MemoryRetrievalResult:
        hits = [self._build_hit(card, relevance=relevance) for card in cards]
        hits.sort(key=lambda hit: (-hit.source_score, hit.card.card_id))
        matched = len(cards) if matched_active is None else int(matched_active)
        # Full paths: total_active == matched_active == all visible cards.
        # Keyword/semantic: total_active is full scope; matched is pre-top_k.
        total = matched if total_active is None else int(total_active)
        return MemoryRetrievalResult(
            decision=decision,
            hits=tuple(hits[: max(1, int(top_k))]),
            scope=scope,
            scope_ids=tuple(scope_ids),
            total_active=total,
            matched_active=matched,
            semantic_backend=self._semantic_active_backend,
        )

    def _scored_result(
        self,
        *,
        decision: str,
        scored: list[tuple[float, Any]],
        scope: str,
        scope_ids: list[str],
        top_k: int,
        total_active: int | None = None,
    ) -> MemoryRetrievalResult:
        hits = [
            self._build_hit(card, relevance=score)
            for score, card in scored
        ]
        hits.sort(key=lambda hit: (-hit.source_score, hit.card.card_id))
        matched = len(scored)
        total = matched if total_active is None else int(total_active)
        return MemoryRetrievalResult(
            decision=decision,
            hits=tuple(hits[: max(1, int(top_k))]),
            scope=scope,
            scope_ids=tuple(scope_ids),
            total_active=total,
            matched_active=matched,
            semantic_backend=self._semantic_active_backend,
        )

    def _build_hit(
        self,
        card: Any,
        *,
        relevance: float | None,
    ) -> MemoryRetrievalHit:
        importance = _clamp01(float(card.priority) / 10.0)
        confidence = _clamp01(float(card.confidence))
        recency = _recency_score(
            str(getattr(card, "updated_at", "") or ""),
            now=self._now_provider(),
        )
        relevance_score = 0.5 if relevance is None else _clamp01(relevance)
        source_score = round(
            (_RELEVANCE_WEIGHT * relevance_score)
            + (_IMPORTANCE_WEIGHT * importance)
            + (_RECENCY_WEIGHT * recency)
            + (_CONFIDENCE_WEIGHT * confidence),
            6,
        )
        return MemoryRetrievalHit(
            card=card,
            relevance=relevance,
            importance=importance,
            recency=recency,
            confidence=confidence,
            source_score=source_score,
        )

    async def _keyword_search(
        self,
        scope: str,
        scope_ids: list[str],
        keywords: list[str],
        *,
        group_id: str | None = None,
        speaker_user_id: str = "",
    ) -> list[Any]:
        """Search the eligible visible card set for any keyword.

        ``CardStore.search_cards`` applies its SQL limit before the eligibility
        filter, so stale high-priority rows could starve a fresh match. The
        keyword path already needs the full visible set for truthful
        ``total_active`` counts; reuse that cache and filter before matching.
        """
        seen: set[str] = set()
        results: list[Any] = []
        visible_cards = await self._full_retrieval_cards(
            scope,
            scope_ids,
            group_id=group_id,
            speaker_user_id=speaker_user_id,
        )

        for kw in keywords:
            needle = str(kw or "").casefold()
            if not needle:
                continue
            for c in visible_cards:
                if c.card_id in seen:
                    continue
                if needle not in str(getattr(c, "content", "") or "").casefold():
                    continue
                seen.add(c.card_id)
                results.append(c)
                if len(results) >= 10:
                    return results

        return results

    async def _semantic_search(
        self,
        scope: str,
        scope_ids: list[str],
        conversation_text: str,
        keywords: list[str],
        *,
        group_id: str | None = None,
        speaker_user_id: str = "",
    ) -> list[tuple[float, Any]]:
        """Fallback semantic search using the configured SimilarityProvider."""
        from services.memory.card_store import Card

        self._semantic_queries += 1
        # Reuse full visible set (includes speaker same_group user facts in groups).
        cards_any = await self._full_retrieval_cards(
            scope,
            scope_ids,
            group_id=group_id,
            speaker_user_id=speaker_user_id,
        )
        cards: list[Card] = list(cards_any)

        scored: list[tuple[float, Card]] = []
        signals = [conversation_text, *keywords]
        for card in cards:
            score = max(
                (
                    self._semantic_score(signal, card.content)
                    for signal in signals
                    if signal
                ),
                default=0.0,
            )
            if score >= _SEMANTIC_MATCH_THRESHOLD:
                scored.append((score, card))

        scored.sort(key=lambda item: (-item[0], -item[1].priority, item[1].updated_at), reverse=False)
        # Defense in depth: re-filter scored pairs (identity if already filtered).
        scored = filter_scored_cards_for_recall(
            scored,
            policy=self._card_eligibility,
            now=self._now(),
        )
        return scored[:8]

    async def _count_active(
        self,
        scope: str,
        scope_ids: list[str],
        *,
        group_id: str | None = None,
        speaker_user_id: str = "",
    ) -> int:
        return len(
            await self._full_retrieval_cards(
                scope,
                scope_ids,
                group_id=group_id,
                speaker_user_id=speaker_user_id,
            )
        )

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


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _recency_score(updated_at: str, *, now: datetime) -> float:
    if not updated_at:
        return 0.5
    try:
        timestamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.5
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    age_days = max(
        0.0,
        (now.astimezone(UTC) - timestamp.astimezone(UTC)).total_seconds()
        / 86_400.0,
    )
    return round(0.5 ** (age_days / _RECENCY_HALF_LIFE_DAYS), 6)


def _format_retrieval_result(
    result: MemoryRetrievalResult,
    *,
    group_id: str | None,
    user_id: str,
) -> str:
    cards = [item.card for item in result.hits]
    scope_ids = list(result.scope_ids)
    if cards:
        mode_label = {
            "keyword": "关键词匹配",
            "semantic_ngram": "轻量语义匹配",
            "semantic_embedding": "语义匹配",
        }.get(result.decision)
        text = _format_cards(
            cards,
            result.scope,
            scope_ids,
            group_id,
            user_id,
            mode_label=mode_label,
        )
        if (
            result.decision.startswith("full_")
            and not group_id
            and result.scope == "user"
        ):
            return f"【当前私聊 @{user_id}】\n{text}"
        return text
    if result.decision.startswith("full_"):
        if result.scope == "user":
            header = f"【用户记忆 / @{_scope_ids_display(scope_ids)}】"
        elif result.scope == "group":
            header = f"【群记忆 / #{_scope_ids_display(scope_ids)}】"
        else:
            header = "【全局记忆】"
        if group_id:
            return f"【当前在群 #{group_id} 中对话】\n{header}\n暂无记录"
        if result.scope == "user":
            return f"【当前私聊 @{user_id}】\n{header}\n暂无记录"
        return f"{header}\n暂无记录"
    if result.decision == "minimal_hint" and result.total_active > 0:
        label = (
            "用户"
            if result.scope == "user"
            else "群"
            if result.scope == "group"
            else "全局"
        )
        id_part = (
            f" / {_scope_ids_display(scope_ids)}"
            if result.scope != "global"
            else ""
        )
        return (
            f"【{label}记忆{id_part}】你有 {result.total_active} 张记忆卡片。"
            "使用 lookup_cards 工具按 scope/scope_id 或关键词查询。"
        )
    return ""


def _format_cards(
    cards: list,
    scope: str,
    scope_ids: list[str],
    group_id: str | None,
    user_id: str,
    *,
    mode_label: str | None = "关键词匹配",
) -> str:
    from services.memory.card_store import CATEGORY_LABELS

    display = _scope_ids_display(scope_ids)
    suffix = f"（{mode_label}）" if mode_label else ""
    if group_id:
        header = f"【群记忆 / #{display}{suffix}】"
    elif scope == "user":
        header = f"【用户记忆 / @{display}{suffix}】"
    else:
        header = f"【全局记忆{suffix}】"

    lines = [header]
    for c in cards:
        cat_label = CATEGORY_LABELS.get(c.category, c.category)
        lines.append(f"[{cat_label}] {c.content}")

    if group_id:
        lines.insert(0, f"【当前在群 #{group_id} 中对话】")

    return "\n".join(lines)
