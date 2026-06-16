"""Dialogue Climate M2 — on-read closed-form dynamics engine.

Generalises the M1 single-dimension tension dynamics (``plugins/schedule/mood.py``
``resolve_m1_tension_on_read``) to the full ``ClimateState``. Three operations,
all closed-form over ``Δt`` (R5 — no resident tick, no momentum term, see Part A
§2.3):

1. ``resolve(state, now_ts)`` — decay every dimension toward its target:
   ``v(t) = target + (v_last − target)·exp(−λ·Δt)``, per-dimension λ.
2. ``apply_signal(state, signal, now_ts)`` — resolve, then exponential-smooth the
   sensed nudge in: ``v = α·(v_resolved + delta) + (1−α)·v_resolved``, with α
   raised by familiarity (close ties react faster — lacuna_core / inertia lit).
3. ``drift_baseline(state, now_ts)`` — baselines crawl toward the current value
   (drift) and toward NEUTRAL (regression), both closed-form over Δt.

CALIBRATION SOURCES (no live M1 sample existed at M2 build time — 2026-06-16 — so
the differential rates are anchored to published affect-dynamics data instead of
guessed; M3 re-tunes against real signals):

* Verduyn & Lavrijsen (2015), *Motivation and Emotion* 39:119–127 — of 27
  emotions, sadness lasts longest (~120 h) while irritation/surprise/shame are
  shortest; sadness persists up to ~240× longer than irritation. → fast vs slow
  dimension *ratio*: tension/irritation must be the fastest-decaying dimension;
  trust/familiarity the slowest.
* Emotional-inertia / AR(1) experience-sampling literature (Kuppens, Hamaker;
  de Haan-Rietdijk et al. 2017 continuous-time ESM) — affect carryover modelled
  as an autoregressive coefficient maps onto the smoothing α here, and the
  continuous-time treatment validates the on-read closed-form choice (R5).

λ are expressed **per hour** (Δt in seconds is divided by 3600 on read), so
``half_life_h = ln(2) / λ``. Defaults below match design master §9 in ordering
and keep tension fastest / trust slowest; the spread between them is widened
toward the Verduyn ratio. All overridable via ClimateDynamicsConfig.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from services.dialogue_climate.state import (
    BASELINE_DIMENSIONS,
    CLIMATE_DIMENSIONS,
    NEUTRAL,
    ClimateSignal,
    ClimateState,
    clamp01,
)

_SECONDS_PER_HOUR = 3600.0

# Per-hour decay constants. half_life_h = ln(2)/λ:
#   tension λ=0.69 → ~1.0h   (fast — irritation dissipates, Verduyn shortest)
#   energy  λ=0.35 → ~2.0h
#   valence λ=0.17 → ~4.1h
#   openness λ=0.14 → ~5.0h
#   trust   λ=0.012 → ~58h   (slow — built/eroded over days)
#   familiarity λ=0.004 → ~173h (~1 week, slowest — relationship memory)
# Ratio tension:familiarity ≈ 170×, in the spirit of Verduyn's sadness:irritation
# spread without copying its absolute (clinical, self-report) magnitudes.
DECAY_RATES: dict[str, float] = {
    "energy": 0.35,
    "valence": 0.17,
    "openness": 0.14,
    "tension": 0.69,
    "trust": 0.012,
    "familiarity": 0.004,
}

@dataclass
class ClimateDynamicsConfig:
    """Tunable dynamics parameters (design master §9 dynamics block).

    ``alpha`` is the base exponential-smoothing weight for incoming signals;
    ``inertia_familiarity_factor`` is how much familiarity raises the effective
    alpha (close ties react faster). ``baseline_drift_rate`` /
    ``baseline_regression_rate`` are per-hour rates for the slow baseline crawl.
    ``decay_rates`` overrides the per-dimension λ.
    """

    alpha: float = 0.2
    inertia_familiarity_factor: float = 0.3
    baseline_drift_rate: float = 0.01
    baseline_regression_rate: float = 0.003
    decay_rates: dict[str, float] = field(default_factory=lambda: dict(DECAY_RATES))


class ClimateDynamics:
    """Stateless on-read dynamics. Pure functions over (state, Δt, signal)."""

    def __init__(self, config: ClimateDynamicsConfig | None = None) -> None:
        self._cfg = config or ClimateDynamicsConfig()

    def _decay_factor(self, dim: str, elapsed_s: float) -> float:
        lam = float(self._cfg.decay_rates.get(dim, DECAY_RATES.get(dim, 0.0)))
        elapsed_h = max(0.0, float(elapsed_s)) / _SECONDS_PER_HOUR
        return math.exp(-lam * elapsed_h)

    def resolve(self, state: ClimateState, now_ts: float) -> ClimateState:
        """Return a new state with every dimension decayed toward its target.

        ``v(t) = target + (v_last − target)·exp(−λ·Δt)``. Idempotent at Δt=0.
        Does not advance baselines (see ``drift_baseline``) but stamps the read.
        """
        elapsed_s = max(0.0, float(now_ts) - float(state.last_update_ts))
        resolved = ClimateState.from_dict(state.to_dict())
        for dim in CLIMATE_DIMENSIONS:
            target = state.baseline_for(dim)
            current = state.get(dim)
            factor = self._decay_factor(dim, elapsed_s)
            resolved.set(dim, target + (current - target) * factor)
        resolved.last_update_ts = float(now_ts)
        return resolved

    def _effective_alpha(self, familiarity: float) -> float:
        """Base alpha raised by familiarity — close ties react faster."""
        boost = self._cfg.inertia_familiarity_factor * clamp01(familiarity)
        return clamp01(self._cfg.alpha + boost)

    def apply_signal(
        self, state: ClimateState, signal: ClimateSignal, now_ts: float
    ) -> ClimateState:
        """Resolve to ``now_ts`` then exponentially smooth ``signal`` in.

        No-op (returns a resolved-only state) for an invalid/zero signal. The
        nudged value is ``α·(resolved+delta) + (1−α)·resolved`` = ``resolved +
        α·delta``, clamped to [0, 1]; α is familiarity-boosted.
        """
        resolved = self.resolve(state, now_ts)
        if not signal.is_valid():
            return resolved
        alpha = self._effective_alpha(resolved.familiarity)
        current = resolved.get(signal.dim)
        resolved.set(signal.dim, current + alpha * float(signal.delta))
        resolved.update_count = state.update_count + 1
        return resolved

    def drift_baseline(self, state: ClimateState, now_ts: float) -> ClimateState:
        """Crawl baselines toward current value (drift) and NEUTRAL (regression).

        Closed-form per Δt: each step the baseline moves a fraction
        ``1−exp(−rate·Δt_h)`` toward the current dimension value, then the same
        toward NEUTRAL. Slow by design (rates ~0.01/h); shapes hysteresis without
        a momentum term (Part A §2.3).
        """
        elapsed_h = max(0.0, float(now_ts) - float(state.last_update_ts)) / _SECONDS_PER_HOUR
        drifted = ClimateState.from_dict(state.to_dict())
        drift_w = 1.0 - math.exp(-self._cfg.baseline_drift_rate * elapsed_h)
        regress_w = 1.0 - math.exp(-self._cfg.baseline_regression_rate * elapsed_h)
        for dim in BASELINE_DIMENSIONS:
            base = state.get_baseline_value(dim)
            base += drift_w * (state.get(dim) - base)
            base += regress_w * (NEUTRAL - base)
            drifted.set_baseline(dim, base)
        return drifted


class ClimateEngine:
    """Per-(group, session) transient ClimateState container.

    Mirrors the MoodEngine M1 pattern: state is in-memory only, keyed by
    (group_id, session_id), resolved on read with closed-form decay. Gated by
    ``m2_enabled`` — when disabled, ``register_signal`` is a no-op and
    ``resolve`` returns a fresh neutral state without storing anything, so the
    engine is a zero-behaviour-change increment until M3 wires sensors in.

    Dormant by design: no reply-path consumer reads this in M2.
    """

    def __init__(
        self,
        *,
        m2_enabled: bool = False,
        config: ClimateDynamicsConfig | None = None,
    ) -> None:
        self._enabled = bool(m2_enabled)
        self._dynamics = ClimateDynamics(config)
        self._states: dict[tuple[str, str], ClimateState] = {}

    @staticmethod
    def _key(group_id: str | int | None, session_id: str) -> tuple[str, str]:
        return (str(group_id or ""), str(session_id or ""))

    @property
    def enabled(self) -> bool:
        return self._enabled

    def register_signal(
        self,
        *,
        dim: str,
        delta: float,
        source: str = "",
        group_id: str | int | None = None,
        session_id: str = "",
        now_ts: float | None = None,
    ) -> bool:
        """Apply a sensed nudge to the per-key state. No-op when disabled."""
        if not self._enabled or not delta:
            return False
        now = time.monotonic() if now_ts is None else float(now_ts)
        signal = ClimateSignal(dim=dim, delta=float(delta), source=source, ts=now)
        if not signal.is_valid():
            return False
        key = self._key(group_id, session_id)
        prior = self._states.get(key)
        if prior is None:
            prior = ClimateState.neutral()
            prior.last_update_ts = now
        self._states[key] = self._dynamics.apply_signal(prior, signal, now)
        return True

    def resolve(
        self,
        *,
        group_id: str | int | None = None,
        session_id: str = "",
        now_ts: float | None = None,
    ) -> ClimateState:
        """Read the on-read-decayed state. Neutral (unstored) when disabled."""
        if not self._enabled:
            return ClimateState.neutral()
        key = self._key(group_id, session_id)
        state = self._states.get(key)
        if state is None:
            return ClimateState.neutral()
        now = time.monotonic() if now_ts is None else float(now_ts)
        resolved = self._dynamics.resolve(state, now)
        self._states[key] = resolved
        return resolved

    def state_count(self) -> int:
        """Number of stored per-key states (observability / test hook)."""
        return len(self._states)


__all__ = [
    "DECAY_RATES",
    "ClimateDynamics",
    "ClimateDynamicsConfig",
    "ClimateEngine",
]


