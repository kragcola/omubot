"""The N7 fiction-only StoryArc owner.

This module deliberately owns a very small state machine.  A StoryArc is a
bot-level record and a group gets a view only through an explicit mapping.  A
committed event changes one arc revision and creates a narrow pending
projection intent only for each target with a typed effect, in the same SQLite
transaction.  Projection catch-up stays disabled until a real Life/partner
owner exists; this module starts no task and has no transport or model
dependency.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, cast

from .store import Store, StoreConnection, StoreRow
from .types import OperationError, Scope

if TYPE_CHECKING:
    from .worldbook import (
        DreamProposal,
        DreamValidator,
        FictionCandidate,
        StoryletEngine,
        StoryletPlan,
        StoryletProposal,
        StoryletState,
    )

ArcRole = Literal["main", "side", "ambient"]
StorySourceKind = Literal["admin_authored_fiction", "social_fiction"]
StoryOriginKind = Literal[
    "admin_authored_fiction", "storylet", "dream_proposal", "social_experience"
]
ProjectionTarget = Literal["life", "partner"]
ArcStatus = Literal["active", "closed"]

_ROLES = frozenset({"main", "side", "ambient"})
_SOURCE_KIND: StorySourceKind = "admin_authored_fiction"
_SOCIAL_SOURCE_KIND: StorySourceKind = "social_fiction"
_SOURCE_KINDS = frozenset({_SOURCE_KIND, _SOCIAL_SOURCE_KIND})
_ORIGIN_KINDS = frozenset(
    {"admin_authored_fiction", "storylet", "dream_proposal", "social_experience"}
)
_PROJECTION_TARGETS: tuple[ProjectionTarget, ...] = ("life", "partner")
_ARC_STATUSES = frozenset({"active", "closed"})
_MAX_GROUPS = 128
_MAX_VARIABLES = 128
_MAX_THREADS = 256
_MAX_EVENTS = 1000000
_MAX_TITLE = 200
_MAX_ID = 128
_MAX_EVENT_TYPE = 64
_MAX_AUTHOR = 128
_MAX_DIGEST = 64
_MAX_CHAT_CHARS = 12_000
_MAX_CHAT_EVENTS = 16
_MAX_EFFECTS = 16
_MAX_EFFECT_VALUE = 512
_MAX_TTL_SECONDS = 7 * 24 * 60 * 60
_LIFE_KEYS = frozenset(
    {"location", "activity", "mood", "energy", "open_constraint", "mood.social_afterglow"}
)
_SOCIAL_RESONANCE_KEY = "social_resonance"
_SOCIAL_AFTERGLOW_KEY = "mood.social_afterglow"
_SOCIAL_AFTERGLOW_VALUE = "近日有温和的社交共鸣"
_SOCIAL_AFTERGLOW_TTL_SECONDS = 24 * 60 * 60
_SOCIAL_NONCURRENT_CODES = frozenset(
    {
        "denied",
        "social_evidence_disabled",
        "social_experience_not_current",
        "social_episode_not_found",
        "social_episode_not_current",
        "social_episode_prompt_unavailable",
        "social_episode_expired",
        "social_experience_invalidated",
        "source_revoked",
        "source_kind_forbidden",
        "source_revision_conflict",
        "social_source_metadata_missing",
        "social_reply_not_found",
        "social_reply_not_succeeded",
        "social_receipt_missing",
        "social_receipt_conflict",
        "social_delivery_incomplete",
    }
)
_LIFE_EFFECT_KEYS = frozenset(
    {
        "key",
        "value",
        "ttl_hours",
        "ttl_seconds",
    }
)
_PARTNER_EFFECT_KEYS = frozenset(
    {
        "entity_id",
        "mood",
        "availability",
        "current_state",
        "constraints",
        "note",
        "event_note",
    }
)
_FORBIDDEN_FICTION_FIELDS = frozenset(
    {
        "bot_id",
        "group_id",
        "user_id",
        "person_id",
        "speaker_id",
        "real_person",
        "real_name",
        "factual",
        "evidence_ref",
        "evidence_refs",
        "display_name",
        "pinned_profile",
        "kind",
        "identity_kind",
        "applied_event_ids",
        "last_event_id",
    }
)

AdminAuthorizer = Callable[[StoreConnection], object]
StoryletAuthorizer = Callable[[StoreConnection], object]
DreamAuthorizer = Callable[[StoreConnection], object]
SocialAuthorizer = Callable[[StoreConnection, Scope, str], object]


def _identity(value: object, code: str, *, limit: int = _MAX_ID) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > limit
        or any(ord(char) < 32 for char in value)
        or "/" in value
        or "\\" in value
    ):
        raise OperationError(code)
    return value


def _title(value: object) -> str:
    if not isinstance(value, str) or len(value) > _MAX_TITLE or any(
        ord(char) < 32 and char not in {"\t"} for char in value
    ):
        raise OperationError("invalid_story_title")
    return value.strip()


def _timestamp(value: object, code: str = "invalid_story_timestamp") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OperationError(code)
    result = float(value)
    if not math.isfinite(result):
        raise OperationError(code)
    return result


def _scope(scope: object, code: str = "invalid_story_scope") -> Scope:
    if type(scope) is not Scope:
        raise OperationError(code)
    return scope


def _groups(values: object, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        raise OperationError("invalid_story_groups")
    raw_values = cast(Sequence[object], values)
    if len(raw_values) > _MAX_GROUPS or (not allow_empty and not raw_values):
        raise OperationError("invalid_story_groups")
    result: list[str] = []
    for value in raw_values:
        normalized = _identity(value, "invalid_story_group", limit=64)
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _scalar(value: object, code: str = "invalid_story_value") -> object:
    if value is None or isinstance(value, str):
        if isinstance(value, str) and (
            len(value) > _MAX_ID or any(ord(char) < 32 for char in value)
        ):
            raise OperationError(code)
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > 1_000_000_000:
            raise OperationError(code)
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > 1_000_000_000:
            raise OperationError(code)
        return value
    raise OperationError(code)


def _variables(values: object) -> dict[str, object]:
    if not isinstance(values, Mapping):
        raise OperationError("invalid_story_variables")
    raw_values = cast(Mapping[object, object], values)
    if len(raw_values) > _MAX_VARIABLES:
        raise OperationError("invalid_story_variables")
    result: dict[str, object] = {}
    for key, value in raw_values.items():
        result[_identity(key, "invalid_story_variable", limit=64)] = _scalar(
            value, "invalid_story_variables"
        )
    return result


def _deltas(values: object) -> dict[str, float | int]:
    if not isinstance(values, Mapping):
        raise OperationError("invalid_story_deltas")
    raw_values = cast(Mapping[object, object], values)
    if len(raw_values) > _MAX_VARIABLES:
        raise OperationError("invalid_story_deltas")
    result: dict[str, float | int] = {}
    for key, value in raw_values.items():
        name = _identity(key, "invalid_story_variable", limit=64)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise OperationError("invalid_story_deltas")
        numeric = float(value)
        if not math.isfinite(numeric) or abs(numeric) > 1_000_000_000:
            raise OperationError("invalid_story_deltas")
        result[name] = value
    return result


def _threads(values: object) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        raise OperationError("invalid_story_threads")
    raw_values = cast(Sequence[object], values)
    if len(raw_values) > _MAX_THREADS:
        raise OperationError("invalid_story_threads")
    result: list[str] = []
    for value in raw_values:
        normalized = _identity(value, "invalid_story_thread", limit=128)
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _effect_value(value: object) -> object:
    """Validate one scalar fiction value; nested/raw evidence is forbidden."""

    if value is None:
        raise OperationError("invalid_story_effect_value")
    if isinstance(value, str):
        if (
            len(value) > _MAX_EFFECT_VALUE
            or any(ord(char) < 32 for char in value)
            or not value.strip()
        ):
            raise OperationError("invalid_story_effect_value")
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > 1_000_000:
            raise OperationError("invalid_story_effect_value")
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > 1_000_000:
            raise OperationError("invalid_story_effect_value")
        return value
    raise OperationError("invalid_story_effect_value")


def _effect_text(value: object, code: str = "invalid_story_effect_value") -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_EFFECT_VALUE:
        raise OperationError(code)
    if any(ord(char) < 32 for char in value):
        raise OperationError(code)
    return value.strip()


@dataclass(frozen=True, slots=True)
class LifeUpdate:
    """One finite-TTL, fiction-only Life state update."""

    key: str
    value: object
    ttl_seconds: float | None = None
    ttl_hours: float | None = None

    def __post_init__(self) -> None:
        key = _identity(self.key, "invalid_story_life_key", limit=64)
        if key in _FORBIDDEN_FICTION_FIELDS:
            raise OperationError("story_factual_effect_forbidden")
        if key not in _LIFE_KEYS:
            raise OperationError("unknown_story_life_key")
        value = _effect_value(self.value)
        has_seconds = self.ttl_seconds is not None
        has_hours = self.ttl_hours is not None
        if has_seconds == has_hours:
            raise OperationError("invalid_story_life_ttl")
        raw_ttl = self.ttl_seconds if has_seconds else self.ttl_hours
        if isinstance(raw_ttl, bool) or not isinstance(raw_ttl, (int, float)):
            raise OperationError("invalid_story_life_ttl")
        ttl = float(raw_ttl) * (3600.0 if has_hours else 1.0)
        if not math.isfinite(ttl) or ttl <= 0 or ttl > _MAX_TTL_SECONDS:
            raise OperationError("invalid_story_life_ttl")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "ttl_seconds", ttl)
        object.__setattr__(self, "ttl_hours", None)

    def to_dict(self) -> dict[str, object]:
        return {"key": self.key, "value": self.value, "ttl_seconds": self.ttl_seconds}


@dataclass(frozen=True, slots=True)
class PartnerUpdate:
    """A patch for a pre-registered fiction partner's mutable runtime state."""

    entity_id: str
    mood: str | None = None
    availability: str | None = None
    current_state: str | None = None
    constraints: tuple[str, ...] | Sequence[str] = ()
    note: str | None = None
    event_note: str | None = None

    def __post_init__(self) -> None:
        entity_id = _identity(self.entity_id, "invalid_story_partner_id", limit=64)
        values: dict[str, str | tuple[str, ...]] = {}
        for field_name in ("mood", "availability", "current_state", "note", "event_note"):
            value = getattr(self, field_name)
            if value is not None:
                values[field_name] = _effect_text(value)
        raw_constraints: object = cast(object, self.constraints)
        if not isinstance(raw_constraints, Sequence) or isinstance(raw_constraints, str | bytes):
            raise OperationError("invalid_story_partner_constraints")
        constraint_values = cast(Sequence[object], raw_constraints)
        if len(constraint_values) > 8:
            raise OperationError("invalid_story_partner_constraints")
        constraints: list[str] = []
        for item in constraint_values:
            text = _effect_text(item, "invalid_story_partner_constraints")
            if text not in constraints:
                constraints.append(text)
        if not values and not constraints:
            raise OperationError("empty_story_partner_update")
        object.__setattr__(self, "entity_id", entity_id)
        object.__setattr__(self, "constraints", tuple(constraints))
        for field_name, value in values.items():
            object.__setattr__(self, field_name, value)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"entity_id": self.entity_id}
        for field_name in ("mood", "availability", "current_state", "note", "event_note"):
            value = getattr(self, field_name)
            if value is not None:
                result[field_name] = value
        if self.constraints:
            result["constraints"] = list(self.constraints)
        return result


def _life_updates(values: object) -> tuple[LifeUpdate, ...]:
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        raise OperationError("invalid_story_life_updates")
    raw_values = cast(Sequence[object], values)
    if len(raw_values) > _MAX_EFFECTS:
        raise OperationError("invalid_story_life_updates")
    result: list[LifeUpdate] = []
    seen: set[str] = set()
    for item in raw_values:
        if isinstance(item, LifeUpdate):
            update = item
        elif isinstance(item, Mapping):
            raw = cast(Mapping[object, object], item)
            unknown = {str(key) for key in raw if str(key) not in _LIFE_EFFECT_KEYS}
            if unknown:
                if unknown & _FORBIDDEN_FICTION_FIELDS:
                    raise OperationError("story_factual_effect_forbidden")
                raise OperationError("unknown_story_effect")
            if "key" not in raw or "value" not in raw:
                raise OperationError("invalid_story_life_update")
            update = LifeUpdate(
                key=cast(str, raw["key"]),
                value=raw["value"],
                ttl_seconds=cast(float | None, raw.get("ttl_seconds")),
                ttl_hours=cast(float | None, raw.get("ttl_hours")),
            )
        else:
            raise OperationError("invalid_story_life_update")
        if update.key in seen:
            raise OperationError("duplicate_story_life_key")
        seen.add(update.key)
        result.append(update)
    return tuple(result)


def _partner_updates(values: object) -> tuple[PartnerUpdate, ...]:
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        raise OperationError("invalid_story_partner_updates")
    raw_values = cast(Sequence[object], values)
    if len(raw_values) > _MAX_EFFECTS:
        raise OperationError("invalid_story_partner_updates")
    result: list[PartnerUpdate] = []
    seen: set[str] = set()
    for item in raw_values:
        if isinstance(item, PartnerUpdate):
            update = item
        elif isinstance(item, Mapping):
            raw = cast(Mapping[object, object], item)
            unknown = {str(key) for key in raw if str(key) not in _PARTNER_EFFECT_KEYS}
            if unknown:
                if unknown & _FORBIDDEN_FICTION_FIELDS:
                    raise OperationError("story_factual_effect_forbidden")
                raise OperationError("unknown_story_effect")
            if "entity_id" not in raw:
                raise OperationError("invalid_story_partner_update")
            constraints = raw.get("constraints", ())
            update = PartnerUpdate(
                entity_id=cast(str, raw["entity_id"]),
                mood=cast(str | None, raw.get("mood")),
                availability=cast(str | None, raw.get("availability")),
                current_state=cast(str | None, raw.get("current_state")),
                constraints=cast(Sequence[str], constraints),
                note=cast(str | None, raw.get("note")),
                event_note=cast(str | None, raw.get("event_note")),
            )
        else:
            raise OperationError("invalid_story_partner_update")
        if update.entity_id in seen:
            raise OperationError("duplicate_story_partner_id")
        seen.add(update.entity_id)
        result.append(update)
    return tuple(result)


def _json(value: object, code: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise OperationError(code) from exc


def _decode(value: object, code: str) -> object:
    if not isinstance(value, str):
        raise OperationError(code)
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise OperationError(code) from exc


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_json(dict(value), "invalid_story_event").encode("utf-8")).hexdigest()


def projection_receipt_id(bot_id: str, event_id: str, target: ProjectionTarget) -> str:
    """Return the stable receipt identity for a single projection target."""

    bot = _identity(bot_id, "invalid_story_bot", limit=64)
    event = _identity(event_id, "invalid_story_event_id")
    if target not in _PROJECTION_TARGETS:
        raise OperationError("invalid_story_projection_target")
    material = "\0".join((bot, event, target)).encode("utf-8")
    return "story_receipt_" + hashlib.sha256(material).hexdigest()


def deterministic_event_id(
    bot_id: str, group_id: str, arc_id: str, decision_digest: str
) -> str:
    """Derive an event identity from a stable external decision identity.

    The digest must come from a proposal/decision ID, sequence, or source
    version supplied by a future caller.  N7 does not derive it from event
    contents, and automated Storylet/Dream/Social callers are not connected.
    """

    bot = _identity(bot_id, "invalid_story_bot", limit=64)
    group = _identity(group_id, "invalid_story_group", limit=64)
    arc = _identity(arc_id, "invalid_story_arc_id")
    digest = _identity(decision_digest, "invalid_story_decision_digest", limit=_MAX_DIGEST)
    if len(digest) != _MAX_DIGEST or any(char not in "0123456789abcdef" for char in digest):
        raise OperationError("invalid_story_decision_digest")
    material = "\0".join((bot, group, arc, digest)).encode("utf-8")
    return "story_event_" + hashlib.sha256(material).hexdigest()


@dataclass(frozen=True, slots=True)
class StoryArcInput:
    """An explicitly authored fiction Arc registration."""

    bot_id: str
    arc_id: str
    role: ArcRole
    group_ids: tuple[str, ...] = ()
    title: str = ""
    stage: str = "active"
    status: ArcStatus = "active"
    variables: Mapping[str, object] = field(default_factory=lambda: dict[str, object]())
    open_threads: tuple[str, ...] = ()
    author: str = ""

    def __post_init__(self) -> None:
        _identity(self.bot_id, "invalid_story_bot", limit=64)
        _identity(self.arc_id, "invalid_story_arc_id")
        if self.role not in _ROLES:
            raise OperationError("invalid_story_role")
        _groups(self.group_ids)
        _title(self.title)
        _identity(self.stage, "invalid_story_stage", limit=64)
        if self.status not in _ARC_STATUSES:
            raise OperationError("invalid_story_status")
        _variables(self.variables)
        _threads(self.open_threads)
        _identity(self.author, "invalid_story_author", limit=_MAX_AUTHOR)


