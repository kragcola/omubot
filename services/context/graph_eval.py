"""Offline-only graph retrieval evaluation harness.

This module is a **synthetic / offline capability baseline** for comparing
control ``GraphContextSource`` runs against experimental graph candidates.
It is **not** production data, **not** an official HippoRAG / GraphRAG /
public-benchmark parity suite, and must **not** be imported from production
``sources.py`` / ``service.py`` ranking paths.

Frozen go/no-go gates (caller-enforced via :func:`decide_control_vs_candidate`):

- safety leakage == 0
- candidate recall gain >= 0.15
- hub leakage delta <= +0.05
- query-cost ratio <= 2.0
- deterministic order
- hit count respects top_k; max hop <= 2
"""

from __future__ import annotations

import contextlib
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeGuard

from services.context.types import ContextHit
from services.memory.entity_identity import entity_ref_from_surface

# Out-of-budget sentinel for invalid / unparseable ``graph_hop`` values.
# Public ``GraphRunMetrics.max_hop`` surfaces this so budgets fail closed
# without changing the dataclass field set.
_INVALID_HOP_SENTINEL = 10**9

# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GraphEvalThresholds:
    """Frozen go/no-go thresholds for control-vs-candidate decisions."""

    min_candidate_recall_gain: float = 0.15
    max_hub_leakage_delta: float = 0.05
    max_safety_leakage: int = 0
    max_query_cost_ratio: float = 2.0
    max_hop: int = 2
    require_deterministic: bool = True
    max_budget_violations: int = 0


DEFAULT_GRAPH_EVAL_THRESHOLDS = GraphEvalThresholds()


@dataclass(frozen=True, slots=True)
class GraphRunMetrics:
    """Per-run metrics for an ordered list of graph ``ContextHit`` results.

    ``top_k`` is the run's normalized non-negative k used for at-k quality
    views (recall / hub leakage). ``hit_count`` is the full returned list
    length so oversized dumps still fail closed on budget.

    ``deterministic`` is **caller-supplied**. Callers must measure it by
    comparing ordered ids across repeated runs; the pure scorer cannot infer
    determinism from a single sequence.
    """

    recall_at_k: float
    hub_leakage: float
    hub_leakage_count: int
    safety_leakage: int
    hit_count: int
    max_hop: int
    ordered_ids: tuple[str, ...]
    budget_violations: int
    latency_ms: float
    query_count: int
    deterministic: bool
    top_k: int


@dataclass(frozen=True, slots=True)
class GraphCandidateDecision:
    """Control-vs-candidate go/no-go with stable reason codes."""

    go: bool
    reasons: tuple[str, ...]
    recall_gain: float
    hub_leakage_delta: float
    query_cost_ratio: float
    control: GraphRunMetrics
    candidate: GraphRunMetrics


@dataclass(frozen=True, slots=True)
class GraphTopologyStats:
    """Undirected multigraph topology over fact dicts (no external deps)."""

    node_count: int
    fact_count: int
    simple_edge_count: int
    density: float
    degree_avg: float
    degree_max: int
    degree_p95: float
    component_count: int
    largest_component_size: int


