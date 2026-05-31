"""Base protocol and types for AI candidate reviewers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AggressivenessConfig:
    auto_approve_min_confidence: float = 0.72
    auto_reject_max_confidence: float = 0.50
    kept_streak_limit: int = 3
    concurrency: int = 20

    @classmethod
    def from_level(cls, level: str, concurrency: int = 20) -> AggressivenessConfig:
        if level == "conservative":
            return cls(
                auto_approve_min_confidence=0.85, auto_reject_max_confidence=0.40,
                kept_streak_limit=5, concurrency=concurrency,
            )
        if level == "aggressive":
            return cls(
                auto_approve_min_confidence=0.60, auto_reject_max_confidence=0.55,
                kept_streak_limit=2, concurrency=concurrency,
            )
        return cls(concurrency=concurrency)


@dataclass
class ReviewState:
    active: bool = False
    processed: int = 0
    approved: int = 0
    rejected: int = 0
    kept: int = 0
    total_at_start: int = 0
    remaining: int = 0
    started_at: str = ""
    last_progress_at: str = ""
    last_done_at: str = ""


@dataclass
class ReviewBatchResult:
    ok: bool = True
    error: str = ""
    processed_in_batch: int = 0
    approved_in_batch: int = 0
    rejected_in_batch: int = 0
    kept_in_batch: int = 0
    remaining: int = 0
    completed: bool = False
    total_at_start: int = 0


@dataclass
class CandidateItem:
    id: str
    domain: str
    content: str
    context: str = ""
    group_id: str = ""
    confidence: float = 0.5
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewVerdict:
    decision: str  # "approved" | "rejected" | "kept"
    confidence: float = 0.5
    reason: str = ""
    improved_content: str = ""


class ReviewerBase(Protocol):
    """Protocol that all noun-specific reviewers must implement."""

    domain: str

    async def get_state(self) -> ReviewState: ...

    async def reset_state(self) -> None: ...

    async def count_pending(self, config: AggressivenessConfig) -> int: ...

    async def run_one_batch(
        self,
        *,
        batch_size: int,
        config: AggressivenessConfig,
        llm_client: Any,
    ) -> ReviewBatchResult: ...
