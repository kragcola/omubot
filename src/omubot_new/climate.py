"""Bounded, on-read Dialogue Climate state with source-owned signals."""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections import OrderedDict, deque
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from time import time
from types import MappingProxyType
from typing import ClassVar, Literal, cast

from .store import ClimateEvent, ClimateSourceProof, ClimateStateRecord, Store, StoreConnection
from .types import Event, OperationError, Scope

ClimateKey = tuple[str, str, str]
ClimateDimension = Literal[
    "energy",
    "valence",
    "openness",
    "tension",
    "trust",
    "familiarity",
]
SensorName = Literal[
    "schedule",
    "irritation",
    "circadian",
    "interaction",
    "calendar",
    "message",
]
SignalMode = Literal["target", "delta"]
ClimateStatus = Literal["available", "unavailable", "degraded"]

_DIMENSIONS: tuple[ClimateDimension, ...] = (
    "energy",
    "valence",
    "openness",
    "tension",
    "trust",
    "familiarity",
)
_SENSOR_NAMES: tuple[SensorName, ...] = (
    "schedule",
    "irritation",
    "circadian",
    "interaction",
    "calendar",
    "message",
)
_SENSOR_DIMENSIONS: dict[SensorName, frozenset[ClimateDimension]] = {
    "schedule": frozenset({"energy", "valence", "openness", "tension"}),
    "irritation": frozenset({"tension"}),
    "circadian": frozenset({"energy"}),
    "interaction": frozenset({"trust", "familiarity"}),
    "calendar": frozenset({"energy", "valence"}),
    "message": frozenset({"valence", "openness", "tension"}),
}
_SENSOR_MODES: dict[SensorName, SignalMode] = {
    "schedule": "target",
    "irritation": "delta",
    "circadian": "target",
    "interaction": "target",
    "calendar": "target",
    "message": "delta",
}
_DEFAULT_BASELINES: dict[ClimateDimension, float] = {
    "energy": 0.5,
    "valence": 0.5,
    "openness": 0.5,
    "tension": 0.0,
    "trust": 0.5,
    "familiarity": 0.5,
}
_DEFAULT_DECAY_SECONDS: dict[ClimateDimension, float] = {
    "energy": 3600.0,
    "valence": 7200.0,
    "openness": 7200.0,
    "tension": 3600.0,
    "trust": 58.0 * 3600.0,
    "familiarity": 7.0 * 24.0 * 3600.0,
}
_CLIMATE_VERSION_PREFIX = "climate-v1:sha256:"
_MESSAGE_OPENNESS_DELTA = 0.05
_IRRITATION_MENTION_DELTA = 0.03
_IRRITATION_BURST_BONUS = 0.01
_INTERACTION_TONE_TOKENS = ("啊", "呀", "啦", "呢", "嘛", "哈", "哈哈", "hhh", "笑", "草")
_INTERACTION_DELTAS: dict[str, tuple[tuple[ClimateDimension, float], ...]] = {
    "cold": (("tension", 0.15), ("valence", -0.15)),
    "tired": (("energy", -0.15), ("openness", -0.1)),
    "high": (("energy", 0.2), ("valence", 0.1)),
    "playful": (("valence", 0.2), ("openness", 0.15)),
}


class SensorStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


class SignalValidationError(ValueError):
    """A source signal does not have a safe identity or bounded shape."""


class FeedbackProofError(ValueError):
    """A feedback update was attempted without a proven send receipt."""


class SignalOrderingError(ValueError):
    """A source event is too old or too far ahead of the applied timeline."""


@dataclass(frozen=True)
class SensorObservation:
    """A source-owned, event-identified input before conversion to signals."""

    key: ClimateKey
    event_id: str
    source_ref: str
    values: tuple[tuple[ClimateDimension, float], ...]
    observed_at: float

    @classmethod
    def from_values(
        cls,
        *,
        key: ClimateKey,
        event_id: str,
        source_ref: str,
        values: Mapping[str, float],
        observed_at: float,
    ) -> SensorObservation:
        _validate_key(key)
        _validate_identity(event_id, "event_id")
        _validate_identity(source_ref, "source_ref")
        _validate_timestamp(observed_at)
        normalized: list[tuple[ClimateDimension, float]] = []
        seen_dimensions: set[ClimateDimension] = set()
        for dimension, value in values.items():
            if dimension not in _DIMENSIONS:
                raise SignalValidationError("unknown climate dimension")
            if dimension in seen_dimensions:
                raise SignalValidationError("climate dimension is duplicated")
            seen_dimensions.add(dimension)
            try:
                numeric_value = float(value)
            except (TypeError, ValueError) as exc:
                raise SignalValidationError("signal value must be numeric") from exc
            normalized.append((dimension, numeric_value))
        return cls(
            key=key,
            event_id=event_id,
            source_ref=source_ref,
            values=tuple(normalized),
            observed_at=observed_at,
        )


@dataclass(frozen=True)
class ClimateSignal:
    key: ClimateKey
    sensor: SensorName
    dimension: ClimateDimension
    mode: SignalMode
    value: float
    source_ref: str
    event_id: str
    observed_at: float


@dataclass(frozen=True)
class SensorRead:
    sensor: SensorName
    key: ClimateKey | None
    status: SensorStatus
    signals: tuple[ClimateSignal, ...]
    source_ref: str | None


class _BaseSensor:
    sensor_name: ClassVar[SensorName]
    dimensions: ClassVar[frozenset[ClimateDimension]]
    mode: ClassVar[SignalMode]

    def sense(self, observation: SensorObservation | None) -> SensorRead:
        if observation is None:
            return SensorRead(self.sensor_name, None, SensorStatus.UNAVAILABLE, (), None)
        if not observation.source_ref.strip() or not observation.event_id.strip():
            return SensorRead(
                self.sensor_name, observation.key, SensorStatus.UNAVAILABLE, (), None
            )

        signals: list[ClimateSignal] = []
        had_invalid_value = False
        for dimension, raw_value in observation.values:
            if dimension not in self.dimensions or not math.isfinite(raw_value):
                had_invalid_value = True
                continue
            if self.mode == "target":
                bounded = max(0.0, min(1.0, raw_value))
            else:
                bounded = max(-1.0, min(1.0, raw_value))
            signals.append(
                ClimateSignal(
                    key=observation.key,
                    sensor=self.sensor_name,
                    dimension=dimension,
                    mode=self.mode,
                    value=bounded,
                    source_ref=observation.source_ref,
                    event_id=observation.event_id,
                    observed_at=observation.observed_at,
                )
            )
        status = (
            SensorStatus.AVAILABLE
            if signals
            else SensorStatus.DEGRADED
            if had_invalid_value
            else SensorStatus.UNAVAILABLE
        )
        return SensorRead(
            self.sensor_name,
            observation.key,
            status,
            tuple(signals),
            observation.source_ref if signals else None,
        )


