"""FactGraphBridge — Phase E.4 graph edge double-write.

Listens to ``KnowledgeGraphService`` fact creations (both the
``submit_fact_candidate(..., promote_directly=True)`` privileged path
and the ``approve_candidate`` branch) and mirrors **doc-backed** facts
into the knowledge graph as ``doc_supports_fact`` edges
(audit ``docs/audits/multilayer-memory-phase-e-design-audit-2026-05-21.md``
§ E.4).

Design notes:

- Triggered only when ``evidence['type'] == 'doc_chunk'`` and
  ``evidence['chunk_id']`` is non-empty. Memory-card-backed facts
  (``card_id``) are not in scope for this edge type — the audit
  contract is *document support* specifically.
- Edge shape: ``edge_type='doc_supports_fact'``,
  ``from_node`` = fact node
  (``source_table='graph_facts'``, ``node_type='fact'``),
  ``to_node`` = chunk node
  (``source_table='knowledge_chunks'``, ``node_type='document_chunk'``).
- ``evidence_refs=(fact_id,)``; ``properties`` carry ``quote`` (the
  evidence excerpt) and ``fact_confidence`` so the consolidator can
  render attribution without joining back to ``graph_facts``.
- All graph writes are best-effort; failures are caught here and
  logged WARN. The fan-out wrapper in
  ``KnowledgeGraphService._fire_fact_listeners`` is the second layer
  of suppression.
- No revoke path: when a fact is rejected/superseded, the source
  ``graph_facts`` row carries the new status; we don't try to flip the
  edge on every governance change. The consolidator is expected to
  filter through the source-of-truth row, not the projection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from services.knowledge_graph.dual_write import ensure_graph_node
from services.knowledge_graph.graph_writer import GraphWriter
from services.knowledge_graph.types import GraphEdgeDraft

if TYPE_CHECKING:
    from services.knowledge_graph.service import KnowledgeGraphService
    from services.knowledge_graph.types import GraphFact

_L = logger.bind(channel="fact_graph")

FACT_SOURCE_TABLE = "graph_facts"
CHUNK_SOURCE_TABLE = "knowledge_chunks"
EDGE_TYPE = "doc_supports_fact"
DOC_EVIDENCE_TYPE = "doc_chunk"


class FactGraphBridge:
    """Mirrors doc-backed graph facts into ``doc_supports_fact`` edges."""

    def __init__(self, writer: GraphWriter) -> None:
        self._writer = writer

    def attach(self, service: KnowledgeGraphService) -> None:
        """Subscribe to ``service``'s fact listener fan-out."""
        service.add_fact_listener(self._on_fact)

    async def _on_fact(self, fact: GraphFact, evidence: dict[str, Any]) -> None:
        if not evidence:
            return
        if evidence.get("type") != DOC_EVIDENCE_TYPE:
            return
        chunk_id = str(evidence.get("chunk_id") or "").strip()
        if not chunk_id:
            return
        try:
            await self._upsert_edge(fact, evidence, chunk_id)
        except Exception as exc:
            _L.warning(
                "doc_supports_fact edge upsert failed | "
                "fact={} chunk={} err={}",
                fact.fact_id, chunk_id, exc,
            )

    async def _upsert_edge(
        self,
        fact: GraphFact,
        evidence: dict[str, Any],
        chunk_id: str,
    ) -> None:
        scope = fact.scope or "global"
        group_id = fact.scope_id if scope == "group" else ""
        fact_label = f"{fact.subject} {fact.predicate} {fact.object}"[:80]
        fact_node_id = await ensure_graph_node(
            self._writer,
            node_type="fact",
            source_table=FACT_SOURCE_TABLE,
            source_id=fact.fact_id,
            scope=scope,
            group_id=group_id,
            label=fact_label,
            properties={
                "subject": fact.subject,
                "predicate": fact.predicate,
                "object": fact.object,
                "confidence": fact.confidence,
                "status": fact.status,
            },
        )
        chunk_node_id = await ensure_graph_node(
            self._writer,
            node_type="document_chunk",
            source_table=CHUNK_SOURCE_TABLE,
            source_id=chunk_id,
            scope=scope,
            group_id=group_id,
            label=chunk_id[:80],
            properties={"placeholder_for_chunk_id": chunk_id},
        )
        draft = GraphEdgeDraft(
            edge_type=EDGE_TYPE,
            from_node_id=fact_node_id,
            to_node_id=chunk_node_id,
            scope=scope,
            group_id=group_id,
            confidence=float(fact.confidence),
            evidence_refs=(fact.fact_id,),
            properties={
                "quote": str(evidence.get("quote") or "")[:240],
                "fact_confidence": float(fact.confidence),
            },
        )
        await self._writer.write_edge(draft)
        _L.info(
            "doc_supports_fact edge written | fact={} chunk={}",
            fact.fact_id, chunk_id,
        )
