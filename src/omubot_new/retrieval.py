"""Authorized Memory/Markdown retrieval with bounded typed evidence packs.

Optional KnowledgeService shares the same Store/Policy. Each owner ranks its
own source; mixed cold selection uses RRF and exact dispatch-time provenance
checks. Without that owner, the existing fact/ordinary behavior is preserved.
Documents are data, never personal consent or instructions.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from time import time
from types import MappingProxyType
from typing import Literal, cast
from zoneinfo import ZoneInfo

from .graph import GraphProjection, GraphRelation, GraphService, SelfFactGraphRelation
from .knowledge import KnowledgeHit, KnowledgeService, SharedKnowledgeHit
from .memory import (
    MemoryCard,
    MemoryCardQueryResult,
    MemoryFact,
    MemoryFactPointer,
    MemoryService,
    MemoryTemporalTrace,
)
from .store import StoreConnection
from .types import OperationError, Scope

RetrieveMode = Literal["skip", "doc", "fact", "hybrid"]
QueryNeed = Literal[
    "ordinary_fact",
    "preference",
    "temporal_current",
    "temporal_earlier",
    "premise_check",
    "relation_multihop",
    "broad_recall",
    "doc_grounding",
]
_NEED_PRIORITY: tuple[QueryNeed, ...] = (
    "premise_check",
    "temporal_earlier",
    "temporal_current",
    "preference",
    "relation_multihop",
    "doc_grounding",
    "broad_recall",
    "ordinary_fact",
)
_QUERY_PLAN_VERSION = "qa_rg_v1"
_NEED_MARKERS: Mapping[QueryNeed, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "preference": (("喜欢", "偏好", "爱吃", "讨厌", "不喜欢", "口味"), ("prefer", "favorite", "favourite")),
    "temporal_earlier": (
        ("以前", "之前", "曾经", "原来", "当时", "过去", "从前", "往年"),
        ("earlier", "before", "used to", "previously"),
    ),
    "temporal_current": (("现在", "如今", "此刻"), ("now", "currently", "today")),
    "premise_check": (("还记得", "明明", "我记得", "不是还"), ()),
    "relation_multihop": (
        ("关系", "同事", "之间", "和谁", "谁和", "关联", "多跳"),
        ("relation", "relationship", "connected", "knows"),
    ),
    "doc_grounding": (
        ("手册", "文档", "说明书", "文档里", "按文档", "部署手册", "文档资料", "手册里"),
        ("according to", "documentation", "manual", "readme", "wiki"),
    ),
    "broad_recall": (
        ("都有哪些", "有哪些", "所有记忆", "关于我的记忆", "汇总", "回忆一下", "记得我"),
        ("everything about", "all my", "what do you know"),
    ),
}
_PROFILE_RATIOS: Mapping[QueryNeed, tuple[int, int, int]] = {
    "preference": (60, 15, 25),
    "temporal_current": (60, 10, 30),
    "temporal_earlier": (60, 10, 30),
    "premise_check": (65, 10, 25),
    "relation_multihop": (25, 10, 65),
    "doc_grounding": (15, 70, 15),
}
_PROFILE_IDS: Mapping[QueryNeed, str] = {
    "ordinary_fact": "ordinary_identity",
    "preference": "preference_memory_v1",
    "temporal_current": "temporal_current_memory_v1",
    "temporal_earlier": "temporal_earlier_memory_v1",
    "premise_check": "premise_check_memory_v1",
    "relation_multihop": "relation_graph_v1",
    "doc_grounding": "doc_grounding_v1",
    "broad_recall": "broad_recall_clamp_v1",
}
_TYPE_BUCKETS: Mapping[str, str] = {
    "memory_fact": "memory",
    "memory_card": "memory",
    "document": "doc",
    "graph_fact": "graph",
}
_BUCKETS = ("memory", "doc", "graph")
PackState = Literal[
    "skip",
    "empty",
    "hint_only",
    "nonempty",
    "demote_present",
    "omit_only",
]

_RETRIEVE_MODES = frozenset({"skip", "doc", "fact", "hybrid"})
_PACK_STATES = frozenset({"skip", "empty", "hint_only", "nonempty", "demote_present", "omit_only"})
_MAX_HITS = 64
_MAX_CHARS = 32_000
_DEFAULT_HITS = 8
_DEFAULT_CHARS = 4_096
_DEFAULT_HOT_CHARS = 1_024
_MAX_HOT_HITS = 12
_MAX_HOT_CHARS = 4_096
_MAX_QUERY_TOKENS = 32
_MAX_QUERY_TOKEN_CHARS = 256
_ALGORITHM_VERSION = "n6.fact-retrieval.v2"
_DOCUMENT_UNAVAILABLE = "document_source_not_connected"
_TRACE_HISTORY_INTENT = re.compile(
    r"(?:之前|以前|先前|刚才|上次).{0,24}(?:说过|提过|讲过|聊过|告诉过|答应过)"
    r"|(?:还记得|记得).{0,16}(?:之前|以前|先前|刚才|上次).{0,12}"
    r"(?:说过|提过|讲过|聊过)"
    r"|(?:不是|难道|没有|没).{0,12}(?:之前|以前|刚才|上次)?"
    r"(?:说过|提过|讲过|聊过)",
)
_QUERY_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CJK_CHAR_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\U00020000-\U0002fa1f\U00030000-\U000323af]"
)


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    """Pure N3 plan metadata; no storage or model call is performed."""

    plan_id: str
    query_digest: str
    application_scope: Scope
    policy_revision: int | None
    visibility_revision: int | None
    needs: tuple[str, ...]
    source_kinds: tuple[str, ...]
    retrieve_mode: RetrieveMode
    type_caps: Mapping[str, int]
    total_budget: int
    algorithm_version: str
    reason_codes: tuple[str, ...]
    query_profile: str = "ordinary_identity"
    bucket_budgets: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    query_planner_enabled: bool = False
    planner_identity: bool = True

    def to_dict(self) -> dict[str, object]:
        """Closed, body-free planner metadata with fresh nested containers."""

        return {
            "version": _QUERY_PLAN_VERSION,
            "needs": list(self.needs),
            "profile_id": self.query_profile,
            "mode": self.retrieve_mode,
            "type_caps": dict(self.type_caps),
            "bucket_budgets": dict(self.bucket_budgets),
            "total_budget": self.total_budget,
            "reason_codes": list(self.reason_codes),
            "enabled": self.query_planner_enabled,
            "identity": self.planner_identity,
        }


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    """One active, source-backed fact selected for the bounded pack.

    ``score`` is only a deterministic lexical ordering score.  It is never
    treated as confidence and cannot bypass the evidence checks.
    """

    object_id: str
    domain: Literal["memory_fact"]
    fact_revision: int
    subject_id: str
    predicate: str
    value: str
    source_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    source_scope: Scope
    visibility_scope: Scope
    state: Literal["active"]
    conflict_state: Literal["none"]
    score: float
    rank: int
    reason_code: str
    card_projection: MemoryCard | None = None


@dataclass(frozen=True, slots=True)
class SourceRank:
    source_kind: Literal["memory_fact", "memory_card", "document", "graph_fact"]
    object_id: str
    source_rank: int
    rrf_score: float


def _document_chars(hit: KnowledgeHit) -> int:
    return sum(
        len(str(value))
        for value in (
            hit.body,
            hit.title,
            hit.source_label,
            hit.source_id,
            hit.chunk_id,
            hit.source_hash,
            hit.index_version,
            hit.uploader_id,
            hit.source_revision,
            hit.content_revision,
            hit.position.ordinal,
            hit.position.start_char,
            hit.position.end_char,
            hit.position.start_line,
            hit.position.end_line,
        )
    )


def _graph_chars(hit: GraphRelation | SelfFactGraphRelation, path: tuple[str, ...]) -> int:
    return sum(len(value) for value in (
        hit.relation_id, str(hit.revision), hit.subject_id, hit.predicate, hit.target_id, *path,
    )) + len(hit.source.model_dump_json())


def _graph_matches(hit: GraphRelation | SelfFactGraphRelation, value: str) -> bool:
    if value.casefold() in f"{hit.subject_id} {hit.predicate} {hit.target_id}".casefold():
        return True
    if isinstance(hit, SelfFactGraphRelation):
        fact = hit.source.fact
        return value in (fact.fact_id, fact.subject_id, *fact.source_ids, *fact.evidence_refs)
    return value in (hit.source.source_id, hit.source.chunk_id)


def _document_matches(hit: KnowledgeHit, value: str) -> bool:
    needle = value.casefold()
    return needle in f"{hit.title} {hit.body}".casefold() or needle in {
        hit.source_id.casefold(),
        hit.chunk_id.casefold(),
        hit.source_label.casefold(),
    }


def _fuse_documents(
    plan: RetrievalPlan,
    cold: RetrievalResult,
    documents: Sequence[KnowledgeHit],
    *,
    required: Sequence[str],
    forbidden: Sequence[str],
    already_used: int = 0,
    graph: GraphProjection | None = None,
    hot_hits: Sequence[RetrievalHit] = (),
    shared_documents: Sequence[SharedKnowledgeHit] = (),
) -> RetrievalResult:
    # Each owner establishes its own rank. Heterogeneous lexical/BM25 scores
    # are never compared; RRF uses only those source ranks (k=60).
    memory_ranks = {pointer.object_id: pointer for pointer in cold.combined_rank}
    ranked: list[tuple[SourceRank, RetrievalHit | KnowledgeHit | GraphRelation]] = [
        (memory_ranks.get(hit.object_id, SourceRank("memory_fact", hit.object_id, rank,
                                                   1 / (60 + rank))), hit)
        for rank, hit in enumerate(cold.hits, 1)
    ]
    ranked.extend(
        (SourceRank(
            "document",
            ":".join((hit.scope.bot_id, hit.scope.group_id, hit.source_id, hit.chunk_id,
                      str(hit.content_revision), hit.index_version))
            if any(shared.hit == hit for shared in shared_documents) else hit.chunk_id,
            rank, 1 / (60 + rank),
        ), hit)
        for rank, hit in enumerate(documents, 1)
        if not any(_document_matches(hit, value) for value in forbidden)
    )
    graph_paths = {} if graph is None else dict(zip(
        (item.relation_id for item in graph.relations), graph.paths, strict=True,
    ))
    personal: dict[tuple[str, int], list[SelfFactGraphRelation]] = {}
    if graph is not None:
        hot_primary = {(hit.object_id, hit.fact_revision) for hit in hot_hits}
        for edge in graph.relations:
            if isinstance(edge, SelfFactGraphRelation):
                primary = (edge.source.fact.fact_id, edge.source.fact.fact_revision)
                if primary not in hot_primary and not any(_graph_matches(edge, value) for value in forbidden):
                    personal.setdefault(primary, []).append(edge)
        memory_values = {(hit.subject_id, hit.predicate, hit.value) for hit in (*hot_hits, *cold.hits)}
        ranked.extend(
            (SourceRank("graph_fact", hit.relation_id, rank, 1 / (60 + rank)), hit)
            for rank, hit in enumerate(
                (edge for edge in graph.relations if isinstance(edge, GraphRelation)), 1,
            )
            if (hit.subject_id, hit.predicate, hit.target_id) not in memory_values
            and not any(_graph_matches(hit, value) for value in forbidden)
        )
    ranked.sort(
        key=lambda pair: (-pair[0].rrf_score, pair[0].source_kind == "document", pair[0].object_id)
    )
    facts: list[RetrievalHit] = []
    docs: list[KnowledgeHit] = []
    selected_shared: list[SharedKnowledgeHit] = []
    order: list[SourceRank] = []
    used = 0
    doc_used = 0
    doc_budget = retrieval_bucket_budget(plan, "document")
    graph_budget = retrieval_bucket_budget(plan, "graph_fact")
    graph_used = 0
    edges: list[GraphRelation | SelfFactGraphRelation] = []
    alias_cost = 0 if graph is None else sum(
        len(item.alias_id) + len(item.alias) + len(item.entity_id) + len(str(item.revision))
        + len(item.source.model_dump_json()) for item in graph.aliases
    )
    for pointer, hit in ranked:
        remaining = plan.total_budget - already_used - used
        if isinstance(hit, KnowledgeHit):
            frozen_shared = tuple(shared for shared in shared_documents if shared.hit == hit)
            available = min(remaining, doc_budget - doc_used)
            length = min(len(hit.body), available - (_document_chars(hit) - len(hit.body)))
            if length <= 0:
                continue
            body = hit.body[:length]
            end_line = hit.position.start_line + sum(
                match.end() < length
                for match in re.finditer(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]", hit.body)
            )
            hit = replace(
                hit,
                body=body,
                position=replace(
                    hit.position,
                    end_char=hit.position.start_char + length,
                    end_line=end_line,
                ),
            )
            cost = _document_chars(hit)
            assert cost <= available
            docs.append(hit)
            selected_shared.extend(replace(shared, hit=hit) for shared in frozen_shared)
            doc_used += cost
        elif isinstance(hit, GraphRelation):
            if len(edges) >= plan.type_caps.get("graph_fact", 0):
                continue
            path = graph_paths[hit.relation_id]
            # Every path edge retained in this pack has its own exact proof.
            if any(identity not in {edge.relation_id for edge in edges} for identity in path[:-1]):
                continue
            cost = _graph_chars(hit, path) + (alias_cost if not edges else 0)
            if cost > remaining or graph_used + cost > graph_budget:
                continue
            edges.append(hit)
            graph_used += cost
        else:
            cost = _hit_chars(hit)
            if cost > remaining:
                continue
            facts.append(replace(hit, rank=len(facts) + 1))
            # A self edge is a view of this exact primary, not another ranked
            # evidence item. Keep the original value and charge its attachment
            # to the existing graph bucket and this candidate's total cost.
            for edge in personal.get((hit.object_id, hit.fact_revision), ()):
                if len(edges) >= plan.type_caps.get("graph_fact", 0):
                    break
                edge_cost = (
                    _graph_chars(edge, graph_paths[edge.relation_id]) + (alias_cost if not edges else 0)
                )
                if cost + edge_cost > remaining or graph_used + edge_cost > graph_budget:
                    continue
                edges.append(edge)
                graph_used += edge_cost
                cost += edge_cost
        used += cost
        order.append(pointer)
    missing = tuple(
        _safe_diagnostic("required", index)
        for index, value in enumerate(required)
        if not any(
            value.casefold() in f"{hit.subject_id} {hit.predicate} {hit.value}".casefold()
            or value in (*hit.source_refs, *hit.evidence_refs)
            for hit in facts
        )
        and not any(_document_matches(hit, value) for hit in docs)
        and not any(_graph_matches(hit, value) for hit in edges)
    )
    if missing:
        facts, docs, edges, order, used = [], [], [], [], 0
        selected_shared = []
    diagnostics = tuple(
        code for code in cold.no_evidence if code not in {_DOCUMENT_UNAVAILABLE, "required_missing"}
    )
    if missing:
        diagnostics += ("required_missing",)
    if not documents:
        diagnostics += ("document_miss",)
    if "relation_multihop" in plan.needs and graph is None:
        diagnostics += (
            ("graph_miss",) if "graph_fact" in plan.source_kinds else ("graph_source_not_connected",)
        )
    return replace(
        cold,
        hits=tuple(facts),
        documents=tuple(docs),
        shared_documents=tuple(selected_shared),
        graph=replace(
            graph, relations=tuple(edges), paths=tuple(graph_paths[edge.relation_id] for edge in edges),
        )
        if graph is not None and edges else None,
        combined_rank=tuple(order),
        pack_state="nonempty"
        if facts or docs or edges
        else "omit_only"
        if cold.pack_state == "omit_only"
        else "empty",
        required_missing=missing,
        budget_used=used,
        total_characters=used,
        no_evidence=diagnostics,
        reason_codes=tuple(
            code for code in (*cold.reason_codes, "source_rank_rrf_v1") if code != _DOCUMENT_UNAVAILABLE
        ),
    )


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Final bounded pack plus closed-set diagnostics."""

    hits: tuple[RetrievalHit, ...]
    pack_state: PackState
    required_missing: tuple[str, ...]
    forbidden_filtered: tuple[str, ...]
    no_evidence: tuple[str, ...]
    budget_used: int
    total_characters: int
    plan_id: str
    policy_revision: int | None
    visibility_revision: int | None
    retrieve_mode: RetrieveMode
    query_digest: str
    reason_codes: tuple[str, ...]
    documents: tuple[KnowledgeHit, ...] = ()
    shared_documents: tuple[SharedKnowledgeHit, ...] = ()
    graph: GraphProjection | None = None
    combined_rank: tuple[SourceRank, ...] = ()
    card_total_active: int | None = None
    card_matched_active: int | None = None

    def __post_init__(self) -> None:
        if self.pack_state not in _PACK_STATES:
            raise ValueError("invalid_pack_state")
        if self.budget_used != self.total_characters or self.budget_used < 0:
            raise ValueError("invalid_budget_usage")
        if len(self.hits) > _MAX_HITS:
            raise ValueError("retrieval_hit_limit")
        if tuple(hit.rank for hit in self.hits) != tuple(range(1, len(self.hits) + 1)):
            raise ValueError("retrieval_rank_mismatch")

    def to_dict(self) -> dict[str, object]:
        """Return diagnostics without user/group/fact identifiers or text."""

        return {
            "hit_count": len(self.hits),
            "document_hit_count": len(self.documents),
            "graph_hit_count": 0 if self.graph is None else len(self.graph.relations),
            "card_hit_count": sum(hit.card_projection is not None for hit in self.hits),
            "card_total_active": self.card_total_active,
            "card_matched_active": self.card_matched_active,
            "source_ref_count": sum(len(hit.source_refs) for hit in self.hits),
            "pack_state": self.pack_state,
            "required_missing": list(self.required_missing),
            "forbidden_filtered": list(self.forbidden_filtered),
            "no_evidence": list(self.no_evidence),
            "budget_used": self.budget_used,
            "total_characters": self.total_characters,
            "policy_revision": self.policy_revision,
            "visibility_revision": self.visibility_revision,
            "retrieve_mode": self.retrieve_mode,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class MemoryContextPack:
    """One-turn hot view plus cold retrieval under one shared character budget."""

    hot_hits: tuple[RetrievalHit, ...]
    cold: RetrievalResult
    total_budget: int
    total_budget_used: int
    plan_id: str
    policy_revision: int | None
    visibility_revision: int | None
    retrieve_mode: RetrieveMode
    omitted_reason_codes: tuple[str, ...] = ()
    temporal_trace: MemoryTemporalTrace | None = None
    retrieval_plan: RetrievalPlan | None = None

    @property
    def documents(self) -> tuple[KnowledgeHit, ...]:
        return self.cold.documents

    @property
    def shared_documents(self) -> tuple[SharedKnowledgeHit, ...]:
        return self.cold.shared_documents

    @property
    def graph(self) -> GraphProjection | None:
        return self.cold.graph

    @property
    def combined_rank(self) -> tuple[SourceRank, ...]:
        return self.cold.combined_rank

    def __post_init__(self) -> None:
        if (
            type(self.total_budget) is not int
            or type(self.total_budget_used) is not int
            or self.total_budget < 0
            or not 0 <= self.total_budget_used <= self.total_budget
        ):
            raise ValueError("invalid_memory_context_budget")
        if self.total_budget_used != (
            sum(_hit_chars(hit) for hit in self.hot_hits)
            + self.cold.budget_used
            + _trace_chars(self.temporal_trace)
        ):
            raise ValueError("memory_context_budget_mismatch")
        if len(self.hot_hits) > 12:
            raise ValueError("memory_context_hot_limit")

    def to_dict(self) -> dict[str, object]:
        """Return bounded, body-free diagnostics for this one-turn view."""

        result: dict[str, object] = {
            "hot_hit_count": len(self.hot_hits),
            "cold_hit_count": len(self.cold.hits),
            "document_hit_count": len(self.documents),
            "graph_hit_count": 0 if self.graph is None else len(self.graph.relations),
            "cold_pack_state": self.cold.pack_state,
            "total_budget": self.total_budget,
            "total_budget_used": self.total_budget_used,
            "cold_budget_used": self.cold.budget_used,
            "policy_revision": self.policy_revision,
            "visibility_revision": self.visibility_revision,
            "retrieve_mode": self.retrieve_mode,
            "omitted_reason_codes": list(self.omitted_reason_codes),
            "reason_codes": list(self.cold.reason_codes),
        }
        if self.temporal_trace is not None:
            result["temporal_trace_version_count"] = len(self.temporal_trace.versions)
        return result


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_mode(value: object) -> RetrieveMode:
    if not isinstance(value, str) or value not in _RETRIEVE_MODES:
        raise OperationError("invalid_retrieve_mode")
    return cast(RetrieveMode, value)


def _bounded(value: int, *, maximum: int, code: str) -> int:
    if type(value) is not int or value < 0:
        raise OperationError(code)
    return min(value, maximum)


def _revision(value: int | None, code: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise OperationError(code)
    return value


def _identifiers(values: Sequence[str], code: str) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if not value or value != value.strip() or len(value) > 64 or any(ord(char) < 32 for char in value):
            raise OperationError(code)
        if value not in result:
            result.append(value)
    return tuple(result)


def _safe_diagnostic(prefix: str, index: int) -> str:
    """Identify a missing input position without echoing or hashing its value."""

    return f"{prefix}_{index}"


def classify_query_needs(query: str, current_message: str = "") -> tuple[QueryNeed, ...]:
    """Conservative closed needs; classification never grants historical access."""

    text = f"{query}\n{current_message}".strip().casefold()
    detected: set[QueryNeed] = set()
    for need, (chinese, english) in _NEED_MARKERS.items():
        if any(marker in text for marker in chinese) or any(
            re.search(r"(?<![a-z0-9])" + r"\s+".join(map(re.escape, marker.split())) + r"(?![a-z0-9])", text)
            for marker in english
        ):
            detected.add(need)
    if re.search(r"不是.{0,12}吗", text):
        detected.add("premise_check")
    classified: tuple[QueryNeed, ...] = tuple(need for need in _NEED_PRIORITY if need in detected)
    return classified[:2] or ("ordinary_fact",)


def _allocate_buckets(weights: tuple[int, int, int], capacity: int) -> dict[str, int]:
    """Integer largest-remainder allocation; ties retain memory/doc/graph order."""

    total_weight = sum(weights)
    if total_weight == 0:
        weights, total_weight = (1, 1, 1), 3
    values = [capacity * weight // total_weight for weight in weights]
    remainders = [capacity * weight % total_weight for weight in weights]
    order = sorted(range(3), key=lambda index: (-remainders[index], index))
    for index in order[: capacity - sum(values)]:
        values[index] += 1
    return dict(zip(_BUCKETS, values, strict=True))


def _query_profile(
    needs: tuple[str, ...],
    caps: dict[str, int],
    buckets: dict[str, int],
    capacity: int,
    *,
    enabled: bool,
) -> tuple[str, dict[str, int], dict[str, int], bool, tuple[str, ...]]:
    if not enabled or needs == ("ordinary_fact",):
        return "ordinary_identity", caps, buckets, True, ("ordinary" if enabled else "disabled", "identity")
    primary = cast(QueryNeed, needs[0])
    profile_id = _PROFILE_IDS[primary]
    if primary == "broad_recall":
        weights = cast(tuple[int, int, int], tuple(buckets[bucket] for bucket in _BUCKETS))
        reasons = ("need:broad_recall", f"profile:{profile_id}", "clamp_all")
    else:
        weights = _PROFILE_RATIOS[primary]
        if "document" in caps:
            caps["document"] = min(caps["document"], 1) if primary != "doc_grounding" else caps["document"]
        if primary == "relation_multihop":
            for kind in ("memory_fact", "memory_card"):
                if kind in caps:
                    caps[kind] = min(caps[kind], 3)
        else:
            if "graph_fact" in caps:
                caps["graph_fact"] = min(caps["graph_fact"], 2)
            if primary == "doc_grounding":
                for kind in ("memory_fact", "memory_card"):
                    if kind in caps:
                        caps[kind] = min(caps[kind], 2)
        reasons = (f"need:{primary}", f"profile:{profile_id}")
    return profile_id, caps, _allocate_buckets(weights, capacity), False, reasons


def retrieval_bucket_budget(plan: RetrievalPlan, source_kind: str) -> int:
    """The pack soft cap for one actual provider, under the unchanged total cap."""

    if source_kind not in _TYPE_BUCKETS:
        raise OperationError("invalid_retrieval_source_kind")
    return min(plan.total_budget, plan.bucket_budgets[_TYPE_BUCKETS[source_kind]])


@dataclass(frozen=True, slots=True)
class CardEligibilityPolicy:
    """Read-time Card TTL policy, reserved for a real Card provider's gate."""

    enabled: bool = False
    category_ttl_days: Mapping[str, int] = field(default_factory=lambda: {"status": 30, "event": 180})

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("invalid card eligibility switch")
        days: dict[str, int] = {}
        for category, value in self.category_ttl_days.items():
            if category not in {
                "status",
                "event",
                "preference",
                "boundary",
                "relationship",
                "promise",
                "fact",
            }:
                raise ValueError("invalid card category")
            if type(value) is not int:
                raise ValueError("invalid card TTL")
            days[category] = max(1, min(value, 3650))
        object.__setattr__(self, "category_ttl_days", MappingProxyType(days))


def parse_card_anchor_timestamp(value: str) -> datetime | None:
    """Offset-less CardStore anchors mean Shanghai, unlike fact rank timestamps."""

    value = value.strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai")) if parsed.tzinfo is None else parsed


def card_time_eligible(
    *,
    category: str,
    updated_at: str,
    created_at: str,
    now: datetime,
    policy: CardEligibilityPolicy,
) -> bool:
    """Non-destructive TTL check; the Card owner separately gates active status.

    No current MemoryFact has a Card category. This helper must not be applied
    by guessing a category from a fact predicate or to historical trace parents.
    """

    if not policy.enabled:
        return True
    days = policy.category_ttl_days.get(category)
    if days is None:
        return True
    if now.tzinfo is None:
        raise ValueError("Card eligibility clock requires an explicit timezone")
    anchor = parse_card_anchor_timestamp(updated_at.strip() or created_at)
    if anchor is None:
        return False
    return now.astimezone(UTC) - anchor.astimezone(UTC) <= timedelta(days=days)


def _source_kinds(mode: RetrieveMode) -> tuple[str, ...]:
    if mode == "skip":
        return ()
    if mode == "doc":
        return ("document",)
    if mode == "fact":
        return ("memory_fact",)
    return ("memory_fact", "document")


def build_retrieval_plan(
    application_scope: Scope,
    *,
    retrieve_mode: RetrieveMode,
    query: str = "",
    needs: Sequence[str] = (),
    max_hits: int = _DEFAULT_HITS,
    total_budget: int = _DEFAULT_CHARS,
    policy_revision: int | None = None,
    visibility_revision: int | None = None,
    query_planner_enabled: bool = False,
    current_message: str = "",
    source_caps: Mapping[str, int] | None = None,
    bucket_budgets: Mapping[str, int] | None = None,
) -> RetrievalPlan:
    """Build a deterministic plan without reading or logging any content."""

    if type(application_scope) is not Scope:
        raise OperationError("invalid_retrieval_scope")
    mode = _normalize_mode(retrieve_mode)
    normalized_query = query.strip()
    normalized_needs = _identifiers(needs, "invalid_retrieval_need")
    if len(normalized_needs) > 2:
        raise OperationError("retrieval_need_limit")
    if any(need not in _NEED_PRIORITY for need in normalized_needs):
        raise OperationError("invalid_retrieval_need")
    if type(query_planner_enabled) is not bool:
        raise OperationError("invalid_query_planner_setting")
    if query_planner_enabled:
        inferred = normalized_needs or classify_query_needs(normalized_query, current_message)
        normalized_needs = tuple(
            need
            for need in _NEED_PRIORITY
            if need in inferred and (need != "ordinary_fact" or len(inferred) == 1)
        )
    else:
        normalized_needs = ("ordinary_fact",)
    hit_limit = _bounded(max_hits, maximum=_MAX_HITS, code="invalid_retrieval_hit_limit")
    budget = _bounded(total_budget, maximum=_MAX_CHARS, code="invalid_retrieval_budget")
    policy_revision = _revision(policy_revision, "invalid_policy_revision")
    visibility_revision = _revision(visibility_revision, "invalid_visibility_revision")
    caps = {
        "memory_fact": hit_limit if mode in {"fact", "hybrid"} else 0,
        # The first batch has no document provider, including in hybrid mode.
        "document": 0,
    }
    if query_planner_enabled:
        caps["memory_card"] = hit_limit if mode in {"fact", "hybrid"} else 0
    if source_caps is not None:
        if any(kind not in _TYPE_BUCKETS for kind in source_caps):
            raise OperationError("invalid_retrieval_type_caps")
        caps = {
            kind: min(hit_limit, _bounded(value, maximum=_MAX_HITS, code="invalid_retrieval_type_caps"))
            for kind, value in source_caps.items()
        }
        caps = {
            kind: value
            if (
                mode == "hybrid"
                or mode == "doc"
                and kind == "document"
                or mode == "fact"
                and kind != "document"
            )
            else 0
            for kind, value in caps.items()
        }
    if query_planner_enabled and "memory_card" not in caps:
        caps["memory_card"] = hit_limit if mode in {"fact", "hybrid"} else 0
    kinds = tuple(dict.fromkeys((*_source_kinds(mode), "memory_card"))) if (
        query_planner_enabled and mode in {"fact", "hybrid"}
    ) else _source_kinds(mode)
    if caps.get("graph_fact", 0):
        kinds = (*kinds, "graph_fact")
    buckets = {"memory": budget, "doc": 0, "graph": 0}
    if bucket_budgets is not None:
        if any(bucket not in _BUCKETS for bucket in bucket_budgets):
            raise OperationError("invalid_retrieval_bucket_budget")
        buckets = {
            bucket: _bounded(
                bucket_budgets.get(bucket, 0), maximum=_MAX_CHARS, code="invalid_retrieval_bucket_budget"
            )
            for bucket in _BUCKETS
        }
    profile, caps, buckets, identity, profile_reasons = _query_profile(
        normalized_needs,
        caps,
        buckets,
        budget,
        enabled=query_planner_enabled,
    )
    reasons = [
        "retrieve_mode_skip"
        if mode == "skip"
        else _DOCUMENT_UNAVAILABLE
        if mode in {"doc", "hybrid"}
        else "fact_source",
        "empty_query" if not normalized_query else "query_digest_only",
        *profile_reasons,
    ]
    digest = _digest(normalized_query)
    material = {
        "algorithm_version": _ALGORITHM_VERSION,
        "application_scope": {
            "bot_id": application_scope.bot_id,
            "group_id": application_scope.group_id,
        },
        "needs": normalized_needs,
        "policy_revision": policy_revision,
        "query_digest": digest,
        "retrieve_mode": mode,
        "source_kinds": kinds,
        "total_budget": budget,
        "type_caps": caps,
        "visibility_revision": visibility_revision,
        "query_profile": profile,
        "bucket_budgets": buckets,
        "query_planner_enabled": query_planner_enabled,
    }
    plan_id = "rplan_" + _digest(json.dumps(material, sort_keys=True, separators=(",", ":")))
    return RetrievalPlan(
        plan_id=plan_id,
        query_digest=digest,
        application_scope=application_scope,
        policy_revision=policy_revision,
        visibility_revision=visibility_revision,
        needs=normalized_needs,
        source_kinds=kinds,
        retrieve_mode=mode,
        type_caps=MappingProxyType(caps),
        total_budget=budget,
        algorithm_version=_ALGORITHM_VERSION,
        reason_codes=tuple(reasons),
        query_profile=profile,
        bucket_budgets=MappingProxyType(buckets),
        query_planner_enabled=query_planner_enabled,
        planner_identity=identity,
    )


def _query_tokens(query: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for match in _QUERY_TOKEN_RE.finditer(query):
        text = match.group(0)
        index = 0
        while index < len(text):
            is_cjk = _CJK_CHAR_RE.fullmatch(text[index]) is not None
            end = index + 1
            while end < len(text) and (_CJK_CHAR_RE.fullmatch(text[end]) is not None) == is_cjk:
                end += 1
            fragment = text[index:end]
            if is_cjk:
                tokens.extend(fragment[offset : offset + 2].casefold() for offset in range(len(fragment) - 1))
            else:
                tokens.extend(part.group(0).casefold() for part in _QUERY_TOKEN_RE.finditer(fragment))
            index = end
    return tuple(dict.fromkeys(tokens))


def _bounded_query_tokens(query: str) -> tuple[tuple[str, ...], bool]:
    tokens = _query_tokens(query)
    bounded = tuple(token for token in tokens if len(token) <= _MAX_QUERY_TOKEN_CHARS)
    truncated = len(bounded) != len(tokens) or len(bounded) > _MAX_QUERY_TOKENS
    return bounded[:_MAX_QUERY_TOKENS], truncated


def _fact_text(fact: MemoryFact) -> str:
    return " ".join((fact.subject_id, fact.predicate, fact.value)).casefold()


def _matches(fact: MemoryFact, value: str) -> bool:
    needle = value.casefold()
    return needle in _fact_text(fact) or any(
        needle == ref.casefold() for ref in (*fact.source_ids, *fact.evidence_refs)
    )


def _score(fact: MemoryFact, tokens: tuple[str, ...]) -> float:
    if not tokens:
        return 0.0
    return round(sum(1 for token in tokens if _matches(fact, token)) / len(tokens), 6)


def _hit(fact: MemoryFact, score: float, rank: int) -> RetrievalHit:
    return RetrievalHit(
        object_id=fact.fact_id,
        domain="memory_fact",
        fact_revision=fact.fact_revision,
        subject_id=fact.subject_id,
        predicate=fact.predicate,
        value=fact.value,
        source_refs=tuple(fact.source_ids),
        evidence_refs=tuple(fact.evidence_refs),
        source_scope=fact.scope,
        visibility_scope=fact.scope,
        state="active",
        conflict_state="none",
        score=score,
        rank=rank,
        reason_code="fact_match",
    )


def _hot_hit(fact: MemoryFact, rank: int) -> RetrievalHit:
    hit = _hit(fact, 0.0, rank)
    reason = {
        "identity.preferred_name": "hot_stable_name",
        "communication.boundary": "hot_communication_boundary",
        "communication.preference": "hot_communication_preference",
    }.get(fact.predicate, "hot_allowlisted_fact")
    return replace(hit, reason_code=reason)


def _hit_chars(hit: RetrievalHit) -> int:
    return (0 if hit.card_projection is None else
            len(hit.card_projection.category) + len(str(hit.card_projection.classification_revision))) + sum(
        len(value)
        for value in (
            hit.object_id,
            str(hit.fact_revision),
            hit.subject_id,
            hit.predicate,
            hit.value,
            *hit.source_refs,
            *hit.evidence_refs,
        )
    )


def _trace_chars(trace: MemoryTemporalTrace | None) -> int:
    if trace is None:
        return 0
    return sum(
        len(value)
        for fact in trace.versions
        for value in (
            fact.fact_id,
            str(fact.fact_revision),
            fact.subject_id,
            fact.predicate,
            fact.value,
            *fact.source_ids,
            *fact.evidence_refs,
        )
    )


def _explicit_history_intent(message: str) -> bool:
    return bool(_TRACE_HISTORY_INTENT.search(" ".join(message.split())))


def _result(
    plan: RetrievalPlan,
    *,
    hits: Sequence[RetrievalHit],
    state: PackState,
    required_missing: Sequence[str] = (),
    forbidden_filtered: Sequence[str] = (),
    no_evidence: Sequence[str] = (),
    budget_used: int = 0,
    reasons: Sequence[str] = (),
) -> RetrievalResult:
    return RetrievalResult(
        hits=tuple(hits),
        pack_state=state,
        required_missing=tuple(dict.fromkeys(required_missing)),
        forbidden_filtered=tuple(dict.fromkeys(forbidden_filtered)),
        no_evidence=tuple(dict.fromkeys(no_evidence)),
        budget_used=budget_used,
        total_characters=budget_used,
        plan_id=plan.plan_id,
        policy_revision=plan.policy_revision,
        visibility_revision=plan.visibility_revision,
        retrieve_mode=plan.retrieve_mode,
        query_digest=plan.query_digest,
        reason_codes=tuple(dict.fromkeys((*plan.reason_codes, *reasons))),
    )


def build_evidence_pack(
    plan: RetrievalPlan,
    facts: Sequence[object],
    *,
    query: str,
    required: Sequence[str] = (),
    forbidden: Sequence[str] = (),
) -> RetrievalResult:
    """Apply the N6 evidence gate and bounded budget to active facts.

    A ``MemoryFact`` without both source and evidence references is omitted.
    Cross-scope facts, inactive facts, and non-fact candidates are omitted as
    well.  No post-ranking score changes these checks.
    """

    required_values = _identifiers(required, "invalid_required_evidence")
    forbidden_values = _identifiers(forbidden, "invalid_forbidden_evidence")
    if plan.retrieve_mode == "skip":
        return _result(
            plan,
            hits=(),
            state="skip",
            required_missing=tuple(
                _safe_diagnostic("required", index) for index, _ in enumerate(required_values)
            ),
            no_evidence=("skip",),
        )
    if plan.retrieve_mode == "doc":
        return _result(
            plan,
            hits=(),
            state="empty",
            required_missing=tuple(
                _safe_diagnostic("required", index) for index, _ in enumerate(required_values)
            ),
            no_evidence=(_DOCUMENT_UNAVAILABLE,),
        )

    if not query.strip():
        return _result(
            plan,
            hits=(),
            state="empty",
            required_missing=tuple(
                _safe_diagnostic("required", index) for index, _ in enumerate(required_values)
            ),
            no_evidence=("empty_query", _DOCUMENT_UNAVAILABLE)
            if plan.retrieve_mode == "hybrid"
            else ("empty_query",),
        )

    tokens, token_truncated = _bounded_query_tokens(query.strip())
    token_reasons = ("query_token_truncated",) if token_truncated else ()
    candidates: list[tuple[MemoryFact, float]] = []
    gate_omitted = False
    forbidden_reasons: list[str] = []
    no_evidence: list[str] = []
    for candidate in facts:
        if not isinstance(candidate, MemoryFact):
            gate_omitted = True
            no_evidence.append("invalid_fact")
            continue
        if candidate.status != "active":
            gate_omitted = True
            no_evidence.append("inactive_fact")
            continue
        if candidate.scope != plan.application_scope:
            forbidden_reasons.append("cross_scope")
            no_evidence.append("cross_scope")
            continue
        if not candidate.source_ids:
            gate_omitted = True
            no_evidence.append("missing_source_ref")
            continue
        if not candidate.evidence_refs:
            gate_omitted = True
            no_evidence.append("missing_evidence")
            continue
        if any(_matches(candidate, value) for value in forbidden_values):
            forbidden_reasons.append("forbidden_filtered")
            continue
        relevance = _score(candidate, tokens)
        if relevance <= 0:
            continue
        candidates.append((candidate, relevance))

    candidates.sort(key=lambda item: (-item[1], -item[0].updated_at, item[0].fact_id))
    candidates = candidates[: int(plan.type_caps.get("memory_fact", 0))]

    missing = [
        _safe_diagnostic("required", index)
        for index, value in enumerate(required_values)
        if not any(_matches(candidate, value) for candidate, _ in candidates)
    ]
    if missing:
        no_evidence.append("required_missing")
        return _result(
            plan,
            hits=(),
            state="omit_only" if gate_omitted else "empty",
            required_missing=missing,
            forbidden_filtered=forbidden_reasons,
            no_evidence=no_evidence,
            reasons=token_reasons,
        )

    retained: list[tuple[MemoryFact, float]] = []
    budget_used = 0
    budget_dropped = False
    memory_budget = retrieval_bucket_budget(plan, "memory_fact")
    for candidate, relevance in candidates:
        candidate_hit = _hit(candidate, relevance, len(retained) + 1)
        cost = _hit_chars(candidate_hit)
        if budget_used + cost > memory_budget:
            budget_dropped = True
            continue
        budget_used += cost
        retained.append((candidate, relevance))
    if budget_dropped:
        no_evidence.append("budget_exhausted")

    hits = tuple(
        _hit(candidate, relevance, index) for index, (candidate, relevance) in enumerate(retained, 1)
    )
    if plan.retrieve_mode == "hybrid":
        no_evidence.append(_DOCUMENT_UNAVAILABLE)
    if not candidates and not gate_omitted and not forbidden_reasons:
        no_evidence.append("miss")
    state: PackState = "nonempty" if hits else "omit_only" if gate_omitted else "empty"
    return _result(
        plan,
        hits=hits,
        state=state,
        forbidden_filtered=forbidden_reasons,
        no_evidence=no_evidence,
        budget_used=budget_used,
        reasons=token_reasons,
    )


def _card_hit(card: MemoryCard, score: float, rank: int) -> RetrievalHit:
    return replace(_hit(card.fact, score, rank), card_projection=card,
                   reason_code="card_match")


def _build_card_pack(
    plan: RetrievalPlan, facts: Sequence[MemoryFact], cards: MemoryCardQueryResult,
    *, query: str, required: Sequence[str], forbidden: Sequence[str],
    excluded_ids: frozenset[str] = frozenset(),
) -> RetrievalResult:
    """One owner fact has one rank contribution/body, including expired Cards."""
    required = _identifiers(required, "invalid_required_evidence")
    classified_ids = frozenset(cards.classified_fact_ids)
    ordinary = build_evidence_pack(
        plan, tuple(fact for fact in facts if fact.fact_id not in classified_ids),
        query=query, forbidden=forbidden,
    )
    selected_cards = {card.fact.fact_id: card for card in cards.cards
                      if card.fact.fact_id not in excluded_ids}
    card_plan = replace(plan, type_caps=MappingProxyType({
        **plan.type_caps, "memory_fact": plan.type_caps.get("memory_card", 0),
    }))
    projected = build_evidence_pack(card_plan, tuple(card.fact for card in selected_cards.values()),
                                    query=query, forbidden=forbidden)
    ranked = [(SourceRank("memory_fact", hit.object_id, rank, 1 / (60 + rank)), hit)
              for rank, hit in enumerate(ordinary.hits, 1)]
    ranked.extend((SourceRank("memory_card", hit.object_id, rank, 1 / (60 + rank)),
                   _card_hit(selected_cards[hit.object_id], hit.score, hit.rank))
                  for rank, hit in enumerate(projected.hits, 1))
    ranked.sort(key=lambda item: (-item[0].rrf_score, item[0].source_kind, item[0].object_id))
    hits: list[RetrievalHit] = []
    order: list[SourceRank] = []
    used = 0
    for pointer, hit in ranked:
        cost = _hit_chars(hit)
        if len(hits) >= _MAX_HITS or used + cost > retrieval_bucket_budget(plan, "memory_card"):
            continue
        hits.append(replace(hit, rank=len(hits)+1))
        order.append(pointer)
        used += cost
    missing = tuple(_safe_diagnostic("required", index) for index, value in enumerate(required)
                    if not any(value.casefold() in f"{hit.subject_id} {hit.predicate} {hit.value}".casefold()
                               or value in (*hit.source_refs, *hit.evidence_refs) for hit in hits))
    if missing:
        hits, order, used = [], [], 0
    diagnostics = tuple(dict.fromkeys((*ordinary.no_evidence, *projected.no_evidence)))
    if hits:
        diagnostics = tuple(code for code in diagnostics if code != "miss")
    if missing:
        diagnostics += ("required_missing",)
    return replace(ordinary, hits=tuple(hits), combined_rank=tuple(order),
                   pack_state="nonempty" if hits else "empty", required_missing=missing,
                   budget_used=used, total_characters=used, no_evidence=diagnostics,
                   forbidden_filtered=tuple(dict.fromkeys((*ordinary.forbidden_filtered,
                                                          *projected.forbidden_filtered))),
                   reason_codes=tuple(dict.fromkeys((
                       *ordinary.reason_codes, "card_source", "source_rank_rrf_v1"))),
                   card_total_active=cards.total_active, card_matched_active=cards.matched_active)


class RetrievalService:
    """Read active facts through the authorized MemoryService owner."""

    def __init__(
        self,
        memory: MemoryService,
        *,
        policy_revision: int | None = None,
        visibility_revision: int | None = None,
        default_max_hits: int = _DEFAULT_HITS,
        default_total_budget: int = _DEFAULT_CHARS,
        query_planner_enabled: bool = False,
        knowledge: KnowledgeService | None = None,
        graph: GraphService | None = None,
        cross_group_sharing_enabled: bool = False,
        personal_graph_enabled: bool = False,
    ) -> None:
        self.memory = memory
        if knowledge is not None and (
            knowledge.store is not memory.store or knowledge.policy is not memory.policy
        ):
            raise OperationError("retrieval_owner_mismatch")
        if graph is not None and (
            graph.store is not memory.store or graph.policy is not memory.policy
            or graph.knowledge is not knowledge
        ):
            raise OperationError("retrieval_owner_mismatch")
        self.knowledge = knowledge
        self.graph = graph
        if type(personal_graph_enabled) is not bool:
            raise OperationError("invalid_personal_graph_setting")
        if personal_graph_enabled and (graph is None or graph.memory is not memory):
            raise OperationError("retrieval_owner_mismatch")
        self.personal_graph_enabled = personal_graph_enabled
        self.cross_group_sharing_enabled = cross_group_sharing_enabled
        self.policy_revision = _revision(policy_revision, "invalid_policy_revision")
        self.visibility_revision = _revision(visibility_revision, "invalid_visibility_revision")
        self.default_max_hits = _bounded(
            default_max_hits, maximum=_MAX_HITS, code="invalid_retrieval_hit_limit"
        )
        self.default_total_budget = _bounded(
            default_total_budget, maximum=_MAX_CHARS, code="invalid_retrieval_budget"
        )
        if type(query_planner_enabled) is not bool:
            raise OperationError("invalid_query_planner_setting")
        self.query_planner_enabled = query_planner_enabled

    def plan(
        self,
        scope: Scope,
        *,
        retrieve_mode: RetrieveMode,
        query: str = "",
        needs: Sequence[str] = (),
        max_hits: int | None = None,
        total_budget: int | None = None,
        current_message: str = "",
        source_caps: Mapping[str, int] | None = None,
        bucket_budgets: Mapping[str, int] | None = None,
    ) -> RetrievalPlan:
        connected = (self.knowledge is not None and retrieve_mode in {"doc", "hybrid"}) or (
            self.graph is not None and retrieve_mode in {"fact", "hybrid"}
        )
        hits = self.default_max_hits if max_hits is None else max_hits
        budget = self.default_total_budget if total_budget is None else total_budget
        plan = build_retrieval_plan(
            scope,
            retrieve_mode=retrieve_mode,
            query=query,
            needs=needs,
            max_hits=self.default_max_hits if max_hits is None else max_hits,
            total_budget=(self.default_total_budget if total_budget is None else total_budget),
            policy_revision=self.policy_revision,
            visibility_revision=self.visibility_revision,
            query_planner_enabled=self.query_planner_enabled,
            current_message=current_message,
            source_caps=source_caps
            if source_caps is not None
            else {
                "memory_fact": hits if retrieve_mode in {"fact", "hybrid"} else 0,
                "document": min(hits, 20) if self.knowledge is not None else 0,
                **({"graph_fact": min(hits, 8)} if self.graph is not None else {}),
            }
            if connected
            else None,
            bucket_budgets=bucket_budgets
            if bucket_budgets is not None
            else {
                "memory": budget if retrieve_mode in {"fact", "hybrid"} else 0,
                "doc": budget,
                "graph": budget if self.graph is not None else 0,
            }
            if connected
            else None,
        )

        if connected:
            return replace(
                plan, reason_codes=tuple(
                    code for code in plan.reason_codes if code != _DOCUMENT_UNAVAILABLE
                ),
            )
        return plan

    async def _documents(
        self, actor: str, scope: Scope, plan: RetrievalPlan, query: str
    ) -> tuple[KnowledgeHit, ...]:
        if self.knowledge is None or plan.retrieve_mode not in {"doc", "hybrid"} or not query.strip():
            return ()
        cap = min(plan.type_caps.get("document", 0), 20)
        budget = min(retrieval_bucket_budget(plan, "document"), 8192)
        if not cap or not budget:
            return ()
        return await self.knowledge.search(query, scope=scope, actor=actor, limit=cap, body_budget=budget)

    async def _shared_documents(
        self, actor: str, scope: Scope, plan: RetrievalPlan, query: str,
        local: tuple[KnowledgeHit, ...],
    ) -> tuple[SharedKnowledgeHit, ...]:
        if (not self.cross_group_sharing_enabled or self.knowledge is None
                or plan.retrieve_mode not in {"doc", "hybrid"} or not query.strip()):
            return ()
        cap = min(plan.type_caps.get("document", 0), 20) - len(local)
        budget = min(retrieval_bucket_budget(plan, "document"), 8192)
        if cap <= 0 or not budget:
            return ()
        _, grants = await self.memory.policy.visibility_snapshot()
        candidates: list[SharedKnowledgeHit] = []
        for grant in grants:
            if (grant.status != "active" or grant.expires_at <= time() or grant.target_scope != scope
                    or grant.material_type != "knowledge"):
                continue
            receipt = await self.memory.policy.read_visibility_receipt(
                actor=actor, grant_id=grant.grant_id, source_scope=grant.source_scope,
                target_scope=scope, material_type="knowledge", object_refs=grant.object_refs,
            )
            found = await self.knowledge.search_shared(
                query, actor=actor, target_scope=scope, receipt=receipt,
                limit=cap - len(candidates), body_budget=budget,
            )
            for candidate in found:
                if not any(old.hit == candidate.hit for old in candidates):
                    candidates.append(candidate)
            if len(candidates) >= cap:
                break
        return tuple(candidates)

    async def retrieve(
        self,
        actor: str,
        scope: Scope,
        *,
        retrieve_mode: RetrieveMode,
        query: str = "",
        required: Sequence[str] = (),
        forbidden: Sequence[str] = (),
        needs: Sequence[str] = (),
        max_hits: int | None = None,
        total_budget: int | None = None,
        current_message: str = "",
    ) -> RetrievalResult:
        if (self.knowledge is not None and retrieve_mode in {"doc", "hybrid"}) or (
            self.graph is not None and retrieve_mode in {"fact", "hybrid"}
        ):
            return (
                await self.retrieve_context(
                    actor,
                    scope,
                    retrieve_mode=retrieve_mode,
                    query=query,
                    required=required,
                    forbidden=forbidden,
                    needs=needs,
                    max_hits=max_hits,
                    total_budget=total_budget,
                    hot_budget=0,
                )
            ).cold
        plan = self.plan(
            scope,
            retrieve_mode=retrieve_mode,
            query=query,
            needs=needs,
            max_hits=max_hits,
            total_budget=total_budget,
            current_message=current_message,
        )
        if plan.retrieve_mode in {"skip", "doc"}:
            return build_evidence_pack(
                plan,
                (),
                query=query,
                required=required,
                forbidden=forbidden,
            )
        if not query.strip():
            return build_evidence_pack(
                plan,
                (),
                query=query,
                required=required,
                forbidden=forbidden,
            )
        # MemoryService is the only connected fact provider. Its read path
        # enforces memory.retrieve, exact scope, active fact status, source
        # tombstones, and the source author's current message.read,
        # memory.archive, and memory.learn grants.
        tokens, _ = _bounded_query_tokens(query.strip())
        search = await self.memory.search_facts(
            actor=actor,
            scope=scope,
            tokens=tokens,
            limit=256,
        )
        if plan.query_planner_enabled:
            cards = await self.memory.search_cards(actor=actor, scope=scope, query=query, limit=256)
            result = _build_card_pack(plan, search.facts, cards, query=query,
                                      required=required, forbidden=forbidden)
        else:
            result = build_evidence_pack(
                plan, search.facts, query=query, required=required, forbidden=forbidden,
            )
        if not search.truncated:
            return result
        return replace(
            result,
            reason_codes=tuple(dict.fromkeys((*result.reason_codes, "memory_candidate_truncated"))),
        )

    async def _trace_from_current_message(
        self,
        actor: str,
        scope: Scope,
        message: str,
        subject_id: str | None,
        current_speakers: Sequence[str],
    ) -> tuple[MemoryTemporalTrace | None, str | None]:
        if not _explicit_history_intent(message):
            return None, None
        if subject_id is None or subject_id not in current_speakers:
            return None, "trace_omitted:subject_not_current_speaker"
        tokens, _ = _bounded_query_tokens(message)
        search = await self.memory.search_facts(
            actor=actor,
            scope=scope,
            tokens=tokens,
            subject_id=subject_id,
            limit=8,
        )
        if search.truncated or len(search.facts) > 1:
            return None, "trace_omitted:ambiguous_head"
        if not search.facts:
            return None, "trace_omitted:no_head_match"
        trace = await self.memory.read_temporal_trace(
            actor=actor,
            scope=scope,
            subject_id=subject_id,
            head_fact_id=search.facts[0].fact_id,
        )
        if trace.omitted_reason is not None:
            return None, f"trace_omitted:{trace.omitted_reason}"
        return trace, None

    async def retrieve_context(
        self,
        actor: str,
        scope: Scope,
        *,
        retrieve_mode: RetrieveMode,
        query: str = "",
        hot_subject_ids: Sequence[str] = (),
        required: Sequence[str] = (),
        forbidden: Sequence[str] = (),
        needs: Sequence[str] = (),
        max_hits: int | None = None,
        total_budget: int | None = None,
        hot_budget: int | None = None,
        trace_message: str | None = None,
        trace_subject_id: str | None = None,
    ) -> MemoryContextPack:
        """Build an ephemeral hot/cold view under one owner and one budget.

        The hot subject list must come from current-turn speaker/addressing
        decisions. Hot facts are selected first; cold retrieval receives only
        the remaining character budget and excludes hot fact IDs.
        """

        budget = _bounded(
            self.default_total_budget if total_budget is None else total_budget,
            maximum=_MAX_CHARS,
            code="invalid_retrieval_budget",
        )
        hot_cap = (
            min(_DEFAULT_HOT_CHARS, budget)
            if hot_budget is None
            else _bounded(
                hot_budget,
                maximum=_MAX_HOT_CHARS,
                code="invalid_memory_hot_budget",
            )
        )
        hot_cap = min(hot_cap, budget)
        revision = self.policy_revision
        if revision is None:
            revision = await self.memory.policy.revision()
        visibility_revision = self.visibility_revision
        if visibility_revision is None:
            visibility_revision = revision
        plan = build_retrieval_plan(
            scope,
            retrieve_mode=retrieve_mode,
            query=query,
            needs=needs,
            max_hits=(self.default_max_hits if max_hits is None else max_hits),
            total_budget=budget,
            policy_revision=revision,
            visibility_revision=visibility_revision,
            query_planner_enabled=self.query_planner_enabled,
            current_message=trace_message or "",
            source_caps={
                "memory_fact": (self.default_max_hits if max_hits is None else max_hits)
                if retrieve_mode in {"fact", "hybrid"}
                else 0,
                "document": min(self.default_max_hits if max_hits is None else max_hits, 20)
                if self.knowledge is not None else 0,
                **({"graph_fact": min(self.default_max_hits if max_hits is None else max_hits, 8)}
                   if self.graph is not None else {}),
            }
            if (self.knowledge is not None and retrieve_mode in {"doc", "hybrid"}) or (
                self.graph is not None and retrieve_mode in {"fact", "hybrid"}
            ) else None,
            bucket_budgets={"memory": budget if retrieve_mode in {"fact", "hybrid"} else 0,
                            "doc": budget if self.knowledge is not None else 0,
                            "graph": budget if self.graph is not None else 0}
            if (self.knowledge is not None and retrieve_mode in {"doc", "hybrid"}) or (
                self.graph is not None and retrieve_mode in {"fact", "hybrid"}
            )
            else None,
        )
        required_values = _identifiers(required, "invalid_required_evidence")
        forbidden_values = _identifiers(forbidden, "invalid_forbidden_evidence")
        omitted: list[str] = []
        documents: tuple[KnowledgeHit, ...] = ()
        shared_documents: tuple[SharedKnowledgeHit, ...] = ()
        connected = (self.knowledge is not None and plan.retrieve_mode in {"doc", "hybrid"}) or (
            self.graph is not None and plan.retrieve_mode in {"fact", "hybrid"}
        )
        if connected:
            plan = replace(
                plan, reason_codes=tuple(
                    code for code in plan.reason_codes if code != _DOCUMENT_UNAVAILABLE
                ),
            )
        try:
            documents = await self._documents(actor, scope, plan, query)
            shared_documents = await self._shared_documents(actor, scope, plan, query, documents)
            documents = (*documents, *(shared.hit for shared in shared_documents))
        except OperationError as exc:
            if exc.code != "denied":
                raise
            omitted.append("document_permission_revoked")
        graph_projection = None
        if self.graph is not None and plan.retrieve_mode in {"fact", "hybrid"} and query.strip():
            cap = min(plan.type_caps.get("graph_fact", 0), 8)
            if cap and retrieval_bucket_budget(plan, "graph_fact"):
                try:
                    graph_projection = await self.graph.query_projection(
                        query, actor=actor, scope=scope, limit=cap,
                    )
                except OperationError as exc:
                    if exc.code != "denied":
                        raise
                    omitted.append("graph_permission_revoked")
        temporal_trace: MemoryTemporalTrace | None = None
        trace_cost = 0
        if trace_message is not None and plan.retrieve_mode != "doc":
            try:
                temporal_trace, trace_reason = await self._trace_from_current_message(
                    actor,
                    scope,
                    trace_message,
                    trace_subject_id,
                    hot_subject_ids,
                )
            except OperationError as exc:
                if exc.code != "denied":
                    raise
                temporal_trace, trace_reason = None, "trace_omitted:permission_revoked"
            if trace_reason is not None:
                omitted.append(trace_reason)
            elif temporal_trace is not None:
                trace_cost = _trace_chars(temporal_trace)
                if trace_cost > budget:
                    temporal_trace = None
                    trace_cost = 0
                    omitted.append("trace_omitted:budget")
        hot_hits: list[RetrievalHit] = []
        hot_used = 0

        def pack(
            cold: RetrievalResult,
            *,
            hits: Sequence[RetrievalHit] = (),
            reasons: Sequence[str] = (),
            trace: MemoryTemporalTrace | None = temporal_trace,
        ) -> MemoryContextPack:
            if connected:
                cold = _fuse_documents(
                    plan,
                    cold,
                    documents,
                    required=required_values,
                    forbidden=forbidden_values,
                    already_used=sum(_hit_chars(hit) for hit in hits) + _trace_chars(trace),
                    graph=graph_projection,
                    hot_hits=hits,
                    shared_documents=shared_documents,
                )
            used = sum(_hit_chars(hit) for hit in hits) + cold.budget_used + _trace_chars(trace)
            return MemoryContextPack(
                hot_hits=tuple(hits),
                cold=cold,
                total_budget=plan.total_budget,
                total_budget_used=used,
                plan_id=plan.plan_id,
                policy_revision=plan.policy_revision,
                visibility_revision=plan.visibility_revision,
                retrieve_mode=plan.retrieve_mode,
                omitted_reason_codes=tuple(dict.fromkeys(reasons)),
                temporal_trace=trace,
                retrieval_plan=plan,
            )

        def permission_denied() -> MemoryContextPack:
            cold = build_evidence_pack(
                plan, (), query=query, required=required_values, forbidden=forbidden_values
            )
            return pack(
                cold,
                reasons=(*omitted, "memory_permission_revoked"),
                trace=None,
            )

        try:
            cards = None
            if plan.query_planner_enabled and plan.retrieve_mode != "doc":
                cards = await self.memory.search_cards(actor=actor, scope=scope, query=query, limit=256) if (
                    plan.retrieve_mode != "skip" and query.strip()
                ) else await self.memory.query_cards(actor=actor, scope=scope, limit=1)
            classified_ids: frozenset[str] = (
                frozenset(cards.classified_fact_ids) if cards is not None else frozenset()
            )
            hot_cap = min(hot_cap, budget - trace_cost, retrieval_bucket_budget(plan, "memory_fact"))
            hot_search = (
                await self.memory.search_hot_facts(
                    actor=actor,
                    scope=scope,
                    subject_ids=hot_subject_ids,
                    limit=_MAX_HOT_HITS,
                )
                if hot_cap > 0 and plan.retrieve_mode != "doc"
                else None
            )
            if hot_search is not None:
                for fact in hot_search.facts:
                    if any(_matches(fact, value) for value in forbidden_values):
                        omitted.append("hot_forbidden_filtered")
                        continue
                    if fact.fact_id in classified_ids:
                        card = await self.memory.read_card(actor=actor, scope=scope, fact_id=fact.fact_id)
                        if card is None:
                            omitted.append("hot_card_ineligible")
                            continue
                        candidate = replace(_hot_hit(fact, len(hot_hits) + 1), card_projection=card)
                    else:
                        candidate = _hot_hit(fact, len(hot_hits) + 1)
                    cost = _hit_chars(candidate)
                    if hot_used + cost > hot_cap:
                        omitted.append("hot_budget_omitted")
                        break
                    hot_hits.append(candidate)
                    hot_used += cost
                if hot_search.truncated:
                    omitted.append("hot_limit_omitted")

            remaining_budget = plan.total_budget - trace_cost - hot_used
            cold_plan = replace(
                plan,
                total_budget=remaining_budget,
                type_caps=MappingProxyType(dict(plan.type_caps)),
                bucket_budgets=MappingProxyType(
                    {
                        **plan.bucket_budgets,
                        "memory": max(0, plan.bucket_budgets["memory"] - hot_used),
                    }
                ),
            )
            if plan.retrieve_mode not in {"skip", "doc"} and query.strip() and remaining_budget > 0:
                tokens, _ = _bounded_query_tokens(query.strip())
                search = await self.memory.search_facts(actor=actor, scope=scope, tokens=tokens, limit=256)
                hot_ids = {hit.object_id for hit in hot_hits}
                candidates = tuple(fact for fact in search.facts if fact.fact_id not in hot_ids)
                if cards is not None:
                    cold = _build_card_pack(
                        cold_plan, candidates, cards, query=query,
                        required=() if connected else required_values, forbidden=forbidden_values,
                        excluded_ids=frozenset(hot_ids),
                    )
                else:
                    cold = build_evidence_pack(
                        cold_plan, candidates, query=query,
                        required=() if connected else required_values, forbidden=forbidden_values,
                    )
                if search.truncated:
                    cold = replace(
                        cold,
                        reason_codes=tuple(dict.fromkeys((*cold.reason_codes, "memory_candidate_truncated"))),
                    )
            else:
                cold = build_evidence_pack(
                    cold_plan,
                    (),
                    query=query,
                    required=required_values,
                    forbidden=forbidden_values,
                )
            if self.personal_graph_enabled and self.graph is not None and cold.hits:
                cap = min(plan.type_caps.get("graph_fact", 0), 8)
                available = cap - (len(graph_projection.relations) if graph_projection is not None else 0)
                if available > 0 and retrieval_bucket_budget(plan, "graph_fact"):
                    pointers: list[MemoryFactPointer] = []
                    for hit in cold.hits[:available]:
                        pointer = await self.memory.fact_pointer(hit.object_id, actor=actor, scope=scope)
                        if (
                            pointer.fact_revision != hit.fact_revision or pointer.subject_id != hit.subject_id
                            or pointer.source_ids != hit.source_refs
                            or pointer.evidence_refs != hit.evidence_refs
                        ):
                            raise OperationError("stale_retrieval")
                        pointers.append(pointer)
                    try:
                        personal_projection = await self.graph.project_self_facts(
                            pointers, actor=actor, scope=scope, limit=available,
                        )
                    except OperationError as exc:
                        if exc.code != "denied":
                            raise
                        try:
                            await self.graph.policy.check(actor, scope, "graph.retrieve")
                        except OperationError as graph_error:
                            if graph_error.code != "denied":
                                raise
                            omitted.append("graph_permission_revoked")
                            personal_projection = None
                        else:
                            raise exc
                    if personal_projection is not None and personal_projection.relations:
                        if graph_projection is None:
                            graph_projection = personal_projection
                        else:
                            graph_projection = replace(
                                graph_projection,
                                relations=(*graph_projection.relations, *personal_projection.relations),
                                paths=(*graph_projection.paths, *personal_projection.paths),
                                nodes_visited=(
                                    graph_projection.nodes_visited + personal_projection.nodes_visited
                                ),
                                edges_examined=(
                                    graph_projection.edges_examined + personal_projection.edges_examined
                                ),
                            )
            context = pack(cold, hits=hot_hits, reasons=omitted)
            if (hot_hits or cold.hits or context.documents or context.graph is not None
                    or temporal_trace is not None):
                await self.memory.store.transaction(
                    lambda db: self.assert_current_transaction(db, actor, scope, context)
                )
            return context
        except OperationError as exc:
            if exc.code == "denied":
                context = permission_denied()
                if context.documents or context.graph is not None:
                    await self.memory.store.transaction(
                        lambda db: self.assert_current_transaction(db, actor, scope, context)
                    )
                return context
            raise

    def assert_current_transaction(
        self,
        db: StoreConnection,
        actor: str,
        scope: Scope,
        result: RetrievalResult | MemoryContextPack,
    ) -> None:
        """Recheck one result inside a caller's durable action transaction.

        This narrow owner hook closes the gap between an async visibility check
        and the model/send intent. It deliberately validates only the exact
        active source refs carried by this result; it never returns fact text.
        """
        if isinstance(result, MemoryContextPack):
            hits = (*result.hot_hits, *result.cold.hits)
            policy_revision = result.policy_revision
            trace = result.temporal_trace
            documents = result.documents
            graph = result.graph
        else:
            if result.pack_state != "nonempty" or not (
                result.hits or result.documents or result.graph is not None
            ):
                raise OperationError("stale_retrieval")
            hits = result.hits
            policy_revision = result.policy_revision
            trace = None
            documents = result.documents
            graph = result.graph
        if documents:
            if self.knowledge is None:
                raise OperationError("stale_retrieval")
            shared = result.shared_documents
            if shared and not self.cross_group_sharing_enabled:
                raise OperationError("stale_retrieval")
            local = tuple(hit for hit in documents if not any(item.hit == hit for item in shared))
            if local:
                self.knowledge.assert_hits_transaction(db, scope=scope, actor=actor, hits=local)
            if shared:
                self.knowledge.assert_shared_hits_transaction(
                    db, actor=actor, target_scope=scope, hits=shared,
                )
        if graph is not None:
            if graph.personal_upload_subjects and not self.personal_graph_enabled:
                raise OperationError("graph_personal_consumer_required")
            if self.graph is None:
                raise OperationError("stale_retrieval")
            self.graph.assert_projection_transaction(db, actor=actor, scope=scope, frozen=graph)
        if not hits and trace is None and (documents or graph is not None):
            return
        current_revision = self.memory.assert_retrieval_actor_transaction(db, actor, scope)
        if policy_revision is not None and current_revision != policy_revision:
            raise OperationError("stale_retrieval")
        if not hits:
            if isinstance(result, MemoryContextPack) and trace is None:
                return
            if trace is None:
                raise OperationError("stale_retrieval")
        if any(
            hit.source_scope != scope
            or hit.visibility_scope != scope
            or hit.state != "active"
            or hit.conflict_state != "none"
            or not hit.source_refs
            or not hit.evidence_refs
            for hit in hits
        ):
            raise OperationError("stale_retrieval")
        for hit in hits:
            if hit.card_projection is not None:
                card = hit.card_projection
                if (card.fact.fact_id != hit.object_id or card.fact.fact_revision != hit.fact_revision
                        or card.fact.subject_id != hit.subject_id or card.fact.predicate != hit.predicate
                        or card.fact.value != hit.value or card.fact.source_ids != hit.source_refs
                        or card.fact.evidence_refs != hit.evidence_refs):
                    raise OperationError("stale_retrieval")
                self.memory.assert_card_projection_transaction(db, actor=actor, scope=scope, card=card)
            elif self.query_planner_enabled:
                self.memory.assert_unclassified_fact_transaction(db, scope=scope, fact_id=hit.object_id)
            self.memory.assert_retrieval_fact_transaction(
                db,
                fact_id=hit.object_id,
                fact_revision=hit.fact_revision,
                scope=scope,
                subject_id=hit.subject_id,
                predicate=hit.predicate,
                value=hit.value,
                source_ids=hit.source_refs,
                evidence_refs=hit.evidence_refs,
            )
            self.memory.assert_retrieval_source_transaction(db, hit.subject_id, hit.source_refs, scope)
        if trace is not None:
            if (
                len(trace.versions) < 2
                or trace.versions[-1].fact_id != trace.head_fact_id
                or any(fact.scope != scope for fact in trace.versions)
                or any(
                    fact.subject_id != trace.versions[-1].subject_id
                    or fact.predicate != trace.versions[-1].predicate
                    or fact.status != ("active" if index == len(trace.versions) - 1 else "superseded")
                    or not fact.source_ids
                    or not fact.evidence_refs
                    for index, fact in enumerate(trace.versions)
                )
                or any(
                    newer.supersedes_fact_id != older.fact_id
                    for older, newer in zip(trace.versions[:-1], trace.versions[1:], strict=True)
                )
            ):
                raise OperationError("stale_retrieval")
            self.memory.assert_temporal_trace_transaction(db, actor=actor, scope=scope, trace=trace)


__all__ = [
    "CardEligibilityPolicy",
    "MemoryContextPack",
    "PackState",
    "RetrievalHit",
    "RetrievalPlan",
    "RetrievalResult",
    "RetrievalService",
    "MemoryTemporalTrace",
    "RetrieveMode",
    "QueryNeed",
    "build_evidence_pack",
    "build_retrieval_plan",
    "card_time_eligible",
    "classify_query_needs",
    "parse_card_anchor_timestamp",
    "retrieval_bucket_budget",
]
