"""Worldbook runtime assembly — gated service surface for Stage-0 offline use."""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from loguru import logger

from services.worldbook.config import WorldbookConfig
from services.worldbook.domain import (
    SOCIAL_INFLUENCE_SUMMARY,
    SOCIAL_LIFE_KEY,
    SOCIAL_LIFE_TTL_HOURS,
    SOCIAL_LIFE_VALUE,
    SOCIAL_RESONANCE_DELTA,
    SOCIAL_RESONANCE_MAX,
    SOCIAL_RESONANCE_MIN,
    CanonMutationError,
    EventRecord,
    FictionCommitRecord,
    ProposalDecision,
    ProposalProcessResult,
    SocialEvidenceRef,
    SocialStoryCommitResult,
    Storylet,
    StoryletCommitResult,
    TypedStoryletEffects,
    deterministic_social_story_event_id,
    deterministic_storylet_event_id,
    parse_typed_storylet_effects,
    utc_now_iso,
)
from services.worldbook.drama import DramaManager, DramaSelection
from services.worldbook.dream_bridge import DreamProposalBridge
from services.worldbook.ledger import StoryLedgerAdapter
from services.worldbook.projection import ProjectionResult, PromptProjection
from services.worldbook.proposal_lifecycle import ProposalLifecycle
from services.worldbook.reducer import EventReducer
from services.worldbook.store import (
    CanonRegistry,
    CommitStore,
    DecisionStore,
    LifeStateStore,
    PersonaCanonRef,
    ProposalStore,
    StoryletRegistry,
)
from services.worldbook.trigger import WorldInfoTrigger

_L = logger.bind(channel="worldbook.runtime")
_CST = timezone(timedelta(hours=8))


