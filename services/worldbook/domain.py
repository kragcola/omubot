"""Immutable domain models for the Worldbook Living Story Runtime.

Canon is runtime-immutable. Mutable state carries revision / source /
confidence / privacy / TTL metadata and fails closed on missing evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal

PrivacyLevel = Literal["public", "group", "private", "system"]
Confidence = Literal["high", "medium", "low", "unknown"]
ArcRole = Literal["main", "side", "ambient"]
EventStatus = Literal["proposal", "validated", "committed", "rejected"]
SourceKind = Literal[
    "persona_canon",
    "native_canon",
    "life_state",
    "story_ledger",
    "social_evidence",
    "storylet",
    "drama",
    "dream_proposal",
    "event_reducer",
    "system",
]

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
_CANON_MUTATION_MSG = "Canon is immutable at runtime; mutation rejected"
_SOURCE_KINDS = frozenset({
    "persona_canon",
    "native_canon",
    "life_state",
    "story_ledger",
    "social_evidence",
    "storylet",
    "drama",
    "dream_proposal",
    "event_reducer",
    "system",
})
_PRIVACY_LEVELS = frozenset({"public", "group", "private", "system"})
_CONFIDENCE_LEVELS = frozenset({"high", "medium", "low", "unknown"})
_STORYLET_SEVERITIES = frozenset({
    "daily",
    "tension",
    "setback",
    "major",
    "crisis",
    "recovery",
})


class CanonMutationError(RuntimeError):
    """Raised when a caller attempts to mutate Persona or Native World Canon."""

    def __init__(self, detail: str = _CANON_MUTATION_MSG) -> None:
        super().__init__(detail)


def _require_id(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if _ID_RE.fullmatch(text) is None:
        raise ValueError(f"invalid {field_name}: {value!r}")
    return text


def _freeze_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    return MappingProxyType({str(k): v for k, v in value.items()})


def _list_str(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


@dataclass(frozen=True, slots=True)
class SourceMeta:
    """Provenance attached to every projected or stored fact."""

    source: SourceKind
    scope: str
    confidence: Confidence = "unknown"
    privacy: PrivacyLevel = "system"
    updated_at: str = ""
    decay_at: str | None = None
    revision: int = 0
    evidence_refs: tuple[str, ...] = ()
    hit_reason: str = ""
    priority: int = 100
    budget_decision: str = ""

    def __post_init__(self) -> None:
        if str(self.source) not in _SOURCE_KINDS:
            raise ValueError(f"invalid source: {self.source!r}")
        if not str(self.scope or "").strip():
            raise ValueError("scope is required")
        if str(self.confidence) not in _CONFIDENCE_LEVELS:
            raise ValueError(f"invalid confidence: {self.confidence!r}")
        if str(self.privacy) not in _PRIVACY_LEVELS:
            raise ValueError(f"invalid privacy: {self.privacy!r}")
        if int(self.revision) < 0:
            raise ValueError("revision must be non-negative")
        object.__setattr__(self, "scope", str(self.scope).strip())
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(
                str(ref).strip()
                for ref in self.evidence_refs
                if str(ref).strip()
            ),
        )
        if self.decay_at:
            try:
                datetime.fromisoformat(str(self.decay_at).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"invalid decay_at: {self.decay_at!r}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "scope": self.scope,
            "confidence": self.confidence,
            "privacy": self.privacy,
            "updated_at": self.updated_at,
            "decay_at": self.decay_at,
            "revision": int(self.revision),
            "evidence_refs": list(self.evidence_refs),
            "hit_reason": self.hit_reason,
            "priority": int(self.priority),
            "budget_decision": self.budget_decision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceMeta:
        refs = data.get("evidence_refs") or ()
        if isinstance(refs, list):
            refs_t = tuple(str(r) for r in refs if str(r).strip())
        else:
            refs_t = tuple(str(r) for r in refs) if isinstance(refs, tuple) else ()
        return cls(
            source=str(data.get("source") or "system"),  # type: ignore[arg-type]
            scope=str(data.get("scope") or ""),
            confidence=str(data.get("confidence") or "unknown"),  # type: ignore[arg-type]
            privacy=str(data.get("privacy") or "system"),  # type: ignore[arg-type]
            updated_at=str(data.get("updated_at") or ""),
            decay_at=(
                str(data["decay_at"])
                if data.get("decay_at") not in (None, "")
                else None
            ),
            revision=max(0, int(data.get("revision") or 0)),
            evidence_refs=refs_t,
            hit_reason=str(data.get("hit_reason") or ""),
            priority=int(data.get("priority") or 100),
            budget_decision=str(data.get("budget_decision") or ""),
        )


@dataclass(frozen=True, slots=True)
class CanonEntry:
    """Immutable Native World / Persona Canon fact.

    Loaded only from versioned configuration. Runtime APIs reject mutation.
    """

    entry_id: str
    title: str
    text: str
    keywords: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    regexes: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    related_entities: tuple[str, ...] = ()
    priority: int = 100
    always_active: bool = False
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_id", _require_id(self.entry_id, "entry_id"))
        if not str(self.text or "").strip():
            raise ValueError("canon text must be non-empty")
        object.__setattr__(self, "text", str(self.text).strip())
        object.__setattr__(self, "title", str(self.title or self.entry_id).strip())
        object.__setattr__(
            self,
            "keywords",
            tuple(str(k).strip() for k in self.keywords if str(k).strip()),
        )
        object.__setattr__(
            self,
            "aliases",
            tuple(str(a).strip() for a in self.aliases if str(a).strip()),
        )
        object.__setattr__(
            self,
            "regexes",
            tuple(str(r).strip() for r in self.regexes if str(r).strip()),
        )
        object.__setattr__(
            self,
            "entity_ids",
            tuple(str(e).strip() for e in self.entity_ids if str(e).strip()),
        )
        object.__setattr__(
            self,
            "related_entities",
            tuple(str(e).strip() for e in self.related_entities if str(e).strip()),
        )
        object.__setattr__(self, "metadata", _freeze_mapping(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "title": self.title,
            "text": self.text,
            "keywords": list(self.keywords),
            "aliases": list(self.aliases),
            "regexes": list(self.regexes),
            "entity_ids": list(self.entity_ids),
            "related_entities": list(self.related_entities),
            "priority": int(self.priority),
            "always_active": bool(self.always_active),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CanonEntry:
        return cls(
            entry_id=str(data.get("entry_id") or data.get("id") or ""),
            title=str(data.get("title") or ""),
            text=str(data.get("text") or data.get("content") or ""),
            keywords=tuple(_list_str(data.get("keywords"))),
            aliases=tuple(_list_str(data.get("aliases"))),
            regexes=tuple(_list_str(data.get("regexes"))),
            entity_ids=tuple(_list_str(data.get("entity_ids"))),
            related_entities=tuple(_list_str(data.get("related_entities"))),
            priority=int(data.get("priority") or 100),
            always_active=bool(data.get("always_active") or False),
            metadata=dict(data.get("metadata") or {})
            if isinstance(data.get("metadata"), dict)
            else {},
        )


# Sources allowed to label life-state as self / private (trusted self-model).
_LIFE_STATE_TRUSTED_SOURCES = frozenset({
    "life_state",
    "story_ledger",
    "drama",
    "system",
})


def validate_life_state_meta(meta: SourceMeta, *, require_ttl: bool = True) -> None:
    """Fail closed when life-state metadata is incomplete or untrusted."""
    if str(meta.source) not in _SOURCE_KINDS:
        raise ValueError(f"invalid life state source: {meta.source!r}")
    if not str(meta.scope or "").strip():
        raise ValueError("life state scope is required")
    if str(meta.privacy) not in _PRIVACY_LEVELS:
        raise ValueError(f"invalid life state privacy: {meta.privacy!r}")
    if not str(meta.updated_at or "").strip():
        raise ValueError("life state updated_at is required")
    try:
        datetime.fromisoformat(str(meta.updated_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid life state updated_at: {meta.updated_at!r}") from exc
    if require_ttl:
        if not meta.decay_at:
            raise ValueError("life state decay_at (TTL) is required")
        try:
            datetime.fromisoformat(str(meta.decay_at).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid life state decay_at: {meta.decay_at!r}") from exc
    # Untrusted data (e.g. social evidence) cannot be relabeled as self/private.
    if (
        (str(meta.scope) == "self" or str(meta.privacy) == "private")
        and str(meta.source) not in _LIFE_STATE_TRUSTED_SOURCES
    ):
        raise ValueError(
            f"untrusted source {meta.source!r} cannot use scope=self "
            f"or privacy=private for life state"
        )


@dataclass(frozen=True, slots=True)
class LifeStateItem:
    key: str
    value: str
    meta: SourceMeta

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": self.value, "meta": self.meta.to_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LifeStateItem:
        meta_raw = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        return cls(
            key=str(data.get("key") or "").strip(),
            value=str(data.get("value") or "").strip(),
            meta=SourceMeta.from_dict(meta_raw),  # type: ignore[arg-type]
        )


@dataclass(slots=True)
class LifeState:
    """High-frequency mutable life snapshot (location, mood, energy, …)."""

    revision: int = 0
    items: dict[str, LifeStateItem] = field(default_factory=dict)
    updated_at: str = ""
    # Exact, non-expiring ledger of storylet/life side-effect event ids.
    # Item-level evidence_refs are not the idempotency truth.
    applied_event_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": max(0, int(self.revision)),
            "updated_at": self.updated_at,
            "items": {k: v.to_dict() for k, v in sorted(self.items.items())},
            "applied_event_ids": list(self.applied_event_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LifeState:
        raw_items_value = data.get("items")
        raw_items: Mapping[str, Any] = (
            raw_items_value if isinstance(raw_items_value, Mapping) else {}
        )
        items: dict[str, LifeStateItem] = {}
        for key, value in raw_items.items():
            if isinstance(value, dict):
                items[str(key)] = LifeStateItem.from_dict(value)
        raw_applied = data.get("applied_event_ids")
        applied: list[str] = []
        if isinstance(raw_applied, list):
            seen: set[str] = set()
            for item in raw_applied:
                text = str(item or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    applied.append(text)
        return cls(
            revision=max(0, int(data.get("revision") or 0)),
            items=items,
            updated_at=str(data.get("updated_at") or ""),
            applied_event_ids=applied,
        )


@dataclass(frozen=True, slots=True)
class SocialEvidenceRef:
    """Scoped reference into the existing Social Narrative factual store."""

    experience_id: str
    group_id: str
    user_id: str
    evidence_message_id: str
    evidence_time: str
    summary: str
    privacy: PrivacyLevel = "group"
    confidence: Confidence = "high"

    def __post_init__(self) -> None:
        if not str(self.experience_id or "").strip():
            raise ValueError("experience_id required")
        if not str(self.group_id or "").strip():
            raise ValueError("group_id required for social evidence")
        if not str(self.user_id or "").strip():
            raise ValueError("user_id required for social evidence")
        if not str(self.evidence_message_id or "").strip():
            raise ValueError("evidence_message_id required; fail-closed")
        if not str(self.evidence_time or "").strip():
            raise ValueError("evidence_time required for social evidence")
        try:
            datetime.fromisoformat(
                str(self.evidence_time).strip().replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                f"invalid evidence_time: {self.evidence_time!r}"
            ) from exc
        if not str(self.summary or "").strip():
            raise ValueError("summary required for social evidence")
        if str(self.privacy) not in _PRIVACY_LEVELS:
            raise ValueError(f"invalid privacy: {self.privacy!r}")
        if str(self.confidence) not in _CONFIDENCE_LEVELS:
            raise ValueError(f"invalid confidence: {self.confidence!r}")
        object.__setattr__(self, "experience_id", str(self.experience_id).strip())
        object.__setattr__(self, "group_id", str(self.group_id).strip())
        object.__setattr__(self, "user_id", str(self.user_id).strip())
        object.__setattr__(
            self, "evidence_message_id", str(self.evidence_message_id).strip()
        )
        object.__setattr__(self, "evidence_time", str(self.evidence_time).strip())
        object.__setattr__(self, "summary", str(self.summary).strip())

    def evidence_ref(self) -> str:
        return f"social:{self.group_id}:{self.evidence_message_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "evidence_message_id": self.evidence_message_id,
            "evidence_time": self.evidence_time,
            "summary": self.summary,
            "privacy": self.privacy,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SocialEvidenceRef:
        return cls(
            experience_id=str(data.get("experience_id") or ""),
            group_id=str(data.get("group_id") or ""),
            user_id=str(data.get("user_id") or ""),
            evidence_message_id=str(
                data.get("evidence_message_id") or data.get("evidence_ref") or ""
            ),
            evidence_time=str(data.get("evidence_time") or ""),
            summary=str(data.get("summary") or data.get("user_text") or ""),
            privacy=str(data.get("privacy") or "group"),  # type: ignore[arg-type]
            confidence=str(data.get("confidence") or "high"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class StoryLedgerView:
    """Stable main + side + ambient view over StoryArcStore (no second ledger)."""

    main: Mapping[str, Any] | None
    sides: tuple[Mapping[str, Any], ...]
    ambient: tuple[Mapping[str, Any], ...]
    ordering: tuple[str, ...]

    def all_arc_ids(self) -> tuple[str, ...]:
        ids: list[str] = []
        if self.main is not None:
            aid = str(self.main.get("arc_id") or "")
            if aid:
                ids.append(aid)
        for arc in self.sides:
            aid = str(arc.get("arc_id") or "")
            if aid:
                ids.append(aid)
        for arc in self.ambient:
            aid = str(arc.get("arc_id") or "")
            if aid:
                ids.append(aid)
        return tuple(ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "main": dict(self.main) if self.main is not None else None,
            "sides": [dict(a) for a in self.sides],
            "ambient": [dict(a) for a in self.ambient],
            "ordering": list(self.ordering),
        }


@dataclass(frozen=True, slots=True)
class EventRecord:
    """Committed story event applied by the reducer."""

    event_id: str
    event_type: str
    summary: str
    status: EventStatus = "committed"
    arc_id: str = ""
    variable_deltas: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    consequences: tuple[str, ...] = ()
    recovery_steps: int = 0
    severity: str = "daily"
    source: SourceKind = "event_reducer"
    evidence_refs: tuple[str, ...] = ()
    committed_at: str = ""
    step: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_id(self.event_id, "event_id"))
        object.__setattr__(self, "summary", str(self.summary or "").strip())
        object.__setattr__(
            self, "variable_deltas", _freeze_mapping(dict(self.variable_deltas))
        )
        object.__setattr__(
            self,
            "consequences",
            tuple(str(c).strip() for c in self.consequences if str(c).strip()),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(str(e).strip() for e in self.evidence_refs if str(e).strip()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "summary": self.summary,
            "status": self.status,
            "arc_id": self.arc_id,
            "variable_deltas": dict(self.variable_deltas),
            "consequences": list(self.consequences),
            "recovery_steps": int(self.recovery_steps),
            "severity": self.severity,
            "source": self.source,
            "evidence_refs": list(self.evidence_refs),
            "committed_at": self.committed_at,
            "step": int(self.step),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EventRecord:
        deltas = data.get("variable_deltas")
        return cls(
            event_id=str(data.get("event_id") or ""),
            event_type=str(data.get("event_type") or "daily"),
            summary=str(data.get("summary") or ""),
            status=str(data.get("status") or "committed"),  # type: ignore[arg-type]
            arc_id=str(data.get("arc_id") or ""),
            variable_deltas=dict(deltas) if isinstance(deltas, dict) else {},
            consequences=tuple(_list_str(data.get("consequences"))),
            recovery_steps=max(0, int(data.get("recovery_steps") or 0)),
            severity=str(data.get("severity") or "daily"),
            source=str(data.get("source") or "event_reducer"),  # type: ignore[arg-type]
            evidence_refs=tuple(_list_str(data.get("evidence_refs"))),
            committed_at=str(data.get("committed_at") or ""),
            step=max(0, int(data.get("step") or 0)),
        )


@dataclass(frozen=True, slots=True)
class EventProposal:
    """Dream / reflection proposal — never auto-promoted to factual or Canon."""

    proposal_id: str
    kind: Literal["arc_replan", "reflection", "fiction_event"]
    summary: str
    status: EventStatus = "proposal"
    arc_id: str = ""
    payload: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    created_at: str = ""
    source: SourceKind = "dream_proposal"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "proposal_id", _require_id(self.proposal_id, "proposal_id")
        )
        object.__setattr__(self, "summary", str(self.summary or "").strip())
        if not self.summary:
            raise ValueError("proposal summary must be non-empty")
        object.__setattr__(self, "payload", _freeze_mapping(dict(self.payload)))
        # Hard guard: proposals may never claim persona/native canon writes.
        kind = str(self.kind)
        if kind not in {"arc_replan", "reflection", "fiction_event"}:
            raise ValueError(f"invalid proposal kind: {self.kind!r}")
        # Persistence boundary: only proposal status may exist on this type.
        status = str(self.status or "").strip()
        if status != "proposal":
            raise ValueError(
                f"EventProposal status must be 'proposal', got {self.status!r}"
            )
        object.__setattr__(self, "status", "proposal")
        source = str(self.source or "").strip()
        if source not in {"dream_proposal", "system"}:
            raise ValueError(
                f"EventProposal source must be dream_proposal/system, got {self.source!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "kind": self.kind,
            "summary": self.summary,
            "status": self.status,
            "arc_id": self.arc_id,
            "payload": dict(self.payload),
            "created_at": self.created_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EventProposal:
        payload = data.get("payload")
        return cls(
            proposal_id=str(data.get("proposal_id") or data.get("id") or ""),
            kind=str(data.get("kind") or "reflection"),  # type: ignore[arg-type]
            summary=str(data.get("summary") or ""),
            status=str(data.get("status") or "proposal"),  # type: ignore[arg-type]
            arc_id=str(data.get("arc_id") or ""),
            payload=dict(payload) if isinstance(payload, dict) else {},
            created_at=str(data.get("created_at") or ""),
            source=str(data.get("source") or "dream_proposal"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class Storylet:
    """Structured candidate event (not free-form prompt text)."""

    storylet_id: str
    title: str
    text: str
    conditions: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    saliency: float = 1.0
    severity: str = "daily"
    once: bool = False
    cooldown_steps: int = 0
    delay_steps: int = 0
    cost: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    consequence: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    recovery_steps: int = 0
    required_evidence: tuple[str, ...] = ()
    scope: str = "fiction"
    priority: int = 100
    # Authored packs require this; direct programmatic Storylets may leave empty
    # and fall back to the live main arc at projection time.
    target_arc_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "storylet_id", _require_id(self.storylet_id, "storylet_id")
        )
        object.__setattr__(self, "title", str(self.title or self.storylet_id).strip())
        object.__setattr__(self, "text", str(self.text or "").strip())
        if not self.text:
            raise ValueError("storylet text must be non-empty")
        severity = str(self.severity or "").strip().lower()
        if severity not in _STORYLET_SEVERITIES:
            raise ValueError(f"invalid storylet severity: {self.severity!r}")
        object.__setattr__(self, "severity", severity)
        if str(self.scope or "").strip() != "fiction":
            raise ValueError("storylet scope must be fiction")
        object.__setattr__(self, "scope", "fiction")
        for field_name in ("cooldown_steps", "delay_steps", "recovery_steps"):
            value = int(getattr(self, field_name))
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "conditions", _freeze_mapping(dict(self.conditions)))
        object.__setattr__(self, "cost", _freeze_mapping(dict(self.cost)))
        object.__setattr__(
            self, "consequence", _freeze_mapping(dict(self.consequence))
        )
        object.__setattr__(
            self,
            "required_evidence",
            tuple(str(e).strip() for e in self.required_evidence if str(e).strip()),
        )
        sal = float(self.saliency)
        if not (0.0 <= sal <= 10.0):
            raise ValueError(f"invalid saliency: {self.saliency!r}")
        object.__setattr__(self, "saliency", sal)
        target = str(self.target_arc_id or "").strip()
        if target:
            target = _require_id(target, "target_arc_id")
        object.__setattr__(self, "target_arc_id", target)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "storylet_id": self.storylet_id,
            "title": self.title,
            "text": self.text,
            "conditions": dict(self.conditions),
            "saliency": float(self.saliency),
            "severity": self.severity,
            "once": bool(self.once),
            "cooldown_steps": int(self.cooldown_steps),
            "delay_steps": int(self.delay_steps),
            "cost": dict(self.cost),
            "consequence": dict(self.consequence),
            "recovery_steps": int(self.recovery_steps),
            "required_evidence": list(self.required_evidence),
            "scope": self.scope,
            "priority": int(self.priority),
        }
        if self.target_arc_id:
            payload["target_arc_id"] = self.target_arc_id
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Storylet:
        saliency_value = data.get("saliency")
        return cls(
            storylet_id=str(data.get("storylet_id") or data.get("id") or ""),
            title=str(data.get("title") or ""),
            text=str(data.get("text") or data.get("content") or ""),
            conditions=dict(data["conditions"])
            if isinstance(data.get("conditions"), dict)
            else {},
            saliency=float(saliency_value if saliency_value is not None else 1.0),
            severity=str(data.get("severity") or "daily"),
            once=bool(data.get("once") or data.get("once_only") or False),
            cooldown_steps=max(0, int(data.get("cooldown_steps") or 0)),
            delay_steps=max(0, int(data.get("delay_steps") or 0)),
            cost=dict(data["cost"]) if isinstance(data.get("cost"), dict) else {},
            consequence=dict(data["consequence"])
            if isinstance(data.get("consequence"), dict)
            else {},
            recovery_steps=max(0, int(data.get("recovery_steps") or 0)),
            required_evidence=tuple(_list_str(data.get("required_evidence"))),
            scope=str(data.get("scope") or "fiction"),
            priority=int(data.get("priority") or 100),
            target_arc_id=str(data.get("target_arc_id") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class ProjectionTrace:
    source: SourceKind
    scope: str
    hit_reason: str
    evidence_refs: tuple[str, ...]
    budget_decision: str
    priority: int = 100
    label: str = ""
    char_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "scope": self.scope,
            "hit_reason": self.hit_reason,
            "evidence_refs": list(self.evidence_refs),
            "budget_decision": self.budget_decision,
            "priority": int(self.priority),
            "label": self.label,
            "char_count": int(self.char_count),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ProjectionBlock:
    block_id: str
    label: str
    text: str
    meta: SourceMeta
    atomic: bool = True
    char_count: int = 0

    def __post_init__(self) -> None:
        text = str(self.text or "")
        object.__setattr__(self, "text", text)
        count = int(self.char_count) if self.char_count else len(text)
        object.__setattr__(self, "char_count", count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "label": self.label,
            "text": self.text,
            "meta": self.meta.to_dict(),
            "atomic": bool(self.atomic),
            "char_count": int(self.char_count),
        }


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


# ---------------------------------------------------------------------------
# Typed Storylet effects (closed set; unknown keys fail closed)
# ---------------------------------------------------------------------------

TYPED_STORYLET_EFFECT_KEYS = frozenset({
    "variable_deltas",
    "open_threads",
    "resolve_threads",
    "stage",
    "partner_updates",
    "life_updates",
})

# Authored storylets may only set these nested fields; runtime writes fixed
# source/scope/confidence/privacy for life items and refuses partner card
# identity fields (display_name, pinned_profile, kind, applied_event_ids, …).
LIFE_UPDATE_ALLOWED_KEYS = frozenset({
    "key",
    "value",
    "ttl_hours",
    "ttl_seconds",
})
PARTNER_UPDATE_ALLOWED_KEYS = frozenset({
    "entity_id",
    "mood",
    "availability",
    "current_state",
    "constraints",
    "note",
    "event_note",
})

StoryletCommitStatus = Literal["committed", "rejected", "already_committed"]


@dataclass(frozen=True, slots=True)
class TypedStoryletEffects:
    """Validated closed-set cost/consequence effects for a Storylet commit."""

    variable_deltas: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    open_threads: tuple[str, ...] = ()
    resolve_threads: tuple[str, ...] = ()
    stage: str | None = None
    partner_updates: tuple[Mapping[str, Any], ...] = ()
    life_updates: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "variable_deltas",
            _freeze_mapping(dict(self.variable_deltas)),
        )
        object.__setattr__(
            self,
            "open_threads",
            tuple(str(x).strip() for x in self.open_threads if str(x).strip()),
        )
        object.__setattr__(
            self,
            "resolve_threads",
            tuple(str(x).strip() for x in self.resolve_threads if str(x).strip()),
        )
        stage = str(self.stage).strip() if self.stage is not None else None
        object.__setattr__(self, "stage", stage or None)
        partners = tuple(
            _freeze_mapping(dict(item))
            for item in self.partner_updates
            if isinstance(item, Mapping)
        )
        object.__setattr__(self, "partner_updates", partners)
        lives = tuple(
            _freeze_mapping(dict(item))
            for item in self.life_updates
            if isinstance(item, Mapping)
        )
        object.__setattr__(self, "life_updates", lives)

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable_deltas": dict(self.variable_deltas),
            "open_threads": list(self.open_threads),
            "resolve_threads": list(self.resolve_threads),
            "stage": self.stage,
            "partner_updates": [dict(p) for p in self.partner_updates],
            "life_updates": [dict(u) for u in self.life_updates],
        }


@dataclass(frozen=True, slots=True)
class StoryletCommitResult:
    """Structured result of a formal Storylet atomic commit attempt."""

    status: StoryletCommitStatus
    storylet_id: str
    event_id: str = ""
    reason: str = ""
    arc_id: str = ""
    event: EventRecord | None = None
    effects: TypedStoryletEffects | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"committed", "already_committed"}

    @property
    def projected(self) -> bool:
        """Only freshly committed storylets enter Prompt (not rejections)."""
        return self.status == "committed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "storylet_id": self.storylet_id,
            "event_id": self.event_id,
            "reason": self.reason,
            "arc_id": self.arc_id,
            "event": self.event.to_dict() if self.event is not None else None,
            "effects": self.effects.to_dict() if self.effects is not None else None,
        }


# ---------------------------------------------------------------------------
# Social → story bridge (bounded, Arc-scoped, raw-text-free)
# ---------------------------------------------------------------------------

SocialStoryCommitStatus = Literal["committed", "already_committed", "rejected"]

# Fixed generic Chinese summary — never derived from user_text / bot_reply.
SOCIAL_INFLUENCE_SUMMARY = "日常社交互动留下了温和共鸣"
# Closed self-state Life key/value (generic, no person names or raw chat).
SOCIAL_LIFE_KEY = "mood.social_afterglow"
SOCIAL_LIFE_VALUE = "近日有温和的社交共鸣"
SOCIAL_LIFE_TTL_HOURS = 24.0
SOCIAL_RESONANCE_DELTA = 0.05
SOCIAL_RESONANCE_MIN = 0.0
SOCIAL_RESONANCE_MAX = 1.0


@dataclass(frozen=True, slots=True)
class SocialStoryCommitResult:
    """Structured result of a social→story atomic commit attempt.

    Observation fields are BlockTrace-compatible metadata only: no raw chat
    text, person names, or private content. Callers may surface this via the
    existing BlockTraceStore / admin API without inventing a second telemetry DB.
    """

    status: SocialStoryCommitStatus
    reason: str = ""
    event_id: str = ""
    arc_id: str = ""
    evidence_ref: str = ""
    experience_id: str = ""
    event: EventRecord | None = None
    observation: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observation", _freeze_mapping(dict(self.observation))
        )

    @property
    def ok(self) -> bool:
        return self.status in {"committed", "already_committed"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "event_id": self.event_id,
            "arc_id": self.arc_id,
            "evidence_ref": self.evidence_ref,
            "experience_id": self.experience_id,
            "event": self.event.to_dict() if self.event is not None else None,
            "observation": dict(self.observation),
        }


def deterministic_social_story_event_id(
    *,
    experience_id: str,
    group_id: str,
    user_id: str,
    evidence_message_id: str,
    target_arc_id: str,
) -> str:
    """Stable Arc-scoped social influence event id (replay-safe, bounded).

    Material includes experience, group, user, evidence message, and target
    main Arc so the same factual record cannot collide across arcs. When the
    readable form exceeds the domain id limit, a digest form is used.
    """
    exp = str(experience_id or "").strip()
    gid = str(group_id or "").strip()
    uid = str(user_id or "").strip()
    mid = str(evidence_message_id or "").strip()
    aid = str(target_arc_id or "").strip()
    if not exp or not gid or not uid or not mid or not aid:
        raise ValueError(
            "experience_id, group_id, user_id, evidence_message_id, "
            "and target_arc_id are required"
        )
    # IDs must match domain grammar; digest absorbs free-form social ids.
    safe_arc = _require_id(aid, "target_arc_id")
    material = f"{exp}\0{gid}\0{uid}\0{mid}\0{safe_arc}".encode()
    digest = hashlib.sha256(material).hexdigest()[:40]
    # Prefer readable form when all components are domain-safe and short.
    try:
        safe_exp = _require_id(exp, "experience_id")
        safe_gid = _require_id(gid, "group_id")
        safe_uid = _require_id(uid, "user_id")
        safe_mid = _require_id(mid, "evidence_message_id")
        readable = (
            f"social_influence.{safe_arc}.{safe_exp}."
            f"{safe_gid}.{safe_uid}.{safe_mid}"
        )
        if len(readable) <= 120 and _ID_RE.fullmatch(readable) is not None:
            return readable
    except ValueError:
        pass
    return f"social_influence.h.{digest}"


def _reject_unknown_effect_keys(
    payload: Mapping[str, Any],
    *,
    field_name: str,
) -> None:
    unknown = sorted(
        str(k) for k in payload if str(k) not in TYPED_STORYLET_EFFECT_KEYS
    )
    if unknown:
        raise ValueError(
            f"unknown typed effect key(s) in {field_name}: {','.join(unknown)}"
        )


def _coerce_str_list(value: object, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _coerce_mapping_list(value: object, *, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of objects")
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"{field_name} items must be objects")
        out.append(dict(item))
    return out


def _merge_variable_deltas(
    base: dict[str, Any],
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    out = dict(base)
    for key, delta in extra.items():
        key_s = str(key)
        if key_s not in out:
            out[key_s] = delta
            continue
        current = out[key_s]
        if (
            isinstance(delta, (int, float))
            and not isinstance(delta, bool)
            and isinstance(current, (int, float))
            and not isinstance(current, bool)
        ):
            out[key_s] = float(current) + float(delta)
        else:
            # Non-numeric overwrite is last-writer (consequence wins when merged last).
            out[key_s] = delta
    return out


def parse_typed_storylet_effects(
    cost: Mapping[str, Any] | None,
    consequence: Mapping[str, Any] | None,
) -> TypedStoryletEffects:
    """Parse and validate Storylet cost/consequence against the closed typed set.

    Unknown top-level keys fail closed. Cost and consequence are merged;
    numeric ``variable_deltas`` sum; lists of threads concatenate (deduped order).
    """
    cost_map = dict(cost or {})
    cons_map = dict(consequence or {})
    _reject_unknown_effect_keys(cost_map, field_name="cost")
    _reject_unknown_effect_keys(cons_map, field_name="consequence")

    deltas: dict[str, Any] = {}
    open_threads: list[str] = []
    resolve_threads: list[str] = []
    stage: str | None = None
    partner_updates: list[dict[str, Any]] = []
    life_updates: list[dict[str, Any]] = []

    for field_name, payload in (("cost", cost_map), ("consequence", cons_map)):
        raw_deltas = payload.get("variable_deltas")
        if raw_deltas is not None:
            if not isinstance(raw_deltas, Mapping):
                raise ValueError(f"{field_name}.variable_deltas must be an object")
            deltas = _merge_variable_deltas(deltas, raw_deltas)
        open_threads.extend(
            _coerce_str_list(payload.get("open_threads"), field_name=f"{field_name}.open_threads")
        )
        resolve_threads.extend(
            _coerce_str_list(
                payload.get("resolve_threads"),
                field_name=f"{field_name}.resolve_threads",
            )
        )
        if "stage" in payload and payload.get("stage") is not None:
            stage_text = str(payload.get("stage") or "").strip()
            if not stage_text:
                raise ValueError(f"{field_name}.stage must be non-empty when set")
            stage = stage_text
        partner_updates.extend(
            _coerce_mapping_list(
                payload.get("partner_updates"),
                field_name=f"{field_name}.partner_updates",
            )
        )
        life_updates.extend(
            _coerce_mapping_list(
                payload.get("life_updates"),
                field_name=f"{field_name}.life_updates",
            )
        )

    # Validate + strip nested life_updates (closed key set; finite TTL required).
    cleaned_life: list[dict[str, Any]] = []
    for item in life_updates:
        unknown = sorted(
            str(k) for k in item if str(k) not in LIFE_UPDATE_ALLOWED_KEYS
        )
        if unknown:
            raise ValueError(
                f"unknown life_updates field(s): {','.join(unknown)}"
            )
        key = str(item.get("key") or "").strip()
        if not key:
            raise ValueError("life_updates[].key is required")
        if "value" not in item:
            raise ValueError(f"life_updates[{key!r}].value is required")
        ttl_hours = item.get("ttl_hours", item.get("ttl_seconds"))
        if ttl_hours is None:
            raise ValueError(
                f"life_updates[{key!r}] requires finite ttl_hours (or ttl_seconds)"
            )
        try:
            ttl_val = float(ttl_hours)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"life_updates[{key!r}] ttl must be numeric"
            ) from exc
        if ttl_val <= 0:
            raise ValueError(f"life_updates[{key!r}] ttl must be positive")
        cleaned: dict[str, Any] = {"key": key, "value": item.get("value")}
        if "ttl_hours" in item:
            cleaned["ttl_hours"] = item["ttl_hours"]
        if "ttl_seconds" in item:
            cleaned["ttl_seconds"] = item["ttl_seconds"]
        cleaned_life.append(cleaned)

    cleaned_partners: list[dict[str, Any]] = []
    for item in partner_updates:
        unknown = sorted(
            str(k) for k in item if str(k) not in PARTNER_UPDATE_ALLOWED_KEYS
        )
        if unknown:
            raise ValueError(
                f"unknown partner_updates field(s): {','.join(unknown)}"
            )
        entity_id = str(item.get("entity_id") or "").strip()
        if not entity_id:
            raise ValueError("partner_updates[].entity_id is required")
        cleaned_p: dict[str, Any] = {"entity_id": entity_id}
        for key in PARTNER_UPDATE_ALLOWED_KEYS:
            if key == "entity_id" or key not in item:
                continue
            cleaned_p[key] = item[key]
        cleaned_partners.append(cleaned_p)

    # Dedupe threads preserving order.
    def _dedupe(items: list[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for text in items:
            if text not in seen:
                seen.add(text)
                out.append(text)
        return tuple(out)

    return TypedStoryletEffects(
        variable_deltas=deltas,
        open_threads=_dedupe(open_threads),
        resolve_threads=_dedupe(resolve_threads),
        stage=stage,
        partner_updates=tuple(cleaned_partners),
        life_updates=tuple(cleaned_life),
    )


def deterministic_storylet_event_id(
    storylet_id: str,
    step: int,
    arc_id: str = "",
) -> str:
    """Stable event identity for Storylet commits (replay-safe, arc-scoped).

    Committed IDs always include ``arc_id`` so the same storylet+step on two
    arcs cannot collide. ``arc_id`` may be omitted only for legacy helpers;
    production commit paths must pass a non-empty arc id.

    When the readable form would exceed the domain id limit (120 chars), a
    stable digest form is used so max-length arc/storylet ids remain valid.
    """
    sid = _require_id(storylet_id, "storylet_id")
    step_i = max(0, int(step))
    aid = str(arc_id or "").strip()
    if aid:
        safe_arc = _require_id(aid, "arc_id")
        readable = f"storylet.{safe_arc}.{sid}.s{step_i}"
    else:
        safe_arc = ""
        readable = f"storylet.{sid}.s{step_i}"
    if len(readable) <= 120 and _ID_RE.fullmatch(readable) is not None:
        return readable
    material = f"{safe_arc}\0{sid}\0{step_i}".encode()
    digest = hashlib.sha256(material).hexdigest()[:40]
    # storylet.h.<40 hex> stays well under 120 and matches id grammar.
    return f"storylet.h.{digest}"


# ---------------------------------------------------------------------------
# Dream proposal lifecycle (immutable decisions + commit records)
# ---------------------------------------------------------------------------

DecisionStatus = Literal["validated", "rejected"]
CommitStatus = Literal["committed"]

# Closed Dream replan / fiction payload keys (plus typed effect keys).
DREAM_REPLAN_PAYLOAD_KEYS = frozenset({
    "last_event_summary",
    "open_threads",
    "next_day_seed",
    "effects",
    "consequence",
    "cost",
    "severity",
    "recovery_steps",
    "reason",
}) | TYPED_STORYLET_EFFECT_KEYS

# Metadata / control keys that must never appear (normalized form).
FORBIDDEN_PROPOSAL_KEY_NORMALIZED = frozenset({
    "personacanon",
    "nativecanon",
    "canonwrite",
    "factual",
    "factualcommit",
    "socialfact",
    "userid",
    "groupid",
    "qq",
    "messageid",
    "usertext",
    "botreply",
    "experienceid",
    "evidencemessageid",
})

# Control field → forbidden values (normalized).
FORBIDDEN_CONTROL_VALUES: Mapping[str, frozenset[str]] = MappingProxyType({
    "subjectkind": frozenset({"factual"}),
    "kind": frozenset({"factual"}),
    "source": frozenset({"human", "user", "socialevidence"}),
})


def normalize_control_token(value: object) -> str:
    """Normalize keys/control values for closed forbidden matching."""
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def find_forbidden_proposal_marker(value: Any, *, path: str = "payload") -> str | None:
    """Return a human reason if value smuggles factual/Canon/social-human control data.

    Scans keys and control-field values only — does not scan free prose for
    ordinary words.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_norm = normalize_control_token(key)
            if key_norm in FORBIDDEN_PROPOSAL_KEY_NORMALIZED:
                return f"forbidden_key:{key_norm}@{path}"
            if key_norm in FORBIDDEN_CONTROL_VALUES:
                val_norm = normalize_control_token(item)
                if val_norm in FORBIDDEN_CONTROL_VALUES[key_norm]:
                    return f"forbidden_control:{key_norm}={val_norm}@{path}"
            nested = find_forbidden_proposal_marker(
                item, path=f"{path}.{key_norm or 'item'}"
            )
            if nested is not None:
                return nested
        return None
    if isinstance(value, (list, tuple, set)):
        for index, item in enumerate(value):
            nested = find_forbidden_proposal_marker(item, path=f"{path}[{index}]")
            if nested is not None:
                return nested
    return None


