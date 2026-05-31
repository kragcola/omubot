from __future__ import annotations

import asyncio

import pytest

from services.llm.arbiter import InterruptionResult
from services.scheduler import (
    _CB_HALF_OPEN_S,
    _GATE_TIMEOUT_S,
    _MAX_ABORTS_PER_FIRE,
    _EmissionGate,
)


@pytest.mark.asyncio
async def test_gate_open_by_default() -> None:
    gate = _EmissionGate()
    assert await gate.check() is True
    assert await gate.check() is True


@pytest.mark.asyncio
async def test_gate_pending_then_continue() -> None:
    gate = _EmissionGate()
    await gate.check()
    gate.arm()
    gate.resolve(InterruptionResult(action="continue", reason="ok"))
    assert await gate.check() is True


@pytest.mark.asyncio
async def test_gate_pending_then_abort() -> None:
    gate = _EmissionGate()
    await gate.check()
    gate.arm()
    gate.resolve(InterruptionResult(action="revise", reason="user corrected"))
    assert await gate.check() is False
    assert gate.verdict is not None
    assert gate.verdict.action == "revise"


@pytest.mark.asyncio
async def test_l1_timeout_failopen() -> None:
    gate = _EmissionGate()
    await gate.check()
    gate.arm()
    result = await asyncio.wait_for(gate.check(), timeout=_GATE_TIMEOUT_S + 1.0)
    assert result is True


@pytest.mark.asyncio
async def test_l2_budget_exhausted() -> None:
    gate = _EmissionGate()
    await gate.check()
    for index in range(_MAX_ABORTS_PER_FIRE + 1):
        gate.arm()
        gate.resolve(InterruptionResult(action="revise", reason=f"abort {index}"))
    assert gate.abort_count == _MAX_ABORTS_PER_FIRE + 1
    assert await gate.check() is True


@pytest.mark.asyncio
async def test_l3_circuit_breaker() -> None:
    gate = _EmissionGate()
    await gate.check()
    for _ in range(3):
        gate.arm()
        gate.resolve(InterruptionResult(action="continue", reason="timeout", fallback=True), timed_out=True)
    assert gate.circuit_open is True
    gate.arm()
    assert await gate.check() is True
    assert gate.circuit_open is True
    assert _CB_HALF_OPEN_S > 0
