"""Unified context retrieval service."""

from services.context.eval import (
    ContextEvalCase,
    ContextEvalResult,
    ContextEvalSummary,
    ContextHitExpectation,
    TemporalTraceExpectation,
    evaluate_context_case,
    evaluate_context_cases,
    load_context_eval_cases,
)
from services.context.query_plan import (
    CLOSED_NEEDS,
    PLAN_VERSION,
    QueryAwarePlan,
    QueryNeed,
    plan_query_aware_retrieval,
    sanitize_plan_meta,
)
from services.context.service import ContextService
from services.context.sources import GraphContextSource, KnowledgeContextSource, MemoryContextSource
from services.context.types import (
    ContextHit,
    ContextHitType,
    ContextPack,
    ContextProvenance,
    ContextScoreBreakdown,
)

__all__ = [
    "CLOSED_NEEDS",
    "PLAN_VERSION",
    "ContextEvalCase",
    "ContextEvalResult",
    "ContextEvalSummary",
    "ContextHit",
    "ContextHitExpectation",
    "ContextHitType",
    "ContextPack",
    "ContextProvenance",
    "ContextScoreBreakdown",
    "ContextService",
    "GraphContextSource",
    "KnowledgeContextSource",
    "MemoryContextSource",
    "QueryAwarePlan",
    "QueryNeed",
    "TemporalTraceExpectation",
    "evaluate_context_case",
    "evaluate_context_cases",
    "load_context_eval_cases",
    "plan_query_aware_retrieval",
    "sanitize_plan_meta",
]