@dataclass(frozen=True, slots=True)
class CandidateWindowMetrics:
    """Gold recall and scope counts inside an already-fetched fact window."""

    window_size: int
    gold_total: int
    gold_found: int
    gold_fact_recall: float
    scope_counts: Mapping[tuple[str, str], int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pure scorers
# ---------------------------------------------------------------------------


def score_graph_hits(
    hits: Sequence[ContextHit],
    *,
    gold_fact_ids: Iterable[str],
    forbidden_ids: Iterable[str],
    allowed_scopes: Iterable[tuple[str, str]],
    hub_entity_keys: Iterable[str],
    top_k: int,
    latency_ms: float,
    query_count: int,
    deterministic: bool,
    max_hop: int = 2,
) -> GraphRunMetrics:
    """Score ordered graph hits against gold / safety / budget labels.

    Parameters are pure inputs; no I/O.

    **At-k quality** (``recall_at_k``, ``hub_leakage_count`` / rate) is
    computed from the ordered top-k view ``hits[:max(0, top_k)]``. Gold or
    hub leakage past rank k is not counted.

    **Full-list fail-closed** signals still inspect every returned hit:
    ``hit_count``, safety leakage, hop validation, and budget violations
    (oversized dumps, hop cap, invalid hops).

    Safety leakage counts each unsafe hit **once** even if it is
    simultaneously forbidden, superseded, and out-of-scope. Empty
    ``allowed_scopes`` means no scopes are allowed (every hit is scope
    leakage) — there is no implicit bypass.

    ``deterministic`` is caller-supplied (measure via repeated ordered-id
    runs). Latency is observational only (no frozen latency gate here).

    Budget violations count hit_count > top_k, max_hop > max_hop cap, and
    any invalid/negative/non-integer ``graph_hop`` (fail closed).
    """
    gold = {str(x) for x in gold_fact_ids if str(x)}
    forbidden = {str(x) for x in forbidden_ids if str(x)}
    allowed = {(str(s), str(sid)) for s, sid in allowed_scopes}
    hubs = {str(k) for k in hub_entity_keys if str(k)}

    normalized_top_k, invalid_top_k = _normalize_nonnegative_int(top_k)
    ordered_ids = tuple(str(hit.id) for hit in hits)
    hit_count = len(hits)
    # Quality view: ordered top-k only.
    at_k_hits = list(hits[:normalized_top_k])
    at_k_ids = {str(hit.id) for hit in at_k_hits}

    hops = [_hit_hop(hit) for hit in hits]
    invalid_hop_count = sum(1 for hop in hops if hop is None)
    valid_hops = [hop for hop in hops if hop is not None]
    if invalid_hop_count:
        max_hop_observed = _INVALID_HOP_SENTINEL
    elif valid_hops:
        max_hop_observed = max(valid_hops)
    else:
        max_hop_observed = 0

    if not gold:
        recall = 0.0
    else:
        found = sum(1 for fact_id in gold if fact_id in at_k_ids)
        recall = found / len(gold)

    hub_leakage_count = 0
    for hit in at_k_hits:
        fact_id = str(hit.id)
        is_gold = fact_id in gold
        if not is_gold and hubs and _hit_incident_to_hubs(hit, hubs):
            hub_leakage_count += 1

    # Safety: full returned list; each hit contributes at most 1.
    safety_leakage = 0
    for hit in hits:
        fact_id = str(hit.id)
        is_unsafe = False
        if fact_id in forbidden:
            is_unsafe = True
        if str(hit.status or "").lower() == "superseded":
            is_unsafe = True
        scope_pair = (
            str(hit.scope or "global"),
            str(hit.scope_id or "global"),
        )
        # Empty allowed set = no scopes allowed; every hit is leakage.
        if scope_pair not in allowed:
            is_unsafe = True
        if is_unsafe:
            safety_leakage += 1

    # Rate denominator is the at-k window size (not full hit_count).
    at_k_count = len(at_k_hits)
    hub_leakage = (hub_leakage_count / at_k_count) if at_k_count else 0.0

    normalized_query_count, invalid_query_count = _normalize_nonnegative_int(
        query_count
    )
    valid_deterministic = isinstance(deterministic, bool)
    normalized_deterministic = deterministic if valid_deterministic else False
    valid_latency = _finite_number(latency_ms) and float(latency_ms) >= 0.0
    normalized_latency = float(latency_ms) if valid_latency else 0.0

    budget_violations = sum(
        (
            invalid_top_k,
            invalid_query_count,
            not valid_deterministic,
            not valid_latency,
        )
    )
    if hit_count > normalized_top_k:
        budget_violations += 1
    normalized_max_hop, invalid_max_hop = _normalize_nonnegative_int(max_hop)
    if invalid_max_hop:
        budget_violations += 1
    if max_hop_observed > normalized_max_hop:
        budget_violations += 1
    if invalid_hop_count:
        # Count each invalid hop as its own budget violation signal, but the
        # public max_hop field already carries the out-of-budget sentinel.
        budget_violations += invalid_hop_count

    return GraphRunMetrics(
        recall_at_k=float(recall),
        hub_leakage=float(hub_leakage),
        hub_leakage_count=int(hub_leakage_count),
        safety_leakage=int(safety_leakage),
        hit_count=int(hit_count),
        max_hop=int(max_hop_observed),
        ordered_ids=ordered_ids,
        budget_violations=int(budget_violations),
        latency_ms=normalized_latency,
        query_count=normalized_query_count,
        deterministic=normalized_deterministic,
        top_k=int(normalized_top_k),
    )


def decide_control_vs_candidate(
    control: GraphRunMetrics,
    candidate: GraphRunMetrics,
    *,
    thresholds: GraphEvalThresholds = DEFAULT_GRAPH_EVAL_THRESHOLDS,
) -> GraphCandidateDecision:
    """Enforce frozen thresholds; return go=False with stable reason codes.

    Reason codes (stable strings):

    - ``recall_gain_insufficient``
    - ``hub_leakage_delta_exceeded``
    - ``safety_leakage``
    - ``budget_violation``
    - ``determinism_failure``
    - ``query_cost_ratio_exceeded``
    - ``invalid_metrics``
    - ``invalid_thresholds``
    """
    metrics_valid = _graph_metrics_are_valid(control) and _graph_metrics_are_valid(
        candidate
    )
    thresholds_valid = _graph_thresholds_are_valid(thresholds)

    recall_gain = (
        float(candidate.recall_at_k) - float(control.recall_at_k)
        if _unit_interval_number(control.recall_at_k)
        and _unit_interval_number(candidate.recall_at_k)
        else float("-inf")
    )
    hub_delta = (
        float(candidate.hub_leakage) - float(control.hub_leakage)
        if _unit_interval_number(control.hub_leakage)
        and _unit_interval_number(candidate.hub_leakage)
        else float("inf")
    )

    # Fail closed on unmeasured / invalid query cost. Zero or negative counts
    # are not a valid measured ratio; surface as inf with the stable reason.
    if (
        not _is_int(control.query_count)
        or not _is_int(candidate.query_count)
        or control.query_count <= 0
        or candidate.query_count <= 0
    ):
        query_cost_ratio = float("inf")
    else:
        query_cost_ratio = float(candidate.query_count) / float(control.query_count)

    reasons: list[str] = []
    if not metrics_valid:
        reasons.append("invalid_metrics")
    if not thresholds_valid:
        reasons.append("invalid_thresholds")

    # Invalid inputs cannot be evaluated meaningfully. Return conservative
    # derived values without applying malformed thresholds or coercing fields.
    if reasons:
        return GraphCandidateDecision(
            go=False,
            reasons=tuple(reasons),
            recall_gain=recall_gain,
            hub_leakage_delta=hub_delta,
            query_cost_ratio=query_cost_ratio,
            control=control,
            candidate=candidate,
        )

    if recall_gain < float(thresholds.min_candidate_recall_gain):
        reasons.append("recall_gain_insufficient")
    if hub_delta > float(thresholds.max_hub_leakage_delta):
        reasons.append("hub_leakage_delta_exceeded")
    if int(candidate.safety_leakage) > int(thresholds.max_safety_leakage):
        reasons.append("safety_leakage")

    budget_over = int(candidate.budget_violations) > int(
        thresholds.max_budget_violations
    )
    # Independently enforce hop cap even when budget_violations was pre-zeroed.
    hop_over = int(candidate.max_hop) > int(thresholds.max_hop)
    # Independently enforce hit_count <= top_k even when budget_violations
    # was manually zeroed (same fail-closed pattern as hop).
    oversized = int(candidate.hit_count) > int(candidate.top_k)
    if budget_over or hop_over or oversized:
        reasons.append("budget_violation")

    # Candidate-only asymmetry: control nondeterminism is not a blocker.
    if thresholds.require_deterministic and not bool(candidate.deterministic):
        reasons.append("determinism_failure")
    if query_cost_ratio > float(thresholds.max_query_cost_ratio):
        reasons.append("query_cost_ratio_exceeded")

    reason_tuple = tuple(reasons)
    return GraphCandidateDecision(
        go=not reason_tuple,
        reasons=reason_tuple,
        recall_gain=recall_gain,
        hub_leakage_delta=hub_delta,
        query_cost_ratio=query_cost_ratio,
        control=control,
        candidate=candidate,
    )


def compute_topology_stats(
    facts: Sequence[Mapping[str, Any]],
    *,
    allowed_scopes: Iterable[tuple[str, str]] | None = None,
) -> GraphTopologyStats:
    """Pure topology stats over graph fact dicts (optional scope filter).

    Treats each fact as an undirected edge between entity endpoints. Node
    identity prefers canonical ``subject_entity_key`` / ``object_entity_key``
    (top-level or nested metadata); otherwise falls back to a deterministic
    scope-qualified surface so identical surfaces in different scopes do not
    merge. Multigraph degree counts every endpoint occurrence (self-loops
    contribute 2). Simple edges collapse parallel pairs and **exclude**
    self-loops so density is not inflated. Connected components use
    Union-Find. Deterministic and dependency-free (no networkx/scipy).

    Degree percentile convention: nearest-rank on the **value-sorted**
    ascending degree multiset — rank = ceil(p/100 * n), 1-indexed, clamped
    to [1, n]. Sorting by node label is incorrect and must not be used.
    """
    allowed = (
        {(str(s), str(sid)) for s, sid in allowed_scopes}
        if allowed_scopes is not None
        else None
    )

    nodes: set[str] = set()
    multigraph_degree: dict[str, int] = defaultdict(int)
    simple_edges: set[tuple[str, str]] = set()
    parent: dict[str, str] = {}
    fact_count = 0

    def _find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: str, b: str) -> None:
        ra, rb = _find(a), _find(b)
        if ra == rb:
            return
        # Deterministic link: smaller label is root.
        if ra < rb:
            parent[rb] = ra
        else:
            parent[ra] = rb

    for item in facts:
        if allowed is not None:
            scope_pair = (
                str(item.get("scope") or "global"),
                str(item.get("scope_id") or "global"),
            )
            if scope_pair not in allowed:
                continue
        subject_key, object_key = _fact_endpoint_keys(item)
        if not subject_key or not object_key:
            continue
        fact_count += 1
        for node in (subject_key, object_key):
            if node not in parent:
                parent[node] = node
            nodes.add(node)
            multigraph_degree[node] += 1
        if subject_key != object_key:
            a, b = (
                (subject_key, object_key)
                if subject_key <= object_key
                else (object_key, subject_key)
            )
            simple_edges.add((a, b))
        _union(subject_key, object_key)

    node_count = len(nodes)
    simple_edge_count = len(simple_edges)
    density = (
        0.0
        if node_count <= 1
        else (2.0 * simple_edge_count) / (node_count * (node_count - 1))
    )

    if node_count == 0:
        degree_avg = 0.0
        degree_max = 0
        degree_p95 = 0.0
        component_count = 0
        largest = 0
    else:
        # Value-sorted ascending degrees for nearest-rank percentile.
        degrees = sorted(int(multigraph_degree[n]) for n in nodes)
        degree_avg = sum(degrees) / node_count
        degree_max = max(degrees)
        degree_p95 = _percentile_nearest_rank(degrees, 95.0)

        roots: dict[str, int] = defaultdict(int)
        for node in nodes:
            roots[_find(node)] += 1
        component_count = len(roots)
        largest = max(roots.values()) if roots else 0

    return GraphTopologyStats(
        node_count=node_count,
        fact_count=fact_count,
        simple_edge_count=simple_edge_count,
        density=float(density),
        degree_avg=float(degree_avg),
        degree_max=int(degree_max),
        degree_p95=float(degree_p95),
        component_count=int(component_count),
        largest_component_size=int(largest),
    )