@dataclass(frozen=True, slots=True)
class StoryEventInput:
    """A bounded fiction event; no chat/factual fields exist."""

    event_id: str
    bot_id: str
    group_id: str
    arc_id: str
    source_kind: StorySourceKind
    event_type: str
    variable_deltas: Mapping[str, object] = field(default_factory=lambda: dict[str, object]())
    open_threads: tuple[str, ...] = ()
    resolve_threads: tuple[str, ...] = ()
    stage: str | None = None
    arc_status: ArcStatus | None = None
    author: str = ""
    decision_digest: str = ""
    effects: Mapping[str, object] | None = None
    life_updates: Sequence[LifeUpdate | Mapping[str, object]] = ()
    partner_updates: Sequence[PartnerUpdate | Mapping[str, object]] = ()

    def __post_init__(self) -> None:
        _identity(self.event_id, "invalid_story_event_id")
        _identity(self.bot_id, "invalid_story_bot", limit=64)
        _identity(self.group_id, "invalid_story_group", limit=64)
        _identity(self.arc_id, "invalid_story_arc_id")
        if self.source_kind not in _SOURCE_KINDS:
            raise OperationError("story_source_forbidden")
        _identity(self.event_type, "invalid_story_event_type", limit=_MAX_EVENT_TYPE)
        _deltas(self.variable_deltas)
        opened = _threads(self.open_threads)
        resolved = _threads(self.resolve_threads)
        if set(opened) & set(resolved):
            raise OperationError("invalid_story_threads")
        if self.stage is not None:
            _identity(self.stage, "invalid_story_stage", limit=64)
        if self.arc_status is not None and self.arc_status not in _ARC_STATUSES:
            raise OperationError("invalid_story_status")
        _identity(self.author, "invalid_story_author", limit=_MAX_AUTHOR)
        if self.decision_digest:
            digest = _identity(
                self.decision_digest, "invalid_story_decision_digest", limit=_MAX_DIGEST
            )
            if len(digest) != _MAX_DIGEST or any(
                char not in "0123456789abcdef" for char in digest
            ):
                raise OperationError("invalid_story_decision_digest")
        raw_effects: object = cast(object, self.effects)
        direct_life = _life_updates(self.life_updates)
        direct_partner = _partner_updates(self.partner_updates)
        if raw_effects is not None:
            if not isinstance(raw_effects, Mapping):
                raise OperationError("invalid_story_effects")
            raw_effect_map = cast(Mapping[object, object], raw_effects)
            unknown = {
                str(key)
                for key in raw_effect_map
                if str(key) not in {"life_updates", "partner_updates"}
            }
            if unknown:
                if unknown & _FORBIDDEN_FICTION_FIELDS:
                    raise OperationError("story_factual_effect_forbidden")
                raise OperationError("unknown_story_effect")
            if direct_life or direct_partner:
                raise OperationError("duplicate_story_effect")
            direct_life = _life_updates(raw_effect_map.get("life_updates", ()))
            direct_partner = _partner_updates(raw_effect_map.get("partner_updates", ()))
        object.__setattr__(self, "effects", None)
        object.__setattr__(self, "life_updates", direct_life)
        object.__setattr__(self, "partner_updates", direct_partner)


@dataclass(frozen=True, slots=True)
class StoryArcRecord:
    bot_id: str
    arc_id: str
    role: ArcRole
    title: str
    stage: str
    status: ArcStatus
    variables: Mapping[str, object]
    open_threads: tuple[str, ...]
    group_ids: tuple[str, ...]
    revision: int
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class StoryChatProjection:
    """One frozen chat view and its committed main Arc source."""

    text: str = field(repr=False)
    scope: Scope
    arc_id: str
    arc_revision: int


@dataclass(frozen=True, slots=True)
class FictionPartnerInput:
    """An explicitly registered fiction partner identity for one group."""

    bot_id: str
    group_id: str
    entity_id: str
    display_name: str = ""
    pinned_profile: str = ""
    author: str = ""

    def __post_init__(self) -> None:
        _identity(self.bot_id, "invalid_story_bot", limit=64)
        _identity(self.group_id, "invalid_story_group", limit=64)
        _identity(self.entity_id, "invalid_story_partner_id", limit=64)
        if self.display_name:
            _effect_text(self.display_name, "invalid_story_partner_identity")
        if self.pinned_profile:
            _effect_text(self.pinned_profile, "invalid_story_partner_identity")
        _identity(self.author, "invalid_story_author", limit=_MAX_AUTHOR)


@dataclass(frozen=True, slots=True)
class LifeStateRecord:
    bot_id: str
    group_id: str
    key: str
    value: object
    event_id: str
    expires_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class FictionPartnerStateRecord:
    bot_id: str
    group_id: str
    entity_id: str
    display_name: str
    pinned_profile: str
    mood: str
    availability: str
    current_state: str
    constraints: tuple[str, ...]
    note: str
    event_note: str
    last_event_id: str


@dataclass(frozen=True, slots=True)
class ScheduleFictionSnapshot:
    """Opaque fiction revision used to guard one Schedule commit."""

    bot_id: str
    group_id: str
    now: float
    available: bool
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ScheduleFictionInputs:
    """One owner-consistent, group-scoped fiction read for Schedule."""

    arcs: tuple[StoryArcRecord, ...]
    life: tuple[LifeStateRecord, ...]
    partners: tuple[FictionPartnerStateRecord, ...]
    snapshot: ScheduleFictionSnapshot


@dataclass(frozen=True, slots=True)
class StoryEventRecord:
    event_id: str
    bot_id: str
    group_id: str
    arc_id: str
    source_kind: StorySourceKind
    author: str
    event_type: str
    variable_deltas: Mapping[str, float | int]
    open_threads: tuple[str, ...]
    resolve_threads: tuple[str, ...]
    stage: str | None
    arc_status: ArcStatus | None
    payload_digest: str
    decision_digest: str
    from_revision: int
    to_revision: int
    result_variables: Mapping[str, object]
    result_open_threads: tuple[str, ...]
    result_stage: str
    result_status: ArcStatus
    committed_at: float
    life_updates: tuple[LifeUpdate, ...] = ()
    partner_updates: tuple[PartnerUpdate, ...] = ()
    origin_kind: StoryOriginKind = "admin_authored_fiction"


DreamDecisionStatus = Literal["pending", "validated", "rejected"]


@dataclass(frozen=True, slots=True)
class DreamProposalRecord:
    """Durable Dream proposal and its owner-controlled decision state."""

    bot_id: str
    group_id: str
    proposal_id: str
    target_arc_id: str
    target_arc_revision: int
    source_fingerprint: str
    created_at: float
    proposal_json: Mapping[str, object]
    proposal_digest: str
    decision_status: DreamDecisionStatus
    decision_digest: str | None
    decided_at: float | None
    reason: str | None
    committed_event_id: str | None
    committed_at: float | None


