"""Offline-only graph retrieval evaluation harness tests.

These tests exercise ``services.context.graph_eval`` as a **synthetic /
offline capability baseline**. They are not production data, not an
official HippoRAG / GraphRAG / public-benchmark parity suite, and must not
be read as a production PPR go/no-go by themselves.

``GraphContextSource`` + ``KnowledgeGraphService`` remain the control path.
This module only scores ordered hits and topology; it does not wire
candidates into production ranking.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from services.context.graph_eval import (
    DEFAULT_GRAPH_EVAL_THRESHOLDS,
    CandidateWindowMetrics,
    GraphCandidateDecision,
    GraphEvalThresholds,
    GraphRunMetrics,
    GraphTopologyStats,
    compute_topology_stats,
    decide_control_vs_candidate,
    score_candidate_window,
    score_graph_hits,
)
from services.context.sources import GraphContextSource
from services.context.types import ContextHit, ContextScoreBreakdown
from services.knowledge_graph import GraphFact, KnowledgeGraphService
from services.memory.entity_identity import entity_ref_from_surface

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hit(
    fact_id: str,
    *,
    content: str = "",
    score: float = 1.0,
    scope: str = "user",
    scope_id: str = "123",
    status: str = "active",
    hop: int = 0,
    subject: str = "",
    object_: str = "",
    subject_entity_key: str = "",
    object_entity_key: str = "",
    confidence: float = 0.9,
) -> ContextHit:
    return ContextHit(
        id=fact_id,
        type="graph_fact",
        content=content or fact_id,
        score=score,
        source="knowledge_graph",
        scope=scope,
        scope_id=scope_id,
        status=status,
        retriever="graph_ngram",
        score_breakdown=ContextScoreBreakdown(
            source_score=score,
            confidence=confidence,
        ),
        metadata={
            "graph_hop": hop,
            "subject": subject,
            "object": object_,
            "subject_entity_key": subject_entity_key,
            "object_entity_key": object_entity_key,
            "confidence": confidence,
            "status": status,
            "scope": scope,
            "scope_id": scope_id,
            "fact_id": fact_id,
        },
    )


def _entity_key(surface: str, *, scope: str = "user", scope_id: str = "123") -> str:
    return entity_ref_from_surface(
        subject=surface,
        scope=scope,
        scope_id=scope_id,
    ).entity_key


async def _add_fact(
    graph: KnowledgeGraphService,
    *,
    subject: str,
    predicate: str,
    object_: str,
    confidence: float,
    scope: str = "user",
    scope_id: str = "123",
    source: str = "graph_eval_fixture",
    evidence_id: str | None = None,
) -> str:
    result = await graph.submit_fact_candidate(
        subject=subject,
        predicate=predicate,
        object=object_,
        confidence=confidence,
        source=source,
        evidence={
            "type": "fixture",
            "id": evidence_id or f"{subject}:{predicate}:{object_}",
            "scope": scope,
            "scope_id": scope_id,
        },
        scope=scope,
        scope_id=scope_id,
        promote_directly=True,
    )
    assert isinstance(result, GraphFact)
    return str(result.fact_id)


# ---------------------------------------------------------------------------
# Pure unit: score_graph_hits
# ---------------------------------------------------------------------------


def test_score_graph_hits_recall_hub_budget_and_safety() -> None:
    hub = _entity_key("中枢节点")
    user = _entity_key("用户123")
    gold = {"g1", "g2"}
    hits = [
        _hit(
            "g1",
            hop=0,
            subject="用户123",
            object_="中枢节点",
            subject_entity_key=user,
            object_entity_key=hub,
        ),
        _hit(
            "g2",
            hop=0,
            subject="用户123",
            object_="中枢节点",
            subject_entity_key=user,
            object_entity_key=hub,
        ),
        _hit(
            "spoke_a",
            hop=1,
            subject="挂靠A",
            object_="中枢节点",
            subject_entity_key=_entity_key("挂靠A"),
            object_entity_key=hub,
        ),
        _hit(
            "spoke_b",
            hop=1,
            subject="挂靠B",
            object_="中枢节点",
            subject_entity_key=_entity_key("挂靠B"),
            object_entity_key=hub,
        ),
        _hit(
            "forbidden_x",
            hop=1,
            subject="挂靠C",
            object_="中枢节点",
            subject_entity_key=_entity_key("挂靠C"),
            object_entity_key=hub,
            status="active",
        ),
        _hit(
            "leak_scope",
            hop=1,
            scope="group",
            scope_id="999",
            subject="群外",
            object_="中枢节点",
            subject_entity_key=_entity_key("群外", scope="group", scope_id="999"),
            object_entity_key=hub,
        ),
    ]

    metrics = score_graph_hits(
        hits,
        gold_fact_ids=gold,
        forbidden_ids={"forbidden_x"},
        allowed_scopes={("user", "123"), ("global", "global")},
        hub_entity_keys={hub},
        top_k=6,
        latency_ms=12.5,
        query_count=1,
        deterministic=True,
    )

    assert isinstance(metrics, GraphRunMetrics)
    assert metrics.recall_at_k == pytest.approx(1.0)
    assert metrics.hit_count == 6
    assert metrics.max_hop == 1
    assert metrics.ordered_ids == (
        "g1",
        "g2",
        "spoke_a",
        "spoke_b",
        "forbidden_x",
        "leak_scope",
    )
    # 4 non-gold hub-incident hits out of 6 returned hits.
    assert metrics.hub_leakage_count == 4
    assert metrics.hub_leakage == pytest.approx(4 / 6)
    # forbidden + out-of-scope
    assert metrics.safety_leakage == 2
    assert metrics.budget_violations == 0
    assert metrics.latency_ms == pytest.approx(12.5)
    assert metrics.query_count == 1
    assert metrics.deterministic is True


def test_score_graph_hits_budget_and_superseded_safety() -> None:
    hits = [
        _hit("a", hop=0, status="active"),
        _hit("b", hop=3, status="superseded"),
        _hit("c", hop=1, status="active"),
    ]
    metrics = score_graph_hits(
        hits,
        gold_fact_ids={"z"},
        forbidden_ids=set(),
        allowed_scopes={("user", "123"), ("global", "global")},
        hub_entity_keys=set(),
        top_k=2,
        latency_ms=1.0,
        query_count=1,
        deterministic=False,
    )
    assert metrics.recall_at_k == pytest.approx(0.0)
    assert metrics.hit_count == 3
    assert metrics.max_hop == 3
    # hit_count > top_k and max_hop > 2
    assert metrics.budget_violations >= 2
    assert metrics.safety_leakage >= 1  # superseded
    assert metrics.deterministic is False


# ---------------------------------------------------------------------------
# Pure unit: decide_control_vs_candidate
# ---------------------------------------------------------------------------


def _metrics(
    *,
    recall: float,
    hub_leakage: float = 0.0,
    safety_leakage: int = 0,
    hit_count: int = 100,
    max_hop: int = 1,
    ordered_ids: tuple[str, ...] | None = None,
    budget_violations: int = 0,
    latency_ms: float = 10.0,
    query_count: int = 1,
    deterministic: bool = True,
    top_k: int | None = None,
) -> GraphRunMetrics:
    # Default top_k to hit_count so existing constructions stay in-budget unless
    # a test intentionally sets hit_count > top_k.
    normalized_top_k = hit_count if top_k is None else int(top_k)
    normalized_ids = (
        tuple(f"hit-{index}" for index in range(max(0, hit_count)))
        if ordered_ids is None
        else ordered_ids
    )
    at_k_count = max(0, min(hit_count, normalized_top_k))
    hub_leakage_count = (
        round(hub_leakage * at_k_count)
        if isinstance(hub_leakage, (int, float))
        and not isinstance(hub_leakage, bool)
        and math.isfinite(hub_leakage)
        else 0
    )
    return GraphRunMetrics(
        recall_at_k=recall,
        hub_leakage=hub_leakage,
        hub_leakage_count=hub_leakage_count,
        safety_leakage=safety_leakage,
        hit_count=hit_count,
        max_hop=max_hop,
        ordered_ids=normalized_ids,
        budget_violations=budget_violations,
        latency_ms=latency_ms,
        query_count=query_count,
        deterministic=deterministic,
        top_k=normalized_top_k,
    )


def test_decide_safe_candidate_passes() -> None:
    control = _metrics(recall=0.40, hub_leakage=0.50, query_count=1)
    candidate = _metrics(
        recall=0.70,  # gain 0.30 >= 0.15
        hub_leakage=0.52,  # delta +0.02 <= 0.05
        safety_leakage=0,
        budget_violations=0,
        query_count=1,
        deterministic=True,
    )
    decision = decide_control_vs_candidate(
        control,
        candidate,
        thresholds=DEFAULT_GRAPH_EVAL_THRESHOLDS,
    )
    assert isinstance(decision, GraphCandidateDecision)
    assert decision.go is True
    assert decision.reasons == ()
    assert decision.recall_gain == pytest.approx(0.30)
    assert decision.hub_leakage_delta == pytest.approx(0.02)
    assert decision.query_cost_ratio == pytest.approx(1.0)


def test_decide_reason_codes_are_stable_for_each_gate() -> None:
    control = _metrics(recall=0.50, hub_leakage=0.10, query_count=1)

    insufficient = decide_control_vs_candidate(
        control,
        _metrics(recall=0.55, hub_leakage=0.10),  # gain 0.05 < 0.15
        thresholds=DEFAULT_GRAPH_EVAL_THRESHOLDS,
    )
    assert insufficient.go is False
    assert "recall_gain_insufficient" in insufficient.reasons

    hub_delta = decide_control_vs_candidate(
        control,
        _metrics(recall=0.80, hub_leakage=0.20),  # delta +0.10 > 0.05
        thresholds=DEFAULT_GRAPH_EVAL_THRESHOLDS,
    )
    assert hub_delta.go is False
    assert "hub_leakage_delta_exceeded" in hub_delta.reasons

    safety = decide_control_vs_candidate(
        control,
        _metrics(recall=0.80, safety_leakage=1),
        thresholds=DEFAULT_GRAPH_EVAL_THRESHOLDS,
    )
    assert safety.go is False
    assert "safety_leakage" in safety.reasons

    budget = decide_control_vs_candidate(
        control,
        _metrics(recall=0.80, budget_violations=1),
        thresholds=DEFAULT_GRAPH_EVAL_THRESHOLDS,
    )
    assert budget.go is False
    assert "budget_violation" in budget.reasons

    det = decide_control_vs_candidate(
        control,
        _metrics(recall=0.80, deterministic=False),
        thresholds=DEFAULT_GRAPH_EVAL_THRESHOLDS,
    )
    assert det.go is False
    assert "determinism_failure" in det.reasons

    cost = decide_control_vs_candidate(
        control,
        _metrics(recall=0.80, query_count=3),  # ratio 3.0 > 2.0
        thresholds=DEFAULT_GRAPH_EVAL_THRESHOLDS,
    )
    assert cost.go is False
    assert "query_cost_ratio_exceeded" in cost.reasons


def test_default_thresholds_are_frozen_gates() -> None:
    t = DEFAULT_GRAPH_EVAL_THRESHOLDS
    assert isinstance(t, GraphEvalThresholds)
    assert t.min_candidate_recall_gain == pytest.approx(0.15)
    assert t.max_hub_leakage_delta == pytest.approx(0.05)
    assert t.max_safety_leakage == 0
    assert t.max_query_cost_ratio == pytest.approx(2.0)
    assert t.max_hop == 2


# ---------------------------------------------------------------------------
# Pure unit: topology + candidate window
# ---------------------------------------------------------------------------


def test_topology_stats_star_clique_and_components() -> None:
    # Star: hub H connected to A,B,C (3 spokes) via undirected simple edges.
    star = [
        {"fact_id": "s1", "subject": "H", "object": "A", "scope": "user", "scope_id": "1"},
        {"fact_id": "s2", "subject": "H", "object": "B", "scope": "user", "scope_id": "1"},
        {"fact_id": "s3", "subject": "H", "object": "C", "scope": "user", "scope_id": "1"},
        # Multi-edge between H-A (multigraph degree uses all endpoints).
        {"fact_id": "s1b", "subject": "A", "object": "H", "scope": "user", "scope_id": "1"},
    ]
    star_stats = compute_topology_stats(star)
    assert isinstance(star_stats, GraphTopologyStats)
    assert star_stats.node_count == 4
    assert star_stats.fact_count == 4
    assert star_stats.simple_edge_count == 3  # undirected unique pairs
    assert star_stats.component_count == 1
    assert star_stats.largest_component_size == 4
    assert star_stats.degree_max >= star_stats.degree_avg
    assert star_stats.degree_p95 >= star_stats.degree_avg

    # Clique K3 + isolated edge component.
    clique = [
        {"fact_id": "c1", "subject": "X", "object": "Y", "scope": "global", "scope_id": "global"},
        {"fact_id": "c2", "subject": "Y", "object": "Z", "scope": "global", "scope_id": "global"},
        {"fact_id": "c3", "subject": "Z", "object": "X", "scope": "global", "scope_id": "global"},
        {"fact_id": "d1", "subject": "P", "object": "Q", "scope": "user", "scope_id": "9"},
    ]
    all_stats = compute_topology_stats(clique)
    assert all_stats.node_count == 5
    assert all_stats.component_count == 2
    assert all_stats.largest_component_size == 3
    assert all_stats.simple_edge_count == 4
    # Density of simple undirected graph: 2E / (N(N-1))
    expected_density = (2 * 4) / (5 * 4)
    assert all_stats.density == pytest.approx(expected_density)

    scoped = compute_topology_stats(
        clique,
        allowed_scopes={("global", "global")},
    )
    assert scoped.node_count == 3
    assert scoped.fact_count == 3
    assert scoped.component_count == 1
    assert scoped.largest_component_size == 3


def test_score_candidate_window_gold_recall_and_scope_counts() -> None:
    window = [
        {"fact_id": "t1", "scope": "user", "scope_id": "123"},
        {"fact_id": "noise1", "scope": "global", "scope_id": "global"},
        {"fact_id": "noise2", "scope": "user", "scope_id": "123"},
    ]
    metrics = score_candidate_window(
        window,
        gold_fact_ids={"t1", "t2"},
    )
    assert isinstance(metrics, CandidateWindowMetrics)
    assert metrics.window_size == 3
    assert metrics.gold_total == 2
    assert metrics.gold_found == 1
    assert metrics.gold_fact_recall == pytest.approx(0.5)
    assert metrics.scope_counts[("user", "123")] == 2
    assert metrics.scope_counts[("global", "global")] == 1


# ---------------------------------------------------------------------------
# Correction regressions (fail-closed gates + topology correctness)
# ---------------------------------------------------------------------------


def test_topology_p95_uses_value_sorted_degrees_not_label_order() -> None:
    """Nearest-rank P95 must sort by degree value, not node-label order.

    Convention (documented in compute_topology_stats / _percentile_nearest_rank):
    nearest-rank percentile on a non-empty ascending degree sequence —
    rank = ceil(p/100 * n), 1-indexed, clamped to [1, n].

    Counterexample: hub labeled ``A`` (lexicographically first) with 9 spokes
    yields label-order degrees ``[9, 1, 1, ...]`` so an unsorted nearest-rank
    at p95 (rank=10 of n=10) incorrectly returns 1; value-sorted degrees
    ``[1]*9 + [9]`` correctly return 9.
    """
    # 9-spoke star, hub "A" first in label order.
    star9 = [
        {
            "fact_id": f"e{i}",
            "subject": "A",
            "object": f"S{i}",
            "scope": "user",
            "scope_id": "1",
        }
        for i in range(9)
    ]
    stats = compute_topology_stats(star9)
    assert stats.node_count == 10
    assert stats.degree_max == 9
    assert stats.degree_avg == pytest.approx(1.8)
    # ceil(0.95 * 10) = 10 → 10th of value-sorted [1]*9+[9] = 9
    assert stats.degree_p95 == pytest.approx(9.0)

    # 20-spoke star with hub "A": nearest-rank p95 is 1 on the true degree
    # multiset [1]*20+[20] (rank ceil(0.95*21)=20). Value-sort must still be
    # applied; result happens to equal 1 only because the multiset is correct.
    star20 = [
        {
            "fact_id": f"e{i}",
            "subject": "A",
            "object": f"T{i:02d}",
            "scope": "user",
            "scope_id": "1",
        }
        for i in range(20)
    ]
    s20 = compute_topology_stats(star20)
    assert s20.node_count == 21
    assert s20.degree_avg == pytest.approx(40 / 21)
    assert s20.degree_max == 20
    assert s20.degree_p95 == pytest.approx(1.0)

    # Distribution where lexical order cannot influence the answer: degrees
    # after value-sort are independent of how node labels sort. Hub "Z" +
    # spokes "A".."I" still yields the same multiset and exact p95.
    star_z = [
        {
            "fact_id": f"z{i}",
            "subject": "Z",
            "object": chr(ord("A") + i),
            "scope": "user",
            "scope_id": "1",
        }
        for i in range(9)
    ]
    stats_z = compute_topology_stats(star_z)
    assert stats_z.degree_p95 == pytest.approx(9.0)
    assert stats_z.degree_max == 9


def test_decide_enforces_thresholds_max_hop_independently() -> None:
    control = _metrics(recall=0.40, hub_leakage=0.50, query_count=1, max_hop=1)
    candidate = _metrics(
        recall=0.70,
        hub_leakage=0.40,
        safety_leakage=0,
        budget_violations=0,  # pre-scored clean, but hop exceeds thresholds
        query_count=1,
        deterministic=True,
        max_hop=3,
    )
    decision = decide_control_vs_candidate(
        control,
        candidate,
        thresholds=DEFAULT_GRAPH_EVAL_THRESHOLDS,
    )
    assert decision.go is False
    assert "budget_violation" in decision.reasons


def test_decide_zero_or_negative_query_counts_fail_closed() -> None:
    control_ok = _metrics(recall=0.40, hub_leakage=0.50, query_count=1)
    both_zero = decide_control_vs_candidate(
        _metrics(recall=0.40, hub_leakage=0.50, query_count=0),
        _metrics(
            recall=0.70,
            hub_leakage=0.52,
            safety_leakage=0,
            budget_violations=0,
            query_count=0,
            deterministic=True,
        ),
        thresholds=DEFAULT_GRAPH_EVAL_THRESHOLDS,
    )
    assert both_zero.go is False
    assert "query_cost_ratio_exceeded" in both_zero.reasons
    assert both_zero.query_cost_ratio == float("inf")

    control_neg = decide_control_vs_candidate(
        _metrics(recall=0.40, hub_leakage=0.50, query_count=-1),
        _metrics(
            recall=0.70,
            hub_leakage=0.52,
            safety_leakage=0,
            budget_violations=0,
            query_count=1,
            deterministic=True,
        ),
        thresholds=DEFAULT_GRAPH_EVAL_THRESHOLDS,
    )
    assert control_neg.go is False
    assert "query_cost_ratio_exceeded" in control_neg.reasons
    assert control_neg.query_cost_ratio == float("inf")

    cand_neg = decide_control_vs_candidate(
        control_ok,
        _metrics(
            recall=0.70,
            hub_leakage=0.52,
            safety_leakage=0,
            budget_violations=0,
            query_count=-2,
            deterministic=True,
        ),
        thresholds=DEFAULT_GRAPH_EVAL_THRESHOLDS,
    )
    assert cand_neg.go is False
    assert "query_cost_ratio_exceeded" in cand_neg.reasons
    assert cand_neg.query_cost_ratio == float("inf")


def test_score_empty_allowed_scopes_is_total_scope_leakage() -> None:
    hits = [
        _hit("a", hop=0, scope="user", scope_id="123"),
        _hit("b", hop=0, scope="global", scope_id="global"),
    ]
    metrics = score_graph_hits(
        hits,
        gold_fact_ids={"a"},
        forbidden_ids=set(),
        allowed_scopes=(),  # empty = no scopes allowed
        hub_entity_keys=set(),
        top_k=10,
        latency_ms=0.0,
        query_count=1,
        deterministic=True,
    )
    assert metrics.safety_leakage == 2


def test_score_invalid_or_negative_graph_hop_is_budget_violation() -> None:
    hits = [
        _hit("ok", hop=0),
        ContextHit(
            id="bad_str",
            type="graph_fact",
            content="bad_str",
            score=1.0,
            source="knowledge_graph",
            scope="user",
            scope_id="123",
            status="active",
            retriever="graph_ngram",
            score_breakdown=ContextScoreBreakdown(
                source_score=1.0,
                confidence=0.9,
            ),
            metadata={"graph_hop": "not-a-number", "subject": "x", "object": "y"},
        ),
        ContextHit(
            id="bad_bool",
            type="graph_fact",
            content="bad_bool",
            score=1.0,
            source="knowledge_graph",
            scope="user",
            scope_id="123",
            status="active",
            retriever="graph_ngram",
            score_breakdown=ContextScoreBreakdown(
                source_score=1.0,
                confidence=0.9,
            ),
            metadata={"graph_hop": True, "subject": "x", "object": "y"},
        ),
        ContextHit(
            id="bad_neg",
            type="graph_fact",
            content="bad_neg",
            score=1.0,
            source="knowledge_graph",
            scope="user",
            scope_id="123",
            status="active",
            retriever="graph_ngram",
            score_breakdown=ContextScoreBreakdown(
                source_score=1.0,
                confidence=0.9,
            ),
            metadata={"graph_hop": -1, "subject": "x", "object": "y"},
        ),
        ContextHit(
            id="bad_float",
            type="graph_fact",
            content="bad_float",
            score=1.0,
            source="knowledge_graph",
            scope="user",
            scope_id="123",
            status="active",
            retriever="graph_ngram",
            score_breakdown=ContextScoreBreakdown(
                source_score=1.0,
                confidence=0.9,
            ),
            metadata={"graph_hop": 1.5, "subject": "x", "object": "y"},
        ),
        ContextHit(
            id="bad_inf",
            type="graph_fact",
            content="bad_inf",
            score=1.0,
            source="knowledge_graph",
            scope="user",
            scope_id="123",
            status="active",
            retriever="graph_ngram",
            score_breakdown=ContextScoreBreakdown(
                source_score=1.0,
                confidence=0.9,
            ),
            metadata={"graph_hop": float("inf"), "subject": "x", "object": "y"},
        ),
    ]
    metrics = score_graph_hits(
        hits,
        gold_fact_ids=set(),
        forbidden_ids=set(),
        allowed_scopes={("user", "123"), ("global", "global")},
        hub_entity_keys=set(),
        top_k=10,
        latency_ms=0.0,
        query_count=1,
        deterministic=True,
        max_hop=2,
    )
    # Invalid hops must fail closed as budget violations (not coerce to 0).
    assert metrics.budget_violations >= 1
    # max_hop reflects an out-of-budget sentinel (above the cap).
    assert metrics.max_hop > 2


def test_topology_cross_scope_nodes_and_self_loop_density() -> None:
    """Identical surfaces in different scopes must not merge nodes/components.

    Production GraphContextSource keys entities by canonical entity keys (or
    scope-qualified surfaces). Topology must do the same so global/user
    "Alice" are distinct.
    """
    cross = [
        {
            "fact_id": "g1",
            "subject": "Alice",
            "object": "Bob",
            "scope": "global",
            "scope_id": "global",
            "subject_entity_key": "global:Alice",
            "object_entity_key": "global:Bob",
        },
        {
            "fact_id": "u1",
            "subject": "Alice",
            "object": "Bob",
            "scope": "user",
            "scope_id": "123",
            "subject_entity_key": "user:123:Alice",
            "object_entity_key": "user:123:Bob",
        },
        # Nested metadata entity keys (production fact shape).
        {
            "fact_id": "u2",
            "subject": "Carol",
            "object": "Dave",
            "scope": "user",
            "scope_id": "123",
            "metadata": {
                "subject_entity_key": "user:123:Carol",
                "object_entity_key": "user:123:Dave",
            },
        },
        # No entity keys: fall back to deterministic scope-qualified surfaces.
        {
            "fact_id": "g2",
            "subject": "Eve",
            "object": "Frank",
            "scope": "global",
            "scope_id": "global",
        },
        {
            "fact_id": "u3",
            "subject": "Eve",
            "object": "Frank",
            "scope": "user",
            "scope_id": "9",
        },
    ]
    stats = compute_topology_stats(cross)
    # 5 edges, 10 distinct endpoints (no cross-scope merge).
    assert stats.fact_count == 5
    assert stats.node_count == 10
    assert stats.component_count == 5
    assert stats.simple_edge_count == 5

    # Self-loop: multigraph degree may count both endpoints, but simple-edge
    # density must not treat (X,X) as a simple undirected pair contribution.
    with_loop = [
        {
            "fact_id": "loop",
            "subject": "X",
            "object": "X",
            "scope": "user",
            "scope_id": "1",
        },
        {
            "fact_id": "edge",
            "subject": "X",
            "object": "Y",
            "scope": "user",
            "scope_id": "1",
        },
    ]
    loop_stats = compute_topology_stats(with_loop)
    assert loop_stats.node_count == 2
    assert loop_stats.fact_count == 2
    # Only the X-Y pair is a simple edge; self-loop excluded from simple edges.
    assert loop_stats.simple_edge_count == 1
    expected_density = (2 * 1) / (2 * 1)
    assert loop_stats.density == pytest.approx(expected_density)
    # Multigraph degree on X counts self-loop endpoints (+2) plus edge (+1) = 3.
    assert loop_stats.degree_max == 3


# ---------------------------------------------------------------------------
# Temp-DB integration: current control exposes star-hub + limit=200 failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hub_aware_expansion_recovers_target_and_bounds_hub_leakage(
    tmp_path: Path,
) -> None:
    """Hub-aware bounded expansion retains a continuing target branch.

    Two user→hub direct seeds open multi-hop expansion. Twenty higher-
    confidence leaf spokes must not crowd out the lower-confidence target
    bridge and its one-step continuation.
    """
    graph = KnowledgeGraphService(tmp_path / "star_hub.db")
    await graph.init()
    try:
        seed_ids: list[str] = []
        seed_ids.append(
            await _add_fact(
                graph,
                subject="用户123",
                predicate="关注主题",
                object_="中枢节点",
                confidence=0.90,
            )
        )
        seed_ids.append(
            await _add_fact(
                graph,
                subject="用户123",
                predicate="收藏资料",
                object_="中枢节点",
                confidence=0.90,
            )
        )
        # Lower-confidence target chain (should lose to high-conf spokes).
        target_bridge = await _add_fact(
            graph,
            subject="中枢节点",
            predicate="桥接",
            object_="目标资料",
            confidence=0.55,
            evidence_id="target-bridge",
        )
        target_summary = await _add_fact(
            graph,
            subject="目标资料",
            predicate="摘要",
            object_="重点结论",
            confidence=0.50,
            evidence_id="target-summary",
        )
        for i in range(20):
            await _add_fact(
                graph,
                subject=f"挂靠实体{i}",
                predicate="挂靠",
                object_="中枢节点",
                confidence=0.99,
                evidence_id=f"spoke-{i}",
            )

        source = GraphContextSource(graph, max_hops=2)
        top_k = 6
        hits = await source.search(
            "用户123关注主题和收藏资料",
            user_id="123",
            top_k=top_k,
        )
        assert len(hits) == top_k
        assert all(int(hit.metadata.get("graph_hop", 0)) <= 2 for hit in hits)

        hub_key = _entity_key("中枢节点")
        gold = {seed_ids[0], seed_ids[1], target_bridge, target_summary}
        metrics = score_graph_hits(
            hits,
            gold_fact_ids=gold,
            forbidden_ids=set(),
            allowed_scopes={("user", "123"), ("global", "global")},
            hub_entity_keys={hub_key},
            top_k=top_k,
            latency_ms=0.0,
            query_count=1,
            deterministic=True,
        )

        hit_ids = {hit.id for hit in hits}
        assert target_bridge in hit_ids
        assert target_summary in hit_ids
        target_recall = score_graph_hits(
            hits,
            gold_fact_ids={target_bridge, target_summary},
            forbidden_ids=set(),
            allowed_scopes={("user", "123"), ("global", "global")},
            hub_entity_keys={hub_key},
            top_k=top_k,
            latency_ms=0.0,
            query_count=1,
            deterministic=True,
        ).recall_at_k
        assert target_recall == pytest.approx(1.0)

        assert metrics.recall_at_k == pytest.approx(1.0)
        assert metrics.hub_leakage_count <= 2
        assert metrics.hub_leakage <= (2 / 6)
        assert metrics.safety_leakage == 0
        assert metrics.budget_violations == 0
        assert metrics.max_hop <= 2
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_scoped_candidate_window_recovers_lower_confidence_user_target(
    tmp_path: Path,
) -> None:
    """The control window misses; scoped production retrieval recovers target.

    200 high-confidence global noise facts fill the list window; one lower-
    confidence user target is absent from the window and from retrieval.
    """
    graph = KnowledgeGraphService(tmp_path / "limit200.db")
    await graph.init()
    try:
        for i in range(200):
            await _add_fact(
                graph,
                subject=f"噪声主体{i}",
                predicate="噪声关系",
                object_=f"噪声客体{i}",
                confidence=0.99,
                scope="global",
                scope_id="global",
                evidence_id=f"noise-{i}",
            )
        target_id = await _add_fact(
            graph,
            subject="用户123",
            predicate="真正偏好",
            object_="稀有目标知识",
            confidence=0.40,
            scope="user",
            scope_id="123",
            evidence_id="user-target",
        )

        window = await graph.list_relationships(limit=200)
        window_metrics = score_candidate_window(
            window,
            gold_fact_ids={target_id},
        )
        assert window_metrics.window_size == 200
        assert window_metrics.gold_fact_recall == pytest.approx(0.0)
        assert all(item.get("fact_id") != target_id for item in window)

        source = GraphContextSource(graph, max_hops=2)
        hits = await source.search(
            "用户123真正偏好稀有目标知识",
            user_id="123",
            top_k=8,
        )
        hit_ids = {hit.id for hit in hits}
        assert target_id in hit_ids

        metrics = score_graph_hits(
            hits,
            gold_fact_ids={target_id},
            forbidden_ids=set(),
            allowed_scopes={("user", "123"), ("global", "global")},
            hub_entity_keys=set(),
            top_k=8,
            latency_ms=0.0,
            query_count=1,
            deterministic=True,
        )
        assert metrics.recall_at_k == pytest.approx(1.0)
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_control_baseline_is_deterministic_and_within_hop_budget(
    tmp_path: Path,
) -> None:
    """Control baseline: same query twice → identical ordered ids; hop<=2.

    Determinism is caller-measured by comparing two ordered-id runs. The pure
    scorer does not infer determinism from a single sequence; callers must
    supply the flag from an actual multi-run comparison.
    """
    graph = KnowledgeGraphService(tmp_path / "baseline.db")
    await graph.init()
    try:
        await _add_fact(
            graph,
            subject="用户123",
            predicate="喜欢",
            object_="音游",
            confidence=0.9,
        )
        await _add_fact(
            graph,
            subject="用户123",
            predicate="常玩",
            object_="音游",
            confidence=0.85,
        )
        await _add_fact(
            graph,
            subject="音游",
            predicate="类型",
            object_="节奏游戏",
            confidence=0.8,
        )

        source = GraphContextSource(graph, max_hops=2)
        query = "用户123喜欢音游吗"
        first = await source.search(query, user_id="123", top_k=5)
        second = await source.search(query, user_id="123", top_k=5)
        ordered_first = [h.id for h in first]
        ordered_second = [h.id for h in second]
        assert ordered_first == ordered_second
        # Derive the scorer flag from the actual two-run comparison (not hardcoded).
        runs_deterministic = ordered_first == ordered_second

        metrics = score_graph_hits(
            first,
            gold_fact_ids={first[0].id} if first else set(),
            forbidden_ids=set(),
            allowed_scopes={("user", "123"), ("global", "global")},
            hub_entity_keys=set(),
            top_k=5,
            latency_ms=1.0,
            query_count=1,
            deterministic=runs_deterministic,
        )
        assert metrics.deterministic is runs_deterministic
        assert metrics.deterministic is True  # two control runs matched
        assert metrics.top_k == 5
        assert metrics.max_hop <= 2
        assert metrics.budget_violations == 0
        assert metrics.hit_count <= 5
    finally:
        await graph.close()


# ---------------------------------------------------------------------------
# Contract corrections (true at-k, distinct unsafe, top_k, canonical hub)
# ---------------------------------------------------------------------------


def test_score_gold_beyond_k_not_counted_in_recall_at_k() -> None:
    """recall_at_k must use ordered top-k only; gold past rank k does not count.

    Oversized dumps still fail closed via hit_count / budget on the full list.
    """
    hits = [
        _hit("noise_a", hop=0),
        _hit("noise_b", hop=0),
        _hit("gold_late", hop=0),  # gold at rank 3, beyond top_k=2
    ]
    metrics = score_graph_hits(
        hits,
        gold_fact_ids={"gold_late"},
        forbidden_ids=set(),
        allowed_scopes={("user", "123"), ("global", "global")},
        hub_entity_keys=set(),
        top_k=2,
        latency_ms=0.0,
        query_count=1,
        deterministic=True,
    )
    assert metrics.recall_at_k == pytest.approx(0.0)
    assert metrics.hit_count == 3  # full list inspected
    assert metrics.top_k == 2
    assert metrics.budget_violations >= 1  # hit_count > top_k
    assert metrics.ordered_ids == ("noise_a", "noise_b", "gold_late")


def test_score_hub_beyond_k_not_counted_in_hub_leakage() -> None:
    """hub_leakage_count/rate use top-k view; hub hits past k are ignored."""
    hub = _entity_key("中枢节点")
    hits = [
        _hit(
            "gold1",
            hop=0,
            subject="用户123",
            object_="中枢节点",
            subject_entity_key=_entity_key("用户123"),
            object_entity_key=hub,
        ),
        _hit(
            "gold2",
            hop=0,
            subject="用户123",
            object_="中枢节点",
            subject_entity_key=_entity_key("用户123"),
            object_entity_key=hub,
        ),
        # Hub-incident non-gold past top_k=2 — must not inflate hub_leakage.
        _hit(
            "spoke_late",
            hop=1,
            subject="挂靠晚",
            object_="中枢节点",
            subject_entity_key=_entity_key("挂靠晚"),
            object_entity_key=hub,
        ),
    ]
    metrics = score_graph_hits(
        hits,
        gold_fact_ids={"gold1", "gold2"},
        forbidden_ids=set(),
        allowed_scopes={("user", "123"), ("global", "global")},
        hub_entity_keys={hub},
        top_k=2,
        latency_ms=0.0,
        query_count=1,
        deterministic=True,
    )
    assert metrics.recall_at_k == pytest.approx(1.0)
    assert metrics.hub_leakage_count == 0
    assert metrics.hub_leakage == pytest.approx(0.0)
    assert metrics.hit_count == 3
    assert metrics.top_k == 2
    assert metrics.budget_violations >= 1


def test_score_triple_category_unsafe_hit_counts_once() -> None:
    """One hit that is forbidden + superseded + out-of-scope counts once."""
    hits = [
        _hit(
            "triple_bad",
            hop=0,
            scope="group",
            scope_id="999",
            status="superseded",
        ),
    ]
    metrics = score_graph_hits(
        hits,
        gold_fact_ids=set(),
        forbidden_ids={"triple_bad"},
        allowed_scopes={("user", "123"), ("global", "global")},
        hub_entity_keys=set(),
        top_k=5,
        latency_ms=0.0,
        query_count=1,
        deterministic=True,
    )
    assert metrics.safety_leakage == 1
    assert DEFAULT_GRAPH_EVAL_THRESHOLDS.max_safety_leakage == 0


def test_decide_hit_count_gt_top_k_budget_even_when_violations_zeroed() -> None:
    """decide must fail budget when candidate.hit_count > candidate.top_k.

    Independent of budget_violations being manually zeroed (same pattern as hop).
    """
    control = _metrics(recall=0.40, hub_leakage=0.50, query_count=1, hit_count=4, top_k=4)
    candidate = _metrics(
        recall=0.70,
        hub_leakage=0.40,
        safety_leakage=0,
        budget_violations=0,  # manually zeroed
        query_count=1,
        deterministic=True,
        hit_count=10,
        top_k=5,
        max_hop=1,
    )
    decision = decide_control_vs_candidate(
        control,
        candidate,
        thresholds=DEFAULT_GRAPH_EVAL_THRESHOLDS,
    )
    assert decision.go is False
    assert "budget_violation" in decision.reasons


def test_score_canonical_hub_fallback_from_surface_metadata() -> None:
    """Missing entity keys: derive via entity_ref_from_surface, match hub keys.

    No free-form content parsing; raw surface hub labels remain supported for
    synthetic callers that pass surface strings as hub_entity_keys.
    """
    hub_surface = "中枢节点"
    hub_key = _entity_key(hub_surface)
    # Hit has only surface metadata — no subject/object entity keys.
    hits = [
        _hit(
            "spoke",
            hop=1,
            subject="挂靠X",
            object_=hub_surface,
            subject_entity_key="",
            object_entity_key="",
        ),
    ]
    # Canonical hub keys must match derived keys from surfaces.
    metrics = score_graph_hits(
        hits,
        gold_fact_ids=set(),
        forbidden_ids=set(),
        allowed_scopes={("user", "123"), ("global", "global")},
        hub_entity_keys={hub_key},
        top_k=5,
        latency_ms=0.0,
        query_count=1,
        deterministic=True,
    )
    assert metrics.hub_leakage_count == 1
    assert metrics.hub_leakage == pytest.approx(1.0)

    # Content must not be free-parsed as hub incidence.
    content_only = ContextHit(
        id="content_only",
        type="graph_fact",
        content=f"挂靠Y --挂靠-> {hub_surface}",
        score=1.0,
        source="knowledge_graph",
        scope="user",
        scope_id="123",
        status="active",
        retriever="graph_ngram",
        score_breakdown=ContextScoreBreakdown(
            source_score=1.0,
            confidence=0.9,
        ),
        metadata={"graph_hop": 1},  # no subject/object surfaces
    )
    content_metrics = score_graph_hits(
        [content_only],
        gold_fact_ids=set(),
        forbidden_ids=set(),
        allowed_scopes={("user", "123"), ("global", "global")},
        hub_entity_keys={hub_key},
        top_k=5,
        latency_ms=0.0,
        query_count=1,
        deterministic=True,
    )
    assert content_metrics.hub_leakage_count == 0

    # Raw surface hub labels still work for synthetic callers.
    surface_hub_metrics = score_graph_hits(
        hits,
        gold_fact_ids=set(),
        forbidden_ids=set(),
        allowed_scopes={("user", "123"), ("global", "global")},
        hub_entity_keys={hub_surface},
        top_k=5,
        latency_ms=0.0,
        query_count=1,
        deterministic=True,
    )
    assert surface_hub_metrics.hub_leakage_count == 1


def test_score_populates_normalized_top_k_on_metrics() -> None:
    metrics = score_graph_hits(
        [_hit("a", hop=0)],
        gold_fact_ids=set(),
        forbidden_ids=set(),
        allowed_scopes={("user", "123")},
        hub_entity_keys=set(),
        top_k=7,
        latency_ms=0.0,
        query_count=1,
        deterministic=True,
    )
    assert metrics.top_k == 7
    # Negative top_k normalizes to 0 for the stored field and budget compare.
    neg = score_graph_hits(
        [_hit("a", hop=0)],
        gold_fact_ids=set(),
        forbidden_ids=set(),
        allowed_scopes={("user", "123")},
        hub_entity_keys=set(),
        top_k=-3,
        latency_ms=0.0,
        query_count=1,
        deterministic=True,
    )
    assert neg.top_k == 0
    assert neg.budget_violations >= 1


@pytest.mark.parametrize(
    ("control_recall", "candidate_recall"),
    [
        pytest.param(float("nan"), 0.8, id="control-nan"),
        pytest.param(0.4, float("nan"), id="candidate-nan"),
        pytest.param(0.4, float("inf"), id="candidate-pos-inf"),
        pytest.param(float("-inf"), 0.8, id="control-neg-inf"),
        pytest.param(1.2, 0.8, id="control-out-of-range"),
        pytest.param(0.4, -0.1, id="candidate-out-of-range"),
    ],
)
def test_decide_non_finite_or_out_of_range_recall_fails_closed(
    control_recall: float,
    candidate_recall: float,
) -> None:
    decision = decide_control_vs_candidate(
        _metrics(recall=control_recall),
        _metrics(recall=candidate_recall),
    )

    assert decision.go is False
    assert "invalid_metrics" in decision.reasons


@pytest.mark.parametrize(
    ("control_hub", "candidate_hub"),
    [
        pytest.param(float("nan"), 0.0, id="control-nan"),
        pytest.param(0.0, float("nan"), id="candidate-nan"),
        pytest.param(0.0, float("-inf"), id="candidate-neg-inf"),
        pytest.param(-0.1, 0.0, id="control-negative"),
        pytest.param(0.0, 1.1, id="candidate-over-one"),
    ],
)
def test_decide_non_finite_or_out_of_range_hub_fails_closed(
    control_hub: float,
    candidate_hub: float,
) -> None:
    decision = decide_control_vs_candidate(
        _metrics(recall=0.4, hub_leakage=control_hub),
        _metrics(recall=0.8, hub_leakage=candidate_hub),
    )

    assert decision.go is False
    assert "invalid_metrics" in decision.reasons


@pytest.mark.parametrize(
    "candidate",
    [
        pytest.param(_metrics(recall=0.8, safety_leakage=-1), id="negative-safety"),
        pytest.param(_metrics(recall=0.8, max_hop=-1), id="negative-hop"),
        pytest.param(
            _metrics(recall=0.8, budget_violations=-1),
            id="negative-budget-violations",
        ),
        pytest.param(_metrics(recall=0.8, hit_count=-1), id="negative-hit-count"),
        pytest.param(_metrics(recall=0.8, top_k=-1), id="negative-top-k"),
    ],
)
def test_decide_negative_manual_metrics_fail_closed(
    candidate: GraphRunMetrics,
) -> None:
    decision = decide_control_vs_candidate(_metrics(recall=0.4), candidate)

    assert decision.go is False
    assert "invalid_metrics" in decision.reasons


def test_decide_non_bool_determinism_and_bool_query_count_fail_closed() -> None:
    string_determinism = decide_control_vs_candidate(
        _metrics(recall=0.4),
        _metrics(recall=0.8, deterministic="false"),  # type: ignore[arg-type]
    )
    bool_query_count = decide_control_vs_candidate(
        _metrics(recall=0.4),
        _metrics(recall=0.8, query_count=True),  # type: ignore[arg-type]
    )

    assert string_determinism.go is False
    assert "invalid_metrics" in string_determinism.reasons
    assert bool_query_count.go is False
    assert "invalid_metrics" in bool_query_count.reasons


@pytest.mark.parametrize(
    "candidate",
    [
        pytest.param(
            _metrics(recall=0.8, hit_count=2, ordered_ids=("only-one",)),
            id="ordered-id-count-mismatch",
        ),
        pytest.param(
            GraphRunMetrics(
                recall_at_k=0.8,
                hub_leakage=1.0,
                hub_leakage_count=3,
                safety_leakage=0,
                hit_count=2,
                max_hop=1,
                ordered_ids=("a", "b"),
                budget_violations=0,
                latency_ms=1.0,
                query_count=1,
                deterministic=True,
                top_k=2,
            ),
            id="hub-count-exceeds-window",
        ),
        pytest.param(
            GraphRunMetrics(
                recall_at_k=0.8,
                hub_leakage=0.0,
                hub_leakage_count=4,
                safety_leakage=0,
                hit_count=4,
                max_hop=1,
                ordered_ids=("a", "b", "c", "d"),
                budget_violations=0,
                latency_ms=1.0,
                query_count=1,
                deterministic=True,
                top_k=4,
            ),
            id="hub-rate-count-mismatch",
        ),
        pytest.param(
            _metrics(recall=0.8, hit_count=0, top_k=5, max_hop=0),
            id="positive-recall-with-empty-window",
        ),
    ],
)
def test_decide_internally_inconsistent_metrics_fail_closed(
    candidate: GraphRunMetrics,
) -> None:
    decision = decide_control_vs_candidate(_metrics(recall=0.4), candidate)

    assert decision.go is False
    assert "invalid_metrics" in decision.reasons


@pytest.mark.parametrize(
    "top_k",
    [
        pytest.param(True, id="bool"),
        pytest.param(1.5, id="fractional"),
        pytest.param(-1, id="negative"),
    ],
)
def test_score_invalid_top_k_fails_closed_without_coercing_to_budget(
    top_k: object,
) -> None:
    metrics = score_graph_hits(
        [_hit("a", hop=0)],
        gold_fact_ids={"a"},
        forbidden_ids=set(),
        allowed_scopes={("user", "123")},
        hub_entity_keys=set(),
        top_k=top_k,  # type: ignore[arg-type]
        latency_ms=0.0,
        query_count=1,
        deterministic=True,
    )

    assert metrics.top_k == 0
    assert metrics.budget_violations >= 1


@pytest.mark.parametrize(
    "thresholds",
    [
        pytest.param(
            GraphEvalThresholds(min_candidate_recall_gain=float("nan")),
            id="nan-recall-gain",
        ),
        pytest.param(
            GraphEvalThresholds(max_hub_leakage_delta=float("inf")),
            id="inf-hub-delta",
        ),
        pytest.param(
            GraphEvalThresholds(max_query_cost_ratio=-1.0),
            id="negative-cost-ratio",
        ),
        pytest.param(
            GraphEvalThresholds(require_deterministic="yes"),  # type: ignore[arg-type]
            id="non-bool-determinism",
        ),
        pytest.param(GraphEvalThresholds(max_hop=3), id="hop-over-production-cap"),
        pytest.param(
            GraphEvalThresholds(max_query_cost_ratio=10**1000),
            id="huge-query-cost-ratio",
        ),
    ],
)
def test_decide_invalid_thresholds_fail_closed(
    thresholds: GraphEvalThresholds,
) -> None:
    decision = decide_control_vs_candidate(
        _metrics(recall=0.4),
        _metrics(recall=0.8),
        thresholds=thresholds,
    )

    assert decision.go is False
    assert "invalid_thresholds" in decision.reasons


def test_score_empty_gold_does_not_claim_perfect_recall() -> None:
    metrics = score_graph_hits(
        [_hit("a", hop=0)],
        gold_fact_ids=set(),
        forbidden_ids=set(),
        allowed_scopes={("user", "123")},
        hub_entity_keys=set(),
        top_k=1,
        latency_ms=0.0,
        query_count=1,
        deterministic=True,
    )

    assert metrics.recall_at_k == 0.0
