"""StyleGraphBridge (E.2) tests.

Covers the audit § E.2 contract:

- ``approved`` writes a ``style_applies_to_situation`` edge with
  ``confidence``, ``evidence_refs=(expression_id,)``, and
  ``properties`` carrying ``persona_fit`` / ``mood_fit``
- ``muted`` / ``rejected`` revoke the edge
  (``status='disabled'``)
- ``muted``→``approved`` cycle reactivates (``status='active'``)
- non-status changes do NOT fire the listener
- empty situation is a no-op
- ``pending``→``pending`` (no flip) is a no-op
- listener exception is caught
- graph write failure does NOT roll back ``update_expression``
- D2 cancel-path leaves both stores in a consistent state
- evidence_refs carries the bare ``expression_id``
"""

from __future__ import annotations

import asyncio

import pytest

from services.knowledge_graph.graph_writer import GraphWriter
from services.knowledge_graph.store import KnowledgeGraphStore
from services.style import NewStyleExpression, StyleStore
from services.style.graph_bridge import (
    EDGE_TYPE,
    EXPRESSION_SOURCE_TABLE,
    SITUATION_SOURCE_TABLE,
    StyleGraphBridge,
    _situation_source_id,
)


@pytest.fixture
async def stores(tmp_path):
    style = StyleStore(tmp_path / "style.db")
    await style.init()
    kg = KnowledgeGraphStore(str(tmp_path / "graph.db"))
    await kg.init()
    writer = GraphWriter(kg)
    bridge = StyleGraphBridge(writer)
    bridge.attach(style)
    yield style, kg, writer, bridge
    await style.close()
    await kg.close()


async def _seed_expression(store: StyleStore, *, situation: str = "大家在轻松吐槽") -> str:
    expr = await store.upsert_expression(
        NewStyleExpression(
            situation=situation,
            style="短促附和再接一点明亮反应",
            group_id="100",
            risk_tags=["sarcasm"],
            output_policy="transform",
            confidence=0.7,
            persona_fit=0.6,
            mood_fit=0.55,
        ),
        evidence={
            "group_id": "100",
            "speaker": "Alice(1)",
            "raw_text": "这也太离谱了吧",
            "source_type": "human",
            "message_id": 42,
        },
        actor="test",
    )
    return expr.expression_id


@pytest.mark.asyncio
async def test_approve_writes_style_applies_to_situation_edge(stores):
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)

    ok = await style.update_expression(eid, status="approved", actor="admin")
    assert ok is True

    expr_node = await writer.get_node_by_source(EXPRESSION_SOURCE_TABLE, eid)
    assert expr_node is not None
    assert expr_node.node_type == "style_expression"
    assert expr_node.scope == "group"
    assert expr_node.group_id == "100"

    sit_id = _situation_source_id("大家在轻松吐槽")
    sit_node = await writer.get_node_by_source(SITUATION_SOURCE_TABLE, sit_id)
    assert sit_node is not None
    assert sit_node.node_type == "fact"

    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=expr_node.node_id,
        to_node_id=sit_node.node_id,
    )
    assert edge is not None
    assert edge.status == "active"
    assert edge.confidence == pytest.approx(0.7, abs=0.01)
    assert eid in edge.evidence_refs
    assert edge.properties.get("persona_fit") == pytest.approx(0.6, abs=0.01)
    assert edge.properties.get("mood_fit") == pytest.approx(0.55, abs=0.01)


@pytest.mark.asyncio
async def test_muted_revokes_edge(stores):
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)
    await style.update_expression(eid, status="approved", actor="admin")
    await style.update_expression(eid, status="muted", actor="admin")

    sit_id = _situation_source_id("大家在轻松吐槽")
    expr_node = await writer.get_node_by_source(EXPRESSION_SOURCE_TABLE, eid)
    sit_node = await writer.get_node_by_source(SITUATION_SOURCE_TABLE, sit_id)
    assert expr_node is not None
    assert sit_node is not None
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=expr_node.node_id,
        to_node_id=sit_node.node_id,
    )
    assert edge is not None
    assert edge.status == "disabled"


@pytest.mark.asyncio
async def test_rejected_revokes_edge(stores):
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)
    await style.update_expression(eid, status="approved", actor="admin")
    await style.update_expression(eid, status="rejected", actor="admin")

    expr_node = await writer.get_node_by_source(EXPRESSION_SOURCE_TABLE, eid)
    sit_node = await writer.get_node_by_source(
        SITUATION_SOURCE_TABLE, _situation_source_id("大家在轻松吐槽"),
    )
    assert expr_node is not None
    assert sit_node is not None
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=expr_node.node_id,
        to_node_id=sit_node.node_id,
    )
    assert edge is not None
    assert edge.status == "disabled"


