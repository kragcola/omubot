"""Fail-closed bridge from new schedules to governed Worldbook events.

This module owns the cross-store recovery protocol.  It deliberately never
discovers or upgrades unmarked legacy schedule files.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, cast

from loguru import logger

from plugins.schedule.store import ScheduleGovernanceSnapshot, ScheduleStore
from plugins.schedule.types import Schedule
from services.worldbook.governance_contracts import (
    LEGACY_SINGLETON_WORLD_ID,
    WorldbookCommitReceiptV1,
    WorldbookEventProposalV1,
    WorldbookEventSource,
)
from services.worldbook.governance_store import WorldbookGovernanceStore
from services.worldbook.governed_adapters import (
    build_schedule_event_proposal,
    verify_committed_world_event,
)

_L = logger.bind(channel="schedule-worldbook-governance")
_INTENT_VERSION = "schedule.worldbook_governance_intent.v1"
_INTENT_FIELDS = frozenset(
    {
        "contract_version",
        "world_id",
        "arc_id",
        "schedule_date",
        "summary_sha256",
        "source_sha256",
        "proposed_at",
        "proposal_id",
        "proposal_sha256",
    }
)
_ARC_MARKER_KEY = "worldbook_schedule_governance_v1"


@dataclass(frozen=True, slots=True)
class ScheduleGovernanceOutcome:
    """Bounded status returned to the generator or operator action layer."""

    status: str
    proposal_id: str = ""
    receipt: WorldbookCommitReceiptV1 | None = None
    reason: str = ""

    @property
    def receipt_present(self) -> bool:
        return self.receipt is not None


class ScheduleWorldbookGovernanceBridge:
    """Recoverable source -> proposal -> decision -> receipt bridge.

    The bridge is bound only after Runtime v2 opens the supplied governance
    store.  It serializes its own work and can be quiesced before that store is
    closed during application shutdown.
    """

    def __init__(
        self,
        *,
        schedule_store: ScheduleStore,
        worldbook_runtime: Any,
        governance_store: WorldbookGovernanceStore,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if schedule_store is None:
            raise ValueError("schedule_store is required")
        if worldbook_runtime is None:
            raise ValueError("worldbook_runtime is required")
        if governance_store is None:
            raise ValueError("governance_store is required")
        self._schedule_store = schedule_store
        self._runtime = worldbook_runtime
        self._governance_store = governance_store
        self._now = now or (lambda: datetime.now(UTC))
        self._lifecycle_lock = asyncio.Lock()
        self._closed = False

    async def close(self) -> None:
        """Stop admission and wait for every in-flight bridge operation."""
        async with self._lifecycle_lock:
            self._closed = True

    async def prepare_fresh(self, schedule: Schedule) -> ScheduleGovernanceOutcome:
        """Persist a fresh source marker and append exactly one v2 proposal.

        A pre-existing marked source is retried exactly.  An unmarked source is
        legacy or otherwise outside this cutover and is never overwritten.
        """
        async with self._lifecycle_lock:
            if self._closed:
                return ScheduleGovernanceOutcome(status="unavailable", reason="bridge_closed")
            return await self._prepare_fresh_locked(schedule)

    async def resume_pending_source(self, schedule_date: str) -> ScheduleGovernanceOutcome:
        """Resume only a source carrying this bridge's immutable intent marker."""
        async with self._lifecycle_lock:
            if self._closed:
                return ScheduleGovernanceOutcome(status="unavailable", reason="bridge_closed")
            snapshot = self._schedule_store.load_governance_snapshot(schedule_date)
            if snapshot is None:
                return ScheduleGovernanceOutcome(status="absent", reason="source_missing_or_invalid")
            try:
                proposal = self._proposal_from_snapshot(snapshot)
            except (TypeError, ValueError) as exc:
                return self._blocked("invalid_intent", exc)
            try:
                await self._governance_store.append_proposal(proposal)
                decision = await self._governance_store.get_operator_decision(
                    proposal.proposal_id
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return self._blocked("proposal_store_unavailable", exc, proposal)
            if decision is not None and decision.decision == "approve":
                return await self._commit_approved_locked(proposal.proposal_id)
            if decision is not None:
                return ScheduleGovernanceOutcome(
                    status="rejected",
                    proposal_id=proposal.proposal_id,
                )
            return ScheduleGovernanceOutcome(status="proposed", proposal_id=proposal.proposal_id)

    async def commit_approved(self, proposal_id: str) -> ScheduleGovernanceOutcome:
        """Commit an already-approved proposal, or finish its receipt suffix."""
        async with self._lifecycle_lock:
            if self._closed:
                return ScheduleGovernanceOutcome(status="unavailable", reason="bridge_closed")
            return await self._commit_approved_locked(proposal_id)

    async def _prepare_fresh_locked(
        self,
        schedule: Schedule,
    ) -> ScheduleGovernanceOutcome:
        if not isinstance(schedule, Schedule):
            return ScheduleGovernanceOutcome(status="blocked", reason="invalid_schedule")
        snapshot = self._schedule_store.load_governance_snapshot(schedule.date)
        if snapshot is not None:
            if snapshot.source_sha256 != ScheduleStore.source_sha256(schedule):
                return ScheduleGovernanceOutcome(
                    status="blocked",
                    reason="source_already_exists",
                )
            try:
                proposal = self._proposal_from_snapshot(snapshot)
            except (TypeError, ValueError) as exc:
                return self._blocked("invalid_existing_intent", exc)
            try:
                await self._governance_store.append_proposal(proposal)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return self._blocked("proposal_store_unavailable", exc, proposal)
            return ScheduleGovernanceOutcome(status="proposed", proposal_id=proposal.proposal_id)

        if self._schedule_store.source_exists(schedule.date):
            return ScheduleGovernanceOutcome(
                status="blocked",
                reason="unmarked_existing_source",
            )
        try:
            proposal = self._build_fresh_proposal(schedule)
            intent = self._intent_for(schedule, proposal)
            self._schedule_store.save(schedule, governance_intent=intent)
        except (TypeError, ValueError) as exc:
            return self._blocked("proposal_build_failed", exc)
        try:
            await self._governance_store.append_proposal(proposal)
        except asyncio.CancelledError:
            # The saved intent is the exact recovery record for this prefix.
            raise
        except Exception as exc:
            return self._blocked("proposal_append_failed", exc, proposal)
        return ScheduleGovernanceOutcome(status="proposed", proposal_id=proposal.proposal_id)

    async def _commit_approved_locked(
        self,
        proposal_id: str,
    ) -> ScheduleGovernanceOutcome:
        clean_id = str(proposal_id or "").strip()
        if not clean_id:
            return ScheduleGovernanceOutcome(status="blocked", reason="invalid_proposal_id")
        try:
            receipt = await self._governance_store.get_commit_receipt(clean_id)
            if receipt is not None:
                return ScheduleGovernanceOutcome(
                    status="committed",
                    proposal_id=clean_id,
                    receipt=receipt,
                )
            proposal = await self._governance_store.get_proposal(clean_id)
            decision = await self._governance_store.get_operator_decision(clean_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._blocked("proposal_store_unavailable", exc)
        if proposal is None:
            return ScheduleGovernanceOutcome(status="blocked", reason="proposal_missing")
        if not self._is_schedule_proposal(proposal):
            return ScheduleGovernanceOutcome(
                status="blocked",
                proposal_id=proposal.proposal_id,
                reason="proposal_not_schedule",
            )
        if decision is None or decision.decision != "approve":
            return ScheduleGovernanceOutcome(
                status="blocked",
                proposal_id=proposal.proposal_id,
                reason="approval_missing",
            )
        schedule_date = self._schedule_date_of(proposal)
        snapshot = self._schedule_store.load_governance_snapshot(schedule_date)
        if snapshot is None:
            return ScheduleGovernanceOutcome(
                status="blocked",
                proposal_id=proposal.proposal_id,
                reason="marked_source_missing_or_invalid",
            )
        try:
            marked_proposal = self._proposal_from_snapshot(snapshot)
        except (TypeError, ValueError) as exc:
            return self._blocked("invalid_intent", exc, proposal)
        if marked_proposal != proposal:
            return ScheduleGovernanceOutcome(
                status="blocked",
                proposal_id=proposal.proposal_id,
                reason="proposal_marker_mismatch",
            )
        try:
            target_arc_id = self._explicit_main_arc_id(schedule_date)
        except (TypeError, ValueError) as exc:
            return self._blocked("main_arc_unavailable", exc, proposal)
        if target_arc_id != proposal.world_ref.arc_id:
            return ScheduleGovernanceOutcome(
                status="blocked",
                proposal_id=proposal.proposal_id,
                reason="main_arc_changed",
            )

        store = self._story_arc_store()
        update = getattr(store, "update", None)
        load = getattr(store, "load", None)
        if not callable(update) or not callable(load):
            return ScheduleGovernanceOutcome(
                status="blocked",
                proposal_id=proposal.proposal_id,
                reason="atomic_story_store_required",
            )
        update_arc = cast(Callable[[str, Callable[[Any], None]], Any], update)
        load_arc = cast(Callable[[str], Any], load)

        mutation: dict[str, Any] = {"status": "blocked", "reason": "not_applied"}
        try:
            update_arc(
                target_arc_id,
                lambda arc: self._apply_commit_mutation(
                    arc,
                    proposal=proposal,
                    snapshot=snapshot,
                    mutation=mutation,
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._blocked("story_arc_update_failed", exc, proposal)
        if mutation["status"] == "blocked":
            return ScheduleGovernanceOutcome(
                status="blocked",
                proposal_id=proposal.proposal_id,
                reason=str(mutation["reason"]),
            )

        try:
            persisted_arc = load_arc(target_arc_id)
            if persisted_arc is None:
                raise ValueError("persisted_arc_missing")
            to_dict = getattr(persisted_arc, "to_dict", None)
            if not callable(to_dict):
                raise ValueError("persisted_arc_is_not_serializable")
            raw_arc_snapshot = to_dict()
            if not isinstance(raw_arc_snapshot, Mapping):
                raise ValueError("persisted_arc_snapshot_is_invalid")
            arc_snapshot = dict(raw_arc_snapshot)
            committed_event = self._persisted_event_from_arc(
                arc_snapshot,
                proposal.event.event_id,
            )
            receipt = verify_committed_world_event(
                proposal,
                committed_event=committed_event,
                committed_arc_snapshot=arc_snapshot,
                committed_event_ids=self._committed_ids(arc_snapshot),
                persisted_revision=int(arc_snapshot.get("revision") or 0),
            )
            persisted_receipt = await self._governance_store.append_commit_receipt(receipt)
        except asyncio.CancelledError:
            # A completed Arc update without a receipt is explicitly recovered by
            # the next exact retry; do not invent a second event.
            raise
        except Exception as exc:
            return self._blocked("receipt_suffix_pending", exc, proposal)
        return ScheduleGovernanceOutcome(
            status="committed",
            proposal_id=proposal.proposal_id,
            receipt=persisted_receipt,
        )

    def _build_fresh_proposal(self, schedule: Schedule) -> WorldbookEventProposalV1:
        target_arc_id = self._explicit_main_arc_id(schedule.date)
        summary = _summarize_schedule(schedule)
        if not summary:
            raise ValueError("schedule summary is empty")
        return build_schedule_event_proposal(
            world_id=LEGACY_SINGLETON_WORLD_ID,
            target_arc_id=target_arc_id,
            schedule_date=schedule.date,
            summary=summary,
            proposed_at=self._now_utc(),
        )

    def _proposal_from_snapshot(
        self,
        snapshot: ScheduleGovernanceSnapshot,
    ) -> WorldbookEventProposalV1:
        intent = snapshot.governance_intent
        if not isinstance(intent, dict) or set(intent) != _INTENT_FIELDS:
            raise ValueError("governance intent is not the v1 closed schema")
        if str(intent.get("contract_version") or "") != _INTENT_VERSION:
            raise ValueError("governance intent version is invalid")
        schedule = snapshot.schedule
        if str(intent.get("schedule_date") or "") != schedule.date:
            raise ValueError("governance intent date does not match source")
        if str(intent.get("world_id") or "") != LEGACY_SINGLETON_WORLD_ID:
            raise ValueError("governance intent world is invalid")
        source_sha256 = str(intent.get("source_sha256") or "").lower()
        if not _is_sha256(source_sha256) or source_sha256 != snapshot.source_sha256:
            raise ValueError("governance intent source digest does not match")
        summary = _summarize_schedule(schedule)
        if not summary:
            raise ValueError("governed schedule summary is empty")
        summary_sha256 = hashlib.sha256(summary.encode("utf-8")).hexdigest()
        if str(intent.get("summary_sha256") or "").lower() != summary_sha256:
            raise ValueError("governance intent summary digest does not match")
        proposal = build_schedule_event_proposal(
            world_id=LEGACY_SINGLETON_WORLD_ID,
            target_arc_id=str(intent.get("arc_id") or ""),
            schedule_date=schedule.date,
            summary=summary,
            proposed_at=str(intent.get("proposed_at") or ""),
        )
        if (
            str(intent.get("proposal_id") or "") != proposal.proposal_id
            or str(intent.get("proposal_sha256") or "").lower()
            != proposal.proposal_sha256
        ):
            raise ValueError("governance intent proposal identity does not match")
        return proposal

    def _intent_for(
        self,
        schedule: Schedule,
        proposal: WorldbookEventProposalV1,
    ) -> dict[str, str]:
        source_binding = proposal.source_binding
        summary_sha256 = str(getattr(source_binding, "summary_sha256", "") or "")
        if not _is_sha256(summary_sha256):
            raise ValueError("schedule proposal binding is invalid")
        proposed_at = proposal.proposed_at
        if not isinstance(proposed_at, datetime):
            raise TypeError("proposal timestamp is invalid")
        return {
            "contract_version": _INTENT_VERSION,
            "world_id": proposal.world_ref.world_id,
            "arc_id": proposal.world_ref.arc_id,
            "schedule_date": schedule.date,
            "summary_sha256": summary_sha256,
            "source_sha256": ScheduleStore.source_sha256(schedule),
            "proposed_at": _iso_utc(proposed_at),
            "proposal_id": proposal.proposal_id,
            "proposal_sha256": proposal.proposal_sha256,
        }

    def _explicit_main_arc_id(self, schedule_date: str) -> str:
        config = getattr(self._runtime, "config", None)
        if not bool(getattr(config, "enabled", False)) or not bool(
            getattr(config, "schedule_projection_enabled", False)
        ):
            raise ValueError("worldbook schedule projection is disabled")
        ledger = getattr(self._runtime, "ledger", None)
        if ledger is None:
            raise ValueError("worldbook ledger is unavailable")
        load_stack = getattr(ledger, "load_stack", None)
        if not callable(load_stack):
            raise ValueError("worldbook ledger cannot load its stack")
        view = load_stack(on_date=schedule_date)
        main = getattr(view, "main", None)
        if not isinstance(main, Mapping):
            raise ValueError("worldbook has no explicit main arc")
        arc_id = str(main.get("arc_id") or "").strip()
        if not arc_id or str(main.get("arc_role") or "").strip().lower() != "main":
            raise ValueError("worldbook main arc is not explicit")
        arc = self._story_arc_store().load(arc_id)
        if (
            arc is None
            or str(getattr(arc, "arc_id", "") or "") != arc_id
            or str(getattr(arc, "arc_role", "") or "").strip().lower() != "main"
        ):
            raise ValueError("worldbook explicit main arc is unavailable")
        return arc_id

    def _story_arc_store(self) -> Any:
        ledger = getattr(self._runtime, "ledger", None)
        store = getattr(ledger, "_store", None)
        if store is None:
            raise ValueError("worldbook story arc store is unavailable")
        return store

    def _apply_commit_mutation(
        self,
        arc: Any,
        *,
        proposal: WorldbookEventProposalV1,
        snapshot: ScheduleGovernanceSnapshot,
        mutation: dict[str, Any],
    ) -> None:
        if str(getattr(arc, "arc_id", "") or "") != proposal.world_ref.arc_id:
            mutation["reason"] = "target_arc_changed"
            return
        if str(getattr(arc, "arc_role", "") or "").strip().lower() != "main":
            mutation["reason"] = "target_arc_not_main"
            return
        budget = getattr(arc, "event_budget", None)
        if not isinstance(budget, dict):
            mutation["reason"] = "event_budget_unavailable"
            return
        schedule_date = self._schedule_date_of(proposal)
        if self._has_legacy_schedule_fact(arc, schedule_date):
            mutation["reason"] = "legacy_schedule_fact_exists"
            return
        markers = budget.get(_ARC_MARKER_KEY)
        if markers is not None and not isinstance(markers, dict):
            mutation["reason"] = "governance_marker_unavailable"
            return
        expected_marker = self._arc_marker(snapshot, proposal)
        existing_marker = markers.get(schedule_date) if isinstance(markers, dict) else None
        if existing_marker is not None:
            if existing_marker != expected_marker:
                mutation["reason"] = "governance_marker_conflict"
                return
            if not self._arc_has_canonical_event(arc, proposal.event.event_id):
                mutation["reason"] = "governance_marker_without_event"
                return
            mutation["status"] = "already_committed"
            mutation["reason"] = "exact_recovery"
            return

        try:
            step = date.fromisoformat(schedule_date).toordinal()
        except ValueError:
            mutation["reason"] = "invalid_schedule_date"
            return
        committed = replace(
            proposal.event,
            status="committed",
            committed_at=_iso_utc(self._now_utc()),
            step=step,
        )
        reducer = getattr(self._runtime, "reducer", None)
        apply = getattr(reducer, "apply", None)
        if not callable(apply):
            mutation["reason"] = "worldbook_reducer_unavailable"
            return
        apply(arc, committed, now_step=step)
        if not self._arc_has_canonical_event(arc, proposal.event.event_id):
            mutation["reason"] = "reducer_did_not_commit_event"
            return
        if markers is None:
            markers = {}
            budget[_ARC_MARKER_KEY] = markers
        markers[schedule_date] = expected_marker
        mutation["status"] = "committed"
        mutation["reason"] = "ok"

    @staticmethod
    def _arc_marker(
        snapshot: ScheduleGovernanceSnapshot,
        proposal: WorldbookEventProposalV1,
    ) -> dict[str, str]:
        return {
            "proposal_id": proposal.proposal_id,
            "proposal_sha256": proposal.proposal_sha256,
            "event_id": proposal.event.event_id,
            "summary_sha256": str(
                getattr(proposal.source_binding, "summary_sha256", "") or ""
            ),
            "source_sha256": snapshot.source_sha256,
        }

    @staticmethod
    def _arc_has_canonical_event(arc: Any, event_id: str) -> bool:
        ids = getattr(arc, "event_budget", {}).get("committed_event_ids")
        if not isinstance(ids, list) or event_id not in {str(item) for item in ids}:
            return False
        history = getattr(arc, "event_history", None)
        if not isinstance(history, list):
            return False
        return sum(
            1
            for item in history
            if isinstance(item, Mapping) and str(item.get("event_id") or "") == event_id
        ) == 1

    @staticmethod
    def _has_legacy_schedule_fact(arc: Any, schedule_date: str) -> bool:
        legacy_ids = {
            f"schedule.{schedule_date}",
            f"schedule:{schedule_date}",
            f"schedule_generator:{schedule_date}",
        }
        budget = getattr(arc, "event_budget", None)
        if not isinstance(budget, Mapping):
            return True
        generated_dates = budget.get("generated_schedule_dates")
        if isinstance(generated_dates, (list, tuple)) and schedule_date in {
            str(value) for value in generated_dates
        }:
            return True
        committed_ids = budget.get("committed_event_ids")
        if isinstance(committed_ids, (list, tuple)) and legacy_ids.intersection(
            {str(value) for value in committed_ids}
        ):
            return True
        for collection_name in ("event_history", "last_events"):
            values = getattr(arc, collection_name, None)
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, Mapping):
                    continue
                if str(item.get("event_id") or "") in legacy_ids:
                    return True
                if (
                    str(item.get("source") or "") == "schedule_generator"
                    and str(item.get("date") or "") == schedule_date
                ):
                    return True
        return False

    @staticmethod
    def _persisted_event_from_arc(
        arc_snapshot: Mapping[str, Any],
        event_id: str,
    ) -> Any:
        history = arc_snapshot.get("event_history")
        if not isinstance(history, list):
            raise ValueError("persisted arc event history is unavailable")
        matches = [
            item
            for item in history
            if isinstance(item, Mapping) and str(item.get("event_id") or "") == event_id
        ]
        if len(matches) != 1:
            raise ValueError("persisted canonical event is missing or ambiguous")
        event = matches[0]
        from services.worldbook.domain import EventRecord

        return EventRecord(
            event_id=str(event.get("event_id") or ""),
            event_type=str(event.get("event_type") or ""),
            summary=str(event.get("summary") or ""),
            status="committed",
            arc_id=str(event.get("arc_id") or ""),
            variable_deltas=dict(event.get("variable_deltas") or {}),
            consequences=tuple(str(value) for value in event.get("consequences") or ()),
            recovery_steps=0,
            severity=str(event.get("severity") or ""),
            source=cast(Any, str(event.get("source") or "")),
            evidence_refs=tuple(str(value) for value in event.get("evidence_refs") or ()),
            committed_at=str(event.get("committed_at") or ""),
            step=int(event.get("step") or 0),
        )

    @staticmethod
    def _committed_ids(arc_snapshot: Mapping[str, Any]) -> tuple[str, ...]:
        budget = arc_snapshot.get("event_budget")
        if not isinstance(budget, Mapping):
            raise ValueError("persisted event budget is unavailable")
        raw = budget.get("committed_event_ids")
        if not isinstance(raw, (list, tuple)):
            raise ValueError("persisted committed event IDs are unavailable")
        return tuple(str(value) for value in raw)

    @staticmethod
    def _schedule_date_of(proposal: WorldbookEventProposalV1) -> str:
        return str(getattr(proposal.source_binding, "schedule_date", "") or "")

    @staticmethod
    def _is_schedule_proposal(proposal: WorldbookEventProposalV1) -> bool:
        return bool(
            proposal.source is WorldbookEventSource.SCHEDULE
            and proposal.world_ref.world_id == LEGACY_SINGLETON_WORLD_ID
        )

    def _now_utc(self) -> datetime:
        current = self._now()
        if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("bridge clock must return an aware datetime")
        return current.astimezone(UTC)

    @staticmethod
    def _blocked(
        code: str,
        exc: BaseException,
        proposal: WorldbookEventProposalV1 | None = None,
    ) -> ScheduleGovernanceOutcome:
        _L.warning(
            "worldbook schedule governance blocked | code={} error={}",
            code,
            type(exc).__name__,
        )
        return ScheduleGovernanceOutcome(
            status="blocked",
            proposal_id=proposal.proposal_id if proposal is not None else "",
            reason=code,
        )


def _summarize_schedule(schedule: Schedule) -> str:
    pieces: list[str] = []
    if schedule.theme:
        pieces.append(f"主题《{schedule.theme}》")
    if schedule.day_narrative:
        pieces.append(_truncate_line(schedule.day_narrative, 70))
    for slot in schedule.slots:
        if slot.activity in {"practice", "study", "social"} and slot.description:
            pieces.append(f"{slot.time} {slot.description}")
            break
    return "；".join(_truncate_line(piece, 90) for piece in pieces if piece)


def _truncate_line(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


__all__ = [
    "ScheduleGovernanceOutcome",
    "ScheduleWorldbookGovernanceBridge",
]
