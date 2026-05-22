"""Tests for the multi-bucket token budget in pack_context_hits.

PR4: replace max_chars character truncation with a token-aware multi-bucket
budget. These tests cover the load-bearing properties:

1. Total token ceiling is respected.
2. Per-bucket caps don't bleed (a flood of doc chunks can't squeeze out the
   one decisive memory or graph hit).
3. buffer_tokens is held back from the global ceiling.
4. Pack order — memory and graph fill before doc, since doc is the
   residual bucket.
5. Legacy max_chars kwarg still works (back-compat for any caller still
   using the old API).
6. Empty inputs return an empty pack without crashing.
"""

from __future__ import annotations

from typing import cast

from services.context.packing import (
    DEFAULT_BUDGET,
    ContextBudget,
    estimate_tokens,
    pack_context_hits,
)
from services.context.types import ContextHit, ContextHitType


def _hit(hit_id: str, hit_type: str, *, content: str, score: float = 1.0) -> ContextHit:
    return ContextHit(
        id=hit_id,
        type=cast(ContextHitType, hit_type),
        content=content,
        score=score,
        source=hit_type,
    )


def test_estimate_tokens_is_monotonic_and_handles_empty() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") >= 1
    assert estimate_tokens("a" * 30) > estimate_tokens("a" * 10)


def test_pack_context_hits_respects_total_token_ceiling() -> None:
    # Each chunk is ~50 chars ≈ 16 tokens with len//3 heuristic. Build 50
    # chunks and cap total at ~80 tokens — only ~5 should survive.
    hits = [_hit(f"d{i}", "doc_chunk", content="x" * 50) for i in range(50)]
    budget = ContextBudget(
        total_tokens=80,
        memory_tokens=80,
        doc_tokens=80,
        graph_tokens=80,
        buffer_tokens=0,
    )
    pack = pack_context_hits(hits, budget=budget)
    selected_tokens = sum(estimate_tokens(_render_line_for_hit(h)) for h in pack.hits)
    assert selected_tokens <= 80
    assert pack.omitted_count > 0
    assert len(pack.hits) < len(hits)


def test_pack_context_hits_per_bucket_cap_blocks_doc_flood() -> None:
    # The exact regression we want to prevent: 20 fat doc chunks shouldn't
    # be able to squeeze out the one critical memory card. The per-bucket
    # doc cap is small; memory has its own cap.
    fat_docs = [_hit(f"d{i}", "doc_chunk", content="x" * 200) for i in range(20)]
    critical_memory = _hit("m1", "memory_card", content="user 偏好简短回复")
    critical_graph = _hit("g1", "graph_fact", content="omubot 由 docker compose 部署")
    budget = ContextBudget(
        total_tokens=400,
        memory_tokens=50,
        doc_tokens=200,
        graph_tokens=50,
        buffer_tokens=0,
    )

    pack = pack_context_hits([*fat_docs, critical_memory, critical_graph], budget=budget)
    pack_ids = {h.id for h in pack.hits}

    assert "m1" in pack_ids, "memory card must survive doc flood"
    assert "g1" in pack_ids, "graph fact must survive doc flood"


def test_pack_context_hits_buffer_tokens_held_back() -> None:
    # buffer_tokens shrinks the effective ceiling. With buffer=100 and
    # total=200, only ~100 tokens of content should make it in.
    hits = [_hit(f"d{i}", "doc_chunk", content="x" * 30) for i in range(30)]
    budget = ContextBudget(
        total_tokens=200,
        memory_tokens=200,
        doc_tokens=200,
        graph_tokens=200,
        buffer_tokens=100,
    )
    pack = pack_context_hits(hits, budget=budget)
    selected_tokens = sum(estimate_tokens(_render_line_for_hit(h)) for h in pack.hits)
    assert selected_tokens <= 100  # total - buffer


def test_pack_context_hits_pack_order_memory_then_graph_then_doc() -> None:
    # When all three buckets compete for a tight global budget, memory and
    # graph fill before doc — doc is the residual bucket. We assert by
    # constructing a budget that fits only memory + graph; doc should be
    # empty or partial.
    memory = _hit("m1", "memory_card", content="aaaaaaaaaaaaaaaaaaaa")
    graph = _hit("g1", "graph_fact", content="bbbbbbbbbbbbbbbbbbbb")
    doc = _hit("d1", "doc_chunk", content="cccccccccccccccccccc")

    # memory + graph render lines fit; total is just enough for both, doc
    # would push us over.
    line_tokens = estimate_tokens("- [memory_card] " + "a" * 20)
    budget = ContextBudget(
        total_tokens=line_tokens * 2,
        memory_tokens=line_tokens * 2,
        doc_tokens=line_tokens * 2,
        graph_tokens=line_tokens * 2,
        buffer_tokens=0,
    )

    pack = pack_context_hits([doc, memory, graph], budget=budget)
    pack_ids = [h.id for h in pack.hits]
    assert "m1" in pack_ids
    assert "g1" in pack_ids
    assert "d1" not in pack_ids


def test_pack_context_hits_legacy_max_chars_kwarg_still_works() -> None:
    # Back-compat: callers passing max_chars instead of budget should keep
    # working. This is the path tests/eval code might still exercise.
    hits = [_hit(f"d{i}", "doc_chunk", content="x" * 30) for i in range(20)]
    pack_legacy = pack_context_hits(hits, max_chars=200)
    assert len(pack_legacy.text) > 0
    # Single-bucket char budget translates to a single-bucket token
    # budget; all hits must fit under the translated ceiling.
    selected_tokens = sum(estimate_tokens(_render_line_for_hit(h)) for h in pack_legacy.hits)
    assert selected_tokens <= estimate_tokens("x" * 200) + 5  # small slack for rounding


def test_pack_context_hits_default_budget_used_when_neither_arg() -> None:
    hits = [_hit(f"d{i}", "doc_chunk", content="x" * 30) for i in range(5)]
    pack = pack_context_hits(hits)
    # 5 hits, all small — should all fit under the 6000-token default budget.
    assert len(pack.hits) == 5
    assert pack.omitted_count == 0


def test_pack_context_hits_empty_returns_empty_pack() -> None:
    pack = pack_context_hits([])
    assert pack.text == ""
    assert pack.hits == []
    assert pack.omitted_count == 0


def test_pack_context_hits_text_renders_grouped_sections() -> None:
    hits = [
        _hit("m1", "memory_card", content="memo content"),
        _hit("d1", "doc_chunk", content="doc content"),
        _hit("g1", "graph_fact", content="graph content"),
    ]
    pack = pack_context_hits(hits, budget=DEFAULT_BUDGET)
    # Sections should be present and in render order: memory, doc, graph
    text = pack.text
    assert "记忆卡片" in text
    assert "文档资料" in text
    assert "关系事实" in text
    assert text.index("记忆卡片") < text.index("文档资料") < text.index("关系事实")


def _render_line_for_hit(hit: ContextHit) -> str:
    title = hit.title or hit.source or hit.id
    return f"- [{title}] {hit.content.strip()}"
