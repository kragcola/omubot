"""BlockTraceBus — prompt-block tracing and budget management."""

from services.block_trace.budget_manager import PromptBudgetManager
from services.block_trace.episode_provider import EpisodeProvider
from services.block_trace.providers import ContextProvider, QueryContext
from services.block_trace.slang_provider import SlangProvider
from services.block_trace.store import BlockTraceStore
from services.block_trace.style_provider import StyleProvider
from services.block_trace.types import (
    BudgetDecision,
    PromptBlockCandidate,
    PromptBlockTrace,
    PromptLayer,
    PromptSource,
)

# `BlockTraceBus` is the architectural name from the multilayer-memory plan
# (docs/audits/multilayer-memory-learning-report-2026-05-17.md § 10.2).
# `BlockTraceStore` is the concrete SQLite-backed implementation that fulfills
# the BlockTraceBus protocol (record / list_for_request / find_by_source_ref).
# Export the alias so callers can write to the architectural name.
BlockTraceBus = BlockTraceStore

__all__ = [
    "BlockTraceBus",
    "BlockTraceStore",
    "BudgetDecision",
    "ContextProvider",
    "EpisodeProvider",
    "PromptBlockCandidate",
    "PromptBlockTrace",
    "PromptBudgetManager",
    "PromptLayer",
    "PromptSource",
    "QueryContext",
    "SlangProvider",
    "StyleProvider",
]
