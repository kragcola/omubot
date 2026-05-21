"""EpisodeGraphBridge (D.5) tests.

Covers:

- ``approved`` writes ``episode_supports_profile`` edge with confidence
  + evidence_refs intact
- ``disabled`` revokes the edge (status='disabled')
- ``disabled``→``approved`` cycle reactivates the edge (status='active')
- ``enabled_for_prompt`` does **not** write a new edge (already exists
  from prior approve)
- empty group_id is a no-op (no ambiguous to-node)
- graph write failure does NOT roll back the episode state transition
- D2 cancel-path: ``asyncio.wait_for(timeout=0.0)`` while transitioning
  leaves both stores in a consistent state
"""

from __future__ import annotations

import asyncio

import pytest

from services.episodic.graph_bridge import EDGE_TYPE, EpisodeGraphBridge
from services.episodic.store import EpisodeStore
from services.knowledge_graph.graph_writer import GraphWriter
from services.knowledge_graph.store import KnowledgeGraphStore


@pytest.fixture
async def stores(tmp_path):
    ep_store = EpisodeStore(str(tmp_path / "episodic.db"))
    await ep_store.init()
    kg_store = KnowledgeGraphStore(str(tmp_path / "graph.db"))
    await kg_store.init()
    writer = GraphWriter(kg_store)
    bridge = EpisodeGraphBridge(writer)
    bridge.attach(ep_store)
    yield ep_store, kg_store, writer, bridge
    await ep_store.close()
    await kg_store.close()


async def _seed_through_approved(
    store: EpisodeStore,
    *,
    group_id: str = "g1",
    situation: str = "用户问技术问题",
    confidence: float = 0.8,
) -> str:
    ep = await store.create_episode(
        situation=situation, group_id=group_id, confidence=confidence,
    )
    await store.transition_state(ep.episode_id, new_state="candidate")
    await store.transition_state(ep.episode_id, new_state="approved")
    return ep.episode_id


@pytest.mark.asyncio
async def test_approve_writes_episode_supports_profile_edge(stores):
    ep_store, _kg_store, writer, _bridge = stores
    ep_id = await _seed_through_approved(ep_store)

    ep_node = await writer.get_node_by_source("episodes", ep_id)
    assert ep_node is not None
    assert ep_node.node_type == "episode"
    assert ep_node.scope == "group"
    assert ep_node.group_id == "g1"

    group_node = await writer.get_node_by_source("groups", "g1")
    assert group_node is not None
    assert group_node.node_type == "group"

    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=ep_node.node_id,
        to_node_id=group_node.node_id,
    )
    assert edge is not None
    assert edge.status == "active"
    assert edge.confidence == pytest.approx(0.8)
    assert ep_id in edge.evidence_refs


@pytest.mark.asyncio
async def test_disable_revokes_edge(stores):
    ep_store, _kg_store, writer, _bridge = stores
    ep_id = await _seed_through_approved(ep_store)
    await ep_store.transition_state(ep_id, new_state="disabled")

    ep_node = await writer.get_node_by_source("episodes", ep_id)
    group_node = await writer.get_node_by_source("groups", "g1")
    assert ep_node is not None
    assert group_node is not None
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=ep_node.node_id,
        to_node_id=group_node.node_id,
    )
    assert edge is not None
    assert edge.status == "disabled"


@pytest.mark.asyncio
async def test_disabled_then_reapproved_reactivates_edge(stores):
    ep_store, _kg_store, writer, _bridge = stores
    ep_id = await _seed_through_approved(ep_store)
    await ep_store.transition_state(ep_id, new_state="disabled")
    # disabled→approved is a legal transition (see VALID_TRANSITIONS)
    await ep_store.transition_state(ep_id, new_state="approved")

    ep_node = await writer.get_node_by_source("episodes", ep_id)
    group_node = await writer.get_node_by_source("groups", "g1")
    assert ep_node is not None
    assert group_node is not None
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=ep_node.node_id,
        to_node_id=group_node.node_id,
    )
    assert edge is not None
    assert edge.status == "active"


@pytest.mark.asyncio
async def test_enabled_for_prompt_does_not_create_extra_edge(stores):
    ep_store, _kg_store, writer, _bridge = stores
    ep_id = await _seed_through_approved(ep_store)
    edges_before, count_before = await writer.list_edges(
        edge_type=EDGE_TYPE, status="active",
    )
    assert count_before == 1

    await ep_store.transition_state(ep_id, new_state="enabled_for_prompt")
    edges_after, count_after = await writer.list_edges(
        edge_type=EDGE_TYPE, status="active",
    )
    # No new row, the existing edge stays active
    assert count_after == 1
    assert edges_after[0].edge_id == edges_before[0].edge_id


@pytest.mark.asyncio
async def test_disabled_before_approved_is_noop(stores):
    """dry_run→disabled must not crash and must not write an edge."""
    ep_store, _kg_store, writer, _bridge = stores
    ep = await ep_store.create_episode(
        situation="never approved", group_id="g1", confidence=0.4,
    )
    await ep_store.transition_state(ep.episode_id, new_state="disabled")

    ep_node = await writer.get_node_by_source("episodes", ep.episode_id)
    # No approve happened → bridge never created the node
    assert ep_node is None
    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE, status="active")
    assert count == 0