def _stable_id(prefix: str, *parts: str, max_len: int = 120) -> str:
    cleaned = [str(p or "").strip() for p in parts if str(p or "").strip()]
    readable = ".".join([prefix, *cleaned]) if cleaned else prefix
    if len(readable) <= max_len and _ID_RE.fullmatch(readable) is not None:
        return readable
    material = "\0".join([prefix, *cleaned]).encode()
    digest = hashlib.sha256(material).hexdigest()[:40]
    hashed = f"{prefix}.h.{digest}"
    if len(hashed) > max_len or _ID_RE.fullmatch(hashed) is None:
        raise ValueError(f"cannot form stable id for prefix={prefix!r}")
    return hashed


def deterministic_decision_id(proposal_id: str) -> str:
    pid = _require_id(proposal_id, "proposal_id")
    return _stable_id("decision", pid)


def deterministic_commit_id(proposal_id: str, decision_id: str) -> str:
    pid = _require_id(proposal_id, "proposal_id")
    did = _require_id(decision_id, "decision_id")
    return _stable_id("commit", pid, did)


def deterministic_dream_event_id(proposal_id: str, arc_id: str) -> str:
    """Arc-scoped event id for a Dream proposal commit (domain id limit safe)."""
    pid = _require_id(proposal_id, "proposal_id")
    aid = _require_id(arc_id, "arc_id")
    return _stable_id("dream", aid, pid)


