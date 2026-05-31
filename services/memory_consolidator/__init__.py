"""MemoryConsolidator package — Phase C dry-run candidates.

Public surface intentionally narrow:

- :class:`MemoryConsolidator` — orchestrator (one dry-run pass)
- :class:`ConsolidatorCandidatesStore` — independent dry-run candidates db
- :class:`Candidate` / :class:`ScanRun` / :class:`RunReport` — typed rows
- :data:`CANDIDATE_DOMAINS` / :data:`CANDIDATE_STATES` — Literal whitelists
"""

from __future__ import annotations

from services.memory_consolidator.consolidator import MemoryConsolidator
from services.memory_consolidator.event_boundary import EventBoundaryDetector
from services.memory_consolidator.feedback_sources import (
    NegativeSignal,
    collect_negative_signals,
    fetch_slang_rejected_drifts,
    fetch_style_feedback_signals,
    fetch_style_rejected_expressions,
)
from services.memory_consolidator.promoter import EpisodePromoter, PromoteResult
from services.memory_consolidator.reflector import (
    ReflectionGenerator,
    ReflectionRunReport,
)
from services.memory_consolidator.store import (
    CandidateFilter,
    CandidateRevision,
    ConsolidatorCandidatesStore,
)
from services.memory_consolidator.types import (
    CANDIDATE_DOMAINS,
    CANDIDATE_SCOPES,
    CANDIDATE_STATES,
    VALID_DECISION_TRANSITIONS,
    Candidate,
    CandidateDomain,
    CandidateScope,
    CandidateState,
    EpisodePayload,
    FactPayload,
    GraphRelationPayload,
    RunReport,
    RunStatus,
    ScanRun,
    SlangPayload,
    StylePayload,
    derive_raw_text,
    normalize_payload,
)

__all__ = [
    "CANDIDATE_DOMAINS",
    "CANDIDATE_SCOPES",
    "CANDIDATE_STATES",
    "VALID_DECISION_TRANSITIONS",
    "Candidate",
    "CandidateDomain",
    "CandidateFilter",
    "CandidateRevision",
    "CandidateScope",
    "CandidateState",
    "ConsolidatorCandidatesStore",
    "EpisodePayload",
    "EpisodePromoter",
    "EventBoundaryDetector",
    "FactPayload",
    "GraphRelationPayload",
    "MemoryConsolidator",
    "NegativeSignal",
    "PromoteResult",
    "ReflectionGenerator",
    "ReflectionRunReport",
    "RunReport",
    "RunStatus",
    "ScanRun",
    "SlangPayload",
    "StylePayload",
    "collect_negative_signals",
    "derive_raw_text",
    "fetch_slang_rejected_drifts",
    "fetch_style_feedback_signals",
    "fetch_style_rejected_expressions",
    "normalize_payload",
]
