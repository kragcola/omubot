"""Lightweight BlockTrace observations for auxiliary LLM calls."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from loguru import logger

from services.block_trace.types import PromptBlockTrace

_L = logger.bind(channel="block_trace")


async def record_llm_call_trace(
    store: object | None,
    *,
    request_id: str,
    task: str,
    provider: str,
    session_id: str = "",
    group_id: str = "",
    user_id: str = "",
    turn_id: str = "",
    event_id: str = "",
    candidate_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record an observation-only trace for a non-main LLM call.

    This is deliberately fail-soft: trace persistence must never change reply
    routing, thinker decisions, or semantic-gate consumption.
    """
    if store is None or not hasattr(store, "record"):
        return
    call_ref = turn_id or event_id or request_id
    trace_metadata: dict[str, Any] = {
        "session_id": session_id,
        "group_id": group_id,
        "user_id": user_id,
        "turn_id": turn_id,
        "event_id": event_id,
        "observer": "u13_double_haiku_trace",
    }
    if metadata:
        trace_metadata.update(metadata)
    trace = PromptBlockTrace(
        trace_id="",
        request_id=request_id,
        task=task,
        source="system",
        provider=provider,
        candidate_id=candidate_id or f"llm_{provider}:{call_ref}",
        decision="shadow_only",
        hit_reason="u13_double_haiku_observation",
        evidence_refs=(),
        token_estimate=0,
        char_count=0,
        position="dynamic",
        label="LLM调用观测",
        priority=100,
        budget_reason="shadow: U13 double-haiku trace",
        metadata=trace_metadata,
    )
    try:
        await cast(Any, store).record(trace)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _L.debug(
            "llm call trace skipped | request={} task={} provider={} err={}",
            request_id,
            task,
            provider,
            exc,
        )
