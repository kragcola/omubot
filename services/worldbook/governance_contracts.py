"""Pure governance contracts for proposed and durably committed world events."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, ClassVar

from services.memory.governance_contracts import EvidenceAtomV1
from services.worldbook.domain import (
    SOCIAL_INFLUENCE_SUMMARY,
    SOCIAL_RESONANCE_DELTA,
    EventRecord,
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LEGACY_SINGLETON_WORLD_ID = "omubot.default"
SOCIAL_EVIDENCE_ATTESTATION = "Verified group-scoped social interaction."
_SCHEDULE_FORBIDDEN_SOCIAL_CARRIERS = (
    "social_experience",
    "social_evidence",
    "experience_id",
    "user_id",
    "group_id",
    "user_text",
    "bot_reply",
    "evidence_message_id",
    "evidence_source",
    "evidence_time",
    "evidence_ref",
    "content_sha256",
    "actor_ref",
    "quote=",
    "occurred_at=",
    "origin_group_ref",
    "subject_ref",
    "owner_id",
    "message_pk:",
    "user:qq:",
    "group:qq:",
    "raw_user",
    "raw_bot",
)


class WorldbookEventSource(StrEnum):
    SCHEDULE = "schedule"
    SOCIAL_EVIDENCE = "social_evidence"


def _bounded_text(value: object, field_name: str, *, maximum: int = 240) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
    ):
        raise ValueError(f"{field_name} must be 1..{maximum} safe characters")
    return text


def _canonical_id(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if _ID_RE.fullmatch(text) is None:
        raise ValueError(f"invalid {field_name}: {value!r}")
    return text


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


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return _iso_utc(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_worldbook_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def worldbook_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_worldbook_json(value).encode("utf-8")).hexdigest()


def _validated_digest(value: object, field_name: str) -> str:
    digest = str(value or "").strip().lower()
    if _SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{field_name} must be a bare SHA-256 digest")
    return digest


@dataclass(frozen=True, slots=True)
class WorldRefV1:
    world_id: str
    arc_id: str
    contract_version: ClassVar[str] = "worldbook.ref.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "world_id", _canonical_id(self.world_id, "world_id"))
        object.__setattr__(self, "arc_id", _canonical_id(self.arc_id, "arc_id"))

    @property
    def subject_ref(self) -> str:
        return f"worldbook:world:{self.world_id}"

    def to_dict(self) -> dict[str, str]:
        return {
            "contract_version": self.contract_version,
            "world_id": self.world_id,
            "arc_id": self.arc_id,
            "subject_ref": self.subject_ref,
        }


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _positive_qq_id(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text.isdigit() or int(text) <= 0:
        raise ValueError(f"{field_name} must be a positive numeric QQ ID")
    return str(int(text))


@dataclass(frozen=True, slots=True)
class ScheduleSourceBindingV1:
    schedule_date: str
    summary_sha256: str
    contract_version: ClassVar[str] = "worldbook.schedule_source.v1"

    def __post_init__(self) -> None:
        raw_date = str(self.schedule_date or "").strip()
        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ValueError("schedule_date must be a real ISO date") from exc
        if parsed_date.isoformat() != raw_date:
            raise ValueError("schedule_date must use canonical ISO format")
        object.__setattr__(self, "schedule_date", raw_date)
        object.__setattr__(
            self,
            "summary_sha256",
            _validated_digest(self.summary_sha256, "summary_sha256"),
        )

    @classmethod
    def create(cls, *, schedule_date: str, summary: str) -> ScheduleSourceBindingV1:
        return cls(
            schedule_date=schedule_date,
            summary_sha256=_text_sha256(summary),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "contract_version": self.contract_version,
            "schedule_date": self.schedule_date,
            "summary_sha256": self.summary_sha256,
        }


@dataclass(frozen=True, slots=True)
class SocialSourceBindingV1:
    experience_id: str
    group_id: str
    user_id: str
    evidence_message_id: str
    evidence_source: str
    evidence_time: datetime | str
    contract_version: ClassVar[str] = "worldbook.social_source.v1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "experience_id",
            _canonical_id(self.experience_id, "experience_id"),
        )
        object.__setattr__(self, "group_id", _positive_qq_id(self.group_id, "group_id"))
        object.__setattr__(self, "user_id", _positive_qq_id(self.user_id, "user_id"))
        object.__setattr__(
            self,
            "evidence_message_id",
            _canonical_id(self.evidence_message_id, "evidence_message_id"),
        )
        object.__setattr__(
            self,
            "evidence_source",
            _canonical_id(self.evidence_source, "evidence_source"),
        )
        object.__setattr__(
            self,
            "evidence_time",
            _aware_utc(self.evidence_time, "evidence_time"),
        )

    def to_dict(self) -> dict[str, str]:
        if not isinstance(self.evidence_time, datetime):
            raise TypeError("evidence_time was not normalized")
        return {
            "contract_version": self.contract_version,
            "experience_id": self.experience_id,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "evidence_message_id": self.evidence_message_id,
            "evidence_source": self.evidence_source,
            "evidence_time": _iso_utc(self.evidence_time),
        }


type WorldbookSourceBindingV1 = ScheduleSourceBindingV1 | SocialSourceBindingV1


def _social_evidence_ref(source_binding: SocialSourceBindingV1) -> str:
    digest = worldbook_sha256(source_binding.to_dict())
    return f"social_experience:{source_binding.experience_id}.{digest[:24]}"


def _source_binding_sha256(
    *,
    source: WorldbookEventSource,
    world_ref: WorldRefV1,
    source_binding: WorldbookSourceBindingV1,
) -> str:
    return worldbook_sha256(
        {
            "source": source.value,
            "world_ref": world_ref.to_dict(),
            "source_binding": source_binding.to_dict(),
        }
    )


def _deterministic_world_event_id(
    *,
    source: WorldbookEventSource,
    world_ref: WorldRefV1,
    source_binding: WorldbookSourceBindingV1,
) -> str:
    digest = _source_binding_sha256(
        source=source,
        world_ref=world_ref,
        source_binding=source_binding,
    )
    prefix = "schedule" if source is WorldbookEventSource.SCHEDULE else "social"
    return f"wevt_{prefix}_{digest[:24]}"


@dataclass(frozen=True, slots=True)
class WorldbookEventProposalV1:
    world_ref: WorldRefV1
    source: WorldbookEventSource
    source_ref: str
    source_binding: WorldbookSourceBindingV1
    evidence: tuple[EvidenceAtomV1, ...]
    event: EventRecord
    proposed_at: datetime | str
    status: str = "proposal"
    contract_version: str = field(init=False, default="worldbook.event_proposal.v1")
    source_binding_sha256: str = field(init=False)
    proposal_id: str = field(init=False)
    proposal_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.world_ref, WorldRefV1):
            raise TypeError("world_ref must be WorldRefV1")
        try:
            source = WorldbookEventSource(self.source)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid Worldbook event source: {self.source!r}") from exc
        source_ref = str(self.source_ref or "").strip()
        if not source_ref or len(source_ref) > 240:
            raise ValueError("source_ref must be 1..240 characters")
        if any(ord(character) < 32 or ord(character) == 127 for character in source_ref):
            raise ValueError("source_ref contains control characters")
        if not isinstance(
            self.source_binding,
            ScheduleSourceBindingV1 | SocialSourceBindingV1,
        ):
            raise TypeError("source_binding must be a Worldbook source binding v1")
        binding_digest = _source_binding_sha256(
            source=source,
            world_ref=self.world_ref,
            source_binding=self.source_binding,
        )
        evidence = tuple(self.evidence)
        if not all(isinstance(atom, EvidenceAtomV1) for atom in evidence):
            raise TypeError("evidence must contain only EvidenceAtomV1 values")
        refs = tuple(atom.evidence_ref for atom in evidence)
        if len(refs) != len(set(refs)):
            raise ValueError("evidence refs must be unique")
        if not isinstance(self.event, EventRecord):
            raise TypeError("event must be EventRecord")
        if self.status != "proposal" or self.event.status != "proposal":
            raise ValueError("Worldbook proposal and event must remain proposals")
        if self.event.arc_id != self.world_ref.arc_id:
            raise ValueError("event arc_id must match world_ref")
        if str(self.event.source) != source.value:
            raise ValueError("event source must match proposal source")
        if tuple(self.event.evidence_refs) != refs:
            raise ValueError("event evidence_refs must exactly match proposal evidence")
        if not self.event.summary.strip():
            raise ValueError("event summary is required")
        proposed_at = _aware_utc(self.proposed_at, "proposed_at")
        expected_event_id = _deterministic_world_event_id(
            source=source,
            world_ref=self.world_ref,
            source_binding=self.source_binding,
        )
        if self.event.event_id != expected_event_id:
            raise ValueError("event_id must match the governed source binding")
        if type(self.event.step) is not int or type(self.event.recovery_steps) is not int:
            raise TypeError("proposal event step metadata must use exact integers")
        if self.event.committed_at or self.event.step != 0:
            raise ValueError("proposal events cannot carry commit metadata")
        if self.event.consequences or self.event.recovery_steps != 0:
            raise ValueError("proposal effects must use the closed v1 schema")
        if self.event.severity != "daily":
            raise ValueError("governed v1 proposals must use daily severity")

        if source is WorldbookEventSource.SCHEDULE:
            self._validate_schedule_event(source_ref, evidence, self.source_binding)
        else:
            self._validate_social_event(
                source_ref,
                evidence,
                proposed_at,
                self.source_binding,
            )

        object.__setattr__(self, "source", source)
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "source_binding_sha256", binding_digest)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "proposed_at", proposed_at)
        logical_digest = worldbook_sha256(self._logical_identity_dict())
        content_digest = worldbook_sha256(self._content_dict())
        object.__setattr__(self, "proposal_sha256", content_digest)
        object.__setattr__(self, "proposal_id", f"wprop_{logical_digest[:24]}")

    def _validate_schedule_event(
        self,
        source_ref: str,
        evidence: tuple[EvidenceAtomV1, ...],
        source_binding: WorldbookSourceBindingV1,
    ) -> None:
        if not isinstance(source_binding, ScheduleSourceBindingV1):
            raise TypeError("schedule proposal requires ScheduleSourceBindingV1")
        if evidence or self.event.evidence_refs:
            raise ValueError("schedule proposals cannot carry evidence")
        raw_date = source_ref.removeprefix("schedule:")
        if source_ref == raw_date:
            raise ValueError("schedule source_ref must bind a schedule date")
        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ValueError("schedule source_ref must bind a real ISO date") from exc
        if parsed_date.isoformat() != raw_date:
            raise ValueError("schedule source_ref must use canonical ISO format")
        if source_binding.schedule_date != raw_date:
            raise ValueError("schedule source binding must match source_ref")
        if source_binding.summary_sha256 != _text_sha256(self.event.summary):
            raise ValueError("schedule source binding must match event summary")
        if self.event.event_type != "schedule" or dict(self.event.variable_deltas):
            raise ValueError("schedule event must use the closed schedule schema")
        privacy_surface = f"{source_ref}\n{self.event.summary}".lower()
        if any(token in privacy_surface for token in _SCHEDULE_FORBIDDEN_SOCIAL_CARRIERS):
            raise ValueError("schedule proposals cannot carry social scope tokens")

    def _validate_social_event(
        self,
        source_ref: str,
        evidence: tuple[EvidenceAtomV1, ...],
        proposed_at: datetime,
        source_binding: WorldbookSourceBindingV1,
    ) -> None:
        if not isinstance(source_binding, SocialSourceBindingV1):
            raise TypeError("social proposal requires SocialSourceBindingV1")
        if len(evidence) != 1:
            raise ValueError("social proposals require one verified evidence atom")
        atom = evidence[0]
        if not source_ref.startswith("social_experience:"):
            raise ValueError("social source_ref must bind a factual experience")
        if source_ref != atom.evidence_ref:
            raise ValueError("social source_ref must exactly match evidence")
        if source_ref != _social_evidence_ref(source_binding):
            raise ValueError("social source binding must match evidence ref")
        if atom.quote != SOCIAL_EVIDENCE_ATTESTATION:
            raise ValueError("social proposal evidence must be a bounded attestation")
        if not atom.actor_ref.startswith("user:qq:") or not isinstance(
            atom.occurred_at, datetime
        ):
            raise ValueError("social evidence requires a scoped actor and source time")
        if atom.actor_ref != f"user:qq:{source_binding.user_id}":
            raise ValueError("social source binding must match evidence actor")
        if atom.occurred_at != source_binding.evidence_time:
            raise ValueError("social source binding must match evidence time")
        if proposed_at < atom.occurred_at:
            raise ValueError("proposed_at cannot precede social evidence")
        expected_effect = {"social_resonance": SOCIAL_RESONANCE_DELTA}
        if (
            self.event.event_type != "social_influence"
            or self.event.summary != SOCIAL_INFLUENCE_SUMMARY
            or dict(self.event.variable_deltas) != expected_effect
        ):
            raise ValueError("social event must use the closed social influence schema")

    @classmethod
    def create(
        cls,
        *,
        world_ref: WorldRefV1,
        source: WorldbookEventSource,
        source_ref: str,
        source_binding: WorldbookSourceBindingV1,
        evidence: tuple[EvidenceAtomV1, ...],
        event: EventRecord,
        proposed_at: datetime | str,
    ) -> WorldbookEventProposalV1:
        return cls(
            world_ref=world_ref,
            source=source,
            source_ref=source_ref,
            source_binding=source_binding,
            evidence=evidence,
            event=event,
            proposed_at=proposed_at,
        )

    def _logical_identity_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "world_ref": self.world_ref.to_dict(),
            "source": self.source.value,
            "source_ref": self.source_ref,
            "source_binding": self.source_binding.to_dict(),
            "source_binding_sha256": self.source_binding_sha256,
            "evidence": [atom.to_dict() for atom in self.evidence],
            "event": self.event.to_dict(),
            "status": self.status,
        }

    def _content_dict(self) -> dict[str, Any]:
        return {**self._logical_identity_dict(), "proposed_at": self.proposed_at}

    def to_dict(self) -> dict[str, Any]:
        return {
            **_jsonable(self._content_dict()),
            "proposal_id": self.proposal_id,
            "proposal_sha256": self.proposal_sha256,
        }


@dataclass(frozen=True, slots=True)
class WorldbookOperatorDecisionV1:
    proposal_id: str
    decision: str
    reason_code: str
    operator_ref: str
    decided_at: datetime | str
    contract_version: str = field(
        init=False,
        default="worldbook.operator_decision.v1",
    )
    decision_id: str = field(init=False)
    record_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        proposal_id = _canonical_id(self.proposal_id, "proposal_id")
        decision = str(self.decision or "").strip()
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        reason_code = _bounded_text(self.reason_code, "reason_code", maximum=120)
        operator_ref = _bounded_text(self.operator_ref, "operator_ref", maximum=160)
        decided_at = _aware_utc(self.decided_at, "decided_at")
        core = {
            "contract_version": self.contract_version,
            "proposal_id": proposal_id,
            "decision": decision,
            "reason_code": reason_code,
            "operator_ref": operator_ref,
            "decided_at": _iso_utc(decided_at),
        }
        decision_id = f"wdecision_{worldbook_sha256(core)[:24]}"
        record = {**core, "decision_id": decision_id}
        object.__setattr__(self, "proposal_id", proposal_id)
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "operator_ref", operator_ref)
        object.__setattr__(self, "decided_at", decided_at)
        object.__setattr__(self, "decision_id", decision_id)
        object.__setattr__(self, "record_sha256", worldbook_sha256(record))

    @classmethod
    def create(
        cls,
        *,
        proposal_id: str,
        decision: str,
        reason_code: str,
        operator_ref: str,
        decided_at: datetime | str,
    ) -> WorldbookOperatorDecisionV1:
        return cls(
            proposal_id=proposal_id,
            decision=decision,
            reason_code=reason_code,
            operator_ref=operator_ref,
            decided_at=decided_at,
        )

    def to_dict(self) -> dict[str, Any]:
        decided_at = _aware_utc(self.decided_at, "decided_at")
        return {
            "contract_version": self.contract_version,
            "proposal_id": self.proposal_id,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "operator_ref": self.operator_ref,
            "decided_at": _iso_utc(decided_at),
            "decision_id": self.decision_id,
            "record_sha256": self.record_sha256,
        }


@dataclass(frozen=True, slots=True, init=False)
class WorldbookCommitReceiptV1:
    world_ref: WorldRefV1 = field(init=False)
    proposal_id: str = field(init=False)
    proposal_sha256: str = field(init=False)
    event_id: str = field(init=False)
    arc_id: str = field(init=False)
    persisted_revision: int = field(init=False)
    contract_version: str = field(init=False, default="worldbook.commit_receipt.v1")
    persisted_event_sha256: str = field(init=False)
    persisted_arc_sha256: str = field(init=False)
    committed_event_ids_sha256: str = field(init=False)
    receipt_sha256: str = field(init=False)

    def __init__(self) -> None:
        raise TypeError("WorldbookCommitReceiptV1 is created only by commit verification")

    def _identity_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "world_ref": self.world_ref.to_dict(),
            "proposal_id": self.proposal_id,
            "proposal_sha256": self.proposal_sha256,
            "event_id": self.event_id,
            "arc_id": self.arc_id,
            "persisted_revision": self.persisted_revision,
            "persisted_event_sha256": self.persisted_event_sha256,
            "persisted_arc_sha256": self.persisted_arc_sha256,
            "committed_event_ids_sha256": self.committed_event_ids_sha256,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**_jsonable(self._identity_dict()), "receipt_sha256": self.receipt_sha256}


__all__ = [
    "LEGACY_SINGLETON_WORLD_ID",
    "SOCIAL_EVIDENCE_ATTESTATION",
    "ScheduleSourceBindingV1",
    "SocialSourceBindingV1",
    "WorldRefV1",
    "WorldbookCommitReceiptV1",
    "WorldbookEventProposalV1",
    "WorldbookEventSource",
    "WorldbookOperatorDecisionV1",
    "WorldbookSourceBindingV1",
    "canonical_worldbook_json",
    "worldbook_sha256",
]
