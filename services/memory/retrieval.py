"""Retrieval gate: decide which memory cards to inject into the system prompt.

Four-tier gating strategy:
  1. New session (turn 1) → full retrieval (cached)
  2. Periodic refresh (every N turns) → full retrieval (cached)
  3. Keyword match → partial cards matching conversation topic
  4. Default → minimal hint + prompt to use lookup_cards tool
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from services.memory.card_store import CardStore

_L = logger.bind(channel="system")

_MAX_SESSIONS = 500
_SPLIT_RE = re.compile("[，。！？、；：\"\"''（）()\\s]+")


class _GateState:
    __slots__ = ("last_full_refresh_turn", "turn_count")

    def __init__(self) -> None:
        self.turn_count: int = 0
        self.last_full_refresh_turn: int = -1


class RetrievalGate:
    """Per-session gating layer that decides which cards to inject."""

    def __init__(self, card_store: CardStore, refresh_interval: int = 10) -> None:
        self._store = card_store
        self._refresh_interval = refresh_interval
        self._sessions: dict[str, _GateState] = {}
        self._full_cache: dict[str, str] = {}

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
        scope, scope_id = _resolve_scope(user_id, group_id)

        state = self._get_or_create(session_id)
        state.turn_count += 1

        # Gate 1: new session → full retrieval
        if state.turn_count == 1:
            state.last_full_refresh_turn = 1
            _L.debug("retrieval gate | session={} decision=full (new session)", session_id)
            return await self._full_retrieval(scope, scope_id, group_id, user_id)

        # Gate 2: periodic refresh
        if state.turn_count - state.last_full_refresh_turn >= self._refresh_interval:
            state.last_full_refresh_turn = state.turn_count
            _L.debug("retrieval gate | session={} decision=full (periodic refresh turn={})",
                     session_id, state.turn_count)
            return await self._full_retrieval(scope, scope_id, group_id, user_id)

        # Gate 3: keyword match
        if conversation_text:
            keywords = extract_keywords(conversation_text)
            if keywords:
                cards = await self._keyword_search(scope, scope_id, keywords)
                if cards:
                    _L.debug("retrieval gate | session={} decision=keyword keywords={!r} matched={}",
                             session_id, keywords, len(cards))
                    return _format_cards(cards, scope, scope_id, group_id, user_id)

        # Gate 4: minimal hint
        total = await self._count_active(scope, scope_id)
        if total > 0:
            label = "用户" if scope == "user" else "群" if scope == "group" else "全局"
            id_part = f" / {scope_id}" if scope != "global" else ""
            _L.debug("retrieval gate | session={} decision=minimal total={}", session_id, total)
            return (
                f"【{label}记忆{id_part}】你有 {total} 张记忆卡片。"
                "使用 lookup_cards 工具按 scope/scope_id 或关键词查询。"
            )
        return ""

    def invalidate_entity(self, scope: str, scope_id: str) -> None:
        """Clear full-retrieval cache for one entity (called after card changes)."""
        key = f"{scope}_{scope_id}"
        if self._full_cache.pop(key, None) is not None:
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

    async def _full_retrieval(
        self, scope: str, scope_id: str, group_id: str | None, user_id: str
    ) -> str:
        """Full entity prompt, cached by entity key."""
        key = f"{scope}_{scope_id}"
        if key in self._full_cache:
            return self._full_cache[key]

        text = await self._store.build_entity_prompt(scope, scope_id)
        if group_id:
            text = f"【当前在群 #{group_id} 中对话】\n{text}"
        elif scope == "user":
            text = f"【当前私聊 @{user_id}】\n{text}"
        self._full_cache[key] = text
        return text

    async def _keyword_search(
        self, scope: str, scope_id: str, keywords: list[str]
    ) -> list:
        """Search cards matching any keyword, scoped to this entity + global."""
        from services.memory.card_store import Card

        seen: set[str] = set()
        results: list[Card] = []

        for kw in keywords:
            batch = await self._store.search_cards(kw, limit=5)
            for c in batch:
                if c.card_id in seen:
                    continue
                if c.scope in (scope, "global") and (c.scope == "global" or c.scope_id == scope_id):
                    seen.add(c.card_id)
                    results.append(c)

        return results[:10]

    async def _count_active(self, scope: str, scope_id: str) -> int:
        cards = await self._store.get_entity_cards(scope, scope_id)
        return len(cards)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _resolve_scope(user_id: str, group_id: str | None) -> tuple[str, str]:
    if group_id:
        return ("group", group_id)
    if user_id:
        return ("user", user_id)
    return ("global", "global")


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
    scope_id: str,
    group_id: str | None,
    user_id: str,
) -> str:
    from services.memory.card_store import CATEGORY_LABELS

    if group_id:
        header = f"【群记忆 / #{scope_id}（关键词匹配）】"
    elif scope == "user":
        header = f"【用户记忆 / @{scope_id}（关键词匹配）】"
    else:
        header = "【全局记忆（关键词匹配）】"

    lines = [header]
    for c in cards:
        cat_label = CATEGORY_LABELS.get(c.category, c.category)
        lines.append(f"[{cat_label}] {c.content}")

    if group_id:
        lines.insert(0, f"【当前在群 #{group_id} 中对话】")

    return "\n".join(lines)
