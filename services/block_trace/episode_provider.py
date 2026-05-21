"""EpisodeProvider — D.4 recall path for ``enabled_for_prompt`` episodes.

Pulls top-K episodes whose state is ``enabled_for_prompt`` for the
current group, renders them into one PromptBlockCandidate, and stamps
``last_used_at`` per recalled episode for downstream decay accounting.

Design notes (from
``docs/audits/multilayer-memory-phase-d-design-audit-2026-05-21.md``
§ D.4):

- ``enabled_for_prompt`` is the **only** state that may surface in the
  prompt — invariant enforced by ``EpisodeStore.list_for_recall``
- top-K default = 3, priority **lower** than slang/style so the budget
  manager trims episodes first under pressure
- no new LLM call: matching is left to operator promotion + (future)
  normalizer cluster_id lookups; this provider is pure SQL + string
- ``BlockTraceBus`` double-write is the responsibility of the bus
  itself (it records every PromptBlockCandidate that surfaces); the
  provider only encodes ``evidence_refs=(episode_id,)`` so that
  ``find_by_source_ref(source='episode', source_id=ep_id)`` works
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.block_trace.providers import ContextProvider, QueryContext
from services.block_trace.types import PromptBlockCandidate

if TYPE_CHECKING:
    from services.episodic.store import Episode

_L = logger.bind(channel="provider")

# Lower than slang(40) / style profile(42) / style expressions(45) — the
# budget manager trims by descending priority, so episodes get cut first
# when token pressure hits. This matches Phase D § 4 risk row "episode
# 召回过多导致 prompt 膨胀 → top_k=3 + token 预算硬上限".
_EPISODE_PRIORITY = 50

# Hard cap on chars per recalled episode line — keeps the block bounded
# even when an admin slipped through a long reflection. Total block size
# is therefore at most top_k * this cap.
_PER_EPISODE_CHAR_CAP = 280


def _render_episode_line(ep: Episode) -> str:
    """Render one episode as a single human-facing reflection line.

    Format (from audit § D.4 verbatim): ``曾经在 {situation} 时
    {action_taken}，结果 {outcome_signal}，下次：{reflection}``. Empty
    fields are skipped gracefully — a partially-filled episode still
    produces a usable hint instead of producing "结果 ，下次：".
    """
    situation = (ep.situation or "").strip()
    action = (ep.action_taken or "").strip()
    outcome = (ep.outcome_signal or "").strip()
    reflection = (ep.reflection or "").strip()

    parts: list[str] = []
    if situation:
        parts.append(f"曾经在 {situation} 时")
    if action:
        parts.append(f"{action}")
    elif situation:
        # No action recorded — keep the sentence grammatical
        parts.append("处理过")
    if outcome:
        parts.append(f"结果 {outcome}")
    if reflection:
        parts.append(f"下次：{reflection}")
    elif not parts:
        return ""
    line = "，".join(parts)
    if len(line) > _PER_EPISODE_CHAR_CAP:
        line = line[: _PER_EPISODE_CHAR_CAP - 1] + "…"
    return line


class EpisodeProvider:
    """ContextProvider that pulls ``enabled_for_prompt`` episodes.

    The episode store handle is resolved lazily through ``store_getter``
    so this provider can be registered before EpisodeStore.init() races
    with plugin startup ordering — same pattern as
    ``SlangProvider`` / ``StyleProvider``.
    """

    name = "episode"

    def __init__(
        self,
        store_getter: Callable[[], Any],
        *,
        top_k: int = 3,
        enabled: bool = True,
    ) -> None:
        self._get_store = store_getter
        self._top_k = max(0, int(top_k))
        self._enabled = bool(enabled)

    async def provide(self, ctx: QueryContext) -> list[PromptBlockCandidate]:
        if not self._enabled or not ctx.group_id or self._top_k <= 0:
            return []
        store = self._get_store()
        if store is None:
            return []
        try:
            episodes = await store.list_for_recall(
                group_id=str(ctx.group_id),
                limit=self._top_k,
            )
        except Exception as exc:
            _L.warning("episode recall failed | group={} err={}", ctx.group_id, exc)
            return []
        if not episodes:
            return []

        lines: list[str] = []
        episode_ids: list[str] = []
        for ep in episodes:
            line = _render_episode_line(ep)
            if not line:
                continue
            lines.append(f"- {line}")
            episode_ids.append(ep.episode_id)

        if not lines:
            return []

        block_text = "相关历史反思（从过往同类场景沉淀，仅供参考）：\n" + "\n".join(lines)

        # Stamp last_used_at on every recalled episode — best-effort.
        # Failures here must not block the prompt block from surfacing,
        # so we suppress and log. ``asyncio.gather`` keeps stamping
        # parallel-ish without serializing the recall path.
        try:
            await asyncio.gather(
                *(store.update_last_used(ep_id) for ep_id in episode_ids),
                return_exceptions=True,
            )
        except Exception as exc:
            _L.debug("episode last_used stamp failed | err={}", exc)

        candidate = PromptBlockCandidate(
            candidate_id="pbc_" + secrets.token_hex(6),
            source="episode",
            provider="episode_provider",
            layer="dynamic",
            label="历史反思",
            text=block_text,
            priority=_EPISODE_PRIORITY,
            position="dynamic",
            scope="group",
            group_id=ctx.group_id or "",
            hit_reason="episode_recall_enabled_for_prompt",
            char_count=len(block_text),
            evidence_refs=tuple(episode_ids),
            metadata={"episode_count": len(episode_ids)},
        )
        return [candidate]


assert isinstance(EpisodeProvider(store_getter=lambda: None), ContextProvider)