class WorldbookRuntime:
    """Facade used by providers, schedule adapters, and tests.

    Construction is cheap. Heavy I/O (registry load) happens only when
    ``enabled=True`` and ``ensure_loaded()`` / projection is invoked.
    """

    def __init__(
        self,
        config: WorldbookConfig,
        *,
        root: str | Path | None = None,
        story_arc_store: Any | None = None,
        social_narrative_store: Any | None = None,
        partner_state_store: Any | None = None,
        persona_identity_text: str = "",
        persona_id: str = "",
        semantic_scorer: Any | None = None,
    ) -> None:
        self.config = config
        self._root = Path(root) if root is not None else Path(".")
        paths = config.resolve_paths(self._root)
        self._paths = paths
        self._canon = CanonRegistry(paths["canon_dir"])
        self._storylets = StoryletRegistry(paths["storylet_dir"])
        self._life = LifeStateStore(paths["state_dir"])
        self._proposals = ProposalStore(paths["state_dir"])
        self._decisions = DecisionStore(paths["state_dir"])
        self._commits = CommitStore(paths["state_dir"])
        self._persona = PersonaCanonRef(
            identity_text=persona_identity_text,
            persona_id=persona_id,
        )
        self._trigger = WorldInfoTrigger(semantic_scorer=semantic_scorer)
        self._drama = DramaManager(
            max_setbacks_per_arc=config.max_setbacks_per_arc,
            max_events_per_tick=config.max_events_per_tick,
            recovery_window_steps=config.recovery_window_steps,
        )
        self._projection = PromptProjection(config)
        self._reducer = EventReducer()
        self._ledger = StoryLedgerAdapter(story_arc_store)
        self._social_store = social_narrative_store
        self._partner_store = partner_state_store
        self._lifecycle = ProposalLifecycle(
            proposal_store=self._proposals,
            decision_store=self._decisions,
            commit_store=self._commits,
            story_arc_store=story_arc_store,
            reducer=self._reducer,
            apply_partner_updates=self._apply_partner_updates,
            apply_life_updates=self._apply_life_updates,
            partner_entity_known=self._partner_entity_known,
        )
        self._dream = DreamProposalBridge(
            self._proposals,
            lifecycle=self._lifecycle,
            enabled=bool(config.enabled and config.dream_proposal_enabled),
        )
        self._loaded = False
        self._io_performed = False

    @property
    def io_performed(self) -> bool:
        """True only if registry/state I/O happened (for disabled no-I/O probes)."""
        return self._io_performed

    @property
    def dream_bridge(self) -> DreamProposalBridge:
        return self._dream

    @property
    def proposal_store(self) -> ProposalStore:
        return self._proposals

    @property
    def decision_store(self) -> DecisionStore:
        return self._decisions

    @property
    def commit_store(self) -> CommitStore:
        return self._commits

    @property
    def proposal_lifecycle(self) -> ProposalLifecycle:
        return self._lifecycle

    def validate_proposal(self, proposal_id: str) -> ProposalDecision:
        """Deterministic validate/reject; never mutates EventProposal JSON."""
        self._io_performed = True
        return self._lifecycle.validate(proposal_id)

    def commit_validated_proposal(self, proposal_id: str) -> FictionCommitRecord:
        """Commit only a persisted validated decision; catch up after crash."""
        self._io_performed = True
        return self._lifecycle.commit(proposal_id)

    def process_proposal(self, proposal_id: str) -> ProposalProcessResult:
        """Validate then commit when possible (no LLM)."""
        self._io_performed = True
        return self._lifecycle.process(proposal_id)

    def validate_and_commit_proposal(self, proposal_id: str) -> ProposalProcessResult:
        """Alias for process_proposal (Dream bridge convenience)."""
        return self.process_proposal(proposal_id)

    @property
    def canon_registry(self) -> CanonRegistry:
        return self._canon

    @property
    def persona_canon(self) -> PersonaCanonRef:
        return self._persona

    @property
    def life_store(self) -> LifeStateStore:
        return self._life

    @property
    def storylet_registry(self) -> StoryletRegistry:
        return self._storylets

    @property
    def drama(self) -> DramaManager:
        return self._drama

    @property
    def reducer(self) -> EventReducer:
        return self._reducer

    @property
    def ledger(self) -> StoryLedgerAdapter:
        return self._ledger

    def ensure_loaded(self) -> None:
        if not self.config.enabled:
            return
        if self._loaded:
            return
        self._io_performed = True
        self._canon.load()
        self._storylets.load()
        self._trigger.set_entries(self._canon.list_entries())
        self._loaded = True
        _L.debug(
            "worldbook loaded | canon={} storylets={}",
            len(self._canon.list_entries()),
            len(self._storylets.list_storylets()),
        )

    def reject_canon_mutation(self, *_args: Any, **_kwargs: Any) -> None:
        raise CanonMutationError()

    def mutate_canon(self, *_args: Any, **_kwargs: Any) -> None:
        raise CanonMutationError()

    def select_storylets(
        self,
        *,
        arc_budget: dict[str, Any] | None = None,
        variables: dict[str, Any] | None = None,
        available_evidence: Sequence[str] | None = None,
        now_step: int = 0,
        limit: int | None = None,
    ) -> list[DramaSelection]:
        if not self.config.enabled or not self.config.storylet_enabled:
            return []
        self.ensure_loaded()
        return self._drama.select(
            self._storylets.list_storylets(),
            arc_budget=arc_budget,
            variables=variables,
            available_evidence=available_evidence,
            now_step=now_step,
            limit=limit,
        )

    def commit_event(self, arc: Any, event: EventRecord, *, now_step: int = 0) -> EventRecord:
        if not self.config.enabled:
            raise RuntimeError("worldbook.enabled is false; cannot commit events")
        return self._reducer.apply(arc, event, now_step=now_step)

    @staticmethod
    def _resolve_now_step(arc_budget: Mapping[str, Any] | None) -> int:
        """Resolve storylet clock as max of all valid nonnegative budget fields."""
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

    @staticmethod
    def _collect_available_evidence(
        ledger: Any,
        social: Sequence[SocialEvidenceRef] | None = None,
    ) -> list[str]:
        """Build activation evidence tokens from ledger arcs + social refs."""
        evidence: list[str] = []
        seen: set[str] = set()

        def _add(token: str) -> None:
            text = str(token or "").strip()
            if text and text not in seen:
                seen.add(text)
                evidence.append(text)

        for arc_id in getattr(ledger, "all_arc_ids", lambda: ())():
            aid = str(arc_id or "").strip()
            if aid:
                _add(f"arc:{aid}")
        for role_name in ("main", "sides", "ambient"):
            raw = getattr(ledger, role_name, None)
            arcs: list[Any]
            if role_name == "main":
                arcs = [raw] if isinstance(raw, Mapping) else []
            elif isinstance(raw, (list, tuple)):
                arcs = list(raw)
            else:
                arcs = []
            for arc in arcs:
                if not isinstance(arc, Mapping):
                    continue
                aid = str(arc.get("arc_id") or "").strip()
                if aid:
                    _add(f"arc:{aid}")
                # Optional explicit evidence tokens on the arc.
                extra = arc.get("available_evidence") or arc.get("evidence_refs")
                if isinstance(extra, (list, tuple)):
                    for item in extra:
                        _add(str(item))
        for ref in social or ():
            try:
                _add(ref.evidence_ref())
            except Exception:
                continue
        return evidence

    @staticmethod
    def _committed_event_ids_of(arc: Any) -> set[str]:
        budget = getattr(arc, "event_budget", None)
        if not isinstance(budget, Mapping):
            return set()
        raw = budget.get("committed_event_ids")
        if not isinstance(raw, list):
            return set()
        return {str(x).strip() for x in raw if str(x).strip()}

    def _parse_storylet_effects(
        self, storylet: Storylet
    ) -> tuple[TypedStoryletEffects | None, str]:
        try:
            effects = parse_typed_storylet_effects(storylet.cost, storylet.consequence)
            return effects, ""
        except (TypeError, ValueError) as exc:
            return None, f"typed_effects_invalid:{exc}"

    def _partner_entity_known(self, entity_id: str, *, arc: Any) -> bool:
        """True if entity exists on the locked target Arc or partner store."""
        eid = str(entity_id or "").strip()
        if not eid:
            return False
        partners = getattr(arc, "partner_states", None)
        if isinstance(partners, Mapping) and eid in partners:
            return True
        store = self._partner_store
        if store is not None:
            load = getattr(store, "load", None)
            if callable(load):
                try:
                    if load(eid) is not None:
                        return True
                except Exception:
                    return False
        return False

    def commit_storylet(
        self,
        storylet: Storylet,
        *,
        arc_id: str | None = None,
        now_step: int | None = None,
        available_evidence: Sequence[str] | None = None,
        variables: Mapping[str, Any] | None = None,
    ) -> StoryletCommitResult:
        """Atomically commit a Storylet: eligibility, budget, typed effects, EventRecord.

        Eligibility recheck, once/cooldown budget, typed-effect application, and
        EventRecord commit happen inside one ``StoryArcStore.update``. Partner and
        life side-effects run only after a successful (or already-committed) Arc
        write and are idempotent by ``event_id``.
        """
        sid = str(storylet.storylet_id or "").strip()
        if not self.config.enabled or not self.config.storylet_enabled:
            return StoryletCommitResult(
                status="rejected",
                storylet_id=sid,
                reason="worldbook_or_storylet_disabled",
            )

        effects, parse_err = self._parse_storylet_effects(storylet)
        if effects is None:
            return StoryletCommitResult(
                status="rejected",
                storylet_id=sid,
                reason=parse_err or "typed_effects_invalid",
            )

        store = getattr(self._ledger, "_store", None)
        if store is None:
            return StoryletCommitResult(
                status="rejected",
                storylet_id=sid,
                reason="missing_store",
            )

        resolved_arc_id = str(arc_id or "").strip()
        if not resolved_arc_id:
            # Prefer authored target; fall back to live main for programmatic calls.
            resolved_arc_id = str(getattr(storylet, "target_arc_id", "") or "").strip()
        if not resolved_arc_id:
            ledger = self._ledger.load_stack()
            main = getattr(ledger, "main", None)
            if isinstance(main, Mapping):
                resolved_arc_id = str(main.get("arc_id") or "").strip()
        if not resolved_arc_id:
            return StoryletCommitResult(
                status="rejected",
                storylet_id=sid,
                reason="missing_arc_id",
            )

        # Fail closed if target Arc is absent before any mutation.
        load_arc = getattr(store, "load", None)
        if callable(load_arc) and load_arc(resolved_arc_id) is None:
            return StoryletCommitResult(
                status="rejected",
                storylet_id=sid,
                reason="missing_target_arc",
                arc_id=resolved_arc_id,
                effects=effects,
            )

        evidence_list = [str(x).strip() for x in (available_evidence or ()) if str(x).strip()]
        # Capture mutator outcome without leaking partial state on failure.
        outcome: dict[str, Any] = {
            "status": "rejected",
            "reason": "not_applied",
            "event": None,
            "effects": effects,
            "event_id": "",
        }

        def _mutate(arc: Any) -> None:
            budget_raw = getattr(arc, "event_budget", None)
            live_budget = dict(budget_raw) if isinstance(budget_raw, Mapping) else {}
            # Per-Arc logical-step cap is owned by DramaManager via
            # events_tick_step + events_this_tick. Do not zero the count here:
            # chat then schedule at the same now_step must share the cap.
            step = (
                int(now_step)
                if now_step is not None
                else self._resolve_now_step(live_budget)
            )
            live_arc_id = str(getattr(arc, "arc_id", "") or resolved_arc_id)
            event_id = deterministic_storylet_event_id(sid, step, live_arc_id)
            outcome["event_id"] = event_id
            # ``variables`` arg is compatibility-only; never authorizes a commit.
            _ = variables

            if event_id in self._committed_event_ids_of(arc):
                # Replay: mark already_committed without re-running reducer.
                # Side-effect catch-up still runs after the store update returns.
                outcome["status"] = "already_committed"
                outcome["reason"] = "idempotent_replay"
                outcome["event"] = EventRecord(
                    event_id=event_id,
                    event_type=storylet.severity,
                    summary=storylet.text,
                    status="committed",
                    arc_id=live_arc_id,
                    variable_deltas=dict(effects.variable_deltas),
                    consequences=tuple(effects.open_threads),
                    recovery_steps=int(storylet.recovery_steps or 0),
                    severity=storylet.severity,
                    source="storylet",
                    evidence_refs=tuple(storylet.required_evidence),
                    committed_at=utc_now_iso(),
                    step=step,
                )
                return

            # Eligibility always rechecks locked arc.variables truth.
            raw_vars = getattr(arc, "variables", None)
            live_variables = dict(raw_vars) if isinstance(raw_vars, dict) else {}
            selections = self._drama.select(
                [storylet],
                arc_budget=live_budget,
                variables=live_variables,
                available_evidence=evidence_list,
                now_step=step,
                limit=1,
            )
            if not selections:
                outcome["status"] = "rejected"
                outcome["reason"] = "ineligible"
                return

            # Known fiction partners only — before selection budget / EventRecord.
            for partner_item in effects.partner_updates:
                entity_id = str(partner_item.get("entity_id") or "").strip()
                if not self._partner_entity_known(entity_id, arc=arc):
                    outcome["status"] = "rejected"
                    outcome["reason"] = "unknown_partner"
                    return

            selection = selections[0]
            # Selection budget (once/cooldown/delay) — no setback accounting here.
            updated_budget = self._drama.apply_selection_budget(
                live_budget, selection, now_step=step
            )
            # Preserve any existing setback/recovery keys already on the arc.
            if isinstance(budget_raw, dict):
                for key in ("setback_count", "recovery_until_step", "committed_event_ids"):
                    if key not in updated_budget and key in budget_raw:
                        updated_budget[key] = budget_raw[key]
                budget_raw.clear()
                budget_raw.update(updated_budget)
            else:
                try:
                    cast(Any, arc).event_budget = dict(updated_budget)
                except Exception:
                    outcome["status"] = "rejected"
                    outcome["reason"] = "budget_assign_failed"
                    return

            event = EventRecord(
                event_id=event_id,
                event_type=storylet.severity,
                summary=storylet.text,
                status="committed",
                arc_id=live_arc_id,
                variable_deltas=dict(effects.variable_deltas),
                consequences=tuple(effects.open_threads),
                recovery_steps=int(storylet.recovery_steps or 0),
                severity=storylet.severity,
                source="storylet",
                evidence_refs=tuple(storylet.required_evidence),
                committed_at=utc_now_iso(),
                step=step,
            )
            applied = self._reducer.apply(arc, event, now_step=step)

            # Resolve open threads named by effects.
            open_threads = getattr(arc, "open_threads", None)
            if isinstance(open_threads, list) and effects.resolve_threads:
                resolve_set = set(effects.resolve_threads)
                keep = [
                    t
                    for t in open_threads
                    if str(t).strip() not in resolve_set
                ]
                open_threads[:] = keep

            if effects.stage:
                with contextlib.suppress(Exception):
                    cast(Any, arc).stage = str(effects.stage)

            outcome["status"] = "committed"
            outcome["reason"] = "ok"
            outcome["event"] = applied

        update = getattr(store, "update", None)

        try:
            if not callable(update):
                # Formal Storylet commit requires a callable atomic update port.
                return StoryletCommitResult(
                    status="rejected",
                    storylet_id=sid,
                    reason="atomic_update_required",
                    arc_id=resolved_arc_id,
                    effects=effects,
                )
            update(resolved_arc_id, _mutate)
        except Exception as exc:
            _L.warning(
                "storylet commit failed | storylet_id={} arc_id={} err={}",
                sid,
                resolved_arc_id,
                exc,
            )
            return StoryletCommitResult(
                status="rejected",
                storylet_id=sid,
                reason=f"persist_failed:{type(exc).__name__}",
                arc_id=resolved_arc_id,
                effects=effects,
            )

        self._io_performed = True
        status = str(outcome.get("status") or "rejected")
        event = outcome.get("event")
        event_id = str(outcome.get("event_id") or "")
        reason = str(outcome.get("reason") or "")

        if status not in {"committed", "already_committed"}:
            return StoryletCommitResult(
                status="rejected",
                storylet_id=sid,
                event_id=event_id,
                reason=reason or "rejected",
                arc_id=resolved_arc_id,
                effects=effects,
            )

        # Side effects only after Arc commit success (including idempotent replay
        # so partner/life can catch up after crash mid-side-effect).
        if isinstance(event, EventRecord) and status in {
            "committed",
            "already_committed",
        }:
            self._apply_partner_updates(
                effects.partner_updates,
                event_id=event.event_id,
                arc_id=resolved_arc_id,
            )
            self._apply_life_updates(
                effects.life_updates,
                event_id=event.event_id,
            )

        return StoryletCommitResult(
            status=status,  # type: ignore[arg-type]
            storylet_id=sid,
            event_id=event_id or (event.event_id if isinstance(event, EventRecord) else ""),
            reason=reason,
            arc_id=resolved_arc_id,
            event=event if isinstance(event, EventRecord) else None,
            effects=effects,
        )

    def commit_social_experience(
        self,
        record: Any,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
    ) -> SocialStoryCommitResult:
        """Atomically commit a bounded social_influence Event onto the explicit main Arc.

        Gates (fail-closed): worldbook + social_evidence enabled, non-empty allowlist
        membership, factual/active SocialExperience, group/user match, valid opaque
        evidence, and an explicit ``arc_role=main`` Arc. Never seeds arcs, never
        mtime-selects, never consumes user_text/bot_reply for event content.
        """
        exp_id = str(getattr(record, "experience_id", "") or "").strip()
        rec_group = str(getattr(record, "group_id", "") or "").strip()
        rec_user = str(getattr(record, "user_id", "") or "").strip()
        mid = str(getattr(record, "evidence_message_id", "") or "").strip()
        call_group = str(group_id if group_id is not None else rec_group).strip()
        call_user = str(user_id if user_id is not None else rec_user).strip()
        evidence_ref = ""
        if call_group and mid:
            evidence_ref = f"social:{call_group}:{mid}"

        def _reject(reason: str, *, arc_id: str = "", event_id: str = "") -> SocialStoryCommitResult:
            return SocialStoryCommitResult(
                status="rejected",
                reason=reason,
                event_id=event_id,
                arc_id=arc_id,
                evidence_ref=evidence_ref,
                experience_id=exp_id,
                observation={
                    "kind": "social_story_commit",
                    "status": "rejected",
                    "reason": reason,
                    "experience_id": exp_id,
                    "arc_id": arc_id,
                    "event_id": event_id,
                    "evidence_ref": evidence_ref,
                },
            )

        if not self.config.enabled or not self.config.social_evidence_enabled:
            return _reject("worldbook_or_social_disabled")

        allowlist = {
            str(g).strip() for g in self.config.social_group_allowlist if str(g).strip()
        }
        if not allowlist:
            return _reject("empty_social_group_allowlist")
        if not call_group or call_group not in allowlist:
            return _reject("group_not_allowed")

        if record is None:
            return _reject("missing_record")
        if not exp_id:
            return _reject("missing_experience_id")
        if not rec_group or not rec_user:
            return _reject("missing_record_scope")
        if call_group != rec_group:
            return _reject("group_mismatch")
        if call_user != rec_user:
            return _reject("user_mismatch")

        entity_kind = str(getattr(record, "entity_kind", "") or "").strip().lower()
        if entity_kind != "factual":
            return _reject("entity_kind_not_factual")
        status = str(getattr(record, "status", "") or "").strip().lower()
        if status != "active":
            return _reject("status_not_active")

        # Factual group-store contract: only group/public may enter the story
        # bridge. If an anomalous/fake record carries privacy=private|system
        # (or any other non-group/public value), fail closed with zero mutation.
        raw_privacy = None
        if isinstance(record, Mapping):
            raw_privacy = record.get("privacy")
        elif hasattr(record, "privacy"):
            raw_privacy = record.privacy
        if raw_privacy is not None and str(raw_privacy).strip() != "":
            privacy = str(raw_privacy).strip().lower()
            if privacy not in {"group", "public"}:
                return _reject("privacy_forbidden")

        if not mid:
            return _reject("missing_evidence_message_id")
        evidence_time = str(getattr(record, "evidence_time", "") or "").strip()
        if not evidence_time:
            return _reject("missing_evidence_time")
        try:
            datetime.fromisoformat(evidence_time.replace("Z", "+00:00"))
        except ValueError:
            return _reject("invalid_evidence_time")
        evidence_source = str(getattr(record, "evidence_source", "") or "").strip()
        if not evidence_source:
            return _reject("missing_evidence_source")

        # Explicit main only — never ensure_seeded, never mtime, never legacy promote.
        target_arc_id = self._explicit_main_arc_id()
        if not target_arc_id:
            return _reject("missing_main_arc")

        store = getattr(self._ledger, "_store", None)
        if store is None:
            return _reject("missing_store", arc_id=target_arc_id)
        load_arc = getattr(store, "load", None)
        update = getattr(store, "update", None)
        if not callable(load_arc) or not callable(update):
            return _reject("atomic_update_required", arc_id=target_arc_id)
        if load_arc(target_arc_id) is None:
            return _reject("missing_target_arc", arc_id=target_arc_id)

        try:
            event_id = deterministic_social_story_event_id(
                experience_id=exp_id,
                group_id=call_group,
                user_id=call_user,
                evidence_message_id=mid,
                target_arc_id=target_arc_id,
            )
        except ValueError as exc:
            return _reject(f"invalid_event_id:{exc}", arc_id=target_arc_id)

        outcome: dict[str, Any] = {
            "status": "rejected",
            "reason": "not_applied",
            "event": None,
            "event_id": event_id,
        }

        def _mutate(arc: Any) -> None:
            live_arc_id = str(getattr(arc, "arc_id", "") or target_arc_id)
            role = str(getattr(arc, "arc_role", "") or "").strip().lower()
            if role != "main":
                outcome["status"] = "rejected"
                outcome["reason"] = "target_not_main"
                return

            if event_id in self._committed_event_ids_of(arc):
                outcome["status"] = "already_committed"
                outcome["reason"] = "idempotent_replay"
                outcome["event"] = EventRecord(
                    event_id=event_id,
                    event_type="social_influence",
                    summary=SOCIAL_INFLUENCE_SUMMARY,
                    status="committed",
                    arc_id=live_arc_id,
                    variable_deltas={"social_resonance": 0.0},
                    consequences=(),
                    recovery_steps=0,
                    severity="daily",
                    source="social_evidence",
                    evidence_refs=(evidence_ref,),
                    committed_at=utc_now_iso(),
                    step=self._resolve_now_step(
                        getattr(arc, "event_budget", None)
                        if isinstance(getattr(arc, "event_budget", None), Mapping)
                        else {}
                    ),
                )
                return

            budget_raw = getattr(arc, "event_budget", None)
            step = self._resolve_now_step(
                budget_raw if isinstance(budget_raw, Mapping) else {}
            )

            # Clamp social_resonance into [0, 1] after applying the fixed delta.
            variables = getattr(arc, "variables", None)
            if not isinstance(variables, dict):
                try:
                    cast(Any, arc).variables = {}
                    variables = cast(Any, arc).variables
                except Exception:
                    outcome["status"] = "rejected"
                    outcome["reason"] = "variables_unavailable"
                    return
            try:
                current = float(variables.get("social_resonance", 0.0) or 0.0)
            except (TypeError, ValueError):
                current = 0.0
            target = max(
                SOCIAL_RESONANCE_MIN,
                min(SOCIAL_RESONANCE_MAX, current + SOCIAL_RESONANCE_DELTA),
            )
            applied_delta = round(target - current, 6)

            event = EventRecord(
                event_id=event_id,
                event_type="social_influence",
                summary=SOCIAL_INFLUENCE_SUMMARY,
                status="committed",
                arc_id=live_arc_id,
                variable_deltas={"social_resonance": applied_delta},
                consequences=(),
                recovery_steps=0,
                severity="daily",
                source="social_evidence",
                evidence_refs=(evidence_ref,),
                committed_at=utc_now_iso(),
                step=step,
            )
            applied = self._reducer.apply(arc, event, now_step=step)
            # Hard clamp after reducer in case of concurrent races / non-numeric.
            try:
                final_val = float(variables.get("social_resonance", target) or 0.0)
            except (TypeError, ValueError):
                final_val = target
            variables["social_resonance"] = max(
                SOCIAL_RESONANCE_MIN,
                min(SOCIAL_RESONANCE_MAX, final_val),
            )
            outcome["status"] = "committed"
            outcome["reason"] = "ok"
            outcome["event"] = applied

        try:
            update(target_arc_id, _mutate)
        except Exception as exc:
            _L.warning(
                "social story commit failed | experience_id={} arc_id={} err={}",
                exp_id,
                target_arc_id,
                exc,
            )
            return _reject(
                f"persist_failed:{type(exc).__name__}",
                arc_id=target_arc_id,
                event_id=event_id,
            )

        self._io_performed = True
        status_out = str(outcome.get("status") or "rejected")
        event_out = outcome.get("event")
        reason_out = str(outcome.get("reason") or "")

        if status_out not in {"committed", "already_committed"}:
            return _reject(
                reason_out or "rejected",
                arc_id=target_arc_id,
                event_id=event_id,
            )

        # Life catch-up after Arc success (including idempotent replay).
        if isinstance(event_out, EventRecord):
            self._apply_life_updates(
                (
                    {
                        "key": SOCIAL_LIFE_KEY,
                        "value": SOCIAL_LIFE_VALUE,
                        "ttl_hours": SOCIAL_LIFE_TTL_HOURS,
                    },
                ),
                event_id=event_out.event_id,
            )

        observation = {
            "kind": "social_story_commit",
            "status": status_out,
            "reason": reason_out,
            "experience_id": exp_id,
            "arc_id": target_arc_id,
            "event_id": event_id,
            "evidence_ref": evidence_ref,
            "event_type": "social_influence",
            "source": "social_evidence",
        }
        return SocialStoryCommitResult(
            status=status_out,  # type: ignore[arg-type]
            reason=reason_out,
            event_id=event_id,
            arc_id=target_arc_id,
            evidence_ref=evidence_ref,
            experience_id=exp_id,
            event=event_out if isinstance(event_out, EventRecord) else None,
            observation=observation,
        )

    def _explicit_main_arc_id(self) -> str:
        """Return the sole explicit arc_role=main id, or empty (never seed/mtime)."""
        store = getattr(self._ledger, "_store", None)
        if store is None:
            return ""
        list_ids = getattr(store, "list_arc_ids", None)
        load = getattr(store, "load", None)
        if not callable(list_ids) or not callable(load):
            return ""
        mains: list[tuple[int, str]] = []
        try:
            raw_ids = list_ids()
        except Exception:
            return ""
        if not isinstance(raw_ids, (list, tuple)):
            return ""
        for arc_id in raw_ids:
            aid = str(arc_id or "").strip()
            if not aid:
                continue
            try:
                arc = load(aid)
            except Exception:
                continue
            if arc is None:
                continue
            if isinstance(arc, Mapping):
                role = str(arc.get("arc_role") or arc.get("role") or "").strip().lower()
                order = int(arc.get("stack_order") or 0)
            else:
                role = str(getattr(arc, "arc_role", "") or "").strip().lower()
                order = int(getattr(arc, "stack_order", 0) or 0)
            if role == "main":
                mains.append((order, aid))
        if not mains:
            return ""
        mains.sort(key=lambda item: (item[0], item[1]))
        return mains[0][1]

    def _apply_life_updates(
        self,
        updates: Sequence[Mapping[str, Any]],
        *,
        event_id: str,
    ) -> None:
        """Apply finite-TTL life items; exact idempotency via LifeState ledger.

        Crash catch-up: when ``event_id`` is absent from ``applied_event_ids``,
        apply all item writes for this event and mark the ledger in one
        ``LifeStateStore.update``. When already marked, never overwrite.
        """
        if not updates:
            return
        eid = str(event_id or "").strip()
        if not eid:
            return
        now = datetime.now(_CST)

        prepared: list[tuple[str, str, str, str, str, str, str]] = []
        for item in updates:
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            value = str(item.get("value") if "value" in item else "")
            ttl_hours = item.get("ttl_hours")
            ttl_seconds = item.get("ttl_seconds")
            if ttl_hours is not None:
                try:
                    decay = now + timedelta(hours=float(ttl_hours))
                except (TypeError, ValueError):
                    continue
            elif ttl_seconds is not None:
                try:
                    decay = now + timedelta(seconds=float(ttl_seconds))
                except (TypeError, ValueError):
                    continue
            else:
                continue
            # Authored storylets cannot override provenance; runtime writes fixed
            # story_ledger / self / medium / private for every life item.
            prepared.append(
                (
                    key,
                    value,
                    "story_ledger",
                    "self",
                    "medium",
                    "private",
                    decay.isoformat(),
                )
            )
        if not prepared:
            return

        from services.worldbook.domain import LifeStateItem, SourceMeta, validate_life_state_meta

        def _mutate(state: Any) -> None:
            applied = getattr(state, "applied_event_ids", None)
            if not isinstance(applied, list):
                try:
                    state.applied_event_ids = []
                    applied = state.applied_event_ids
                except Exception:
                    return
            if eid in {str(x).strip() for x in applied if str(x).strip()}:
                # Already marked: never overwrite newer item values.
                return
            for (
                key,
                value,
                life_source,
                scope,
                confidence,
                privacy,
                decay_at,
            ) in prepared:
                meta = SourceMeta(
                    source=life_source,  # type: ignore[arg-type]
                    scope=scope,
                    confidence=confidence,  # type: ignore[arg-type]
                    privacy=privacy,  # type: ignore[arg-type]
                    updated_at=now.isoformat(),
                    decay_at=decay_at,
                    revision=int(getattr(state, "revision", 0) or 0) + 1,
                    evidence_refs=(eid,),
                )
                validate_life_state_meta(meta, require_ttl=True)
                state.items[key] = LifeStateItem(key=key, value=value, meta=meta)
            applied.append(eid)

        try:
            self._life.update(_mutate)
            self._io_performed = True
        except Exception as exc:
            _L.warning(
                "life_update failed | event_id={} err={}",
                eid,
                exc,
            )

    def _apply_partner_updates(
        self,
        updates: Sequence[Mapping[str, Any]],
        *,
        event_id: str,
        arc_id: str,
    ) -> None:
        """Apply partner_updates after Arc commit; idempotent by event_id."""
        if not updates:
            return
        eid = str(event_id or "").strip()
        store = self._partner_store
        # Always also mirror into main arc.partner_states for restart continuity
        # when FictionPartnerStateStore is absent (shadow / unit tests).
        arc_store = getattr(self._ledger, "_store", None)

        for item in updates:
            entity_id = str(item.get("entity_id") or "").strip()
            if not entity_id:
                continue
            patch = {
                k: v
                for k, v in dict(item).items()
                if k not in {"entity_id"}
            }
            if store is not None:
                try:
                    self._apply_partner_store_update(store, entity_id, patch, event_id=eid)
                except Exception as exc:
                    _L.warning(
                        "partner store update failed | entity_id={} err={}",
                        entity_id,
                        exc,
                    )
            if arc_store is not None and arc_id:
                try:
                    self._apply_partner_arc_update(
                        arc_store, arc_id, entity_id, patch, event_id=eid
                    )
                except Exception as exc:
                    _L.warning(
                        "partner arc update failed | entity_id={} arc_id={} err={}",
                        entity_id,
                        arc_id,
                        exc,
                    )

    def _apply_partner_store_update(
        self,
        store: Any,
        entity_id: str,
        patch: Mapping[str, Any],
        *,
        event_id: str,
    ) -> None:
        load = getattr(store, "load", None)
        save = getattr(store, "save", None)
        if not callable(load) or not callable(save):
            return
        state = load(entity_id)
        if state is None:
            # Never invent a partner card from an arbitrary storylet.
            return
        # Exact non-expiring ledger; recent_events is bounded human history only.
        applied = list(getattr(state, "applied_event_ids", None) or [])
        applied_list = [str(x).strip() for x in applied if str(x).strip()]
        if event_id and event_id in applied_list:
            return
        # Only mutable runtime fields — never display_name / pinned_profile / kind.
        for field_name in ("mood", "availability", "current_state"):
            if field_name in patch and patch[field_name] is not None:
                with contextlib.suppress(Exception):
                    setattr(state, field_name, str(patch[field_name]))
        if "constraints" in patch and isinstance(patch["constraints"], (list, tuple)):
            with contextlib.suppress(Exception):
                cast(Any, state).constraints = [str(x) for x in patch["constraints"]]
        note = str(patch.get("note") or patch.get("event_note") or "").strip()
        marker = f"event:{event_id}"
        recent = list(getattr(state, "recent_events", None) or [])
        if event_id:
            entry = marker if not note else f"{marker}:{note}"
            recent = [entry, *[x for x in recent if marker not in str(x)]]
            cast(Any, state).recent_events = recent[:16]
            applied_list.append(event_id)
            cast(Any, state).applied_event_ids = applied_list
        save(state)
        self._io_performed = True

    def _apply_partner_arc_update(
        self,
        arc_store: Any,
        arc_id: str,
        entity_id: str,
        patch: Mapping[str, Any],
        *,
        event_id: str,
    ) -> None:
        def _mutate(arc: Any) -> None:
            partners = getattr(arc, "partner_states", None)
            if not isinstance(partners, dict):
                try:
                    cast(Any, arc).partner_states = {}
                    partners = arc.partner_states
                except Exception:
                    return
            if entity_id not in partners:
                # Fail closed: do not invent partner cards on the arc.
                return
            current = dict(partners.get(entity_id) or {})
            applied = current.get("applied_event_ids")
            applied_list = (
                [str(x).strip() for x in applied if str(x).strip()]
                if isinstance(applied, list)
                else []
            )
            # Exact non-expiring ledger: never cap/evict for idempotency.
            if event_id and event_id in applied_list:
                return
            allowed = {
                "mood",
                "availability",
                "current_state",
                "constraints",
                "note",
                "event_note",
            }
            for key, value in patch.items():
                key_s = str(key)
                if key_s not in allowed:
                    continue
                current[key_s] = value
            if event_id:
                applied_list.append(event_id)
                current["applied_event_ids"] = applied_list
                current["last_event_id"] = event_id
            partners[entity_id] = current

        update = getattr(arc_store, "update", None)
        if callable(update):
            update(arc_id, _mutate)
            self._io_performed = True
            return
        load = getattr(arc_store, "load", None)
        save = getattr(arc_store, "save", None)
        if callable(load) and callable(save):
            arc = load(arc_id)
            if arc is None:
                return
            _mutate(arc)
            save(arc)
            self._io_performed = True

    def _arc_payloads_by_id(self, ledger: Any) -> dict[str, Mapping[str, Any]]:
        """Index ledger arc payloads (main + sides + ambient) by arc_id."""
        out: dict[str, Mapping[str, Any]] = {}
        main = getattr(ledger, "main", None)
        if isinstance(main, Mapping):
            aid = str(main.get("arc_id") or "").strip()
            if aid:
                out[aid] = main
        for role_name in ("sides", "ambient"):
            raw = getattr(ledger, role_name, None)
            if not isinstance(raw, (list, tuple)):
                continue
            for arc in raw:
                if not isinstance(arc, Mapping):
                    continue
                aid = str(arc.get("arc_id") or "").strip()
                if aid:
                    out[aid] = arc
        # Also include any id reported by all_arc_ids that store can load.
        store = getattr(self._ledger, "_store", None)
        load = getattr(store, "load", None) if store is not None else None
        for arc_id in getattr(ledger, "all_arc_ids", lambda: ())():
            aid = str(arc_id or "").strip()
            if not aid or aid in out:
                continue
            if not callable(load):
                continue
            try:
                loaded = load(aid)
            except Exception:
                continue
            if loaded is None:
                continue
            to_dict = getattr(loaded, "to_dict", None)
            if callable(to_dict):
                payload = to_dict()
                if isinstance(payload, Mapping):
                    out[aid] = payload
        return out

    def _global_schedule_step(self, arc_payloads: Mapping[str, Mapping[str, Any]]) -> int:
        """Schedule-authoritative global step = max persisted clock across ledger."""
        best = 0
        found = False
        for payload in arc_payloads.values():
            raw_budget = payload.get("event_budget")
            step = self._resolve_now_step(
                raw_budget if isinstance(raw_budget, Mapping) else {}
            )
            if not found or step > best:
                best = step
                found = True
        return best if found else 0

    def _select_and_commit_storylets(
        self,
        *,
        ledger: Any,
        social: Sequence[SocialEvidenceRef] | None = None,
        limit: int = 1,
    ) -> list[Storylet]:
        """Select against each Storylet's authored target Arc, then commit winner.

        Routing:
        1. Resolve each Storylet's ``target_arc_id`` (empty → live main for
           programmatic compatibility). Missing target Arc → skip (fail closed).
        2. Evaluate eligibility against that Arc's budget/variables with a
           global Schedule-authoritative step (max clock across the ledger).
        3. Globally rank eligible candidates; commit the winner to its target Arc.
        """
        if not self.config.enabled or not self.config.storylet_enabled:
            return []
        self.ensure_loaded()
        arc_payloads = self._arc_payloads_by_id(ledger)
        main = getattr(ledger, "main", None)
        main_arc_id = ""
        if isinstance(main, Mapping):
            main_arc_id = str(main.get("arc_id") or "").strip()
        if not main_arc_id and arc_payloads:
            # Prefer stack main role when main mapping is empty.
            for aid, payload in arc_payloads.items():
                if str(payload.get("arc_role") or "") == "main":
                    main_arc_id = aid
                    break
            if not main_arc_id:
                main_arc_id = next(iter(arc_payloads))

        global_step = self._global_schedule_step(arc_payloads)
        available_evidence = self._collect_available_evidence(ledger, social)
        tick_limit = max(1, int(limit))

        ranked: list[DramaSelection] = []
        for storylet in self._storylets.list_storylets():
            target_id = str(getattr(storylet, "target_arc_id", "") or "").strip()
            if not target_id:
                target_id = main_arc_id
            if not target_id:
                continue
            payload = arc_payloads.get(target_id)
            if payload is None:
                # Authored target missing → fail closed, no projection candidate.
                continue
            raw_budget = payload.get("event_budget")
            arc_budget = dict(raw_budget) if isinstance(raw_budget, Mapping) else {}
            # Keep persisted events_this_tick / events_tick_step so DramaManager
            # can share the per-Arc cap across chat + schedule at the same step.
            variables = (
                dict(payload.get("variables") or {})
                if isinstance(payload.get("variables"), Mapping)
                else {}
            )
            # Evaluate one candidate at a time so each uses its own target budget.
            selections = self._drama.select(
                [storylet],
                arc_budget=arc_budget,
                variables=variables,
                available_evidence=available_evidence,
                now_step=global_step,
                limit=1,
            )
            ranked.extend(selections)

        if not ranked:
            return []

        ranked.sort(
            key=lambda s: (
                -s.rank_score,
                -s.storylet.priority,
                s.storylet.storylet_id,
            )
        )
        winners = ranked[:tick_limit]

        committed: list[Storylet] = []
        for selection in winners:
            storylet = selection.storylet
            target_id = str(getattr(storylet, "target_arc_id", "") or "").strip()
            if not target_id:
                target_id = main_arc_id
            if not target_id:
                continue
            payload = arc_payloads.get(target_id) or {}
            variables = (
                dict(payload.get("variables") or {})
                if isinstance(payload.get("variables"), Mapping)
                else {}
            )
            result = self.commit_storylet(
                storylet,
                arc_id=target_id,
                now_step=global_step,
                available_evidence=available_evidence,
                variables=variables,
            )
            if result.status == "committed":
                committed.append(storylet)
            elif result.status == "already_committed":
                # Idempotent replay: do not re-project as a fresh prompt block.
                continue
            else:
                _L.debug(
                    "storylet commit rejected | id={} reason={} target={}",
                    storylet.storylet_id,
                    result.reason,
                    target_id,
                )
        return committed

    async def load_social_evidence(
        self,
        *,
        group_id: str | None,
        user_id: str | None,
        limit: int = 8,
    ) -> list[SocialEvidenceRef]:
        if (
            not self.config.enabled
            or not self.config.social_evidence_enabled
            or not group_id
            or not user_id
        ):
            return []
        store = self._social_store
        if store is None:
            return []
        recall = getattr(store, "recall", None)
        if not callable(recall):
            return []
        self._io_performed = True
        recall_social = cast(Callable[..., Awaitable[Any]], recall)
        experiences = await recall_social(
            group_id=str(group_id),
            user_id=str(user_id),
            limit=limit,
        )
        out: list[SocialEvidenceRef] = []
        for exp in experiences or []:
            try:
                exp_group_id = str(
                    getattr(exp, "group_id", "")
                    or (exp.get("group_id") if isinstance(exp, dict) else "")
                ).strip()
                exp_user_id = str(
                    getattr(exp, "user_id", "")
                    or (exp.get("user_id") if isinstance(exp, dict) else "")
                ).strip()
                # Fail closed: returned metadata must match request scope.
                if exp_group_id != str(group_id) or exp_user_id != str(user_id):
                    continue
                evidence_id = str(
                    getattr(exp, "evidence_message_id", "")
                    or (exp.get("evidence_message_id") if isinstance(exp, dict) else "")
                )
                if not evidence_id.strip():
                    continue  # fail-closed
                summary = str(
                    getattr(exp, "user_text", "")
                    or getattr(exp, "summary", "")
                    or (exp.get("user_text") if isinstance(exp, dict) else "")
                    or (exp.get("summary") if isinstance(exp, dict) else "")
                    or ""
                ).strip()
                if not summary:
                    continue
                # Preserve source privacy/confidence; never invent higher trust.
                raw_privacy = (
                    getattr(exp, "privacy", None)
                    if not isinstance(exp, dict)
                    else exp.get("privacy")
                )
                raw_confidence = (
                    getattr(exp, "confidence", None)
                    if not isinstance(exp, dict)
                    else exp.get("confidence")
                )
                privacy = str(raw_privacy or "group").strip() or "group"
                confidence = str(raw_confidence or "unknown").strip() or "unknown"
                out.append(
                    SocialEvidenceRef(
                        experience_id=str(
                            getattr(exp, "experience_id", "")
                            or (exp.get("experience_id") if isinstance(exp, dict) else "")
                        ),
                        group_id=exp_group_id,
                        user_id=exp_user_id,
                        evidence_message_id=evidence_id,
                        evidence_time=str(
                            getattr(exp, "evidence_time", "")
                            or (exp.get("evidence_time") if isinstance(exp, dict) else "")
                        ),
                        summary=summary[:240],
                        privacy=privacy,  # type: ignore[arg-type]
                        confidence=confidence,  # type: ignore[arg-type]
                    )
                )
            except (TypeError, ValueError):
                continue
        return out

    async def project_chat(
        self,
        *,
        conversation_text: str,
        group_id: str | None,
        user_id: str | None,
        session_id: str = "",
    ) -> ProjectionResult:
        _ = session_id
        if not self.config.enabled or not self.config.chat_projection_enabled:
            return ProjectionResult(blocks=(), traces=(), mode="chat")
        self.ensure_loaded()
        hits = self._trigger.activate(conversation_text)
        life = self._life.load()
        self._io_performed = True
        ledger = self._ledger.load_stack()
        social: list[SocialEvidenceRef] = []
        if self.config.social_evidence_enabled:
            social = await self.load_social_evidence(
                group_id=group_id, user_id=user_id
            )
        storylets: list[Storylet] = []
        if self.config.storylet_enabled:
            storylets = self._select_and_commit_storylets(
                ledger=ledger,
                social=social,
                limit=1,
            )
        # Reload life after commit so prompt reflects just-written TTL items.
        if storylets:
            life = self._life.load()
        return self._projection.project(
            mode="chat",
            canon_hits=hits,
            life_state=life,
            ledger=ledger,
            social=social,
            storylets=storylets,
            group_id=group_id,
            user_id=user_id,
        )

    def project_schedule(
        self,
        *,
        conversation_text: str = "",
        # Social is intentionally ignored for schedule projection.
        social: Sequence[SocialEvidenceRef] | None = None,
    ) -> ProjectionResult:
        if not self.config.enabled or not self.config.schedule_projection_enabled:
            return ProjectionResult(blocks=(), traces=(), mode="schedule")
        self.ensure_loaded()
        hits = self._trigger.activate(conversation_text)
        life = self._life.load()
        self._io_performed = True
        ledger = self._ledger.load_stack()
        # Schedule mode: no social evidence projection; still may select fiction
        # storylets from ledger-only evidence.
        storylets: list[Storylet] = []
        if self.config.storylet_enabled:
            storylets = self._select_and_commit_storylets(
                ledger=ledger,
                social=None,
                limit=1,
            )
        if storylets:
            life = self._life.load()
        return self._projection.project(
            mode="schedule",
            canon_hits=hits,
            life_state=life,
            ledger=ledger,
            social=list(social or ()),  # will be rejected in projection
            storylets=storylets,
            group_id=None,
            user_id=None,
        )


def build_worldbook_runtime(
    config: WorldbookConfig | None = None,
    *,
    root: str | Path | None = None,
    story_arc_store: Any | None = None,
    social_narrative_store: Any | None = None,
    partner_state_store: Any | None = None,
    persona_identity_text: str = "",
    persona_id: str = "",
    semantic_scorer: Any | None = None,
) -> WorldbookRuntime | None:
    """Build runtime only when enabled; otherwise return None (no I/O)."""
    cfg = config or WorldbookConfig()
    if not cfg.enabled:
        return None
    return WorldbookRuntime(
        cfg,
        root=root,
        story_arc_store=story_arc_store,
        social_narrative_store=social_narrative_store,
        partner_state_store=partner_state_store,
        persona_identity_text=persona_identity_text,
        persona_id=persona_id,
        semantic_scorer=semantic_scorer,
    )