@dataclass(frozen=True, slots=True)
class DreamProposalPage:
    """One bounded page in a stable, exact-scope Dream review queue."""

    proposals: tuple[DreamProposalRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ProjectionIntent:
    event_id: str
    bot_id: str
    group_id: str
    arc_id: str
    target: ProjectionTarget
    intent_digest: str
    status: Literal["pending", "applied"]
    receipt_id: str
    created_at: float
    updated_at: float
    effects: tuple[LifeUpdate | PartnerUpdate, ...] = ()

    @property
    def life_updates(self) -> tuple[LifeUpdate, ...]:
        return tuple(item for item in self.effects if isinstance(item, LifeUpdate))

    @property
    def partner_updates(self) -> tuple[PartnerUpdate, ...]:
        return tuple(item for item in self.effects if isinstance(item, PartnerUpdate))


@dataclass(frozen=True, slots=True)
class ProjectionReceipt:
    receipt_id: str
    event_id: str
    bot_id: str
    group_id: str
    target: ProjectionTarget
    intent_digest: str
    status: Literal["applied"]
    applied_at: float


@dataclass(frozen=True, slots=True)
class StoryCommit:
    event: StoryEventRecord
    arc: StoryArcRecord
    projections: tuple[ProjectionIntent, ...]
    duplicate: bool


def _record_groups(db: StoreConnection, bot_id: str, arc_id: str) -> tuple[str, ...]:
    rows = db.execute(
        "SELECT group_id FROM story_arc_groups WHERE bot_id=? AND arc_id=? ORDER BY group_id",
        (bot_id, arc_id),
    ).fetchall()
    return tuple(_identity(row[0], "invalid_story_group", limit=64) for row in rows)


def _arc_record(db: StoreConnection, row: StoreRow) -> StoryArcRecord:
    role = row["role"]
    status = row["status"]
    revision = row["revision"]
    if role not in _ROLES or status not in _ARC_STATUSES or type(revision) is not int or revision < 0:
        raise OperationError("invalid_story_arc")
    values = _decode(row["variables"], "invalid_story_variables")
    threads = _decode(row["open_threads"], "invalid_story_threads")
    if not isinstance(values, dict) or not isinstance(threads, list):
        raise OperationError("invalid_story_arc")
    decoded_values = cast(dict[object, object], values)
    decoded_threads = cast(list[object], threads)
    normalized_values = _variables(decoded_values)
    normalized_threads = _threads(decoded_threads)
    created_at = _timestamp(row["created_at"])
    updated_at = _timestamp(row["updated_at"])
    return StoryArcRecord(
        bot_id=_identity(row["bot_id"], "invalid_story_bot", limit=64),
        arc_id=_identity(row["arc_id"], "invalid_story_arc_id"),
        role=cast(ArcRole, role),
        title=_title(row["title"]),
        stage=_identity(row["stage"], "invalid_story_stage", limit=64),
        status=cast(ArcStatus, status),
        variables=normalized_values,
        open_threads=normalized_threads,
        group_ids=_record_groups(db, str(row["bot_id"]), str(row["arc_id"])),
        revision=revision,
        created_at=created_at,
        updated_at=updated_at,
    )


def _event_record(row: StoreRow) -> StoryEventRecord:
    deltas = _decode(row["variable_deltas"], "invalid_story_deltas")
    opened = _decode(row["open_threads"], "invalid_story_threads")
    resolved = _decode(row["resolve_threads"], "invalid_story_threads")
    result_variables = _decode(row["result_variables"], "invalid_story_variables")
    result_threads = _decode(row["result_open_threads"], "invalid_story_threads")
    row_keys = set(row.keys())
    raw_life_updates = _decode(
        row["life_updates"] if "life_updates" in row_keys else "[]",
        "invalid_story_life_updates",
    )
    raw_partner_updates = _decode(
        row["partner_updates"] if "partner_updates" in row_keys else "[]",
        "invalid_story_partner_updates",
    )
    raw_origin_kind = row["origin_kind"] if "origin_kind" in row_keys else "admin_authored_fiction"
    if (
        not isinstance(deltas, dict)
        or not isinstance(opened, list)
        or not isinstance(resolved, list)
        or not isinstance(result_variables, dict)
        or not isinstance(result_threads, list)
        or not isinstance(raw_life_updates, list)
        or not isinstance(raw_partner_updates, list)
    ):
        raise OperationError("invalid_story_event")
    decoded_deltas = cast(dict[object, object], deltas)
    decoded_opened = cast(list[object], opened)
    decoded_resolved = cast(list[object], resolved)
    decoded_result_variables = cast(dict[object, object], result_variables)
    decoded_result_threads = cast(list[object], result_threads)
    source = row["source_kind"]
    arc_status = row["arc_status"]
    from_revision = row["from_revision"]
    to_revision = row["to_revision"]
    if (
        source not in _SOURCE_KINDS
        or (arc_status is not None and arc_status not in _ARC_STATUSES)
        or row["result_status"] not in _ARC_STATUSES
        or type(from_revision) is not int
        or type(to_revision) is not int
        or from_revision < 0
        or to_revision != from_revision + 1
        or raw_origin_kind not in _ORIGIN_KINDS
    ):
        raise OperationError("invalid_story_event")
    raw_decision_digest = row["decision_digest"]
    if raw_decision_digest in (None, ""):
        decision_digest = ""
    else:
        decision_digest = _identity(
            raw_decision_digest, "invalid_story_decision_digest", limit=_MAX_DIGEST
        )
        if len(decision_digest) != _MAX_DIGEST or any(
            char not in "0123456789abcdef" for char in decision_digest
        ):
            raise OperationError("invalid_story_decision_digest")
    return StoryEventRecord(
        event_id=_identity(row["event_id"], "invalid_story_event_id"),
        bot_id=_identity(row["bot_id"], "invalid_story_bot", limit=64),
        group_id=_identity(row["group_id"], "invalid_story_group", limit=64),
        arc_id=_identity(row["arc_id"], "invalid_story_arc_id"),
        source_kind=cast(StorySourceKind, source),
        author=_identity(row["author"], "invalid_story_author", limit=_MAX_AUTHOR),
        event_type=_identity(row["event_type"], "invalid_story_event_type", limit=_MAX_EVENT_TYPE),
        variable_deltas=_deltas(decoded_deltas),
        open_threads=_threads(decoded_opened),
        resolve_threads=_threads(decoded_resolved),
        stage=None if row["stage"] is None else _identity(row["stage"], "invalid_story_stage", limit=64),
        arc_status=None if arc_status is None else cast(ArcStatus, arc_status),
        payload_digest=_identity(row["payload_digest"], "invalid_story_digest", limit=64),
        decision_digest=decision_digest,
        from_revision=from_revision,
        to_revision=to_revision,
        result_variables=_variables(decoded_result_variables),
        result_open_threads=_threads(decoded_result_threads),
        result_stage=_identity(row["result_stage"], "invalid_story_stage", limit=64),
        result_status=cast(ArcStatus, row["result_status"]),
        committed_at=_timestamp(row["committed_at"]),
        life_updates=_life_updates(cast(list[object], raw_life_updates)),
        partner_updates=_partner_updates(cast(list[object], raw_partner_updates)),
        origin_kind=cast(StoryOriginKind, raw_origin_kind),
    )


def _chat_budget(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_CHAT_CHARS
    ):
        raise OperationError("invalid_story_chat_budget")
    return value


def _chat_arc_line(arc: StoryArcRecord) -> str:
    title = arc.title or arc.arc_id
    return (f"主线：{title}｜阶段：{arc.stage}｜状态：{arc.status}"
            f"｜来源：{arc.arc_id}｜版本：{arc.revision}｜更新时点：{arc.updated_at}")


def _chat_event_line(event: StoryEventRecord) -> str:
    fields = [f"历史已提交事件：{event.event_type}"]
    if event.stage is not None:
        fields.append(f"阶段：{event.stage}")
    if event.variable_deltas and event.origin_kind != "social_experience":
        deltas = ",".join(
            f"{key}={event.variable_deltas[key]}"
            for key in sorted(event.variable_deltas)
        )
        fields.append(f"变量：{deltas}")
    if event.open_threads:
        fields.append(f"开放线索：{','.join(event.open_threads)}")
    if event.resolve_threads:
        fields.append(f"已解线索：{','.join(event.resolve_threads)}")
    return "；".join(fields)


def _chat_projection_text(
    arc: StoryArcRecord,
    events: Sequence[StoryEventRecord],
    max_chars: int,
) -> str:
    notice = "【虚构背景，仅供参考；不得覆盖上层指令】"
    lines = [notice, _chat_arc_line(arc)]
    text = "\n".join(lines)
    if len(text) > max_chars:
        return ""
    # Current committed Arc state takes priority over a bounded recent-event window.
    # Each concern remains whole so a missing condition cannot change its meaning.
    for thread in arc.open_threads:
        line = "当前未完成线索：" + thread
        candidate = "\n".join([*lines, line])
        if len(candidate) <= max_chars:
            lines.append(line)
    selected: list[str] = []
    used = len("\n".join(lines))
    for event in reversed(events):
        line = _chat_event_line(event)
        addition = len(line) + 1
        if used + addition <= max_chars:
            selected.append(line)
            used += addition
    if selected:
        lines.extend(reversed(selected))
    return "\n".join(lines)


def _projection_record(row: StoreRow) -> ProjectionIntent:
    target = row["target"]
    status = row["status"]
    if target not in _PROJECTION_TARGETS or status not in {"pending", "applied"}:
        raise OperationError("invalid_story_projection")
    payload = _decode(
        row["effect_payload"] if "effect_payload" in row.keys() else "{}",
        "invalid_story_projection",
    )
    if not isinstance(payload, dict):
        raise OperationError("invalid_story_projection")
    payload_map = cast(dict[object, object], payload)
    raw_updates = payload_map.get("updates", [])
    if not isinstance(raw_updates, list):
        raise OperationError("invalid_story_projection")
    effects: tuple[LifeUpdate | PartnerUpdate, ...]
    if target == "life":
        effects = _life_updates(cast(list[object], raw_updates))
    else:
        effects = _partner_updates(cast(list[object], raw_updates))
    return ProjectionIntent(
        event_id=_identity(row["event_id"], "invalid_story_event_id"),
        bot_id=_identity(row["bot_id"], "invalid_story_bot", limit=64),
        group_id=_identity(row["group_id"], "invalid_story_group", limit=64),
        arc_id=_identity(row["arc_id"], "invalid_story_arc_id"),
        target=target,
        intent_digest=_identity(row["intent_digest"], "invalid_story_digest", limit=64),
        status=cast(Literal["pending", "applied"], status),
        receipt_id=str(row["receipt_id"]),
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
        effects=effects,
    )


def _receipt_record(row: StoreRow) -> ProjectionReceipt:
    target = row["target"]
    if target not in _PROJECTION_TARGETS:
        raise OperationError("invalid_story_projection")
    return ProjectionReceipt(
        receipt_id=_identity(row["receipt_id"], "invalid_story_receipt", limit=128),
        event_id=_identity(row["event_id"], "invalid_story_event_id"),
        bot_id=_identity(row["bot_id"], "invalid_story_bot", limit=64),
        group_id=_identity(row["group_id"], "invalid_story_group", limit=64),
        target=target,
        intent_digest=_identity(row["intent_digest"], "invalid_story_digest", limit=64),
        status="applied",
        applied_at=_timestamp(row["applied_at"]),
    )


def _life_state_record(row: StoreRow) -> LifeStateRecord:
    value = _decode(row["value"], "invalid_story_life_state")
    # Life state values intentionally stay scalar, just like effect values.
    value = _effect_value(value)
    return LifeStateRecord(
        bot_id=_identity(row["bot_id"], "invalid_story_bot", limit=64),
        group_id=_identity(row["group_id"], "invalid_story_group", limit=64),
        key=_identity(row["key"], "invalid_story_life_key", limit=64),
        value=value,
        event_id=_identity(row["event_id"], "invalid_story_event_id"),
        expires_at=_timestamp(row["expires_at"], "invalid_story_life_ttl"),
        updated_at=_timestamp(row["updated_at"]),
    )


def _partner_state_record(row: StoreRow) -> FictionPartnerStateRecord:
    if row["identity_kind"] != "fiction":
        raise OperationError("story_partner_identity_forbidden")
    constraints = _decode(row["constraints"], "invalid_story_partner_constraints")
    if not isinstance(constraints, list):
        raise OperationError("invalid_story_partner_constraints")
    normalized_constraints = tuple(
        _effect_text(item, "invalid_story_partner_constraints")
        for item in cast(list[object], constraints)
    )
    return FictionPartnerStateRecord(
        bot_id=_identity(row["bot_id"], "invalid_story_bot", limit=64),
        group_id=_identity(row["group_id"], "invalid_story_group", limit=64),
        entity_id=_identity(row["entity_id"], "invalid_story_partner_id", limit=64),
        display_name=str(row["display_name"]),
        pinned_profile=str(row["pinned_profile"]),
        mood=str(row["mood"]),
        availability=str(row["availability"]),
        current_state=str(row["current_state"]),
        constraints=normalized_constraints,
        note=str(row["note"]),
        event_note=str(row["event_note"]),
        last_event_id=str(row["last_event_id"]),
    )


def _schedule_fiction_material(
    scope: Scope,
    now: float,
    available: bool,
    arcs: tuple[StoryArcRecord, ...],
    life: tuple[LifeStateRecord, ...],
    partners: tuple[FictionPartnerStateRecord, ...],
) -> dict[str, object]:
    return {
        "bot_id": scope.bot_id,
        "group_id": scope.group_id,
        "now": now,
        "available": available,
        "arcs": [
            {
                "arc_id": item.arc_id,
                "role": item.role,
                "title": item.title,
                "stage": item.stage,
                "status": item.status,
                "variables": item.variables,
                "open_threads": item.open_threads,
                "revision": item.revision,
                "updated_at": item.updated_at,
            }
            for item in arcs
        ],
        "life": [
            {
                "key": item.key,
                "value": item.value,
                "event_id": item.event_id,
                "expires_at": item.expires_at,
                "updated_at": item.updated_at,
            }
            for item in life
        ],
        "partners": [
            {
                "entity_id": item.entity_id,
                "display_name": item.display_name,
                "pinned_profile": item.pinned_profile,
                "mood": item.mood,
                "availability": item.availability,
                "current_state": item.current_state,
                "constraints": item.constraints,
                "note": item.note,
                "event_note": item.event_note,
                "last_event_id": item.last_event_id,
            }
            for item in partners
        ],
    }


def _social_effects_transaction(
    db: StoreConnection,
    scope: Scope,
    *,
    now: float,
    authorize_social: SocialAuthorizer | None,
    prevalidated: frozenset[str] = frozenset(),
) -> tuple[dict[str, int], LifeStateRecord | None]:
    """Derive current Bot-self Social effects from current, linked evidence."""

    if authorize_social is None:
        return {}, None
    rows = db.execute(
        "SELECT l.arc_id,l.experience_id,l.event_id,e.committed_at,e.commit_seq,"
        "e.variable_deltas,e.life_updates "
        "FROM story_social_experience_links l "
        "JOIN story_events e ON e.bot_id=l.bot_id AND e.event_id=l.event_id "
        "WHERE l.bot_id=? AND l.group_id=? AND e.group_id=? "
        "AND e.source_kind=? AND e.origin_kind=? ORDER BY e.commit_seq,l.experience_id",
        (
            scope.bot_id,
            scope.group_id,
            scope.group_id,
            _SOCIAL_SOURCE_KIND,
            "social_experience",
        ),
    ).fetchall()
    counts: dict[str, int] = {}
    latest_afterglow: tuple[int, float] | None = None
    expected_deltas = {_SOCIAL_RESONANCE_KEY: 0.05}
    expected_life_updates = (
        LifeUpdate(
            key=_SOCIAL_AFTERGLOW_KEY,
            value=_SOCIAL_AFTERGLOW_VALUE,
            ttl_seconds=_SOCIAL_AFTERGLOW_TTL_SECONDS,
        ),
    )
    for row in rows:
        raw_deltas = _deltas(_decode(row["variable_deltas"], "invalid_story_deltas"))
        raw_life = _life_updates(_decode(row["life_updates"], "invalid_story_life_updates"))
        if raw_deltas != expected_deltas or raw_life != expected_life_updates:
            continue
        experience_id = _identity(row["experience_id"], "invalid_social_experience_id")
        if experience_id in prevalidated:
            authorized = True
        else:
            try:
                authorized = authorize_social(db, scope, experience_id)
            except OperationError as exc:
                if exc.code in _SOCIAL_NONCURRENT_CODES:
                    continue
                raise
        if authorized is False:
            continue
        arc_id = _identity(row["arc_id"], "invalid_story_arc_id")
        _identity(row["event_id"], "invalid_story_event_id")
        committed_at = _timestamp(row["committed_at"])
        sequence = row["commit_seq"]
        if type(sequence) is not int or sequence < 1:
            raise OperationError("invalid_story_sequence")
        counts[arc_id] = counts.get(arc_id, 0) + 1
        expires_at = committed_at + _SOCIAL_AFTERGLOW_TTL_SECONDS
        if expires_at > now and (
            latest_afterglow is None or sequence > latest_afterglow[0]
        ):
            latest_afterglow = (sequence, committed_at)
    if latest_afterglow is None:
        return counts, None
    _sequence, committed_at = latest_afterglow
    return counts, LifeStateRecord(
        bot_id=scope.bot_id,
        group_id=scope.group_id,
        key=_SOCIAL_AFTERGLOW_KEY,
        value=_SOCIAL_AFTERGLOW_VALUE,
        # Schedule gets a stable projection identity, never a source/event ID.
        event_id="social_afterglow_projection",
        expires_at=committed_at + _SOCIAL_AFTERGLOW_TTL_SECONDS,
        updated_at=committed_at,
    )


def _project_social_resonance(
    arc: StoryArcRecord, *, active_sources: int
) -> StoryArcRecord:
    variables = dict(arc.variables)
    # This reserved variable is virtual: the persisted Arc keeps the base
    # state, while current Social links contribute only in this read projection.
    variables.pop(_SOCIAL_RESONANCE_KEY, None)
    if active_sources:
        variables[_SOCIAL_RESONANCE_KEY] = min(1.0, 0.05 * active_sources)
    return replace(arc, variables=variables)


def _schedule_fiction_inputs_transaction(
    db: StoreConnection,
    scope: Scope,
    *,
    now: float,
    available: bool,
    authorize_social: SocialAuthorizer | None,
) -> ScheduleFictionInputs:
    empty_arcs: tuple[StoryArcRecord, ...] = ()
    empty_life: tuple[LifeStateRecord, ...] = ()
    empty_partners: tuple[FictionPartnerStateRecord, ...] = ()
    if not available:
        material = _schedule_fiction_material(
            scope, now, False, empty_arcs, empty_life, empty_partners
        )
        return ScheduleFictionInputs(
            arcs=empty_arcs,
            life=empty_life,
            partners=empty_partners,
            snapshot=ScheduleFictionSnapshot(
                bot_id=scope.bot_id,
                group_id=scope.group_id,
                now=now,
                available=False,
                fingerprint=_digest(material),
            ),
        )

    arc_rows = db.execute(
        "SELECT a.* FROM story_arcs a JOIN story_arc_groups g "
        "ON g.bot_id=a.bot_id AND g.arc_id=a.arc_id "
        "WHERE a.bot_id=? AND g.group_id=? AND a.status='active' "
        "ORDER BY CASE a.role WHEN 'main' THEN 0 WHEN 'side' THEN 1 ELSE 2 END,a.arc_id",
        (scope.bot_id, scope.group_id),
    ).fetchall()
    effects_by_arc, social_afterglow = _social_effects_transaction(
        db, scope, now=now, authorize_social=authorize_social
    )
    visible_arcs = tuple(
        _project_social_resonance(
            _arc_record(db, row), active_sources=effects_by_arc.get(str(row["arc_id"]), 0)
        )
        for row in arc_rows
    )
    mains = tuple(item for item in visible_arcs if item.role == "main")
    if len(mains) > 1:
        raise OperationError("story_main_ambiguous")
    arcs = visible_arcs if mains else empty_arcs
    life_rows = db.execute(
        "SELECT * FROM story_life_states WHERE bot_id=? AND group_id=? AND expires_at>? "
        "AND key<>? ORDER BY key",
        (scope.bot_id, scope.group_id, now, _SOCIAL_AFTERGLOW_KEY),
    ).fetchall()
    life_values = [_life_state_record(row) for row in life_rows]
    if social_afterglow is not None:
        life_values.append(social_afterglow)
    life = tuple(sorted(life_values, key=lambda item: item.key))
    partner_rows = db.execute(
        "SELECT * FROM story_partner_states WHERE bot_id=? AND group_id=? "
        "AND identity_kind='fiction' ORDER BY entity_id",
        (scope.bot_id, scope.group_id),
    ).fetchall()
    partners = tuple(_partner_state_record(row) for row in partner_rows)
    material = _schedule_fiction_material(scope, now, True, arcs, life, partners)
    return ScheduleFictionInputs(
        arcs=arcs,
        life=life,
        partners=partners,
        snapshot=ScheduleFictionSnapshot(
            bot_id=scope.bot_id,
            group_id=scope.group_id,
            now=now,
            available=True,
            fingerprint=_digest(material),
        ),
    )


def _storylet_state_payload(state: StoryletState) -> dict[str, object]:
    """Encode the pure Worldbook budget state for the StoryArc owner."""

    return {
        "triggered": list(state.triggered),
        "cooldowns": dict(state.cooldowns),
        "available_at": dict(state.available_at),
        "setback_count": state.setback_count,
        "recovery_until": state.recovery_until,
        "events_step": state.events_step,
        "events_used": state.events_used,
        "max_events_per_step": state.max_events_per_step,
        "max_setbacks": state.max_setbacks,
    }


def _storylet_state_from_row(row: StoreRow) -> StoryletState:
    """Decode and revalidate one persisted Storylet projection state."""

    from .worldbook import StoryletState as RuntimeStoryletState

    raw = _decode(row["state_json"], "invalid_storylet_state")
    if not isinstance(raw, dict):
        raise OperationError("invalid_storylet_state")
    values = cast(dict[object, object], raw)
    expected_fields = {
        "triggered",
        "cooldowns",
        "available_at",
        "setback_count",
        "recovery_until",
        "events_step",
        "events_used",
        "max_events_per_step",
        "max_setbacks",
    }
    if set(values) != expected_fields:
        raise OperationError("invalid_storylet_state")
    triggered_raw = values["triggered"]
    cooldowns_raw = values["cooldowns"]
    available_at_raw = values["available_at"]
    if (
        not isinstance(triggered_raw, list)
        or not isinstance(cooldowns_raw, dict)
        or not isinstance(available_at_raw, dict)
    ):
        raise OperationError("invalid_storylet_state")
    triggered = cast(list[object], triggered_raw)
    cooldowns = cast(dict[object, object], cooldowns_raw)
    available_at = cast(dict[object, object], available_at_raw)
    if (
        any(not isinstance(item, str) for item in triggered)
        or any(type(key) is not str or type(item) is not int for key, item in cooldowns.items())
        or any(
            type(key) is not str or type(item) is not int
            for key, item in available_at.items()
        )
    ):
        raise OperationError("invalid_storylet_state")
    for key in (
        "setback_count",
        "recovery_until",
        "events_step",
        "events_used",
        "max_events_per_step",
        "max_setbacks",
    ):
        if type(values[key]) is not int:
            raise OperationError("invalid_storylet_state")
    try:
        return RuntimeStoryletState(
            triggered=tuple(cast(str, item) for item in triggered),
            cooldowns=cast(Mapping[str, int], cooldowns),
            available_at=cast(Mapping[str, int], available_at),
            setback_count=cast(int, values["setback_count"]),
            recovery_until=cast(int, values["recovery_until"]),
            events_step=cast(int, values["events_step"]),
            events_used=cast(int, values["events_used"]),
            max_events_per_step=cast(int, values["max_events_per_step"]),
            max_setbacks=cast(int, values["max_setbacks"]),
        )
    except (OperationError, TypeError, ValueError) as exc:
        raise OperationError("invalid_storylet_state") from exc


def _storylet_plain(value: object) -> object:
    if isinstance(value, Mapping):
        mapped = cast(Mapping[object, object], value)
        return {str(key): _storylet_plain(item) for key, item in mapped.items()}
    if isinstance(value, (tuple, list)):
        sequence = cast(Sequence[object], value)
        return [_storylet_plain(item) for item in sequence]
    return value


def _storylet_proposal_material(proposal: StoryletProposal) -> dict[str, object]:
    return {
        "proposal_id": proposal.proposal_id,
        "scope": (proposal.scope.bot_id, proposal.scope.group_id),
        "target_arc_id": proposal.target_arc_id,
        "target_arc_revision": proposal.target_arc_revision,
        "storylet_id": proposal.storylet_id,
        "step": proposal.step,
        "source_kind": proposal.source_kind,
        "source_fingerprint": proposal.source_fingerprint,
        "consequence": _storylet_plain(proposal.consequence),
    }


def _storylet_event_id(proposal: StoryletProposal) -> str:
    identity_digest = _digest(
        {
            "kind": "storylet",
            "proposal": _storylet_proposal_material(proposal),
        }
    )
    return deterministic_event_id(
        proposal.scope.bot_id,
        proposal.scope.group_id,
        proposal.target_arc_id,
        identity_digest,
    )


def _storylet_decision_digest(
    proposal: StoryletProposal, next_state: StoryletState
) -> str:
    return _digest(
        {
            "kind": "storylet",
            "proposal": _storylet_proposal_material(proposal),
            "next_state": _storylet_state_payload(next_state),
        }
    )


def _dream_proposal_material(proposal: DreamProposal) -> dict[str, object]:
    return {
        "proposal_id": proposal.proposal_id,
        "scope": (proposal.scope.bot_id, proposal.scope.group_id),
        "target_arc_id": proposal.target_arc_id,
        "target_arc_revision": proposal.target_arc_revision,
        "source_fingerprint": proposal.source_fingerprint,
        "created_at": proposal.created_at,
        "payload": _storylet_plain(proposal.payload),
        "source_kind": proposal.source_kind,
        "source_domain": proposal.source_domain,
        "kind": proposal.kind,
        "summary": proposal.summary,
    }


def _dream_proposal_digest(proposal: DreamProposal) -> str:
    return _digest(_dream_proposal_material(proposal))


def _dream_candidate_material(candidate: FictionCandidate) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "proposal_id": candidate.proposal_id,
        "scope": (candidate.scope.bot_id, candidate.scope.group_id),
        "target_arc_id": candidate.target_arc_id,
        "target_arc_revision": candidate.target_arc_revision,
        "source_kind": candidate.source_kind,
        "source_fingerprint": candidate.source_fingerprint,
        "created_at": candidate.created_at,
        "kind": candidate.kind,
        "summary": candidate.summary,
        "payload": _storylet_plain(candidate.payload),
    }


def _dream_decision_digest(
    proposal_digest: str,
    *,
    source_fingerprint: str,
    candidate: FictionCandidate | None = None,
    reason: str = "",
) -> str:
    material: dict[str, object] = {
        "proposal_digest": proposal_digest,
        "source_fingerprint": source_fingerprint,
        "status": "validated" if candidate is not None else "rejected",
        "reason": reason,
    }
    if candidate is not None:
        material["candidate"] = _dream_candidate_material(candidate)
    return _digest(material)


def _dream_digest_text(value: object, code: str) -> str:
    result = _identity(value, code, limit=_MAX_DIGEST)
    if len(result) != _MAX_DIGEST or any(char not in "0123456789abcdef" for char in result):
        raise OperationError(code)
    return result


def _dream_record(row: StoreRow) -> DreamProposalRecord:
    proposal_json = _decode(row["proposal_json"], "dream_proposal_tampered")
    if not isinstance(proposal_json, dict):
        raise OperationError("dream_proposal_tampered")
    payload = cast(dict[object, object], proposal_json)
    bot_id = _identity(row["bot_id"], "dream_scope_denied", limit=64)
    group_id = _identity(row["group_id"], "dream_scope_denied", limit=64)
    proposal_id = _identity(row["proposal_id"], "dream_proposal_tampered")
    target_arc_id = _identity(row["target_arc_id"], "dream_proposal_tampered")
    target_revision = row["target_arc_revision"]
    if type(target_revision) is not int or target_revision < 0:
        raise OperationError("dream_proposal_tampered")
    source_fingerprint = _identity(
        row["source_fingerprint"], "dream_source_tampered", limit=_MAX_ID
    )
    created_at = _timestamp(row["created_at"], "dream_proposal_tampered")
    proposal_digest = _dream_digest_text(row["proposal_digest"], "dream_proposal_tampered")
    status = row["decision_status"]
    if status not in {"pending", "validated", "rejected"}:
        raise OperationError("dream_proposal_tampered")
    raw_decision_digest = row["decision_digest"]
    decision_digest = (
        None
        if raw_decision_digest is None
        else _dream_digest_text(raw_decision_digest, "dream_decision_tampered")
    )
    raw_decided_at = row["decided_at"]
    decided_at = (
        None
        if raw_decided_at is None
        else _timestamp(raw_decided_at, "dream_decision_tampered")
    )
    reason = row["reason"]
    if reason is not None:
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > _MAX_EFFECT_VALUE
        ):
            raise OperationError("dream_decision_tampered")
    raw_committed_event = row["committed_event_id"]
    committed_event_id = (
        None
        if raw_committed_event is None
        else _identity(raw_committed_event, "dream_commit_tampered")
    )
    raw_committed_at = row["committed_at"]
    committed_at = (
        None
        if raw_committed_at is None
        else _timestamp(raw_committed_at, "dream_commit_tampered")
    )
    if status == "pending":
        if any(
            value is not None
            for value in (decision_digest, decided_at, reason, committed_event_id, committed_at)
        ):
            raise OperationError("dream_proposal_tampered")
    elif status == "validated":
        if decision_digest is None or decided_at is None:
            raise OperationError("dream_decision_tampered")
        if (committed_event_id is None) != (committed_at is None):
            raise OperationError("dream_commit_tampered")
    elif (
        decision_digest is None
        or decided_at is None
        or reason is None
        or committed_event_id is not None
        or committed_at is not None
    ):
        raise OperationError("dream_decision_tampered")
    return DreamProposalRecord(
        bot_id=bot_id,
        group_id=group_id,
        proposal_id=proposal_id,
        target_arc_id=target_arc_id,
        target_arc_revision=target_revision,
        source_fingerprint=source_fingerprint,
        created_at=created_at,
        proposal_json=cast(Mapping[str, object], payload),
        proposal_digest=proposal_digest,
        decision_status=cast(DreamDecisionStatus, status),
        decision_digest=decision_digest,
        decided_at=decided_at,
        reason=reason,
        committed_event_id=committed_event_id,
        committed_at=committed_at,
    )


