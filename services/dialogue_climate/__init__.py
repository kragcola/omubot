"""Dialogue Climate (Part A) services.

M1 ships only the dormant tension dimension; this package currently holds the
durable gray-run metrics recorder used to calibrate tau / threshold before the
M2 full-dimension engine is greenlit. The full ClimateState / dynamics engine
is reserved for M2+ under this same package.
"""

from services.dialogue_climate.m1_metrics import M1MetricsRecorder

__all__ = ["M1MetricsRecorder"]
