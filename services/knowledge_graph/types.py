"""Types for the lightweight SQLite knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

GraphStatus = Literal["active", "pending", "rejected", "superseded"]


@dataclass(slots=True)
class GraphFact:
    fact_id: str
    subject: str
    predicate: str
    object: str
    confidence: float
    status: GraphStatus
    source: str
    created_at: str
    updated_at: str
    scope: str = "global"
    scope_id: str = "global"
    supersedes: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "confidence": self.confidence,
            "status": self.status,
            "source": self.source,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "supersedes": self.supersedes,
            "metadata": dict(self.metadata),
            "evidence": [dict(item) for item in self.evidence],
        }


@dataclass(slots=True)
class GraphCandidate:
    candidate_id: str
    subject: str
    predicate: str
    object: str
    confidence: float
    status: GraphStatus
    source: str
    evidence: dict[str, Any]
    created_at: str
    updated_at: str
    scope: str = "global"
    scope_id: str = "global"
    review_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "confidence": self.confidence,
            "status": self.status,
            "source": self.source,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "evidence": dict(self.evidence),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "review_note": self.review_note,
        }
