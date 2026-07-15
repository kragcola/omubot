"""Dialogue Climate M2 — unified ClimateState + ClimateSignal.

Part A M1 (see ``plugins/schedule/mood.py``) shipped a single tension dimension
as an in-memory ``(value, baseline, last_ts)`` triple resolved with closed-form
decay. M2 generalises that *same* on-read mechanism to the full six-dimension
``ClimateState`` described in the Dialogue Climate design master
(``docs/tracking/omubot-grayscale-issue17-research-dialogue-climate.md`` §4/§5)
while honouring the Part A §2 R5 decision: **no resident tick, no momentum term**
— state is always a closed-form analytic function of ``Δt = now − last_ts``.

This module is the pure data layer. Dynamics live in ``dynamics.py``. Per the
M2 dispatch boundary it is **dormant**: nothing reads ``ClimateState`` in the
reply path until M3 (sensors) / M4 (policy/adapters). The ``m2_enabled`` flag
defaults off, so wiring this package is a zero-behaviour-change increment.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


def clamp01(value: float) -> float:
    """Clamp a value into the closed unit interval [0.0, 1.0]."""
    return max(0.0, min(1.0, float(value)))


# The six dimensions carried by ClimateState. ``tension`` is fast-decaying
# (irritation dissipates quickly); ``trust`` / ``familiarity`` are slow (built
# and eroded over many interactions). See ``dynamics.DECAY_RATES`` for the
# empirically-anchored per-dimension constants and their sources.
CLIMATE_DIMENSIONS: tuple[str, ...] = (
    "energy",
    "valence",
    "openness",
    "tension",
    "trust",
    "familiarity",
)

# Dimensions that carry a slow-drifting baseline (the others regress to a fixed
# neutral). Mirrors design master §5 Phase 1 (baseline_energy/valence/openness).
BASELINE_DIMENSIONS: tuple[str, ...] = ("energy", "valence", "openness")

# Neutral resting point for bounded affect dimensions.
NEUTRAL: float = 0.5

@dataclass
class ClimateSignal:
    """A single sensed nudge toward a dimension, the M3 sensor output contract.

    M2 only *defines* this contract; no sensor produces signals yet. ``dim`` must
    be one of ``CLIMATE_DIMENSIONS``; ``delta`` is the raw signed nudge before
    smoothing; ``source`` is a free-form sensor tag for observability; ``ts`` is
    a monotonic timestamp (``time.monotonic()``) used by the dynamics engine.
    """

    dim: str
    delta: float = 0.0
    target: float | None = None
    source: str = ""
    ts: float = 0.0

    def is_valid(self) -> bool:
        return self.dim in CLIMATE_DIMENSIONS and (
            self.target is not None or bool(self.delta)
        )


@dataclass
class ClimateState:
    """Unified six-dimension affect state with slow-drifting baselines.

    Field set is byte-aligned with design master §5 Phase 1. ``trust`` and
    ``familiarity`` are conceptually per-user; M2 stores state per (group,
    session) key like M1 and leaves per-user fan-out to M3. All six dimensions
    and the three baselines live in [0, 1]; ``last_update_ts`` is monotonic.
    """

    energy: float = NEUTRAL
    valence: float = NEUTRAL
    openness: float = NEUTRAL
    tension: float = 0.0
    trust: float = NEUTRAL
    familiarity: float = 0.0

    # Slow-drifting baselines (only for BASELINE_DIMENSIONS).
    baseline_energy: float = NEUTRAL
    baseline_valence: float = NEUTRAL
    baseline_openness: float = NEUTRAL

    # Metadata.
    last_update_ts: float = 0.0
    update_count: int = 0

    def baseline_for(self, dim: str) -> float:
        """Return the decay target for ``dim``.

        BASELINE_DIMENSIONS decay toward their drifting baseline; tension decays
        toward 0.0 (calm); trust/familiarity decay toward NEUTRAL/0.0 resting.
        """
        if dim == "energy":
            return self.baseline_energy
        if dim == "valence":
            return self.baseline_valence
        if dim == "openness":
            return self.baseline_openness
        if dim == "tension":
            return 0.0
        if dim == "trust":
            return NEUTRAL
        if dim == "familiarity":
            return 0.0
        raise ValueError(f"unknown climate dimension: {dim!r}")

    def get(self, dim: str) -> float:
        if dim not in CLIMATE_DIMENSIONS:
            raise ValueError(f"unknown climate dimension: {dim!r}")
        return float(getattr(self, dim))

    def set(self, dim: str, value: float) -> None:
        if dim not in CLIMATE_DIMENSIONS:
            raise ValueError(f"unknown climate dimension: {dim!r}")
        setattr(self, dim, clamp01(value))

    def set_baseline(self, dim: str, value: float) -> None:
        if dim not in BASELINE_DIMENSIONS:
            raise ValueError(f"{dim!r} has no drifting baseline")
        setattr(self, f"baseline_{dim}", clamp01(value))

    def get_baseline_value(self, dim: str) -> float:
        if dim not in BASELINE_DIMENSIONS:
            raise ValueError(f"{dim!r} has no drifting baseline")
        return float(getattr(self, f"baseline_{dim}"))

    def clamp_all(self) -> None:
        """Clamp every stored dimension + baseline back into [0, 1]."""
        for dim in CLIMATE_DIMENSIONS:
            setattr(self, dim, clamp01(getattr(self, dim)))
        for dim in BASELINE_DIMENSIONS:
            setattr(self, f"baseline_{dim}", clamp01(getattr(self, f"baseline_{dim}")))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClimateState:
        """Rebuild from a dict, ignoring unknown keys and clamping bounds."""
        fields = set(cls.__dataclass_fields__)
        state = cls(**{k: v for k, v in (data or {}).items() if k in fields})
        state.clamp_all()
        return state

    @classmethod
    def neutral(cls) -> ClimateState:
        """A fresh resting state (the value M2 returns when disabled)."""
        return cls()


__all__ = [
    "BASELINE_DIMENSIONS",
    "CLIMATE_DIMENSIONS",
    "NEUTRAL",
    "ClimateSignal",
    "ClimateState",
    "clamp01",
]
