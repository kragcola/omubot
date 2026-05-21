"""SlangGraphBridge — Phase E.1 graph edge double-write.

Listens to ``SlangStore.record_hit`` and mirrors term-group hits into the
knowledge graph as ``term_used_in_group`` edges.

Design notes (from
``docs/audits/multilayer-memory-phase-e-design-audit-2026-05-21.md``
§ E.1):

- Triggered on every successful ``record_hit`` (i.e. the call returned
  True after writing the SQL row). De-dup is at the graph layer:
  ``write_edge`` upserts on ``(edge_type, from_node_id, to_node_id)`` so
  repeated hits on the same (term, group) update properties rather than
  inserting new rows.
- Edge shape: ``edge_type='term_used_in_group'``,
  ``from_node`` = term node (``source_table='slang_terms'``), ``to_node`` =
  group node (``source_table='groups'``). ``confidence`` mirrors the
  current ``term.confidence`` snapshot at hit time;
  ``evidence_refs=(term_id,)``; ``properties`` carries
  ``usage_count`` and ``last_seen_at`` for graph-side analytics.
- No revoke path. Term mute / expire is not a hit signal — Phase E.1
  scope is "term was used here", which is monotonic. Future cleanup of
  expired-term edges is a separate concern (Phase F territory).
- Empty ``group_id`` is skipped (rare; private chat hits without group
  anchor have no to-node).
- Graph write failures are swallowed and logged at WARN. The listener
  fan-out wrapper in ``SlangStore._fire_hit_listeners`` provides a
  second layer of suppression.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from services.knowledge_graph.dual_write import ensure_graph_node
from services.knowledge_graph.graph_writer import GraphWriter
from services.knowledge_graph.types import GraphEdgeDraft

if TYPE_CHECKING:
    from services.slang.store import SlangStore

_L = logger.bind(channel="slang_graph")

TERM_SOURCE_TABLE = "slang_terms"
GROUP_SOURCE_TABLE = "groups"
EDGE_TYPE = "term_used_in_group"


class SlangGraphBridge:
    """Mirrors slang term hits into knowledge_graph edges."""

    def __init__(self, writer: GraphWriter) -> None:
        self._writer = writer
        self._store: SlangStore | None = None

    def attach(self, store: SlangStore) -> None:
        """Subscribe to ``store``'s hit listener fan-out.

        Stashes ``store`` so the bridge can pull ``term`` snapshots when
        building edge properties — listener payload is intentionally
        minimal so the bridge takes responsibility for the lookup.
        Idempotent on the listener side: registering twice would fire
        the hook twice, but chat plugin startup wires exactly once.
        """
        self._store = store
        store.add_hit_listener(self._on_hit)

    async def _on_hit(
        self,
        term_id: str,
        group_id: str,
        user_id: str,
        usage_count: int,
    ) -> None:
        if not group_id:
            return
        store = self._store
        if store is None:
            return
        try:
            term = await store.get_term(term_id)
            if term is None:
                return
            term_node_id = await ensure_graph_node(
                self._writer,
                node_type="term",
                source_table=TERM_SOURCE_TABLE,
                source_id=term_id,
                scope="group",
                group_id=group_id,
                label=term.term[:80] or term_id,
                properties={
                    "confidence": term.confidence,
                    "status": term.status,
                },
            )
            group_node_id = await ensure_graph_node(
                self._writer,
                node_type="group",
                source_table=GROUP_SOURCE_TABLE,
                source_id=group_id,
                scope="group",
                group_id=group_id,
                label=f"group:{group_id}",
            )
            draft = GraphEdgeDraft(
                edge_type=EDGE_TYPE,
                from_node_id=term_node_id,
                to_node_id=group_node_id,
                scope="group",
                group_id=group_id,
                confidence=float(term.confidence),
                evidence_refs=(term_id,),
                properties={
                    "usage_count": int(usage_count),
                    "last_seen_at": term.last_seen_at,
                },
            )
            await self._writer.write_edge(draft)
            _L.info(
                "term_used_in_group edge upserted | term={} group={} usage={}",
                term_id, group_id, usage_count,
            )
        except Exception as exc:
            _L.warning(
                "term_used_in_group edge upsert failed | "
                "term={} group={} err={}",
                term_id, group_id, exc,
            )
