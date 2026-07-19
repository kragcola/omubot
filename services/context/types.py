"""Provider-neutral context retrieval types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ContextHitType = Literal["memory_card", "doc_chunk", "graph_fact"]


@dataclass(slots=True)
class ContextProvenance:
    """Minimal evidence pointer carried with every retrievable context item."""

    owner: str = ""
    source_id: str = ""
    source_message_id: str = ""
    captured_at: str = ""
    captured_by: str = ""
    evidence_refs: tuple[str, ...] = ()
    supersedes_id: str = ""
    valid_from: str = ""
    valid_to: str = ""
    entity_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "source_id": self.source_id,
            "source_message_id": self.source_message_id,
            "captured_at": self.captured_at,
            "captured_by": self.captured_by,
            "evidence_refs": list(self.evidence_refs),
            "supersedes_id": self.supersedes_id,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "entity_key": self.entity_key,
        }


@dataclass(slots=True)
class ContextScoreBreakdown:
    """Explain source ranking separately from cross-source fusion."""

    source_score: float = 0.0
    relevance: float | None = None
    importance: float | None = None
    confidence: float | None = None
    recency: float | None = None
    fusion: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        return {
            "source_score": self.source_score,
            "relevance": self.relevance,
            "importance": self.importance,
            "confidence": self.confidence,
            "recency": self.recency,
            "fusion": self.fusion,
        }


@dataclass(slots=True)
class ContextHit:
    """One normalized context item from memory, docs, or graph."""

    id: str
    type: ContextHitType
    content: str
    score: float
    source: str
    title: str = ""
    scope: str = "global"
    scope_id: str = "global"
    status: str = "active"
    retriever: str = ""
    provenance: ContextProvenance | None = None
    score_breakdown: ContextScoreBreakdown | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "content": self.content,
            "score": self.score,
            "source": self.source,
            "title": self.title,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "status": self.status,
            "retriever": self.retriever,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "score_breakdown": (
                self.score_breakdown.to_dict() if self.score_breakdown else None
            ),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ContextPack:
    """Packed prompt text plus the hits that survived the budget.

    ``trace_seed_ids`` is an additive *internal* field: pre-gate real memory
    card ids for TemporalTrace authorization. It is intentionally omitted from
    ``to_dict()`` so external pack payloads stay pre-v1 compatible.

    ``evidence_use_contract`` is additive *internal* pack-state observability
    (euc_v1). Also omitted from ``to_dict()``. Defaults to None for old/fake packs.
    """

    text: str
    hits: list[ContextHit]
    omitted_count: int = 0
    trace_seed_ids: tuple[str, ...] = ()
    evidence_use_contract: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "hits": [hit.to_dict() for hit in self.hits],
            "omitted_count": self.omitted_count,
        }