def proposal_semantic_fields(proposal: EventProposal | Mapping[str, Any]) -> dict[str, Any]:
    """Semantic identity fields for a proposal (excludes created_at).

    Used for ProposalStore immutability/idempotency only. Decision fingerprints
    must use proposal_content_fingerprint, which also binds created_at.
    """
    if isinstance(proposal, EventProposal):
        payload = _json_safe(dict(proposal.payload))
        return {
            "proposal_id": str(proposal.proposal_id),
            "kind": str(proposal.kind),
            "summary": str(proposal.summary),
            "status": str(proposal.status),
            "arc_id": str(proposal.arc_id or ""),
            "payload": payload if isinstance(payload, dict) else {},
            "source": str(proposal.source),
        }
    payload_raw = proposal.get("payload")
    payload = (
        _json_safe(dict(payload_raw))
        if isinstance(payload_raw, Mapping)
        else {}
    )
    return {
        "proposal_id": str(proposal.get("proposal_id") or ""),
        "kind": str(proposal.get("kind") or ""),
        "summary": str(proposal.get("summary") or ""),
        "status": str(proposal.get("status") or "proposal"),
        "arc_id": str(proposal.get("arc_id") or ""),
        "payload": payload if isinstance(payload, dict) else {},
        "source": str(proposal.get("source") or "dream_proposal"),
    }


