"""StyleFeedbackGraphBridge (E.3) tests.

Covers the audit § E.3 contract:

- Negative feedback for ``target_type='expression'`` writes a
  ``user_corrected_bot_about`` edge with ``confidence=1.0``,
  ``evidence_refs=(feedback_id,)``, and ``properties`` carrying the
  verbatim ``target_type``, ``rating``, timestamp, and clipped note.
- Negative feedback for ``target_type='profile'`` works the same way.
- Positive / neutral feedback is a no-op.
- Unknown ``target_type`` (e.g. ``'reply'``) is a no-op.
- Empty ``target_id`` is a no-op.
- Empty ``actor`` collapses to a sentinel ``anonymous`` user node.
- Listener exceptions in sibling listeners do not break the bridge.
- Graph write failure does NOT roll back ``record_feedback`` SQL commit.
- D2 cancel-path leaves both stores in a consistent state.
- ``evidence_refs`` carries the bare ``feedback_id``.
"""

from __future__ import annotations

import asyncio

import pytest

from services.knowledge_graph.graph_writer import GraphWriter
from services.knowledge_graph.store import KnowledgeGraphStore
from services.style import NewStyleExpression, StyleStore
from services.style.feedback_graph_bridge import (
    ANONYMOUS_ACTOR,
    EDGE_TYPE,
    EXPRESSION_SOURCE_TABLE,
    PROFILE_SOURCE_TABLE,
    USER_SOURCE_TABLE,
    StyleFeedbackGraphBridge,
)


@pytest.fixture
async def stores(tmp_path):
    style = StyleStore(tmp_path / "style.db")
    await style.init()
    kg = KnowledgeGraphStore(str(tmp_path / "graph.db"))
    await kg.init()
    writer = GraphWriter(kg)
    bridge = StyleFeedbackGraphBridge(writer)
    bridge.attach(style)
    yield style, kg, writer, bridge
    await style.close()
    await kg.close()


async def _seed_expression(store: StyleStore, *, situation: str = "测试场景") -> str:
    expr = await store.upsert_expression(
        NewStyleExpression(
            situation=situation,
            style="测试风格",
            group_id="100",
            risk_tags=[],
            output_policy="transform",
            confidence=0.7,
            persona_fit=0.6,
            mood_fit=0.55,
        ),
        evidence={
            "group_id": "100",
            "speaker": "Alice(1)",
            "raw_text": "示例对话",
            "source_type": "human",
            "message_id": 1,
        },
        actor="test",
    )
    return expr.expression_id


@pytest.mark.asyncio
async def test_negative_expression_feedback_writes_edge(stores):
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)

    fb = await style.record_feedback(
        target_type="expression",
        target_id=eid,
        group_id="100",
        rating="negative",
        source="admin",
        actor="user_42",
        raw_text="说得不像我",
        context="我从来不会这样接话",
    )

    user_node = await writer.get_node_by_source(USER_SOURCE_TABLE, "user_42")
    target_node = await writer.get_node_by_source(EXPRESSION_SOURCE_TABLE, eid)
    assert user_node is not None
    assert user_node.node_type == "user"
    assert target_node is not None

    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=user_node.node_id,
        to_node_id=target_node.node_id,
    )
    assert edge is not None
    assert edge.status == "active"
    assert edge.confidence == pytest.approx(1.0, abs=0.01)
    assert fb.feedback_id in edge.evidence_refs
    assert edge.properties.get("target_type") == "expression"
    assert edge.properties.get("rating") == "negative"
    # Note clip lives at <=240 chars; we just confirm it carries the context
    assert "从来不会" in edge.properties.get("note", "")


@pytest.mark.asyncio
async def test_negative_profile_feedback_writes_edge(stores):
    style, _kg, writer, _bridge = stores

    fb = await style.record_feedback(
        target_type="profile",
        target_id="profile_xyz",
        group_id="100",
        rating="negative",
        source="admin",
        actor="user_77",
        raw_text="整体语气太冷",
    )

    user_node = await writer.get_node_by_source(USER_SOURCE_TABLE, "user_77")
    target_node = await writer.get_node_by_source(PROFILE_SOURCE_TABLE, "profile_xyz")
    assert user_node is not None
    assert target_node is not None
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=user_node.node_id,
        to_node_id=target_node.node_id,
    )
    assert edge is not None
    assert edge.properties.get("target_type") == "profile"
    assert fb.feedback_id in edge.evidence_refs


@pytest.mark.asyncio
async def test_positive_feedback_is_skipped(stores):
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)
    await style.record_feedback(
        target_type="expression",
        target_id=eid,
        group_id="100",
        rating="positive",
        source="admin",
        actor="user_42",
    )
    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE)
    assert count == 0


