"""Immutable evidence and promotion contracts for memory governance v1.

This module is deliberately pure. It defines the future source-of-truth
boundary without opening a database or changing any existing memory projection.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, Literal, overload

from services.memory.entity_identity import parse_entity_key
from services.memory.linked_refs import parse_linked_ref

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/+-]{0,239}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_WORLD_SUBJECT_RE = re.compile(
    r"^worldbook:(?:world|entity):[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$"
)
_BOT_SUBJECT_RE = re.compile(r"^bot:[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
_CARD_CATEGORIES = frozenset(
    {"preference", "boundary", "relationship", "event", "promise", "fact", "status"}
)
_EVIDENCE_KINDS = frozenset(
    {
        "entity",
        "card",
        "fact",
        "message_pk",
        "message",
        "episode",
        "candidate",
        "observation",
        "worldbook_event",
        "social_experience",
        "operator_record",
    }
)

class SourceKind(StrEnum):
    USER_STATEMENT = "user_statement"
    CONVERSATION_INFERENCE = "conversation_inference"
    SYSTEM_EVENT = "system_event"
    OPERATOR_ASSERTION = "operator_assertion"
    MIGRATION = "migration"


class ProducerKind(StrEnum):
    MEMO = "memo"
    COMPACTION = "compaction"
    CONSOLIDATOR = "consolidator"
    MANUAL = "manual"
    MIGRATION = "migration"


class OwnerScope(StrEnum):
    USER = "user"
    GROUP = "group"
    GLOBAL = "global"
    WORLD = "world"


class Visibility(StrEnum):
    PRIVATE = "private"
    SAME_GROUP = "same_group"
    GLOBAL = "global"


class TimeBasis(StrEnum):
    SOURCE_EVENT = "source_event"
    PRODUCER_CLOCK = "producer_clock"
    UNKNOWN = "unknown"


class ProjectionKind(StrEnum):
    CARD = "card"
    SLANG = "slang"
    STYLE = "style"
    EPISODE = "episode"
    GRAPH_RELATION = "graph_relation"


class Operation(StrEnum):
    CREATE = "create"
    REINFORCE = "reinforce"
    SUPERSEDE = "supersede"
    EXPIRE = "expire"


class ConflictKind(StrEnum):
    DUPLICATE = "duplicate"
    CONTRADICTION = "contradiction"
    AMBIGUOUS_SUPERSESSION = "ambiguous_supersession"
    SUBJECT_MISMATCH = "subject_mismatch"
    VISIBILITY_MISMATCH = "visibility_mismatch"
    TEMPORAL_OVERLAP = "temporal_overlap"


class ConflictDetector(StrEnum):
    DETERMINISTIC = "deterministic"
    POLICY = "policy"
    OPERATOR = "operator"


class PromotionKind(StrEnum):
    REVIEW_QUEUED = "review_queued"
    PROMOTION_APPROVED = "promotion_approved"
    PROMOTION_REJECTED = "promotion_rejected"
    CONFLICT_RESOLVED = "conflict_resolved"
    PROJECTION_APPLIED = "projection_applied"
    PROJECTION_FAILED = "projection_failed"


class ActorKind(StrEnum):
    POLICY = "policy"
    OPERATOR = "operator"
    MIGRATION = "migration"
    PROJECTOR = "projector"


class RepeatPolicy(StrEnum):
    UNDERSTAND_ONLY = "understand_only"
    REPEAT_ALLOWED = "repeat_allowed"
    NEVER_REPEAT = "never_repeat"


class GraphEdgeType(StrEnum):
    FACT = "fact"
    SOCIAL = "social"
    TEMPORAL = "temporal"
    CAUSAL = "causal"


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


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
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite numbers are not canonical JSON")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _bare_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _enum[T: StrEnum](enum_type: type[T], value: object, field_name: str) -> T:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc


def _clean_text(value: object, field_name: str, *, maximum: int = 2_000) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ValueError(f"{field_name} must be 1..{maximum} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValueError(f"{field_name} contains control characters")
    return text


def _optional_text(value: object, field_name: str, *, maximum: int = 2_000) -> str:
    if value is None or str(value).strip() == "":
        return ""
    return _clean_text(value, field_name, maximum=maximum)


def _evidence_quote(value: object, *, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise TypeError("quote must be a string")
    if not value.strip() or len(value) > maximum:
        raise ValueError(f"quote must be 1..{maximum} characters")
    if any(
        (ord(character) < 32 and character not in {"\t", "\n", "\r"})
        or ord(character) == 127
        for character in value
    ):
        raise ValueError("quote contains unsupported control characters")
    return value


def _safe_id(value: object, field_name: str, *, maximum: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) > maximum or _SAFE_ID_RE.fullmatch(text) is None:
        raise ValueError(f"invalid {field_name}: {value!r}")
    return text


@overload
def _utc_time(
    value: object,
    field_name: str,
    *,
    optional: Literal[False] = False,
) -> datetime: ...


@overload
def _utc_time(
    value: object,
    field_name: str,
    *,
    optional: Literal[True],
) -> datetime | None: ...


def _utc_time(
    value: object,
    field_name: str,
    *,
    optional: bool = False,
) -> datetime | None:
    if value is None or value == "":
        if optional:
            return None
        raise ValueError(f"{field_name} is required")
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


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("confidence must be a finite number")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return 0.0 if result == 0.0 else result


def _canonical_subject(value: object) -> str:
    text = str(value or "").strip()
    parsed = parse_entity_key(text)
    if parsed is not None:
        return parsed.entity_key
    if _WORLD_SUBJECT_RE.fullmatch(text) or _BOT_SUBJECT_RE.fullmatch(text):
        return text
    raise ValueError(f"invalid canonical subject_ref: {value!r}")


def _canonical_group_ref(value: object) -> str:
    canonical = _canonical_subject(value)
    if not canonical.startswith("group:qq:"):
        raise ValueError("origin_group_ref must be a canonical QQ group ref")
    return canonical


def _canonical_owner(scope: OwnerScope, value: object) -> str:
    text = str(value or "").strip()
    if scope in {OwnerScope.USER, OwnerScope.GROUP}:
        if not text.isdigit():
            raise ValueError(f"{scope.value} owner_id must be numeric")
        return str(int(text))
    if scope is OwnerScope.GLOBAL:
        if text != "global":
            raise ValueError("global owner_id must be 'global'")
        return text
    return _safe_id(text, "world owner_id", maximum=120)


def _canonical_evidence_ref(value: object) -> str:
    text = _safe_id(value, "evidence_ref")
    kind, separator, target = text.partition(":")
    if not separator or kind not in _EVIDENCE_KINDS or not target:
        raise ValueError(f"invalid evidence_ref: {value!r}")
    parsed = parse_linked_ref(text)
    if parsed is not None and parsed.kind != "legacy":
        return parsed.canonical
    return f"{kind}:{_safe_id(target, 'evidence target', maximum=220)}"


def _canonical_projection_ref(value: object, *, expected_kind: str | None = None) -> str:
    text = _safe_id(value, "projection_ref")
    kind, separator, target = text.partition(":")
    if not separator or not target:
        raise ValueError("projection_ref must be canonical")
    if expected_kind is not None and kind != expected_kind:
        raise ValueError(f"projection_ref must use {expected_kind}: prefix")
    parsed = parse_linked_ref(text)
    if parsed is not None and parsed.kind != "legacy":
        return parsed.canonical
    return f"{kind}:{_safe_id(target, 'projection target', maximum=220)}"


def _canonical_actor_ref(value: object) -> str:
    text = str(value or "").strip()
    try:
        return _canonical_subject(text)
    except ValueError:
        pass
    prefix, separator, target = text.partition(":")
    if prefix not in {"policy", "operator", "migration", "projector", "service"}:
        raise ValueError(f"invalid actor_ref: {value!r}")
    if not separator:
        raise ValueError(f"invalid actor_ref: {value!r}")
    return f"{prefix}:{_safe_id(target, 'actor target', maximum=200)}"


def _sorted_unique(values: Iterable[object], field_name: str) -> tuple[str, ...]:
    return tuple(sorted({_safe_id(value, field_name) for value in values}))


@dataclass(frozen=True, slots=True)
class CardClaimV1:
    category: str
    content: str
    claim_kind: ClassVar[str] = "card"

    def __post_init__(self) -> None:
        category = str(self.category or "").strip().lower()
        if category not in _CARD_CATEGORIES:
            raise ValueError(f"invalid card category: {self.category!r}")
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "content", _clean_text(self.content, "content"))

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.claim_kind, "category": self.category, "content": self.content}


@dataclass(frozen=True, slots=True)
class FactClaimV1:
    subject: str
    predicate: str
    object_value: str
    claim_kind: ClassVar[str] = "fact"

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _canonical_subject(self.subject))
        object.__setattr__(self, "predicate", _clean_text(self.predicate, "predicate", maximum=200))
        object.__setattr__(self, "object_value", _clean_text(self.object_value, "object_value"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.claim_kind,
            "subject": self.subject,
            "predicate": self.predicate,
            "object_value": self.object_value,
        }


@dataclass(frozen=True, slots=True)
class SlangClaimV1:
    term: str
    meaning: str
    aliases: tuple[str, ...] = ()
    repeat_policy: RepeatPolicy = RepeatPolicy.UNDERSTAND_ONLY
    claim_kind: ClassVar[str] = "slang"

    def __post_init__(self) -> None:
        object.__setattr__(self, "term", _clean_text(self.term, "term", maximum=160))
        object.__setattr__(self, "meaning", _clean_text(self.meaning, "meaning"))
        aliases = tuple(
            dict.fromkeys(
                _clean_text(alias, "alias", maximum=160) for alias in tuple(self.aliases)
            )
        )
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(
            self,
            "repeat_policy",
            _enum(RepeatPolicy, self.repeat_policy, "repeat_policy"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.claim_kind,
            "term": self.term,
            "meaning": self.meaning,
            "aliases": list(self.aliases),
            "repeat_policy": self.repeat_policy.value,
        }


@dataclass(frozen=True, slots=True)
class StyleClaimV1:
    expression: str
    situation: str
    outcome_signal: str = ""
    claim_kind: ClassVar[str] = "style"

    def __post_init__(self) -> None:
        object.__setattr__(self, "expression", _clean_text(self.expression, "expression"))
        object.__setattr__(self, "situation", _clean_text(self.situation, "situation"))
        object.__setattr__(
            self,
            "outcome_signal",
            _optional_text(self.outcome_signal, "outcome_signal"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.claim_kind,
            "expression": self.expression,
            "situation": self.situation,
            "outcome_signal": self.outcome_signal,
        }


@dataclass(frozen=True, slots=True)
class EpisodeClaimV1:
    situation: str
    observed_context: str = ""
    action_taken: str = ""
    outcome_signal: str = ""
    reflection: str = ""
    claim_kind: ClassVar[str] = "episode"

    def __post_init__(self) -> None:
        object.__setattr__(self, "situation", _clean_text(self.situation, "situation"))
        for field_name in (
            "observed_context",
            "action_taken",
            "outcome_signal",
            "reflection",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_text(getattr(self, field_name), field_name),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.claim_kind,
            "situation": self.situation,
            "observed_context": self.observed_context,
            "action_taken": self.action_taken,
            "outcome_signal": self.outcome_signal,
            "reflection": self.reflection,
        }


@dataclass(frozen=True, slots=True)
class GraphRelationClaimV1:
    subject_node: str
    predicate: str
    object_node: str
    edge_type: GraphEdgeType = GraphEdgeType.FACT
    claim_kind: ClassVar[str] = "graph_relation"

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_node", _canonical_subject(self.subject_node))
        object.__setattr__(self, "predicate", _clean_text(self.predicate, "predicate", maximum=200))
        object.__setattr__(self, "object_node", _canonical_subject(self.object_node))
        object.__setattr__(
            self,
            "edge_type",
            _enum(GraphEdgeType, self.edge_type, "edge_type"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.claim_kind,
            "subject_node": self.subject_node,
            "predicate": self.predicate,
            "object_node": self.object_node,
            "edge_type": self.edge_type.value,
        }


type MemoryClaimV1 = (
    CardClaimV1
    | FactClaimV1
    | SlangClaimV1
    | StyleClaimV1
    | EpisodeClaimV1
    | GraphRelationClaimV1
)
_CLAIM_TYPES = (
    CardClaimV1,
    FactClaimV1,
    SlangClaimV1,
    StyleClaimV1,
    EpisodeClaimV1,
    GraphRelationClaimV1,
)


@dataclass(frozen=True, slots=True)
class EvidenceAtomV1:
    evidence_ref: str
    content_sha256: str
    quote: str
    actor_ref: str
    occurred_at: datetime | str | None

    def __post_init__(self) -> None:
        quote = _evidence_quote(self.quote)
        digest = str(self.content_sha256 or "").strip().lower()
        if _DIGEST_RE.fullmatch(digest) is None or digest != sha256_text(quote):
            raise ValueError("content_sha256 must match the exact evidence quote")
        object.__setattr__(self, "evidence_ref", _canonical_evidence_ref(self.evidence_ref))
        object.__setattr__(self, "content_sha256", digest)
        object.__setattr__(self, "quote", quote)
        object.__setattr__(self, "actor_ref", _canonical_actor_ref(self.actor_ref))
        object.__setattr__(
            self,
            "occurred_at",
            _utc_time(self.occurred_at, "occurred_at", optional=True),
        )

    def to_dict(self) -> dict[str, Any]:
        occurred_at = self.occurred_at
        if occurred_at is not None and not isinstance(occurred_at, datetime):
            raise TypeError("occurred_at was not normalized")
        return {
            "evidence_ref": self.evidence_ref,
            "content_sha256": self.content_sha256,
            "quote": self.quote,
            "actor_ref": self.actor_ref,
            "occurred_at": _iso_utc(occurred_at) if occurred_at is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ObservationV1:
    source_kind: SourceKind
    producer_kind: ProducerKind
    producer_version: str
    producer_run_id: str
    subject_ref: str
    owner_scope: OwnerScope
    owner_id: str
    visibility: Visibility
    origin_group_ref: str | None
    claim: MemoryClaimV1
    evidence: tuple[EvidenceAtomV1, ...]
    observed_at: datetime | str
    source_occurred_at: datetime | str | None
    time_basis: TimeBasis
    valid_from: datetime | str | None
    valid_to: datetime | str | None
    confidence: float
    contract_version: str = field(init=False, default="memory.observation.v1")
    observation_id: str = field(init=False)
    observation_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        source_kind = _enum(SourceKind, self.source_kind, "source_kind")
        producer_kind = _enum(ProducerKind, self.producer_kind, "producer_kind")
        owner_scope = _enum(OwnerScope, self.owner_scope, "owner_scope")
        visibility = _enum(Visibility, self.visibility, "visibility")
        time_basis = _enum(TimeBasis, self.time_basis, "time_basis")
        if not isinstance(self.claim, _CLAIM_TYPES):
            raise TypeError("claim must be an immutable memory claim v1")
        evidence_values = tuple(self.evidence)
        if not evidence_values or not all(
            isinstance(atom, EvidenceAtomV1) for atom in evidence_values
        ):
            raise ValueError("at least one EvidenceAtomV1 is required")
        by_ref: dict[str, EvidenceAtomV1] = {}
        for atom in evidence_values:
            existing = by_ref.get(atom.evidence_ref)
            if existing is not None and existing != atom:
                raise ValueError("one evidence_ref cannot describe conflicting evidence")
            by_ref[atom.evidence_ref] = atom
        evidence = tuple(by_ref[key] for key in sorted(by_ref))

        subject_ref = _canonical_subject(self.subject_ref)
        owner_id = _canonical_owner(owner_scope, self.owner_id)
        origin_group_ref = (
            _canonical_group_ref(self.origin_group_ref)
            if self.origin_group_ref is not None and str(self.origin_group_ref).strip()
            else None
        )
        if visibility is Visibility.SAME_GROUP and origin_group_ref is None:
            raise ValueError("same_group visibility requires origin_group_ref")
        if visibility is Visibility.PRIVATE and owner_scope is not OwnerScope.USER:
            raise ValueError("private visibility is valid only for user-owned memory")
        if visibility is Visibility.GLOBAL and producer_kind in {
            ProducerKind.MEMO,
            ProducerKind.COMPACTION,
            ProducerKind.CONSOLIDATOR,
        }:
            raise ValueError("LLM producers cannot select global visibility")
        if owner_scope is OwnerScope.USER:
            expected_subject = f"user:qq:{owner_id}"
            if producer_kind in {
                ProducerKind.MEMO,
                ProducerKind.COMPACTION,
            } and subject_ref != expected_subject:
                raise ValueError("user producer subject must match trusted owner")
        if (
            owner_scope is OwnerScope.GROUP
            and subject_ref.startswith("group:qq:")
            and subject_ref != f"group:qq:{owner_id}"
        ):
            raise ValueError("group subject must match trusted owner")

        observed_at = _utc_time(self.observed_at, "observed_at")
        source_at = _utc_time(
            self.source_occurred_at,
            "source_occurred_at",
            optional=True,
        )
        if time_basis is TimeBasis.SOURCE_EVENT and source_at is None:
            raise ValueError("source_event time basis requires source_occurred_at")
        if time_basis is TimeBasis.UNKNOWN and source_at is not None:
            raise ValueError("unknown time basis cannot claim source_occurred_at")
        valid_from = _utc_time(self.valid_from, "valid_from", optional=True)
        valid_to = _utc_time(self.valid_to, "valid_to", optional=True)
        if valid_from is not None and valid_to is not None and valid_to <= valid_from:
            raise ValueError("valid_to must be later than valid_from")

        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "producer_kind", producer_kind)
        object.__setattr__(
            self,
            "producer_version",
            _safe_id(self.producer_version, "producer_version", maximum=80),
        )
        object.__setattr__(
            self,
            "producer_run_id",
            _safe_id(self.producer_run_id, "producer_run_id", maximum=160),
        )
        object.__setattr__(self, "subject_ref", subject_ref)
        object.__setattr__(self, "owner_scope", owner_scope)
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "visibility", visibility)
        object.__setattr__(self, "origin_group_ref", origin_group_ref)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "source_occurred_at", source_at)
        object.__setattr__(self, "time_basis", time_basis)
        object.__setattr__(self, "valid_from", valid_from)
        object.__setattr__(self, "valid_to", valid_to)
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        digest = _bare_digest(self._identity_dict())
        object.__setattr__(self, "observation_sha256", digest)
        object.__setattr__(self, "observation_id", f"mobs_{digest[:24]}")

    def _identity_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "source_kind": self.source_kind,
            "producer_kind": self.producer_kind,
            "producer_version": self.producer_version,
            "producer_run_id": self.producer_run_id,
            "subject_ref": self.subject_ref,
            "owner_scope": self.owner_scope,
            "owner_id": self.owner_id,
            "visibility": self.visibility,
            "origin_group_ref": self.origin_group_ref,
            "claim": self.claim.to_dict(),
            "evidence": [atom.to_dict() for atom in self.evidence],
            "observed_at": self.observed_at,
            "source_occurred_at": self.source_occurred_at,
            "time_basis": self.time_basis,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "confidence": self.confidence,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **_jsonable(self._identity_dict()),
            "observation_id": self.observation_id,
            "observation_sha256": self.observation_sha256,
        }


def _projection_payload(
    projection_kind: ProjectionKind,
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("projection payload must be a mapping")
    raw = {str(key): value for key, value in payload.items()}
    schemas: dict[ProjectionKind, tuple[set[str], set[str]]] = {
        ProjectionKind.CARD: ({"category", "content"}, {"category", "content"}),
        ProjectionKind.SLANG: (
            {"term", "meaning", "aliases", "repeat_policy"},
            {"term", "meaning"},
        ),
        ProjectionKind.STYLE: (
            {"expression", "situation", "outcome_signal"},
            {"expression", "situation"},
        ),
        ProjectionKind.EPISODE: (
            {
                "situation",
                "observed_context",
                "action_taken",
                "outcome_signal",
                "reflection",
            },
            {"situation"},
        ),
        ProjectionKind.GRAPH_RELATION: (
            {"subject_node", "predicate", "object_node", "edge_type"},
            {"subject_node", "predicate", "object_node"},
        ),
    }
    allowed, required = schemas[projection_kind]
    if set(raw) - allowed or not required.issubset(raw):
        raise ValueError("projection payload does not match its closed schema")
    if projection_kind is ProjectionKind.CARD:
        normalized = CardClaimV1(
            category=str(raw["category"]),
            content=str(raw["content"]),
        ).to_dict()
    elif projection_kind is ProjectionKind.SLANG:
        normalized = SlangClaimV1(
            term=str(raw["term"]),
            meaning=str(raw["meaning"]),
            aliases=tuple(raw.get("aliases") or ()),
            repeat_policy=raw.get("repeat_policy", "understand_only"),  # type: ignore[arg-type]
        ).to_dict()
    elif projection_kind is ProjectionKind.STYLE:
        normalized = StyleClaimV1(
            expression=str(raw["expression"]),
            situation=str(raw["situation"]),
            outcome_signal=str(raw.get("outcome_signal") or ""),
        ).to_dict()
    elif projection_kind is ProjectionKind.EPISODE:
        normalized = EpisodeClaimV1(
            situation=str(raw["situation"]),
            observed_context=str(raw.get("observed_context") or ""),
            action_taken=str(raw.get("action_taken") or ""),
            outcome_signal=str(raw.get("outcome_signal") or ""),
            reflection=str(raw.get("reflection") or ""),
        ).to_dict()
    else:
        normalized = GraphRelationClaimV1(
            subject_node=str(raw["subject_node"]),
            predicate=str(raw["predicate"]),
            object_node=str(raw["object_node"]),
            edge_type=raw.get("edge_type", "fact"),  # type: ignore[arg-type]
        ).to_dict()
    normalized.pop("kind", None)
    return MappingProxyType({key: _freeze_json(normalized[key]) for key in sorted(normalized)})


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in sorted(value.items())}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("projection payload contains non-finite number")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"projection payload contains {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class ProjectionProposalV1:
    projection_kind: ProjectionKind
    operation: Operation
    target_ref: str | None
    payload: Mapping[str, Any]
    contract_version: str = field(init=False, default="memory.projection_proposal.v1")

    def __post_init__(self) -> None:
        kind = _enum(ProjectionKind, self.projection_kind, "projection_kind")
        operation = _enum(Operation, self.operation, "operation")
        if operation is Operation.CREATE:
            if self.target_ref is not None and str(self.target_ref).strip():
                raise ValueError("create proposal cannot have target_ref")
            target_ref = None
        else:
            if self.target_ref is None or not str(self.target_ref).strip():
                raise ValueError(f"{operation.value} proposal requires target_ref")
            target_ref = _canonical_projection_ref(
                self.target_ref,
                expected_kind=kind.value,
            )
        object.__setattr__(self, "projection_kind", kind)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "target_ref", target_ref)
        object.__setattr__(self, "payload", _projection_payload(kind, self.payload))

    @classmethod
    def create(
        cls,
        *,
        projection_kind: ProjectionKind | str,
        operation: Operation | str,
        payload: Mapping[str, Any],
        target_ref: str | None = None,
    ) -> ProjectionProposalV1:
        return cls(
            projection_kind=projection_kind,  # type: ignore[arg-type]
            operation=operation,  # type: ignore[arg-type]
            target_ref=target_ref,
            payload=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "projection_kind": self.projection_kind.value,
            "operation": self.operation.value,
            "target_ref": self.target_ref,
            "payload": _jsonable(self.payload),
        }


@dataclass(frozen=True, slots=True)
class CandidateEnvelopeV1:
    observation: ObservationV1
    proposal: ProjectionProposalV1
    producer_kind: ProducerKind
    producer_run_id: str
    producer_item_id: str
    produced_at: datetime
    model_output_sha256: str
    contract_version: str = field(init=False, default="memory.candidate.v1")
    mode: str = field(init=False, default="shadow")
    candidate_id: str = field(init=False)
    candidate_sha256: str = field(init=False)
    idempotency_key: str = field(init=False)

    @classmethod
    def create(
        cls,
        *,
        observation: ObservationV1,
        proposal: ProjectionProposalV1,
        producer_kind: ProducerKind | str,
        producer_run_id: str,
        producer_item_id: str,
        produced_at: datetime | str,
        model_output: str,
    ) -> CandidateEnvelopeV1:
        if not isinstance(observation, ObservationV1):
            raise TypeError("observation must be ObservationV1")
        if not isinstance(proposal, ProjectionProposalV1):
            raise TypeError("proposal must be ProjectionProposalV1")
        kind = _enum(ProducerKind, producer_kind, "producer_kind")
        if observation.producer_kind is not kind:
            raise ValueError("candidate producer_kind must match observation")
        clean_run = _safe_id(producer_run_id, "producer_run_id", maximum=160)
        if observation.producer_run_id != clean_run:
            raise ValueError("candidate producer_run_id must match observation")
        return cls(
            observation=observation,
            proposal=proposal,
            producer_kind=kind,  # type: ignore[arg-type]
            producer_run_id=clean_run,
            producer_item_id=_safe_id(
                producer_item_id,
                "producer_item_id",
                maximum=160,
            ),
            produced_at=_utc_time(produced_at, "produced_at"),  # type: ignore[arg-type]
            model_output_sha256=sha256_text(str(model_output)),
        )

    def __post_init__(self) -> None:
        if not isinstance(self.observation, ObservationV1):
            raise TypeError("observation must be ObservationV1")
        if not isinstance(self.proposal, ProjectionProposalV1):
            raise TypeError("proposal must be ProjectionProposalV1")
        kind = _enum(ProducerKind, self.producer_kind, "producer_kind")
        run_id = _safe_id(self.producer_run_id, "producer_run_id", maximum=160)
        item_id = _safe_id(self.producer_item_id, "producer_item_id", maximum=160)
        produced_at = _utc_time(self.produced_at, "produced_at")
        if self.observation.producer_kind is not kind:
            raise ValueError("candidate producer_kind must match observation")
        if self.observation.producer_run_id != run_id:
            raise ValueError("candidate producer_run_id must match observation")
        observed_at = self.observation.observed_at
        if not isinstance(observed_at, datetime):
            raise TypeError("observation observed_at was not normalized")
        if produced_at < observed_at:
            raise ValueError("candidate cannot be produced before its observation")
        model_output_sha256 = str(self.model_output_sha256 or "").strip().lower()
        if _DIGEST_RE.fullmatch(model_output_sha256) is None:
            raise ValueError("model_output_sha256 must be a canonical SHA-256 digest")
        object.__setattr__(self, "producer_kind", kind)
        object.__setattr__(self, "producer_run_id", run_id)
        object.__setattr__(self, "producer_item_id", item_id)
        object.__setattr__(self, "produced_at", produced_at)
        object.__setattr__(self, "model_output_sha256", model_output_sha256)
        material = {
            "contract_version": self.contract_version,
            "mode": self.mode,
            "producer_kind": self.producer_kind,
            "producer_run_id": self.producer_run_id,
            "producer_item_id": self.producer_item_id,
            "produced_at": self.produced_at,
            "observation_sha256": self.observation.observation_sha256,
            "proposal": self.proposal.to_dict(),
            "model_output_sha256": self.model_output_sha256,
        }
        digest = _bare_digest(material)
        object.__setattr__(self, "candidate_sha256", digest)
        object.__setattr__(self, "candidate_id", f"mcand_{digest[:24]}")
        object.__setattr__(self, "idempotency_key", f"sha256:{digest}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "mode": self.mode,
            "candidate_id": self.candidate_id,
            "candidate_sha256": self.candidate_sha256,
            "producer_kind": self.producer_kind.value,
            "producer_run_id": self.producer_run_id,
            "producer_item_id": self.producer_item_id,
            "produced_at": _iso_utc(self.produced_at),
            "observation": self.observation.to_dict(),
            "proposal": self.proposal.to_dict(),
            "model_output_sha256": self.model_output_sha256,
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True, slots=True)
class ConflictV1:
    kind: ConflictKind
    subject_ref: str
    claim_key: str
    observation_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    existing_projection_refs: tuple[str, ...]
    detected_at: datetime
    detector: ConflictDetector
    basis_evidence_refs: tuple[str, ...]
    contract_version: str = field(init=False, default="memory.conflict.v1")
    conflict_id: str = field(init=False)
    conflict_key: str = field(init=False)

    @classmethod
    def create(
        cls,
        *,
        kind: ConflictKind | str,
        subject_ref: str,
        claim_key: str,
        observation_ids: Iterable[str],
        detected_at: datetime | str,
        candidate_ids: Iterable[str] = (),
        existing_projection_refs: Iterable[str] = (),
        detector: ConflictDetector | str = ConflictDetector.DETERMINISTIC,
        basis_evidence_refs: Iterable[str] = (),
    ) -> ConflictV1:
        observations = _sorted_unique(observation_ids, "observation_id")
        if len(observations) < 2:
            raise ValueError("conflict requires at least two observations")
        evidence = tuple(
            sorted({_canonical_evidence_ref(value) for value in basis_evidence_refs})
        )
        if not evidence:
            raise ValueError("conflict requires basis evidence")
        projections = tuple(
            sorted(
                {
                    _canonical_projection_ref(value)
                    for value in existing_projection_refs
                }
            )
        )
        return cls(
            kind=_enum(ConflictKind, kind, "conflict kind"),  # type: ignore[arg-type]
            subject_ref=_canonical_subject(subject_ref),
            claim_key=_safe_id(claim_key, "claim_key"),
            observation_ids=observations,
            candidate_ids=_sorted_unique(candidate_ids, "candidate_id"),
            existing_projection_refs=projections,
            detected_at=_utc_time(detected_at, "detected_at"),  # type: ignore[arg-type]
            detector=_enum(ConflictDetector, detector, "detector"),  # type: ignore[arg-type]
            basis_evidence_refs=evidence,
        )

    def __post_init__(self) -> None:
        kind = _enum(ConflictKind, self.kind, "conflict kind")
        subject_ref = _canonical_subject(self.subject_ref)
        claim_key = _safe_id(self.claim_key, "claim_key")
        observation_ids = _sorted_unique(self.observation_ids, "observation_id")
        if len(observation_ids) < 2:
            raise ValueError("conflict requires at least two observations")
        candidate_ids = _sorted_unique(self.candidate_ids, "candidate_id")
        existing_projection_refs = tuple(
            sorted(
                {
                    _canonical_projection_ref(value)
                    for value in self.existing_projection_refs
                }
            )
        )
        detected_at = _utc_time(self.detected_at, "detected_at")
        detector = _enum(ConflictDetector, self.detector, "detector")
        basis_evidence_refs = tuple(
            sorted(
                {
                    _canonical_evidence_ref(value)
                    for value in self.basis_evidence_refs
                }
            )
        )
        if not basis_evidence_refs:
            raise ValueError("conflict requires basis evidence")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "subject_ref", subject_ref)
        object.__setattr__(self, "claim_key", claim_key)
        object.__setattr__(self, "observation_ids", observation_ids)
        object.__setattr__(self, "candidate_ids", candidate_ids)
        object.__setattr__(self, "existing_projection_refs", existing_projection_refs)
        object.__setattr__(self, "detected_at", detected_at)
        object.__setattr__(self, "detector", detector)
        object.__setattr__(self, "basis_evidence_refs", basis_evidence_refs)
        key_material = {
            "kind": self.kind,
            "subject_ref": self.subject_ref,
            "claim_key": self.claim_key,
        }
        object.__setattr__(self, "conflict_key", sha256_text(canonical_json(key_material)))
        digest = _bare_digest(
            {
                "contract_version": self.contract_version,
                **key_material,
                "observation_ids": self.observation_ids,
                "candidate_ids": self.candidate_ids,
                "existing_projection_refs": self.existing_projection_refs,
                "detected_at": self.detected_at,
                "detector": self.detector,
                "basis_evidence_refs": self.basis_evidence_refs,
            }
        )
        object.__setattr__(self, "conflict_id", f"mconf_{digest[:24]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "conflict_id": self.conflict_id,
            "conflict_key": self.conflict_key,
            "kind": self.kind.value,
            "subject_ref": self.subject_ref,
            "claim_key": self.claim_key,
            "observation_ids": list(self.observation_ids),
            "candidate_ids": list(self.candidate_ids),
            "existing_projection_refs": list(self.existing_projection_refs),
            "detected_at": _iso_utc(self.detected_at),
            "detector": self.detector.value,
            "basis_evidence_refs": list(self.basis_evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class PromotionEventV1:
    candidate_id: str
    candidate_sha256: str
    event_kind: PromotionKind
    actor_kind: ActorKind
    actor_ref: str
    occurred_at: datetime
    reason_code: str
    operator_note: str
    conflict_ids: tuple[str, ...]
    projection_kind: ProjectionKind
    operation: Operation
    projection_ref: str | None
    receipt_ref: str | None
    note_sha256: str
    contract_version: str = field(init=False, default="memory.promotion_event.v1")
    event_id: str = field(init=False)
    idempotency_key: str = field(init=False)

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        candidate_sha256: str,
        event_kind: PromotionKind | str,
        actor_kind: ActorKind | str,
        actor_ref: str,
        occurred_at: datetime | str,
        reason_code: str,
        operator_note: str,
        conflict_ids: Iterable[str],
        projection_kind: ProjectionKind | str,
        operation: Operation | str,
        projection_ref: str | None,
        receipt_ref: str | None,
    ) -> PromotionEventV1:
        kind = _enum(PromotionKind, event_kind, "event_kind")
        actor = _enum(ActorKind, actor_kind, "actor_kind")
        projection = _enum(ProjectionKind, projection_kind, "projection_kind")
        operation_value = _enum(Operation, operation, "operation")
        clean_conflicts = _sorted_unique(conflict_ids, "conflict_id")
        clean_projection = None
        clean_receipt = None
        if kind in {
            PromotionKind.REVIEW_QUEUED,
            PromotionKind.PROMOTION_APPROVED,
            PromotionKind.PROMOTION_REJECTED,
        }:
            if projection_ref or receipt_ref:
                raise ValueError("review/decision event cannot contain projection receipt")
        elif kind is PromotionKind.CONFLICT_RESOLVED:
            if actor is not ActorKind.OPERATOR:
                raise ValueError("conflict_resolved actor must be operator")
            if not clean_conflicts:
                raise ValueError("conflict_resolved requires conflict_ids")
            if projection_ref or receipt_ref:
                raise ValueError("conflict_resolved cannot contain projection receipt")
        elif kind is PromotionKind.PROJECTION_APPLIED:
            if not projection_ref or not receipt_ref:
                raise ValueError("projection_applied requires projection_ref and receipt_ref")
            clean_projection = _canonical_projection_ref(
                projection_ref,
                expected_kind=projection.value,
            )
            clean_receipt = _canonical_projection_ref(receipt_ref)
            if actor is not ActorKind.PROJECTOR:
                raise ValueError("projection_applied actor must be projector")
        else:
            if projection_ref or not receipt_ref:
                raise ValueError("projection_failed requires only receipt_ref")
            clean_receipt = _canonical_projection_ref(receipt_ref)
            if actor is not ActorKind.PROJECTOR:
                raise ValueError("projection_failed actor must be projector")
        note = " ".join(str(operator_note or "").split())
        if len(note) > 500:
            raise ValueError("operator_note must not exceed 500 characters")
        digest = str(candidate_sha256 or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("candidate_sha256 must be a bare SHA-256 digest")
        return cls(
            candidate_id=_safe_id(candidate_id, "candidate_id"),
            candidate_sha256=digest,
            event_kind=kind,  # type: ignore[arg-type]
            actor_kind=actor,  # type: ignore[arg-type]
            actor_ref=_canonical_actor_ref(actor_ref),
            occurred_at=_utc_time(occurred_at, "occurred_at"),  # type: ignore[arg-type]
            reason_code=_safe_id(reason_code, "reason_code", maximum=120),
            operator_note=note,
            conflict_ids=clean_conflicts,
            projection_kind=projection,  # type: ignore[arg-type]
            operation=operation_value,  # type: ignore[arg-type]
            projection_ref=clean_projection,
            receipt_ref=clean_receipt,
            note_sha256=sha256_text(note),
        )

    def __post_init__(self) -> None:
        candidate_sha256 = str(self.candidate_sha256 or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", candidate_sha256) is None:
            raise ValueError("candidate_sha256 must be a bare SHA-256 digest")
        candidate_id = _safe_id(self.candidate_id, "candidate_id")
        if candidate_id != f"mcand_{candidate_sha256[:24]}":
            raise ValueError("candidate_id must match candidate_sha256")
        event_kind = _enum(PromotionKind, self.event_kind, "event_kind")
        actor_kind = _enum(ActorKind, self.actor_kind, "actor_kind")
        actor_ref = _canonical_actor_ref(self.actor_ref)
        if not actor_ref.startswith(f"{actor_kind.value}:"):
            raise ValueError("actor_ref namespace must match actor_kind")
        projection_kind = _enum(
            ProjectionKind,
            self.projection_kind,
            "projection_kind",
        )
        operation = _enum(Operation, self.operation, "operation")
        occurred_at = _utc_time(self.occurred_at, "occurred_at")
        reason_code = _safe_id(self.reason_code, "reason_code", maximum=120)
        operator_note = " ".join(str(self.operator_note or "").split())
        if len(operator_note) > 500:
            raise ValueError("operator_note must not exceed 500 characters")
        conflict_ids = _sorted_unique(self.conflict_ids, "conflict_id")
        projection_ref = None
        receipt_ref = None
        if event_kind in {
            PromotionKind.REVIEW_QUEUED,
            PromotionKind.PROMOTION_APPROVED,
            PromotionKind.PROMOTION_REJECTED,
        }:
            if actor_kind is ActorKind.PROJECTOR:
                raise ValueError("projector cannot queue or decide promotion")
            if self.projection_ref or self.receipt_ref:
                raise ValueError("review/decision event cannot contain projection receipt")
        elif event_kind is PromotionKind.CONFLICT_RESOLVED:
            if actor_kind is not ActorKind.OPERATOR:
                raise ValueError("conflict_resolved actor must be operator")
            if not conflict_ids:
                raise ValueError("conflict_resolved requires conflict_ids")
            if self.projection_ref or self.receipt_ref:
                raise ValueError("conflict_resolved cannot contain projection receipt")
        elif event_kind is PromotionKind.PROJECTION_APPLIED:
            if actor_kind is not ActorKind.PROJECTOR:
                raise ValueError("projection_applied actor must be projector")
            if not self.projection_ref or not self.receipt_ref:
                raise ValueError("projection_applied requires projection_ref and receipt_ref")
            projection_ref = _canonical_projection_ref(
                self.projection_ref,
                expected_kind=projection_kind.value,
            )
            receipt_ref = _canonical_projection_ref(self.receipt_ref)
        else:
            if actor_kind is not ActorKind.PROJECTOR:
                raise ValueError("projection_failed actor must be projector")
            if self.projection_ref or not self.receipt_ref:
                raise ValueError("projection_failed requires only receipt_ref")
            receipt_ref = _canonical_projection_ref(self.receipt_ref)

        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "candidate_sha256", candidate_sha256)
        object.__setattr__(self, "event_kind", event_kind)
        object.__setattr__(self, "actor_kind", actor_kind)
        object.__setattr__(self, "actor_ref", actor_ref)
        object.__setattr__(self, "occurred_at", occurred_at)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "operator_note", operator_note)
        object.__setattr__(self, "conflict_ids", conflict_ids)
        object.__setattr__(self, "projection_kind", projection_kind)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "projection_ref", projection_ref)
        object.__setattr__(self, "receipt_ref", receipt_ref)
        object.__setattr__(self, "note_sha256", sha256_text(operator_note))
        material = {
            "contract_version": self.contract_version,
            "candidate_id": self.candidate_id,
            "candidate_sha256": self.candidate_sha256,
            "event_kind": self.event_kind,
            "actor_kind": self.actor_kind,
            "actor_ref": self.actor_ref,
            "occurred_at": self.occurred_at,
            "reason_code": self.reason_code,
            "note_sha256": self.note_sha256,
            "conflict_ids": self.conflict_ids,
            "projection_kind": self.projection_kind,
            "operation": self.operation,
            "projection_ref": self.projection_ref,
            "receipt_ref": self.receipt_ref,
        }
        digest = _bare_digest(material)
        object.__setattr__(self, "event_id", f"mpev_{digest[:24]}")
        object.__setattr__(self, "idempotency_key", f"sha256:{digest}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "event_id": self.event_id,
            "candidate_id": self.candidate_id,
            "candidate_sha256": self.candidate_sha256,
            "event_kind": self.event_kind.value,
            "actor_kind": self.actor_kind.value,
            "actor_ref": self.actor_ref,
            "occurred_at": _iso_utc(self.occurred_at),
            "reason_code": self.reason_code,
            "operator_note": self.operator_note,
            "note_sha256": self.note_sha256,
            "conflict_ids": list(self.conflict_ids),
            "projection_kind": self.projection_kind.value,
            "operation": self.operation.value,
            "projection_ref": self.projection_ref,
            "receipt_ref": self.receipt_ref,
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True, slots=True)
class PromotionFoldV1:
    candidate_id: str
    candidate_sha256: str
    status: str
    decision_event_id: str = ""
    projection_event_id: str = ""
    projection_ref: str = ""
    receipt_ref: str = ""
    resolved_conflict_ids: tuple[str, ...] = ()
    unresolved_conflict_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()


def fold_promotion_events(
    events: Iterable[PromotionEventV1],
    *,
    known_conflict_ids: Iterable[str] = (),
    conflict_detected_at: Mapping[str, datetime | str] | None = None,
    conflict_known_at: Mapping[str, datetime | str] | None = None,
    event_append_seq: Mapping[str, int] | None = None,
    conflict_append_seq: Mapping[str, int] | None = None,
) -> PromotionFoldV1:
    if conflict_detected_at is not None and conflict_known_at is not None:
        raise ValueError(
            "provide only one of conflict_detected_at or conflict_known_at"
        )
    unique: dict[str, PromotionEventV1] = {}
    ordered: list[PromotionEventV1] = []
    for event in events:
        if not isinstance(event, PromotionEventV1):
            raise TypeError("promotion fold accepts PromotionEventV1 only")
        previous = unique.get(event.event_id)
        if previous is not None and previous != event:
            raise ValueError("one promotion event id cannot contain two facts")
        if previous is not None:
            continue
        unique[event.event_id] = event
        ordered.append(event)
    if not ordered:
        raise ValueError("promotion fold requires at least one event")
    candidate_id = ordered[0].candidate_id
    candidate_sha = ordered[0].candidate_sha256
    projection_kind = ordered[0].projection_kind
    operation = ordered[0].operation
    detected_at_by_conflict: dict[str, datetime | None] = {
        value: None
        for value in _sorted_unique(known_conflict_ids, "conflict_id")
    }
    conflict_times = (
        conflict_known_at
        if conflict_known_at is not None
        else conflict_detected_at
    )
    for raw_id, raw_time in (conflict_times or {}).items():
        conflict_id = _safe_id(raw_id, "conflict_id")
        detected_at_by_conflict[conflict_id] = _utc_time(
            raw_time,
            "conflict detected_at",
        )
    known_conflicts = set(detected_at_by_conflict)
    sequence_mode = event_append_seq is not None or conflict_append_seq is not None
    if sequence_mode and (event_append_seq is None or conflict_append_seq is None):
        raise ValueError(
            "event and conflict append sequences must be provided together"
        )
    normalized_event_sequences: dict[str, int] = {}
    normalized_conflict_sequences: dict[str, int] = {}
    if sequence_mode:
        assert event_append_seq is not None
        assert conflict_append_seq is not None
        for event in ordered:
            raw_sequence = event_append_seq.get(event.event_id)
            if isinstance(raw_sequence, bool) or not isinstance(raw_sequence, int):
                raise TypeError("event append sequence must be an integer")
            if raw_sequence <= 0:
                raise ValueError("event append sequence must be positive")
            normalized_event_sequences[event.event_id] = raw_sequence
        for conflict_id in known_conflicts:
            raw_sequence = conflict_append_seq.get(conflict_id)
            if isinstance(raw_sequence, bool) or not isinstance(raw_sequence, int):
                raise TypeError("conflict append sequence must be an integer")
            if raw_sequence <= 0:
                raise ValueError("conflict append sequence must be positive")
            normalized_conflict_sequences[conflict_id] = raw_sequence
        global_sequences = (
            *normalized_event_sequences.values(),
            *normalized_conflict_sequences.values(),
        )
        if len(set(global_sequences)) != len(global_sequences):
            raise ValueError("governance append sequences must be globally unique")

    def conflicts_known_for(event: PromotionEventV1) -> set[str]:
        if sequence_mode:
            event_sequence = normalized_event_sequences[event.event_id]
            return {
                conflict_id
                for conflict_id, conflict_sequence in (
                    normalized_conflict_sequences.items()
                )
                if conflict_sequence <= event_sequence
            }
        return {
            conflict_id
            for conflict_id, detected_at in detected_at_by_conflict.items()
            if detected_at is None or detected_at <= event.occurred_at
        }

    resolved_conflicts: set[str] = set()
    decision: PromotionEventV1 | None = None
    projection: PromotionEventV1 | None = None
    latest_projection_event: PromotionEventV1 | None = None
    queued = False
    previous_occurred_at: datetime | None = None
    previous_append_seq: int | None = None
    for event in ordered:
        if event.candidate_id != candidate_id or event.candidate_sha256 != candidate_sha:
            raise ValueError("promotion fold cannot mix candidates")
        if event.projection_kind is not projection_kind or event.operation is not operation:
            raise ValueError("promotion events disagree on projection contract")
        if previous_occurred_at is not None and event.occurred_at < previous_occurred_at:
            raise ValueError("promotion events must be folded in append-time order")
        previous_occurred_at = event.occurred_at
        if sequence_mode:
            current_append_seq = normalized_event_sequences[event.event_id]
            if (
                previous_append_seq is not None
                and current_append_seq <= previous_append_seq
            ):
                raise ValueError("promotion event append sequences must increase")
            previous_append_seq = current_append_seq
        if event.event_kind is PromotionKind.REVIEW_QUEUED:
            if decision is not None or projection is not None:
                raise ValueError("review_queued cannot follow a decision")
            if queued:
                raise ValueError("candidate already has a review queue event")
            queued = True
            continue
        if event.event_kind in {
            PromotionKind.PROMOTION_APPROVED,
            PromotionKind.PROMOTION_REJECTED,
        }:
            if decision is not None:
                raise ValueError("candidate already has a promotion decision")
            event_conflicts = set(event.conflict_ids)
            conflicts_at_event = conflicts_known_for(event)
            if not event_conflicts.issubset(conflicts_at_event):
                raise ValueError("promotion decision cites a conflict not yet known")
            for conflict_id in event_conflicts:
                detected_at = detected_at_by_conflict[conflict_id]
                if detected_at is not None and detected_at > event.occurred_at:
                    raise ValueError("conflict cannot be resolved before it is detected")
            if event.event_kind is PromotionKind.PROMOTION_APPROVED:
                preexisting_conflicts = conflicts_at_event
                if preexisting_conflicts and (
                    event.actor_kind is not ActorKind.OPERATOR
                    or not preexisting_conflicts.issubset(event_conflicts)
                ):
                    raise ValueError("unresolved conflicts block promotion approval")
                if event.actor_kind is ActorKind.OPERATOR:
                    resolved_conflicts.update(event_conflicts)
            decision = event
            continue
        if decision is None or decision.event_kind is not PromotionKind.PROMOTION_APPROVED:
            raise ValueError("projection event requires prior approval")
        if event.event_kind is PromotionKind.CONFLICT_RESOLVED:
            event_conflicts = set(event.conflict_ids)
            if not event_conflicts.issubset(conflicts_known_for(event)):
                raise ValueError("conflict resolution cites a conflict not yet known")
            for conflict_id in event_conflicts:
                detected_at = detected_at_by_conflict[conflict_id]
                if detected_at is not None and detected_at > event.occurred_at:
                    raise ValueError("conflict cannot be resolved before it is detected")
            resolved_conflicts.update(event_conflicts)
            continue
        conflicts_at_event = conflicts_known_for(event)
        if conflicts_at_event - resolved_conflicts:
            raise ValueError("unresolved conflicts block projection")
        if event.event_kind is PromotionKind.PROJECTION_APPLIED:
            if projection is not None:
                raise ValueError("candidate already has a projection receipt")
            projection = event
            latest_projection_event = event
        elif event.event_kind is PromotionKind.PROJECTION_FAILED:
            if projection is not None:
                raise ValueError("projection failure cannot follow applied receipt")
            latest_projection_event = event

    resolved_conflicts.intersection_update(known_conflicts)
    unresolved_conflicts = known_conflicts - resolved_conflicts
    if projection is not None:
        status = "projected_conflict" if unresolved_conflicts else "projected"
    elif latest_projection_event is not None:
        status = "projection_failed"
    elif decision is not None:
        if decision.event_kind is PromotionKind.PROMOTION_APPROVED:
            status = "conflicted" if unresolved_conflicts else "approved"
        else:
            status = "rejected"
    elif queued:
        status = "review_queued"
    else:
        status = "unreviewed"
    projection_ref = (
        latest_projection_event.projection_ref or ""
        if latest_projection_event is not None
        else ""
    )
    receipt_ref = (
        latest_projection_event.receipt_ref or ""
        if latest_projection_event is not None
        else ""
    )
    return PromotionFoldV1(
        candidate_id=candidate_id,
        candidate_sha256=candidate_sha,
        status=status,
        decision_event_id=decision.event_id if decision else "",
        projection_event_id=(
            latest_projection_event.event_id
            if latest_projection_event is not None
            else ""
        ),
        projection_ref=projection_ref,
        receipt_ref=receipt_ref,
        resolved_conflict_ids=tuple(sorted(resolved_conflicts)),
        unresolved_conflict_ids=tuple(sorted(unresolved_conflicts)),
        event_ids=tuple(event.event_id for event in ordered),
    )


__all__ = [
    "ActorKind",
    "CandidateEnvelopeV1",
    "CardClaimV1",
    "ConflictDetector",
    "ConflictKind",
    "ConflictV1",
    "EpisodeClaimV1",
    "EvidenceAtomV1",
    "FactClaimV1",
    "GraphEdgeType",
    "GraphRelationClaimV1",
    "MemoryClaimV1",
    "Operation",
    "OwnerScope",
    "ProducerKind",
    "ProjectionKind",
    "ProjectionProposalV1",
    "PromotionEventV1",
    "PromotionFoldV1",
    "PromotionKind",
    "RepeatPolicy",
    "SlangClaimV1",
    "SourceKind",
    "StyleClaimV1",
    "TimeBasis",
    "Visibility",
    "canonical_json",
    "fold_promotion_events",
    "sha256_text",
]
