"""SlangGraphBridge (E.1) tests.

Covers the audit § E.1 contract:

- ``record_hit`` mirrors a ``term_used_in_group`` edge with
  ``confidence``, ``evidence_refs=(term_id,)``, and ``properties``
  carrying ``usage_count`` + ``last_seen_at``
- repeated hits upsert (no duplicate edge rows; ``usage_count``
  property updates)
- empty ``group_id`` is a no-op (rare; private chat hits)
- listener exception is caught (single broken listener does not
  poison fan-out for siblings)
- graph write failure does NOT roll back ``record_hit`` itself —
  the SQL row is committed before the listener fires
- D2 cancel-path: ``asyncio.wait_for(timeout=0.0)`` mid-record_hit
  leaves the source store readable and consistent
- ``evidence_refs`` carries the bare ``term_id``
"""

from __future__ import annotations

import asyncio

import pytest

from services.knowledge_graph.graph_writer import GraphWriter
from services.knowledge_graph.store import KnowledgeGraphStore
from services.slang.graph_bridge import EDGE_TYPE, SlangGraphBridge
from services.slang.store import SlangStore


@pytest.fixture
async def stores(tmp_path):
    slang = SlangStore(tmp_path / "slang.db")
    await slang.init()
    kg = KnowledgeGraphStore(str(tmp_path / "graph.db"))
    await kg.init()
    writer = GraphWriter(kg)
    bridge = SlangGraphBridge(writer)
    bridge.attach(slang)
    yield slang, kg, writer, bridge
    await slang.close()
    await kg.close()


async def _seed_term(
    store: SlangStore,
    *,
    term: str = "猫饼",
    group_id: str = "g1",
    confidence: float = 0.8,
):
    return await store.create_term(
        term=term,
        meaning="离谱又可爱的操作",
        scope="group",
        group_id=group_id,
        confidence=confidence,
        status="approved",
    )


@pytest.mark.asyncio
async def test_record_hit_writes_term_used_in_group_edge(stores):
    slang, _kg, writer, _bridge = stores
    term = await _seed_term(slang)

    ok = await slang.record_hit(
        term.term_id,
        group_id="g1",
        user_id="u1",
        message_id=1001,
        raw_text="猫饼真猫饼",
    )
    assert ok is True

    term_node = await writer.get_node_by_source("slang_terms", term.term_id)
    assert term_node is not None
    assert term_node.node_type == "term"
    assert term_node.scope == "group"
    assert term_node.group_id == "g1"

    group_node = await writer.get_node_by_source("groups", "g1")
    assert group_node is not None
    assert group_node.node_type == "group"

    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=term_node.node_id,
        to_node_id=group_node.node_id,
    )
    assert edge is not None
    assert edge.status == "active"
    assert edge.confidence == pytest.approx(0.8)
    assert term.term_id in edge.evidence_refs
    assert edge.properties.get("usage_count") == 1
    assert edge.properties.get("last_seen_at")


@pytest.mark.asyncio
async def test_repeated_hits_upsert_same_edge(stores):
    slang, _kg, writer, _bridge = stores
    term = await _seed_term(slang)

    for i in range(3):
        ok = await slang.record_hit(
            term.term_id,
            group_id="g1",
            user_id="u1",
            message_id=2000 + i,
        )
        assert ok is True

    # Only one row regardless of how many hits
    edges, count = await writer.list_edges(edge_type=EDGE_TYPE, status="active")
    assert count == 1
    edge = edges[0]
    # usage_count is monotonic; last fire wins
    assert edge.properties.get("usage_count") == 3


@pytest.mark.asyncio
async def test_empty_group_id_is_skipped(stores):
    """Hit without a group anchor is a noop on the graph side."""
    slang, _kg, writer, _bridge = stores
    term = await _seed_term(slang)

    ok = await slang.record_hit(
        term.term_id, group_id="", user_id="u1", message_id=3001,
    )
    # The SQL row updates regardless (record_observation handles empty
    # group_id), but the bridge skipped the graph write.
    assert ok is True

    term_node = await writer.get_node_by_source("slang_terms", term.term_id)
    assert term_node is None
    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE)
    assert count == 0


