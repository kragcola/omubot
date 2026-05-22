"""Prompt packing helpers for unified context hits.

PR4 (2026-05-22) replaces the original character truncation with a
LightRAG-style multi-bucket *token* budget. Char counts and token counts
diverge violently for CJK content (1 char ≈ 1.3 tokens for cl100k vs.
1 char ≈ 0.3 tokens for ASCII). The legacy `max_chars=2400` would let a
graph_fact line that fits in 200 chars eat 800+ tokens of the prompt
budget while the same byte count of pure ASCII doc would barely register.
Token-aware budgeting also lets us **reserve** capacity per source so a
flood of doc chunks can't squeeze out the one decisive memory card or
graph fact.

Token estimation reuses the project-wide `len(text) // 3` heuristic
(see services/block_trace/budget_manager.py:106) — coarse but monotonic
and identical to what the rest of Omubot uses for prompt accounting.
Switching to tiktoken is a future PR; the bucket structure here is
algorithm-stable across that swap.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from services.context.types import ContextHit, ContextPack

_TYPE_LABELS = {
    "memory_card": "记忆卡片",
    "doc_chunk": "文档资料",
    "graph_fact": "关系事实",
}

# Render order: memory first (cheapest tokens, highest signal-per-token),
# graph next (entity facts), doc last (longest, can absorb leftovers).
_RENDER_ORDER: tuple[str, ...] = ("memory_card", "doc_chunk", "graph_fact")

BucketName = Literal["memory", "doc", "graph"]

# Pack order: which buckets get filled first when the global budget is
# tight. Memory and graph are *cheap and decisive*, so they fill before
# doc. Doc fills last and absorbs any remaining global budget — that
# matches LightRAG's "entities first, relationships second, chunks
# residual" ordering.
_PACK_ORDER: tuple[BucketName, ...] = ("memory", "graph", "doc")


@dataclass(slots=True, frozen=True)
class ContextBudget:
    """Multi-bucket token budget for context packing.

    `total_tokens` is the hard ceiling across all buckets. Each per-bucket
    token cap is a *soft* cap: a bucket may consume less than its cap if
    the global ceiling is reached first, and may NOT exceed its cap even
    if the global ceiling has slack. `buffer_tokens` is held back from
    the global ceiling to leave room for the framing labels we render
    around the hit content.
    """

    total_tokens: int = 6000
    memory_tokens: int = 1500
    doc_tokens: int = 2500
    graph_tokens: int = 1700
    buffer_tokens: int = 300

    def cap_for(self, bucket: BucketName) -> int:
        if bucket == "memory":
            return self.memory_tokens
        if bucket == "doc":
            return self.doc_tokens
        if bucket == "graph":
            return self.graph_tokens
        return 0


DEFAULT_BUDGET = ContextBudget()


def estimate_tokens(text: str) -> int:
    """Project-wide token estimator. CJK-friendly upper bound."""
    if not text:
        return 0
    return max(1, len(text) // 3)


def _bucket_of(hit: ContextHit) -> BucketName:
    if hit.type == "memory_card":
        return "memory"
    if hit.type == "graph_fact":
        return "graph"
    return "doc"


def pack_context_hits(
    hits: list[ContextHit],
    *,
    budget: ContextBudget | None = None,
    max_chars: int | None = None,
) -> ContextPack:
    """Pack hits into a compact, readable prompt block under a token budget.

    Backwards-compat: callers passing `max_chars` get the legacy character
    truncation translated into a single-bucket token budget — preserves
    the old behavior for any caller still using the old kwarg, while
    new callers should pass `budget` directly.
    """
    effective_budget = _resolve_budget(budget, max_chars)
    return _pack_with_budget(hits, effective_budget)


def _resolve_budget(budget: ContextBudget | None, max_chars: int | None) -> ContextBudget:
    if budget is not None:
        return budget
    if max_chars is None:
        return DEFAULT_BUDGET
    # Legacy mode: translate char limit to a single-bucket token budget
    # using the same heuristic. Old callers keep working unchanged.
    legacy_total = estimate_tokens("x" * max_chars)
    return ContextBudget(
        total_tokens=legacy_total,
        memory_tokens=legacy_total,
        doc_tokens=legacy_total,
        graph_tokens=legacy_total,
        buffer_tokens=0,
    )


def _pack_with_budget(hits: list[ContextHit], budget: ContextBudget) -> ContextPack:
    if not hits:
        return ContextPack(text="", hits=[], omitted_count=0)

    buckets: dict[BucketName, list[ContextHit]] = defaultdict(list)
    for hit in hits:
        buckets[_bucket_of(hit)].append(hit)

    used_total = 0
    used_per_bucket: dict[BucketName, int] = defaultdict(int)
    selected: list[ContextHit] = []
    selected_set: set[tuple[str, str]] = set()
    global_ceiling = max(0, budget.total_tokens - budget.buffer_tokens)

    for bucket in _PACK_ORDER:
        bucket_cap = budget.cap_for(bucket)
        for hit in buckets.get(bucket, []):
            line = _render_line(hit)
            cost = estimate_tokens(line)
            if used_per_bucket[bucket] + cost > bucket_cap:
                continue
            if used_total + cost > global_ceiling:
                continue
            key = (hit.type, hit.id)
            if key in selected_set:
                continue
            selected.append(hit)
            selected_set.add(key)
            used_per_bucket[bucket] += cost
            used_total += cost

    text = _render_pack_text(selected)
    return ContextPack(
        text=text,
        hits=selected,
        omitted_count=max(0, len(hits) - len(selected)),
    )


def _render_line(hit: ContextHit) -> str:
    title = hit.title or hit.source or hit.id
    return f"- [{title}] {hit.content.strip()}"


def _render_pack_text(selected: list[ContextHit]) -> str:
    grouped: dict[str, list[str]] = defaultdict(list)
    for hit in selected:
        grouped[hit.type].append(_render_line(hit))
    parts: list[str] = []
    for hit_type in _RENDER_ORDER:
        lines = grouped.get(hit_type)
        if lines:
            parts.append(f"【{_TYPE_LABELS.get(hit_type, hit_type)}】\n" + "\n".join(lines))
    return "\n\n".join(parts)