def _dream_proposal_from_record(record: DreamProposalRecord) -> DreamProposal:
    from .worldbook import DreamProposal as RuntimeDreamProposal

    values = record.proposal_json
    expected = {
        "proposal_id",
        "scope",
        "target_arc_id",
        "target_arc_revision",
        "source_fingerprint",
        "created_at",
        "payload",
        "source_kind",
        "source_domain",
        "kind",
        "summary",
    }
    if set(values) != expected:
        raise OperationError("dream_proposal_tampered")
    raw_scope = values["scope"]
    if not isinstance(raw_scope, (list, tuple)):
        raise OperationError("dream_proposal_tampered")
    scope_values = cast(Sequence[object], raw_scope)
    if len(scope_values) != 2:
        raise OperationError("dream_proposal_tampered")
    try:
        proposal = RuntimeDreamProposal(
            proposal_id=cast(str, values["proposal_id"]),
            scope=Scope(
                bot_id=cast(str, scope_values[0]),
                group_id=cast(str, scope_values[1]),
            ),
            target_arc_id=cast(str, values["target_arc_id"]),
            target_arc_revision=cast(int, values["target_arc_revision"]),
            source_fingerprint=cast(str, values["source_fingerprint"]),
            created_at=cast(float, values["created_at"]),
            payload=cast(Mapping[str, object], values["payload"]),
            source_kind=cast(str, values["source_kind"]),
            source_domain=cast(str, values["source_domain"]),
            kind=cast(str, values["kind"]),
            summary=cast(str, values["summary"]),
        )
    except (OperationError, TypeError, ValueError) as exc:
        raise OperationError("dream_proposal_tampered") from exc
    if (
        proposal.scope.bot_id != record.bot_id
        or proposal.scope.group_id != record.group_id
        or proposal.proposal_id != record.proposal_id
        or proposal.target_arc_id != record.target_arc_id
        or proposal.target_arc_revision != record.target_arc_revision
        or proposal.source_fingerprint != record.source_fingerprint
        or proposal.created_at != record.created_at
        or _dream_proposal_digest(proposal) != record.proposal_digest
    ):
        raise OperationError("dream_proposal_tampered")
    return proposal


def _dream_event_from_candidate(
    candidate: FictionCandidate, decision_digest: str
) -> tuple[StoryEventInput, str, dict[str, float | int], tuple[str, ...], tuple[str, ...]]:
    consequence = dict(candidate.payload)
    allowed = {"variable_deltas", "open_threads", "resolve_threads", "stage", "arc_status"}
    if set(consequence) - allowed:
        raise OperationError("dream_effect_forbidden")
    deltas = _deltas(consequence.get("variable_deltas", {}))
    opened = _threads(consequence.get("open_threads", ()))
    resolved = _threads(consequence.get("resolve_threads", ()))
    event = StoryEventInput(
        event_id=deterministic_event_id(
            candidate.scope.bot_id,
            candidate.scope.group_id,
            candidate.target_arc_id,
            decision_digest,
        ),
        bot_id=candidate.scope.bot_id,
        group_id=candidate.scope.group_id,
        arc_id=candidate.target_arc_id,
        source_kind=_SOURCE_KIND,
        event_type=candidate.kind,
        variable_deltas=deltas,
        open_threads=opened,
        resolve_threads=resolved,
        stage=cast(str | None, consequence.get("stage")),
        arc_status=cast(ArcStatus | None, consequence.get("arc_status")),
        author="dream",
        decision_digest=decision_digest,
    )
    payload_digest = _digest(
        {
            "event_id": event.event_id,
            "bot_id": event.bot_id,
            "group_id": event.group_id,
            "arc_id": event.arc_id,
            "source_kind": event.source_kind,
            "author": event.author,
            "event_type": event.event_type,
            "variable_deltas": deltas,
            "open_threads": opened,
            "resolve_threads": resolved,
            "stage": event.stage,
            "arc_status": event.arc_status,
            "decision_digest": decision_digest,
        }
    )
    return event, payload_digest, deltas, opened, resolved


def _dream_candidate_from_proposal(proposal: DreamProposal) -> FictionCandidate:
    from .worldbook import FictionCandidate as RuntimeFictionCandidate

    return RuntimeFictionCandidate(
        candidate_id="",
        proposal_id=proposal.proposal_id,
        scope=proposal.scope,
        target_arc_id=proposal.target_arc_id,
        target_arc_revision=proposal.target_arc_revision,
        source_kind="dream_proposal",
        source_fingerprint=proposal.source_fingerprint,
        created_at=proposal.created_at,
        kind=proposal.kind,
        summary=proposal.summary,
        payload=proposal.payload,
    )


