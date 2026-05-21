"""StyleFeedbackGraphBridge — Phase E.3 graph edge double-write.

Listens to ``StyleStore.record_feedback`` and mirrors **negative** user
ratings into the knowledge graph as ``user_corrected_bot_about`` edges
(audit ``docs/audits/multilayer-memory-phase-e-design-audit-2026-05-21.md``
§ E.3).

Design notes:

- Triggered after every successful ``record_feedback`` SQL commit.
- Fires only when ``rating == 'negative'``. Positive / neutral entries
  are noise for this edge type — they would dilute the "user correction"
  semantics that the consolidator reads back.
- Real ``target_type`` values in the codebase are ``expression`` and
  ``profile`` (the audit text said ``expression``/``reply``/``persona``;
  the codebase grew differently — see ``services/style/store.py`` callers).
  Anything else is a no-op so future target types don't crash the bridge.
- ``actor`` is the QQ id of whoever submitted the feedback; we collapse
  the empty-actor case to a sentinel ``"anonymous"`` user node so
  multiple anonymous corrections still aggregate.
- ``confidence=1.0`` — admin/user explicit corrections are ground truth;
  no aggregation needed.
- ``evidence_refs=(feedback_id,)``; ``properties`` carry the verbatim
  ``target_type``, ``rating``, timestamp, and a clipped ``note`` so the
  consolidator can render the correction in context without joining
  back to ``style_feedback`` for every rendering pass.
- No revoke path: feedback is single-event. If the user later submits a
  positive rating for the same target, that's a *separate* feedback row
  with its own ``feedback_id`` — we don't try to undo the prior edge.
- All graph writes are best-effort; failures are caught here and logged
  WARN. The fan-out wrapper in ``StyleStore._fire_feedback_listeners``
  is the second layer of suppression.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from services.knowledge_graph.dual_write import ensure_graph_node
from services.knowledge_graph.graph_writer import GraphWriter
from services.knowledge_graph.types import GraphEdgeDraft

if TYPE_CHECKING:
    from services.style.store import StyleFeedback, StyleStore

_L = logger.bind(channel="style_feedback_graph")

EXPRESSION_SOURCE_TABLE = "style_expressions"
PROFILE_SOURCE_TABLE = "style_profiles"
USER_SOURCE_TABLE = "users"
EDGE_TYPE = "user_corrected_bot_about"

ANONYMOUS_ACTOR = "anonymous"

# target_type → (target source_table, fallback node_type)
_TARGET_MAP: dict[str, tuple[str, str]] = {
    "expression": (EXPRESSION_SOURCE_TABLE, "style_expression"),
    "profile": (PROFILE_SOURCE_TABLE, "style_expression"),
}


class StyleFeedbackGraphBridge:
    """Mirrors negative style feedback into ``user_corrected_bot_about`` edges."""

    def __init__(self, writer: GraphWriter) -> None:
        self._writer = writer

    def attach(self, store: StyleStore) -> None:
        """Subscribe to ``store``'s feedback listener fan-out."""
        store.add_feedback_listener(self._on_feedback)

    async def _on_feedback(self, feedback: StyleFeedback) -> None:
        if feedback.rating != "negative":
            return
        if not feedback.target_id:
            return
        mapping = _TARGET_MAP.get(feedback.target_type)
        if mapping is None:
            return
        try:
            await self._upsert_edge(feedback, *mapping)
        except Exception as exc:
            _L.warning(
                "user_corrected_bot_about edge upsert failed | "
                "feedback={} target={}:{} err={}",
                feedback.feedback_id, feedback.target_type, feedback.target_id, exc,
            )

    async def _upsert_edge(
        self,
        feedback: StyleFeedback,
        target_source_table: str,
        target_fallback_node_type: str,
    ) -> None:
        actor = feedback.actor or ANONYMOUS_ACTOR
        scope = "group" if feedback.group_id else "global"
        user_node_id = await ensure_graph_node(
            self._writer,
            node_type="user",
            source_table=USER_SOURCE_TABLE,
            source_id=actor,
            scope=scope,
            group_id=feedback.group_id,
            label=actor[:80],
            properties={},
        )
        target_node = await self._writer.get_node_by_source(
            target_source_table, feedback.target_id,
        )
        if target_node is None:
            target_node_id = await ensure_graph_node(
                self._writer,
                node_type=target_fallback_node_type,
                source_table=target_source_table,
                source_id=feedback.target_id,
                scope=scope,
                group_id=feedback.group_id,
                label=feedback.target_id[:80],
                properties={"placeholder": True},
            )
        else:
            target_node_id = target_node.node_id
        draft = GraphEdgeDraft(
            edge_type=EDGE_TYPE,
            from_node_id=user_node_id,
            to_node_id=target_node_id,
            scope=scope,
            group_id=feedback.group_id,
            confidence=1.0,
            evidence_refs=(feedback.feedback_id,),
            properties={
                "target_type": feedback.target_type,
                "rating": feedback.rating,
                "feedback_at": feedback.created_at,
                "note": (feedback.context or feedback.raw_text or "")[:240],
            },
        )
        await self._writer.write_edge(draft)
        _L.info(
            "user_corrected_bot_about edge written | "
            "feedback={} actor={} target={}:{}",
            feedback.feedback_id, actor,
            feedback.target_type, feedback.target_id,
        )