class ScheduleSensor(_BaseSensor):
    sensor_name: ClassVar[SensorName] = "schedule"
    mode: ClassVar[SignalMode] = "target"
    dimensions: ClassVar[frozenset[ClimateDimension]] = frozenset(
        {"energy", "valence", "openness", "tension"}
    )


class IrritationSensor(_BaseSensor):
    sensor_name: ClassVar[SensorName] = "irritation"
    mode: ClassVar[SignalMode] = "delta"
    dimensions: ClassVar[frozenset[ClimateDimension]] = frozenset({"tension"})


class CircadianSensor(_BaseSensor):
    sensor_name: ClassVar[SensorName] = "circadian"
    mode: ClassVar[SignalMode] = "target"
    dimensions: ClassVar[frozenset[ClimateDimension]] = frozenset({"energy"})


class InteractionSensor(_BaseSensor):
    sensor_name: ClassVar[SensorName] = "interaction"
    mode: ClassVar[SignalMode] = "target"
    dimensions: ClassVar[frozenset[ClimateDimension]] = frozenset({"trust", "familiarity"})


class CalendarSensor(_BaseSensor):
    sensor_name: ClassVar[SensorName] = "calendar"
    mode: ClassVar[SignalMode] = "target"
    dimensions: ClassVar[frozenset[ClimateDimension]] = frozenset({"energy", "valence"})


def calendar_climate_observation(
    *,
    key: ClimateKey,
    local_day: str,
    is_holiday: bool,
    is_self_birthday: bool,
    observed_at: float,
) -> SensorObservation | None:
    """Map verified rich-calendar facts to the legacy CalendarSensor targets."""

    if is_self_birthday:
        kind = "self_birthday"
        values = {"valence": 0.8, "energy": 0.7}
    elif is_holiday:
        kind = "holiday"
        values = {"valence": 0.65}
    else:
        return None
    return SensorObservation.from_values(
        key=key,
        event_id=f"calendar:{local_day}:{kind}",
        source_ref=f"calendar:{local_day}:{kind}",
        values=values,
        observed_at=observed_at,
    )


class MessageSensor(_BaseSensor):
    sensor_name: ClassVar[SensorName] = "message"
    mode: ClassVar[SignalMode] = "delta"
    dimensions: ClassVar[frozenset[ClimateDimension]] = frozenset(
        {"valence", "openness", "tension"}
    )


def message_climate_observation(
    event: Event,
    *,
    observed_at: float | None = None,
) -> SensorObservation | None:
    """Adapt one authorized group message into a bounded message signal.

    The caller must have already checked ``message.read`` for this event's
    subject and scope.  This pure adapter consumes only structured routing
    metadata: a non-empty message identity and an explicit mention of this
    bot.  It never reads text, rich segments, or reply content, and it grants
    no permission to send a reply.  Because ``Event`` has no source timestamp,
    callers must provide one; no wall-clock fallback is used during replay.
    """

    if type(event) is not Event or type(event.scope) is not Scope:
        return None
    if observed_at is None or type(observed_at) not in (int, float):
        return None
    timestamp = float(observed_at)
    if not math.isfinite(timestamp):
        return None
    if (
        not event.event_id
        or event.event_id != event.event_id.strip()
        or not event.user_id
        or event.user_id != event.user_id.strip()
        or not event.message_id
        or event.message_id != event.message_id.strip()
        or not event.mentioned
        or event.scope.bot_id not in event.mention_targets
    ):
        return None

    key: ClimateKey = (event.scope.bot_id, event.scope.group_id, event.user_id)
    source_ref = f"message:{event.message_id}"
    try:
        return SensorObservation.from_values(
            key=key,
            event_id=event.event_id,
            source_ref=source_ref,
            values={"openness": _MESSAGE_OPENNESS_DELTA},
            observed_at=timestamp,
        )
    except (SignalValidationError, TypeError, ValueError):
        return None


def irritation_climate_observation(
    event: Event,
    *,
    burst_continuation: bool,
    observed_at: float | None = None,
) -> SensorObservation | None:
    """Adapt one trusted bot mention into an irritation tension delta.

    This adapter only consumes structured mention metadata.  The caller owns
    the bounded five-minute observation window and passes its result through
    ``burst_continuation``; this function keeps no history and never reads
    message text or poke events.
    """

    if type(event) is not Event or type(event.scope) is not Scope:
        return None
    if type(burst_continuation) is not bool:
        return None
    if observed_at is None or type(observed_at) not in (int, float):
        return None
    timestamp = float(observed_at)
    if not math.isfinite(timestamp):
        return None
    if (
        not event.event_id
        or event.event_id != event.event_id.strip()
        or not event.user_id
        or event.user_id != event.user_id.strip()
        or not event.message_id
        or event.message_id != event.message_id.strip()
        or not event.mentioned
        or event.scope.bot_id not in event.mention_targets
    ):
        return None

    key: ClimateKey = (event.scope.bot_id, event.scope.group_id, event.user_id)
    material = "\0".join(("irritation-mention", *key, event.message_id)).encode("utf-8")
    identity = hashlib.sha256(material).hexdigest()
    event_id = f"irritation:mention:{identity}"
    source_ref = f"irritation:message:{identity}"
    delta = _IRRITATION_MENTION_DELTA + (
        _IRRITATION_BURST_BONUS if burst_continuation else 0.0
    )
    try:
        return SensorObservation.from_values(
            key=key,
            event_id=event_id,
            source_ref=source_ref,
            values={"tension": delta},
            observed_at=timestamp,
        )
    except (SignalValidationError, TypeError, ValueError):
        return None


