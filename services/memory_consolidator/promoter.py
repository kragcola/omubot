"""EpisodePromoter — D.1 promote bridge.

Wraps the read-only handoff from a Phase C ``consolidator_candidates``
row (``domain="episode"``) to a Phase D ``EpisodeStore.create_episode``.
The candidate's typed payload is projected via :func:`normalize_payload`,
inserted as a fresh episode, and advanced through ``dry_run → candidate →
approved`` because the source candidate already passed human review. The
separate ``approved → enabled_for_prompt`` gate remains explicit. The candidate row is
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

import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from services.memory.entity_identity import make_entity_ref
from services.memory.linked_refs import make_linked_ref, normalize_linked_refs, parse_linked_ref
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
        message_archive: Any = None,
        card_store: Any = None,
        knowledge_graph: Any = None,
        entity_alias_store: Any = None,
    ) -> None:
        self._candidates = candidates_store
        self._episodes = episode_store
        self._message_archive = message_archive
        self._card_store = card_store
        self._knowledge_graph = knowledge_graph
        self._entity_alias_store = entity_alias_store

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
            transition_error = await self._ensure_approved(existing, actor=actor)
            return PromoteResult(
                candidate_id=candidate_id,
                episode_id=existing.episode_id,
                skipped_reason=transition_error or "already_promoted",
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

        linked_refs = await self._build_linked_refs(candidate)
        meta = self._build_meta(candidate, actor=actor)
        meta["linked_ref_version"] = 1
        meta["linked_ref_counts"] = _linked_ref_counts(linked_refs)

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
                linked_memory_ids=list(linked_refs),
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
        transition_error = await self._ensure_approved(episode, actor=actor)
        if transition_error:
            return PromoteResult(
                candidate_id=candidate_id,
                episode_id=episode.episode_id,
                skipped_reason=transition_error,
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

    async def _ensure_approved(
        self,
        episode: Episode,
        *,
        actor: str,
    ) -> str:
        """Advance a derived episode to approved without bypassing its state machine."""
        state = episode.episode_state
        if state in {"approved", "enabled_for_prompt"}:
            return ""
        if state == "disabled":
            return "episode_disabled"
        try:
            if state == "dry_run":
                await self._episodes.transition_state(
                    episode.episode_id,
                    new_state="candidate",
                    actor=actor,
                    reason="consolidator candidate approved",
                )
                state = "candidate"
            if state == "candidate":
                await self._episodes.transition_state(
                    episode.episode_id,
                    new_state="approved",
                    actor=actor,
                    reason="consolidator candidate approved",
                )
                return ""
        except Exception as exc:
            _L.warning(
                "episode promote state transition failed | candidate={} episode={} "
                "state={} error={}",
                episode.meta.get("consolidator_candidate_id", ""),
                episode.episode_id,
                state,
                exc,
            )
            return f"state_transition_failed:{type(exc).__name__}"
        return f"unsupported_episode_state:{state}"

    async def _find_existing_episode(
        self, candidate_id: str,
    ) -> Episode | None:
        """Idempotency check via ``meta.consolidator_candidate_id``.

        Delegates to :meth:`EpisodeStore.find_by_source_meta` so the lookup
        is database-wide (not a recent-N Python scan). A prior promote for
        the same candidate remains durable even when many newer episodes
        exist.
        """
        return await self._episodes.find_by_source_meta(
            source="consolidator",
            meta_key="consolidator_candidate_id",
            meta_value=candidate_id,
        )

    @staticmethod
    def _build_meta(candidate: Candidate, *, actor: str) -> dict[str, Any]:
        return {
            "consolidator_candidate_id": candidate.candidate_id,
            "consolidator_run_id": candidate.run_id,
            "normalizer_cluster_id": candidate.normalizer_cluster_id,
            "source_message_pks": list(candidate.source_message_pks),
            "promoted_by": actor,
        }

    async def _build_linked_refs(self, candidate: Candidate) -> tuple[str, ...]:
        refs: list[str] = []
        group_id = str(candidate.group_id or "").strip()
        usable_group = candidate.scope != "global" and group_id.isdigit()
        if usable_group:
            group_ref = make_entity_ref(
                kind="group",
                scope="group",
                scope_id=group_id,
                display=group_id,
                platform_id=group_id,
            )
            refs.append(make_linked_ref(kind="entity", target_id=group_ref.entity_key).canonical)

        message_pks = _positive_ints(candidate.source_message_pks)
        refs.extend(
            make_linked_ref(kind="message_pk", target_id=message_pk).canonical
            for message_pk in message_pks
        )

        # Without a numeric group scope we keep raw message_pk refs only —
        # never hydrate message/user/card/fact unscoped (cross-group poison risk).
        if not usable_group or not message_pks:
            return normalize_linked_refs(refs, preserve_legacy=False)

        rows: list[dict[str, Any]] = []
        if self._message_archive is not None:
            try:
                raw_rows = await self._message_archive.get_messages_by_pks(
                    message_pks,
                    chat_type="group",
                    chat_id=group_id,
                )
                rows = [dict(row) for row in (raw_rows or []) if isinstance(row, dict)]
            except Exception as exc:
                _L.warning(
                    "episode link archive lookup failed | candidate={} error={}",
                    candidate.candidate_id,
                    exc,
                )

        platform_message_ids: list[str] = []
        proven_user_ids: set[str] = set()
        seen_alias_observations: set[tuple[str, str, str]] = set()
        for row in rows:
            message_id = str(row.get("message_id") or "").strip()
            if message_id and message_id not in platform_message_ids:
                platform_message_ids.append(message_id)
                refs.append(make_linked_ref(kind="message", target_id=message_id).canonical)
            if str(row.get("role") or "").strip().lower() != "user":
                continue
            parsed_speaker = _parse_archive_speaker(str(row.get("speaker") or ""))
            if parsed_speaker is None:
                continue
            user_id, alias_surface = parsed_speaker
            proven_user_ids.add(user_id)
            user_ref = make_entity_ref(
                kind="user",
                scope="user",
                scope_id=user_id,
                display=alias_surface or user_id,
                platform_id=user_id,
            )
            refs.append(make_linked_ref(kind="entity", target_id=user_ref.entity_key).canonical)
            observation_key = (user_ref.entity_key, alias_surface, group_id)
            if (
                alias_surface
                and alias_surface != user_id
                and group_id
                and self._entity_alias_store is not None
                and observation_key not in seen_alias_observations
            ):
                seen_alias_observations.add(observation_key)
                try:
                    await self._entity_alias_store.observe(
                        entity_key=user_ref.entity_key,
                        alias=alias_surface,
                        scope="group",
                        scope_id=group_id,
                        confidence=0.5,
                        source="archive_speaker",
                    )
                except Exception as exc:
                    _L.warning(
                        "episode link alias observe failed | candidate={} entity={} error={}",
                        candidate.candidate_id,
                        user_ref.entity_key,
                        exc,
                    )

        allowed_scopes: set[tuple[str, str]] = {("group", group_id)}
        allowed_scopes.update(("user", uid) for uid in proven_user_ids)

        card_ids: list[str] = []
        if platform_message_ids and self._card_store is not None:
            try:
                cards = await self._card_store.find_by_source_message_ids(
                    platform_message_ids,
                    allowed_scopes=allowed_scopes,
                )
                for card in cards or []:
                    card_id = str(
                        card.get("card_id")
                        if isinstance(card, dict)
                        else getattr(card, "card_id", "")
                    ).strip()
                    if card_id and card_id not in card_ids:
                        card_ids.append(card_id)
                        refs.append(make_linked_ref(kind="card", target_id=card_id).canonical)
            except Exception as exc:
                _L.warning(
                    "episode link card lookup failed | candidate={} error={}",
                    candidate.candidate_id,
                    exc,
                )

        evidence_ids = [*card_ids, *platform_message_ids]
        if evidence_ids and self._knowledge_graph is not None:
            try:
                fact_ids = await self._knowledge_graph.find_fact_ids_by_evidence_refs(
                    evidence_ids,
                    allowed_scopes=allowed_scopes,
                )
                for fact_id in fact_ids or []:
                    clean_fact_id = str(fact_id or "").strip()
                    if clean_fact_id:
                        refs.append(make_linked_ref(kind="fact", target_id=clean_fact_id).canonical)
            except Exception as exc:
                _L.warning(
                    "episode link graph lookup failed | candidate={} error={}",
                    candidate.candidate_id,
                    exc,
                )

        return normalize_linked_refs(refs, preserve_legacy=False)


def _positive_ints(values: list[Any]) -> list[int]:
    result: list[int] = []
    for raw in values:
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            value = raw
        elif isinstance(raw, str) and raw.strip().isdecimal():
            value = int(raw.strip())
        else:
            continue
        if value <= 0 or value in result:
            continue
        result.append(value)
    return result


_ARCHIVE_SPEAKER_RE = re.compile(r"^(?P<alias>.*)\((?P<user_id>\d+)\)\s*$")


def _parse_archive_speaker(value: str) -> tuple[str, str] | None:
    match = _ARCHIVE_SPEAKER_RE.fullmatch(str(value or "").strip())
    if match is None:
        return None
    user_id = str(int(match.group("user_id")))
    alias_surface = match.group("alias").strip()
    return user_id, alias_surface


def _linked_ref_counts(values: tuple[str, ...]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for value in values:
        ref = parse_linked_ref(value)
        if ref is not None:
            counts[ref.kind] += 1
    return dict(counts)
