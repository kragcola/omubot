"""dual_write — utilities to mirror source-of-truth entities into the graph layer.

This module provides fire-and-forget helpers that write a `graph_nodes` row
for a source entity (slang term, style expression, episode, fact, ...).
The graph layer is *additive*: it does not replace the original store; it
just maintains a parallel projection used by Phase D Consolidator and the
admin graph view.

Callers are expected to wrap calls in try/except — failures here MUST never
break the source-of-truth write path.
"""

from __future__ import annotations

from typing import Any

from services.knowledge_graph.graph_writer import GraphWriter
from services.knowledge_graph.types import GraphNodeDraft


async def ensure_graph_node(
    writer: GraphWriter,
    *,
    node_type: str,
    source_table: str,
    source_id: str,
    scope: str,
    group_id: str,
    label: str,
    properties: dict[str, Any] | None = None,
) -> str:
    """Upsert a graph node for a source entity.

    Returns the node_id. Safe to call repeatedly — `write_node` upserts on
    `(source_table, source_id)`.
    """
    draft = GraphNodeDraft(
        node_type=node_type,
        source_table=source_table,
        source_id=source_id,
        scope=scope,
        group_id=group_id,
        label=label,
        properties=properties or {},
    )
    return await writer.write_node(draft)
