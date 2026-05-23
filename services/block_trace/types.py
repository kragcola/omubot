"""Types for the BlockTraceBus prompt-block tracing system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

BudgetDecision = Literal["accepted", "trimmed", "rejected", "shadow_only"]

PromptLayer = Literal["core", "stable", "dynamic", "tail", "tool_hint"]

PromptSource = Literal[
    "slang", "style", "memory", "knowledge", "graph",
    "episode", "declarative_fact", "context", "schedule",
    "affection", "sticker", "food", "system",
]


@dataclass(frozen=True)
class PromptBlockCandidate:
    candidate_id: str
    source: str
    provider: str
    layer: PromptLayer
    label: str
    text: str
    priority: int
    position: str
    scope: str
    group_id: str
    hit_reason: str
    char_count: int
    evidence_refs: tuple[str, ...] = ()
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AcceptedDecision:
    candidate_id: str
    source: str
    provider: str
    evidence_refs: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    char_count: int = 0
    group_id: str = ""
    scope: str = ""
    hit_reason: str = ""
    label: str = ""
    decision: BudgetDecision = "accepted"


@dataclass(frozen=True)
class PromptBlockTrace:
    trace_id: str
    request_id: str
    task: str
    source: str
    provider: str
    candidate_id: str
    decision: BudgetDecision
    hit_reason: str
    evidence_refs: tuple[str, ...]
    token_estimate: int
    char_count: int
    position: str
    label: str
    priority: int = 100
    decay_state: str = ""
    budget_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "task": self.task,
            "source": self.source,
            "provider": self.provider,
            "candidate_id": self.candidate_id,
            "decision": self.decision,
            "hit_reason": self.hit_reason,
            "evidence_refs": list(self.evidence_refs),
            "token_estimate": self.token_estimate,
            "char_count": self.char_count,
            "position": self.position,
            "label": self.label,
            "priority": self.priority,
            "decay_state": self.decay_state,
            "budget_reason": self.budget_reason,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }
