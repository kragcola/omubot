"""FactGraphBridge (E.4) tests.

Covers the audit § E.4 contract:

- Doc-backed fact (``evidence['type']='doc_chunk'`` + ``chunk_id``)
  writes a ``doc_supports_fact`` edge with ``confidence=fact.confidence``,
  ``evidence_refs=(fact_id,)``, and ``properties`` carrying ``quote``
  and ``fact_confidence``.
- Memory-card-backed fact (``evidence['type']='memory_card'``) is a
  no-op — the edge type is *document support* specifically.
- Empty / missing ``chunk_id`` is a no-op.
- ``submit_fact_candidate`` direct-active branch (confidence >= 0.85)
  fires the listener.
- ``approve_candidate`` (candidate → active) also fires the listener.
- Pending candidate (0.60 <= confidence < 0.85) does NOT fire the
  listener — only ``GraphFact`` instantiation should.
- Listener exception in a sibling listener doesn't break the bridge.
- Graph write failure does NOT roll back the fact SQL commit.
- D2 cancel-path leaves both stores in a consistent state.
- ``evidence_refs`` carries the bare ``fact_id``.
"""

from __future__ import annotations

import asyncio

import pytest

from services.knowledge_graph.fact_graph_bridge import (
    CHUNK_SOURCE_TABLE,
    EDGE_TYPE,
    FACT_SOURCE_TABLE,
    FactGraphBridge,
)
from services.knowledge_graph.graph_writer import GraphWriter
from services.knowledge_graph.service import KnowledgeGraphService


@pytest.fixture
async def stack(tmp_path):
    service = KnowledgeGraphService(str(tmp_path / "kg.db"))
    await service.init()
    writer = GraphWriter(service._store)
    bridge = FactGraphBridge(writer)
    bridge.attach(service)
    yield service, writer, bridge
    await service.close()


def _doc_evidence(chunk_id: str = "chunk_1", quote: str = "示例引文") -> dict:
    return {
        "type": "doc_chunk",
        "chunk_id": chunk_id,
        "id": chunk_id,
        "quote": quote,
    }


@pytest.mark.asyncio
async def test_high_confidence_doc_fact_writes_edge(stack):
    service, writer, _bridge = stack
    fact = await service.submit_fact_candidate(
        subject="主播", predicate="喜欢", object="奶茶",
        confidence=0.9,
        source="extractor",
        evidence=_doc_evidence("chunk_1", "她说她很喜欢奶茶"),
    )
    assert fact is not None
    fact_node = await writer.get_node_by_source(FACT_SOURCE_TABLE, fact.fact_id)
    chunk_node = await writer.get_node_by_source(CHUNK_SOURCE_TABLE, "chunk_1")
    assert fact_node is not None
    assert fact_node.node_type == "fact"
    assert chunk_node is not None
    assert chunk_node.node_type == "document_chunk"
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=fact_node.node_id,
        to_node_id=chunk_node.node_id,
    )
    assert edge is not None
    assert edge.status == "active"
    assert fact.fact_id in edge.evidence_refs
    assert edge.confidence == pytest.approx(0.9, abs=0.01)
    assert edge.properties.get("quote") == "她说她很喜欢奶茶"
    assert edge.properties.get("fact_confidence") == pytest.approx(0.9, abs=0.01)


@pytest.mark.asyncio
async def test_memory_card_evidence_skipped(stack):
    service, writer, _bridge = stack
    await service.submit_fact_candidate(
        subject="主播", predicate="喜欢", object="奶茶",
        confidence=0.9,
        source="extractor",
        evidence={"type": "memory_card", "card_id": "card_1", "id": "card_1", "quote": "..."},
    )
    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE)
    assert count == 0


@pytest.mark.asyncio
async def test_missing_chunk_id_is_noop(stack):
    """type='doc_chunk' but empty chunk_id — bridge must skip cleanly."""
    service, writer, _bridge = stack
    # add_fact requires evidence id, so use a card-style id but mark type as doc_chunk
    await service.submit_fact_candidate(
        subject="主播", predicate="去过", object="日本",
        confidence=0.95,
        source="extractor",
        # type=doc_chunk but no chunk_id field — bridge should skip
        evidence={"type": "doc_chunk", "id": "fallback_id", "quote": ""},
    )
    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE)
    assert count == 0


@pytest.mark.asyncio
async def test_pending_candidate_does_not_fire(stack):
    """Confidence 0.60 ≤ x < 0.85 produces a candidate, not a fact — listener stays silent."""
    service, writer, _bridge = stack
    result = await service.submit_fact_candidate(
        subject="主播", predicate="说过", object="想吃寿司",
        confidence=0.7,
        source="extractor",
        evidence=_doc_evidence("chunk_pending"),
    )
    # Candidate, not fact
    from services.knowledge_graph.types import GraphCandidate
    assert isinstance(result, GraphCandidate)
    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE)
    assert count == 0