class StoryArcStore:
    """The one fiction StoryArc owner over the shared SQLite Store."""

    def __init__(
        self,
        store: Store,
        *,
        enabled: bool = False,
        assembled: bool = False,
        authorize_admin: AdminAuthorizer | None = None,
        admin_authorizer: AdminAuthorizer | None = None,
        authorize_storylet: StoryletAuthorizer | None = None,
        approved_storylet_engine: StoryletEngine | None = None,
        authorize_dream: DreamAuthorizer | None = None,
        authorize_social: SocialAuthorizer | None = None,
        approved_dream_validator: DreamValidator | None = None,
        approved_dream_source_fingerprint: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (
            authorize_admin is not None
            and admin_authorizer is not None
            and authorize_admin is not admin_authorizer
        ):
            raise ValueError("provide one admin authorizer")
        engine_policy: tuple[str, bool, tuple[str, ...], int] | None = None
        if approved_storylet_engine is not None:
            engine_registry = getattr(approved_storylet_engine, "registry", None)
            engine_fingerprint = getattr(engine_registry, "fingerprint", None)
            engine_enabled = getattr(approved_storylet_engine, "enabled", None)
            engine_groups = getattr(approved_storylet_engine, "allowed_groups", None)
            engine_max_setbacks = getattr(approved_storylet_engine, "max_setbacks_per_arc", None)
            if (
                not isinstance(engine_fingerprint, str)
                or type(engine_enabled) is not bool
                or not isinstance(engine_groups, tuple)
                or type(engine_max_setbacks) is not int
                or engine_max_setbacks < 0
            ):
                raise ValueError("invalid approved storylet engine")
            frozen_groups = cast(tuple[str, ...], engine_groups)
            engine_policy = (
                engine_fingerprint,
                engine_enabled,
                frozen_groups,
                engine_max_setbacks,
            )
        self.store = store
        self.enabled = enabled
        self.assembled = assembled
        self._authorize_admin = authorize_admin or admin_authorizer
        self._authorize_storylet = authorize_storylet
        self._approved_storylet_policy = engine_policy
        self._authorize_dream = authorize_dream
        self._authorize_social = authorize_social
        self._approved_dream_validator = approved_dream_validator
        self._approved_dream_source_fingerprint = (
            None
            if approved_dream_source_fingerprint is None
            else _identity(
                approved_dream_source_fingerprint,
                "invalid_dream_source",
                limit=_MAX_ID,
            )
        )
        self._clock = clock

    def _check_bot(self, bot_id: str) -> None:
        if self.store.bot_id is not None and bot_id != self.store.bot_id:
            raise OperationError("story_scope_denied")

    def _available_read(self) -> bool:
        return self.enabled and self.assembled

    def _require_write(self) -> None:
        if not self.enabled:
            raise OperationError("story_disabled")
        if not self.assembled:
            raise OperationError("story_unassembled")
        if self._authorize_admin is None:
            raise OperationError("story_admin_authorization_required")

    def _require_storylet_write(self) -> None:
        if not self.enabled:
            raise OperationError("story_disabled")
        if not self.assembled:
            raise OperationError("story_unassembled")
        if self._authorize_storylet is None:
            raise OperationError("storylet_authorization_required")
        if self._approved_storylet_policy is None:
            raise OperationError("storylet_registry_unapproved")

    def _require_social_write(self) -> None:
        if not self.enabled:
            raise OperationError("story_disabled")
        if not self.assembled:
            raise OperationError("story_unassembled")
        if self._authorize_social is None:
            raise OperationError("story_social_authorization_required")

    def _require_dream_write(self) -> tuple[DreamValidator, str]:
        if not self.enabled:
            raise OperationError("story_disabled")
        if not self.assembled:
            raise OperationError("story_unassembled")
        if self._authorize_dream is None:
            raise OperationError("dream_authorization_required")
        approved = self._approved_dream_validator
        source = self._approved_dream_source_fingerprint
        if approved is None or source is None:
            raise OperationError("dream_validator_unapproved")
        from .worldbook import DreamValidator as RuntimeDreamValidator

        if type(approved) is not RuntimeDreamValidator:
            raise OperationError("dream_validator_unapproved")
        return approved, source

    def _require_projection(self) -> None:
        if not self.enabled:
            raise OperationError("story_disabled")
        if not self.assembled:
            raise OperationError("story_unassembled")

    def _now(self, value: float | None) -> float:
        return _timestamp(self._clock() if value is None else value)

    async def schedule_fiction_inputs(
        self, scope: Scope, *, now: float | None = None
    ) -> ScheduleFictionInputs:
        """Read the bounded fiction inputs Schedule may consume in one transaction."""

        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        timestamp = self._now(now)
        available = self._available_read()
        if not available:
            empty_arcs: tuple[StoryArcRecord, ...] = ()
            empty_life: tuple[LifeStateRecord, ...] = ()
            empty_partners: tuple[FictionPartnerStateRecord, ...] = ()
            fingerprint = _digest(
                _schedule_fiction_material(
                    checked_scope,
                    timestamp,
                    False,
                    empty_arcs,
                    empty_life,
                    empty_partners,
                )
            )
            return ScheduleFictionInputs(
                arcs=empty_arcs,
                life=empty_life,
                partners=empty_partners,
                snapshot=ScheduleFictionSnapshot(
                    bot_id=checked_scope.bot_id,
                    group_id=checked_scope.group_id,
                    now=timestamp,
                    available=False,
                    fingerprint=fingerprint,
                ),
            )

        def read(db: StoreConnection) -> ScheduleFictionInputs:
            return _schedule_fiction_inputs_transaction(
                db,
                checked_scope,
                now=timestamp,
                available=True,
                authorize_social=self._authorize_social,
            )

        return await self.store.transaction(read)

    def assert_schedule_fiction_snapshot(
        self,
        db: StoreConnection,
        scope: Scope,
        snapshot: ScheduleFictionSnapshot,
        *,
        now: float | None = None,
    ) -> None:
        """Assert Schedule's owner-read snapshot inside its open Store transaction."""

        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        if type(snapshot) is not ScheduleFictionSnapshot:
            raise OperationError("invalid_story_schedule_snapshot")
        if (snapshot.bot_id, snapshot.group_id) != (
            checked_scope.bot_id,
            checked_scope.group_id,
        ):
            raise OperationError("story_scope_denied")
        timestamp = self._now(snapshot.now if now is None else now)
        current = _schedule_fiction_inputs_transaction(
            db,
            checked_scope,
            now=timestamp,
            available=self._available_read(),
            authorize_social=self._authorize_social,
        )
        if current.snapshot.fingerprint != snapshot.fingerprint:
            raise OperationError("schedule_fiction_revision_conflict")

    async def register_arc(
        self,
        arc: StoryArcInput,
        *,
        now: float | None = None,
        require_unique_main: bool = False,
    ) -> StoryArcRecord:
        """Register one explicitly authored Arc and its exact group allowlist."""

        self._require_write()
        if type(arc) is not StoryArcInput:
            raise OperationError("invalid_story_arc")
        if type(require_unique_main) is not bool:
            raise OperationError("invalid_story_arc")
        self._check_bot(arc.bot_id)
        _identity(arc.author, "invalid_story_author", limit=_MAX_AUTHOR)
        groups = _groups(arc.group_ids)
        variables = _variables(arc.variables)
        if _SOCIAL_RESONANCE_KEY in variables:
            raise OperationError("story_social_variable_reserved")
        threads = _threads(arc.open_threads)
        timestamp = self._now(now)
        authorizer = self._authorize_admin
        if authorizer is None:
            raise OperationError("story_admin_authorization_required")

        def commit(db: StoreConnection) -> StoryArcRecord:
            authorized = authorizer(db)
            if authorized is False:
                raise OperationError("story_admin_authorization_denied")
            existing = db.execute(
                "SELECT * FROM story_arcs WHERE bot_id=? AND arc_id=?", (arc.bot_id, arc.arc_id)
            ).fetchone()
            if existing is not None:
                current = _arc_record(db, existing)
                if (
                    current.role != arc.role
                    or current.title != _title(arc.title)
                    or current.stage != arc.stage
                    or current.status != arc.status
                    or dict(current.variables) != variables
                    or current.open_threads != threads
                    or current.group_ids != tuple(sorted(groups))
                ):
                    raise OperationError("story_arc_conflict")
                return current
            if require_unique_main and arc.role == "main" and arc.status == "active":
                # New Web registrations preserve the one-main invariant inside
                # the transaction; old ambiguous records remain readable as
                # a fail-closed migration case.
                for group_id in groups:
                    other_main = db.execute(
                        "SELECT 1 FROM story_arcs a JOIN story_arc_groups g "
                        "ON g.bot_id=a.bot_id AND g.arc_id=a.arc_id "
                        "WHERE a.bot_id=? AND g.group_id=? AND a.role='main' "
                        "AND a.status='active' LIMIT 1",
                        (arc.bot_id, group_id),
                    ).fetchone()
                    if other_main is not None:
                        raise OperationError("story_main_ambiguous")
            db.execute(
                "INSERT INTO story_arcs(bot_id,arc_id,role,title,stage,status,variables,open_threads,"
                "revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    arc.bot_id,
                    arc.arc_id,
                    arc.role,
                    _title(arc.title),
                    arc.stage,
                    arc.status,
                    _json(variables, "invalid_story_variables"),
                    _json(list(threads), "invalid_story_threads"),
                    0,
                    timestamp,
                    timestamp,
                ),
            )
            for group_id in groups:
                db.execute(
                    "INSERT INTO story_arc_groups(bot_id,arc_id,group_id) VALUES (?,?,?)",
                    (arc.bot_id, arc.arc_id, group_id),
                )
            row = db.execute(
                "SELECT * FROM story_arcs WHERE bot_id=? AND arc_id=?", (arc.bot_id, arc.arc_id)
            ).fetchone()
            if row is None:
                raise OperationError("invalid_story_arc")
            return _arc_record(db, row)

        return await self.store.transaction(commit)

    async def register_partner(
        self, partner: FictionPartnerInput, *, now: float | None = None
    ) -> FictionPartnerStateRecord:
        """Register one fiction-only partner identity before it can be patched."""

        self._require_write()
        if type(partner) is not FictionPartnerInput:
            raise OperationError("invalid_story_partner")
        self._check_bot(partner.bot_id)
        timestamp = self._now(now)
        authorizer = self._authorize_admin
        if authorizer is None:
            raise OperationError("story_admin_authorization_required")

        def commit(db: StoreConnection) -> FictionPartnerStateRecord:
            authorized = authorizer(db)
            if authorized is False:
                raise OperationError("story_admin_authorization_denied")
            existing = db.execute(
                "SELECT * FROM story_partner_states WHERE bot_id=? AND group_id=? AND entity_id=?",
                (partner.bot_id, partner.group_id, partner.entity_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["identity_kind"] != "fiction"
                    or str(existing["display_name"]) != partner.display_name
                    or str(existing["pinned_profile"]) != partner.pinned_profile
                ):
                    raise OperationError("story_partner_conflict")
                return _partner_state_record(existing)
            db.execute(
                "INSERT INTO story_partner_states(bot_id,group_id,entity_id,identity_kind,"
                "display_name,pinned_profile,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    partner.bot_id,
                    partner.group_id,
                    partner.entity_id,
                    "fiction",
                    partner.display_name,
                    partner.pinned_profile,
                    timestamp,
                    timestamp,
                ),
            )
            row = db.execute(
                "SELECT * FROM story_partner_states WHERE bot_id=? AND group_id=? AND entity_id=?",
                (partner.bot_id, partner.group_id, partner.entity_id),
            ).fetchone()
            if row is None:
                raise OperationError("invalid_story_partner")
            return _partner_state_record(row)

        return await self.store.transaction(commit)

    async def read_life_state(
        self,
        scope: Scope,
        *,
        key: str | None = None,
        now: float | None = None,
    ) -> tuple[LifeStateRecord, ...]:
        """Read unexpired fiction Life state for exactly one group."""

        if not self._available_read():
            return ()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        if key is not None:
            _identity(key, "invalid_story_life_key", limit=64)
            if key not in _LIFE_KEYS:
                raise OperationError("unknown_story_life_key")
        timestamp = self._now(now)

        def read(db: StoreConnection) -> tuple[LifeStateRecord, ...]:
            query = (
                "SELECT * FROM story_life_states WHERE bot_id=? AND group_id=? "
                "AND expires_at>? AND key<>?"
            )
            params: list[object] = [
                checked_scope.bot_id,
                checked_scope.group_id,
                timestamp,
                _SOCIAL_AFTERGLOW_KEY,
            ]
            if key is not None:
                query += " AND key=?"
                params.append(key)
            query += " ORDER BY key"
            rows = db.execute(query, tuple(params)).fetchall()
            values = [_life_state_record(row) for row in rows]
            if key in {None, _SOCIAL_AFTERGLOW_KEY}:
                _effects_by_arc, social_afterglow = _social_effects_transaction(
                    db, checked_scope, now=timestamp, authorize_social=self._authorize_social
                )
                if social_afterglow is not None:
                    values.append(social_afterglow)
            return tuple(sorted(values, key=lambda item: item.key))

        return await self.store.transaction(read)

    async def read_partner_states(
        self, scope: Scope, *, entity_id: str | None = None
    ) -> tuple[FictionPartnerStateRecord, ...]:
        """Read only pre-registered fiction partner state in one group."""

        if not self._available_read():
            return ()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        if entity_id is not None:
            _identity(entity_id, "invalid_story_partner_id", limit=64)

        def read(db: StoreConnection) -> tuple[FictionPartnerStateRecord, ...]:
            query = (
                "SELECT * FROM story_partner_states WHERE bot_id=? AND group_id=? "
                "AND identity_kind='fiction'"
            )
            params: list[object] = [checked_scope.bot_id, checked_scope.group_id]
            if entity_id is not None:
                query += " AND entity_id=?"
                params.append(entity_id)
            query += " ORDER BY entity_id"
            rows = db.execute(query, tuple(params)).fetchall()
            return tuple(_partner_state_record(row) for row in rows)

        return await self.store.transaction(read)

    async def read_arc(self, scope: Scope, arc_id: str) -> StoryArcRecord | None:
        if not self._available_read():
            return None
        checked_scope = _scope(scope)
        _identity(arc_id, "invalid_story_arc_id")
        self._check_bot(checked_scope.bot_id)
        timestamp = self._now(None)

        def read(db: StoreConnection) -> StoryArcRecord | None:
            row = db.execute(
                "SELECT a.* FROM story_arcs a JOIN story_arc_groups g "
                "ON g.bot_id=a.bot_id AND g.arc_id=a.arc_id "
                "WHERE a.bot_id=? AND a.arc_id=? AND g.group_id=?",
                (checked_scope.bot_id, arc_id, checked_scope.group_id),
            ).fetchone()
            if row is not None:
                arc = _arc_record(db, row)
                effects_by_arc, _afterglow = _social_effects_transaction(
                    db,
                    checked_scope,
                    now=timestamp,
                    authorize_social=self._authorize_social,
                )
                return _project_social_resonance(
                    arc, active_sources=effects_by_arc.get(arc.arc_id, 0)
                )
            known = db.execute(
                "SELECT 1 FROM story_arcs WHERE bot_id=? AND arc_id=?",
                (checked_scope.bot_id, arc_id),
            ).fetchone()
            if known is not None:
                raise OperationError("story_scope_denied")
            other_bot = db.execute(
                "SELECT 1 FROM story_arcs WHERE bot_id<>? LIMIT 1", (checked_scope.bot_id,)
            ).fetchone()
            if other_bot is not None:
                raise OperationError("story_scope_denied")
            return None

        return await self.store.transaction(read)

    async def visible_stack(self, scope: Scope) -> tuple[StoryArcRecord, ...]:
        """Return the visible stack, rejecting zero or multiple explicit mains."""

        if not self._available_read():
            return ()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        timestamp = self._now(None)

        def read(db: StoreConnection) -> tuple[StoryArcRecord, ...]:
            rows = db.execute(
                "SELECT a.* FROM story_arcs a JOIN story_arc_groups g "
                "ON g.bot_id=a.bot_id AND g.arc_id=a.arc_id "
                "WHERE a.bot_id=? AND g.group_id=? AND a.status='active' "
                "ORDER BY CASE a.role WHEN 'main' THEN 0 WHEN 'side' THEN 1 ELSE 2 END,a.arc_id",
                (checked_scope.bot_id, checked_scope.group_id),
            ).fetchall()
            effects_by_arc, _afterglow = _social_effects_transaction(
                db,
                checked_scope,
                now=timestamp,
                authorize_social=self._authorize_social,
            )
            arcs = tuple(
                _project_social_resonance(
                    _arc_record(db, row),
                    active_sources=effects_by_arc.get(str(row["arc_id"]), 0),
                )
                for row in rows
            )
            mains = tuple(arc for arc in arcs if arc.role == "main")
            if not mains:
                other_bot = db.execute(
                    "SELECT 1 FROM story_arcs WHERE bot_id<>? LIMIT 1", (checked_scope.bot_id,)
                ).fetchone()
                if other_bot is not None:
                    raise OperationError("story_scope_denied")
                raise OperationError("story_main_missing")
            if len(mains) > 1:
                raise OperationError("story_main_ambiguous")
            return arcs

        return await self.store.transaction(read)

    def _commit_event_transaction(
        self,
        db: StoreConnection,
        event: StoryEventInput,
        *,
        expected_revision: int,
        timestamp: float,
        author: str,
        deltas: Mapping[str, float | int],
        opened: Sequence[str],
        resolved: Sequence[str],
        life_updates: Sequence[LifeUpdate],
        partner_updates: Sequence[PartnerUpdate],
        payload_digest: str,
        decision_digest: str,
        origin_kind: StoryOriginKind,
        authorizer: Callable[[StoreConnection], object],
        cancel_requested: Callable[[], bool] | None = None,
    ) -> StoryCommit:
        def check_cancel() -> None:
            if cancel_requested is not None and cancel_requested():
                raise asyncio.CancelledError

        authorized = authorizer(db)
        if authorized is False:
            if origin_kind == "storylet":
                raise OperationError("storylet_authorization_denied")
            if origin_kind == "dream_proposal":
                raise OperationError("dream_authorization_denied")
            raise OperationError("story_admin_authorization_denied")
        if origin_kind != "social_experience":
            if _SOCIAL_RESONANCE_KEY in deltas:
                raise OperationError("story_social_variable_reserved")
            if any(update.key == _SOCIAL_AFTERGLOW_KEY for update in life_updates):
                raise OperationError("story_social_life_key_reserved")
        check_cancel()
        existing_event = db.execute(
            "SELECT * FROM story_events WHERE bot_id=? AND event_id=?",
            (event.bot_id, event.event_id),
        ).fetchone()
        if existing_event is not None:
            stored_event = _event_record(existing_event)
            if (
                stored_event.origin_kind != origin_kind
                or stored_event.source_kind != event.source_kind
                or str(existing_event["payload_digest"]) != payload_digest
                or (origin_kind == "storylet" and stored_event.decision_digest != decision_digest)
            ):
                raise OperationError("idempotency_conflict")
            row = db.execute(
                "SELECT * FROM story_arcs WHERE bot_id=? AND arc_id=?",
                (event.bot_id, event.arc_id),
            ).fetchone()
            if row is None or stored_event.arc_id != event.arc_id:
                raise OperationError("story_scope_denied")
            current_arc = _arc_record(db, row)
            if event.group_id not in current_arc.group_ids:
                raise OperationError("story_scope_denied")
            arc_record = replace(
                current_arc,
                revision=stored_event.to_revision,
                stage=stored_event.result_stage,
                status=stored_event.result_status,
                variables=dict(stored_event.result_variables),
                open_threads=stored_event.result_open_threads,
                updated_at=stored_event.committed_at,
            )
            projection_rows = db.execute(
                "SELECT * FROM story_projection_outbox WHERE bot_id=? AND event_id=? "
                "ORDER BY CASE target WHEN 'life' THEN 0 ELSE 1 END",
                (event.bot_id, event.event_id),
            ).fetchall()
            return StoryCommit(
                event=stored_event,
                arc=arc_record,
                projections=tuple(_projection_record(item) for item in projection_rows),
                duplicate=True,
            )

        arc_row = db.execute(
            "SELECT * FROM story_arcs WHERE bot_id=? AND arc_id=?",
            (event.bot_id, event.arc_id),
        ).fetchone()
        if arc_row is None:
            raise OperationError("story_arc_missing")
        arc_record = _arc_record(db, arc_row)
        if event.group_id not in arc_record.group_ids:
            raise OperationError("story_scope_denied")
        if arc_record.status != "active":
            raise OperationError("story_arc_closed")
        if arc_record.revision != expected_revision:
            raise OperationError("story_revision_conflict")

        variables = dict(arc_record.variables)
        if origin_kind != "social_experience":
            for key, delta in deltas.items():
                current = variables.get(key, 0)
                if isinstance(current, bool) or not isinstance(current, (int, float)):
                    raise OperationError("invalid_story_variables")
                updated = float(current) + float(delta)
                if not math.isfinite(updated) or abs(updated) > 1_000_000_000:
                    raise OperationError("invalid_story_variables")
                variables[key] = int(updated) if updated.is_integer() else updated
        # A Social event keeps its fixed delta and 24-hour Life intent in the
        # event row for history. Current values are projected from active links;
        # neither the Arc aggregate nor the normal Life/partner outbox is changed.
        threads = [thread for thread in arc_record.open_threads if thread not in set(resolved)]
        for thread in opened:
            if thread not in threads:
                threads.append(thread)
        next_stage = arc_record.stage if event.stage is None else event.stage
        next_status = arc_record.status if event.arc_status is None else event.arc_status
        next_revision = arc_record.revision + 1
        updated = db.execute(
            "UPDATE story_arcs SET stage=?,status=?,variables=?,open_threads=?,revision=?,updated_at=? "
            "WHERE bot_id=? AND arc_id=? AND revision=?",
            (
                next_stage,
                next_status,
                _json(variables, "invalid_story_variables"),
                _json(threads, "invalid_story_threads"),
                next_revision,
                timestamp,
                event.bot_id,
                event.arc_id,
                expected_revision,
            ),
        )
        if updated.rowcount != 1:
            raise OperationError("story_revision_conflict")
        check_cancel()
        sequence_row = db.execute(
            "SELECT COALESCE(MAX(commit_seq),0)+1 FROM story_events"
        ).fetchone()
        if sequence_row is None or type(sequence_row[0]) is not int or sequence_row[0] < 1:
            raise OperationError("invalid_story_sequence")
        commit_seq = sequence_row[0]
        db.execute(
            "INSERT INTO story_events(bot_id,event_id,arc_id,group_id,source_kind,origin_kind,"
            "author,event_type,"
            "variable_deltas,open_threads,resolve_threads,stage,arc_status,payload_digest,decision_digest,"
            "from_revision,to_revision,result_variables,result_open_threads,result_stage,result_status,"
            "life_updates,partner_updates,committed_at,commit_seq) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event.bot_id,
                event.event_id,
                event.arc_id,
                event.group_id,
                event.source_kind,
                origin_kind,
                author,
                event.event_type,
                _json(deltas, "invalid_story_deltas"),
                _json(list(opened), "invalid_story_threads"),
                _json(list(resolved), "invalid_story_threads"),
                event.stage,
                event.arc_status,
                payload_digest,
                decision_digest,
                expected_revision,
                next_revision,
                _json(variables, "invalid_story_variables"),
                _json(threads, "invalid_story_threads"),
                next_stage,
                next_status,
                _json(
                    [update.to_dict() for update in life_updates],
                    "invalid_story_life_updates",
                ),
                _json(
                    [update.to_dict() for update in partner_updates],
                    "invalid_story_partner_updates",
                ),
                timestamp,
                commit_seq,
            ),
        )
        check_cancel()
        target_effects: list[tuple[ProjectionTarget, Sequence[LifeUpdate | PartnerUpdate]]] = []
        if origin_kind != "social_experience":
            if life_updates:
                target_effects.append(("life", life_updates))
            if partner_updates:
                target_effects.append(("partner", partner_updates))
        for target, effects in target_effects:
            effect_payload = {
                "decision_digest": decision_digest,
                "updates": [item.to_dict() for item in effects],
            }
            intent_digest = _digest(
                {
                    "bot_id": event.bot_id,
                    "group_id": event.group_id,
                    "arc_id": event.arc_id,
                    "event_id": event.event_id,
                    "target": target,
                    "effect_payload": effect_payload,
                }
            )
            db.execute(
                "INSERT INTO story_projection_outbox(bot_id,event_id,arc_id,group_id,target,"
                "effect_payload,intent_digest,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    event.bot_id,
                    event.event_id,
                    event.arc_id,
                    event.group_id,
                    target,
                    _json(effect_payload, "invalid_story_projection"),
                    intent_digest,
                    "pending",
                    timestamp,
                    timestamp,
                ),
            )
        check_cancel()
        fresh_arc = db.execute(
            "SELECT * FROM story_arcs WHERE bot_id=? AND arc_id=?",
            (event.bot_id, event.arc_id),
        ).fetchone()
        fresh_event = db.execute(
            "SELECT * FROM story_events WHERE bot_id=? AND event_id=?",
            (event.bot_id, event.event_id),
        ).fetchone()
        projections = db.execute(
            "SELECT * FROM story_projection_outbox WHERE bot_id=? AND event_id=? "
            "ORDER BY CASE target WHEN 'life' THEN 0 ELSE 1 END",
            (event.bot_id, event.event_id),
        ).fetchall()
        if fresh_arc is None or fresh_event is None:
            raise OperationError("invalid_story_commit")
        check_cancel()
        return StoryCommit(
            event=_event_record(fresh_event),
            arc=_arc_record(db, fresh_arc),
            projections=tuple(_projection_record(item) for item in projections),
            duplicate=False,
        )

    async def commit_event(
        self,
        event: StoryEventInput,
        *,
        expected_revision: int,
        now: float | None = None,
    ) -> StoryCommit:
        """CAS-commit one fiction event and enqueue only real target effects."""

        self._require_write()
        if type(event) is not StoryEventInput:
            raise OperationError("invalid_story_event")
        if event.source_kind != _SOURCE_KIND:
            raise OperationError("story_source_forbidden")
        self._check_bot(event.bot_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise OperationError("story_revision_conflict")
        author = _identity(event.author, "invalid_story_author", limit=_MAX_AUTHOR)
        deltas = _deltas(event.variable_deltas)
        opened = _threads(event.open_threads)
        resolved = _threads(event.resolve_threads)
        life_updates = _life_updates(event.life_updates)
        partner_updates = _partner_updates(event.partner_updates)
        if (life_updates or partner_updates) and not event.decision_digest:
            raise OperationError("story_decision_required")
        timestamp = self._now(now)
        payload: dict[str, object] = {
            "event_id": event.event_id,
            "bot_id": event.bot_id,
            "group_id": event.group_id,
            "arc_id": event.arc_id,
            "source_kind": event.source_kind,
            "author": author,
            "event_type": event.event_type,
            "variable_deltas": deltas,
            "open_threads": opened,
            "resolve_threads": resolved,
            "stage": event.stage,
            "arc_status": event.arc_status,
            "decision_digest": event.decision_digest,
        }
        if life_updates:
            payload["life_updates"] = [update.to_dict() for update in life_updates]
        if partner_updates:
            payload["partner_updates"] = [update.to_dict() for update in partner_updates]
        payload_digest = _digest(payload)
        decision_digest = event.decision_digest
        authorizer = self._authorize_admin
        if authorizer is None:
            raise OperationError("story_admin_authorization_required")
        return await self.store.transaction(
            lambda db: self._commit_event_transaction(
                db,
                event,
                expected_revision=expected_revision,
                timestamp=timestamp,
                author=author,
                deltas=deltas,
                opened=opened,
                resolved=resolved,
                life_updates=life_updates,
                partner_updates=partner_updates,
                payload_digest=payload_digest,
                decision_digest=decision_digest,
                origin_kind="admin_authored_fiction",
                authorizer=authorizer,
            )
        )

    async def commit_social_experience(
        self,
        scope: Scope,
        experience_id: str,
        *,
        expected_revision: int | None = None,
    ) -> StoryCommit:
        """Commit one generic Bot-self fiction event for current Social evidence."""

        self._require_social_write()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        experience_id = _identity(experience_id, "invalid_social_experience_id")
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 0
        ):
            raise OperationError("story_revision_conflict")
        authorizer = self._authorize_social
        if authorizer is None:
            raise OperationError("story_social_authorization_required")
        timestamp = self._now(None)

        def commit(db: StoreConnection) -> StoryCommit:
            authorized = authorizer(db, checked_scope, experience_id)
            if authorized is False:
                raise OperationError("story_social_authorization_denied")
            evidence = db.execute(
                "SELECT platform_message_id FROM social_experiences WHERE experience_id=? "
                "AND bot_id=? AND group_id=? AND status='active'",
                (experience_id, checked_scope.bot_id, checked_scope.group_id),
            ).fetchone()
            if evidence is None:
                raise OperationError("social_experience_not_current")
            platform_message_id = _identity(
                evidence["platform_message_id"], "social_source_metadata_missing"
            )
            link = db.execute(
                "SELECT event_id,arc_id FROM story_social_experience_links "
                "WHERE bot_id=? AND group_id=? AND experience_id=?",
                (checked_scope.bot_id, checked_scope.group_id, experience_id),
            ).fetchone()
            if link is None:
                main_rows = db.execute(
                    "SELECT a.* FROM story_arcs a JOIN story_arc_groups g "
                    "ON g.bot_id=a.bot_id AND g.arc_id=a.arc_id "
                    "WHERE a.bot_id=? AND g.group_id=? AND a.role='main' "
                    "AND a.status='active' ORDER BY a.arc_id",
                    (checked_scope.bot_id, checked_scope.group_id),
                ).fetchall()
                if not main_rows:
                    raise OperationError("story_main_missing")
                if len(main_rows) != 1:
                    raise OperationError("story_main_ambiguous")
                main = _arc_record(db, main_rows[0])
                arc_id = main.arc_id
                commit_revision = (
                    main.revision
                    if expected_revision is None
                    else expected_revision
                )
            else:
                arc_id = _identity(link["arc_id"], "invalid_story_arc_id")
                previous = db.execute(
                    "SELECT from_revision FROM story_events WHERE bot_id=? AND event_id=?",
                    (checked_scope.bot_id, link["event_id"]),
                ).fetchone()
                if previous is None:
                    raise OperationError("story_social_experience_conflict")
                commit_revision = int(previous["from_revision"])
            material = "\0".join(
                (
                    checked_scope.bot_id,
                    checked_scope.group_id,
                    experience_id,
                    platform_message_id,
                    arc_id,
                )
            ).encode("utf-8")
            event_id = "social_" + hashlib.sha256(material).hexdigest()
            if link is not None and str(link["event_id"]) != event_id:
                raise OperationError("story_social_experience_conflict")
            event = StoryEventInput(
                event_id=event_id,
                bot_id=checked_scope.bot_id,
                group_id=checked_scope.group_id,
                arc_id=arc_id,
                source_kind=_SOCIAL_SOURCE_KIND,
                event_type="交流后整理自身想法",
                author="bot",
            )
            deltas = {_SOCIAL_RESONANCE_KEY: 0.05}
            life_updates = (
                LifeUpdate(
                    key=_SOCIAL_AFTERGLOW_KEY,
                    value=_SOCIAL_AFTERGLOW_VALUE,
                    ttl_seconds=_SOCIAL_AFTERGLOW_TTL_SECONDS,
                ),
            )
            payload_material: dict[str, object] = {
                "event_id": event_id,
                "experience_id": experience_id,
                "bot_id": checked_scope.bot_id,
                "group_id": checked_scope.group_id,
                "arc_id": arc_id,
                "source_kind": _SOCIAL_SOURCE_KIND,
                "origin_kind": "social_experience",
                "event_type": "交流后整理自身想法",
                "author": "bot",
            }
            legacy_payload_digest = _digest(payload_material)
            payload_material["variable_deltas"] = deltas
            payload_material["life_updates"] = [
                update.to_dict() for update in life_updates
            ]
            payload_digest = _digest(payload_material)
            if link is not None:
                previous = db.execute(
                    "SELECT variable_deltas,life_updates FROM story_events "
                    "WHERE bot_id=? AND event_id=?",
                    (checked_scope.bot_id, event_id),
                ).fetchone()
                if previous is None:
                    raise OperationError("story_social_experience_conflict")
                previous_deltas = _deltas(
                    _decode(previous["variable_deltas"], "invalid_story_deltas")
                )
                previous_life = _life_updates(
                    _decode(previous["life_updates"], "invalid_story_life_updates")
                )
                if not previous_deltas and not previous_life:
                    payload_digest = legacy_payload_digest
                elif previous_deltas != deltas or previous_life != life_updates:
                    raise OperationError("story_social_experience_conflict")
            result = self._commit_event_transaction(
                db,
                event,
                expected_revision=commit_revision,
                timestamp=timestamp,
                author="bot",
                deltas=deltas,
                opened=(),
                resolved=(),
                life_updates=life_updates,
                partner_updates=(),
                payload_digest=payload_digest,
                decision_digest="",
                origin_kind="social_experience",
                authorizer=lambda _transaction: True,
            )
            if link is None:
                db.execute(
                    "INSERT INTO story_social_experience_links(bot_id,group_id,experience_id,"
                    "event_id,arc_id,created_at) VALUES (?,?,?,?,?,?)",
                    (
                        checked_scope.bot_id,
                        checked_scope.group_id,
                        experience_id,
                        event_id,
                        arc_id,
                        timestamp,
                    ),
                )
            effects_by_arc, _afterglow = _social_effects_transaction(
                db,
                checked_scope,
                now=timestamp,
                authorize_social=authorizer,
                prevalidated=frozenset({experience_id}),
            )
            return replace(
                result,
                arc=_project_social_resonance(
                    result.arc, active_sources=effects_by_arc.get(arc_id, 0)
                ),
            )

        return await self.store.transaction(commit)

    async def commit_storylet(
        self,
        scope: Scope,
        engine: StoryletEngine,
        *,
        arc_id: str,
        step: int,
        evidence: Sequence[str] = (),
        preview: StoryletPlan | StoryletProposal | None = None,
        expected_arc_revision: int | None = None,
        now: float | None = None,
    ) -> StoryCommit:
        """Replan and atomically commit one approved Storylet transition.

        ``preview`` is advisory.  The owner re-reads the Arc and persisted
        Storylet state in the same SQLite transaction, then plans again.  Its
        ``next_state`` is written only after the Arc event succeeds.  A caller
        that performed a history preflight may also pin the observed Arc
        revision; that comparison happens before planning, including when the
        plan would contain no candidate.
        """

        from .worldbook import StoryletEngine as RuntimeStoryletEngine
        from .worldbook import StoryletPlan as RuntimeStoryletPlan
        from .worldbook import StoryletProposal as RuntimeStoryletProposal
        from .worldbook import StoryletState as RuntimeStoryletState

        self._require_storylet_write()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        _identity(arc_id, "invalid_story_arc_id")
        if type(engine) is not RuntimeStoryletEngine:
            raise OperationError("invalid_storylet_engine")
        if not engine.enabled or checked_scope.group_id not in engine.allowed_groups:
            raise OperationError("storylet_scope_denied")
        if evidence:
            raise OperationError("storylet_evidence_unavailable")
        if expected_arc_revision is not None and (
            type(expected_arc_revision) is not int or expected_arc_revision < 0
        ):
            raise OperationError("invalid_story_arc_revision")
        if preview is not None and type(preview) not in {
            RuntimeStoryletPlan,
            RuntimeStoryletProposal,
        }:
            raise OperationError("invalid_storylet_preview")
        preview_proposal = None if preview is None else (
            preview.proposal if isinstance(preview, RuntimeStoryletPlan) else preview
        )
        registry_fingerprint = engine.registry.fingerprint
        if (
            len(registry_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in registry_fingerprint)
        ):
            raise OperationError("invalid_storylet_registry")
        approved_policy = self._approved_storylet_policy
        if approved_policy is None or (
            registry_fingerprint,
            engine.enabled,
            tuple(engine.allowed_groups),
            engine.max_setbacks_per_arc,
        ) != approved_policy:
            raise OperationError("storylet_registry_conflict")
        if preview_proposal is not None:
            if preview_proposal.source_fingerprint != registry_fingerprint:
                raise OperationError("storylet_registry_conflict")
            if preview_proposal.scope != checked_scope or preview_proposal.target_arc_id != arc_id:
                raise OperationError("storylet_proposal_mismatch")
        authorizer = self._authorize_storylet
        if authorizer is None:
            raise OperationError("storylet_authorization_required")
        timestamp = self._now(now)
        owner_task = asyncio.current_task()

        def cancel_requested() -> bool:
            return owner_task is not None and owner_task.cancelling() > 0

        def ensure_main(db: StoreConnection) -> None:
            rows = db.execute(
                "SELECT a.role FROM story_arcs a JOIN story_arc_groups g "
                "ON g.bot_id=a.bot_id AND g.arc_id=a.arc_id "
                "WHERE a.bot_id=? AND g.group_id=? AND a.status='active'",
                (checked_scope.bot_id, checked_scope.group_id),
            ).fetchall()
            mains = [row[0] for row in rows if row[0] == "main"]
            if not mains:
                raise OperationError("story_main_missing")
            if len(mains) > 1:
                raise OperationError("story_main_ambiguous")

        def ensure_fiction_partners(
            db: StoreConnection, updates: Sequence[PartnerUpdate]
        ) -> None:
            for update in updates:
                registered = db.execute(
                    "SELECT 1 FROM story_partner_states WHERE bot_id=? AND group_id=? "
                    "AND entity_id=? AND identity_kind='fiction'",
                    (checked_scope.bot_id, checked_scope.group_id, update.entity_id),
                ).fetchone()
                if registered is None:
                    raise OperationError("story_partner_missing")

        def event_from_proposal(
            proposal: StoryletProposal,
            decision_digest: str,
        ) -> tuple[StoryEventInput, str, dict[str, float | int], tuple[str, ...], tuple[str, ...]]:
            consequence = dict(proposal.consequence)
            allowed = {
                "variable_deltas",
                "open_threads",
                "resolve_threads",
                "stage",
                "arc_status",
                "life_updates",
                "partner_updates",
            }
            if set(consequence) - allowed:
                raise OperationError("storylet_effect_forbidden")
            deltas = _deltas(consequence.get("variable_deltas", {}))
            opened = _threads(consequence.get("open_threads", ()))
            resolved = _threads(consequence.get("resolve_threads", ()))
            life_updates = _life_updates(consequence.get("life_updates", ()))
            partner_updates = _partner_updates(consequence.get("partner_updates", ()))
            event = StoryEventInput(
                event_id=_storylet_event_id(proposal),
                bot_id=checked_scope.bot_id,
                group_id=checked_scope.group_id,
                arc_id=arc_id,
                source_kind=_SOURCE_KIND,
                event_type="storylet",
                variable_deltas=deltas,
                open_threads=opened,
                resolve_threads=resolved,
                stage=cast(str | None, consequence.get("stage")),
                arc_status=cast(ArcStatus | None, consequence.get("arc_status")),
                author="storylet",
                decision_digest=decision_digest,
                life_updates=life_updates,
                partner_updates=partner_updates,
            )
            payload: dict[str, object] = {
                "event_id": event.event_id,
                "bot_id": event.bot_id,
                "group_id": event.group_id,
                "arc_id": event.arc_id,
                "source_kind": event.source_kind,
                "author": event.author,
                "event_type": event.event_type,
                "variable_deltas": deltas,
                "open_threads": opened,
                "resolve_threads": resolved,
                "stage": event.stage,
                "arc_status": event.arc_status,
                "decision_digest": decision_digest,
            }
            if life_updates:
                payload["life_updates"] = [update.to_dict() for update in life_updates]
            if partner_updates:
                payload["partner_updates"] = [update.to_dict() for update in partner_updates]
            payload_digest = _digest(payload)
            return event, payload_digest, deltas, opened, resolved

        def commit(db: StoreConnection) -> StoryCommit:
            arc_row = db.execute(
                "SELECT * FROM story_arcs WHERE bot_id=? AND arc_id=?",
                (checked_scope.bot_id, arc_id),
            ).fetchone()
            if arc_row is None:
                raise OperationError("story_arc_missing")
            target_arc = _arc_record(db, arc_row)
            if checked_scope.group_id not in target_arc.group_ids:
                raise OperationError("story_scope_denied")
            if (
                expected_arc_revision is not None
                and target_arc.revision != expected_arc_revision
            ):
                raise OperationError("storylet_arc_history_conflict")
            preview_existing: StoreRow | None = None
            if preview_proposal is not None:
                candidate_event_id = _storylet_event_id(preview_proposal)
                preview_existing = db.execute(
                    "SELECT * FROM story_events WHERE bot_id=? AND event_id=?",
                    (checked_scope.bot_id, candidate_event_id),
                ).fetchone()
                if preview_existing is None:
                    if target_arc.status != "active":
                        raise OperationError("story_arc_closed")
                    ensure_main(db)
                else:
                    existing = preview_existing
                    state_row = db.execute(
                        "SELECT * FROM storylet_states WHERE bot_id=? AND group_id=? AND arc_id=?",
                        (checked_scope.bot_id, checked_scope.group_id, arc_id),
                    ).fetchone()
                    if state_row is None:
                        raise OperationError("storylet_state_missing")
                    if str(state_row["registry_fingerprint"]) != registry_fingerprint:
                        raise OperationError("storylet_registry_conflict")
                    _storylet_state_from_row(state_row)
                    if isinstance(preview, RuntimeStoryletPlan):
                        expected_digest = _storylet_decision_digest(
                            preview.proposal, preview.next_state
                        )
                    else:
                        expected_digest = str(existing["decision_digest"])
                    event, payload_digest, deltas, opened, resolved = event_from_proposal(
                        preview_proposal, expected_digest
                    )
                    life_updates = _life_updates(event.life_updates)
                    partner_updates = _partner_updates(event.partner_updates)
                    result = self._commit_event_transaction(
                        db,
                        event,
                        expected_revision=0,
                        timestamp=timestamp,
                        author="storylet",
                        deltas=deltas,
                        opened=opened,
                        resolved=resolved,
                        life_updates=life_updates,
                        partner_updates=partner_updates,
                        payload_digest=payload_digest,
                        decision_digest=expected_digest,
                        origin_kind="storylet",
                        authorizer=authorizer,
                        cancel_requested=cancel_requested,
                    )
                    if not result.duplicate:
                        raise OperationError("invalid_storylet_duplicate")
                    return result

            if target_arc.status != "active":
                raise OperationError("story_arc_closed")
            ensure_main(db)
            arc = target_arc
            if arc.status != "active":
                raise OperationError("story_arc_closed")
            state_row = db.execute(
                "SELECT * FROM storylet_states WHERE bot_id=? AND group_id=? AND arc_id=?",
                (checked_scope.bot_id, checked_scope.group_id, arc_id),
            ).fetchone()
            if state_row is None:
                state = RuntimeStoryletState(
                    max_setbacks=engine.max_setbacks_per_arc,
                )
                state_revision = 0
            else:
                if str(state_row["registry_fingerprint"]) != registry_fingerprint:
                    raise OperationError("storylet_registry_conflict")
                state_revision = state_row["revision"]
                if type(state_revision) is not int or state_revision < 0:
                    raise OperationError("invalid_storylet_state")
                state = _storylet_state_from_row(state_row)
            plan = engine.plan(
                checked_scope,
                arc,
                state=state,
                step=step,
                evidence=evidence,
                source_fingerprint=registry_fingerprint,
            )
            if plan is None:
                if preview is not None:
                    raise OperationError("storylet_proposal_mismatch")
                raise OperationError("storylet_no_candidate")
            if preview_proposal is not None and (
                _storylet_proposal_material(plan.proposal)
                != _storylet_proposal_material(preview_proposal)
            ):
                raise OperationError("storylet_proposal_mismatch")
            decision_digest = _storylet_decision_digest(plan.proposal, plan.next_state)
            event, payload_digest, deltas, opened, resolved = event_from_proposal(
                plan.proposal, decision_digest
            )
            life_updates = _life_updates(event.life_updates)
            partner_updates = _partner_updates(event.partner_updates)
            ensure_fiction_partners(db, partner_updates)
            result = self._commit_event_transaction(
                db,
                event,
                expected_revision=arc.revision,
                timestamp=timestamp,
                author="storylet",
                deltas=deltas,
                opened=opened,
                resolved=resolved,
                life_updates=life_updates,
                partner_updates=partner_updates,
                payload_digest=payload_digest,
                decision_digest=decision_digest,
                origin_kind="storylet",
                authorizer=authorizer,
                cancel_requested=cancel_requested,
            )
            if result.duplicate:
                raise OperationError("invalid_storylet_duplicate")
            if cancel_requested():
                raise asyncio.CancelledError
            db.execute(
                "INSERT INTO storylet_states(bot_id,group_id,arc_id,registry_fingerprint,state_json,"
                "revision,updated_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(bot_id,group_id,arc_id) DO UPDATE SET "
                "registry_fingerprint=excluded.registry_fingerprint,state_json=excluded.state_json,"
                "revision=excluded.revision,updated_at=excluded.updated_at",
                (
                    checked_scope.bot_id,
                    checked_scope.group_id,
                    arc_id,
                    registry_fingerprint,
                    _json(_storylet_state_payload(plan.next_state), "invalid_storylet_state"),
                    state_revision + 1,
                    timestamp,
                ),
            )
            if cancel_requested():
                raise asyncio.CancelledError
            return result

        return await self.store.transaction(commit)

    async def submit_dream_proposal(
        self,
        scope: Scope,
        proposal: DreamProposal,
        *,
        now: float | None = None,
    ) -> DreamProposalRecord:
        """Persist one validated-shape Dream proposal without committing fiction."""

        from .worldbook import DreamProposal as RuntimeDreamProposal

        approved, source = self._require_dream_write()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        if type(proposal) is not RuntimeDreamProposal:
            raise OperationError("invalid_dream_proposal")
        if proposal.scope != checked_scope:
            raise OperationError("dream_scope_denied")
        if not approved.enabled or checked_scope.group_id not in approved.allowed_groups:
            raise OperationError("dream_scope_denied")
        if proposal.source_fingerprint != source:
            raise OperationError("dream_source_tampered")
        proposal_digest = _dream_proposal_digest(proposal)
        proposal_json = _json(_dream_proposal_material(proposal), "dream_proposal_invalid")
        validation_now = self._now(now)
        owner_task = asyncio.current_task()

        def cancel_requested() -> bool:
            return owner_task is not None and owner_task.cancelling() > 0

        authorizer = self._authorize_dream
        if authorizer is None:
            raise OperationError("dream_authorization_required")

        def submit(db: StoreConnection) -> DreamProposalRecord:
            if authorizer(db) is False:
                raise OperationError("dream_authorization_denied")
            if cancel_requested():
                raise asyncio.CancelledError
            row = db.execute(
                "SELECT * FROM dream_proposals WHERE bot_id=? AND group_id=? AND proposal_id=?",
                (checked_scope.bot_id, checked_scope.group_id, proposal.proposal_id),
            ).fetchone()
            if row is not None:
                record = _dream_record(row)
                existing = _dream_proposal_from_record(record)
                if (
                    record.proposal_digest != proposal_digest
                    or _dream_proposal_digest(existing) != proposal_digest
                ):
                    raise OperationError("dream_proposal_conflict")
                return record
            arc_row = db.execute(
                "SELECT * FROM story_arcs WHERE bot_id=? AND arc_id=?",
                (checked_scope.bot_id, proposal.target_arc_id),
            ).fetchone()
            if arc_row is None:
                raise OperationError("dream_arc_missing")
            target_arc = _arc_record(db, arc_row)
            if checked_scope.group_id not in target_arc.group_ids:
                raise OperationError("dream_scope_denied")
            validation = approved.validate(
                checked_scope,
                proposal,
                arc=target_arc,
                now=validation_now,
                source_fingerprint=source,
            )
            if validation is None:
                raise OperationError("dream_scope_denied")
            if not validation.accepted:
                raise OperationError(validation.reason or "dream_proposal_rejected")
            if cancel_requested():
                raise asyncio.CancelledError
            db.execute(
                "INSERT INTO dream_proposals("
                "bot_id,group_id,proposal_id,target_arc_id,target_arc_revision,"
                "source_fingerprint,created_at,proposal_json,proposal_digest,decision_status,"
                "decision_digest,decided_at,reason,committed_event_id,committed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,'pending',NULL,NULL,NULL,NULL,NULL)",
                (
                    proposal.scope.bot_id,
                    proposal.scope.group_id,
                    proposal.proposal_id,
                    proposal.target_arc_id,
                    proposal.target_arc_revision,
                    proposal.source_fingerprint,
                    proposal.created_at,
                    proposal_json,
                    proposal_digest,
                ),
            )
            if cancel_requested():
                raise asyncio.CancelledError
            inserted = db.execute(
                "SELECT * FROM dream_proposals WHERE bot_id=? AND group_id=? AND proposal_id=?",
                (checked_scope.bot_id, checked_scope.group_id, proposal.proposal_id),
            ).fetchone()
            if inserted is None:
                raise OperationError("dream_proposal_missing")
            return _dream_record(inserted)

        return await self.store.transaction(submit)

    async def list_dream_proposals_page(
        self,
        scope: Scope,
        *,
        limit: int = 32,
        after: str | None = None,
    ) -> DreamProposalPage:
        """Page durable Dream proposals by ``(created_at, proposal_id)``.

        ``after`` is the last proposal ID from the prior page. It is resolved
        inside the requested bot/group and anchors the descending composite
        key, so equal creation times cannot cause repeats or skipped rows.
        The queue includes every persisted decision/commit state.
        """

        approved, _ = self._require_dream_write()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        if not approved.enabled or checked_scope.group_id not in approved.allowed_groups:
            raise OperationError("dream_scope_denied")
        if type(limit) is not int or not 1 <= limit <= 64:
            raise OperationError("invalid_dream_page_limit")
        if after is not None:
            after = _identity(after, "invalid_dream_cursor")
        authorizer = self._authorize_dream
        if authorizer is None:
            raise OperationError("dream_authorization_required")

        def read(db: StoreConnection) -> DreamProposalPage:
            if authorizer(db) is False:
                raise OperationError("dream_authorization_denied")
            clauses = ["bot_id=?", "group_id=?"]
            params: list[object] = [checked_scope.bot_id, checked_scope.group_id]
            if after is not None:
                anchor = db.execute(
                    "SELECT created_at FROM dream_proposals "
                    "WHERE bot_id=? AND group_id=? AND proposal_id=?",
                    (checked_scope.bot_id, checked_scope.group_id, after),
                ).fetchone()
                if anchor is None:
                    raise OperationError("invalid_dream_cursor")
                created_at = _timestamp(anchor["created_at"], "dream_proposal_tampered")
                clauses.append(
                    "(created_at<? OR (created_at=? AND proposal_id<?))"
                )
                params.extend((created_at, created_at, after))
            rows = db.execute(
                "SELECT * FROM dream_proposals WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC,proposal_id DESC LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            page_rows = rows[:limit]
            proposals: list[DreamProposalRecord] = []
            for row in page_rows:
                record = _dream_record(row)
                _dream_proposal_from_record(record)
                proposals.append(record)
            next_cursor = (
                str(page_rows[-1]["proposal_id"])
                if len(rows) > limit and page_rows
                else None
            )
            return DreamProposalPage(tuple(proposals), next_cursor)

        return await self.store.transaction(read)

    async def read_dream_proposal(
        self,
        scope: Scope,
        proposal_id: str,
    ) -> DreamProposalRecord:
        """Read one durable Dream proposal within the caller's exact scope."""

        approved, _ = self._require_dream_write()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        if not approved.enabled or checked_scope.group_id not in approved.allowed_groups:
            raise OperationError("dream_scope_denied")
        proposal_id = _identity(proposal_id, "invalid_dream_proposal")
        authorizer = self._authorize_dream
        if authorizer is None:
            raise OperationError("dream_authorization_required")

        def read(db: StoreConnection) -> DreamProposalRecord:
            if authorizer(db) is False:
                raise OperationError("dream_authorization_denied")
            row = db.execute(
                "SELECT * FROM dream_proposals WHERE bot_id=? AND group_id=? AND proposal_id=?",
                (checked_scope.bot_id, checked_scope.group_id, proposal_id),
            ).fetchone()
            if row is None:
                raise OperationError("dream_proposal_missing")
            record = _dream_record(row)
            _dream_proposal_from_record(record)
            return record

        return await self.store.transaction(read)

    async def reject_dream_proposal(
        self,
        scope: Scope,
        proposal_id: str,
        *,
        now: float | None = None,
    ) -> DreamProposalRecord:
        """Persist an explicit administrator rejection; never commit fiction."""

        approved, source = self._require_dream_write()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        if not approved.enabled or checked_scope.group_id not in approved.allowed_groups:
            raise OperationError("dream_scope_denied")
        proposal_id = _identity(proposal_id, "invalid_dream_proposal")
        timestamp = self._now(now)
        owner_task = asyncio.current_task()

        def cancel_requested() -> bool:
            return owner_task is not None and owner_task.cancelling() > 0

        authorizer = self._authorize_dream
        if authorizer is None:
            raise OperationError("dream_authorization_required")

        def reject(db: StoreConnection) -> DreamProposalRecord:
            if authorizer(db) is False:
                raise OperationError("dream_authorization_denied")
            if cancel_requested():
                raise asyncio.CancelledError
            row = db.execute(
                "SELECT * FROM dream_proposals WHERE bot_id=? AND group_id=? AND proposal_id=?",
                (checked_scope.bot_id, checked_scope.group_id, proposal_id),
            ).fetchone()
            if row is None:
                raise OperationError("dream_proposal_missing")
            record = _dream_record(row)
            proposal = _dream_proposal_from_record(record)
            if record.source_fingerprint != source:
                raise OperationError("dream_source_tampered")
            if record.committed_event_id is not None:
                raise OperationError("dream_decision_finalized")

            reason = "admin_rejected"
            rejection_digest = _dream_decision_digest(
                record.proposal_digest,
                source_fingerprint=source,
                reason=reason,
            )
            if record.decision_status == "rejected":
                expected_digest = _dream_decision_digest(
                    record.proposal_digest,
                    source_fingerprint=source,
                    reason=record.reason or "",
                )
                if record.decision_digest != expected_digest:
                    raise OperationError("dream_decision_tampered")
                if record.reason == reason and record.decision_digest == rejection_digest:
                    return record
                raise OperationError("dream_decision_finalized")

            if record.decision_status == "validated":
                historical_arc = StoryArcRecord(
                    bot_id=checked_scope.bot_id,
                    arc_id=proposal.target_arc_id,
                    role="main",
                    title="",
                    stage="active",
                    status="active",
                    variables={},
                    open_threads=(),
                    group_ids=(checked_scope.group_id,),
                    revision=proposal.target_arc_revision,
                    created_at=proposal.created_at,
                    updated_at=proposal.created_at,
                )
                validation = approved.validate(
                    checked_scope,
                    proposal,
                    arc=historical_arc,
                    now=proposal.created_at,
                    source_fingerprint=source,
                )
                if (
                    validation is None
                    or not validation.accepted
                    or validation.candidate is None
                ):
                    raise OperationError("dream_decision_tampered")
                expected_validation_digest = _dream_decision_digest(
                    record.proposal_digest,
                    source_fingerprint=source,
                    candidate=validation.candidate,
                )
                if record.decision_digest != expected_validation_digest:
                    raise OperationError("dream_decision_tampered")
            elif record.decision_status != "pending":
                raise OperationError("dream_decision_finalized")

            if cancel_requested():
                raise asyncio.CancelledError
            updated = db.execute(
                "UPDATE dream_proposals SET decision_status='rejected',"
                "decision_digest=?,decided_at=?,reason=? "
                "WHERE bot_id=? AND group_id=? AND proposal_id=? "
                "AND decision_status=? AND committed_event_id IS NULL "
                "AND proposal_digest=? AND source_fingerprint=? AND decision_digest IS ?",
                (
                    rejection_digest,
                    timestamp,
                    reason,
                    checked_scope.bot_id,
                    checked_scope.group_id,
                    proposal_id,
                    record.decision_status,
                    record.proposal_digest,
                    source,
                    record.decision_digest,
                ),
            )
            if updated.rowcount != 1:
                raise OperationError("dream_decision_conflict")
            if cancel_requested():
                raise asyncio.CancelledError
            rejected = db.execute(
                "SELECT * FROM dream_proposals WHERE bot_id=? AND group_id=? AND proposal_id=?",
                (checked_scope.bot_id, checked_scope.group_id, proposal_id),
            ).fetchone()
            if rejected is None:
                raise OperationError("dream_proposal_missing")
            result = _dream_record(rejected)
            _dream_proposal_from_record(result)
            if (
                result.decision_status != "rejected"
                or result.reason != reason
                or result.decision_digest != rejection_digest
            ):
                raise OperationError("dream_decision_tampered")
            return result

        return await self.store.transaction(reject)

    async def decide_dream_proposal(
        self,
        scope: Scope,
        proposal_id: str,
        *,
        now: float | None = None,
    ) -> DreamProposalRecord:
        """Persist one validator decision; no StoryArc event is written here."""

        approved, source = self._require_dream_write()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        _identity(proposal_id, "invalid_dream_proposal")
        timestamp = self._now(now)
        owner_task = asyncio.current_task()

        def cancel_requested() -> bool:
            return owner_task is not None and owner_task.cancelling() > 0

        authorizer = self._authorize_dream
        if authorizer is None:
            raise OperationError("dream_authorization_required")

        def decide(db: StoreConnection) -> DreamProposalRecord:
            if authorizer(db) is False:
                raise OperationError("dream_authorization_denied")
            if cancel_requested():
                raise asyncio.CancelledError
            row = db.execute(
                "SELECT * FROM dream_proposals WHERE bot_id=? AND group_id=? AND proposal_id=?",
                (checked_scope.bot_id, checked_scope.group_id, proposal_id),
            ).fetchone()
            if row is None:
                raise OperationError("dream_proposal_missing")
            record = _dream_record(row)
            proposal = _dream_proposal_from_record(record)
            if record.source_fingerprint != source:
                raise OperationError("dream_source_tampered")
            if record.decision_status != "pending":
                raise OperationError("dream_decision_finalized")
            arc_row = db.execute(
                "SELECT * FROM story_arcs WHERE bot_id=? AND arc_id=?",
                (checked_scope.bot_id, record.target_arc_id),
            ).fetchone()
            target_arc = None if arc_row is None else _arc_record(db, arc_row)
            validation = approved.validate(
                checked_scope,
                proposal,
                arc=target_arc,
                now=timestamp,
                source_fingerprint=source,
            )
            if validation is None:
                raise OperationError("dream_scope_denied")
            if validation.accepted:
                decision_digest = _dream_decision_digest(
                    record.proposal_digest,
                    source_fingerprint=source,
                    candidate=validation.candidate,
                )
                status = "validated"
                reason: str | None = None
            else:
                reason = validation.reason or "dream_proposal_rejected"
                decision_digest = _dream_decision_digest(
                    record.proposal_digest,
                    source_fingerprint=source,
                    reason=reason,
                )
                status = "rejected"
            if cancel_requested():
                raise asyncio.CancelledError
            updated = db.execute(
                "UPDATE dream_proposals SET decision_status=?,decision_digest=?,decided_at=?,reason=? "
                "WHERE bot_id=? AND group_id=? AND proposal_id=? AND decision_status='pending'",
                (
                    status,
                    decision_digest,
                    timestamp,
                    reason,
                    checked_scope.bot_id,
                    checked_scope.group_id,
                    proposal_id,
                ),
            )
            if updated.rowcount != 1:
                raise OperationError("dream_decision_conflict")
            if cancel_requested():
                raise asyncio.CancelledError
            decided = db.execute(
                "SELECT * FROM dream_proposals WHERE bot_id=? AND group_id=? AND proposal_id=?",
                (checked_scope.bot_id, checked_scope.group_id, proposal_id),
            ).fetchone()
            if decided is None:
                raise OperationError("dream_proposal_missing")
            return _dream_record(decided)

        return await self.store.transaction(decide)

    async def commit_dream_proposal(
        self,
        scope: Scope,
        proposal_id: str,
        *,
        now: float | None = None,
    ) -> StoryCommit:
        """Commit only a current, durable Dream validation into StoryArc."""

        approved, source = self._require_dream_write()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        _identity(proposal_id, "invalid_dream_proposal")
        timestamp = self._now(now)
        owner_task = asyncio.current_task()

        def cancel_requested() -> bool:
            return owner_task is not None and owner_task.cancelling() > 0

        authorizer = self._authorize_dream
        if authorizer is None:
            raise OperationError("dream_authorization_required")

        def commit(db: StoreConnection) -> StoryCommit:
            row = db.execute(
                "SELECT * FROM dream_proposals WHERE bot_id=? AND group_id=? AND proposal_id=?",
                (checked_scope.bot_id, checked_scope.group_id, proposal_id),
            ).fetchone()
            if row is None:
                raise OperationError("dream_proposal_missing")
            record = _dream_record(row)
            if record.source_fingerprint != source:
                raise OperationError("dream_source_tampered")
            proposal = _dream_proposal_from_record(record)
            if record.decision_status == "pending":
                raise OperationError("dream_not_validated")
            if record.decision_status == "rejected":
                raise OperationError("dream_rejected")
            if record.decision_digest is None or record.decided_at is None:
                raise OperationError("dream_decision_tampered")
            if record.committed_event_id is not None:
                if record.committed_at is None:
                    raise OperationError("dream_commit_tampered")
                event_row = db.execute(
                    "SELECT * FROM story_events WHERE bot_id=? AND event_id=?",
                    (checked_scope.bot_id, record.committed_event_id),
                ).fetchone()
                if event_row is None:
                    raise OperationError("dream_commit_tampered")
                stored_event = _event_record(event_row)
                expected_event_id = deterministic_event_id(
                    checked_scope.bot_id,
                    checked_scope.group_id,
                    record.target_arc_id,
                    record.decision_digest,
                )
                if (
                    stored_event.origin_kind != "dream_proposal"
                    or stored_event.event_id != expected_event_id
                    or stored_event.group_id != checked_scope.group_id
                    or stored_event.arc_id != record.target_arc_id
                    or stored_event.decision_digest != record.decision_digest
                    or stored_event.committed_at != record.committed_at
                ):
                    raise OperationError("dream_commit_tampered")
                (
                    expected_event,
                    expected_payload_digest,
                    expected_deltas,
                    expected_opened,
                    expected_resolved,
                ) = _dream_event_from_candidate(
                    _dream_candidate_from_proposal(proposal), record.decision_digest
                )
                if (
                    stored_event.event_id != expected_event.event_id
                    or stored_event.event_type != expected_event.event_type
                    or stored_event.author != expected_event.author
                    or stored_event.variable_deltas != expected_deltas
                    or stored_event.open_threads != expected_opened
                    or stored_event.resolve_threads != expected_resolved
                    or stored_event.stage != expected_event.stage
                    or stored_event.arc_status != expected_event.arc_status
                    or stored_event.payload_digest != expected_payload_digest
                ):
                    raise OperationError("dream_commit_tampered")
                result = self._commit_event_transaction(
                    db,
                    expected_event,
                    expected_revision=0,
                    timestamp=timestamp,
                    author=expected_event.author,
                    deltas=expected_deltas,
                    opened=expected_opened,
                    resolved=expected_resolved,
                    life_updates=(),
                    partner_updates=(),
                    payload_digest=expected_payload_digest,
                    decision_digest=record.decision_digest,
                    origin_kind="dream_proposal",
                    authorizer=authorizer,
                    cancel_requested=cancel_requested,
                )
                if not result.duplicate:
                    raise OperationError("dream_commit_tampered")
                return result

            arc_row = db.execute(
                "SELECT * FROM story_arcs WHERE bot_id=? AND arc_id=?",
                (checked_scope.bot_id, record.target_arc_id),
            ).fetchone()
            target_arc = None if arc_row is None else _arc_record(db, arc_row)
            validation = approved.validate(
                checked_scope,
                proposal,
                arc=target_arc,
                now=timestamp,
                source_fingerprint=source,
            )
            if validation is None:
                raise OperationError("dream_scope_denied")
            if not validation.accepted or validation.candidate is None:
                raise OperationError("dream_decision_stale")
            expected_decision = _dream_decision_digest(
                record.proposal_digest,
                source_fingerprint=source,
                candidate=validation.candidate,
            )
            if expected_decision != record.decision_digest:
                raise OperationError("dream_decision_stale")
            event, payload_digest, deltas, opened, resolved = _dream_event_from_candidate(
                validation.candidate, record.decision_digest
            )
            target = target_arc
            if target is None:
                raise OperationError("dream_arc_missing")
            if checked_scope.group_id not in target.group_ids:
                raise OperationError("dream_scope_denied")
            if target.status != "active":
                raise OperationError("dream_arc_closed")
            if target.revision != record.target_arc_revision:
                raise OperationError("dream_decision_stale")
            result = self._commit_event_transaction(
                db,
                event,
                expected_revision=target.revision,
                timestamp=timestamp,
                author="dream",
                deltas=deltas,
                opened=opened,
                resolved=resolved,
                life_updates=(),
                partner_updates=(),
                payload_digest=payload_digest,
                decision_digest=record.decision_digest,
                origin_kind="dream_proposal",
                authorizer=authorizer,
                cancel_requested=cancel_requested,
            )
            if result.duplicate:
                raise OperationError("dream_commit_tampered")
            if cancel_requested():
                raise asyncio.CancelledError
            updated = db.execute(
                "UPDATE dream_proposals SET committed_event_id=?,committed_at=? "
                "WHERE bot_id=? AND group_id=? AND proposal_id=? AND decision_status='validated' "
                "AND committed_event_id IS NULL",
                (
                    event.event_id,
                    timestamp,
                    checked_scope.bot_id,
                    checked_scope.group_id,
                    proposal_id,
                ),
            )
            if updated.rowcount != 1:
                raise OperationError("dream_commit_conflict")
            if cancel_requested():
                raise asyncio.CancelledError
            committed = db.execute(
                "SELECT * FROM dream_proposals WHERE bot_id=? AND group_id=? AND proposal_id=?",
                (checked_scope.bot_id, checked_scope.group_id, proposal_id),
            ).fetchone()
            if committed is None:
                raise OperationError("dream_proposal_missing")
            _dream_record(committed)
            return result

        return await self.store.transaction(commit)

    async def read_chat_projection(
        self, scope: Scope, *, max_chars: int = 1200
    ) -> str:
        """Keep the established text-only read over the same chat producer."""

        projection = await self.read_chat_context(scope, max_chars=max_chars)
        return "" if projection is None else projection.text

    async def read_chat_context(
        self, scope: Scope, *, max_chars: int = 1200
    ) -> StoryChatProjection | None:
        """Read bounded committed fiction text and its source in one transaction.

        Only committed ``story_events`` for the visible active main Arc are
        considered.  The returned text is background data and cannot override
        higher-level instructions.
        """

        if not self._available_read():
            return None
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        budget = _chat_budget(max_chars)
        if budget == 0:
            return None

        def read(db: StoreConnection) -> StoryChatProjection | None:
            main_rows = db.execute(
                "SELECT a.* FROM story_arcs a JOIN story_arc_groups g "
                "ON g.bot_id=a.bot_id AND g.arc_id=a.arc_id "
                "WHERE a.bot_id=? AND g.group_id=? AND a.role='main' "
                "AND a.status='active' ORDER BY a.arc_id",
                (checked_scope.bot_id, checked_scope.group_id),
            ).fetchall()
            mains = tuple(_arc_record(db, row) for row in main_rows)
            if len(mains) > 1:
                raise OperationError("story_main_ambiguous")
            if not mains:
                return None
            main = mains[0]
            event_rows = db.execute(
                "SELECT e.* FROM story_events e "
                "JOIN story_arcs a ON a.bot_id=e.bot_id AND a.arc_id=e.arc_id "
                "JOIN story_arc_groups g ON g.bot_id=a.bot_id AND g.arc_id=a.arc_id "
                "WHERE e.bot_id=? AND e.group_id=? AND e.arc_id=? "
                "AND g.group_id=? AND e.source_kind IN (?,?) "
                "ORDER BY e.committed_at DESC,e.event_id DESC LIMIT ?",
                (
                    checked_scope.bot_id,
                    checked_scope.group_id,
                    main.arc_id,
                    checked_scope.group_id,
                    _SOURCE_KIND,
                    _SOCIAL_SOURCE_KIND,
                    _MAX_CHAT_EVENTS,
                ),
            ).fetchall()
            events = tuple(reversed(tuple(_event_record(row) for row in event_rows)))
            text = _chat_projection_text(main, events, budget)
            if not text:
                return None
            return StoryChatProjection(text, checked_scope, main.arc_id, main.revision)

        return await self.store.transaction(read)

    def assert_chat_projection_transaction(
        self, db: StoreConnection, projection: StoryChatProjection
    ) -> None:
        """Require the frozen main Arc to remain current at the consumer boundary."""

        if type(projection) is not StoryChatProjection:
            raise OperationError("invalid_story_chat_projection")
        scope = _scope(projection.scope, "invalid_story_chat_projection")
        if not self._available_read() or (
            self.store.bot_id is not None and scope.bot_id != self.store.bot_id
        ):
            raise OperationError("stale_fiction_source")
        rows = db.execute(
            "SELECT a.arc_id,a.revision FROM story_arcs a JOIN story_arc_groups g "
            "ON g.bot_id=a.bot_id AND g.arc_id=a.arc_id "
            "WHERE a.bot_id=? AND g.group_id=? AND a.role='main' "
            "AND a.status='active' ORDER BY a.arc_id",
            (scope.bot_id, scope.group_id),
        ).fetchall()
        if (len(rows) != 1 or rows[0]["arc_id"] != projection.arc_id
                or rows[0]["revision"] != projection.arc_revision):
            raise OperationError("stale_fiction_source")

    async def has_social_experience(self, scope: Scope, experience_id: str) -> bool:
        """Return whether this group has a committed Social fiction link."""

        if not self._available_read():
            return False
        checked_scope = _scope(scope)
        _identity(experience_id, "invalid_social_experience_id")
        self._check_bot(checked_scope.bot_id)

        def read(db: StoreConnection) -> bool:
            return db.execute(
                "SELECT 1 FROM story_social_experience_links l "
                "JOIN story_events e ON e.bot_id=l.bot_id AND e.event_id=l.event_id "
                "WHERE l.bot_id=? AND l.group_id=? AND l.experience_id=? "
                "AND e.group_id=? AND e.source_kind=? AND e.origin_kind=?",
                (
                    checked_scope.bot_id,
                    checked_scope.group_id,
                    experience_id,
                    checked_scope.group_id,
                    _SOCIAL_SOURCE_KIND,
                    "social_experience",
                ),
            ).fetchone() is not None

        return await self.store.transaction(read)

    def read_event_transaction(
        self, db: StoreConnection, scope: Scope, event_id: str
    ) -> StoryEventRecord | None:
        """Read a committed visible event inside the caller's shared Store transaction."""

        if not self._available_read():
            return None
        checked_scope = _scope(scope)
        _identity(event_id, "invalid_story_event_id")
        self._check_bot(checked_scope.bot_id)
        row = db.execute(
            "SELECT * FROM story_events WHERE bot_id=? AND event_id=?", (checked_scope.bot_id, event_id)
        ).fetchone()
        if row is None:
            return None
        if row["group_id"] != checked_scope.group_id:
            raise OperationError("story_scope_denied")
        visible = db.execute(
            "SELECT 1 FROM story_arc_groups WHERE bot_id=? AND arc_id=? AND group_id=?",
            (checked_scope.bot_id, row["arc_id"], checked_scope.group_id),
        ).fetchone()
        if visible is None:
            raise OperationError("story_scope_denied")
        return _event_record(row)

    async def read_event(self, scope: Scope, event_id: str) -> StoryEventRecord | None:
        if not self._available_read():
            return None
        return await self.store.transaction(
            lambda db: self.read_event_transaction(db, scope, event_id)
        )

    async def pending_projections(self, scope: Scope) -> tuple[ProjectionIntent, ...]:
        if not self._available_read():
            return ()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)

        def read(db: StoreConnection) -> tuple[ProjectionIntent, ...]:
            rows = db.execute(
                "SELECT * FROM story_projection_outbox WHERE bot_id=? AND group_id=? AND status='pending' "
                "ORDER BY created_at,event_id,CASE target WHEN 'life' THEN 0 ELSE 1 END",
                (checked_scope.bot_id, checked_scope.group_id),
            ).fetchall()
            return tuple(_projection_record(row) for row in rows)

        return await self.store.transaction(read)

    async def projection_receipts(self, scope: Scope) -> tuple[ProjectionReceipt, ...]:
        if not self._available_read():
            return ()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)

        def read(db: StoreConnection) -> tuple[ProjectionReceipt, ...]:
            rows = db.execute(
                "SELECT * FROM story_projection_receipts WHERE bot_id=? AND group_id=? "
                "ORDER BY applied_at,event_id,CASE target WHEN 'life' THEN 0 ELSE 1 END",
                (checked_scope.bot_id, checked_scope.group_id),
            ).fetchall()
            return tuple(_receipt_record(row) for row in rows)

        return await self.store.transaction(read)

    async def catch_up(
        self,
        scope: Scope,
        *,
        targets: Sequence[ProjectionTarget] = _PROJECTION_TARGETS,
        limit: int = 256,
        now: float | None = None,
    ) -> tuple[ProjectionReceipt, ...]:
        """Apply committed typed effects and acknowledge each target atomically.

        The Arc event is committed first.  This catch-up transaction then
        verifies the event, decision digest, group mapping and effect payload,
        applies the real Life/fiction-partner state, and writes the receipt plus
        outbox ack together.  Cancellation observed before the transaction's
        final check or a SQLite failure rolls back all three. Cancellation
        after the commit can still return CancelledError; receipts reconcile it.
        """

        self._require_projection()
        checked_scope = _scope(scope)
        self._check_bot(checked_scope.bot_id)
        selected: list[ProjectionTarget] = []
        for target in targets:
            if target not in _PROJECTION_TARGETS:
                raise OperationError("invalid_story_projection_target")
            if target not in selected:
                selected.append(target)
        if type(limit) is not int or not 1 <= limit <= 256:
            raise OperationError("invalid_story_projection_limit")
        timestamp = self._now(now)
        if not selected:
            return ()
        owner_task = asyncio.current_task()

        def check_cancel() -> None:
            if owner_task is not None and owner_task.cancelling() > 0:
                raise asyncio.CancelledError

        def commit(db: StoreConnection) -> tuple[ProjectionReceipt, ...]:
            check_cancel()
            placeholders = ",".join("?" for _ in selected)
            params: list[object] = [checked_scope.bot_id, checked_scope.group_id, *selected, limit]
            rows = db.execute(
                "SELECT * FROM story_projection_outbox "
                "WHERE bot_id=? AND group_id=? AND status='pending' "
                f"AND target IN ({placeholders}) AND effect_payload<>'{{}}' "
                "ORDER BY created_at,event_id,CASE target WHEN 'life' THEN 0 ELSE 1 END LIMIT ?",
                tuple(params),
            ).fetchall()
            if not rows:
                stale = db.execute(
                    "SELECT 1 FROM story_projection_outbox "
                    "WHERE bot_id=? AND group_id=? AND status='pending' "
                    "AND target IN (" + placeholders + ") AND effect_payload='{}' LIMIT 1",
                    (checked_scope.bot_id, checked_scope.group_id, *selected),
                ).fetchone()
                if stale is not None:
                    raise OperationError("story_projection_effect_missing")
                return ()

            receipts: list[ProjectionReceipt] = []
            for row in rows:
                target = row["target"]
                event_row = db.execute(
                    "SELECT * FROM story_events WHERE bot_id=? AND event_id=?",
                    (row["bot_id"], row["event_id"]),
                ).fetchone()
                if event_row is None:
                    raise OperationError("story_projection_orphan")
                event_record = _event_record(event_row)
                commit_seq = event_row["commit_seq"]
                if type(commit_seq) is not int or commit_seq < 1:
                    raise OperationError("invalid_story_sequence")
                if (
                    event_record.bot_id != row["bot_id"]
                    or event_record.event_id != row["event_id"]
                    or event_record.arc_id != row["arc_id"]
                    or event_record.group_id != row["group_id"]
                    or event_record.group_id != checked_scope.group_id
                    or event_record.source_kind != _SOURCE_KIND
                ):
                    raise OperationError("story_projection_scope_denied")
                visible = db.execute(
                    "SELECT 1 FROM story_arc_groups WHERE bot_id=? AND arc_id=? AND group_id=?",
                    (event_record.bot_id, event_record.arc_id, event_record.group_id),
                ).fetchone()
                if visible is None:
                    raise OperationError("story_projection_scope_denied")

                payload = _decode(row["effect_payload"], "invalid_story_projection")
                if not isinstance(payload, dict):
                    raise OperationError("invalid_story_projection")
                payload_map = cast(dict[object, object], payload)
                if set(str(key) for key in payload_map) != {"decision_digest", "updates"}:
                    raise OperationError("unknown_story_effect")
                decision = payload_map.get("decision_digest")
                if (
                    not isinstance(decision, str)
                    or not decision
                    or decision != event_record.decision_digest
                ):
                    raise OperationError("story_projection_decision_mismatch")
                raw_updates = payload_map.get("updates")
                if not isinstance(raw_updates, list):
                    raise OperationError("invalid_story_projection")
                effects: tuple[LifeUpdate | PartnerUpdate, ...]
                if target == "life":
                    effects = _life_updates(cast(list[object], raw_updates))
                    if effects != event_record.life_updates:
                        raise OperationError("story_projection_effect_mismatch")
                elif target == "partner":
                    effects = _partner_updates(cast(list[object], raw_updates))
                    if effects != event_record.partner_updates:
                        raise OperationError("story_projection_effect_mismatch")
                else:
                    raise OperationError("invalid_story_projection_target")
                if not effects:
                    raise OperationError("story_projection_effect_missing")
                expected_intent_digest = _digest(
                    {
                        "bot_id": row["bot_id"],
                        "group_id": row["group_id"],
                        "arc_id": row["arc_id"],
                        "event_id": row["event_id"],
                        "target": target,
                        "effect_payload": {
                            "decision_digest": decision,
                            "updates": [item.to_dict() for item in effects],
                        },
                    }
                )
                if row["intent_digest"] != expected_intent_digest:
                    raise OperationError("story_projection_digest_mismatch")
                receipt_id = projection_receipt_id(row["bot_id"], row["event_id"], target)
                existing_receipt = db.execute(
                    "SELECT * FROM story_projection_receipts WHERE bot_id=? AND event_id=? AND target=?",
                    (row["bot_id"], row["event_id"], target),
                ).fetchone()
                if existing_receipt is not None:
                    if (
                        existing_receipt["receipt_id"] != receipt_id
                        or existing_receipt["intent_digest"] != row["intent_digest"]
                        or existing_receipt["group_id"] != row["group_id"]
                    ):
                        raise OperationError("story_projection_receipt_conflict")
                    db.execute(
                        "UPDATE story_projection_outbox SET status='applied',receipt_id=?,updated_at=? "
                        "WHERE bot_id=? AND event_id=? AND target=? AND status='pending'",
                        (receipt_id, timestamp, row["bot_id"], row["event_id"], target),
                    )
                    receipts.append(_receipt_record(existing_receipt))
                    check_cancel()
                    continue

                if target == "life":
                    for effect in effects:
                        if not isinstance(effect, LifeUpdate):
                            raise OperationError("story_projection_effect_mismatch")
                        existing_state = db.execute(
                            "SELECT event_id FROM story_life_states "
                            "WHERE bot_id=? AND group_id=? AND key=?",
                            (row["bot_id"], row["group_id"], effect.key),
                        ).fetchone()
                        if existing_state is not None:
                            if existing_state["event_id"] == row["event_id"]:
                                continue
                            prior = db.execute(
                                "SELECT commit_seq FROM story_events WHERE bot_id=? AND event_id=?",
                                (row["bot_id"], existing_state["event_id"]),
                            ).fetchone()
                            if prior is None or type(prior[0]) is not int or prior[0] < 1:
                                raise OperationError("story_projection_orphan")
                            if prior[0] >= commit_seq:
                                continue
                        db.execute(
                            "INSERT INTO story_life_states(bot_id,group_id,key,value,event_id,expires_at,"
                            "event_committed_at,updated_at) VALUES (?,?,?,?,?,?,?,?) "
                            "ON CONFLICT(bot_id,group_id,key) DO UPDATE SET value=excluded.value,"
                            "event_id=excluded.event_id,expires_at=excluded.expires_at,"
                            "event_committed_at=excluded.event_committed_at,"
                            "updated_at=excluded.updated_at",
                            (
                                row["bot_id"],
                                row["group_id"],
                                effect.key,
                                _json(effect.value, "invalid_story_life_state"),
                                row["event_id"],
                                event_record.committed_at + float(effect.ttl_seconds or 0.0),
                                event_record.committed_at,
                                event_record.committed_at,
                            ),
                        )
                else:
                    for effect in effects:
                        if not isinstance(effect, PartnerUpdate):
                            raise OperationError("story_projection_effect_mismatch")
                        partner_row = db.execute(
                            "SELECT * FROM story_partner_states WHERE bot_id=? AND group_id=? "
                            "AND entity_id=? AND identity_kind='fiction'",
                            (row["bot_id"], row["group_id"], effect.entity_id),
                        ).fetchone()
                        if partner_row is None:
                            raise OperationError("story_partner_missing")
                        if partner_row["last_event_id"] == row["event_id"]:
                            continue
                        if partner_row["last_event_id"]:
                            prior = db.execute(
                                "SELECT commit_seq FROM story_events WHERE bot_id=? AND event_id=?",
                                (row["bot_id"], partner_row["last_event_id"]),
                            ).fetchone()
                            if prior is None or type(prior[0]) is not int or prior[0] < 1:
                                raise OperationError("story_projection_orphan")
                            if prior[0] >= commit_seq:
                                continue
                        patch = effect.to_dict()
                        assignments: list[str] = []
                        values: list[object] = []
                        for field_name in (
                            "mood",
                            "availability",
                            "current_state",
                            "note",
                            "event_note",
                        ):
                            if field_name in patch:
                                assignments.append(f"{field_name}=?")
                                values.append(patch[field_name])
                        if "constraints" in patch:
                            assignments.append("constraints=?")
                            values.append(_json(patch["constraints"], "invalid_story_partner_constraints"))
                        assignments.extend(("last_event_id=?", "last_event_at=?", "updated_at=?"))
                        values.extend((row["event_id"], event_record.committed_at, event_record.committed_at))
                        values.extend((row["bot_id"], row["group_id"], effect.entity_id))
                        updated = db.execute(
                            "UPDATE story_partner_states SET "
                            + ",".join(assignments)
                            + " WHERE bot_id=? AND group_id=? AND entity_id=? AND identity_kind='fiction'",
                            tuple(values),
                        )
                        if updated.rowcount != 1:
                            raise OperationError("story_partner_missing")

                db.execute(
                    "INSERT INTO story_projection_receipts(receipt_id,bot_id,event_id,group_id,target,"
                    "intent_digest,applied_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        receipt_id,
                        row["bot_id"],
                        row["event_id"],
                        row["group_id"],
                        target,
                        row["intent_digest"],
                        timestamp,
                    ),
                )
                updated = db.execute(
                    "UPDATE story_projection_outbox SET status='applied',receipt_id=?,updated_at=? "
                    "WHERE bot_id=? AND event_id=? AND target=? AND status='pending'",
                    (receipt_id, timestamp, row["bot_id"], row["event_id"], target),
                )
                if updated.rowcount != 1:
                    raise OperationError("story_projection_conflict")
                applied = db.execute(
                    "SELECT * FROM story_projection_receipts WHERE receipt_id=?", (receipt_id,)
                ).fetchone()
                if applied is None:
                    raise OperationError("invalid_story_projection_receipt")
                receipts.append(_receipt_record(applied))
                check_cancel()
            check_cancel()
            return tuple(receipts)

        return await self.store.transaction(commit)


# A descriptive alias for callers that prefer service terminology.  There is
# still one owner and one SQLite truth source.
StoryArcService = StoryArcStore


__all__ = [
    "AdminAuthorizer",
    "ArcRole",
    "DreamAuthorizer",
    "DreamDecisionStatus",
    "DreamProposalRecord",
    "FictionPartnerInput",
    "FictionPartnerStateRecord",
    "LifeStateRecord",
    "LifeUpdate",
    "PartnerUpdate",
    "ProjectionIntent",
    "ProjectionReceipt",
    "ProjectionTarget",
    "StoryArcInput",
    "StoryArcRecord",
    "StoryArcService",
    "StoryArcStore",
    "StoryCommit",
    "StoryEventInput",
    "StoryEventRecord",
    "StorySourceKind",
    "deterministic_event_id",
    "projection_receipt_id",
]