@pytest.mark.asyncio
async def test_listener_exception_is_caught(stores):
    """A broken sibling listener must not break the bridge listener."""
    slang, _kg, writer, _bridge = stores

    async def _bad(*_args, **_kwargs):
        raise TypeError("listener internal bug")

    slang.add_hit_listener(_bad)

    term = await _seed_term(slang)
    ok = await slang.record_hit(
        term.term_id, group_id="g1", user_id="u1", message_id=4001,
    )
    assert ok is True

    # Bridge ran despite the broken sibling listener
    term_node = await writer.get_node_by_source("slang_terms", term.term_id)
    assert term_node is not None


@pytest.mark.asyncio
async def test_graph_write_failure_does_not_block_record_hit(stores, monkeypatch):
    """Audit § E.1: graph mirror failure must not roll back record_hit."""
    slang, _kg, writer, _bridge = stores
    term = await _seed_term(slang)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("graph disk gone")

    monkeypatch.setattr(writer, "write_node", _boom)

    ok = await slang.record_hit(
        term.term_id, group_id="g1", user_id="u1", message_id=5001,
    )
    assert ok is True  # SQL path committed before bridge fired

    # Term-side state advanced
    refreshed = await slang.get_term(term.term_id)
    assert refreshed is not None
    assert refreshed.usage_count == 1


@pytest.mark.asyncio
async def test_cancel_path_leaves_clean_state(stores):
    """D2: cancel mid-record_hit must not corrupt either store."""
    slang, _kg, writer, _bridge = stores
    term = await _seed_term(slang)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            slang.record_hit(
                term.term_id,
                group_id="g1",
                user_id="u1",
                message_id=6001,
            ),
            timeout=0.0,
        )

    # Source store must remain readable; whatever the SQL path
    # committed before cancel must be self-consistent
    refreshed = await slang.get_term(term.term_id)
    assert refreshed is not None
    assert refreshed.usage_count in {0, 1}

    # Either the bridge ran far enough to write the term node, or it
    # didn't. If it did, the SQL path must have advanced too.
    term_node = await writer.get_node_by_source("slang_terms", term.term_id)
    if term_node is not None:
        assert refreshed.usage_count == 1


@pytest.mark.asyncio
async def test_evidence_refs_carry_term_id(stores):
    """Lock the contract: evidence_refs is the bare [term_id]."""
    slang, _kg, writer, _bridge = stores
    term = await _seed_term(slang)

    await slang.record_hit(
        term.term_id, group_id="g1", user_id="u1", message_id=7001,
    )

    term_node = await writer.get_node_by_source("slang_terms", term.term_id)
    group_node = await writer.get_node_by_source("groups", "g1")
    assert term_node is not None
    assert group_node is not None
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=term_node.node_id,
        to_node_id=group_node.node_id,
    )
    assert edge is not None
    refs = edge.evidence_refs
    assert isinstance(refs, list)
    assert term.term_id in refs


@pytest.mark.asyncio
async def test_muted_term_record_hit_skips_listener(stores):
    """A muted term short-circuits record_hit before the listener fires."""
    slang, _kg, writer, _bridge = stores
    term = await store_set_status_muted(slang)

    ok = await slang.record_hit(
        term.term_id, group_id="g1", user_id="u1", message_id=8001,
    )
    assert ok is False

    term_node = await writer.get_node_by_source("slang_terms", term.term_id)
    assert term_node is None  # listener never ran


async def store_set_status_muted(store: SlangStore):
    """Helper: seed a term and flip its status to muted."""
    term = await store.create_term(
        term="过气梗",
        meaning="不再用了",
        scope="group",
        group_id="g1",
        confidence=0.8,
        status="approved",
    )
    db = store._require_db()
    await db.execute(
        "UPDATE slang_terms SET status = 'muted' WHERE term_id = ?",
        (term.term_id,),
    )
    await db.commit()
    refreshed = await store.get_term(term.term_id)
    assert refreshed is not None
    return refreshed
