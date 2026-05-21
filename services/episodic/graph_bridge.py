"""EpisodeGraphBridge — D.5 graph edge double-write.

Listens to ``EpisodeStore`` state transitions and mirrors approved /
disabled episodes into the knowledge graph as
``episode_supports_profile`` edges.

Design notes (from
``docs/audits/multilayer-memory-phase-d-design-audit-2026-05-21.md``
§ D.5):

- Only writes on transitions **into** ``approved`` (or back to
  ``approved`` from ``disabled``); writes on transition **into**
  ``disabled`` flip the edge ``status='disabled'``.
- ``enabled_for_prompt`` does **not** trigger a graph write — the edge
  already exists from the prior ``approved`` step. ``disabled``
  cleanup applies regardless of which active state preceded it.
- Edge shape: ``edge_type='episode_supports_profile'``,
  ``from_node`` = episode node (``source_table='episodes'``,
  ``source_id=episode_id``), ``to_node`` = group profile node
  (``source_table='groups'``, ``source_id=group_id``).
- ``confidence`` mirrors ``Episode.confidence``;
  ``evidence_refs=(episode_id,)`` so admin's graph view can drill back.
- Graph write failures are swallowed and logged at WARN — graph is an
  auxiliary index, audit § D.5 explicitly forbids rolling back the
  state transition on graph-write errors. The listener wrapper in
  ``EpisodeStore._fire_transition_listeners`` provides a second layer
  of suppression for any unexpected exception.
- Episodes with empty ``group_id`` (rare; ``scope='global'`` only) are
  skipped — the to-node would be ambiguous.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from services.knowledge_graph.dual_write import ensure_graph_node
from services.knowledge_graph.graph_writer import GraphWriter
from services.knowledge_graph.types import GraphEdgeDraft

if TYPE_CHECKING:
    from services.episodic.store import Episode, EpisodeStore

_L = logger.bind(channel="episode_graph")

EPISODE_SOURCE_TABLE = "episodes"
GROUP_SOURCE_TABLE = "groups"
EDGE_TYPE = "episode_supports_profile"


class EpisodeGraphBridge:
    """Mirrors EpisodeStore state transitions into knowledge_graph edges."""

    def __init__(self, writer: GraphWriter) -> None:
        self._writer = writer

    def attach(self, store: EpisodeStore) -> None:
        """Subscribe to ``store``'s transition listener fan-out.

        Idempotent — calling ``attach`` twice on the same store would
        register the bridge twice, which is on the caller; chat plugin
        startup wires this exactly once.
        """
        store.add_transition_listener(self._on_transition)

    async def _on_transition(
        self,
        episode: Episode,
        prev_state: str,
        new_state: str,
        actor: str,
    ) -> None:
        if not episode.group_id:
            return
        if new_state == "approved":
            await self._upsert_edge(episode, status="active")
        elif new_state == "disabled":
            await self._revoke_edge(episode)

    async def _upsert_edge(self, episode: Episode, *, status: str) -> None:
        try:
            ep_node_id = await ensure_graph_node(
                self._writer,
                node_type="episode",
                source_table=EPISODE_SOURCE_TABLE,
                source_id=episode.episode_id,
                scope=episode.scope,
                group_id=episode.group_id,
                label=episode.situation[:80] or episode.episode_id,
                properties={
                    "confidence": episode.confidence,
                    "source": episode.source,
                },
            )
            group_node_id = await ensure_graph_node(
                self._writer,
                node_type="group",
                source_table=GROUP_SOURCE_TABLE,
                source_id=episode.group_id,
                scope="group",
                group_id=episode.group_id,
                label=f"group:{episode.group_id}",
            )
            draft = GraphEdgeDraft(
                edge_type=EDGE_TYPE,
                from_node_id=ep_node_id,
                to_node_id=group_node_id,
                scope="group",
                group_id=episode.group_id,
                confidence=float(episode.confidence),
                evidence_refs=(episode.episode_id,),
                properties={"episode_state": episode.episode_state},
            )
            await self._writer.write_edge(draft)
            # write_edge defaults to status='active' on insert; upsert
            # path leaves status untouched, so make sure a previously
            # disabled edge is reactivated when the episode comes back
            # via disabled→approved.
            if status == "active":
                await self._writer.set_edge_status(
                    edge_type=EDGE_TYPE,
                    from_node_id=ep_node_id,
                    to_node_id=group_node_id,
                    status="active",
                )
            _L.info(
                "episode_supports_profile edge upserted | episode={} group={}",
                episode.episode_id, episode.group_id,
            )
        except Exception as exc:
            _L.warning(
                "episode_supports_profile edge upsert failed | "
                "episode={} group={} err={}",
                episode.episode_id, episode.group_id, exc,
            )

    async def _revoke_edge(self, episode: Episode) -> None:
        try:
            ep_node = await self._writer.get_node_by_source(
                EPISODE_SOURCE_TABLE, episode.episode_id,
            )
            if ep_node is None:
                # Episode was disabled before it was ever approved → no
                # edge ever existed. Nothing to revoke.
                return
            group_node = await self._writer.get_node_by_source(
                GROUP_SOURCE_TABLE, episode.group_id,
            )
            if group_node is None:
                return
            updated = await self._writer.set_edge_status(
                edge_type=EDGE_TYPE,
                from_node_id=ep_node.node_id,
                to_node_id=group_node.node_id,
                status="disabled",
            )
            if updated:
                _L.info(
                    "episode_supports_profile edge revoked | "
                    "episode={} group={}",
                    episode.episode_id, episode.group_id,
                )
        except Exception as exc:
            _L.warning(
                "episode_supports_profile edge revoke failed | "
                "episode={} group={} err={}",
                episode.episode_id, episode.group_id, exc,
            )