class SensorHub:
    """Own the six named conversion interfaces; no source is fabricated."""

    def __init__(self) -> None:
        self._sensors: dict[SensorName, _BaseSensor] = {
            "schedule": ScheduleSensor(),
            "irritation": IrritationSensor(),
            "circadian": CircadianSensor(),
            "interaction": InteractionSensor(),
            "calendar": CalendarSensor(),
            "message": MessageSensor(),
        }

    def sense(self, sensor: str, observation: SensorObservation | None) -> SensorRead:
        if sensor not in _SENSOR_NAMES:
            raise ValueError("unknown climate sensor")
        sensor_name = sensor
        return self._sensors[sensor_name].sense(observation)


@dataclass(frozen=True)
class SendReceiptProof:
    """Internal proof built by the send owner only after a successful send."""

    key: ClimateKey
    event_id: str
    message_id: str

    def __post_init__(self) -> None:
        _validate_key(self.key)
        _validate_identity(self.event_id, "event_id")
        _validate_identity(self.message_id, "message_id")


@dataclass(frozen=True)
class ClimateSnapshot:
    key: ClimateKey
    energy: float
    valence: float
    openness: float
    tension: float
    trust: float
    familiarity: float
    version: str
    revision: int
    status: ClimateStatus
    source_refs: tuple[str, ...]
    event_ids: tuple[str, ...]
    as_of: float
    generation: str | None = None
    source_proofs: tuple[ClimateSourceProof, ...] = ()

    @property
    def values(self) -> Mapping[ClimateDimension, float]:
        return MappingProxyType(
            {
                "energy": self.energy,
                "valence": self.valence,
                "openness": self.openness,
                "tension": self.tension,
                "trust": self.trust,
                "familiarity": self.familiarity,
            }
        )


@dataclass(frozen=True)
class TextInteractionStyle:
    """One current-turn-only interaction style inferred from short text history."""

    label: Literal["cold", "tired", "high", "playful"]
    confidence: float
    short_reply_ratio: float
    tone_particle_rate: float
    deltas: tuple[tuple[ClimateDimension, float], ...]


def infer_text_interaction_style(
    messages: tuple[str, ...], *, sticker_density: float = 0.0,
) -> TextInteractionStyle | None:
    """Apply the legacy short-reply/tone thresholds without timing or sticker data."""

    texts = tuple(text.strip() for text in messages if text.strip())
    if not texts and sticker_density < .35:
        return None
    short_ratio = sum(len(text) <= 6 for text in texts) / len(texts) if texts else 0.0
    tone_rate = sum(
        any(token in text.lower() for token in _INTERACTION_TONE_TOKENS)
        for text in texts
    ) / len(texts) if texts else 0.0
    label: Literal["cold", "tired", "high", "playful"] | None = None
    confidence = 0.0
    if short_ratio >= 0.7 and tone_rate <= 0.2 and sticker_density <= .1:
        label, confidence = "cold", 0.74
    elif short_ratio >= 0.55 and tone_rate <= 0.35:
        label, confidence = "tired", 0.68
    elif sticker_density >= .35:
        label, confidence = "playful", .76
    elif tone_rate >= 0.55 and short_ratio <= 0.45:
        label, confidence = "high", 0.72
    if label is None:
        return None
    return TextInteractionStyle(
        label=label,
        confidence=confidence,
        short_reply_ratio=round(short_ratio, 4),
        tone_particle_rate=round(tone_rate, 4),
        deltas=tuple(
            (dimension, delta * confidence)
            for dimension, delta in _INTERACTION_DELTAS[label]
        ),
    )


def overlay_text_interaction_style(
    snapshot: ClimateSnapshot, style: TextInteractionStyle
) -> ClimateSnapshot:
    """Overlay a text signal on one turn snapshot without changing ClimateEngine state."""

    values = dict(snapshot.values)
    for dimension, delta in style.deltas:
        values[dimension] = max(0.0, min(1.0, values[dimension] + delta))
    status: ClimateStatus = (
        "available" if snapshot.status == "unavailable" else snapshot.status
    )
    version = _climate_snapshot_version(
        snapshot.key,
        snapshot.revision,
        status,
        snapshot.as_of,
        values,
        snapshot.source_refs,
        snapshot.event_ids,
    )
    return replace(
        snapshot,
        energy=values["energy"],
        valence=values["valence"],
        openness=values["openness"],
        tension=values["tension"],
        trust=values["trust"],
        familiarity=values["familiarity"],
        status=status,
        version=version,
    )


@dataclass
class _ClimateEntry:
    values: dict[ClimateDimension, float]
    baselines: dict[ClimateDimension, float]
    last_update: float
    revision: int
    source_status: ClimateStatus
    source_refs: deque[str]
    event_ids: deque[tuple[SensorName, str]]
    seen_events: set[tuple[SensorName, str]]
    generation: str | None = None
    source_proofs: tuple[ClimateSourceProof, ...] = ()


@dataclass
class _KeyLock:
    lock: asyncio.Lock
    references: int = 0


