"""Dialogue Climate M3 — Sensor adapter layer.

M3 wraps existing signal sources as ``Sensor``s that emit ``ClimateSignal``s into
the M2 ``ClimateEngine``. Sensors are **pure source→signal converters**: each
takes a ``SensorInput`` snapshot (already-extracted raw values) and returns a
list of signals, so they unit-test without the full runtime. The ``SensorHub``
orchestrates them, gated by ``m3_sensors_enabled`` — when off, ``collect`` is a
no-op and nothing is fed to the engine (zero behaviour change).

Wave M3-1 ships three mature sources (Schedule / Irritation / Circadian). Wave
M3-2 adds Interaction (per-user familiarity), Calendar (calendar_context rich
day context) and Message (revived MoodClassifier). The ``SensorInput`` fields
for those are present but optional so the contract stays stable across waves.

The tension dimension's sole owner is the ClimateEngine. IrritationSensor emits
a bounded event delta for mention/poke bursts; no parallel tension state exists.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from services.dialogue_climate.state import NEUTRAL, ClimateSignal

# Circadian deltas (mirrors plugins/schedule/mood.py:533-542 late-night /
# post-lunch energy corrections, re-expressed as climate signals).
_CIRCADIAN_LATE_NIGHT_ENERGY = -0.2
_CIRCADIAN_POST_LUNCH_ENERGY = -0.05
_CIRCADIAN_POST_LUNCH_TENSION = -0.05

# Irritation event deltas.
_IRRITATION_MENTION = 0.03
_IRRITATION_POKE = 0.04
_IRRITATION_BURST_BONUS = 0.01
_IRRITATION_CAP = 0.2

@dataclass
class SensorInput:
    """Snapshot of already-extracted raw signal values for one sense pass.

    Decouples sensors from how values are fetched (MoodEngine, AffectionEngine,
    calendar, classifier) so they stay pure + unit-testable. All fields optional;
    a sensor returns no signals when its inputs are absent.
    """

    group_id: str = ""
    user_id: str = ""
    event_id: str = ""

    # ScheduleSensor: MoodEngine profile dims.
    mood_energy: float | None = None
    mood_valence: float | None = None
    mood_openness: float | None = None
    mood_tension: float | None = None

    # IrritationSensor: burst counts (already aggregated by qq_interactions).
    mention_count: int = 0
    poke_count: int = 0
    burst_continuation: bool = False

    # CircadianSensor: local hour [0, 24).
    hour: int | None = None

    # InteractionSensor (M3-2): per-user familiarity from AffectionEngine.
    familiarity: float | None = None

    # CalendarSensor (M3-2): rich day context flags.
    is_holiday: bool = False
    has_self_birthday: bool = False

    # MessageSensor (M3-2): classifier label/confidence.
    message_label: str = ""
    message_confidence: float = 0.0


class Sensor(ABC):
    """A pure source→signal converter."""

    name: str = "sensor"

    @abstractmethod
    def sense(self, data: SensorInput) -> list[ClimateSignal]:
        """Return climate signals for this input snapshot (may be empty)."""
        raise NotImplementedError


class ScheduleSensor(Sensor):
    """MoodEngine 4-dim profile → climate observation targets."""

    name = "schedule"

    def sense(self, data: SensorInput) -> list[ClimateSignal]:
        out: list[ClimateSignal] = []
        pairs = (
            ("energy", data.mood_energy),
            (
                "valence",
                None
                if data.mood_valence is None
                else (float(data.mood_valence) + 1.0) / 2.0,
            ),
            ("openness", data.mood_openness),
            ("tension", data.mood_tension),
        )
        for dim, value in pairs:
            if value is None:
                continue
            out.append(ClimateSignal(dim=dim, target=float(value), source=self.name))
        return out


class IrritationSensor(Sensor):
    """@ / poke burst counts → bounded tension delta (F2: sole tension owner)."""

    name = "irritation"

    def sense(self, data: SensorInput) -> list[ClimateSignal]:
        mentions = max(0, int(data.mention_count or 0))
        pokes = max(0, int(data.poke_count or 0))
        total = mentions + pokes
        if total <= 0:
            return []
        delta = (
            mentions * _IRRITATION_MENTION
            + pokes * _IRRITATION_POKE
            + (
                max(0, total - 1)
                + int(bool(data.burst_continuation))
            ) * _IRRITATION_BURST_BONUS
        )
        delta = max(0.0, min(_IRRITATION_CAP, delta))
        if not delta:
            return []
        return [ClimateSignal(dim="tension", delta=delta, source=self.name)]


class CircadianSensor(Sensor):
    """Local hour → bounded observation targets."""

    name = "circadian"

    def sense(self, data: SensorInput) -> list[ClimateSignal]:
        if data.hour is None:
            return []
        hour = int(data.hour) % 24
        out: list[ClimateSignal] = []
        if hour >= 23 or hour < 5:
            out.append(ClimateSignal(
                dim="energy",
                target=NEUTRAL + _CIRCADIAN_LATE_NIGHT_ENERGY,
                source=self.name,
            ))
        elif 12 <= hour < 14:
            out.append(ClimateSignal(
                dim="energy",
                target=NEUTRAL + _CIRCADIAN_POST_LUNCH_ENERGY,
                source=self.name,
            ))
            out.append(ClimateSignal(
                dim="tension",
                target=max(0.0, _CIRCADIAN_POST_LUNCH_TENSION),
                source=self.name,
            ))
        return out


class InteractionSensor(Sensor):
    """Per-user familiarity (AffectionEngine) → trust/familiarity signals.

    F1: ClimateEngine is keyed per-(group, user), so the per-user familiarity
        slot lands directly. Emits observation targets so repeated senses converge
        rather than ratchet.
    """

    name = "interaction"

    def sense(self, data: SensorInput) -> list[ClimateSignal]:
        if data.familiarity is None:
            return []
        fam = max(0.0, min(1.0, float(data.familiarity)))
        return [
            ClimateSignal(dim="familiarity", target=fam, source=self.name),
            ClimateSignal(
                dim="trust",
                target=NEUTRAL + fam * 0.5,
                source=self.name,
            ),
        ]


class CalendarSensor(Sensor):
    """Rich day context (calendar_context) → valence/energy signals.

    F3: source is the rich ``calendar_context`` DayContext (holiday / self
    birthday), not the lighter ``schedule/calendar``. Holidays lift valence; the
    bot's own birthday lifts valence + energy.
    """

    name = "calendar"

    def sense(self, data: SensorInput) -> list[ClimateSignal]:
        out: list[ClimateSignal] = []
        if data.has_self_birthday:
            out.append(ClimateSignal(dim="valence", target=0.8, source=self.name))
            out.append(ClimateSignal(dim="energy", target=0.7, source=self.name))
        elif data.is_holiday:
            out.append(ClimateSignal(dim="valence", target=0.65, source=self.name))
        return out


class MessageSensor(Sensor):
    """User message tone (revived MoodClassifier) → valence/openness/tension.

    F4: maps the classifier's label + confidence onto climate deltas. cold →
    tension+/valence-; tired → energy-/openness-; playful → valence+/openness+;
    high → energy+. Confidence scales the magnitude. The classifier does NOT
    write MOOD_CURRENT_SLOT here (that slot is the sticker-density channel) —
    MessageSensor only emits climate signals, avoiding the slot collision.
    """

    name = "message"

    _LABEL_DELTAS: ClassVar[dict[str, list[tuple[str, float]]]] = {
        "cold": [("tension", 0.15), ("valence", -0.15)],
        "tired": [("energy", -0.15), ("openness", -0.1)],
        "playful": [("valence", 0.2), ("openness", 0.15)],
        "high": [("energy", 0.2), ("valence", 0.1)],
    }

    def sense(self, data: SensorInput) -> list[ClimateSignal]:
        label = (data.message_label or "").strip().lower()
        conf = max(0.0, min(1.0, float(data.message_confidence or 0.0)))
        if label not in self._LABEL_DELTAS or conf <= 0.0:
            return []
        return [
            ClimateSignal(dim=dim, delta=base * conf, source=self.name)
            for dim, base in self._LABEL_DELTAS[label]
            if base * conf
        ]


class SensorHub:
    """Orchestrates sensors → ClimateEngine. Gated by ``m3_sensors_enabled``."""

    _MAX_SEEN_EVENT_IDS = 4096

    def __init__(self, engine: Any, *, m3_sensors_enabled: bool = False, sensors: list[Sensor] | None = None) -> None:
        self._engine = engine
        self._enabled = bool(m3_sensors_enabled)
        self._sensors = sensors if sensors is not None else default_sensors()
        self._seen_event_ids: dict[str, None] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def collect(self, data: SensorInput, *, now_ts: float | None = None) -> int:
        """Run all sensors and feed signals to the engine. No-op when disabled.

        Returns the number of signals registered.
        """
        if not self._enabled or self._engine is None:
            return 0
        event_id = str(data.event_id or "").strip()
        if event_id and event_id in self._seen_event_ids:
            return 0
        registered = 0
        for sensor in self._sensors:
            try:
                signals = sensor.sense(data)
            except Exception:  # a broken sensor must not block the others
                continue
            for sig in signals:
                if self._engine.register_signal(
                    dim=sig.dim,
                    delta=sig.delta,
                    target=sig.target,
                    source=sig.source,
                    group_id=data.group_id,
                    user_id=data.user_id,
                    now_ts=now_ts,
                ):
                    registered += 1
        if event_id:
            self._seen_event_ids[event_id] = None
            while len(self._seen_event_ids) > self._MAX_SEEN_EVENT_IDS:
                self._seen_event_ids.pop(next(iter(self._seen_event_ids)))
        return registered


def default_sensors() -> list[Sensor]:
    """The full M3 sensor set (M3-1 mature three + M3-2 Interaction/Calendar/Message)."""
    return [
        ScheduleSensor(),
        IrritationSensor(),
        CircadianSensor(),
        InteractionSensor(),
        CalendarSensor(),
        MessageSensor(),
    ]


__all__ = [
    "CalendarSensor",
    "CircadianSensor",
    "InteractionSensor",
    "IrritationSensor",
    "MessageSensor",
    "ScheduleSensor",
    "Sensor",
    "SensorHub",
    "SensorInput",
    "default_sensors",
]
