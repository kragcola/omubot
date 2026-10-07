"""Closed, body-free observations of actual request context construction."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from .types import StrictModel

ContextRole = Literal["context_main", "temporal_trace", "context_constrained", "social_episode"]
ContextDecision = Literal["accepted", "trimmed", "rejected"]
ContextAvailability = Literal["available", "missing", "unknown"]
ObservedPackState = Literal["skip", "empty", "nonempty", "omit_only"]
ContextNeed = Literal[
    "ordinary_fact", "preference", "temporal_current", "temporal_earlier", "premise_check",
    "relation_multihop", "broad_recall", "doc_grounding",
]
ContextProfile = Literal[
    "ordinary_identity", "preference_memory_v1", "temporal_current_memory_v1",
    "temporal_earlier_memory_v1", "premise_check_memory_v1", "relation_graph_v1",
    "doc_grounding_v1", "broad_recall_clamp_v1",
]
CONTEXT_ROLES: tuple[ContextRole, ...] = (
    "context_main", "temporal_trace", "context_constrained", "social_episode",
)


class ContextTypeCaps(StrictModel):
    memory_fact: int
    memory_card: int
    document: int
    graph_fact: int


class ContextBuckets(StrictModel):
    memory: int
    doc: int
    graph: int


class ContextPlanObservation(StrictModel):
    needs: tuple[ContextNeed, ...]
    profile: ContextProfile
    mode: Literal["skip", "doc", "fact", "hybrid"]
    caps: ContextTypeCaps
    buckets: ContextBuckets
    enabled: bool
    identity: bool
    budget: int


class ContextPackObservation(StrictModel):
    state: ObservedPackState | None
    hot_count: int
    cold_count: int
    card_count: int
    document_count: int
    graph_count: int
    temporal_count: int | None
    total_budget: int
    used_budget: int
    cold_used_budget: int


class ContextPathObservation(StrictModel):
    role: ContextRole
    availability: ContextAvailability
    item_count: int | None
    decision: ContextDecision | None


class ContextBudgetObservation(StrictModel):
    outcome: Literal["accepted", "rejected"]
    character_limit: int
    built_characters: int
    final_characters: int
    removed_messages: int


class ContextObservation(StrictModel):
    plan: ContextPlanObservation | None
    pack: ContextPackObservation | None
    paths: tuple[ContextPathObservation, ...]
    budget: ContextBudgetObservation


class ContextPackStateCounts(StrictModel):
    skip: int = 0
    empty: int = 0
    nonempty: int = 0
    omit_only: int = 0
    unknown: int = 0


class ContextPathTotals(StrictModel):
    role: ContextRole
    accepted: int = 0
    trimmed: int = 0
    rejected: int = 0
    missing: int = 0
    unknown: int = 0


class ContextObservationSnapshot(StrictModel):
    enabled: bool
    sample_count: int
    recent: tuple[ContextObservation, ...]
    pack_state_counts: ContextPackStateCounts
    path_totals: tuple[ContextPathTotals, ...]


def context_observation_snapshot(
    records: Sequence[ContextObservation], *, enabled: bool,
) -> ContextObservationSnapshot:
    """Aggregate existing observations; never infer missing source counts."""
    recent = tuple(records) if enabled else ()
    states = dict.fromkeys(("skip", "empty", "nonempty", "omit_only", "unknown"), 0)
    totals = {
        role: dict.fromkeys(("accepted", "trimmed", "rejected", "missing", "unknown"), 0)
        for role in CONTEXT_ROLES
    }
    for record in recent:
        state = record.pack.state if record.pack is not None else None
        states[state or "unknown"] += 1
        for path in record.paths:
            if path.decision is not None:
                totals[path.role][path.decision] += 1
            elif path.availability != "available":
                totals[path.role][path.availability] += 1
    return ContextObservationSnapshot(
        enabled=enabled, sample_count=len(recent), recent=tuple(reversed(recent)),
        pack_state_counts=ContextPackStateCounts(**states),
        path_totals=tuple(ContextPathTotals(role=role, **totals[role]) for role in CONTEXT_ROLES),
    )