class ClimateEngine:
    """The sole mutable Climate owner, keyed by ``(bot, group, user)``."""

    def __init__(
        self,
        *,
        store: Store | None = None,
        decay_seconds: Mapping[str, float] | None = None,
        max_events_per_key: int = 256,
        max_sources_per_key: int = 32,
        max_keys: int = 1024,
        max_persistent_keys: int = 4096,
        max_future_skew_seconds: float = 5.0,
        sensor_hub: SensorHub | None = None,
    ) -> None:
        if (
            max_events_per_key < 1
            or max_sources_per_key < 1
            or max_keys < 1
            or max_persistent_keys < 1
        ):
            raise ValueError("climate memory limits must be positive")
        if not math.isfinite(max_future_skew_seconds) or max_future_skew_seconds < 0:
            raise ValueError("max_future_skew_seconds must be finite and non-negative")
        self._decay_seconds = dict(_DEFAULT_DECAY_SECONDS)
        for dimension, value in (decay_seconds or {}).items():
            if dimension not in _DIMENSIONS or not math.isfinite(value) or value <= 0:
                raise ValueError("decay_seconds must contain positive known dimensions")
            self._decay_seconds[dimension] = value
        self._max_events_per_key = max_events_per_key
        self._max_sources_per_key = max_sources_per_key
        self._max_keys = max_keys
        self._max_persistent_keys = max_persistent_keys
        self._max_future_skew_seconds = max_future_skew_seconds
        self._store = store
        # The Store serializes SQLite transactions. These locks only keep two
        # operations for the *same* climate scope in order; an unrelated group
        # must not wait behind a slow read that has not entered the Store yet.
        self._key_locks: dict[ClimateKey, _KeyLock] = {}
        self._sensor_hub = sensor_hub or SensorHub()
        self._entries: OrderedDict[ClimateKey, _ClimateEntry] = OrderedDict()
        self._last_evicted_key: ClimateKey | None = None
        self._eviction_count = 0

    @property
    def last_evicted_key(self) -> ClimateKey | None:
        """The most recent key evicted by the bounded LRU reservoir."""

        return self._last_evicted_key

    @property
    def eviction_count(self) -> int:
        """Number of keys evicted after the global memory bound was reached."""

        return self._eviction_count

    def snapshot(self, key: ClimateKey, *, now: float | None = None) -> ClimateSnapshot:
        _validate_key(key)
        as_of = time() if now is None else now
        _validate_timestamp(as_of)
        entry = self._entries.get(key)
        if entry is None:
            values = dict(_DEFAULT_BASELINES)
            return self._snapshot(
                key=key,
                values=values,
                revision=0,
                status="unavailable",
                source_refs=(),
                event_ids=(),
                as_of=as_of,
            )
        self._entries.move_to_end(key)
        values = self._resolve(entry, as_of)
        return self._snapshot(
            key=key,
            values=values,
            revision=entry.revision,
            status=entry.source_status,
            source_refs=tuple(entry.source_refs),
            event_ids=tuple(event_id for _, event_id in entry.event_ids),
            as_of=as_of,
            generation=entry.generation,
            source_proofs=entry.source_proofs,
        )

    def ingest(
        self,
        sensor: str,
        observation: SensorObservation | None,
        *,
        key: ClimateKey | None = None,
        now: float | None = None,
    ) -> ClimateSnapshot:
        self._reject_sync_persistent_write()
        read = self._sensor_hub.sense(sensor, observation)
        if read.key is None:
            if key is None:
                raise SignalValidationError("unavailable sensor read has no climate key")
            _validate_key(key)
            return self.snapshot(key, now=now)
        return self.apply_read(read, now=now)

    def apply_read(self, read: SensorRead, *, now: float | None = None) -> ClimateSnapshot:
        self._reject_sync_persistent_write()
        if read.key is None:
            raise SignalValidationError("sensor read has no climate key")
        if read.status is not SensorStatus.AVAILABLE or not read.signals:
            return self.snapshot(read.key, now=now)
        return self._apply_signals(read.key, read.signals, now=now)

    def apply_signal(self, signal: ClimateSignal, *, now: float | None = None) -> ClimateSnapshot:
        self._reject_sync_persistent_write()
        _validate_key(signal.key)
        return self._apply_signals(
            signal.key,
            (signal,),
            now=now,
        )

    def apply_feedback(
        self,
        key: ClimateKey,
        receipt: SendReceiptProof | None,
        *,
        now: float | None = None,
        familiarity_delta: float = 0.05,
    ) -> ClimateSnapshot:
        self._reject_sync_persistent_write()
        _validate_key(key)
        if receipt is None:
            raise FeedbackProofError("send receipt proof is required")
        if receipt.key != key:
            raise FeedbackProofError("send receipt proof scope mismatch")
        if not math.isfinite(familiarity_delta):
            raise SignalValidationError("feedback delta must be finite")
        bounded = max(-1.0, min(1.0, familiarity_delta))
        signal = ClimateSignal(
            key=key,
            sensor="interaction",
            dimension="familiarity",
            mode="delta",
            value=bounded,
            source_ref=f"send_receipt:{receipt.message_id}",
            event_id=receipt.event_id,
            observed_at=time() if now is None else now,
        )
        return self._apply_signals(
            key,
            (signal,),
            now=now,
            allow_receipt_feedback=True,
        )

    def _reject_sync_persistent_write(self) -> None:
        if self._store is not None:
            raise OperationError("climate_persistence_requires_async")

    def clear_stale(self, *, now: float | None = None, max_age_seconds: float) -> int:
        """Drop inactive per-user entries without a resident cleanup task."""

        as_of = time() if now is None else now
        _validate_timestamp(as_of)
        if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        stale_keys = [
            key
            for key, entry in self._entries.items()
            if as_of - entry.last_update > max_age_seconds
        ]
        for key in stale_keys:
            del self._entries[key]
        return len(stale_keys)

    def suggest(self, key: ClimateKey, *, now: float | None = None) -> ClimateSnapshot:
        """Return the current bounded state for a caller that wants a Climate hint.

        This is deliberately read-only with respect to the state values. It does
        not persist, send, or interpret chat content.
        """
        return self.snapshot(key, now=now)

    def export_state(self, key: ClimateKey) -> dict[str, object]:
        """Export a non-secret state record suitable for a durable adapter."""
        _validate_key(key)
        entry = self._entries.get(key)
        if entry is None:
            return {
                "key": key,
                "values": dict(_DEFAULT_BASELINES),
                "baselines": dict(_DEFAULT_BASELINES),
                "last_update": 0.0,
                "revision": 0,
                "source_status": "unavailable",
                "source_refs": (),
                "event_ids": (),
                "generation": None,
                "source_proofs": (),
            }
        return self._export_entry(key, entry)

    def import_state(self, state: Mapping[str, object]) -> ClimateSnapshot:
        """Import a validated state record into bounded memory only.

        Persistence is intentionally separate: callers that need a durable write
        must use ``apply_signal_persisted`` so the Store CAS commits first.
        """
        key_value = state.get("key")
        key_parts = cast(tuple[object, ...], key_value) if isinstance(key_value, tuple) else ()
        if (
            len(key_parts) != 3
            or not all(isinstance(part, str) for part in key_parts)
        ):
            raise SignalValidationError("climate state key is invalid")
        key: ClimateKey = cast(ClimateKey, key_value)
        _validate_key(key)
        values = _state_mapping(state.get("values"), "values")
        baselines = _state_mapping(state.get("baselines"), "baselines")
        last_update = _state_number(state.get("last_update"), "last_update", bounded=False)
        revision = state.get("revision")
        if type(revision) is not int or revision < 0:
            raise SignalValidationError("climate state revision is invalid")
        generation = state.get("generation")
        if generation is not None:
            if not isinstance(generation, str):
                raise SignalValidationError("climate state generation is invalid")
            _validate_identity(generation, "generation")
        source_status = state.get("source_status", "available")
        if not isinstance(source_status, str) or source_status not in (
            "available",
            "unavailable",
            "degraded",
        ):
            raise SignalValidationError("climate state source status is invalid")
        normalized_status: ClimateStatus = source_status
        source_refs = _state_refs(
            state.get("source_refs", ()), limit=self._max_sources_per_key
        )
        event_ids = _state_event_ids(
            state.get("event_ids", ()), limit=self._max_events_per_key
        )
        if len(source_refs) > self._max_sources_per_key:
            raise SignalValidationError("climate state source refs exceed the configured bound")
        if len(event_ids) > self._max_events_per_key:
            raise SignalValidationError("climate state event ids exceed the configured bound")
        source_proofs = state.get("source_proofs", ())
        if not isinstance(source_proofs, tuple):
            raise SignalValidationError("climate state source proofs are invalid")
        proof_parts = cast(tuple[object, ...], source_proofs)
        if not all(isinstance(source, ClimateSourceProof) and source.key == key for source in proof_parts):
            raise SignalValidationError("climate state source proofs are invalid")
        normalized_proofs = cast(tuple[ClimateSourceProof, ...], proof_parts)
        entry = _ClimateEntry(
            values=values,
            baselines=baselines,
            last_update=last_update,
            revision=revision,
            source_status=normalized_status,
            source_refs=deque(source_refs),
            event_ids=deque(event_ids),
            seen_events=set(event_ids),
            generation=generation,
            source_proofs=normalized_proofs,
        )
        if key in self._entries:
            self._entries[key] = entry
            self._entries.move_to_end(key)
        else:
            self._reserve_key(key, entry)
        return self.snapshot(key, now=last_update)

    async def load_persisted(
        self, key: ClimateKey, *, now: float | None = None, proof_now: float | None = None,
    ) -> ClimateSnapshot:
        """Restore one source-valid durable scope into the bounded in-memory LRU."""
        self._require_store()
        async with self._key_lock(key):
            return await self._load_persisted_unlocked(key, now=now, proof_now=proof_now)

    async def _load_persisted_unlocked(
        self, key: ClimateKey, *, now: float | None = None, proof_now: float | None = None,
    ) -> ClimateSnapshot:
        store = self._require_store()
        record = await store.climate_read(key, proof_now=proof_now, max_keys=self._max_persistent_keys)
        self._install_record(record)
        return self.snapshot(key, now=now)

    async def ingest_persisted(
        self, sensor: str, observation: SensorObservation | None, *, source: ClimateSourceProof,
        key: ClimateKey | None = None, now: float | None = None,
        expected_revision: int | None = None,
        authorize: Callable[[StoreConnection], object] | None = None,
        proof_now: float | None = None,
    ) -> ClimateSnapshot:
        """Convert and atomically persist one authenticated, bounded source observation."""
        read = self._sensor_hub.sense(sensor, observation)
        if read.key is None:
            if key is None:
                raise SignalValidationError("unavailable sensor read has no climate key")
            _validate_key(key)
            return await self.load_persisted(key, now=now, proof_now=proof_now)
        return await self.apply_read_persisted(
            read, source=source, now=now, expected_revision=expected_revision,
            authorize=authorize, proof_now=proof_now,
        )

    async def apply_read_persisted(
        self, read: SensorRead, *, source: ClimateSourceProof, now: float | None = None,
        expected_revision: int | None = None,
        authorize: Callable[[StoreConnection], object] | None = None,
        proof_now: float | None = None,
    ) -> ClimateSnapshot:
        if read.key is None:
            raise SignalValidationError("sensor read has no climate key")
        if read.status is not SensorStatus.AVAILABLE or not read.signals:
            return await self.load_persisted(read.key, now=now, proof_now=proof_now)
        return await self.apply_signals_persisted(
            read.key, read.signals, source=source, now=now, expected_revision=expected_revision,
            authorize=authorize, proof_now=proof_now,
        )

    async def apply_signal_persisted(
        self, signal: ClimateSignal, *, source: ClimateSourceProof, now: float | None = None,
        expected_revision: int | None = None,
        authorize: Callable[[StoreConnection], object] | None = None,
        proof_now: float | None = None,
    ) -> ClimateSnapshot:
        return await self.apply_signals_persisted(
            signal.key, (signal,), source=source, now=now, expected_revision=expected_revision,
            authorize=authorize, proof_now=proof_now,
        )

    async def apply_feedback_persisted(
        self, key: ClimateKey, receipt: SendReceiptProof | None, *, source: ClimateSourceProof,
        expected_generation: str, now: float | None = None, familiarity_delta: float = 0.05,
        expected_revision: int | None = None,
        authorize: Callable[[StoreConnection], object] | None = None,
        proof_now: float | None = None,
    ) -> ClimateSnapshot:
        """Persist only an actual successful action receipt in its original frozen epoch."""
        _validate_key(key)
        if not expected_generation:
            raise OperationError("stale_climate_source")
        if receipt is None:
            raise FeedbackProofError("send receipt proof is required")
        if receipt.key != key:
            raise FeedbackProofError("send receipt proof scope mismatch")
        if (source.kind != "receipt" or source.receipt_id != receipt.message_id
                or source.event_id != receipt.event_id):
            raise FeedbackProofError("send receipt source mismatch")
        if not math.isfinite(familiarity_delta):
            raise SignalValidationError("feedback delta must be finite")
        signal = ClimateSignal(
            key=key, sensor="interaction", dimension="familiarity", mode="delta",
            value=max(-1.0, min(1.0, familiarity_delta)), source_ref=f"send_receipt:{receipt.message_id}",
            event_id=receipt.event_id, observed_at=time() if now is None else now,
        )
        return await self._apply_signals_persisted_with_policy(
            key, (signal,), source=source, now=now, expected_revision=expected_revision,
            feedback_generation=(expected_generation,), allow_receipt_feedback=True,
            authorize=authorize, proof_now=proof_now,
        )

    async def apply_signals_persisted(
        self, key: ClimateKey, signals: tuple[ClimateSignal, ...], *, source: ClimateSourceProof,
        now: float | None = None, expected_revision: int | None = None,
        authorize: Callable[[StoreConnection], object] | None = None,
        proof_now: float | None = None,
    ) -> ClimateSnapshot:
        """Prepare any rollover, then CAS-commit a batch computed from that exact epoch."""
        return await self._apply_signals_persisted_with_policy(
            key, signals, source=source, now=now, expected_revision=expected_revision,
            feedback_generation=None, allow_receipt_feedback=False,
            authorize=authorize, proof_now=proof_now,
        )

    async def _apply_signals_persisted_with_policy(
        self, key: ClimateKey, signals: tuple[ClimateSignal, ...], *, source: ClimateSourceProof,
        now: float | None, expected_revision: int | None,
        feedback_generation: tuple[str] | None, allow_receipt_feedback: bool,
        authorize: Callable[[StoreConnection], object] | None, proof_now: float | None,
    ) -> ClimateSnapshot:
        self._require_store()
        async with self._key_lock(key):
            return await self._apply_signals_persisted_unlocked(
                key, signals, source=source, now=now, expected_revision=expected_revision,
                feedback_generation=feedback_generation, allow_receipt_feedback=allow_receipt_feedback,
                authorize=authorize, proof_now=proof_now,
            )

    async def _apply_signals_persisted_unlocked(
        self, key: ClimateKey, signals: tuple[ClimateSignal, ...], *, source: ClimateSourceProof,
        now: float | None, expected_revision: int | None,
        feedback_generation: tuple[str] | None, allow_receipt_feedback: bool,
        authorize: Callable[[StoreConnection], object] | None, proof_now: float | None,
    ) -> ClimateSnapshot:
        """The caller owns this key's lock; all persistent authority stays in Store."""
        store = self._require_store()
        _validate_key(key)
        if not signals:
            raise SignalValidationError("at least one climate signal is required")
        if any(signal.key != key for signal in signals):
            raise SignalValidationError("signal key does not match climate owner")
        for signal in signals:
            _validate_signal(signal, allow_receipt_feedback=allow_receipt_feedback)
        as_of = time() if now is None else now
        _validate_timestamp(as_of)
        event_map: dict[tuple[SensorName, str], ClimateEvent] = {}
        for signal in signals:
            event_map.setdefault((signal.sensor, signal.event_id), ClimateEvent(
                sensor=signal.sensor, event_id=signal.event_id, source_ref=signal.source_ref,
                observed_at=signal.observed_at, source=source,
            ))
        try:
            record = await store.climate_read(key, proof_now=proof_now, max_keys=self._max_persistent_keys)
            for evicted_key in record.evicted_keys:
                self._entries.pop(evicted_key, None)
            generation = record.generation
            if feedback_generation is not None and feedback_generation[0] != generation:
                raise OperationError("stale_climate_source")
            prepared = await store.climate_prepare(
                key, sources=(source,), events=tuple(event_map.values()),
                expected_generation=generation,
                expected_revision=expected_revision if expected_revision is not None else record.revision,
                authorize=authorize, proof_now=proof_now,
                max_events_per_key=self._max_events_per_key,
                max_sources_per_key=self._max_sources_per_key, max_keys=self._max_persistent_keys,
            )
            if feedback_generation is not None and feedback_generation[0] != prepared.generation:
                raise OperationError("stale_climate_source")
            neutral = prepared.source_status == "unavailable" and not prepared.source_proofs
            if neutral:
                self._entries.pop(key, None)
            prior = None if neutral else self._record_entry(prepared)
            candidate, _ = self._prepare_entry(prior, signals, as_of)
            assert candidate is not None
            committed, _ = await store.climate_commit(
                key, values={str(dimension): value for dimension, value in candidate.values.items()},
                baselines={str(dimension): value for dimension, value in candidate.baselines.items()},
                last_update=candidate.last_update,
                expected_revision=prepared.revision,
                expected_generation=prepared.generation,
                source_status=candidate.source_status, source_refs=tuple(candidate.source_refs),
                events=tuple(event_map.values()), sources=(source,), authorize=authorize, proof_now=proof_now,
                max_events_per_key=self._max_events_per_key,
                max_sources_per_key=self._max_sources_per_key, max_keys=self._max_persistent_keys,
            )
        except asyncio.CancelledError:
            # The Store drains owner work: a cancellation may arrive after commit.
            self._entries.clear()
            try:
                committed_after_cancel = await store.climate_read(
                    key, proof_now=proof_now, max_keys=self._max_persistent_keys,
                )
                self._install_record(committed_after_cancel)
            except (asyncio.CancelledError, Exception):
                self._entries.pop(key, None)
            raise
        except OperationError as exc:
            if exc.code == "stale_climate_source":
                self._entries.pop(key, None)
            raise
        self._install_record(committed)
        return self.snapshot(key, now=as_of)

    def _require_store(self) -> Store:
        if self._store is None:
            raise OperationError("climate_store_unconfigured")
        return self._store

    @asynccontextmanager
    async def _key_lock(self, key: ClimateKey) -> AsyncGenerator[None, None]:
        _validate_key(key)
        slot = self._key_locks.get(key)
        if slot is None:
            slot = _KeyLock(asyncio.Lock())
            self._key_locks[key] = slot
        slot.references += 1
        try:
            async with slot.lock:
                yield
        finally:
            slot.references -= 1
            if slot.references == 0 and self._key_locks.get(key) is slot:
                del self._key_locks[key]

    def _record_entry(self, record: ClimateStateRecord) -> _ClimateEntry:
        event_ids: deque[tuple[SensorName, str]] = deque()
        for sensor, event_id in record.event_ids[-self._max_events_per_key :]:
            if sensor not in _SENSOR_NAMES:
                raise SignalValidationError("persisted climate sensor is invalid")
            event_ids.append((sensor, event_id))
        return _ClimateEntry(
            values=dict(zip(_DIMENSIONS, record.values, strict=True)),
            baselines=dict(zip(_DIMENSIONS, record.baselines, strict=True)),
            last_update=record.last_update,
            revision=record.revision,
            source_status=record.source_status,  # type: ignore[arg-type]
            source_refs=deque(record.source_refs[-self._max_sources_per_key :]),
            event_ids=event_ids,
            seen_events=set(event_ids),
            generation=record.generation,
            source_proofs=record.source_proofs,
        )

    def _install_record(self, record: ClimateStateRecord) -> None:
        for evicted_key in record.evicted_keys:
            self._entries.pop(evicted_key, None)
        entry = self._record_entry(record)
        if record.key in self._entries:
            self._entries[record.key] = entry
            self._entries.move_to_end(record.key)
        else:
            self._reserve_key(record.key, entry)

    @staticmethod
    def _export_entry(key: ClimateKey, entry: _ClimateEntry) -> dict[str, object]:
        return {
            "key": key,
            "values": dict(entry.values),
            "baselines": dict(entry.baselines),
            "last_update": entry.last_update,
            "revision": entry.revision,
            "source_status": entry.source_status,
            "source_refs": tuple(entry.source_refs),
            "event_ids": tuple(entry.event_ids),
            "generation": entry.generation,
            "source_proofs": entry.source_proofs,
        }

    def _apply_signals(
        self,
        key: ClimateKey,
        signals: tuple[ClimateSignal, ...],
        *,
        now: float | None,
        allow_receipt_feedback: bool = False,
    ) -> ClimateSnapshot:
        as_of = time() if now is None else now
        _validate_timestamp(as_of)
        if any(signal.key != key for signal in signals):
            raise SignalValidationError("signal key does not match climate owner")
        for signal in signals:
            _validate_signal(signal, allow_receipt_feedback=allow_receipt_feedback)

        prior = self._entries.get(key)
        candidate, accepted = self._prepare_entry(prior, signals, as_of)
        if not accepted or candidate is None:
            return self.snapshot(key, now=as_of)
        if prior is None:
            self._reserve_key(key, candidate)
        else:
            self._entries[key] = candidate
            self._entries.move_to_end(key)
        return self.snapshot(key, now=as_of)

    def _prepare_entry(
        self,
        prior: _ClimateEntry | None,
        signals: tuple[ClimateSignal, ...],
        as_of: float,
    ) -> tuple[_ClimateEntry | None, bool]:
        """Build a candidate entry without changing the resident memory owner."""
        grouped: dict[tuple[SensorName, str], list[ClimateSignal]] = {}
        for signal in signals:
            grouped.setdefault((signal.sensor, signal.event_id), []).append(signal)

        seen_events: set[tuple[SensorName, str]] = (
            set() if prior is None else prior.seen_events
        )
        new_groups: list[tuple[tuple[SensorName, str], list[ClimateSignal], float]] = []
        for identity, event_signals in grouped.items():
            observed_at = event_signals[0].observed_at
            if any(signal.observed_at != observed_at for signal in event_signals[1:]):
                raise SignalOrderingError("one event identity has multiple observed times")
            source_ref = event_signals[0].source_ref
            if any(signal.source_ref != source_ref for signal in event_signals[1:]):
                raise SignalValidationError("one event identity has multiple source refs")
            if identity in seen_events:
                continue
            if observed_at > as_of + self._max_future_skew_seconds:
                raise SignalOrderingError("event is too far in the future")
            if prior is not None and observed_at < prior.last_update:
                raise SignalOrderingError("event is older than current timeline")
            new_groups.append((identity, event_signals, observed_at))

        if not new_groups:
            return prior, False

        entry = self._clone_entry(prior) if prior is not None else self._new_entry(new_groups[0][2])

        accepted = False
        for identity, event_signals, observed_at in sorted(
            new_groups, key=lambda group: group[2]
        ):
            current = self._resolve(entry, observed_at)
            for signal in event_signals:
                if signal.mode == "target":
                    current[signal.dimension] = signal.value
                else:
                    current[signal.dimension] = max(
                        0.0,
                        min(1.0, current[signal.dimension] + signal.value),
                    )
                if signal.source_ref not in entry.source_refs:
                    entry.source_refs.append(signal.source_ref)
                while len(entry.source_refs) > self._max_sources_per_key:
                    entry.source_refs.popleft()
            entry.seen_events.add(identity)
            entry.event_ids.append(identity)
            while len(entry.event_ids) > self._max_events_per_key:
                expired_event = entry.event_ids.popleft()
                entry.seen_events.discard(expired_event)
            entry.revision += 1
            entry.values = current
            entry.last_update = observed_at
            accepted = True
        return entry, accepted

    def _reserve_key(self, key: ClimateKey, entry: _ClimateEntry) -> None:
        if len(self._entries) >= self._max_keys:
            evicted_key, _ = self._entries.popitem(last=False)
            self._last_evicted_key = evicted_key
            self._eviction_count += 1
        self._entries[key] = entry

    def _new_entry(self, now: float) -> _ClimateEntry:
        return _ClimateEntry(
            values=dict(_DEFAULT_BASELINES),
            baselines=dict(_DEFAULT_BASELINES),
            last_update=now,
            revision=0,
            source_status="available",
            source_refs=deque(),
            event_ids=deque(),
            seen_events=set(),
        )

    @staticmethod
    def _clone_entry(entry: _ClimateEntry) -> _ClimateEntry:
        return _ClimateEntry(
            values=dict(entry.values),
            baselines=dict(entry.baselines),
            last_update=entry.last_update,
            revision=entry.revision,
            source_status=entry.source_status,
            source_refs=deque(entry.source_refs),
            event_ids=deque(entry.event_ids),
            seen_events=set(entry.seen_events),
            generation=entry.generation,
            source_proofs=entry.source_proofs,
        )

    def _resolve(self, entry: _ClimateEntry, now: float) -> dict[ClimateDimension, float]:
        elapsed = max(0.0, now - entry.last_update)
        return {
            dimension: entry.baselines[dimension]
            + (entry.values[dimension] - entry.baselines[dimension])
            * math.exp(-elapsed / self._decay_seconds[dimension])
            for dimension in _DIMENSIONS
        }

    def _snapshot(
        self,
        *,
        key: ClimateKey,
        values: Mapping[ClimateDimension, float],
        revision: int,
        status: ClimateStatus,
        source_refs: tuple[str, ...],
        event_ids: tuple[str, ...],
        as_of: float,
        generation: str | None = None,
        source_proofs: tuple[ClimateSourceProof, ...] = (),
    ) -> ClimateSnapshot:
        version = _climate_snapshot_version(
            key, revision, status, as_of, values, source_refs, event_ids, generation
        )
        return ClimateSnapshot(
            key=key,
            energy=values["energy"],
            valence=values["valence"],
            openness=values["openness"],
            tension=values["tension"],
            trust=values["trust"],
            familiarity=values["familiarity"],
            version=version,
            revision=revision,
            status=status,
            source_refs=source_refs,
            event_ids=event_ids,
            as_of=as_of,
            generation=generation,
            source_proofs=source_proofs,
        )


