"""GraphWriter — upsert/query layer for graph_nodes + graph_edges tables."""

from __future__ import annotations

import json
import secrets
from typing import Any

import aiosqlite
from loguru import logger

from services.cross_group import cross_group_where, legacy_cross_group_visible, visibility_from_db
from services.knowledge_graph.store import KnowledgeGraphStore, _now_iso
from services.knowledge_graph.types import (
    GraphEdge,
    GraphEdgeDraft,
    GraphNode,
    GraphNodeDraft,
)


def _row_to_node(row: Any) -> GraphNode:
    keys = row.keys()
    props_raw = row["properties_json"] if "properties_json" in keys else "{}"
    try:
        properties = json.loads(props_raw or "{}")
    except (json.JSONDecodeError, TypeError):
        properties = {}
    cg_groups_raw = row["cross_group_enabled_for_groups"] if "cross_group_enabled_for_groups" in keys else "[]"
    try:
        cg_groups = json.loads(cg_groups_raw or "[]")
    except (json.JSONDecodeError, TypeError):
        cg_groups = []
    visibility = visibility_from_db(row["cross_group_visible"]) if "cross_group_visible" in keys else "none"
    return GraphNode(
        node_id=row["node_id"],
        node_type=row["node_type"],
        source_table=row["source_table"] if "source_table" in keys else "",
        source_id=row["source_id"] if "source_id" in keys else "",
        scope=row["scope"] if "scope" in keys else "group",
        group_id=row["group_id"] if "group_id" in keys else "",
        label=row["label"] if "label" in keys else "",
        properties=properties,
        status=row["status"] if "status" in keys else "active",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        cross_group_visible=legacy_cross_group_visible(visibility),
        cross_group_visibility=visibility,
        cross_group_enabled_by=row["cross_group_enabled_by"] if "cross_group_enabled_by" in keys else "",
        cross_group_enabled_at=row["cross_group_enabled_at"] if "cross_group_enabled_at" in keys else "",
        cross_group_enabled_for_groups=cg_groups,
        cross_group_enabled_reason=row["cross_group_enabled_reason"] if "cross_group_enabled_reason" in keys else "",
    )


def _row_to_edge(row: Any) -> GraphEdge:
    keys = row.keys()
    props_raw = row["properties_json"] if "properties_json" in keys else "{}"
    try:
        properties = json.loads(props_raw or "{}")
    except (json.JSONDecodeError, TypeError):
        properties = {}
    refs_raw = row["evidence_refs"] if "evidence_refs" in keys else "[]"
    try:
        evidence_refs = json.loads(refs_raw or "[]")
    except (json.JSONDecodeError, TypeError):
        evidence_refs = []
    cg_groups_raw = row["cross_group_enabled_for_groups"] if "cross_group_enabled_for_groups" in keys else "[]"
    try:
        cg_groups = json.loads(cg_groups_raw or "[]")
    except (json.JSONDecodeError, TypeError):
        cg_groups = []
    visibility = visibility_from_db(row["cross_group_visible"]) if "cross_group_visible" in keys else "none"
    return GraphEdge(
        edge_id=row["edge_id"],
        edge_type=row["edge_type"],
        from_node_id=row["from_node_id"],
        to_node_id=row["to_node_id"],
        scope=row["scope"] if "scope" in keys else "group",
        group_id=row["group_id"] if "group_id" in keys else "",
        confidence=float(row["confidence"]) if "confidence" in keys else 0.5,
        evidence_refs=evidence_refs,
        properties=properties,
        status=row["status"] if "status" in keys else "active",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        cross_group_visible=legacy_cross_group_visible(visibility),
        cross_group_visibility=visibility,
        cross_group_enabled_by=row["cross_group_enabled_by"] if "cross_group_enabled_by" in keys else "",
        cross_group_enabled_at=row["cross_group_enabled_at"] if "cross_group_enabled_at" in keys else "",
        cross_group_enabled_for_groups=cg_groups,
        cross_group_enabled_reason=row["cross_group_enabled_reason"] if "cross_group_enabled_reason" in keys else "",
    )