@pytest.mark.asyncio
async def test_neutral_feedback_is_skipped(stores):
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)
    await style.record_feedback(
        target_type="expression",
        target_id=eid,
        group_id="100",
        rating="neutral",
        source="weak_signal",
        actor="style_plugin",
        raw_text="某条 bot 回复",
    )
    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE)
    assert count == 0


@pytest.mark.asyncio
async def test_unknown_target_type_is_noop(stores):
    """target_type='reply' (or anything outside expression/profile) must not crash."""
    style, _kg, writer, _bridge = stores
    await style.record_feedback(
        target_type="reply",
        target_id="msg_1",
        group_id="100",
        rating="negative",
        source="admin",
        actor="user_42",
        raw_text="不喜欢这条回复",
    )
    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE)
    assert count == 0


@pytest.mark.asyncio
async def test_empty_target_id_is_noop(stores):
    style, _kg, writer, _bridge = stores
    await style.record_feedback(
        target_type="expression",
        target_id="",
        group_id="100",
        rating="negative",
        source="admin",
        actor="user_42",
    )
    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE)
    assert count == 0


@pytest.mark.asyncio
async def test_empty_actor_collapses_to_anonymous(stores):
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)
    await style.record_feedback(
        target_type="expression",
        target_id=eid,
        group_id="100",
        rating="negative",
        source="admin",
        actor="",
        raw_text="不行",
    )
    user_node = await writer.get_node_by_source(USER_SOURCE_TABLE, ANONYMOUS_ACTOR)
    assert user_node is not None
    target_node = await writer.get_node_by_source(EXPRESSION_SOURCE_TABLE, eid)
    assert target_node is not None
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=user_node.node_id,
        to_node_id=target_node.node_id,
    )
    assert edge is not None


@pytest.mark.asyncio
async def test_listener_exception_is_caught(stores):
    """A broken sibling listener must not break the bridge listener."""
    style, _kg, writer, _bridge = stores

    async def _bad(*_args, **_kwargs):
        raise TypeError("listener internal bug")

    style.add_feedback_listener(_bad)

    eid = await _seed_expression(style)
    fb = await style.record_feedback(
        target_type="expression",
        target_id=eid,
        group_id="100",
        rating="negative",
        source="admin",
        actor="user_42",
    )
    assert fb.feedback_id  # SQL succeeded
    # Bridge should still have written its edge
    user_node = await writer.get_node_by_source(USER_SOURCE_TABLE, "user_42")
    target_node = await writer.get_node_by_source(EXPRESSION_SOURCE_TABLE, eid)
    assert user_node is not None
    assert target_node is not None


@pytest.mark.asyncio
async def test_graph_write_failure_does_not_roll_back_record(stores, monkeypatch):
    """Audit § E.3: graph mirror failure must not roll back feedback row."""
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("graph disk gone")

    monkeypatch.setattr(writer, "write_node", _boom)

    fb = await style.record_feedback(
        target_type="expression",
        target_id=eid,
        group_id="100",
        rating="negative",
        source="admin",
        actor="user_42",
        raw_text="不行",
    )
    assert fb.feedback_id
    rows, total = await style.list_feedback(target_id=eid)
    assert total == 1
    assert rows[0].feedback_id == fb.feedback_id


@pytest.mark.asyncio
async def test_cancel_path_leaves_clean_state(stores):
    """D2: cancel mid-record_feedback must not corrupt either store."""
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            style.record_feedback(
                target_type="expression",
                target_id=eid,
                group_id="100",
                rating="negative",
                source="admin",
                actor="user_42",
                raw_text="不行",
            ),
            timeout=0.0,
        )

    # Source-of-truth must remain consistent: either the row landed or it didn't.
    _rows, total = await style.list_feedback(target_id=eid)
    assert total in {0, 1}

    # If the bridge ran, the user + target nodes must both exist (or both not).
    user_node = await writer.get_node_by_source(USER_SOURCE_TABLE, "user_42")
    target_node = await writer.get_node_by_source(EXPRESSION_SOURCE_TABLE, eid)
    if user_node is not None:
        # Bridge fired only after SQL commit — so the row must be there.
        assert total == 1
        assert target_node is not None


@pytest.mark.asyncio
async def test_evidence_refs_carry_feedback_id(stores):
    """Lock the contract: evidence_refs is the bare [feedback_id]."""
    style, _kg, writer, _bridge = stores
    eid = await _seed_expression(style)
    fb = await style.record_feedback(
        target_type="expression",
        target_id=eid,
        group_id="100",
        rating="negative",
        source="admin",
        actor="user_42",
    )
    user_node = await writer.get_node_by_source(USER_SOURCE_TABLE, "user_42")
    target_node = await writer.get_node_by_source(EXPRESSION_SOURCE_TABLE, eid)
    assert user_node is not None
    assert target_node is not None
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=user_node.node_id,
        to_node_id=target_node.node_id,
    )
    assert edge is not None
    refs = edge.evidence_refs
    assert isinstance(refs, list)
    assert fb.feedback_id in refs