def _validate_key(key: ClimateKey) -> None:
    if len(key) != 3 or any(not part or part != part.strip() or len(part) > 64 for part in key):
        raise SignalValidationError("climate key must contain exact identifiers")


def _climate_snapshot_version(
    key: ClimateKey,
    revision: int,
    status: ClimateStatus,
    as_of: float,
    values: Mapping[ClimateDimension, float],
    source_refs: tuple[str, ...],
    event_ids: tuple[str, ...],
    generation: str | None = None,
) -> str:
    version_payload = "|".join(
        [
            *key,
            generation or "",
            str(revision),
            status,
            f"{as_of:.6f}",
            *(f"{dimension}={values[dimension]:.12f}" for dimension in _DIMENSIONS),
            *source_refs,
            *event_ids,
        ]
    )
    return _CLIMATE_VERSION_PREFIX + hashlib.sha256(version_payload.encode("utf-8")).hexdigest()


def _validate_identity(value: str, label: str) -> None:
    if not value or value != value.strip() or len(value) > 128:
        raise SignalValidationError(f"{label} must be an exact identifier")


def _validate_timestamp(value: float) -> None:
    if not math.isfinite(value):
        raise SignalValidationError("timestamp must be finite")


def _validate_signal(
    signal: ClimateSignal, *, allow_receipt_feedback: bool = False
) -> None:
    if (
        signal.sensor not in _SENSOR_NAMES
        or signal.dimension not in _DIMENSIONS
        or signal.dimension not in _SENSOR_DIMENSIONS[signal.sensor]
    ):
        raise SignalValidationError("signal name is unknown")
    _validate_key(signal.key)
    _validate_identity(signal.event_id, "event_id")
    _validate_identity(signal.source_ref, "source_ref")
    _validate_timestamp(signal.observed_at)
    if signal.mode not in ("target", "delta"):
        raise SignalValidationError("signal mode is unknown")
    if signal.mode != _SENSOR_MODES[signal.sensor]:
        is_authorized_feedback = (
            allow_receipt_feedback
            and signal.sensor == "interaction"
            and signal.dimension == "familiarity"
            and signal.mode == "delta"
        )
        if not is_authorized_feedback:
            if signal.sensor == "interaction" and signal.mode == "delta":
                raise SignalValidationError("interaction delta requires receipt proof")
            raise SignalValidationError("signal mode does not match sensor")
    if not math.isfinite(signal.value):
        raise SignalValidationError("signal value must be finite")
    if signal.mode == "target" and not 0.0 <= signal.value <= 1.0:
        raise SignalValidationError("target signal value must be bounded")
    if signal.mode == "delta" and not -1.0 <= signal.value <= 1.0:
        raise SignalValidationError("delta signal value must be bounded")


