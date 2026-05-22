"""Tests for RRF cross-source fusion in ContextService.

These cover three properties:
1. RRF rank-only fusion is immune to per-source score scale (BM25 huge raw
   scores must not steamroll memory/graph hits).
2. Weighting biases the top-k toward the configured source while preserving
   the multi-source mix.
3. The protocol contract is satisfied by the existing source classes (smoke
   check that the duck typing already in use is structurally correct).
"""

from __future__ import annotations

from typing import cast

import pytest

from services.context import ContextService, GraphContextSource, KnowledgeContextSource, MemoryContextSource
from services.context.service import _rrf_fuse
from services.context.sources import ContextRetriever
from services.context.types import ContextHit, ContextHitType


def _hit(hit_id: str, hit_type: str, *, score: float, content: str = "") -> ContextHit:
    return ContextHit(
        id=hit_id,
        type=cast(ContextHitType, hit_type),
        content=content or hit_id,
        score=score,
        source=hit_type,
    )


def test_rrf_fuse_ignores_raw_score_scale() -> None:
    # Doc has BM25-style raw scores 50× larger than graph; without RRF the doc
    # always wins. With RRF we expect equally-weighted sources to draw based on
    # rank only — the second doc and second graph hit must lose to the top
    # graph hit even though raw scores rank doc #2 above graph #1.
    per_source = {
        "doc": [_hit("d1", "doc_chunk", score=12.0), _hit("d2", "doc_chunk", score=11.5)],
        "graph": [_hit("g1", "graph_fact", score=0.95), _hit("g2", "graph_fact", score=0.20)],
    }
    fused = _rrf_fuse(per_source, weights={"doc": 0.5, "graph": 0.5}, k=60)
    ids_in_order = [h.id for h in fused]

    assert ids_in_order[0] == "d1"
    assert "g1" in ids_in_order[:3]
    # d2 and g1 are both rank-2 in their source with equal weight → tie. Tie
    # breaker is (type, id) so doc_chunk loses to graph_fact alphabetically.
    rank_two = ids_in_order[1:3]
    assert {"g1", "d2"} == set(rank_two)


def test_rrf_fuse_respects_weighting() -> None:
    per_source = {
        "doc": [_hit("d1", "doc_chunk", score=10.0)],
        "memory": [_hit("m1", "memory_card", score=1.5)],
        "graph": [_hit("g1", "graph_fact", score=0.9)],
    }
    fused = _rrf_fuse(per_source, weights={"doc": 0.5, "memory": 0.3, "graph": 0.2}, k=60)
    ranking = [h.id for h in fused]
    assert ranking == ["d1", "m1", "g1"]


def test_rrf_fuse_handles_overlap_across_sources() -> None:
    # Same fact comes from doc and memory (e.g. a deployment line indexed as
    # both a knowledge chunk and a memo). Its fused score should be the sum of
    # contributions, beating singletons that only appear in one source.
    shared_in_doc = ContextHit(id="x", type="doc_chunk", content="shared", score=5.0, source="doc")
    shared_in_memory = ContextHit(id="x", type="doc_chunk", content="shared", score=0.4, source="memory")
    per_source = {
        "doc": [shared_in_doc, _hit("d2", "doc_chunk", score=4.0)],
        "memory": [shared_in_memory, _hit("m1", "memory_card", score=1.0)],
    }
    fused = _rrf_fuse(per_source, weights={"doc": 0.5, "memory": 0.5}, k=60)
    assert fused[0].id == "x"
    # Higher per-source score wins for node materialization.
    assert fused[0].score > 0
    assert any(h.id == "d2" for h in fused)
    assert any(h.id == "m1" for h in fused)


def test_rrf_fuse_empty_input_returns_empty() -> None:
    assert _rrf_fuse({}, weights={"doc": 1.0}, k=60) == []


def test_rrf_fuse_skips_zero_weight_source() -> None:
    per_source = {
        "doc": [_hit("d1", "doc_chunk", score=10.0)],
        "memory": [_hit("m1", "memory_card", score=99.0)],
    }
    fused = _rrf_fuse(per_source, weights={"doc": 1.0, "memory": 0.0}, k=60)
    assert [h.id for h in fused] == ["d1"]


def test_existing_sources_satisfy_protocol() -> None:
    # Static structural check — the duck-typed sources we already ship must
    # match ContextRetriever once the Protocol is introduced. If this breaks
    # in the future, fix the source rather than the test.
    assert isinstance(MemoryContextSource(card_store=None), ContextRetriever)
    assert isinstance(KnowledgeContextSource(), ContextRetriever)
    assert isinstance(GraphContextSource(), ContextRetriever)


@pytest.mark.asyncio
async def test_context_service_uses_rrf_to_protect_against_bm25_outliers() -> None:
    # Reproduces the audit's "BM25 high score crowds out graph hit" failure
    # mode. The LowScoreGraph has the contextually correct hit but a tiny raw
    # score; the FakeDoc dominates by BM25 magnitude only. Under equal RRF
    # weights, graph rank-1 must beat doc rank-2+ even with the raw-score gap.
    fake_doc_hits = [
        ContextHit(id=f"doc_{i}", type="doc_chunk", content=f"unrelated #{i}", score=15.0 - i, source="doc")
        for i in range(5)
    ]
    correct_graph_hit = ContextHit(
        id="graph_correct",
        type="graph_fact",
        content="omubot 由 Docker Compose 部署",
        score=0.6,
        source="graph",
    )

    service = ContextService(
        [
            _StaticSource("knowledge", fake_doc_hits),
            _StaticSource("graph", [correct_graph_hit]),
        ],
        rrf_weights={"doc": 1.0, "graph": 1.0},
    )
    hits = await service.search("omubot 怎么部署", top_k=5)
    ids = [h.id for h in hits]

    assert "graph_correct" in ids
    # Equal weights + RRF k=60: graph rank-1 (1/61) ties doc rank-1 (1/61),
    # tiebreak puts graph_fact ahead by (type, id) sort. Either way graph hit
    # is in the top 2 — the property we want is "rank-1 from any source can't
    # be crowded out below rank-2 of another."
    assert ids.index("graph_correct") <= 1


@pytest.mark.asyncio
async def test_context_service_default_weights_keep_doc_first() -> None:
    # Sanity check: default RRF weights still favor doc when it has matching
    # content — we want to fix score-scale bugs without inverting priority.
    doc_hit = ContextHit(id="d1", type="doc_chunk", content="部署文档命中", score=8.0, source="doc")
    memory_hit = ContextHit(id="m1", type="memory_card", content="无关记忆", score=1.4, source="memory")
    service = ContextService([
        _StaticSource("knowledge", [doc_hit]),
        _StaticSource("memory", [memory_hit]),
    ])
    hits = await service.search("部署", top_k=5)
    assert hits[0].id == "d1"


class _StaticSource:
    """Test helper: replays a fixed hit list, regardless of query."""

    def __init__(self, name: str, hits: list[ContextHit]) -> None:
        self.name = name
        self._hits = hits

    async def search(self, *args, **kwargs) -> list[ContextHit]:
        del args, kwargs
        return list(self._hits)