def _json_safe(value: Any) -> Any:
    """Recursively convert mappings/tuples into JSON-friendly plain types."""
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return value


def canonical_proposal_json(value: Any) -> str:
    """Deterministic JSON for proposal fingerprinting (stable key order)."""
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def proposal_content_fingerprint(proposal: EventProposal | Mapping[str, Any]) -> str:
    """SHA-256 of the full persisted proposal identity including created_at.

    Semantic resubmit equivalence still ignores created_at via
    proposal_semantic_fields; validated decisions bind the originally
    persisted timestamp so post-validation created_at tampering fails closed.
    """
    material = dict(proposal_semantic_fields(proposal))
    if isinstance(proposal, EventProposal):
        material["created_at"] = str(proposal.created_at or "")
    else:
        material["created_at"] = str(proposal.get("created_at") or "")
    raw = canonical_proposal_json(material)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProposalDecision:
    """Immutable validate/reject decision for a Dream EventProposal.

    Never mutates the proposal JSON. Status is only validated|rejected.
    """

    decision_id: str
    proposal_id: str
    status: DecisionStatus
    reason_code: str = ""
    reason: str = ""
    arc_id: str = ""
    normalized_payload: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    effects: TypedStoryletEffects | None = None
    decided_at: str = ""
    proposal_fingerprint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "decision_id", _require_id(self.decision_id, "decision_id")
        )
        object.__setattr__(
            self, "proposal_id", _require_id(self.proposal_id, "proposal_id")
        )
        status = str(self.status or "").strip()
        if status not in {"validated", "rejected"}:
            raise ValueError(
                f"ProposalDecision status must be validated|rejected, got {self.status!r}"
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self, "reason_code", str(self.reason_code or "").strip()
        )
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        arc = str(self.arc_id or "").strip()
        if arc:
            arc = _require_id(arc, "arc_id")
        object.__setattr__(self, "arc_id", arc)
        object.__setattr__(
            self,
            "normalized_payload",
            _freeze_mapping(dict(self.normalized_payload or {})),
        )
        object.__setattr__(
            self,
            "proposal_fingerprint",
            str(self.proposal_fingerprint or "").strip(),
        )
        if not self.decided_at:
            object.__setattr__(self, "decided_at", utc_now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "proposal_id": self.proposal_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "arc_id": self.arc_id,
            "normalized_payload": dict(self.normalized_payload),
            "effects": self.effects.to_dict() if self.effects is not None else None,
            "decided_at": self.decided_at,
            "proposal_fingerprint": self.proposal_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProposalDecision:
        effects_raw = data.get("effects")
        effects: TypedStoryletEffects | None = None
        if isinstance(effects_raw, Mapping):
            effects = TypedStoryletEffects(
                variable_deltas=dict(effects_raw.get("variable_deltas") or {}),
                open_threads=tuple(_list_str(effects_raw.get("open_threads"))),
                resolve_threads=tuple(_list_str(effects_raw.get("resolve_threads"))),
                stage=(
                    str(effects_raw.get("stage")).strip()
                    if effects_raw.get("stage") is not None
                    else None
                ),
                partner_updates=tuple(
                    dict(item)
                    for item in (effects_raw.get("partner_updates") or [])
                    if isinstance(item, Mapping)
                ),
                life_updates=tuple(
                    dict(item)
                    for item in (effects_raw.get("life_updates") or [])
                    if isinstance(item, Mapping)
                ),
            )
        payload = data.get("normalized_payload")
        return cls(
            decision_id=str(data.get("decision_id") or ""),
            proposal_id=str(data.get("proposal_id") or ""),
            status=str(data.get("status") or "rejected"),  # type: ignore[arg-type]
            reason_code=str(data.get("reason_code") or ""),
            reason=str(data.get("reason") or ""),
            arc_id=str(data.get("arc_id") or ""),
            normalized_payload=dict(payload) if isinstance(payload, Mapping) else {},
            effects=effects,
            decided_at=str(data.get("decided_at") or ""),
            proposal_fingerprint=str(data.get("proposal_fingerprint") or ""),
        )


@dataclass(frozen=True, slots=True)
class FictionCommitRecord:
    """Immutable record that a validated Dream decision was committed to Arc."""

    commit_id: str
    proposal_id: str
    decision_id: str
    event_id: str
    arc_id: str
    status: CommitStatus = "committed"
    committed_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "commit_id", _require_id(self.commit_id, "commit_id")
        )
        object.__setattr__(
            self, "proposal_id", _require_id(self.proposal_id, "proposal_id")
        )
        object.__setattr__(
            self, "decision_id", _require_id(self.decision_id, "decision_id")
        )
        object.__setattr__(self, "event_id", _require_id(self.event_id, "event_id"))
        object.__setattr__(self, "arc_id", _require_id(self.arc_id, "arc_id"))
        status = str(self.status or "").strip()
        if status != "committed":
            raise ValueError(
                f"FictionCommitRecord status must be 'committed', got {self.status!r}"
            )
        object.__setattr__(self, "status", "committed")
        if not self.committed_at:
            object.__setattr__(self, "committed_at", utc_now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_id": self.commit_id,
            "proposal_id": self.proposal_id,
            "decision_id": self.decision_id,
            "event_id": self.event_id,
            "arc_id": self.arc_id,
            "status": self.status,
            "committed_at": self.committed_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FictionCommitRecord:
        return cls(
            commit_id=str(data.get("commit_id") or ""),
            proposal_id=str(data.get("proposal_id") or ""),
            decision_id=str(data.get("decision_id") or ""),
            event_id=str(data.get("event_id") or ""),
            arc_id=str(data.get("arc_id") or ""),
            status=str(data.get("status") or "committed"),  # type: ignore[arg-type]
            committed_at=str(data.get("committed_at") or ""),
        )


@dataclass(frozen=True, slots=True)
class ProposalProcessResult:
    """Structured outcome of validate and optional commit for one proposal."""

    proposal_id: str
    decision: ProposalDecision | None = None
    commit: FictionCommitRecord | None = None
    event: EventRecord | None = None

    @property
    def status(self) -> str:
        if self.commit is not None:
            return "committed"
        if self.decision is not None:
            return str(self.decision.status)
        return "missing"

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "status": self.status,
            "decision": self.decision.to_dict() if self.decision else None,
            "commit": self.commit.to_dict() if self.commit else None,
            "event": self.event.to_dict() if self.event else None,
        }


