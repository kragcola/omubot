"""EpisodeProvider — D.4 recall path for ``enabled_for_prompt`` episodes.

Pulls a bounded candidate pool of episodes whose state is
``enabled_for_prompt`` for the current group (and not past ``decay_at``
under the store default eligibility contract), applies register filter,
reranks by deterministic ngram relevance against the query conversation
text, selects top-K, renders one PromptBlockCandidate, and stamps
``last_used_at`` per *selected* episode for downstream decay accounting.

Design notes (from
``docs/audits/multilayer-memory-phase-d-design-audit-2026-05-21.md``
§ D.4 + Memory Episode v2 decay/rerank slice):

- ``enabled_for_prompt`` is the **only** state that may surface in the
  prompt — invariant enforced by ``EpisodeStore.list_for_recall``
- top-K default = 3, priority **lower** than slang/style so the budget
  manager trims episodes first under pressure
- candidate fetch is hard-capped (``_CANDIDATE_POOL_CAP``); final output
  is hard-capped at ``top_k``
- query-conditioned rerank is stable: relevance DESC only so ties keep
  store order (confidence DESC / updated_at DESC). Empty query → no
  reordering. No relevance threshold (does not suppress all fallback).
- ``BlockTraceBus`` double-write is the responsibility of the bus
  itself; the provider encodes ``evidence_refs`` for selected ids only
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.block_trace.providers import ContextProvider, QueryContext
from services.block_trace.types import PromptBlockCandidate
from services.humanization import REGISTER_LABEL_SLOT
from services.memory.linked_refs import linked_ref_evidence
from services.similarity import create_similarity_provider
from services.system_module import Scope

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

# Hard cap on how many eligible episodes we pull from the store before
# register filter + query-conditioned rerank. Final selection remains top_k.
# Fetch sizing: min(CAP, max(top_k, top_k * 3)) so small top_k does not
# always pull the full pool while large top_k stays hard-capped.
_CANDIDATE_POOL_CAP = 24

# Total character budget for the ngram composite document (all rerank
# fields joined). Field order is fixed; overflow truncates the joined
# string at this cap (no partial-field rebalancing).
_COMPOSITE_CHAR_CAP = 1200

# Fields composed into the ngram scoring document (bounded composite).
_RERANK_FIELDS = (
    "situation",
    "observed_context",
    "action_taken",
    "outcome_signal",
    "reflection",
)

_SIMILARITY = create_similarity_provider("ngram")


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


def _episode_composite_text(ep: Any) -> str:
    """Bounded composite document for ngram relevance scoring.

    Fields are joined in ``_RERANK_FIELDS`` order with ``\\n``. Empty
    fields are skipped. The joined document is hard-truncated at
    ``_COMPOSITE_CHAR_CAP`` (default 1200) so a single long reflection
    cannot dominate scoring cost or inflate similarity inputs.
    """
    chunks: list[str] = []
    for field in _RERANK_FIELDS:
        raw = getattr(ep, field, "") or ""
        text = str(raw).strip()
        if text:
            chunks.append(text)
    joined = "\n".join(chunks)
    if len(joined) > _COMPOSITE_CHAR_CAP:
        return joined[:_COMPOSITE_CHAR_CAP]
    return joined


def _rerank_by_query(episodes: list[Any], query_text: str) -> list[Any]:
    """Stable relevance DESC sort; ties preserve store order.

    Empty or normalization-empty query leaves ``episodes`` order unchanged
    (store: confidence DESC, updated_at DESC).
    """
    query = (query_text or "").strip()
    if not query:
        return list(episodes)
    # Probe normalize emptiness via similarity self-score path: empty keys
    # yield 0.0 for any right side when left normalizes empty.
    if _SIMILARITY.similarity(query, query) <= 0.0:
        return list(episodes)

    scored: list[tuple[float, int, Any]] = []
    for index, ep in enumerate(episodes):
        score = _SIMILARITY.similarity(query, _episode_composite_text(ep))
        scored.append((score, index, ep))
    # relevance DESC only; index ASC preserves store order on ties
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [ep for _, _, ep in scored]


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
        register_label = _read_register_label(ctx)
        # Bounded candidate pool: want headroom for register filter + rerank
        # (about 3× top_k) but never below top_k and never above the hard cap.
        # top_k=1 → fetch 3; top_k=10 → fetch 24 (cap); top_k=100 → still 24.
        fetch_limit = min(
            _CANDIDATE_POOL_CAP,
            max(self._top_k, self._top_k * 3),
        )
        try:
            episodes = await store.list_for_recall(
                group_id=str(ctx.group_id),
                limit=fetch_limit,
            )
        except Exception as exc:
            _L.warning("episode recall failed | group={} err={}", ctx.group_id, exc)
            return []
        if not episodes:
            return []

        # 1) Register filter on the full candidate pool (before top_k).
        register_matched = [
            ep for ep in episodes if _episode_matches_register(ep, register_label)
        ]
        if not register_matched:
            return []

        # 2) Query-conditioned stable rerank (ties keep store order).
        ranked = _rerank_by_query(register_matched, ctx.conversation_text or "")

        # 3) Final top_k selection among renderable lines.
        lines: list[str] = []
        episode_ids: list[str] = []
        selected_episodes: list[Any] = []
        for ep in ranked:
            line = _render_episode_line(ep)
            if not line:
                continue
            lines.append(f"- {line}")
            episode_ids.append(ep.episode_id)
            selected_episodes.append(ep)
            if len(lines) >= self._top_k:
                break

        if not lines:
            return []

        block_text = "相关历史反思（从过往同类场景沉淀，仅供参考）：\n" + "\n".join(lines)

        # Typed linked evidence for selected episodes only (after episode ids).
        typed_evidence: list[str] = []
        seen_typed: set[str] = set()
        for ep in selected_episodes:
            linked_raw = getattr(ep, "linked_memory_ids", None)
            if not linked_raw:
                linked_raw = getattr(ep, "linked_memory_refs", None)
            for ref in linked_ref_evidence(linked_raw):
                if ref in seen_typed:
                    continue
                seen_typed.add(ref)
                typed_evidence.append(ref)

        evidence_refs = tuple(episode_ids) + tuple(typed_evidence)

        # Stamp last_used_at on every *selected* episode — best-effort.
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
            evidence_refs=evidence_refs,
            metadata={
                "episode_count": len(episode_ids),
                "register_label": register_label,
                "typed_evidence_count": len(typed_evidence),
                "typed_evidence_refs": list(typed_evidence),
            },
        )
        return [candidate]


def _read_register_label(ctx: QueryContext) -> str:
    if ctx.runtime_state is None:
        return "neutral"
    try:
        snapshot = ctx.runtime_state.get(
            REGISTER_LABEL_SLOT,
            scope=Scope(session_id=ctx.session_id, group_id=ctx.group_id, user_id=ctx.user_id),
        )
    except Exception:
        return "neutral"
    value = getattr(snapshot, "value", None)
    if not isinstance(value, dict):
        return "neutral"
    label = str(value.get("label", "")).strip().lower()
    return label or "neutral"


def _episode_matches_register(ep: Episode, register_label: str) -> bool:
    meta = getattr(ep, "meta", None)
    if not isinstance(meta, dict):
        return True
    allowed = _meta_labels(meta, "register_labels", "allowed_registers", "target_registers")
    blocked = _meta_labels(meta, "avoid_register_labels", "blocked_registers")
    label = (register_label or "neutral").strip().lower()
    if blocked and label in blocked:
        return False
    return not (allowed and label not in allowed)


def _meta_labels(meta: dict[str, Any], *keys: str) -> set[str]:
    labels: set[str] = set()
    for key in keys:
        raw = meta.get(key)
        if isinstance(raw, str):
            labels.update(part.strip().lower() for part in raw.split(",") if part.strip())
        elif isinstance(raw, (list, tuple, set)):
            labels.update(str(part).strip().lower() for part in raw if str(part).strip())
    return labels


assert isinstance(EpisodeProvider(store_getter=lambda: None), ContextProvider)