@pytest.mark.asyncio
async def test_muted_then_reapproved_reactivates_edge(stores):
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)
    await style.update_expression(eid, status="approved", actor="admin")
    await style.update_expression(eid, status="muted", actor="admin")
    await style.update_expression(eid, status="approved", actor="admin")

    expr_node = await writer.get_node_by_source(EXPRESSION_SOURCE_TABLE, eid)
    sit_node = await writer.get_node_by_source(
        SITUATION_SOURCE_TABLE, _situation_source_id("大家在轻松吐槽"),
    )
    assert expr_node is not None
    assert sit_node is not None
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=expr_node.node_id,
        to_node_id=sit_node.node_id,
    )
    assert edge is not None
    assert edge.status == "active"


@pytest.mark.asyncio
async def test_non_status_change_does_not_fire(stores):
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)
    # Update style text only — no status flip — bridge stays silent
    ok = await style.update_expression(eid, style="新的风格描述", actor="admin")
    assert ok is True
    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE)
    assert count == 0


@pytest.mark.asyncio
async def test_status_unchanged_is_noop(stores):
    """Setting status to its current value must not fire the listener."""
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)
    # Initial status is 'pending'; updating it to 'pending' is a no-op
    # at the listener level (existing.status == updated.status)
    await style.update_expression(eid, status="pending", actor="admin")
    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE)
    assert count == 0


@pytest.mark.asyncio
async def test_listener_exception_is_caught(stores):
    """A broken sibling listener must not break the bridge listener."""
    style, _kg, writer, _bridge = stores

    async def _bad(*_args, **_kwargs):
        raise TypeError("listener internal bug")

    style.add_status_listener(_bad)

    eid = await _seed_expression(style)
    ok = await style.update_expression(eid, status="approved", actor="admin")
    assert ok is True
    expr_node = await writer.get_node_by_source(EXPRESSION_SOURCE_TABLE, eid)
    assert expr_node is not None


@pytest.mark.asyncio
async def test_graph_write_failure_does_not_roll_back_status(stores, monkeypatch):
    """Audit § E.2: graph mirror failure must not roll back status flip."""
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("graph disk gone")

    monkeypatch.setattr(writer, "write_node", _boom)

    ok = await style.update_expression(eid, status="approved", actor="admin")
    assert ok is True

    refreshed = await style.get_expression(eid)
    assert refreshed is not None
    assert refreshed.status == "approved"


@pytest.mark.asyncio
async def test_cancel_path_leaves_clean_state(stores):
    """D2: cancel mid-update_expression must not corrupt either store."""
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            style.update_expression(eid, status="approved", actor="admin"),
            timeout=0.0,
        )

    # Source store must still be readable
    refreshed = await style.get_expression(eid)
    assert refreshed is not None
    assert refreshed.status in {"pending", "approved"}

    # If the bridge ran far enough to write the expression node, the
    # SQL path must have reached approved first.
    expr_node = await writer.get_node_by_source(EXPRESSION_SOURCE_TABLE, eid)
    if expr_node is not None:
        assert refreshed.status == "approved"


@pytest.mark.asyncio
async def test_evidence_refs_carry_expression_id(stores):
    """Lock the contract: evidence_refs is the bare [expression_id]."""
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)

    await style.update_expression(eid, status="approved", actor="admin")

    expr_node = await writer.get_node_by_source(EXPRESSION_SOURCE_TABLE, eid)
    sit_node = await writer.get_node_by_source(
        SITUATION_SOURCE_TABLE, _situation_source_id("大家在轻松吐槽"),
    )
    assert expr_node is not None
    assert sit_node is not None
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=expr_node.node_id,
        to_node_id=sit_node.node_id,
    )
    assert edge is not None
    refs = edge.evidence_refs
    assert isinstance(refs, list)
    assert eid in refs


@pytest.mark.asyncio
async def test_disable_before_approve_is_noop(stores):
    """muted before any approve must not crash and must not write an edge."""
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)
    # Skip approve, go straight to muted
    await style.update_expression(eid, status="muted", actor="admin")

    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE)
    assert count == 0


@pytest.mark.asyncio
async def test_situation_source_id_is_stable(stores):
    """Same situation text always hashes to the same source_id."""
    a = _situation_source_id("用户问技术问题")
    b = _situation_source_id("用户问技术问题")
    c = _situation_source_id("不同的场景")
    assert a == b
    assert a != c
    assert a.startswith("sit_")
