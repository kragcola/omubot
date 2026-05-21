"""StyleGraphBridge — Phase E.2 graph edge double-write.

Listens to ``StyleStore.update_expression`` status flips and mirrors
approved expressions into the knowledge graph as
``style_applies_to_situation`` edges.

Design notes (audit
``docs/audits/multilayer-memory-phase-e-design-audit-2026-05-21.md``
§ E.2):

- Triggered when ``status`` actually flips (no fire-on-no-op).
- ``new_status == 'approved'`` upserts the edge with
  ``status='active'``.
- ``new_status in {'muted', 'rejected'}`` revokes the edge via
  ``set_edge_status('disabled')``. ``pending`` is a no-op (and is
  never reachable from a non-pending start anyway).
- Edge shape: ``edge_type='style_applies_to_situation'``,
  ``from_node`` = style_expression node
  (``source_table='style_expressions'``), ``to_node`` = situation
  node — audit § E.2 method A: reuse ``node_type='fact'`` with
  ``source_table='style_situations'`` (dedup-key only, no SQL table)
  and ``source_id`` = SHA1(situation_text) so long Chinese situation
  blobs stay short and stable.
- Empty / global-scope ``group_id`` is allowed (style profiles can be
  global) — the situation node uses ``scope=expression.scope`` and
  ``group_id=expression.group_id``.
- Graph write failures are swallowed and logged at WARN. The
  listener fan-out wrapper in ``StyleStore._fire_status_listeners``
  provides a second layer of suppression.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from loguru import logger

from services.knowledge_graph.dual_write import ensure_graph_node
from services.knowledge_graph.graph_writer import GraphWriter
from services.knowledge_graph.types import GraphEdgeDraft

if TYPE_CHECKING:
    from services.style.store import StyleExpression, StyleStore

_L = logger.bind(channel="style_graph")

EXPRESSION_SOURCE_TABLE = "style_expressions"
SITUATION_SOURCE_TABLE = "style_situations"
EDGE_TYPE = "style_applies_to_situation"

REVOKE_STATUSES = frozenset(("muted", "rejected"))


def _situation_source_id(situation: str) -> str:
    """Hash the situation text into a short stable dedup key.

    Long Chinese situation strings would be unwieldy as a primary key.
    SHA1 truncated to 16 hex chars yields a 64-bit dedup space — far
    more than enough for the situation node population we expect.
    """
    h = hashlib.sha1(situation.encode("utf-8")).hexdigest()[:16]
    return f"sit_{h}"


class StyleGraphBridge:
    """Mirrors style expression status flips into knowledge_graph edges."""

    def __init__(self, writer: GraphWriter) -> None:
        self._writer = writer

    def attach(self, store: StyleStore) -> None:
        """Subscribe to ``store``'s status listener fan-out.

        Idempotent on the listener side: registering twice would fire
        the hook twice, but plugin startup wires exactly once.
        """
        store.add_status_listener(self._on_status)

    async def _on_status(
        self,
        expression: StyleExpression,
        prev_status: str,
        new_status: str,
        actor: str,
    ) -> None:
        del actor, prev_status  # not stored on the edge
        try:
            if new_status == "approved":
                await self._upsert_edge(expression)
            elif new_status in REVOKE_STATUSES:
                await self._disable_edge(expression)
        except Exception as exc:
            _L.warning(
                "style_applies_to_situation edge upsert failed | "
                "expression={} new_status={} err={}",
                expression.expression_id, new_status, exc,
            )

    async def _upsert_edge(self, expression: StyleExpression) -> None:
        situation = expression.situation or ""
        if not situation:
            return
        situation_source_id = _situation_source_id(situation)
        expression_node_id = await ensure_graph_node(
            self._writer,
            node_type="style_expression",
            source_table=EXPRESSION_SOURCE_TABLE,
            source_id=expression.expression_id,
            scope=expression.scope,
            group_id=expression.group_id,
            label=(expression.style or expression.expression_id)[:80],
            properties={
                "confidence": expression.confidence,
                "status": expression.status,
                "output_policy": expression.output_policy,
            },
        )
        situation_node_id = await ensure_graph_node(
            self._writer,
            node_type="fact",  # audit § E.2 method A: reuse fact node type
            source_table=SITUATION_SOURCE_TABLE,
            source_id=situation_source_id,
            scope=expression.scope,
            group_id=expression.group_id,
            label=situation[:80],
            properties={"situation_text": situation},
        )
        draft = GraphEdgeDraft(
            edge_type=EDGE_TYPE,
            from_node_id=expression_node_id,
            to_node_id=situation_node_id,
            scope=expression.scope,
            group_id=expression.group_id,
            confidence=float(expression.confidence),
            evidence_refs=(expression.expression_id,),
            properties={
                "persona_fit": float(expression.persona_fit),
                "mood_fit": float(expression.mood_fit),
            },
        )
        await self._writer.write_edge(draft)
        # Re-activate after a prior disable cycle.
        await self._writer.set_edge_status(
            edge_type=EDGE_TYPE,
            from_node_id=expression_node_id,
            to_node_id=situation_node_id,
            status="active",
        )
        _L.info(
            "style_applies_to_situation edge active | expression={} group={}",
            expression.expression_id, expression.group_id,
        )

    async def _disable_edge(self, expression: StyleExpression) -> None:
        situation = expression.situation or ""
        if not situation:
            return
        expression_node = await self._writer.get_node_by_source(
            EXPRESSION_SOURCE_TABLE, expression.expression_id,
        )
        situation_node = await self._writer.get_node_by_source(
            SITUATION_SOURCE_TABLE, _situation_source_id(situation),
        )
        if expression_node is None or situation_node is None:
            # Approve never happened — nothing to disable.
            return
        ok = await self._writer.set_edge_status(
            edge_type=EDGE_TYPE,
            from_node_id=expression_node.node_id,
            to_node_id=situation_node.node_id,
            status="disabled",
        )
        if ok:
            _L.info(
                "style_applies_to_situation edge disabled | expression={}",
                expression.expression_id,
            )