def score_candidate_window(
    window: Sequence[Mapping[str, Any]],
    *,
    gold_fact_ids: Iterable[str],
) -> CandidateWindowMetrics:
    """Score gold-fact recall inside an already-fetched relationship window."""
    gold = {str(x) for x in gold_fact_ids if str(x)}
    window_ids: list[str] = []
    scope_counts: dict[tuple[str, str], int] = defaultdict(int)
    for item in window:
        fact_id = str(item.get("fact_id") or "").strip()
        if fact_id:
            window_ids.append(fact_id)
        scope_pair = (
            str(item.get("scope") or "global"),
            str(item.get("scope_id") or "global"),
        )
        scope_counts[scope_pair] += 1

    window_set = set(window_ids)
    gold_found = sum(1 for fact_id in gold if fact_id in window_set)
    gold_total = len(gold)
    recall = (gold_found / gold_total) if gold_total else 0.0

    # Freeze scope_counts as a plain sorted dict for determinism.
    frozen_scopes = {
        key: scope_counts[key]
        for key in sorted(scope_counts.keys())
    }
    return CandidateWindowMetrics(
        window_size=len(window),
        gold_total=gold_total,
        gold_found=gold_found,
        gold_fact_recall=float(recall),
        scope_counts=frozen_scopes,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonnegative_int(value: object) -> TypeGuard[int]:
    return _is_int(value) and value >= 0


def _finite_number(value: object) -> TypeGuard[int | float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _unit_interval_number(value: object) -> bool:
    return _finite_number(value) and 0.0 <= float(value) <= 1.0


def _bounded_delta(value: object) -> bool:
    return _finite_number(value) and -1.0 <= float(value) <= 1.0


def _normalize_nonnegative_int(value: object) -> tuple[int, bool]:
    if not _nonnegative_int(value):
        return 0, True
    return value, False


def _graph_metrics_are_valid(metrics: GraphRunMetrics) -> bool:
    if not _unit_interval_number(metrics.recall_at_k):
        return False
    if not _unit_interval_number(metrics.hub_leakage):
        return False
    for value in (
        metrics.hub_leakage_count,
        metrics.safety_leakage,
        metrics.hit_count,
        metrics.max_hop,
        metrics.budget_violations,
        metrics.top_k,
    ):
        if not _nonnegative_int(value):
            return False
    if not _is_int(metrics.query_count):
        return False
    if not _finite_number(metrics.latency_ms) or float(metrics.latency_ms) < 0.0:
        return False
    if not isinstance(metrics.deterministic, bool):
        return False
    if not isinstance(metrics.ordered_ids, tuple) or not all(
        isinstance(item, str) for item in metrics.ordered_ids
    ):
        return False
    if len(metrics.ordered_ids) != metrics.hit_count:
        return False
    at_k_count = min(metrics.hit_count, metrics.top_k)
    if metrics.hub_leakage_count > at_k_count:
        return False
    expected_hub_leakage = (
        metrics.hub_leakage_count / at_k_count
        if at_k_count
        else 0.0
    )
    if not math.isclose(
        float(metrics.hub_leakage),
        expected_hub_leakage,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        return False
    if at_k_count == 0 and float(metrics.recall_at_k) != 0.0:
        return False
    if metrics.hit_count == 0 and metrics.max_hop != 0:
        return False
    return metrics.safety_leakage <= metrics.hit_count


def _graph_thresholds_are_valid(thresholds: GraphEvalThresholds) -> bool:
    if not _bounded_delta(thresholds.min_candidate_recall_gain):
        return False
    if not _bounded_delta(thresholds.max_hub_leakage_delta):
        return False
    if not _nonnegative_int(thresholds.max_safety_leakage):
        return False
    if (
        not _finite_number(thresholds.max_query_cost_ratio)
        or float(thresholds.max_query_cost_ratio) < 0.0
    ):
        return False
    if not _nonnegative_int(thresholds.max_hop) or thresholds.max_hop > 2:
        return False
    if not isinstance(thresholds.require_deterministic, bool):
        return False
    return _nonnegative_int(thresholds.max_budget_violations)


def _hit_hop(hit: ContextHit) -> int | None:
    """Parse ``graph_hop``; return None for invalid / negative / non-int values.

    ``bool`` is rejected (``True`` would otherwise coerce to 1). Non-integer
    floats (e.g. 1.5) and non-numeric strings fail closed as None.
    """
    raw = hit.metadata.get("graph_hop", 0) if hit.metadata else 0
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, float):
        if not math.isfinite(raw) or raw < 0:
            return None
        as_int = int(raw)
        if float(as_int) != raw:
            return None
        return as_int
    try:
        text = str(raw).strip()
        if not text:
            return None
        # Reject floats-as-strings that are not whole integers.
        if "." in text or "e" in text.lower():
            as_float = float(text)
            if not math.isfinite(as_float) or as_float < 0:
                return None
            as_int = int(as_float)
            if float(as_int) != as_float:
                return None
            return as_int
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _hit_entity_keys(hit: ContextHit) -> set[str]:
    """Collect entity keys from metadata, provenance, or surface fallback.

    When ``subject_entity_key`` / ``object_entity_key`` are missing, derive
    canonical keys via :func:`entity_ref_from_surface` using the hit's
    scope/scope_id. Raw surface labels are also retained so synthetic
    callers that pass surface strings as hub keys still match. Content is
    never free-parsed.
    """
    keys: set[str] = set()
    meta = hit.metadata or {}
    for name in ("subject_entity_key", "object_entity_key"):
        value = str(meta.get(name) or "").strip()
        if value:
            keys.add(value)
    if hit.provenance is not None:
        ek = str(hit.provenance.entity_key or "").strip()
        if ek:
            keys.add(ek)

    scope = str(hit.scope or "global")
    scope_id = str(hit.scope_id or "global")
    for role in ("subject", "object"):
        surface = str(meta.get(role) or "").strip()
        if not surface:
            continue
        # Raw surface retained for synthetic hub-label callers.
        keys.add(surface)
        # Canonical key when metadata entity keys were absent.
        key_name = f"{role}_entity_key"
        if not str(meta.get(key_name) or "").strip():
            with contextlib.suppress(TypeError, ValueError):
                keys.add(
                    entity_ref_from_surface(
                        subject=surface,
                        scope=scope,
                        scope_id=scope_id,
                    ).entity_key
                )
    return keys


def _hit_incident_to_hubs(hit: ContextHit, hubs: set[str]) -> bool:
    if not hubs:
        return False
    # Compare hub keys against metadata entity keys, provenance, and
    # canonical/surface fallbacks — never free-form content parsing.
    return bool(_hit_entity_keys(hit) & hubs)


def _percentile_nearest_rank(sorted_values: Sequence[int], percentile: float) -> float:
    """Nearest-rank percentile on a non-empty **value-sorted ascending** sequence.

    Rank = ceil(p/100 * n), 1-indexed, clamped to [1, n]. Callers must pass
    values sorted by numeric value (not by node label or insertion order).
    """
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    # Nearest rank: ceil(p/100 * n), 1-indexed.
    rank = max(1, min(n, int((percentile / 100.0) * n + 0.999999999)))
    return float(sorted_values[rank - 1])


def _fact_endpoint_keys(item: Mapping[str, Any]) -> tuple[str, str]:
    """Resolve subject/object node ids for topology.

    Prefers non-empty entity keys from top-level or nested metadata; otherwise
    uses a deterministic scope-qualified surface fallback so the same surface
    in different scopes remains a distinct node.
    """
    subject_surface = str(item.get("subject") or "").strip()
    object_surface = str(item.get("object") or "").strip()
    if not subject_surface or not object_surface:
        return "", ""

    scope = str(item.get("scope") or "global")
    scope_id = str(item.get("scope_id") or "global")
    nested = item.get("metadata")
    nested_map: Mapping[str, Any] = nested if isinstance(nested, Mapping) else {}

    def _entity_key(role: str, surface: str) -> str:
        key_name = f"{role}_entity_key"
        for source in (item, nested_map):
            raw = str(source.get(key_name) or "").strip()
            if raw:
                return raw
        return f"{scope}:{scope_id}:{surface}"

    return (
        _entity_key("subject", subject_surface),
        _entity_key("object", object_surface),
    )