def _require_optional_mapping_bag(
    raw: Mapping[str, Any],
    key: str,
) -> Mapping[str, Any]:
    """Return mapping bag or empty when absent; reject non-mapping values."""
    if key not in raw:
        return {}
    value = raw[key]
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(
            f"invalid_effects: {key!r} must be an object/mapping, got {type(value).__name__}"
        )
    return value


def parse_dream_fiction_effects(
    payload: Mapping[str, Any] | None,
    *,
    kind: str,
) -> tuple[TypedStoryletEffects, dict[str, Any]]:
    """Validate Dream payload into typed fiction effects + normalized payload.

    Accepts legacy Dream replan fields and optional Storylet-compatible typed
    effects. Unknown top-level keys fail closed. Does not scan free prose.
    """
    raw = dict(payload or {})
    unknown = sorted(
        str(k)
        for k in raw
        if normalize_control_token(k)
        not in {normalize_control_token(x) for x in DREAM_REPLAN_PAYLOAD_KEYS}
    )
    if unknown:
        raise ValueError(f"unknown dream payload key(s): {','.join(unknown)}")

    banned = find_forbidden_proposal_marker(raw)
    if banned is not None:
        raise ValueError(banned)

    # Pull optional nested cost/consequence/effects bags.
    # Present-but-non-mapping values fail closed (invalid_effects); do not coerce.
    cost = _require_optional_mapping_bag(raw, "cost")
    consequence = _require_optional_mapping_bag(raw, "consequence")
    nested_effects = _require_optional_mapping_bag(raw, "effects")

    # Top-level typed keys merge into a synthetic consequence bag.
    top_effects: dict[str, Any] = {}
    for key in TYPED_STORYLET_EFFECT_KEYS:
        if key in raw:
            top_effects[key] = raw[key]

    # Dream replan open_threads feed fiction open threads when no typed list.
    replan_threads = raw.get("open_threads")
    if replan_threads is not None and "open_threads" not in top_effects:
        if not isinstance(replan_threads, list):
            raise ValueError("open_threads must be a list of strings")
        top_effects["open_threads"] = list(replan_threads)

    # Merge bags: cost → nested effects → consequence → top-level (last wins lists).
    merged_cost = dict(cost) if isinstance(cost, Mapping) else {}
    merged_cons: dict[str, Any] = {}
    for bag in (nested_effects, consequence, top_effects):
        if not isinstance(bag, Mapping):
            continue
        for key, value in bag.items():
            if key == "variable_deltas" and isinstance(value, Mapping):
                base = (
                    dict(merged_cons.get("variable_deltas") or {})
                    if isinstance(merged_cons.get("variable_deltas"), Mapping)
                    else {}
                )
                merged_cons["variable_deltas"] = _merge_variable_deltas(base, value)
            elif key in {
                "open_threads",
                "resolve_threads",
                "partner_updates",
                "life_updates",
            } and isinstance(value, list):
                existing = list(merged_cons.get(key) or [])
                existing.extend(value)
                merged_cons[key] = existing
            else:
                merged_cons[key] = value

    effects = parse_typed_storylet_effects(merged_cost, merged_cons)

    severity = str(raw.get("severity") or "daily").strip().lower() or "daily"
    if severity not in _STORYLET_SEVERITIES:
        raise ValueError(f"invalid fiction severity: {severity!r}")
    recovery_steps = max(0, int(raw.get("recovery_steps") or 0))

    last_summary = str(raw.get("last_event_summary") or "").strip()
    next_seed = str(raw.get("next_day_seed") or "").strip()
    normalized: dict[str, Any] = {
        "kind": str(kind),
        "last_event_summary": last_summary,
        "open_threads": list(effects.open_threads),
        "next_day_seed": next_seed,
        "severity": severity,
        "recovery_steps": recovery_steps,
        "effects": effects.to_dict(),
    }
    return effects, normalized
