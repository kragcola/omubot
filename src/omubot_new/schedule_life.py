"""Bounded, opt-in daily role-life clock for N7.

The clock is an explicit application call. It has no background ticker, no
message transport, and no model implementation. A model or deterministic
planner may be supplied through the narrow :class:`SchedulePlanner` port.
The runtime gate and group allowlist are injected from the existing config
assembly; this module does not create a second enable/disable setting.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from datetime import time as datetime_time
from types import MappingProxyType
from typing import Protocol, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .climate import ClimateKey, SensorObservation, SignalValidationError
from .store import Store, StoreConnection, StoreRow
from .story import (
    FictionPartnerStateRecord,
    LifeStateRecord,
    ScheduleFictionSnapshot,
    StoryArcRecord,
    StoryArcStore,
)
from .types import OperationError, Scope

_LOCAL_TIME_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_MAX_POINT_COUNT = 8
_MAX_POINT_CHARS = 160
_MAX_SUMMARY_CHARS = 1200
_MAX_SLOT_COUNT = 24
_MAX_SLOT_TITLE_CHARS = 160
_MAX_SLOT_NOTE_CHARS = 320
_MAX_VARIABLES = 32
_MAX_THREADS = 16
_MAX_ID = 128
_STORYLET_SCAN_BATCH = 7
_CLIMATE_TARGET_DIMENSIONS = frozenset({"energy", "valence", "openness", "tension"})


def _text(value: object, code: str, *, limit: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise OperationError(code)
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise OperationError(code)
    if len(normalized) > limit or any(ord(char) < 32 for char in normalized):
        raise OperationError(code)
    return normalized


def _identifier(value: object, code: str = "invalid_schedule_identifier") -> str:
    return _text(value, code, limit=_MAX_ID)


def _points(values: object, code: str) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        raise OperationError(code)
    raw_values = cast(Sequence[object], values)
    if len(raw_values) > _MAX_POINT_COUNT:
        raise OperationError(code)
    result: list[str] = []
    for value in raw_values:
        item = _text(value, code, limit=_MAX_POINT_CHARS)
        if item not in result:
            result.append(item)
    return tuple(result)


def _scalar(value: object, code: str = "invalid_schedule_projection") -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _text(value, code, limit=_MAX_POINT_CHARS)
    if isinstance(value, int):
        if abs(value) > 1_000_000_000:
            raise OperationError(code)
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > 1_000_000_000:
            raise OperationError(code)
        return value
    raise OperationError(code)


def _json(value: object, code: str = "invalid_schedule_document") -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise OperationError(code) from exc


def _decode(value: object, code: str = "invalid_schedule_document") -> object:
    if not isinstance(value, str):
        raise OperationError(code)
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise OperationError(code) from exc


def _timestamp(value: datetime) -> float:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise OperationError("invalid_schedule_clock")
    result = value.astimezone(UTC).timestamp()
    if not math.isfinite(result):
        raise OperationError("invalid_schedule_clock")
    return result


def _timezone(name: object) -> ZoneInfo:
    normalized = _text(name, "invalid_schedule_timezone", limit=128)
    try:
        return ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise OperationError("invalid_schedule_timezone") from exc


def _climate_targets(value: object) -> Mapping[str, float]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise OperationError("invalid_schedule_climate_targets")
    raw = cast(Mapping[object, object], value)
    if len(raw) > len(_CLIMATE_TARGET_DIMENSIONS):
        raise OperationError("invalid_schedule_climate_targets")
    normalized: dict[str, float] = {}
    for dimension, raw_value in raw.items():
        if dimension not in _CLIMATE_TARGET_DIMENSIONS or isinstance(raw_value, bool):
            raise OperationError("invalid_schedule_climate_targets")
        if not isinstance(raw_value, (int, float)):
            raise OperationError("invalid_schedule_climate_targets")
        number = float(raw_value)
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise OperationError("invalid_schedule_climate_targets")
        normalized[cast(str, dimension)] = number
    return MappingProxyType(dict(sorted(normalized.items())))


@dataclass(frozen=True, slots=True)
class PersonaRolePoints:
    """A bounded Persona projection; full Persona text never enters Schedule."""

    identity: tuple[str, ...] = ()
    traits: tuple[str, ...] = ()
    background: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity", _points(self.identity, "invalid_schedule_persona"))
        object.__setattr__(self, "traits", _points(self.traits, "invalid_schedule_persona"))
        object.__setattr__(self, "background", _points(self.background, "invalid_schedule_persona"))
        object.__setattr__(self, "relationships", _points(self.relationships, "invalid_schedule_persona"))


@dataclass(frozen=True, slots=True)
class ScheduleSlot:
    local_time: str
    title: str
    note: str = ""
    slot_id: str = ""
    climate_targets: Mapping[str, float] = field(
        default_factory=lambda: dict[str, float]()
    )

    def __post_init__(self) -> None:
        local_time = _text(self.local_time, "invalid_schedule_slot", limit=5)
        if _LOCAL_TIME_RE.fullmatch(local_time) is None:
            raise OperationError("invalid_schedule_slot")
        object.__setattr__(self, "local_time", local_time)
        object.__setattr__(
            self,
            "title",
            _text(self.title, "invalid_schedule_slot", limit=_MAX_SLOT_TITLE_CHARS),
        )
        object.__setattr__(
            self,
            "note",
            _text(self.note, "invalid_schedule_slot", limit=_MAX_SLOT_NOTE_CHARS, allow_empty=True),
        )
        if self.slot_id:
            object.__setattr__(self, "slot_id", _identifier(self.slot_id, "invalid_schedule_slot"))
        object.__setattr__(self, "climate_targets", _climate_targets(self.climate_targets))


@dataclass(frozen=True, slots=True)
class ScheduleDraft:
    summary: str
    slots: tuple[ScheduleSlot, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "summary",
            _text(self.summary, "invalid_schedule_summary", limit=_MAX_SUMMARY_CHARS),
        )
        raw_slots: object = cast(object, self.slots)
        if type(raw_slots) not in (tuple, list):
            raise OperationError("invalid_schedule_slots")
        typed_slots = cast(Sequence[object], raw_slots)
        if len(typed_slots) > _MAX_SLOT_COUNT:
            raise OperationError("invalid_schedule_slots")
        normalized: list[ScheduleSlot] = []
        for slot in typed_slots:
            if type(slot) is not ScheduleSlot:
                raise OperationError("invalid_schedule_slots")
            normalized.append(slot)
        object.__setattr__(self, "slots", tuple(normalized))


@dataclass(frozen=True, slots=True)
class FictionArcInput:
    arc_id: str
    role: str
    title: str
    stage: str
    variables: tuple[tuple[str, object], ...]
    open_threads: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FictionLifeInput:
    key: str
    value: object
    event_id: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class FictionPartnerInput:
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
class ScheduleDaySummary:
    local_day: str
    summary: str
    day_id: str


@dataclass(frozen=True, slots=True)
class SchedulePlanInput:
    bot_id: str
    group_id: str
    local_day: str
    timezone: str
    persona: PersonaRolePoints
    yesterday: ScheduleDaySummary | None
    arcs: tuple[FictionArcInput, ...]
    life: tuple[FictionLifeInput, ...]
    partners: tuple[FictionPartnerInput, ...]

    @property
    def scope(self) -> Scope:
        return Scope(bot_id=self.bot_id, group_id=self.group_id)


class SchedulePlanner(Protocol):
    async def plan(self, request: SchedulePlanInput) -> ScheduleDraft:
        """Return a bounded draft; this port grants no persistence or send access."""
        ...


@dataclass(frozen=True, slots=True)
class ScheduleClockRecord:
    bot_id: str
    group_id: str
    enabled: bool
    timezone: str
    revision: int
    last_day: str | None
    updated_at: float


@dataclass(frozen=True, slots=True)
class ScheduleDayRecord:
    bot_id: str
    group_id: str
    local_day: str
    timezone: str
    day_id: str
    input_digest: str
    summary: str
    slots: tuple[ScheduleSlot, ...]
    created_at: float
    updated_at: float
    storylet_fingerprint: str | None = None
    arc_revisions: tuple[tuple[str, str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class ScheduleStoryletStep:
    step: int
    day: ScheduleDayRecord


@dataclass(frozen=True, slots=True)
class ScheduleStoryletBatch:
    last_step: int
    steps: tuple[ScheduleStoryletStep, ...]


def _arc_input(record: StoryArcRecord) -> FictionArcInput:
    variables = tuple(
        (str(key), _scalar(value)) for key, value in sorted(record.variables.items())
    )
    return FictionArcInput(
        arc_id=_identifier(record.arc_id),
        role=_text(record.role, "invalid_schedule_projection", limit=16),
        title=_text(
            record.title,
            "invalid_schedule_projection",
            limit=_MAX_POINT_CHARS,
            allow_empty=True,
        ),
        stage=_text(record.stage, "invalid_schedule_projection", limit=64),
        variables=variables[:_MAX_VARIABLES],
        open_threads=tuple(record.open_threads[:_MAX_THREADS]),
    )


def _life_input(record: LifeStateRecord) -> FictionLifeInput:
    return FictionLifeInput(
        key=_identifier(record.key, "invalid_schedule_projection"),
        value=_scalar(record.value),
        event_id=_identifier(record.event_id, "invalid_schedule_projection"),
        expires_at=float(record.expires_at),
    )


def _partner_input(record: FictionPartnerStateRecord) -> FictionPartnerInput:
    return FictionPartnerInput(
        entity_id=_identifier(record.entity_id, "invalid_schedule_projection"),
        display_name=_text(
            record.display_name,
            "invalid_schedule_projection",
            limit=_MAX_POINT_CHARS,
            allow_empty=True,
        ),
        pinned_profile=_text(
            record.pinned_profile,
            "invalid_schedule_projection",
            limit=_MAX_POINT_CHARS,
            allow_empty=True,
        ),
        mood=_text(record.mood, "invalid_schedule_projection", limit=_MAX_POINT_CHARS, allow_empty=True),
        availability=_text(
            record.availability,
            "invalid_schedule_projection",
            limit=_MAX_POINT_CHARS,
            allow_empty=True,
        ),
        current_state=_text(
            record.current_state,
            "invalid_schedule_projection",
            limit=_MAX_POINT_CHARS,
            allow_empty=True,
        ),
        constraints=tuple(record.constraints[:_MAX_THREADS]),
        note=_text(record.note, "invalid_schedule_projection", limit=_MAX_POINT_CHARS, allow_empty=True),
        event_note=_text(
            record.event_note,
            "invalid_schedule_projection",
            limit=_MAX_POINT_CHARS,
            allow_empty=True,
        ),
        last_event_id=_text(
            record.last_event_id,
            "invalid_schedule_projection",
            limit=_MAX_ID,
            allow_empty=True,
        ),
    )


def _clock_record(row: StoreRow, *, enabled: bool) -> ScheduleClockRecord:
    raw_day = _text(row["last_day"], "invalid_schedule_clock", limit=10, allow_empty=True)
    return ScheduleClockRecord(
        bot_id=_identifier(row["bot_id"]),
        group_id=_identifier(row["group_id"]),
        enabled=enabled,
        timezone=_text(row["timezone"], "invalid_schedule_timezone", limit=128),
        revision=int(row["revision"]),
        last_day=raw_day or None,
        updated_at=float(row["updated_at"]),
    )


def _slots(value: object) -> tuple[ScheduleSlot, ...]:
    raw_slots = cast(list[object], value)
    if not isinstance(value, list) or len(raw_slots) > _MAX_SLOT_COUNT:
        raise OperationError("invalid_schedule_slots")
    result: list[ScheduleSlot] = []
    for raw_item in raw_slots:
        item: Mapping[object, object]
        if not isinstance(raw_item, Mapping):
            raise OperationError("invalid_schedule_slots")
        item = cast(Mapping[object, object], raw_item)
        result.append(
            ScheduleSlot(
                local_time=_text(
                    item.get("local_time"), "invalid_schedule_slot", limit=5
                ),
                title=_text(
                    item.get("title"), "invalid_schedule_slot", limit=_MAX_SLOT_TITLE_CHARS
                ),
                note=_text(
                    item.get("note", ""),
                    "invalid_schedule_slot",
                    limit=_MAX_SLOT_NOTE_CHARS,
                    allow_empty=True,
                ),
                slot_id=_text(
                    item.get("slot_id", ""),
                    "invalid_schedule_slot",
                    limit=_MAX_ID,
                    allow_empty=True,
                ),
                climate_targets=cast(
                    Mapping[str, float], item.get("climate_targets", {})
                ),
            )
        )
    return tuple(result)


def _day_record(row: StoreRow) -> ScheduleDayRecord:
    day = _text(row["local_day"], "invalid_schedule_day", limit=10)
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise OperationError("invalid_schedule_day") from exc
    raw_fingerprint = row["storylet_fingerprint"]
    if raw_fingerprint is not None and (
        not isinstance(raw_fingerprint, str)
        or len(raw_fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in raw_fingerprint)
    ):
        raise OperationError("invalid_schedule_storylet_marker")
    raw_arc_revisions = _decode(row["arc_revisions"], "invalid_schedule_storylet_snapshot")
    if not isinstance(raw_arc_revisions, list):
        raise OperationError("invalid_schedule_storylet_snapshot")
    raw_entries = cast(list[object], raw_arc_revisions)
    if len(raw_entries) > 256:
        raise OperationError("invalid_schedule_storylet_snapshot")
    arc_revisions: list[tuple[str, str, int]] = []
    for raw_entry_value in raw_entries:
        if not isinstance(raw_entry_value, list):
            raise OperationError("invalid_schedule_storylet_snapshot")
        raw_entry = cast(list[object], raw_entry_value)
        if len(raw_entry) != 3:
            raise OperationError("invalid_schedule_storylet_snapshot")
        arc_id = _identifier(raw_entry[0], "invalid_schedule_storylet_snapshot")
        role = _text(raw_entry[1], "invalid_schedule_storylet_snapshot", limit=16)
        revision = raw_entry[2]
        if role not in {"main", "side", "ambient"} or type(revision) is not int or revision < 0:
            raise OperationError("invalid_schedule_storylet_snapshot")
        if any(existing[0] == arc_id for existing in arc_revisions):
            raise OperationError("invalid_schedule_storylet_snapshot")
        arc_revisions.append((arc_id, role, revision))
    return ScheduleDayRecord(
        bot_id=_identifier(row["bot_id"]),
        group_id=_identifier(row["group_id"]),
        local_day=day,
        timezone=_text(row["timezone"], "invalid_schedule_timezone", limit=128),
        day_id=_identifier(row["day_id"]),
        input_digest=_text(row["input_digest"], "invalid_schedule_digest", limit=64),
        summary=_text(row["summary"], "invalid_schedule_summary", limit=_MAX_SUMMARY_CHARS),
        slots=_slots(_decode(row["slots"])),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        storylet_fingerprint=raw_fingerprint,
        arc_revisions=tuple(arc_revisions),
    )


def _slot_occurrence_timestamp(
    day: ScheduleDayRecord, slot: ScheduleSlot, zone: ZoneInfo
) -> float | None:
    """Resolve one local slot to a deterministic instant, rejecting DST gaps."""

    try:
        local_day = date.fromisoformat(day.local_day)
        local_time = datetime_time(int(slot.local_time[:2]), int(slot.local_time[3:]))
    except ValueError:
        return None
    naive = datetime.combine(local_day, local_time)
    candidates: list[float] = []
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        if round_trip == naive:
            timestamp = _timestamp(candidate)
            if timestamp not in candidates:
                candidates.append(timestamp)
    if not candidates:
        return None
    # A repeated local time always uses the first occurrence (fold=0). This
    # keeps a replay on either side of the fall-back boundary one event.
    return min(candidates)


def schedule_chat_projection(
    day: ScheduleDayRecord, *, at: datetime, max_chars: int,
) -> str:
    """Project the nearest committed fiction plans, never execution evidence.

    The current concern is the latest plan whose instant has arrived and the
    next plan in the record's local day. Source identity and complete slot
    text travel together; a record that exceeds the budget is omitted whole.
    The application still owns scope, source permission and turn freezing.
    """
    if max_chars <= 0:
        return ""
    timestamp = _timestamp(at)
    zone = _timezone(day.timezone)
    local_at = at.astimezone(zone)
    if local_at.date().isoformat() != day.local_day:
        return ""

    occurrences: list[tuple[float, int, ScheduleSlot]] = []
    for index, slot in enumerate(day.slots):
        occurrence = _slot_occurrence_timestamp(day, slot, zone)
        if occurrence is not None:
            occurrences.append((occurrence, index, slot))
    due = [item for item in occurrences if item[0] <= timestamp]
    upcoming = [item for item in occurrences if item[0] > timestamp]
    selected: list[tuple[str, tuple[float, int, ScheduleSlot]]] = []
    if due:
        selected.append(("latest_due", max(due, key=lambda item: (item[0], item[1]))))
    if upcoming:
        selected.append(("next", min(upcoming, key=lambda item: (item[0], item[1]))))

    source: dict[str, object] = {
        "source_kind": "committed_fiction_schedule",
        "bot_id": day.bot_id,
        "group_id": day.group_id,
        "day_id": day.day_id,
        "input_digest": day.input_digest,
        "local_day": day.local_day,
        "timezone": day.timezone,
        "as_of": local_at.isoformat(),
        "status": "planned",
    }
    header = "已提交fiction日程计划（仅计划；时间已到不证明已经执行或完成）："
    included: list[dict[str, object]] = []
    result = ""
    for relation, (occurrence, index, slot) in selected:
        record: dict[str, object] = {
            "relation": relation,
            "slot_id": slot.slot_id,
            "slot_index": index,
            "local_time": slot.local_time,
            "occurs_at": datetime.fromtimestamp(occurrence, zone).isoformat(),
            "title": slot.title,
            "note": slot.note,
        }
        candidate = header + _json({**source, "slots": [*included, record]})
        if len(candidate) <= max_chars:
            included.append(record)
            result = candidate
    return result


def schedule_climate_observation(
    day: ScheduleDayRecord | None,
    *,
    key: ClimateKey,
    at: datetime,
) -> SensorObservation | None:
    """Build one real schedule sensor input from a committed local-day record.

    This is a read-only projection. It chooses the last slot whose local time
    is no later than ``at`` in the record's IANA timezone. A missing day,
    different local day, scope mismatch, missing slot identity, or empty target
    map produces no source. Slot titles, notes, and day summaries are never
    consulted.
    """

    if type(day) is not ScheduleDayRecord:
        return None
    if (
        type(key) is not tuple
        or len(key) != 3
        or any(type(part) is not str or not part or part != part.strip() for part in key)
        or any(len(part) > 64 for part in key)
        or key[0] != day.bot_id
        or key[1] != day.group_id
    ):
        return None
    if type(at) is not datetime or at.tzinfo is None or at.utcoffset() is None:
        return None
    try:
        zone = _timezone(day.timezone)
        local_at = at.astimezone(zone)
        current_timestamp = _timestamp(at)
    except (OperationError, OverflowError, ValueError):
        return None
    if local_at.date().isoformat() != day.local_day:
        return None

    selected: ScheduleSlot | None = None
    selected_timestamp = float("-inf")
    selected_index = -1
    for index, slot in enumerate(day.slots):
        occurrence = _slot_occurrence_timestamp(day, slot, zone)
        if occurrence is not None and occurrence <= current_timestamp and (
            occurrence > selected_timestamp
            or occurrence == selected_timestamp and index > selected_index
        ):
            selected = slot
            selected_timestamp = occurrence
            selected_index = index
    if selected is None or not selected.slot_id or not selected.climate_targets:
        return None

    material = "\0".join(
        (
            "schedule-climate",
            *key,
            day.day_id,
            selected.slot_id,
        )
    ).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    source_ref = "schedule-source:" + digest
    event_id = "schedule-event:" + digest
    try:
        return SensorObservation.from_values(
            key=key,
            event_id=event_id,
            source_ref=source_ref,
            values=selected.climate_targets,
            observed_at=selected_timestamp,
        )
    except SignalValidationError:
        return None


class ScheduleLife:
    """Own the explicit daily schedule commit path for one runtime assembly."""

    def __init__(
        self,
        store: Store,
        *,
        story: StoryArcStore | None = None,
        enabled: bool = False,
        allowed_groups: Sequence[str] = (),
        default_timezone: str = "UTC",
        storylet_registry_fingerprint: str | None = None,
        storylet_groups: Sequence[str] = (),
    ) -> None:
        self.store = store
        self.story = story
        self.enabled = enabled
        self.allowed_groups = frozenset(
            _identifier(item, "invalid_schedule_group") for item in allowed_groups
        )
        if storylet_registry_fingerprint is not None and (
            len(storylet_registry_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in storylet_registry_fingerprint)
        ):
            raise OperationError("invalid_schedule_storylet_marker")
        self.storylet_registry_fingerprint = storylet_registry_fingerprint
        self.storylet_groups = frozenset(
            _identifier(item, "invalid_schedule_group") for item in storylet_groups
        )
        if not self.storylet_groups <= self.allowed_groups or (
            self.storylet_groups and storylet_registry_fingerprint is None
        ):
            raise OperationError("invalid_schedule_storylet_marker")
        self.default_timezone = _timezone(default_timezone).key

    def _checked_scope(self, scope: Scope) -> Scope:
        if type(scope) is not Scope:
            raise OperationError("invalid_schedule_scope")
        if self.store.bot_id is not None and scope.bot_id != self.store.bot_id:
            raise OperationError("schedule_scope_denied")
        if scope.group_id not in self.allowed_groups:
            raise OperationError("schedule_scope_denied")
        return scope

    @staticmethod
    def _day_id(scope: Scope, local_day: str) -> str:
        material = "\0".join((scope.bot_id, scope.group_id, local_day)).encode("utf-8")
        return "schedule_day_" + hashlib.sha256(material).hexdigest()

    def _default_clock(self, scope: Scope) -> ScheduleClockRecord:
        return ScheduleClockRecord(
            bot_id=scope.bot_id,
            group_id=scope.group_id,
            enabled=self.enabled,
            timezone=self.default_timezone,
            revision=0,
            last_day=None,
            updated_at=0.0,
        )

    async def read_clock(self, scope: Scope) -> ScheduleClockRecord:
        if type(scope) is not Scope:
            raise OperationError("invalid_schedule_scope")
        if self.store.bot_id is not None and scope.bot_id != self.store.bot_id:
            raise OperationError("schedule_scope_denied")
        checked_scope = scope
        if not self.enabled:
            return self._default_clock(checked_scope)
        checked_scope = self._checked_scope(checked_scope)

        def read(db: StoreConnection) -> ScheduleClockRecord:
            row = db.execute(
                "SELECT * FROM schedule_clocks WHERE bot_id=? AND group_id=?",
                (checked_scope.bot_id, checked_scope.group_id),
            ).fetchone()
            if row is None:
                return self._default_clock(checked_scope)
            record = _clock_record(row, enabled=True)
            if record.timezone != self.default_timezone:
                raise OperationError("schedule_timezone_mismatch")
            return record

        return await self.store.transaction(read)

    def assert_chat_projection_transaction(
        self, db: StoreConnection, day: ScheduleDayRecord,
    ) -> None:
        if not self.enabled or day.group_id not in self.allowed_groups:
            raise OperationError("stale_fiction_source")
        row = db.execute(
            "SELECT * FROM schedule_days WHERE bot_id=? AND group_id=? AND local_day=?",
            (day.bot_id, day.group_id, day.local_day),
        ).fetchone()
        if row is None or _day_record(row) != day:
            raise OperationError("stale_fiction_source")

    async def read_day(self, scope: Scope, local_day: str) -> ScheduleDayRecord | None:
        if type(scope) is not Scope:
            raise OperationError("invalid_schedule_scope")
        if self.store.bot_id is not None and scope.bot_id != self.store.bot_id:
            raise OperationError("schedule_scope_denied")
        checked_scope = scope
        day = _text(local_day, "invalid_schedule_day", limit=10)
        try:
            date.fromisoformat(day)
        except ValueError as exc:
            raise OperationError("invalid_schedule_day") from exc
        if not self.enabled:
            return None
        checked_scope = self._checked_scope(checked_scope)

        def read(db: StoreConnection) -> ScheduleDayRecord | None:
            row = db.execute(
                "SELECT * FROM schedule_days WHERE bot_id=? AND group_id=? AND local_day=?",
                (checked_scope.bot_id, checked_scope.group_id, day),
            ).fetchone()
            return None if row is None else _day_record(row)

        return await self.store.transaction(read)

    async def read_storylet_batch(
        self, scope: Scope, *, through_day: str, updated_at: float, limit: int = 7
    ) -> ScheduleStoryletBatch:
        """Read the next bounded, committed Schedule steps for Storylet replay."""
        checked_scope = self._checked_scope(scope)
        day = _text(through_day, "invalid_schedule_day", limit=10)
        try:
            date.fromisoformat(day)
        except ValueError as exc:
            raise OperationError("invalid_schedule_day") from exc
        timestamp = _timestamp(datetime.fromtimestamp(updated_at, UTC))
        if type(limit) is not int or not 1 <= limit <= _STORYLET_SCAN_BATCH:
            raise OperationError("invalid_storylet_scan_limit")

        def read(db: StoreConnection) -> ScheduleStoryletBatch:
            db.execute(
                "INSERT OR IGNORE INTO storylet_scan_cursors(bot_id,group_id,last_step,updated_at) "
                "VALUES (?,?, -1,?)",
                (checked_scope.bot_id, checked_scope.group_id, timestamp),
            )
            cursor = db.execute(
                "SELECT last_step FROM storylet_scan_cursors WHERE bot_id=? AND group_id=?",
                (checked_scope.bot_id, checked_scope.group_id),
            ).fetchone()
            clock_row = db.execute(
                "SELECT revision,last_day FROM schedule_clocks WHERE bot_id=? AND group_id=?",
                (checked_scope.bot_id, checked_scope.group_id),
            ).fetchone()
            if cursor is None:
                raise OperationError("storylet_scan_cursor_missing")
            last_step = int(cursor["last_step"])
            revision = 0 if clock_row is None else int(clock_row["revision"])
            clock_day = "" if clock_row is None else str(clock_row["last_day"])
            total = int(
                db.execute(
                    "SELECT COUNT(*) FROM schedule_days WHERE bot_id=? AND group_id=?",
                    (checked_scope.bot_id, checked_scope.group_id),
                ).fetchone()[0]
            )
            if total != revision or (total and last_day(db) != clock_day):
                raise OperationError("storylet_schedule_clock_conflict")
            rows = db.execute(
                "WITH numbered AS ("
                "SELECT ROW_NUMBER() OVER (ORDER BY local_day)-1 AS step,* "
                "FROM schedule_days WHERE bot_id=? AND group_id=?"
                ") SELECT * FROM numbered WHERE step>? AND local_day<=? "
                "ORDER BY step LIMIT ?",
                (checked_scope.bot_id, checked_scope.group_id, last_step, day, limit),
            ).fetchall()
            steps = tuple(
                ScheduleStoryletStep(step=int(row["step"]), day=_day_record(row))
                for row in rows
            )
            return ScheduleStoryletBatch(last_step=last_step, steps=steps)

        def last_day(db: StoreConnection) -> str:
            row = db.execute(
                "SELECT local_day FROM schedule_days WHERE bot_id=? AND group_id=? "
                "ORDER BY local_day DESC LIMIT 1",
                (checked_scope.bot_id, checked_scope.group_id),
            ).fetchone()
            return "" if row is None else str(row[0])

        return await self.store.transaction(read)

    async def advance_storylet_cursor(
        self, scope: Scope, *, expected_step: int, step: int, updated_at: float
    ) -> None:
        checked_scope = self._checked_scope(scope)
        timestamp = _timestamp(datetime.fromtimestamp(updated_at, UTC))
        if (
            type(expected_step) is not int
            or type(step) is not int
            or step != expected_step + 1
            or expected_step < -1
        ):
            raise OperationError("invalid_storylet_scan_cursor")

        def advance(db: StoreConnection) -> None:
            updated = db.execute(
                "UPDATE storylet_scan_cursors SET last_step=?,updated_at=? "
                "WHERE bot_id=? AND group_id=? AND last_step=?",
                (
                    step,
                    timestamp,
                    checked_scope.bot_id,
                    checked_scope.group_id,
                    expected_step,
                ),
            )
            if updated.rowcount != 1:
                raise OperationError("storylet_scan_cursor_conflict")

        await self.store.transaction(advance)

    async def _fiction_inputs(
        self, scope: Scope, *, now: float
    ) -> tuple[
        tuple[FictionArcInput, ...],
        tuple[FictionLifeInput, ...],
        tuple[FictionPartnerInput, ...],
        ScheduleFictionSnapshot | None,
        tuple[tuple[str, str, int], ...],
    ]:
        if self.story is None:
            return (), (), (), None, ()
        inputs = await self.story.schedule_fiction_inputs(scope, now=now)
        return (
            tuple(_arc_input(record) for record in inputs.arcs),
            tuple(_life_input(record) for record in inputs.life),
            tuple(_partner_input(record) for record in inputs.partners),
            inputs.snapshot,
            tuple((record.arc_id, record.role, record.revision) for record in inputs.arcs),
        )

    @staticmethod
    def _request_material(request: SchedulePlanInput, draft: ScheduleDraft) -> dict[str, object]:
        return {
            "bot_id": request.bot_id,
            "group_id": request.group_id,
            "local_day": request.local_day,
            "timezone": request.timezone,
            "persona": {
                "identity": request.persona.identity,
                "traits": request.persona.traits,
                "background": request.persona.background,
                "relationships": request.persona.relationships,
            },
            "yesterday": None
            if request.yesterday is None
            else {
                "local_day": request.yesterday.local_day,
                "summary": request.yesterday.summary,
                "day_id": request.yesterday.day_id,
            },
            "arcs": [
                {
                    "arc_id": arc.arc_id,
                    "role": arc.role,
                    "title": arc.title,
                    "stage": arc.stage,
                    "variables": arc.variables,
                    "open_threads": arc.open_threads,
                }
                for arc in request.arcs
            ],
            "life": [
                {
                    "key": item.key,
                    "value": item.value,
                    "event_id": item.event_id,
                    "expires_at": item.expires_at,
                }
                for item in request.life
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
                for item in request.partners
            ],
            "draft": {
                "summary": draft.summary,
                "slots": [
                    {
                        "local_time": slot.local_time,
                        "title": slot.title,
                        "note": slot.note,
                        "climate_targets": dict(slot.climate_targets),
                    }
                    for slot in draft.slots
                ],
            },
        }

    async def advance(
        self,
        scope: Scope,
        *,
        persona: PersonaRolePoints,
        planner: SchedulePlanner,
        at: datetime,
    ) -> ScheduleDayRecord | None:
        """Plan and atomically commit the local day, if its injected gate is on."""

        if type(persona) is not PersonaRolePoints:
            raise OperationError("invalid_schedule_persona")
        if not self.enabled:
            return None
        checked_scope = self._checked_scope(scope)
        timestamp = _timestamp(at)
        clock = await self.read_clock(checked_scope)
        zone = _timezone(clock.timezone)
        local_day = at.astimezone(zone).date()
        local_day_text = local_day.isoformat()
        existing = await self.read_day(checked_scope, local_day_text)
        if existing is not None:
            return existing
        if clock.last_day is not None and local_day_text < clock.last_day:
            raise OperationError("schedule_clock_rewind")
        if clock.last_day is not None:
            try:
                expected_day = date.fromisoformat(clock.last_day) + date.resolution
            except ValueError as exc:
                raise OperationError("invalid_schedule_clock") from exc
            if local_day > expected_day:
                raise OperationError("schedule_gap")

        yesterday: ScheduleDaySummary | None = None
        previous_day = (local_day - date.resolution).isoformat()
        previous = await self.read_day(checked_scope, previous_day)
        if previous is not None:
            yesterday = ScheduleDaySummary(
                local_day=previous.local_day,
                summary=previous.summary,
                day_id=previous.day_id,
            )
        arcs, life, partners, fiction_snapshot, arc_revisions = await self._fiction_inputs(
            checked_scope, now=timestamp
        )
        request = SchedulePlanInput(
            bot_id=checked_scope.bot_id,
            group_id=checked_scope.group_id,
            local_day=local_day_text,
            timezone=clock.timezone,
            persona=persona,
            yesterday=yesterday,
            arcs=arcs,
            life=life,
            partners=partners,
        )
        result = planner.plan(request)
        draft = await result if inspect.isawaitable(result) else result
        if type(draft) is not ScheduleDraft:
            raise OperationError("invalid_schedule_draft")
        _, _, _, latest_snapshot, latest_arc_revisions = await self._fiction_inputs(
            checked_scope, now=timestamp
        )
        if (
            fiction_snapshot is not None
            and latest_snapshot is not None
            and fiction_snapshot.fingerprint != latest_snapshot.fingerprint
        ):
            raise OperationError("schedule_fiction_revision_conflict")
        fiction_snapshot = latest_snapshot
        arc_revisions = latest_arc_revisions
        digest_material = self._request_material(request, draft)
        input_digest = hashlib.sha256(_json(digest_material).encode("utf-8")).hexdigest()
        day_id = self._day_id(checked_scope, local_day_text)
        persisted_slots = tuple(
            ScheduleSlot(
                local_time=slot.local_time,
                title=slot.title,
                note=slot.note,
                slot_id=f"{day_id}_slot_{index:02d}",
                climate_targets=slot.climate_targets,
            )
            for index, slot in enumerate(draft.slots)
        )
        slots_json = _json(
            [
                {
                    "local_time": slot.local_time,
                    "title": slot.title,
                    "note": slot.note,
                    "slot_id": slot.slot_id,
                    "climate_targets": dict(slot.climate_targets),
                }
                for slot in persisted_slots
            ]
        )

        def commit(db: StoreConnection) -> ScheduleDayRecord:
            if self.story is not None and fiction_snapshot is not None:
                self.story.assert_schedule_fiction_snapshot(
                    db,
                    checked_scope,
                    fiction_snapshot,
                    now=timestamp,
                )
            current_row = db.execute(
                "SELECT * FROM schedule_clocks WHERE bot_id=? AND group_id=?",
                (checked_scope.bot_id, checked_scope.group_id),
            ).fetchone()
            current = (
                self._default_clock(checked_scope)
                if current_row is None
                else _clock_record(current_row, enabled=True)
            )
            if current.timezone != clock.timezone:
                raise OperationError("schedule_revision_conflict")
            existing_row = db.execute(
                "SELECT * FROM schedule_days WHERE bot_id=? AND group_id=? AND local_day=?",
                (checked_scope.bot_id, checked_scope.group_id, local_day_text),
            ).fetchone()
            if existing_row is not None:
                existing_record = _day_record(existing_row)
                if existing_record.input_digest != input_digest:
                    raise OperationError("schedule_idempotency_conflict")
                return existing_record
            if current.revision != clock.revision or (
                current.last_day is not None and local_day_text < current.last_day
            ):
                raise OperationError("schedule_revision_conflict")
            if current_row is None:
                db.execute(
                    "INSERT INTO schedule_clocks(bot_id,group_id,timezone,revision,last_day,updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (checked_scope.bot_id, checked_scope.group_id, clock.timezone, 0, "", timestamp),
                )
            db.execute(
                "INSERT INTO schedule_days(bot_id,group_id,local_day,timezone,day_id,input_digest,"
                "summary,slots,created_at,updated_at,storylet_fingerprint,arc_revisions) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    checked_scope.bot_id,
                    checked_scope.group_id,
                    local_day_text,
                    clock.timezone,
                    day_id,
                    input_digest,
                    draft.summary,
                    slots_json,
                    timestamp,
                    timestamp,
                    self.storylet_registry_fingerprint
                    if checked_scope.group_id in self.storylet_groups
                    else None,
                    _json(arc_revisions, "invalid_schedule_storylet_snapshot"),
                ),
            )
            updated = db.execute(
                "UPDATE schedule_clocks SET last_day=?,revision=?,updated_at=? "
                "WHERE bot_id=? AND group_id=? AND revision=?",
                (
                    local_day_text,
                    current.revision + 1,
                    timestamp,
                    checked_scope.bot_id,
                    checked_scope.group_id,
                    current.revision,
                ),
            )
            if updated.rowcount != 1:
                raise OperationError("schedule_revision_conflict")
            fresh = db.execute(
                "SELECT * FROM schedule_days WHERE bot_id=? AND group_id=? AND local_day=?",
                (checked_scope.bot_id, checked_scope.group_id, local_day_text),
            ).fetchone()
            if fresh is None:
                raise OperationError("invalid_schedule_commit")
            return _day_record(fresh)

        return await self.store.transaction(commit)
