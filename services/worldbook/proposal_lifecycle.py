"""Deterministic Dream proposal validate / commit lifecycle (no LLM).

Keeps EventProposal immutable (status=proposal forever). Decisions and
commit records live in separate stores under Worldbook state_dir.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from typing import Any

from loguru import logger

from services.worldbook.domain import (
    EventProposal,
    EventRecord,
    FictionCommitRecord,
    ProposalDecision,
    ProposalProcessResult,
    TypedStoryletEffects,
    deterministic_commit_id,
    deterministic_decision_id,
    deterministic_dream_event_id,
    find_forbidden_proposal_marker,
    parse_dream_fiction_effects,
    proposal_content_fingerprint,
    utc_now_iso,
)
from services.worldbook.reducer import EventReducer
from services.worldbook.store import CommitStore, DecisionStore, ProposalStore

_L = logger.bind(channel="worldbook.proposal_lifecycle")


class ProposalLifecycle:
    """Validate and commit Dream fiction proposals against StoryArcStore."""

    def __init__(
        self,
        *,
        proposal_store: ProposalStore,
        decision_store: DecisionStore,
        commit_store: CommitStore,
        story_arc_store: Any | None,
        reducer: EventReducer,
        apply_partner_updates: Any,
        apply_life_updates: Any,
        partner_entity_known: Any,
    ) -> None:
        self._proposals = proposal_store
        self._decisions = decision_store
        self._commits = commit_store
        self._arc_store = story_arc_store
        self._reducer = reducer
        self._apply_partner_updates = apply_partner_updates
        self._apply_life_updates = apply_life_updates
        self._partner_entity_known = partner_entity_known

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, proposal_id: str) -> ProposalDecision:
        """Validate (or load existing decision) for a proposal. Never mutates proposal."""
        pid = str(proposal_id or "").strip()
        if not pid:
            raise ValueError("proposal_id is required")

        existing = self._decisions.load_for_proposal(pid)
        if existing is not None:
            return existing

        proposal = self._proposals.load(pid)
        if proposal is None:
            return self._persist_reject(
                proposal_id=pid,
                reason_code="missing_proposal",
                reason="proposal does not exist",
                arc_id="",
            )

        return self._validate_proposal(proposal)

    def commit(self, proposal_id: str) -> FictionCommitRecord:
        """Commit a previously validated decision. Rejected decisions never commit."""
        pid = str(proposal_id or "").strip()
        if not pid:
            raise ValueError("proposal_id is required")

        existing_commit = self._commits.load_for_proposal(pid)
        if existing_commit is not None:
            return self._handle_existing_commit(pid, existing_commit)

        decision = self._decisions.load_for_proposal(pid)
        if decision is None:
            decision = self.validate(pid)

        if decision.status != "validated":
            raise RuntimeError(
                f"cannot commit rejected decision proposal_id={pid!r} "
                f"reason={decision.reason_code}"
            )

        return self._commit_validated(decision)

    def process(self, proposal_id: str) -> ProposalProcessResult:
        """Validate then commit when possible; rejections leave no Arc mutation."""
        pid = str(proposal_id or "").strip()
        decision = self.validate(pid)
        if decision.status != "validated":
            return ProposalProcessResult(proposal_id=pid, decision=decision)
        try:
            record = self.commit(pid)
        except Exception as exc:
            _L.warning(
                "proposal commit failed after validate | proposal_id={} err={}",
                pid,
                exc,
            )
            return ProposalProcessResult(proposal_id=pid, decision=decision)
        event = self._load_event_from_arc(record.arc_id, record.event_id)
        return ProposalProcessResult(
            proposal_id=pid,
            decision=decision,
            commit=record,
            event=event,
        )

    # ------------------------------------------------------------------
    # Validate internals
    # ------------------------------------------------------------------

    def _validate_proposal(self, proposal: EventProposal) -> ProposalDecision:
        pid = proposal.proposal_id
        arc_id = str(proposal.arc_id or "").strip()
        fingerprint = proposal_content_fingerprint(proposal)

        if str(proposal.status) != "proposal":
            return self._persist_reject(
                proposal_id=pid,
                reason_code="invalid_proposal_status",
                reason=f"proposal status must be proposal, got {proposal.status!r}",
                arc_id=arc_id,
                proposal_fingerprint=fingerprint,
            )
        if str(proposal.source) not in {"dream_proposal", "system"}:
            return self._persist_reject(
                proposal_id=pid,
                reason_code="invalid_source",
                reason=f"source must be dream_proposal/system, got {proposal.source!r}",
                arc_id=arc_id,
                proposal_fingerprint=fingerprint,
            )
        kind = str(proposal.kind or "").strip()
        if kind not in {"arc_replan", "reflection", "fiction_event"}:
            return self._persist_reject(
                proposal_id=pid,
                reason_code="invalid_kind",
                reason=f"kind must be arc_replan|reflection|fiction_event, got {kind!r}",
                arc_id=arc_id,
                proposal_fingerprint=fingerprint,
            )

        # Forbidden metadata in summary bag is not scanned as prose; only
        # structured payload + explicit control fields on the proposal dict.
        summary_marker = find_forbidden_proposal_marker(
            {"summary_meta": {}},  # no-op placeholder
        )
        _ = summary_marker
        payload_marker = find_forbidden_proposal_marker(dict(proposal.payload))
        if payload_marker is not None:
            return self._persist_reject(
                proposal_id=pid,
                reason_code="forbidden_metadata",
                reason=payload_marker,
                arc_id=arc_id,
                proposal_fingerprint=fingerprint,
            )
        # Also scan the proposal envelope keys that could smuggle controls.
        envelope_marker = find_forbidden_proposal_marker(
            {
                "kind": kind,
                "source": proposal.source,
                "payload": dict(proposal.payload),
            }
        )
        if envelope_marker is not None:
            return self._persist_reject(
                proposal_id=pid,
                reason_code="forbidden_metadata",
                reason=envelope_marker,
                arc_id=arc_id,
                proposal_fingerprint=fingerprint,
            )

        if not arc_id:
            return self._persist_reject(
                proposal_id=pid,
                reason_code="missing_arc_id",
                reason="target arc_id is required for fiction commit",
                arc_id="",
                proposal_fingerprint=fingerprint,
            )

        arc = self._load_arc(arc_id)
        if arc is None:
            return self._persist_reject(
                proposal_id=pid,
                reason_code="missing_arc",
                reason=f"target arc {arc_id!r} does not exist",
                arc_id=arc_id,
                proposal_fingerprint=fingerprint,
            )
        scope = str(getattr(arc, "scope", "") or "").strip().lower()
        if scope != "fiction":
            return self._persist_reject(
                proposal_id=pid,
                reason_code="non_fiction_arc",
                reason=f"target arc scope must be fiction, got {scope!r}",
                arc_id=arc_id,
                proposal_fingerprint=fingerprint,
            )

        try:
            effects, normalized = parse_dream_fiction_effects(
                dict(proposal.payload),
                kind=kind,
            )
        except (TypeError, ValueError) as exc:
            return self._persist_reject(
                proposal_id=pid,
                reason_code="invalid_effects",
                reason=str(exc),
                arc_id=arc_id,
                proposal_fingerprint=fingerprint,
            )

        # Known fiction partners only.
        for partner_item in effects.partner_updates:
            entity_id = str(partner_item.get("entity_id") or "").strip()
            if not self._partner_entity_known(entity_id, arc=arc):
                return self._persist_reject(
                    proposal_id=pid,
                    reason_code="unknown_partner",
                    reason=f"unknown fiction partner {entity_id!r}",
                    arc_id=arc_id,
                    normalized_payload=normalized,
                    proposal_fingerprint=fingerprint,
                )

        decision = ProposalDecision(
            decision_id=deterministic_decision_id(pid),
            proposal_id=pid,
            status="validated",
            reason_code="ok",
            reason="validated",
            arc_id=arc_id,
            normalized_payload=normalized,
            effects=effects,
            decided_at=utc_now_iso(),
            proposal_fingerprint=fingerprint,
        )
        return self._decisions.save_decision(decision)

    def _persist_reject(
        self,
        *,
        proposal_id: str,
        reason_code: str,
        reason: str,
        arc_id: str,
        normalized_payload: Mapping[str, Any] | None = None,
        proposal_fingerprint: str = "",
    ) -> ProposalDecision:
        decision = ProposalDecision(
            decision_id=deterministic_decision_id(proposal_id),
            proposal_id=proposal_id,
            status="rejected",
            reason_code=reason_code,
            reason=reason,
            arc_id=arc_id,
            normalized_payload=dict(normalized_payload or {}),
            effects=None,
            decided_at=utc_now_iso(),
            proposal_fingerprint=proposal_fingerprint,
        )
        return self._decisions.save_decision(decision)

    # ------------------------------------------------------------------
    # Commit internals
    # ------------------------------------------------------------------

    def _require_bound_proposal(
        self,
        decision: ProposalDecision,
    ) -> EventProposal:
        """Reload proposal and require exact fingerprint match with decision."""
        pid = decision.proposal_id
        expected_fp = str(decision.proposal_fingerprint or "").strip()
        if not expected_fp:
            raise RuntimeError(
                f"proposal_changed/fingerprint missing on decision "
                f"proposal_id={pid!r}"
            )
        proposal = self._proposals.load(pid)
        if proposal is None:
            raise RuntimeError(f"proposal {pid!r} missing at commit")
        live_fp = proposal_content_fingerprint(proposal)
        if live_fp != expected_fp:
            raise RuntimeError(
                f"proposal_changed/fingerprint mismatch proposal_id={pid!r}: "
                f"decision bound to {expected_fp[:12]}… live={live_fp[:12]}…"
            )
        return proposal

    def _handle_existing_commit(
        self,
        proposal_id: str,
        existing_commit: FictionCommitRecord,
    ) -> FictionCommitRecord:
        """Crash catch-up only when commit record identity + Arc event are true."""
        decision = self._decisions.load_for_proposal(proposal_id)
        if decision is None or decision.status != "validated":
            # Fail closed: an orphan or rejected-backed commit record must not
            # return success or run Life/partner catch-up.
            status = (
                str(decision.status)
                if decision is not None
                else "missing"
            )
            raise RuntimeError(
                f"commit_record_without_validated_decision "
                f"proposal_id={proposal_id!r} decision_status={status!r}"
            )

        # Bind decision to current proposal content (fail closed on tamper).
        self._require_bound_proposal(decision)

        # Record identity must match decision + deterministic event id.
        expected_decision_id = decision.decision_id
        expected_arc_id = str(decision.arc_id or "").strip()
        expected_event_id = deterministic_dream_event_id(
            proposal_id, expected_arc_id
        )
        expected_commit_id = deterministic_commit_id(
            proposal_id, expected_decision_id
        )

        if str(existing_commit.proposal_id or "").strip() != proposal_id:
            raise RuntimeError(
                f"commit_record mismatch proposal_id expected={proposal_id!r} "
                f"got={existing_commit.proposal_id!r}"
            )
        if str(existing_commit.decision_id or "").strip() != expected_decision_id:
            raise RuntimeError(
                f"commit_record mismatch decision_id expected={expected_decision_id!r} "
                f"got={existing_commit.decision_id!r}"
            )
        if str(existing_commit.arc_id or "").strip() != expected_arc_id:
            raise RuntimeError(
                f"commit_record mismatch arc_id expected={expected_arc_id!r} "
                f"got={existing_commit.arc_id!r}"
            )
        if str(existing_commit.event_id or "").strip() != expected_event_id:
            raise RuntimeError(
                f"commit_record mismatch event_id expected={expected_event_id!r} "
                f"got={existing_commit.event_id!r}"
            )
        if str(existing_commit.commit_id or "").strip() != expected_commit_id:
            raise RuntimeError(
                f"commit_record mismatch commit_id expected={expected_commit_id!r} "
                f"got={existing_commit.commit_id!r}"
            )

        # Arc truth: committed-event ledger (and history evidence when present).
        self._require_arc_event_truth(
            arc_id=expected_arc_id,
            event_id=expected_event_id,
            proposal_id=proposal_id,
            decision_id=expected_decision_id,
        )

        # Only after Arc truth may side-effect catch-up run.
        self._catch_up_side_effects(decision, expected_event_id)
        return existing_commit

    def _require_arc_event_truth(
        self,
        *,
        arc_id: str,
        event_id: str,
        proposal_id: str,
        decision_id: str,
    ) -> None:
        arc = self._load_arc(arc_id)
        if arc is None:
            raise RuntimeError(
                f"commit_record_arc_event_missing arc_id={arc_id!r} "
                f"event_id={event_id!r} proposal_id={proposal_id!r}"
            )
        committed = self._committed_event_ids(arc)
        if event_id not in committed:
            raise RuntimeError(
                f"commit_record_arc_event_missing event_id={event_id!r} "
                f"arc_id={arc_id!r} proposal_id={proposal_id!r}"
            )
        # Prefer verifying event_history / last_events when the event is present
        # there with dream source + proposal/decision evidence refs.
        history_item = self._find_history_event(arc, event_id)
        if history_item is not None:
            source = str(history_item.get("source") or "").strip()
            if source and source != "dream_proposal":
                raise RuntimeError(
                    f"commit_record_arc_event_mismatch event_id={event_id!r}: "
                    f"source={source!r}"
                )
            evidence = history_item.get("evidence_refs")
            if isinstance(evidence, (list, tuple)):
                refs = {str(x).strip() for x in evidence if str(x).strip()}
                need_p = f"proposal:{proposal_id}"
                need_d = f"decision:{decision_id}"
                if refs and (need_p not in refs or need_d not in refs):
                    raise RuntimeError(
                        f"commit_record_arc_event_mismatch event_id={event_id!r}: "
                        f"evidence_refs missing proposal/decision binding"
                    )

    def _find_history_event(
        self,
        arc: Any,
        event_id: str,
    ) -> Mapping[str, Any] | None:
        for collection_name in ("event_history", "last_events"):
            collection = getattr(arc, collection_name, None)
            if not isinstance(collection, list):
                continue
            for item in collection:
                if not isinstance(item, Mapping):
                    continue
                if str(item.get("event_id") or "").strip() == event_id:
                    return item
        return None

    def _commit_validated(self, decision: ProposalDecision) -> FictionCommitRecord:
        if decision.status != "validated":
            raise RuntimeError("only validated decisions may commit")

        pid = decision.proposal_id
        arc_id = str(decision.arc_id or "").strip()
        if not arc_id:
            raise RuntimeError("validated decision missing arc_id")

        proposal = self._require_bound_proposal(decision)
        if str(proposal.arc_id or "").strip() != arc_id:
            raise RuntimeError("proposal/decision arc_id mismatch at commit")

        event_id = deterministic_dream_event_id(pid, arc_id)
        commit_id = deterministic_commit_id(pid, decision.decision_id)
        effects = decision.effects or TypedStoryletEffects()
        normalized = dict(decision.normalized_payload or {})
        severity = str(normalized.get("severity") or "daily").strip().lower() or "daily"
        recovery_steps = max(0, int(normalized.get("recovery_steps") or 0))
        summary = str(proposal.summary or "").strip()
        open_threads = effects.open_threads
        next_seed = str(normalized.get("next_day_seed") or "").strip()

        store = self._arc_store
        if store is None:
            raise RuntimeError("story_arc_store required for fiction commit")
        update = getattr(store, "update", None)
        if not callable(update):
            raise RuntimeError("StoryArcStore.update is mandatory for fiction commit")

        outcome: dict[str, Any] = {
            "status": "rejected",
            "reason": "not_applied",
            "event": None,
        }

        def _mutate(arc: Any) -> None:
            # Recheck fiction scope + identity inside the lock.
            live_arc_id = str(getattr(arc, "arc_id", "") or "").strip()
            if live_arc_id != arc_id:
                outcome["status"] = "rejected"
                outcome["reason"] = "arc_id_mismatch"
                return
            scope = str(getattr(arc, "scope", "") or "").strip().lower()
            if scope != "fiction":
                outcome["status"] = "rejected"
                outcome["reason"] = "non_fiction_arc"
                return

            budget_raw = getattr(arc, "event_budget", None)
            live_budget = dict(budget_raw) if isinstance(budget_raw, Mapping) else {}
            step = self._resolve_now_step(live_budget)

            # Idempotent: event already committed → catch-up only.
            committed_ids = self._committed_event_ids(arc)
            if event_id in committed_ids:
                outcome["status"] = "already_committed"
                outcome["reason"] = "idempotent_replay"
                outcome["event"] = EventRecord(
                    event_id=event_id,
                    event_type=severity,
                    summary=summary,
                    status="committed",
                    arc_id=arc_id,
                    variable_deltas=dict(effects.variable_deltas),
                    consequences=tuple(open_threads),
                    recovery_steps=recovery_steps,
                    severity=severity,
                    source="dream_proposal",
                    evidence_refs=(
                        f"proposal:{pid}",
                        f"decision:{decision.decision_id}",
                    ),
                    committed_at=utc_now_iso(),
                    step=step,
                )
                return

            event = EventRecord(
                event_id=event_id,
                event_type=severity,
                summary=summary,
                status="committed",
                arc_id=arc_id,
                variable_deltas=dict(effects.variable_deltas),
                consequences=tuple(open_threads),
                recovery_steps=recovery_steps,
                severity=severity,
                source="dream_proposal",
                evidence_refs=(
                    f"proposal:{pid}",
                    f"decision:{decision.decision_id}",
                ),
                committed_at=utc_now_iso(),
                step=step,
            )
            # Exactly one EventReducer apply; no Storylet selection budget.
            applied = self._reducer.apply(arc, event, now_step=step)

            # Resolve + open threads + stage + next_day_seed from decision.
            open_list = getattr(arc, "open_threads", None)
            if isinstance(open_list, list):
                if effects.resolve_threads:
                    resolve_set = set(effects.resolve_threads)
                    open_list[:] = [
                        t for t in open_list if str(t).strip() not in resolve_set
                    ]
                for thread in effects.open_threads:
                    text = str(thread).strip()
                    if text and text not in open_list:
                        open_list.append(text)

            if effects.stage:
                with contextlib.suppress(Exception):
                    arc.stage = str(effects.stage)
            if next_seed:
                with contextlib.suppress(Exception):
                    arc.next_day_seed = next_seed

            outcome["status"] = "committed"
            outcome["reason"] = "ok"
            outcome["event"] = applied

        try:
            update(arc_id, _mutate)
        except Exception as exc:
            _L.warning(
                "dream commit arc update failed | proposal_id={} arc_id={} err={}",
                pid,
                arc_id,
                exc,
            )
            raise RuntimeError(f"arc_update_failed:{type(exc).__name__}") from exc

        status = str(outcome.get("status") or "rejected")
        if status not in {"committed", "already_committed"}:
            raise RuntimeError(
                f"arc commit rejected reason={outcome.get('reason')!r}"
            )

        # Side effects after Arc success (including replay catch-up).
        self._apply_partner_updates(
            effects.partner_updates,
            event_id=event_id,
            arc_id=arc_id,
        )
        self._apply_life_updates(
            effects.life_updates,
            event_id=event_id,
        )

        record = FictionCommitRecord(
            commit_id=commit_id,
            proposal_id=pid,
            decision_id=decision.decision_id,
            event_id=event_id,
            arc_id=arc_id,
            status="committed",
            committed_at=utc_now_iso(),
        )
        try:
            return self._commits.save_commit(record)
        except Exception as exc:
            # Arc + side effects already applied; re-raise so caller knows
            # record write failed (replay will catch up record + side effects).
            _L.warning(
                "commit record save failed after arc | proposal_id={} err={}",
                pid,
                exc,
            )
            raise

    def _catch_up_side_effects(
        self,
        decision: ProposalDecision,
        event_id: str,
    ) -> None:
        effects = decision.effects or TypedStoryletEffects()
        arc_id = str(decision.arc_id or "").strip()
        if not arc_id:
            return
        self._apply_partner_updates(
            effects.partner_updates,
            event_id=event_id,
            arc_id=arc_id,
        )
        self._apply_life_updates(
            effects.life_updates,
            event_id=event_id,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_arc(self, arc_id: str) -> Any | None:
        store = self._arc_store
        if store is None:
            return None
        load = getattr(store, "load", None)
        if not callable(load):
            return None
        try:
            return load(arc_id)
        except Exception:
            return None

    def _load_event_from_arc(self, arc_id: str, event_id: str) -> EventRecord | None:
        arc = self._load_arc(arc_id)
        if arc is None:
            return None
        for collection_name in ("event_history", "last_events"):
            collection = getattr(arc, collection_name, None)
            if not isinstance(collection, list):
                continue
            for item in collection:
                if not isinstance(item, Mapping):
                    continue
                if str(item.get("event_id") or "").strip() != event_id:
                    continue
                try:
                    return EventRecord.from_dict(dict(item))
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _committed_event_ids(arc: Any) -> set[str]:
        budget = getattr(arc, "event_budget", None)
        if not isinstance(budget, Mapping):
            return set()
        raw = budget.get("committed_event_ids")
        if not isinstance(raw, list):
            return set()
        return {str(x).strip() for x in raw if str(x).strip()}

    @staticmethod
    def _resolve_now_step(arc_budget: Mapping[str, Any] | None) -> int:
        if not isinstance(arc_budget, Mapping):
            return 0
        best = 0
        found = False
        for key in ("last_event_step", "generated_days", "now_step", "step"):
            raw = arc_budget.get(key)
            if raw is None or raw == "":
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value < 0:
                continue
            if not found or value > best:
                best = value
                found = True
        return best if found else 0
