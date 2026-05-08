"""Provider-neutral context retrieval types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ContextHitType = Literal["memory_card", "doc_chunk", "graph_fact"]


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
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ContextPack:
    """Packed prompt text plus the hits that survived the budget."""

    text: str
    hits: list[ContextHit]
    omitted_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "hits": [hit.to_dict() for hit in self.hits],
            "omitted_count": self.omitted_count,
        }
