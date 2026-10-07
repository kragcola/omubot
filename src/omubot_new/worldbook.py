"""Pure, bounded N7 Worldbook domain primitives.

The module deliberately has no file, database, model, scheduler, or transport
dependency.  Canon, Storylet, and Dream values are immutable inputs to a
future owner.  This module can only return scoped projections or fiction
proposals; it never commits a StoryArc event.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, cast

from .story import LifeUpdate, PartnerUpdate, StoryArcRecord
from .types import OperationError, Scope

CanonKind = Literal["native_canon", "persona_canon"]
StoryletSeverity = Literal["daily", "tension", "setback", "recovery"]

_MAX_ID = 128
_MAX_TITLE = 200
_MAX_TEXT = 1600
_MAX_TRIGGER_COUNT = 16
_MAX_CANON_ENTRIES = 256
_MAX_STORYLETS = 256
_MAX_REGEX = 128
_MAX_QUERY = 4096
_MAX_BUDGET = 200_000
_MAX_NESTING = 6
_MAX_LIST = 32
_MAX_MAP = 32
_MAX_FINGERPRINT = 128
_FORBIDDEN_MARKERS = frozenset(
    {
        "factual",
        "factual_commit",
        "social",
        "social_evidence",
        "real_person",
        "real_name",
        "user_id",
        "person_id",
        "speaker_id",
        "persona_canon",
        "native_canon",
        "canon_write",
        "canon_override",
        "private_message",
        "private_chat",
        "private_evidence",
    }
)
_STORYLET_CONDITION_KEYS = frozenset(
    {
        "min_step",
        "after_step",
        "arc_revision",
        "in_recovery",
        "var_gte",
        "var_lte",
        "var_eq",
        "has_thread",
        "missing_thread",
    }
)
_FICTION_EFFECT_KEYS = frozenset(
    {
        "variable_deltas",
        "open_threads",
        "resolve_threads",
        "stage",
        "arc_status",
        "life_updates",
        "partner_updates",
    }
)


def _empty_object_map() -> dict[str, object]:
    return {}


def _empty_int_map() -> dict[str, int]:
    return {}


def _text(value: object, code: str, *, limit: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise OperationError(code)
    result = value.strip()
    if not allow_empty and not result:
        raise OperationError(code)
    if len(result) > limit or any(ord(char) < 32 for char in result):
        raise OperationError(code)
    return result


def _identifier(value: object, code: str = "invalid_worldbook_id") -> str:
    result = _text(value, code, limit=_MAX_ID)
    if any(char in result for char in ("/", "\\", "*", "?")):
        raise OperationError(code)
    return result


def _positive_int(value: object, code: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OperationError(code)
    if value < 0 or (value == 0 and not allow_zero):
        raise OperationError(code)
    return value


def _number(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OperationError(code)
    result = float(value)
    if not math.isfinite(result):
        raise OperationError(code)
    return result


def _scope(value: object) -> Scope:
    if type(value) is not Scope:
        raise OperationError("invalid_worldbook_scope")
    return value


def _flag(value: object) -> bool:
    if type(value) is not bool:
        raise OperationError("invalid_worldbook_gate")
    return value


def _groups(values: object) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        raise OperationError("invalid_worldbook_groups")
    sequence = cast(Sequence[object], values)
    if len(sequence) > 128:
        raise OperationError("invalid_worldbook_groups")
    result: list[str] = []
    for value in sequence:
        group = _identifier(value, "invalid_worldbook_group")
        if group not in result:
            result.append(group)
    return tuple(sorted(result))


def _freeze(value: object, *, depth: int = 0) -> object:
    """Copy JSON-shaped values into immutable containers with bounded depth."""

    if depth > _MAX_NESTING:
        raise OperationError("worldbook_value_too_deep")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        if abs(value) > 1_000_000_000:
            raise OperationError("worldbook_value_out_of_range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > 1_000_000_000:
            raise OperationError("worldbook_value_out_of_range")
        return value
    if isinstance(value, Mapping):
        mapped = cast(Mapping[object, object], value)
        if len(mapped) > _MAX_MAP:
            raise OperationError("worldbook_mapping_too_large")
        frozen: dict[str, object] = {}
        for key, item in mapped.items():
            name = _identifier(key, "invalid_worldbook_key")
            frozen[name] = _freeze(item, depth=depth + 1)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        sequence = cast(Sequence[object], value)
        if len(sequence) > _MAX_LIST:
            raise OperationError("worldbook_list_too_large")
        return tuple(_freeze(item, depth=depth + 1) for item in sequence)
    raise OperationError("invalid_worldbook_value")


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        mapped = cast(Mapping[object, object], value)
        return {str(key): _plain(item) for key, item in mapped.items()}
    if isinstance(value, (tuple, list)):
        sequence = cast(Sequence[object], value)
        return [_plain(item) for item in sequence]
    return value


def _digest(value: object) -> str:
    payload = json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        mapped = cast(Mapping[object, object], value)
        for key, item in mapped.items():
            if str(key).strip().casefold() in _FORBIDDEN_MARKERS:
                return True
            if _contains_forbidden(item):
                return True
        return False
    if isinstance(value, (tuple, list)):
        sequence = cast(Sequence[object], value)
        return any(_contains_forbidden(item) for item in sequence)
    if isinstance(value, str):
        return value.strip().casefold() in _FORBIDDEN_MARKERS
    return False


def _restricted_regex(pattern: object) -> str:
    value = _text(pattern, "invalid_worldbook_regex", limit=_MAX_REGEX)
    # The first runtime contract deliberately permits a fixed-width subset:
    # literals, character classes, ``.`` and anchors.  Groups, alternation and
    # every repetition operator are rejected, so Python's backtracking engine
    # cannot be handed a catastrophic expression such as ``(a|aa)+``.
    escaped = False
    in_class = False
    class_length = 0
    for char in value:
        if escaped:
            if char.isdigit() or char in {"g", "k"}:
                raise OperationError("invalid_worldbook_regex")
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[" and not in_class:
            in_class = True
            class_length = 0
            continue
        if char == "]" and in_class:
            if class_length == 0:
                raise OperationError("invalid_worldbook_regex")
            in_class = False
            continue
        if in_class:
            class_length += 1
            if char in "[]()|*+?{}":
                raise OperationError("invalid_worldbook_regex")
            continue
        if char in "()|*+?{}":
            raise OperationError("invalid_worldbook_regex")
    if escaped or in_class:
        raise OperationError("invalid_worldbook_regex")
    try:
        re.compile(value, re.IGNORECASE)
    except re.error as exc:
        raise OperationError("invalid_worldbook_regex") from exc
    return value


def _normalize_tokens(values: object, code: str) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        raise OperationError(code)
    sequence = cast(Sequence[object], values)
    if len(sequence) > _MAX_TRIGGER_COUNT:
        raise OperationError(code)
    result: list[str] = []
    for value in sequence:
        token = _text(value, code, limit=_MAX_TEXT)
        if token not in result:
            result.append(token)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class CanonEntry:
    """One immutable Persona or Native World Canon block."""

    entry_id: str
    title: str
    text: str
    version: int = 1
    kind: CanonKind = "native_canon"
    keywords: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    regexes: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    priority: int = 100
    source_ref: str = ""
    source_pack: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_id", _identifier(self.entry_id))
        object.__setattr__(self, "title", _text(self.title, "invalid_worldbook_title", limit=_MAX_TITLE))
        object.__setattr__(self, "text", _text(self.text, "invalid_worldbook_text", limit=_MAX_TEXT))
        object.__setattr__(self, "version", _positive_int(self.version, "invalid_worldbook_version"))
        if self.kind not in {"native_canon", "persona_canon"}:
            raise OperationError("invalid_worldbook_canon_kind")
        object.__setattr__(self, "keywords", _normalize_tokens(self.keywords, "invalid_worldbook_keywords"))
        object.__setattr__(self, "aliases", _normalize_tokens(self.aliases, "invalid_worldbook_aliases"))
        object.__setattr__(
            self,
            "regexes",
            tuple(
                _restricted_regex(item)
                for item in _normalize_tokens(self.regexes, "invalid_worldbook_regex")
            ),
        )
        object.__setattr__(
            self,
            "entity_ids",
            _normalize_tokens(self.entity_ids, "invalid_worldbook_entities"),
        )
        if type(self.priority) is not int:
            raise OperationError("invalid_worldbook_priority")
        object.__setattr__(self, "priority", max(-1000, min(1000, self.priority)))
        if type(self.source_ref) is not str or type(self.source_pack) is not str:
            raise OperationError("invalid_worldbook_provenance")
        if len(self.source_ref) > 256 or len(self.source_pack) > 64:
            raise OperationError("invalid_worldbook_provenance")


@dataclass(frozen=True, slots=True)
class CanonHit:
    entry: CanonEntry
    reasons: tuple[str, ...]
    score: float


@dataclass(frozen=True, slots=True)
class CanonProjection:
    scope: Scope
    registry_version: int
    hits: tuple[CanonHit, ...]
    dropped: tuple[tuple[str, str], ...]
    used_chars: int


class CanonRegistry:
    """Versioned immutable Canon registry with deterministic scoped matching."""

    __slots__ = ("_registry_version", "_entries", "_enabled", "_allowed_groups")

    def __init__(
        self,
        *,
        registry_version: int,
        entries: Iterable[CanonEntry],
        enabled: bool = False,
        allowed_groups: Sequence[str] = (),
    ) -> None:
        self._registry_version = _positive_int(registry_version, "invalid_worldbook_version")
        materialized = tuple(entries)
        if len(materialized) > _MAX_CANON_ENTRIES:
            raise OperationError("worldbook_registry_too_large")
        if any(type(item) is not CanonEntry for item in materialized):
            raise OperationError("invalid_worldbook_canon_entry")
        if len({item.entry_id for item in materialized}) != len(materialized):
            raise OperationError("duplicate_worldbook_canon_id")
        self._entries = tuple(sorted(materialized, key=lambda item: (-item.priority, item.entry_id)))
        self._enabled = _flag(enabled)
        self._allowed_groups = _groups(allowed_groups)

    @property
    def registry_version(self) -> int:
        return self._registry_version

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def allowed_groups(self) -> tuple[str, ...]:
        return self._allowed_groups

    @property
    def entries(self) -> tuple[CanonEntry, ...]:
        return self._entries

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "version": self.registry_version,
                "entries": [
                    {
                        "id": item.entry_id,
                        "version": item.version,
                        "kind": item.kind,
                        "title": item.title,
                        "text": item.text,
                        "keywords": item.keywords,
                        "aliases": item.aliases,
                        "regexes": item.regexes,
                        "entities": item.entity_ids,
                        "priority": item.priority,
                        "source_ref": item.source_ref,
                        "source_pack": item.source_pack,
                    }
                    for item in self._entries
                ],
            }
        )

    def activate(
        self,
        scope: Scope,
        query: str,
        *,
        entities: Sequence[str] = (),
        budget_chars: int = _MAX_BUDGET,
    ) -> CanonProjection:
        checked_scope = _scope(scope)
        if not self.enabled or checked_scope.group_id not in self.allowed_groups:
            return CanonProjection(checked_scope, self.registry_version, (), (), 0)
        text = _text(query, "invalid_worldbook_query", limit=_MAX_QUERY, allow_empty=True)
        if type(budget_chars) is not int or not 0 <= budget_chars <= _MAX_BUDGET:
            raise OperationError("invalid_worldbook_budget")
        if type(entities) not in (tuple, list):
            raise OperationError("invalid_worldbook_entities")
        if len(entities) > _MAX_TRIGGER_COUNT:
            raise OperationError("invalid_worldbook_entities")
        activated_entities = {
            _text(entity, "invalid_worldbook_entity", limit=_MAX_ID) for entity in entities
        }
        hits: list[CanonHit] = []
        lowered = text.casefold()
        for entry in self._entries:
            reasons: list[str] = []
            score = float(entry.priority) / 1000.0
            for keyword in entry.keywords:
                if keyword.casefold() in lowered:
                    reasons.append(f"keyword:{keyword}")
                    score += 1.0
            for alias in entry.aliases:
                if alias.casefold() in lowered:
                    reasons.append(f"alias:{alias}")
                    score += 1.2
            for pattern in entry.regexes:
                try:
                    matched = re.search(pattern, text[:_MAX_QUERY], re.IGNORECASE)
                except re.error:
                    matched = None
                if matched is not None:
                    reasons.append(f"regex:{pattern}")
                    score += 1.5
            entity_hits = sorted(set(entry.entity_ids) & activated_entities)
            reasons.extend(f"entity:{item}" for item in entity_hits)
            score += 0.8 * len(entity_hits)
            if reasons:
                hits.append(CanonHit(entry, tuple(sorted(set(reasons))), score))
        hits.sort(key=lambda item: (-item.score, -item.entry.priority, item.entry.entry_id))
        remaining = budget_chars
        accepted: list[CanonHit] = []
        dropped: list[tuple[str, str]] = []
        for hit in hits:
            size = len(hit.entry.text)
            if size <= remaining:
                accepted.append(hit)
                remaining -= size
            else:
                dropped.append((hit.entry.entry_id, "budget"))
        return CanonProjection(
            checked_scope,
            self.registry_version,
            tuple(accepted),
            tuple(dropped),
            budget_chars - remaining,
        )


@dataclass(frozen=True, slots=True)
class Storylet:
    """A structured fiction candidate definition; it is never a commit."""

    storylet_id: str
    title: str
    target_arc_id: str = ""
    conditions: Mapping[str, object] = field(default_factory=_empty_object_map)
    consequence: Mapping[str, object] = field(default_factory=_empty_object_map)
    once: bool = False
    cooldown_steps: int = 0
    delay_steps: int = 0
    severity: StoryletSeverity = "daily"
    recovery_steps: int = 0
    cost: int | Mapping[str, object] = 1
    required_evidence: tuple[str, ...] = ()
    priority: int = 100
    summary: str = ""
    source_ref: str = ""
    source_pack: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "storylet_id", _identifier(self.storylet_id, "invalid_storylet_id"))
        object.__setattr__(self, "title", _text(self.title, "invalid_storylet_title", limit=_MAX_TITLE))
        object.__setattr__(
            self,
            "target_arc_id",
            _text(self.target_arc_id, "invalid_storylet_arc", limit=_MAX_ID, allow_empty=True),
        )
        frozen_conditions = _freeze(self.conditions)
        frozen_consequence = _freeze(self.consequence)
        if not isinstance(frozen_conditions, Mapping) or not isinstance(frozen_consequence, Mapping):
            raise OperationError("invalid_storylet_mapping")
        conditions = cast(Mapping[str, object], frozen_conditions)
        consequence = cast(Mapping[str, object], frozen_consequence)
        object.__setattr__(self, "conditions", conditions)
        object.__setattr__(self, "consequence", consequence)
        for field_name in ("cooldown_steps", "delay_steps", "recovery_steps"):
            object.__setattr__(
                self,
                field_name,
                _positive_int(getattr(self, field_name), "invalid_storylet_window", allow_zero=True),
            )
        if self.severity not in {"daily", "tension", "setback", "recovery"}:
            raise OperationError("invalid_storylet_severity")
        if isinstance(self.cost, Mapping):
            raw_cost = cast(Mapping[object, object], self.cost)
            if set(raw_cost) != {"events"}:
                raise OperationError("invalid_storylet_cost")
            cost = _positive_int(raw_cost["events"], "invalid_storylet_cost")
        else:
            cost = _positive_int(self.cost, "invalid_storylet_cost")
        object.__setattr__(self, "cost", cost)
        object.__setattr__(
            self,
            "required_evidence",
            _normalize_tokens(self.required_evidence, "invalid_storylet_evidence"),
        )
        if type(self.priority) is not int:
            raise OperationError("invalid_storylet_priority")
        object.__setattr__(self, "priority", max(-1000, min(1000, self.priority)))
        object.__setattr__(
            self,
            "summary",
            _text(self.summary, "invalid_storylet_summary", limit=_MAX_TEXT, allow_empty=True),
        )
        if type(self.source_ref) is not str or type(self.source_pack) is not str:
            raise OperationError("invalid_storylet_provenance")
        if len(self.source_ref) > 256 or len(self.source_pack) > 64:
            raise OperationError("invalid_storylet_provenance")


class StoryletRegistry:
    """Immutable versioned Storylet registry supplied by the caller."""

    __slots__ = ("_version", "_storylets", "_fingerprint")

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, name):
            raise AttributeError("StoryletRegistry is immutable")
        object.__setattr__(self, name, value)

    def __init__(self, *, version: int, storylets: Iterable[Storylet]) -> None:
        self._version = _positive_int(version, "invalid_storylet_version")
        materialized = tuple(storylets)
        if len(materialized) > _MAX_STORYLETS:
            raise OperationError("storylet_registry_too_large")
        if any(type(item) is not Storylet for item in materialized):
            raise OperationError("invalid_storylet")
        if len({item.storylet_id for item in materialized}) != len(materialized):
            raise OperationError("duplicate_storylet_id")
        self._storylets = tuple(sorted(materialized, key=lambda item: (-item.priority, item.storylet_id)))
        self._fingerprint = _digest(
            {
                "version": self._version,
                "storylets": [
                    {
                        "storylet_id": item.storylet_id,
                        "title": item.title,
                        "target_arc_id": item.target_arc_id,
                        "conditions": item.conditions,
                        "consequence": item.consequence,
                        "once": item.once,
                        "cooldown_steps": item.cooldown_steps,
                        "delay_steps": item.delay_steps,
                        "severity": item.severity,
                        "recovery_steps": item.recovery_steps,
                        "cost": item.cost,
                        "required_evidence": item.required_evidence,
                        "priority": item.priority,
                        "summary": item.summary,
                        "source_ref": item.source_ref,
                        "source_pack": item.source_pack,
                    }
                    for item in self._storylets
                ],
            }
        )

    @property
    def version(self) -> int:
        return self._version

    @property
    def storylets(self) -> tuple[Storylet, ...]:
        return self._storylets

    @property
    def fingerprint(self) -> str:
        """Stable identity of every approved rule, including its version."""
        return self._fingerprint


def _storylet_cost(value: int | Mapping[str, object]) -> int:
    if type(value) is not int:
        raise OperationError("invalid_storylet_cost")
    return value


@dataclass(frozen=True, slots=True)
class StoryletState:
    """Caller-owned replay budget; no persistence is performed here."""

    triggered: tuple[str, ...] = ()
    cooldowns: Mapping[str, int] = field(default_factory=_empty_int_map)
    available_at: Mapping[str, int] = field(default_factory=_empty_int_map)
    setback_count: int = 0
    recovery_until: int = -1
    events_step: int = -1
    events_used: int = 0
    max_events_per_step: int = 1
    max_setbacks: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "triggered",
            tuple(sorted({_identifier(item, "invalid_storylet_id") for item in self.triggered})),
        )
        for field_name in ("cooldowns", "available_at"):
            raw = cast(object, getattr(self, field_name))
            if not isinstance(raw, Mapping):
                raise OperationError("invalid_storylet_budget")
            mapped = cast(Mapping[object, object], raw)
            normalized: dict[str, int] = {}
            for key, value in mapped.items():
                normalized[_identifier(key, "invalid_storylet_id")] = _positive_int(
                    value, "invalid_storylet_budget", allow_zero=True
                )
            object.__setattr__(self, field_name, MappingProxyType(normalized))
        for field_name in (
            "setback_count",
            "events_used",
            "max_events_per_step",
            "max_setbacks",
        ):
            object.__setattr__(
                self,
                field_name,
                _positive_int(getattr(self, field_name), "invalid_storylet_budget", allow_zero=True),
            )
        if type(self.events_step) is not int or self.events_step < -1:
            raise OperationError("invalid_storylet_budget")
        if type(self.recovery_until) is not int or self.recovery_until < -1:
            raise OperationError("invalid_storylet_budget")


@dataclass(frozen=True, slots=True)
class StoryletProposal:
    proposal_id: str
    scope: Scope
    target_arc_id: str
    target_arc_revision: int
    storylet_id: str
    step: int
    source_kind: Literal["storylet"]
    source_fingerprint: str
    consequence: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class StoryletPlan:
    """Proposal plus a transition to apply only after the owner commits.

    ``next_state`` is advisory output.  Cancellation or persistence failure
    must discard it so once/cooldown/event budget is not consumed.
    """

    proposal: StoryletProposal
    next_state: StoryletState


def _validated_consequence(value: Mapping[str, object]) -> dict[str, object] | None:
    if _contains_forbidden(value):
        return None
    unknown = set(value) - _FICTION_EFFECT_KEYS
    if unknown:
        return None
    result: dict[str, object] = {}
    if "variable_deltas" in value:
        raw = value["variable_deltas"]
        if not isinstance(raw, Mapping):
            return None
        mapped = cast(Mapping[object, object], raw)
        deltas: dict[str, object] = {}
        for key, item in mapped.items():
            try:
                name = _text(key, "invalid_storylet_effect", limit=64)
            except OperationError:
                return None
            if "/" in name or "\\" in name:
                return None
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return None
            if not math.isfinite(float(item)) or abs(float(item)) > 1_000_000_000:
                return None
            deltas[name] = item
        result["variable_deltas"] = MappingProxyType(deltas)
    for key in ("open_threads", "resolve_threads"):
        if key in value:
            raw_threads = value[key]
            if not isinstance(raw_threads, (tuple, list)):
                return None
            threads = cast(Sequence[object], raw_threads)
            if len(threads) > _MAX_LIST:
                return None
            try:
                result[key] = tuple(_identifier(item, "invalid_storylet_effect") for item in threads)
            except OperationError:
                return None
    for key in ("stage", "arc_status"):
        if key in value:
            item = value[key]
            if not isinstance(item, str) or not item.strip():
                return None
            if key == "arc_status" and item not in {"active", "closed"}:
                return None
            try:
                text_value = _text(item, "invalid_storylet_effect", limit=64)
            except OperationError:
                return None
            if "/" in text_value or "\\" in text_value:
                return None
            result[key] = text_value
    if "life_updates" in value:
        raw_updates = value["life_updates"]
        if not isinstance(raw_updates, (tuple, list)):
            return None
        raw_sequence = cast(Sequence[object], raw_updates)
        if len(raw_sequence) > 16:
            return None
        updates: list[Mapping[str, object]] = []
        seen: set[str] = set()
        for raw_update in raw_sequence:
            if not isinstance(raw_update, Mapping):
                return None
            fields = cast(Mapping[object, object], raw_update)
            if set(fields) - {"key", "value", "ttl_hours", "ttl_seconds"}:
                return None
            if "key" not in fields or "value" not in fields:
                return None
            try:
                update = LifeUpdate(
                    key=cast(str, fields["key"]),
                    value=fields["value"],
                    ttl_hours=cast(float | None, fields.get("ttl_hours")),
                    ttl_seconds=cast(float | None, fields.get("ttl_seconds")),
                )
            except OperationError:
                return None
            if update.key in seen:
                return None
            seen.add(update.key)
            updates.append(MappingProxyType(update.to_dict()))
        result["life_updates"] = tuple(updates)
    if "partner_updates" in value:
        raw_updates = value["partner_updates"]
        if not isinstance(raw_updates, (tuple, list)):
            return None
        raw_sequence = cast(Sequence[object], raw_updates)
        if len(raw_sequence) > 16:
            return None
        updates = []
        seen = set()
        for raw_update in raw_sequence:
            if not isinstance(raw_update, Mapping):
                return None
            fields = cast(Mapping[object, object], raw_update)
            if set(fields) - {
                "entity_id",
                "mood",
                "availability",
                "current_state",
                "constraints",
                "note",
                "event_note",
            }:
                return None
            if "entity_id" not in fields:
                return None
            try:
                update = PartnerUpdate(
                    entity_id=cast(str, fields["entity_id"]),
                    mood=cast(str | None, fields.get("mood")),
                    availability=cast(str | None, fields.get("availability")),
                    current_state=cast(str | None, fields.get("current_state")),
                    constraints=cast(Sequence[str], fields.get("constraints", ())),
                    note=cast(str | None, fields.get("note")),
                    event_note=cast(str | None, fields.get("event_note")),
                )
            except OperationError:
                return None
            if update.entity_id in seen:
                return None
            seen.add(update.entity_id)
            updates.append(MappingProxyType(update.to_dict()))
        result["partner_updates"] = tuple(updates)
    return result


def _conditions_met(
    storylet: Storylet,
    *,
    arc: StoryArcRecord,
    state: StoryletState,
    step: int,
    evidence: set[str],
) -> bool:
    conditions = storylet.conditions
    unknown = set(conditions) - _STORYLET_CONDITION_KEYS
    if unknown:
        return False
    for key in ("min_step", "after_step"):
        if key in conditions:
            value = conditions[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0 or step < value:
                return False
    if "arc_revision" in conditions:
        revision = conditions["arc_revision"]
        if type(revision) is not int or revision != arc.revision:
            return False
    if "in_recovery" in conditions:
        if not isinstance(conditions["in_recovery"], bool):
            return False
        if conditions["in_recovery"] != (step < state.recovery_until):
            return False
    variables = arc.variables
    for key in ("var_gte", "var_lte"):
        if key not in conditions:
            continue
        values = conditions[key]
        if not isinstance(values, Mapping):
            return False
        mapped = cast(Mapping[str, object], values)
        for name, expected in mapped.items():
            if name not in variables:
                return False
            try:
                actual_number = _number(variables[name], "invalid_storylet_condition")
                expected_number = _number(expected, "invalid_storylet_condition")
                if key == "var_gte" and actual_number < expected_number:
                    return False
                if key == "var_lte" and actual_number > expected_number:
                    return False
            except OperationError:
                return False
    if "var_eq" in conditions:
        values = conditions["var_eq"]
        if not isinstance(values, Mapping):
            return False
        mapped = cast(Mapping[str, object], values)
        for name, expected in mapped.items():
            if name not in variables or variables[name] != expected:
                return False
    threads = set(arc.open_threads)
    for key, must_exist in (("has_thread", True), ("missing_thread", False)):
        if key not in conditions:
            continue
        values = conditions[key]
        if not isinstance(values, (list, tuple)):
            return False
        sequence = cast(Sequence[object], values)
        for item in sequence:
            if not isinstance(item, str):
                return False
            if (item in threads) != must_exist:
                return False
    required = set(storylet.required_evidence)
    return required.issubset(evidence)


class StoryletEngine:
    """Select one deterministic fiction proposal without mutating an owner."""

    __slots__ = ("registry", "enabled", "allowed_groups", "max_setbacks_per_arc")

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, name):
            raise AttributeError("StoryletEngine is immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        registry: StoryletRegistry,
        *,
        enabled: bool = False,
        allowed_groups: Sequence[str] = (),
        max_setbacks_per_arc: int = 1,
    ) -> None:
        if type(registry) is not StoryletRegistry:
            raise OperationError("invalid_storylet_registry")
        self.registry = registry
        self.enabled = _flag(enabled)
        self.allowed_groups = _groups(allowed_groups)
        self.max_setbacks_per_arc = _positive_int(
            max_setbacks_per_arc, "invalid_storylet_budget", allow_zero=True
        )

    def plan(
        self,
        scope: Scope,
        arc: StoryArcRecord | None,
        *,
        state: StoryletState,
        step: int,
        evidence: Sequence[str] = (),
        source_fingerprint: str = "",
    ) -> StoryletPlan | None:
        checked_scope = _scope(scope)
        if not self.enabled or checked_scope.group_id not in self.allowed_groups:
            return None
        if type(state) is not StoryletState:
            raise OperationError("invalid_storylet_budget")
        now_step = _positive_int(step, "invalid_storylet_step", allow_zero=True)
        if arc is None or type(arc) is not StoryArcRecord:
            return None
        if (
            arc.bot_id != checked_scope.bot_id
            or checked_scope.group_id not in arc.group_ids
            or arc.status != "active"
        ):
            return None
        if now_step < state.events_step:
            raise OperationError("storylet_step_regression")
        available_evidence = {
            _text(item, "invalid_storylet_evidence", limit=_MAX_ID) for item in evidence
        }
        normalized_state = state
        if state.events_step != now_step:
            normalized_state = StoryletState(
                triggered=state.triggered,
                cooldowns=state.cooldowns,
                available_at=state.available_at,
                setback_count=state.setback_count,
                recovery_until=state.recovery_until,
                events_step=now_step,
                events_used=0,
                max_events_per_step=state.max_events_per_step,
                max_setbacks=state.max_setbacks,
            )
        if normalized_state.events_used >= normalized_state.max_events_per_step:
            return None
        in_recovery = now_step < normalized_state.recovery_until
        for storylet in self.registry.storylets:
            event_cost = _storylet_cost(storylet.cost)
            if storylet.target_arc_id and storylet.target_arc_id != arc.arc_id:
                continue
            if storylet.storylet_id in normalized_state.triggered:
                continue
            if now_step < normalized_state.cooldowns.get(storylet.storylet_id, -1):
                continue
            delay_until = normalized_state.available_at.get(storylet.storylet_id)
            if delay_until is None:
                delay_until = storylet.delay_steps
            if now_step < delay_until:
                continue
            if storylet.severity == "recovery" and not in_recovery:
                continue
            if storylet.severity == "setback" and (
                in_recovery
                or normalized_state.setback_count
                >= min(normalized_state.max_setbacks, self.max_setbacks_per_arc)
            ):
                continue
            if (
                normalized_state.events_used + event_cost
                > normalized_state.max_events_per_step
            ):
                continue
            if not _conditions_met(
                storylet,
                arc=arc,
                state=normalized_state,
                step=now_step,
                evidence=available_evidence,
            ):
                continue
            consequence = _validated_consequence(storylet.consequence)
            if consequence is None:
                continue
            source = source_fingerprint or f"storylet-registry:{self.registry.version}"
            source = _text(source, "invalid_storylet_source", limit=_MAX_FINGERPRINT)
            proposal_id = _digest(
                {
                    "scope": (checked_scope.bot_id, checked_scope.group_id),
                    "arc": (arc.arc_id, arc.revision),
                    "storylet": storylet.storylet_id,
                    "step": now_step,
                    "source": source,
                }
            )
            proposal = StoryletProposal(
                proposal_id=f"storylet_{proposal_id}",
                scope=checked_scope,
                target_arc_id=arc.arc_id,
                target_arc_revision=arc.revision,
                storylet_id=storylet.storylet_id,
                step=now_step,
                source_kind="storylet",
                source_fingerprint=source,
                consequence=MappingProxyType(consequence),
            )
            cooldowns = dict(normalized_state.cooldowns)
            if storylet.cooldown_steps:
                cooldowns[storylet.storylet_id] = now_step + storylet.cooldown_steps
            available_at = dict(normalized_state.available_at)
            if storylet.delay_steps:
                available_at[storylet.storylet_id] = now_step + storylet.delay_steps
            triggered = set(normalized_state.triggered)
            if storylet.once:
                triggered.add(storylet.storylet_id)
            setback_count = normalized_state.setback_count
            recovery_until = normalized_state.recovery_until
            if storylet.severity == "setback":
                setback_count += 1
                recovery_until = max(recovery_until, now_step + storylet.recovery_steps)
            elif storylet.severity == "recovery":
                recovery_until = -1
            next_state = StoryletState(
                triggered=tuple(sorted(triggered)),
                cooldowns=cooldowns,
                available_at=available_at,
                setback_count=setback_count,
                recovery_until=recovery_until,
                events_step=now_step,
                events_used=normalized_state.events_used + event_cost,
                max_events_per_step=normalized_state.max_events_per_step,
                max_setbacks=normalized_state.max_setbacks,
            )
            return StoryletPlan(proposal=proposal, next_state=next_state)
        return None


@dataclass(frozen=True, slots=True)
class DreamProposal:
    """Uncommitted Dream input carrying only a candidate fiction payload."""

    proposal_id: str
    scope: Scope
    target_arc_id: str
    target_arc_revision: int
    source_fingerprint: str
    created_at: float
    payload: Mapping[str, object]
    source_kind: str = "dream_proposal"
    source_domain: str = "fiction"
    kind: str = "fiction_event"
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", _identifier(self.proposal_id, "invalid_dream_proposal"))
        object.__setattr__(self, "scope", _scope(self.scope))
        object.__setattr__(self, "target_arc_id", _identifier(self.target_arc_id, "invalid_dream_arc"))
        object.__setattr__(
            self,
            "target_arc_revision",
            _positive_int(self.target_arc_revision, "invalid_dream_revision", allow_zero=True),
        )
        object.__setattr__(
            self,
            "source_fingerprint",
            _text(self.source_fingerprint, "invalid_dream_source", limit=_MAX_FINGERPRINT),
        )
        object.__setattr__(self, "created_at", _number(self.created_at, "invalid_dream_time"))
        object.__setattr__(self, "source_kind", _text(self.source_kind, "invalid_dream_source", limit=64))
        object.__setattr__(self, "source_domain", _text(self.source_domain, "invalid_dream_source", limit=64))
        object.__setattr__(self, "kind", _text(self.kind, "invalid_dream_kind", limit=64))
        object.__setattr__(
            self,
            "summary",
            _text(self.summary, "invalid_dream_summary", limit=_MAX_TEXT, allow_empty=True),
        )
        frozen = _freeze(self.payload)
        if not isinstance(frozen, Mapping):
            raise OperationError("invalid_dream_payload")
        object.__setattr__(self, "payload", frozen)


@dataclass(frozen=True, slots=True)
class FictionCandidate:
    candidate_id: str
    proposal_id: str
    scope: Scope
    target_arc_id: str
    target_arc_revision: int
    source_kind: Literal["dream_proposal"]
    source_fingerprint: str
    created_at: float
    kind: str
    summary: str
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class DreamValidation:
    candidate: FictionCandidate | None
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.candidate is not None


class DreamValidator:
    """Validate Dream as a fiction candidate; never mutate StoryArc or Canon."""

    __slots__ = ("enabled", "allowed_groups", "max_age_seconds", "future_skew_seconds")

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, name):
            raise AttributeError("DreamValidator is immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        *,
        enabled: bool = False,
        allowed_groups: Sequence[str] = (),
        max_age_seconds: float = 24 * 60 * 60,
        future_skew_seconds: float = 60.0,
    ) -> None:
        self.enabled = _flag(enabled)
        self.allowed_groups = _groups(allowed_groups)
        self.max_age_seconds = _number(max_age_seconds, "invalid_dream_age")
        self.future_skew_seconds = _number(future_skew_seconds, "invalid_dream_age")
        if self.max_age_seconds <= 0 or self.future_skew_seconds < 0:
            raise OperationError("invalid_dream_age")

    def validate(
        self,
        scope: Scope,
        proposal: DreamProposal,
        *,
        arc: StoryArcRecord | None,
        now: float,
        source_fingerprint: str,
    ) -> DreamValidation | None:
        checked_scope = _scope(scope)
        if not self.enabled or checked_scope.group_id not in self.allowed_groups:
            return None
        if type(proposal) is not DreamProposal:
            return DreamValidation(None, "invalid_dream_proposal")
        if proposal.scope != checked_scope:
            return DreamValidation(None, "dream_scope_mismatch")
        if proposal.source_kind != "dream_proposal" or proposal.source_domain != "fiction":
            return DreamValidation(None, "dream_source_forbidden")
        if proposal.kind not in {"fiction_event", "arc_replan", "reflection"}:
            return DreamValidation(None, "dream_kind_forbidden")
        if arc is None:
            return DreamValidation(None, "dream_arc_missing")
        if (
            arc.bot_id != checked_scope.bot_id
            or checked_scope.group_id not in arc.group_ids
            or arc.arc_id != proposal.target_arc_id
        ):
            return DreamValidation(None, "dream_scope_or_arc_mismatch")
        if arc.status != "active":
            return DreamValidation(None, "dream_arc_closed")
        if arc.revision != proposal.target_arc_revision:
            return DreamValidation(None, "dream_arc_revision_conflict")
        try:
            timestamp = _number(now, "invalid_dream_time")
        except OperationError:
            return DreamValidation(None, "invalid_dream_time")
        age = timestamp - proposal.created_at
        if age < -self.future_skew_seconds or age > self.max_age_seconds:
            return DreamValidation(None, "dream_time_invalid")
        try:
            expected = _text(source_fingerprint, "invalid_dream_source", limit=_MAX_FINGERPRINT)
        except OperationError:
            return DreamValidation(None, "invalid_dream_source")
        if expected != proposal.source_fingerprint:
            return DreamValidation(None, "dream_source_tampered")
        payload = _validated_consequence(proposal.payload)
        if payload is None:
            return DreamValidation(None, "dream_payload_forbidden")
        candidate_id = _digest(
            {
                "proposal": proposal.proposal_id,
                "scope": (checked_scope.bot_id, checked_scope.group_id),
                "arc": (arc.arc_id, arc.revision),
                "source": proposal.source_fingerprint,
                "created_at": proposal.created_at,
                "kind": proposal.kind,
                "payload": payload,
            }
        )
        return DreamValidation(
            FictionCandidate(
                candidate_id=f"fiction_candidate_{candidate_id}",
                proposal_id=proposal.proposal_id,
                scope=checked_scope,
                target_arc_id=arc.arc_id,
                target_arc_revision=arc.revision,
                source_kind="dream_proposal",
                source_fingerprint=proposal.source_fingerprint,
                created_at=proposal.created_at,
                kind=proposal.kind,
                summary=proposal.summary,
                payload=MappingProxyType(payload),
            )
        )


@dataclass(frozen=True, slots=True)
class WorldbookSettings:
    enabled: bool = False
    chat_projection_enabled: bool = False
    schedule_projection_enabled: bool = False
    storylet_enabled: bool = False
    dream_proposal_enabled: bool = False
    social_evidence_enabled: bool = False
    allowed_groups: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "enabled",
            "chat_projection_enabled",
            "schedule_projection_enabled",
            "storylet_enabled",
            "dream_proposal_enabled",
            "social_evidence_enabled",
        ):
            _flag(getattr(self, name))
        object.__setattr__(self, "allowed_groups", _groups(self.allowed_groups))


class Worldbook:
    """A gate-only facade that composes the pure registries without I/O."""

    def __init__(
        self,
        *,
        settings: WorldbookSettings | None = None,
        canon: CanonRegistry,
        storylets: StoryletRegistry,
        dream: DreamValidator | None = None,
    ) -> None:
        if settings is not None and type(settings) is not WorldbookSettings:
            raise OperationError("invalid_worldbook_settings")
        self.settings = WorldbookSettings() if settings is None else settings
        self.canon = canon
        self.storylets = storylets
        self.dream = dream or DreamValidator()

    def _allowed(self, scope: Scope) -> bool:
        return scope.group_id in self.settings.allowed_groups

    def project_canon(
        self,
        scope: Scope,
        query: str,
        *,
        entities: Sequence[str] = (),
        budget_chars: int = _MAX_BUDGET,
    ) -> tuple[CanonHit, ...]:
        checked_scope = _scope(scope)
        if not (
            self.settings.enabled and self.settings.chat_projection_enabled
        ) or not self._allowed(checked_scope):
            return ()
        result = self.canon.activate(
            checked_scope,
            query,
            entities=entities,
            budget_chars=budget_chars,
        )
        return result.hits

    def plan_storylet(
        self,
        scope: Scope,
        arc: StoryArcRecord | None,
        *,
        state: StoryletState,
        step: int,
        evidence: Sequence[str] = (),
        source_fingerprint: str = "",
    ) -> StoryletPlan | None:
        checked_scope = _scope(scope)
        if not (self.settings.enabled and self.settings.storylet_enabled) or not self._allowed(checked_scope):
            return None
        engine = StoryletEngine(
            self.storylets,
            enabled=True,
            allowed_groups=(checked_scope.group_id,),
        )
        return engine.plan(
            checked_scope,
            arc,
            state=state,
            step=step,
            evidence=evidence,
            source_fingerprint=source_fingerprint,
        )

    def validate_dream(
        self,
        scope: Scope,
        proposal: DreamProposal,
        *,
        arc: StoryArcRecord | None,
        now: float,
        source_fingerprint: str,
    ) -> DreamValidation | None:
        checked_scope = _scope(scope)
        if not (
            self.settings.enabled and self.settings.dream_proposal_enabled
        ) or not self._allowed(checked_scope):
            return None
        return self.dream.validate(
            checked_scope,
            proposal,
            arc=arc,
            now=now,
            source_fingerprint=source_fingerprint,
        )
