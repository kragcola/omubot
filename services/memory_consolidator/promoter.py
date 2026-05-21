"""EpisodePromoter — D.1 promote bridge.

Wraps the read-only handoff from a Phase C ``consolidator_candidates``
row (``domain="episode"``) to a Phase D ``EpisodeStore.create_episode``.
The candidate's typed payload is projected via :func:`normalize_payload`
and then inserted as a fresh ``dry_run`` episode; the candidate row is
**not** mutated here — admin :func:`decide_candidate` already wrote
``state="approved"`` before the promoter is called, and the audit chain
is preserved by stashing ``consolidator_candidate_id / run_id /
source_message_pks / normalizer_cluster_id`` into ``episodes.meta_json``.

Promotion is best-effort:

* Only ``domain == "episode"`` candidates are promoted; other domains
  silently no-op so the same hook can sit on every ``decide(approved)``.
* Promotion failures (e.g. EpisodeStore not initialized, schema drift)
  are logged at WARN; they never re-raise nor roll back the candidate
  state — the candidate row is the source of truth, the episode is a
  derived artifact.
* Idempotent on candidate_id: if a previous promote already produced an
  episode with the same ``meta.consolidator_candidate_id``, the second
  call is a no-op and returns the existing episode_id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.memory_consolidator.types import normalize_payload

if TYPE_CHECKING:
    from services.episodic.store import Episode, EpisodeStore
    from services.memory_consolidator.store import ConsolidatorCandidatesStore
    from services.memory_consolidator.types import Candidate

_L = logger.bind(channel="memory_consolidator")


@dataclass(slots=True)
class PromoteResult:
    candidate_id: str
    episode_id: str
    skipped_reason: str = ""

    @property
    def promoted(self) -> bool:
        return bool(self.episode_id) and not self.skipped_reason


class EpisodePromoter:
    """Promote ``domain="episode"`` candidates into EpisodeStore."""

    def __init__(
        self,
        *,
        candidates_store: ConsolidatorCandidatesStore,
        episode_store: EpisodeStore,
    ) -> None:
        self._candidates = candidates_store
        self._episodes = episode_store

    async def promote(
        self,
        candidate_id: str,
        *,
        actor: str = "admin",
    ) -> PromoteResult:
        candidate = await self._candidates.get_candidate(candidate_id)
        if candidate is None:
            return PromoteResult(
                candidate_id=candidate_id,
                episode_id="",
                skipped_reason="candidate_not_found",
            )
        if candidate.domain != "episode":
            return PromoteResult(
                candidate_id=candidate_id,
                episode_id="",
                skipped_reason=f"domain={candidate.domain}",
            )
        if candidate.state != "approved":
            return PromoteResult(
                candidate_id=candidate_id,
                episode_id="",
                skipped_reason=f"state={candidate.state}",
            )

        existing = await self._find_existing_episode(candidate_id)
        if existing is not None:
            return PromoteResult(
                candidate_id=candidate_id,
                episode_id=existing.episode_id,
                skipped_reason="already_promoted",
            )

        try:
            payload = normalize_payload("episode", candidate.payload)
        except ValueError as exc:
            _L.warning(
                "episode promote payload invalid | candidate={} error={}",
                candidate_id,
                exc,
            )
            return PromoteResult(
                candidate_id=candidate_id,
                episode_id="",
                skipped_reason="invalid_payload",
            )

        meta = self._build_meta(candidate, actor=actor)

        try:
            episode = await self._episodes.create_episode(
                situation=str(payload.get("situation", "")),
                observed_context=str(payload.get("observed_context", "")),
                action_taken=str(payload.get("action_taken", "")),
                outcome_signal=str(payload.get("outcome_signal", "")),
                reflection=str(payload.get("reflection", "")),
                group_id=str(candidate.group_id or ""),
                scope=str(candidate.scope) if candidate.scope == "global" else "group",
                source="consolidator",
                confidence=float(candidate.confidence),
                meta=meta,
            )
        except Exception as exc:
            _L.warning(
                "episode promote create_episode failed | candidate={} error={}",
                candidate_id,
                exc,
            )
            return PromoteResult(
                candidate_id=candidate_id,
                episode_id="",
                skipped_reason=f"create_failed:{type(exc).__name__}",
            )

        await self._episodes.record_revision(
            episode.episode_id,
            action="promote_from_candidate",
            actor=actor,
            prev_state="",
            new_state="dry_run",
            after={"consolidator_candidate_id": candidate_id},
            reason=f"promoted from consolidator candidate {candidate_id}",
            meta={
                "consolidator_run_id": candidate.run_id,
                "normalizer_cluster_id": candidate.normalizer_cluster_id,
            },
        )
        _L.info(
            "episode promoted | candidate={} episode={} group={} confidence={:.2f}",
            candidate_id,
            episode.episode_id,
            candidate.group_id,
            candidate.confidence,
        )
        return PromoteResult(
            candidate_id=candidate_id,
            episode_id=episode.episode_id,
        )

    async def _find_existing_episode(
        self, candidate_id: str,
    ) -> Episode | None:
        """Idempotency check via ``meta.consolidator_candidate_id``.

        Scans the most recent ``dry_run`` / ``candidate`` / ``approved``
        episodes with ``source='consolidator'`` and matches on the meta
        field. Limit kept small (≤200) since promote-then-decide is a
        narrow window — the candidate row is approved by an admin click,
        and a duplicate decide is the only realistic source of dupes.
        """
        # Use list_episodes without state filter to catch any prior state
        recents = await self._episodes.list_episodes(
            group_id="",
            state_filter=None,
            limit=200,
            offset=0,
        )
        for ep in recents:
            if ep.source != "consolidator":
                continue
            if str(ep.meta.get("consolidator_candidate_id", "")) == candidate_id:
                return ep
        return None

    @staticmethod
    def _build_meta(candidate: Candidate, *, actor: str) -> dict[str, Any]:
        return {
            "consolidator_candidate_id": candidate.candidate_id,
            "consolidator_run_id": candidate.run_id,
            "normalizer_cluster_id": candidate.normalizer_cluster_id,
            "source_message_pks": list(candidate.source_message_pks),
            "promoted_by": actor,
        }
