"""Types for the lightweight document knowledge service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class KnowledgeChunk:
    """One indexed document chunk."""

    chunk_id: str
    title: str
    content: str
    source: str
    source_path: str
    source_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class KnowledgeHit:
    """Structured search result returned by KnowledgeService."""

    chunk_id: str
    content: str
    source: str
    title: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.chunk_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.chunk_id,
            "chunk_id": self.chunk_id,
            "content": self.content,
            "source": self.source,
            "title": self.title,
            "score": self.score,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class KnowledgeSourceStatus:
    """Indexing status for a source file."""

    source: str
    path: str
    status: str
    chunk_count: int = 0
    source_hash: str = ""
    skipped_reason: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "path": self.path,
            "status": self.status,
            "chunk_count": self.chunk_count,
            "source_hash": self.source_hash,
            "skipped_reason": self.skipped_reason,
            "updated_at": self.updated_at,
        }