@pytest.mark.asyncio
async def test_empty_group_id_is_skipped(stores):
    """Episodes with empty group_id (scope=global) skip graph mirror."""
    ep_store, _kg_store, writer, _bridge = stores
    ep = await ep_store.create_episode(
        situation="global ep", group_id="", confidence=0.8,
    )
    await ep_store.transition_state(ep.episode_id, new_state="candidate")
    await ep_store.transition_state(ep.episode_id, new_state="approved")

    ep_node = await writer.get_node_by_source("episodes", ep.episode_id)
    assert ep_node is None
    _edges, count = await writer.list_edges(edge_type=EDGE_TYPE)
    assert count == 0


@pytest.mark.asyncio
async def test_graph_write_failure_does_not_roll_back_state(stores, monkeypatch):
    """Audit § D.5: graph write failure must not roll back state machine."""
    ep_store, _kg_store, writer, _bridge = stores

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("graph disk gone")

    monkeypatch.setattr(writer, "write_node", _boom)

    ep = await ep_store.create_episode(
        situation="t", group_id="g1", confidence=0.8,
    )
    await ep_store.transition_state(ep.episode_id, new_state="candidate")
    # Even with the writer broken, the transition succeeds
    ok = await ep_store.transition_state(ep.episode_id, new_state="approved")
    assert ok is True

    refreshed = await ep_store.get_episode(ep.episode_id)
    assert refreshed is not None
    assert refreshed.episode_state == "approved"


@pytest.mark.asyncio
async def test_listener_exception_is_caught(stores, monkeypatch):
    """Even an outright TypeError in the listener must not bubble."""
    ep_store, _kg_store, _writer, _bridge = stores

    async def _bad_listener(*_args, **_kwargs):
        raise TypeError("listener internal bug")

    # Inject a broken listener alongside the real bridge
    ep_store.add_transition_listener(_bad_listener)

    ep = await ep_store.create_episode(
        situation="t", group_id="g1", confidence=0.8,
    )
    await ep_store.transition_state(ep.episode_id, new_state="candidate")
    ok = await ep_store.transition_state(ep.episode_id, new_state="approved")
    assert ok is True


@pytest.mark.asyncio
async def test_cancel_path_leaves_clean_state(stores):
    """D2: cancel mid-approve must not corrupt either store.

    A timeout=0.0 forces wait_for to cancel the transition+listener
    chain immediately. After the cancellation, the episode state and
    graph edge state must remain mutually consistent — either both
    say approved/edge-active or both say candidate/no-edge.
    """
    ep_store, _kg_store, writer, _bridge = stores
    ep = await ep_store.create_episode(
        situation="t", group_id="g1", confidence=0.8,
    )
    await ep_store.transition_state(ep.episode_id, new_state="candidate")

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            ep_store.transition_state(ep.episode_id, new_state="approved"),
            timeout=0.0,
        )

    # Re-fetch — store must still be readable
    refreshed = await ep_store.get_episode(ep.episode_id)
    assert refreshed is not None
    assert refreshed.episode_state in {"candidate", "approved"}

    # If the state managed to flip to approved, the bridge may or may
    # not have run; if it did run, the edge should match. The contract
    # is "consistent within each side, no half-written rows".
    ep_node = await writer.get_node_by_source("episodes", ep.episode_id)
    if ep_node is not None:
        # Bridge ran far enough to write the episode node — that means
        # the state had to be approved before the listener fired.
        assert refreshed.episode_state == "approved"


@pytest.mark.asyncio
async def test_evidence_refs_carry_episode_id(stores):
    """Lock the contract: evidence_refs is the bare episode_id list."""
    ep_store, _kg_store, writer, _bridge = stores
    ep_id = await _seed_through_approved(ep_store)

    ep_node = await writer.get_node_by_source("episodes", ep_id)
    group_node = await writer.get_node_by_source("groups", "g1")
    assert ep_node is not None
    assert group_node is not None
    edge = await writer.find_edge(
        edge_type=EDGE_TYPE,
        from_node_id=ep_node.node_id,
        to_node_id=group_node.node_id,
    )
    assert edge is not None
    refs = edge.evidence_refs
    assert isinstance(refs, list)
    assert ep_id in refs
    assert all(isinstance(r, str) and r.startswith("ep_") for r in refs)


@pytest.mark.asyncio
async def test_set_edge_status_idempotent(tmp_path):
    """GraphWriter.set_edge_status returns False when nothing matches."""
    kg = KnowledgeGraphStore(str(tmp_path / "graph.db"))
    await kg.init()
    try:
        writer = GraphWriter(kg)
        ok = await writer.set_edge_status(
            edge_type=EDGE_TYPE,
            from_node_id="gn_missing",
            to_node_id="gn_also_missing",
            status="disabled",
        )
        assert ok is False
    finally:
        await kg.close()
