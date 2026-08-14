"""Pure adapters from trusted domain records to governed Worldbook events."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, cast

from services.memory.governance_contracts import EvidenceAtomV1, sha256_text
from services.social_narrative import SocialExperience
from services.worldbook import governance_contracts as governance
from services.worldbook.domain import (
    SOCIAL_INFLUENCE_SUMMARY,
    SOCIAL_RESONANCE_DELTA,
    EventRecord,
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _required_text(value: object, field_name: str, *, maximum: int = 240) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ValueError(f"{field_name} must be 1..{maximum} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValueError(f"{field_name} contains control characters")
    return text


def _qq_id(value: object, field_name: str) -> str:
    text = _required_text(value, field_name, maximum=32)
    if not text.isdigit():
        raise ValueError(f"{field_name} must be numeric")
    return str(int(text))


def _aware_utc(value: datetime | str, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid {field_name}: {value!r}") from exc
    else:
        raise TypeError(f"{field_name} must be an aware datetime or ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def build_schedule_event_proposal(
    *,
    world_id: str,
    target_arc_id: str,
    schedule_date: str,
    summary: str,
    proposed_at: datetime | str,
) -> governance.WorldbookEventProposalV1:
    world_ref = governance.WorldRefV1(world_id=world_id, arc_id=target_arc_id)
    raw_date = str(schedule_date or "").strip()
    if _ISO_DATE_RE.fullmatch(raw_date) is None:
        raise ValueError("schedule_date must use YYYY-MM-DD")
    try:
        parsed_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ValueError(f"invalid schedule_date: {schedule_date!r}") from exc
    if parsed_date.isoformat() != raw_date:
        raise ValueError("schedule_date must be canonical ISO format")
    clean_summary = _required_text(summary, "summary", maximum=2_000)
    source_binding = governance.ScheduleSourceBindingV1.create(
        schedule_date=raw_date,
        summary=clean_summary,
    )
    event = EventRecord(
        event_id=governance._deterministic_world_event_id(
            source=governance.WorldbookEventSource.SCHEDULE,
            world_ref=world_ref,
            source_binding=source_binding,
        ),
        event_type="schedule",
        summary=clean_summary,
        status="proposal",
        arc_id=world_ref.arc_id,
        variable_deltas={},
        consequences=(),
        recovery_steps=0,
        severity="daily",
        source=cast(Any, governance.WorldbookEventSource.SCHEDULE.value),
        evidence_refs=(),
        committed_at="",
        step=0,
    )
    return governance.WorldbookEventProposalV1.create(
        world_ref=world_ref,
        source=governance.WorldbookEventSource.SCHEDULE,
        source_ref=f"schedule:{raw_date}",
        source_binding=source_binding,
        evidence=(),
        event=event,
        proposed_at=proposed_at,
    )


def build_social_event_proposal(
    *,
    record: SocialExperience,
    world_id: str,
    target_arc_id: str,
    current_group_id: str,
    current_user_id: str,
    proposed_at: datetime | str,
) -> governance.WorldbookEventProposalV1:
    if not isinstance(record, SocialExperience):
        raise TypeError("record must be a persisted SocialExperience")
    if str(record.entity_kind or "").strip().lower() != "factual":
        raise ValueError("social record must be factual")
    if str(record.status or "").strip().lower() != "active":
        raise ValueError("social record must be active")
    privacy = str(getattr(record, "privacy", "") or "").strip().lower()
    if privacy and privacy not in {"group", "public"}:
        raise ValueError("private social evidence cannot enter Worldbook")

    record_group = _qq_id(record.group_id, "record.group_id")
    record_user = _qq_id(record.user_id, "record.user_id")
    current_group = _qq_id(current_group_id, "current_group_id")
    current_user = _qq_id(current_user_id, "current_user_id")
    if record_group != current_group:
        raise ValueError("social evidence group scope mismatch")
    if record_user != current_user:
        raise ValueError("social evidence user scope mismatch")

    experience_id = _required_text(record.experience_id, "experience_id")
    message_id = _required_text(record.evidence_message_id, "evidence_message_id")
    evidence_source = _required_text(record.evidence_source, "evidence_source")
    occurred_at = _aware_utc(record.evidence_time, "evidence_time")
    proposed_at_utc = _aware_utc(proposed_at, "proposed_at")
    if proposed_at_utc < occurred_at:
        raise ValueError("proposed_at cannot precede social evidence")
    world_ref = governance.WorldRefV1(world_id=world_id, arc_id=target_arc_id)
    source_binding = governance.SocialSourceBindingV1(
        experience_id=experience_id,
        group_id=record_group,
        user_id=record_user,
        evidence_message_id=message_id,
        evidence_source=evidence_source,
        evidence_time=occurred_at,
    )
    evidence_ref = governance._social_evidence_ref(source_binding)
    evidence = (
        EvidenceAtomV1(
            evidence_ref=evidence_ref,
            content_sha256=sha256_text(governance.SOCIAL_EVIDENCE_ATTESTATION),
            quote=governance.SOCIAL_EVIDENCE_ATTESTATION,
            actor_ref=f"user:qq:{record_user}",
            occurred_at=occurred_at,
        ),
    )
    event = EventRecord(
        event_id=governance._deterministic_world_event_id(
            source=governance.WorldbookEventSource.SOCIAL_EVIDENCE,
            world_ref=world_ref,
            source_binding=source_binding,
        ),
        event_type="social_influence",
        summary=SOCIAL_INFLUENCE_SUMMARY,
        status="proposal",
        arc_id=world_ref.arc_id,
        variable_deltas={"social_resonance": SOCIAL_RESONANCE_DELTA},
        consequences=(),
        recovery_steps=0,
        severity="daily",
        source="social_evidence",
        evidence_refs=(evidence_ref,),
        committed_at="",
        step=0,
    )
    return governance.WorldbookEventProposalV1.create(
        world_ref=world_ref,
        source=governance.WorldbookEventSource.SOCIAL_EVIDENCE,
        source_ref=evidence_ref,
        source_binding=source_binding,
        evidence=evidence,
        event=event,
        proposed_at=proposed_at_utc,
    )


def _normalized_event_ids(values: Sequence[object], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_required_text(value, field_name, maximum=120) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} contains duplicate event IDs")
    return normalized


def _committed_semantics(event: EventRecord) -> dict[str, Any]:
    payload = event.to_dict()
    for key in ("status", "committed_at", "step"):
        payload.pop(key, None)
    return payload


def _persisted_reducer_event(event: EventRecord) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "summary": event.summary,
        "severity": event.severity,
        "step": int(event.step),
        "source": event.source,
        "arc_id": event.arc_id,
        "evidence_refs": list(event.evidence_refs),
        "committed_at": event.committed_at,
        "variable_deltas": dict(event.variable_deltas),
        "consequences": list(event.consequences),
    }


def verify_committed_world_event(
    proposal: governance.WorldbookEventProposalV1,
    *,
    committed_event: EventRecord,
    committed_arc_snapshot: Mapping[str, Any],
    committed_event_ids: Sequence[str],
    persisted_revision: int,
) -> governance.WorldbookCommitReceiptV1:
    if not isinstance(proposal, governance.WorldbookEventProposalV1):
        raise TypeError("proposal must be WorldbookEventProposalV1")
    if proposal.world_ref.world_id != governance.LEGACY_SINGLETON_WORLD_ID:
        raise ValueError("legacy StoryArc snapshots can verify only omubot.default")
    if not isinstance(committed_event, EventRecord):
        raise TypeError("committed_event must be EventRecord")
    if committed_event.status != "committed":
        raise ValueError("committed_event must have committed status")
    if type(committed_event.step) is not int or type(committed_event.recovery_steps) is not int:
        raise TypeError("committed event step metadata must use exact integers")
    _aware_utc(committed_event.committed_at, "committed_event.committed_at")
    if _committed_semantics(committed_event) != _committed_semantics(proposal.event):
        raise ValueError("committed event payload does not exactly match proposal")
    if committed_event.arc_id != proposal.world_ref.arc_id:
        raise ValueError("committed event arc does not match proposal")
    if not isinstance(committed_arc_snapshot, Mapping):
        raise TypeError("committed_arc_snapshot must be a mapping")
    snapshot = dict(committed_arc_snapshot)
    snapshot_world = str(snapshot.get("world_id") or "").strip()
    if snapshot_world and snapshot_world != proposal.world_ref.world_id:
        raise ValueError("persisted arc world does not match proposal")
    if str(snapshot.get("arc_id") or "").strip() != proposal.world_ref.arc_id:
        raise ValueError("persisted arc does not match proposal")
    if isinstance(persisted_revision, bool) or not isinstance(persisted_revision, int):
        raise TypeError("persisted_revision must be an integer")
    snapshot_revision = snapshot.get("revision")
    if isinstance(snapshot_revision, bool) or not isinstance(snapshot_revision, int):
        raise TypeError("persisted arc revision must be an integer")
    if persisted_revision <= 0 or snapshot_revision != persisted_revision:
        raise ValueError("persisted revision does not match arc snapshot")

    caller_ids = _normalized_event_ids(committed_event_ids, "committed_event_ids")
    budget = snapshot.get("event_budget")
    if not isinstance(budget, Mapping):
        raise ValueError("persisted arc is missing event_budget")
    durable_raw = budget.get("committed_event_ids")
    if not isinstance(durable_raw, list | tuple):
        raise ValueError("persisted arc is missing durable committed_event_ids")
    durable_ids = _normalized_event_ids(durable_raw, "snapshot committed_event_ids")
    if set(caller_ids) != set(durable_ids) or committed_event.event_id not in caller_ids:
        raise ValueError("durable committed event IDs do not match")

    history = snapshot.get("event_history")
    if not isinstance(history, list):
        raise ValueError("persisted arc is missing event_history")
    matching_history = [
        item
        for item in history
        if isinstance(item, Mapping)
        and str(item.get("event_id") or "").strip() == committed_event.event_id
    ]
    expected_json = governance.canonical_worldbook_json(
        _persisted_reducer_event(committed_event)
    )
    if (
        len(matching_history) != 1
        or governance.canonical_worldbook_json(matching_history[0]) != expected_json
    ):
        raise ValueError("persisted event history does not exactly match committed event")

    receipt = object.__new__(governance.WorldbookCommitReceiptV1)
    object.__setattr__(receipt, "world_ref", proposal.world_ref)
    object.__setattr__(receipt, "proposal_id", proposal.proposal_id)
    object.__setattr__(receipt, "proposal_sha256", proposal.proposal_sha256)
    object.__setattr__(receipt, "event_id", committed_event.event_id)
    object.__setattr__(receipt, "arc_id", committed_event.arc_id)
    object.__setattr__(receipt, "persisted_revision", persisted_revision)
    object.__setattr__(receipt, "contract_version", "worldbook.commit_receipt.v1")
    object.__setattr__(
        receipt,
        "persisted_event_sha256",
        governance.worldbook_sha256(committed_event.to_dict()),
    )
    object.__setattr__(
        receipt,
        "persisted_arc_sha256",
        governance.worldbook_sha256(snapshot),
    )
    object.__setattr__(
        receipt,
        "committed_event_ids_sha256",
        governance.worldbook_sha256(sorted(durable_ids)),
    )
    object.__setattr__(
        receipt,
        "receipt_sha256",
        governance.worldbook_sha256(receipt._identity_dict()),
    )
    return receipt


__all__ = [
    "build_schedule_event_proposal",
    "build_social_event_proposal",
    "verify_committed_world_event",
]
