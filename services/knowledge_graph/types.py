"""Types for the lightweight SQLite knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

GraphStatus = Literal["active", "pending", "rejected", "superseded"]

GraphNodeType = Literal[
    "term", "style_expression", "episode", "fact",
    "user", "group", "document_chunk",
]
GraphEdgeType = Literal[
    "term_used_in_context", "term_implies_emotion",
    "style_applies_to_situation", "episode_supports_profile",
    "doc_supports_fact", "user_corrected_bot_about",
]


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
    cross_group_visible: bool = False
    cross_group_enabled_by: str = ""
    cross_group_enabled_at: str = ""
    cross_group_enabled_for_groups: list[str] = field(default_factory=list)
    cross_group_enabled_reason: str = ""

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
            "cross_group_visible": self.cross_group_visible,
            "cross_group_enabled_by": self.cross_group_enabled_by,
            "cross_group_enabled_at": self.cross_group_enabled_at,
            "cross_group_enabled_for_groups": list(self.cross_group_enabled_for_groups),
            "cross_group_enabled_reason": self.cross_group_enabled_reason,
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
    cross_group_visible: bool = False
    cross_group_enabled_by: str = ""
    cross_group_enabled_at: str = ""
    cross_group_enabled_for_groups: list[str] = field(default_factory=list)
    cross_group_enabled_reason: str = ""

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
            "cross_group_visible": self.cross_group_visible,
            "cross_group_enabled_by": self.cross_group_enabled_by,
            "cross_group_enabled_at": self.cross_group_enabled_at,
            "cross_group_enabled_for_groups": list(self.cross_group_enabled_for_groups),
            "cross_group_enabled_reason": self.cross_group_enabled_reason,
        }


@dataclass(frozen=True)
class GraphNodeDraft:
    node_type: str
    source_table: str
    source_id: str
    scope: str
    group_id: str
    label: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdgeDraft:
    edge_type: str
    from_node_id: str
    to_node_id: str
    scope: str
    group_id: str
    confidence: float = 0.5
    evidence_refs: tuple[str, ...] = ()
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GraphNode:
    node_id: str
    node_type: str
    source_table: str
    source_id: str
    scope: str
    group_id: str
    label: str
    properties: dict[str, Any]
    status: str
    created_at: str
    updated_at: str
    cross_group_visible: bool = False
    cross_group_enabled_by: str = ""
    cross_group_enabled_at: str = ""
    cross_group_enabled_for_groups: list[str] = field(default_factory=list)
    cross_group_enabled_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "source_table": self.source_table,
            "source_id": self.source_id,
            "scope": self.scope,
            "group_id": self.group_id,
            "label": self.label,
            "properties": dict(self.properties),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "cross_group_visible": self.cross_group_visible,
            "cross_group_enabled_by": self.cross_group_enabled_by,
            "cross_group_enabled_at": self.cross_group_enabled_at,
            "cross_group_enabled_for_groups": list(self.cross_group_enabled_for_groups),
            "cross_group_enabled_reason": self.cross_group_enabled_reason,
        }


@dataclass(slots=True)
class GraphEdge:
    edge_id: str
    edge_type: str
    from_node_id: str
    to_node_id: str
    scope: str
    group_id: str
    confidence: float
    evidence_refs: list[str]
    properties: dict[str, Any]
    status: str
    created_at: str
    updated_at: str
    cross_group_visible: bool = False
    cross_group_enabled_by: str = ""
    cross_group_enabled_at: str = ""
    cross_group_enabled_for_groups: list[str] = field(default_factory=list)
    cross_group_enabled_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "edge_type": self.edge_type,
            "from_node_id": self.from_node_id,
            "to_node_id": self.to_node_id,
            "scope": self.scope,
            "group_id": self.group_id,
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
            "properties": dict(self.properties),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "cross_group_visible": self.cross_group_visible,
            "cross_group_enabled_by": self.cross_group_enabled_by,
            "cross_group_enabled_at": self.cross_group_enabled_at,
            "cross_group_enabled_for_groups": list(self.cross_group_enabled_for_groups),
            "cross_group_enabled_reason": self.cross_group_enabled_reason,
        }
