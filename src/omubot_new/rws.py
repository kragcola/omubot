"""Pure, source-labeled RWS scoring for the gray participation branch.

This module deliberately has no model, store, policy, queue, or sender access.
The neutral score is a decision score for the explicitly configured threshold;
it is not a calibrated probability or a claim of equivalence with the legacy
scheduler. Missing and disabled inputs contribute zero logit and remain visible
in the returned explanation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

SignalStatus = Literal["available", "missing", "disabled"]
RwsAction = Literal["fire", "skip"]
RwsState = Literal["scored", "neutral_no_weighted_signals"]
RwsSignalName = Literal[
    "legacy_threshold",
    "eot_probability",
    "hawkes_rho",
    "outcome_ratio",
    "reward_feedback",
    "memory_familiarity",
    "willingness_phase",
    "info_gain",
    "skip_pressure",
    "mood_residual",
    "schedule_residual",
]

_SIGNAL_NAMES: tuple[RwsSignalName, ...] = (
    "legacy_threshold",
    "eot_probability",
    "hawkes_rho",
    "outcome_ratio",
    "reward_feedback",
    "memory_familiarity",
    "willingness_phase",
    "info_gain",
    "skip_pressure",
    "mood_residual",
    "schedule_residual",
)
_WEIGHTED_SIGNALS = frozenset(
    {
        "legacy_threshold",
        "eot_probability",
        "hawkes_rho",
        "outcome_ratio",
        "memory_familiarity",
        "willingness_phase",
    }
)


@dataclass(frozen=True)
class RwsSignal:
    """One bounded feature with explicit provenance and availability."""

    name: RwsSignalName
    status: SignalStatus
    source: str
    reason: str
    value: float | None = None
    calibrated: bool = False

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.reason.strip():
            raise ValueError("RWS signals require a source and reason")
        if self.status == "available":
            if self.value is None or not math.isfinite(self.value):
                raise ValueError("available RWS signals require a finite value")
            if self.name == "eot_probability" and not self.calibrated:
                raise ValueError("EOT may be available only with a calibrated score")
            if self.name != "eot_probability" and self.calibrated:
                raise ValueError("calibrated metadata applies only to EOT scores")
            if self.name == "reward_feedback":
                raise ValueError("reward feedback is disabled until its loop is implemented")
        elif self.value is not None or self.calibrated:
            raise ValueError("missing or disabled RWS signals cannot carry values")


@dataclass(frozen=True)
class RwsScore:
    """Deterministic score and action, explicitly not a probability."""

    decision_score: float
    threshold: float
    action: RwsAction
    state: RwsState
    signals: tuple[RwsSignal, ...]
    terms: tuple[tuple[str, float], ...]

    def to_dict(self) -> dict[str, object]:
        """Return a body-free diagnostic suitable for bounded in-memory traces."""
        return {
            "decision_score": self.decision_score,
            "threshold": self.threshold,
            "action": self.action,
            "state": self.state,
            "signals": [
                {
                    "name": signal.name,
                    "status": signal.status,
                    "source": signal.source,
                    "reason": signal.reason,
                    "value": signal.value,
                    "calibrated": signal.calibrated,
                }
                for signal in self.signals
            ],
            "terms": dict(self.terms),
        }


def gray_zone_signals() -> tuple[RwsSignal, ...]:
    """Describe the N3 inputs that are actually connected today.

    A's complete/hold result is a Boolean completeness gate, not an EOT score.
    Reward, Hawkes and bandit remain off until their own feedback/source
    contracts are implemented. These records therefore take the named neutral
    path instead of inventing values.
    """
    return (
        RwsSignal(
            "legacy_threshold",
            "missing",
            "conversation.gray_default",
            "no_independent_legacy_propensity_score",
        ),
        RwsSignal(
            "eot_probability",
            "missing",
            "arbiter_a",
            "complete_hold_not_probability",
        ),
        RwsSignal(
            "hawkes_rho",
            "disabled",
            "legacy_hawkes",
            "disabled_by_current_n3_policy",
        ),
        RwsSignal(
            "outcome_ratio",
            "missing",
            "legacy_memory_signals.recent_outcome_ratio",
            "memory_episode_source_not_connected",
        ),
        RwsSignal(
            "reward_feedback",
            "disabled",
            "rws_reward_queue",
            "reward_feedback_loop_not_connected",
        ),
        RwsSignal(
            "memory_familiarity",
            "missing",
            "memory_store",
            "n6_memory_source_not_connected",
        ),
        RwsSignal(
            "willingness_phase",
            "missing",
            "relationship_state",
            "relationship_signal_not_connected",
        ),
        RwsSignal(
            "info_gain",
            "missing",
            "retrieval",
            "n6_retrieval_not_connected",
        ),
        RwsSignal(
            "skip_pressure",
            "missing",
            "conversation.skip_history",
            "skip_pressure_not_connected",
        ),
        RwsSignal(
            "mood_residual",
            "missing",
            "dialogue_climate",
            "n4_climate_signal_not_connected",
        ),
        RwsSignal(
            "schedule_residual",
            "missing",
            "schedule",
            "n7_schedule_signal_not_connected",
        ),
    )


def compute_rws(
    signals: tuple[RwsSignal, ...], *, threshold: float
) -> RwsScore:
    """Apply the old RWS feature signs to only explicitly available sources.

    These are deterministic decision-score weights inherited from the legacy
    RWS formula, not fitted or calibrated coefficients. In this N3 slice the
    live Conversation supplies only named missing/disabled inputs.
    """
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("RWS threshold must be between zero and one")
    names = [signal.name for signal in signals]
    if len(names) != len(set(names)) or set(names) != set(_SIGNAL_NAMES):
        raise ValueError("RWS requires exactly one source record per known signal")

    terms: dict[str, float] = {}
    for signal in signals:
        if signal.status != "available":
            continue
        assert signal.value is not None
        value = signal.value
        if signal.name in {
            "legacy_threshold",
            "eot_probability",
            "outcome_ratio",
            "memory_familiarity",
            "willingness_phase",
            "hawkes_rho",
            "info_gain",
            "skip_pressure",
        } and not 0 <= value <= 1:
            raise ValueError(f"{signal.name} must be between zero and one")
        if signal.name in {"mood_residual", "schedule_residual"} and not -1 <= value <= 1:
            raise ValueError(f"{signal.name} must be between minus one and one")
        if signal.name == "legacy_threshold":
            probability = min(0.999, max(0.001, value))
            terms[signal.name] = math.log(probability / (1 - probability))
        elif signal.name == "eot_probability":
            if signal.calibrated:
                terms[signal.name] = (value - 0.5) * 2.0
        elif signal.name == "hawkes_rho":
            terms[signal.name] = -1.3 * value
        elif signal.name == "outcome_ratio":
            terms[signal.name] = 0.08 * (value - 0.5) * 2.0
        elif signal.name == "memory_familiarity":
            terms[signal.name] = 0.06 * value
        elif signal.name == "willingness_phase":
            terms[signal.name] = 0.08 * (value - 0.5) * 2.0
        # The following fields are recorded for source visibility but their
        # legacy weight is zero in the inherited formula.
        elif signal.name in {
            "reward_feedback",
            "info_gain",
            "skip_pressure",
            "mood_residual",
            "schedule_residual",
        }:
            terms[signal.name] = 0.0

    logit = sum(terms.values())
    if logit >= 0:
        exp_term = math.exp(-logit)
        score = 1.0 / (1.0 + exp_term)
    else:
        exp_term = math.exp(logit)
        score = exp_term / (1.0 + exp_term)
    state: RwsState = (
        "scored"
        if any(
            signal.status == "available" and signal.name in _WEIGHTED_SIGNALS
            for signal in signals
        )
        else "neutral_no_weighted_signals"
    )
    action: RwsAction = "fire" if score >= threshold else "skip"
    return RwsScore(
        decision_score=score,
        threshold=threshold,
        action=action,
        state=state,
        signals=signals,
        terms=tuple(sorted(terms.items())),
    )
