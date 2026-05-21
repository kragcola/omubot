"""Tests for GraphWriter (graph_nodes + graph_edges) and dual_write helpers."""

from __future__ import annotations

import pytest

from services.knowledge_graph.dual_write import ensure_graph_node
from services.knowledge_graph.graph_writer import GraphWriter
from services.knowledge_graph.store import KnowledgeGraphStore
from services.knowledge_graph.types import GraphEdgeDraft, GraphNodeDraft


async def _open_store(tmp_path) -> KnowledgeGraphStore:
    store = KnowledgeGraphStore(tmp_path / "graph.db")
    await store.init()
    return store


async def _table_exists(store: KnowledgeGraphStore, name: str) -> bool:
    cursor = await store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    row = await cursor.fetchone()
    return row is not None


@pytest.mark.asyncio
async def test_schema_has_graph_nodes_table(tmp_path) -> None:
    store = await _open_store(tmp_path)
    try:
        assert await _table_exists(store, "graph_nodes") is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_schema_has_graph_edges_table(tmp_path) -> None:
    store = await _open_store(tmp_path)
    try:
        assert await _table_exists(store, "graph_edges") is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_write_node_inserts(tmp_path) -> None:
    store = await _open_store(tmp_path)
    try:
        writer = GraphWriter(store)
        node_id = await writer.write_node(GraphNodeDraft(
            node_type="term",
            source_table="slang_terms",
            source_id="t_001",
            scope="group",
            group_id="g_42",
            label="阴阳怪气",
            properties={"variant": "default"},
        ))
        assert node_id.startswith("gn_")
        node = await writer.get_node(node_id)
        assert node is not None
        assert node.label == "阴阳怪气"
        assert node.properties == {"variant": "default"}
        assert node.status == "active"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_write_node_upserts(tmp_path) -> None:
    store = await _open_store(tmp_path)
    try:
        writer = GraphWriter(store)
        first = await writer.write_node(GraphNodeDraft(
            node_type="term", source_table="slang_terms", source_id="t_002",
            scope="group", group_id="g_42", label="老梗",
        ))
        second = await writer.write_node(GraphNodeDraft(
            node_type="term", source_table="slang_terms", source_id="t_002",
            scope="group", group_id="g_42", label="新梗",
            properties={"updated": True},
        ))
        assert first == second
        node = await writer.get_node(first)
        assert node is not None
        assert node.label == "新梗"
        assert node.properties == {"updated": True}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_write_edge_inserts(tmp_path) -> None:
    store = await _open_store(tmp_path)
    try:
        writer = GraphWriter(store)
        from_id = await writer.write_node(GraphNodeDraft(
            node_type="term", source_table="slang_terms", source_id="t_a",
            scope="group", group_id="g_1", label="a",
        ))
        to_id = await writer.write_node(GraphNodeDraft(
            node_type="fact", source_table="graph_facts", source_id="f_a",
            scope="group", group_id="g_1", label="A 喜欢 B",
        ))
        edge_id = await writer.write_edge(GraphEdgeDraft(
            edge_type="term_used_in_context",
            from_node_id=from_id, to_node_id=to_id,
            scope="group", group_id="g_1",
            confidence=0.71, evidence_refs=("ev_1",),
            properties={"k": 1},
        ))
        assert edge_id.startswith("gx_")
        edges, total = await writer.list_edges(node_id=from_id)
        assert total == 1
        assert edges[0].edge_id == edge_id
        assert edges[0].confidence == pytest.approx(0.71)
        assert edges[0].evidence_refs == ["ev_1"]
        assert edges[0].properties == {"k": 1}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_write_edge_upserts(tmp_path) -> None:
    store = await _open_store(tmp_path)
    try:
        writer = GraphWriter(store)
        from_id = await writer.write_node(GraphNodeDraft(
            node_type="term", source_table="slang_terms", source_id="t_b",
            scope="group", group_id="g_1", label="b",
        ))
        to_id = await writer.write_node(GraphNodeDraft(
            node_type="fact", source_table="graph_facts", source_id="f_b",
            scope="group", group_id="g_1", label="B fact",
        ))
        first = await writer.write_edge(GraphEdgeDraft(
            edge_type="doc_supports_fact",
            from_node_id=from_id, to_node_id=to_id,
            scope="group", group_id="g_1",
            confidence=0.5,
        ))
        second = await writer.write_edge(GraphEdgeDraft(
            edge_type="doc_supports_fact",
            from_node_id=from_id, to_node_id=to_id,
            scope="group", group_id="g_1",
            confidence=0.92, evidence_refs=("ev_2", "ev_3"),
            properties={"reviewed": True},
        ))
        assert first == second
        edges, _total = await writer.list_edges(node_id=from_id, edge_type="doc_supports_fact")
        assert len(edges) == 1
        assert edges[0].confidence == pytest.approx(0.92)
        assert edges[0].evidence_refs == ["ev_2", "ev_3"]
        assert edges[0].properties == {"reviewed": True}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_list_nodes_filter_by_type(tmp_path) -> None:
    store = await _open_store(tmp_path)
    try:
        writer = GraphWriter(store)
        await writer.write_node(GraphNodeDraft(
            node_type="term", source_table="slang_terms", source_id="t_x",
            scope="group", group_id="g_1", label="x",
        ))
        await writer.write_node(GraphNodeDraft(
            node_type="fact", source_table="graph_facts", source_id="f_x",
            scope="group", group_id="g_1", label="fx",
        ))
        nodes, total = await writer.list_nodes(node_type="term")
        assert total == 1
        assert all(n.node_type == "term" for n in nodes)
        nodes, total = await writer.list_nodes(node_type="fact")
        assert total == 1
        assert all(n.node_type == "fact" for n in nodes)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_list_edges_by_node(tmp_path) -> None:
    store = await _open_store(tmp_path)
    try:
        writer = GraphWriter(store)
        a = await writer.write_node(GraphNodeDraft(
            node_type="term", source_table="t", source_id="a",
            scope="group", group_id="g", label="a",
        ))
        b = await writer.write_node(GraphNodeDraft(
            node_type="term", source_table="t", source_id="b",
            scope="group", group_id="g", label="b",
        ))
        c = await writer.write_node(GraphNodeDraft(
            node_type="term", source_table="t", source_id="c",
            scope="group", group_id="g", label="c",
        ))
        await writer.write_edge(GraphEdgeDraft(
            edge_type="term_implies_emotion", from_node_id=a, to_node_id=b,
            scope="group", group_id="g",
        ))
        await writer.write_edge(GraphEdgeDraft(
            edge_type="term_implies_emotion", from_node_id=c, to_node_id=a,
            scope="group", group_id="g",
        ))
        out, _ = await writer.list_edges(node_id=a, direction="out")
        assert len(out) == 1 and out[0].from_node_id == a
        in_, _ = await writer.list_edges(node_id=a, direction="in")
        assert len(in_) == 1 and in_[0].to_node_id == a
        both, total = await writer.list_edges(node_id=a, direction="both")
        assert total == 2
        assert {e.edge_id for e in both} == {out[0].edge_id, in_[0].edge_id}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_stats(tmp_path) -> None:
    store = await _open_store(tmp_path)
    try:
        writer = GraphWriter(store)
        a = await writer.write_node(GraphNodeDraft(
            node_type="term", source_table="t", source_id="a",
            scope="group", group_id="g", label="a",
        ))
        b = await writer.write_node(GraphNodeDraft(
            node_type="fact", source_table="t", source_id="b",
            scope="group", group_id="g", label="b",
        ))
        await writer.write_edge(GraphEdgeDraft(
            edge_type="doc_supports_fact", from_node_id=a, to_node_id=b,
            scope="group", group_id="g",
        ))
        stats = await writer.get_stats()
        assert stats["total_nodes"] == 2
        assert stats["total_edges"] == 1
        assert stats["by_node_type"]["term"] == 1
        assert stats["by_node_type"]["fact"] == 1
        assert stats["by_edge_type"]["doc_supports_fact"] == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cross_group_filter(tmp_path) -> None:
    store = await _open_store(tmp_path)
    try:
        writer = GraphWriter(store)
        clause, params = writer.apply_cross_group_filter("", "g_42")
        assert "scope = 'global'" in clause
        assert "scope = 'group' AND group_id = ?" in clause
        assert "cross_group_visible = 1" in clause
        assert params == ["g_42"]

        clause2, params2 = writer.apply_cross_group_filter("status = 'active'", "g_42")
        assert clause2.startswith("status = 'active' AND ")
        assert params2 == ["g_42"]

        clause3, _ = writer.apply_cross_group_filter("", "g_1", table_alias="n")
        assert "n.scope" in clause3
        assert "n.group_id" in clause3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_ensure_graph_node(tmp_path) -> None:
    store = await _open_store(tmp_path)
    try:
        writer = GraphWriter(store)
        node_id = await ensure_graph_node(
            writer,
            node_type="episode",
            source_table="episodes",
            source_id="ep_1",
            scope="group",
            group_id="g_42",
            label="第一段经验",
            properties={"source": "consolidator"},
        )
        assert node_id.startswith("gn_")
        node = await writer.get_node_by_source("episodes", "ep_1")
        assert node is not None
        assert node.label == "第一段经验"
        assert node.properties == {"source": "consolidator"}

        again = await ensure_graph_node(
            writer,
            node_type="episode",
            source_table="episodes",
            source_id="ep_1",
            scope="group",
            group_id="g_42",
            label="第一段经验（修正）",
        )
        assert again == node_id
        node2 = await writer.get_node(again)
        assert node2 is not None
        assert node2.label == "第一段经验（修正）"
    finally:
        await store.close()