@pytest.mark.asyncio
async def test_approve_candidate_fires_listener(stack):
    """candidate → active flip via approve_candidate must mirror the edge."""
    service, writer, _bridge = stack
    candidate = await service.submit_fact_candidate(
        subject="主播", predicate="喝过", object="拿铁",
        confidence=0.7,
        source="extractor",
        evidence=_doc_evidence("chunk_approve"),
    )
    fact = await service.approve_candidate(candidate.candidate_id)
    assert fact is not None
    fact_node = await writer.get_node_by_source(FACT_SOURCE_TABLE, fact.fact_id)
    chunk_node = await writer.get_node_by_source(CHUNK_SOURCE_TABLE, "chunk_approve")
    assert fact_node is not None
    assert chunk_node is not None
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=fact_node.node_id,
        to_node_id=chunk_node.node_id,
    )
    assert edge is not None


@pytest.mark.asyncio
async def test_listener_exception_is_caught(stack):
    """A broken sibling listener must not break the bridge listener."""
    service, writer, _bridge = stack

    async def _bad(_fact, _evidence):
        raise TypeError("listener internal bug")

    service.add_fact_listener(_bad)

    fact = await service.submit_fact_candidate(
        subject="主播", predicate="喜欢", object="奶茶",
        confidence=0.9,
        source="extractor",
        evidence=_doc_evidence("chunk_safe"),
    )
    assert fact is not None
    fact_node = await writer.get_node_by_source(FACT_SOURCE_TABLE, fact.fact_id)
    assert fact_node is not None


@pytest.mark.asyncio
async def test_graph_write_failure_does_not_roll_back_fact(stack, monkeypatch):
    """Audit § E.4: graph mirror failure must not roll back the fact."""
    service, writer, _bridge = stack

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("graph disk gone")

    monkeypatch.setattr(writer, "write_node", _boom)

    fact = await service.submit_fact_candidate(
        subject="主播", predicate="喜欢", object="奶茶",
        confidence=0.9,
        source="extractor",
        evidence=_doc_evidence("chunk_corrupt"),
    )
    assert fact is not None
    # Source-of-truth fact row must still be queryable
    refreshed = await service.get_relationship(fact.fact_id)
    assert refreshed is not None


@pytest.mark.asyncio
async def test_cancel_path_leaves_clean_state(stack):
    """D2: cancel mid-submit_fact_candidate must not corrupt either store."""
    service, writer, _bridge = stack

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            service.submit_fact_candidate(
                subject="主播", predicate="喜欢", object="奶茶",
                confidence=0.9,
                source="extractor",
                evidence=_doc_evidence("chunk_cancel"),
            ),
            timeout=0.0,
        )

    # If the bridge ran far enough to write the fact node, the SQL
    # commit must have completed first (listener fires after add_fact).
    fact_nodes, _ = await writer.list_nodes(node_type="fact")
    chunk_nodes, _ = await writer.list_nodes(node_type="document_chunk")
    if fact_nodes:
        # The fact node must have a corresponding source-of-truth row
        for node in fact_nodes:
            refreshed = await service.get_relationship(node.source_id)
            assert refreshed is not None
    # Bridge mirrors fact + chunk together — if fact node exists, chunk
    # node must too (or both must not exist)
    assert (len(fact_nodes) > 0) == (len(chunk_nodes) > 0)


@pytest.mark.asyncio
async def test_evidence_refs_carry_fact_id(stack):
    """Lock the contract: evidence_refs is the bare [fact_id]."""
    service, writer, _bridge = stack
    fact = await service.submit_fact_candidate(
        subject="主播", predicate="去过", object="北京",
        confidence=0.9,
        source="extractor",
        evidence=_doc_evidence("chunk_refs"),
    )
    assert fact is not None
    fact_node = await writer.get_node_by_source(FACT_SOURCE_TABLE, fact.fact_id)
    chunk_node = await writer.get_node_by_source(CHUNK_SOURCE_TABLE, "chunk_refs")
    assert fact_node is not None
    assert chunk_node is not None
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=fact_node.node_id,
        to_node_id=chunk_node.node_id,
    )
    assert edge is not None
    refs = edge.evidence_refs
    assert isinstance(refs, list)
    assert fact.fact_id in refs


@pytest.mark.asyncio
async def test_repeated_facts_upsert_same_edge(stack):
    """Two doc facts about the *same* (fact_id, chunk) only produce one edge."""
    service, writer, _bridge = stack
    fact = await service.submit_fact_candidate(
        subject="主播", predicate="喜欢", object="奶茶",
        confidence=0.9,
        source="extractor",
        evidence=_doc_evidence("chunk_idem", "Q1"),
    )
    assert fact is not None
    # Same triple → submit returns the existing fact (no new fact_id)
    same = await service.submit_fact_candidate(
        subject="主播", predicate="喜欢", object="奶茶",
        confidence=0.95,
        source="extractor",
        evidence=_doc_evidence("chunk_idem", "Q2"),
    )
    assert same is not None
    assert same.fact_id == fact.fact_id
    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE)
    # Either zero or one edge (depending on whether the dedup short-circuit
    # fires the listener); never two
    assert count <= 1
