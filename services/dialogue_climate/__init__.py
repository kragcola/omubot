"""Dialogue Climate (Part A) services.

M1 shipped only the dormant tension dimension; this package holds the durable
gray-run metrics recorder used to calibrate tau / threshold. M2 adds the full
six-dimension ``ClimateState`` + on-read ``ClimateDynamics`` / ``ClimateEngine``
(``state.py`` / ``dynamics.py``), dormant behind ``m2_enabled`` until M3 wires
sensors in.
"""

from services.dialogue_climate.dynamics import (
    DECAY_RATES,
    ClimateDynamics,
    ClimateDynamicsConfig,
    ClimateEngine,
)
from services.dialogue_climate.m1_metrics import M1MetricsRecorder
from services.dialogue_climate.state import (
    CLIMATE_DIMENSIONS,
    ClimateSignal,
    ClimateState,
)

__all__ = [
    "CLIMATE_DIMENSIONS",
    "DECAY_RATES",
    "ClimateDynamics",
    "ClimateDynamicsConfig",
    "ClimateEngine",
    "ClimateSignal",
    "ClimateState",
    "M1MetricsRecorder",
]

