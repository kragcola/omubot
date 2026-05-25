from __future__ import annotations

import asyncio

import pytest

from services.reply_workflow import (
    ReplyGateFeatures,
    SemanticGateResult,
    evaluate_semantic_gate,
    semantic_gate_threshold,
    should_consume_semantic_gate,
)


def test_semantic_gate_threshold_dynamic_off_uses_fixed_value() -> None:
    threshold = semantic_gate_threshold(
        fixed_threshold=0.78,
        dynamic_enabled=False,
        familiarity=0.9,
        mood_energy=0.1,
    )

    assert threshold.effective_threshold == 0.78
    assert threshold.log_fields()["fixed_threshold"] == 0.78
    assert threshold.log_fields()["effective_threshold"] == 0.78
    assert threshold.log_fields()["dynamic_enabled"] is False
    assert threshold.log_fields()["threshold_adjustments"] == []


def test_semantic_gate_threshold_high_familiarity_lowers_bar() -> None:
    threshold = semantic_gate_threshold(
        fixed_threshold=0.78,
        dynamic_enabled=True,
        familiarity=0.7,
    )

    assert threshold.effective_threshold == 0.68
    assert threshold.adjustments == ("familiarity_high:-0.10",)


def test_semantic_gate_threshold_low_mood_raises_bar() -> None:
    threshold = semantic_gate_threshold(
        fixed_threshold=0.78,
        dynamic_enabled=True,
        mood_energy=0.2,
    )

    assert threshold.effective_threshold == 0.83
    assert threshold.adjustments == ("mood_low:+0.05",)


def test_semantic_gate_threshold_clamps_dynamic_range() -> None:
    lowered = semantic_gate_threshold(
        fixed_threshold=0.62,
        dynamic_enabled=True,
        familiarity=0.9,
    )
    raised = semantic_gate_threshold(
        fixed_threshold=0.84,
        dynamic_enabled=True,
        mood_energy=0.1,
    )

    assert lowered.effective_threshold == 0.6
    assert lowered.adjustments == ("familiarity_high:-0.10", "clamped")
    assert raised.effective_threshold == 0.85
    assert raised.adjustments == ("mood_low:+0.05", "clamped")


def test_semantic_gate_threshold_missing_state_degrades_to_fixed_value() -> None:
    threshold = semantic_gate_threshold(
        fixed_threshold=0.78,
        dynamic_enabled=True,
        familiarity=None,
        mood_energy=None,
    )

    assert threshold.effective_threshold == 0.78
    assert threshold.adjustments == ()


def test_semantic_gate_consumes_with_effective_threshold() -> None:
    threshold = semantic_gate_threshold(
        fixed_threshold=0.78,
        dynamic_enabled=True,
        familiarity=0.8,
    )
    result = SemanticGateResult(
        action="force_reply",
        confidence=0.72,
        intent="continue_or_expand",
        reason="要求继续",
    )

    assert should_consume_semantic_gate(result, threshold=threshold.effective_threshold)


@pytest.mark.asyncio
async def test_semantic_gate_timeout_still_fails_closed() -> None:
    async def api_call(_request):
        await asyncio.sleep(0.05)
        return {"text": '{"action":"force_reply","confidence":1.0}'}

    result = await evaluate_semantic_gate(
        ReplyGateFeatures(current_text="继续说呢", current_user_id="111"),
        api_call=api_call,
        timeout_ms=1,
    )

    assert result is None