class GraphWriter:
    def __init__(self, store: KnowledgeGraphStore) -> None:
        self._store = store

    @property
    def _db(self) -> aiosqlite.Connection:
        return self._store._require_db()

    async def write_node(self, draft: GraphNodeDraft) -> str:
        now = _now_iso()
        db = self._db
        cursor = await db.execute(
            "SELECT node_id FROM graph_nodes WHERE source_table = ? AND source_id = ?",
            (draft.source_table, draft.source_id),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            node_id = existing["node_id"]
            await db.execute(
                "UPDATE graph_nodes SET label = ?, properties_json = ?, updated_at = ? WHERE node_id = ?",
                (draft.label, json.dumps(draft.properties, ensure_ascii=False), now, node_id),
            )
            await db.commit()
            logger.debug(
                "graph_node upserted (update) | node_id={} source={}:{}",
                node_id, draft.source_table, draft.source_id,
            )
            return node_id
        node_id = "gn_" + secrets.token_hex(6)
        await db.execute(
            """INSERT INTO graph_nodes
               (node_id, node_type, source_table, source_id, scope, group_id,
                label, properties_json, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                node_id, draft.node_type, draft.source_table, draft.source_id,
                draft.scope, draft.group_id, draft.label,
                json.dumps(draft.properties, ensure_ascii=False),
                now, now,
            ),
        )
        await db.commit()
        logger.debug(
            "graph_node upserted (insert) | node_id={} source={}:{}",
            node_id, draft.source_table, draft.source_id,
        )
        return node_id

    async def write_edge(self, draft: GraphEdgeDraft) -> str:
        now = _now_iso()
        db = self._db
        cursor = await db.execute(
            "SELECT edge_id FROM graph_edges WHERE edge_type = ? AND from_node_id = ? AND to_node_id = ?",
            (draft.edge_type, draft.from_node_id, draft.to_node_id),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            edge_id = existing["edge_id"]
            await db.execute(
                "UPDATE graph_edges SET confidence = ?, evidence_refs = ?, "
                "properties_json = ?, updated_at = ? WHERE edge_id = ?",
                (
                    draft.confidence,
                    json.dumps(list(draft.evidence_refs), ensure_ascii=False),
                    json.dumps(draft.properties, ensure_ascii=False),
                    now,
                    edge_id,
                ),
            )
            await db.commit()
            return edge_id
        edge_id = "gx_" + secrets.token_hex(6)
        await db.execute(
            """INSERT INTO graph_edges
               (edge_id, edge_type, from_node_id, to_node_id, scope, group_id,
                confidence, evidence_refs, properties_json, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                edge_id, draft.edge_type, draft.from_node_id, draft.to_node_id,
                draft.scope, draft.group_id, draft.confidence,
                json.dumps(list(draft.evidence_refs), ensure_ascii=False),
                json.dumps(draft.properties, ensure_ascii=False),
                now, now,
            ),
        )
        await db.commit()
        return edge_id

    async def set_edge_status(
        self,
        *,
        edge_type: str,
        from_node_id: str,
        to_node_id: str,
        status: str,
    ) -> bool:
        """Update the status of the unique edge identified by the triple.

        Returns True when a row was matched and updated, False otherwise.
        Used by Phase D.5 episode_supports_profile bridge to revoke an
        edge (status='disabled') when the underlying episode is disabled
        and re-activate (status='active') after a disabled→approved cycle.
        """
        now = _now_iso()
        cursor = await self._db.execute(
            "UPDATE graph_edges SET status = ?, updated_at = ? "
            "WHERE edge_type = ? AND from_node_id = ? AND to_node_id = ?",
            (status, now, edge_type, from_node_id, to_node_id),
        )
        await self._db.commit()
        return (cursor.rowcount or 0) > 0

    async def find_edge(
        self,
        *,
        edge_type: str,
        from_node_id: str,
        to_node_id: str,
    ) -> GraphEdge | None:
        cursor = await self._db.execute(
            "SELECT * FROM graph_edges WHERE edge_type = ? "
            "AND from_node_id = ? AND to_node_id = ?",
            (edge_type, from_node_id, to_node_id),
        )
        row = await cursor.fetchone()
        return _row_to_edge(row) if row else None

    async def get_node(self, node_id: str) -> GraphNode | None:
        db = self._db
        cursor = await db.execute("SELECT * FROM graph_nodes WHERE node_id = ?", (node_id,))
        row = await cursor.fetchone()
        return _row_to_node(row) if row else None

    async def get_node_by_source(self, source_table: str, source_id: str) -> GraphNode | None:
        db = self._db
        cursor = await db.execute(
            "SELECT * FROM graph_nodes WHERE source_table = ? AND source_id = ?",
            (source_table, source_id),
        )
        row = await cursor.fetchone()
        return _row_to_node(row) if row else None

    async def list_nodes(
        self,
        *,
        node_type: str = "",
        group_id: str = "",
        status: str = "active",
        search: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[GraphNode], int]:
        db = self._db
        where: list[str] = []
        values: list[Any] = []
        if node_type:
            where.append("node_type = ?")
            values.append(node_type)
        if group_id:
            where.append("group_id = ?")
            values.append(group_id)
        if status:
            where.append("status = ?")
            values.append(status)
        if search:
            where.append("(label LIKE ? OR source_id LIKE ?)")
            pattern = f"%{search}%"
            values.extend([pattern, pattern])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        cursor = await db.execute(f"SELECT COUNT(*) AS cnt FROM graph_nodes {where_sql}", values)
        count_row = await cursor.fetchone()
        total = int(count_row["cnt"]) if count_row else 0
        cursor = await db.execute(
            f"SELECT * FROM graph_nodes {where_sql} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            [*values, limit, offset],
        )
        rows = await cursor.fetchall()
        return [_row_to_node(r) for r in rows], total

    async def list_edges(
        self,
        *,
        node_id: str = "",
        direction: str = "both",
        edge_type: str = "",
        status: str = "active",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[GraphEdge], int]:
        db = self._db
        where: list[str] = []
        values: list[Any] = []
        if node_id:
            if direction == "out":
                where.append("from_node_id = ?")
                values.append(node_id)
            elif direction == "in":
                where.append("to_node_id = ?")
                values.append(node_id)
            else:
                where.append("(from_node_id = ? OR to_node_id = ?)")
                values.extend([node_id, node_id])
        if edge_type:
            where.append("edge_type = ?")
            values.append(edge_type)
        if status:
            where.append("status = ?")
            values.append(status)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        cursor = await db.execute(f"SELECT COUNT(*) AS cnt FROM graph_edges {where_sql}", values)
        count_row = await cursor.fetchone()
        total = int(count_row["cnt"]) if count_row else 0
        cursor = await db.execute(
            f"SELECT * FROM graph_edges {where_sql} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            [*values, limit, offset],
        )
        rows = await cursor.fetchall()
        return [_row_to_edge(r) for r in rows], total

    async def get_stats(self) -> dict[str, Any]:
        db = self._db
        cursor = await db.execute(
            "SELECT node_type, COUNT(*) AS cnt FROM graph_nodes WHERE status = 'active' GROUP BY node_type"
        )
        node_counts = {row["node_type"]: row["cnt"] for row in await cursor.fetchall()}
        cursor = await db.execute(
            "SELECT edge_type, COUNT(*) AS cnt FROM graph_edges WHERE status = 'active' GROUP BY edge_type"
        )
        edge_counts = {row["edge_type"]: row["cnt"] for row in await cursor.fetchall()}
        cursor = await db.execute("SELECT COUNT(*) AS cnt FROM graph_nodes WHERE status = 'active'")
        nodes_row = await cursor.fetchone()
        total_nodes = int(nodes_row["cnt"]) if nodes_row else 0
        cursor = await db.execute("SELECT COUNT(*) AS cnt FROM graph_edges WHERE status = 'active'")
        edges_row = await cursor.fetchone()
        total_edges = int(edges_row["cnt"]) if edges_row else 0
        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "by_node_type": node_counts,
            "by_edge_type": edge_counts,
        }

    def apply_cross_group_filter(
        self, base_where: str, viewer_group_id: str, *, table_alias: str = "",
    ) -> tuple[str, list[str]]:
        prefix = f"{table_alias}." if table_alias else ""
        cg_clause = cross_group_where(
            group_id_col=f"{prefix}group_id",
            scope_col=f"{prefix}scope",
            visibility_col=f"{prefix}cross_group_visible",
            enabled_groups_col=f"{prefix}cross_group_enabled_for_groups",
        )
        if base_where:
            return f"{base_where} AND {cg_clause}", [viewer_group_id, viewer_group_id]
        return cg_clause, [viewer_group_id, viewer_group_id]