def _state_mapping(value: object, label: str) -> dict[ClimateDimension, float]:
    if not isinstance(value, Mapping):
        raise SignalValidationError(f"climate state {label} is invalid")
    mapping = cast(Mapping[object, object], value)
    if set(mapping) != set(_DIMENSIONS):
        raise SignalValidationError(f"climate state {label} is invalid")
    normalized: dict[ClimateDimension, float] = {}
    for dimension in _DIMENSIONS:
        raw = mapping[dimension]
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise SignalValidationError(f"climate state {label} is invalid")
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise SignalValidationError(f"climate state {label} is invalid") from exc
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise SignalValidationError(f"climate state {label} is invalid")
        normalized[dimension] = number
    return normalized


def _state_number(value: object, label: str, *, bounded: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SignalValidationError(f"climate state {label} is invalid")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SignalValidationError(f"climate state {label} is invalid") from exc
    if not math.isfinite(number) or (bounded and not 0.0 <= number <= 1.0):
        raise SignalValidationError(f"climate state {label} is invalid")
    return number


def _state_refs(value: object, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise SignalValidationError("climate state source refs are invalid")
    items = cast(tuple[object, ...] | list[object], value)
    if len(items) > limit:
        raise SignalValidationError("climate state source refs exceed the bound")
    refs: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item or item != item.strip() or len(item) > 128:
            raise SignalValidationError("climate state source refs are invalid")
        if item not in refs:
            refs.append(item)
    return tuple(refs)


def _state_event_ids(value: object, *, limit: int) -> tuple[tuple[SensorName, str], ...]:
    if not isinstance(value, (tuple, list)):
        raise SignalValidationError("climate state event ids are invalid")
    items = cast(tuple[object, ...] | list[object], value)
    if len(items) > limit:
        raise SignalValidationError("climate state event ids exceed the bound")
    event_ids: list[tuple[SensorName, str]] = []
    for item in items:
        if not isinstance(item, (tuple, list)):
            raise SignalValidationError("climate state event ids are invalid")
        pair = cast(tuple[object, ...] | list[object], item)
        if len(pair) != 2:
            raise SignalValidationError("climate state event ids are invalid")
        sensor, event_id = pair
        if not isinstance(sensor, str) or sensor not in _SENSOR_NAMES or not isinstance(event_id, str):
            raise SignalValidationError("climate state event ids are invalid")
        normalized_sensor = sensor
        _validate_identity(event_id, "event_id")
        identity = (normalized_sensor, event_id)
        if identity in event_ids:
            raise SignalValidationError("climate state event ids are duplicated")
        event_ids.append(identity)
    return tuple(event_ids)


def overlay_familiarity(snapshot: ClimateSnapshot, score: float, daily_count: int) -> ClimateSnapshot:
    """Verified long-term score plus today’s actual contributions supply legacy targets."""
    familiarity = round(min(1.0, .7 * score / 100.0 + .3 * min(1.0, daily_count / 10.0)), 4)
    return overlay_text_interaction_style(snapshot, TextInteractionStyle(
        "high", 1.0, 0.0, 0.0,
        (("familiarity", familiarity - snapshot.familiarity),
         ("trust", .5 + familiarity * .5 - snapshot.trust))))
