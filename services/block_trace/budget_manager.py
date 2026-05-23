"""PromptBudgetManager — prioritize, trim, and trace prompt blocks."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from typing import Any

from loguru import logger

from kernel.types import PromptBlock
from services.admin_events import publish_block_trace_recorded
from services.block_trace.store import BlockTraceStore
from services.block_trace.types import (
    AcceptedDecision,
    BudgetDecision,
    PromptBlockCandidate,
    PromptBlockTrace,
)

_L = logger.bind(channel="budget")


class PromptBudgetManager:
    """Post-hook that sits between fire_on_pre_prompt and build_blocks.

    Sorts blocks by priority within each position bucket, enforces per-bucket
    character budgets, and records a PromptBlockTrace for every block decision.
    In Step 1 the budgets are deliberately loose — the primary goal is tracing.
    """

    def __init__(
        self,
        trace_store: BlockTraceStore,
        *,
        max_static_chars: int = 1500,
        max_stable_chars: int = 2000,
        max_dynamic_chars: int = 4000,
        slang_store_getter: Callable[[], Any] | None = None,
        style_store_getter: Callable[[], Any] | None = None,
        episode_store_getter: Callable[[], Any] | None = None,
    ) -> None:
        self._store = trace_store
        self._slang_store_getter = slang_store_getter
        self._style_store_getter = style_store_getter
        self._episode_store_getter = episode_store_getter
        self._budgets = {
            "static": max_static_chars,
            "stable": max_stable_chars,
            "dynamic": max_dynamic_chars,
        }

    def process(
        self,
        candidates: list[PromptBlockCandidate],
        *,
        request_id: str,
        task: str = "main",
        session_id: str = "",
        group_id: str | None = None,
    ) -> tuple[list[PromptBlock], list[AcceptedDecision]]:
        """Prioritize, trim, trace. Returns surviving blocks and accepted refs."""
        del session_id
        buckets: dict[str, list[PromptBlockCandidate]] = {
            "static": [],
            "stable": [],
            "dynamic": [],
        }
        for candidate in candidates:
            key = candidate.position if candidate.position in buckets else "dynamic"
            buckets[key].append(candidate)

        for key in buckets:
            buckets[key].sort(key=lambda c: c.priority)

        surviving: list[PromptBlock] = []
        accepted_decisions: list[AcceptedDecision] = []
        observation_decisions: list[AcceptedDecision] = []
        traces: list[PromptBlockTrace] = []

        for position in ("static", "stable", "dynamic"):
            budget = self._budgets[position]
            used = 0
            for candidate in buckets[position]:
                char_count = candidate.char_count or len(candidate.text)
                remaining = budget - used

                if remaining >= char_count:
                    decision: BudgetDecision = "accepted"
                    reason = f"accepted: {char_count} chars within budget ({remaining} remaining)"
                    surviving.append(_candidate_to_prompt_block(candidate, position=position))
                    accepted_decision = _accepted_decision(
                        candidate,
                        char_count=char_count,
                        decision=decision,
                    )
                    accepted_decisions.append(accepted_decision)
                    observation_decisions.append(accepted_decision)
                    used += char_count
                elif remaining > 0:
                    decision = "trimmed"
                    trimmed = _candidate_to_prompt_block(
                        candidate,
                        text=candidate.text[:remaining],
                        position=position,
                    )
                    reason = f"trimmed: {char_count} -> {remaining} chars (budget exhausted)"
                    surviving.append(trimmed)
                    observation_decisions.append(_accepted_decision(
                        candidate,
                        char_count=remaining,
                        decision=decision,
                    ))
                    used += remaining
                else:
                    decision = "rejected"
                    reason = f"rejected: no budget left (need {char_count}, have 0)"

                traces.append(PromptBlockTrace(
                    trace_id="bt_" + secrets.token_hex(6),
                    request_id=request_id,
                    task=task,
                    source=candidate.source or "unknown",
                    provider=candidate.provider or (
                        candidate.source + "_plugin" if candidate.source else "unknown"
                    ),
                    candidate_id=candidate.candidate_id,
                    decision=decision,
                    hit_reason=candidate.hit_reason or candidate.label,
                    evidence_refs=candidate.evidence_refs,
                    token_estimate=char_count // 3,
                    char_count=char_count,
                    position=candidate.position,
                    label=candidate.label,
                    priority=candidate.priority,
                    budget_reason=reason,
                    metadata=candidate.metadata,
                ))

        if traces:
            self._fire_and_forget_record(traces)
            self._fire_and_forget_observations(
                observation_decisions,
                request_id=request_id,
                fallback_group_id=group_id or "",
            )
            accepted = sum(1 for t in traces if t.decision == "accepted")
            trimmed = sum(1 for t in traces if t.decision == "trimmed")
            rejected = sum(1 for t in traces if t.decision == "rejected")
            _L.debug(
                "budget | req={} blocks={} accepted={} trimmed={} rejected={}",
                request_id[:24], len(traces), accepted, trimmed, rejected,
            )
            publish_block_trace_recorded(
                request_id=request_id,
                count=len(traces),
                accepted=accepted,
                trimmed=trimmed,
                rejected=rejected,
            )

        return surviving, accepted_decisions

    def _fire_and_forget_record(self, traces: list[PromptBlockTrace]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._safe_record(traces))
        task.add_done_callback(lambda t: t.result() if not t.cancelled() and not t.exception() else None)

    async def _safe_record(self, traces: list[PromptBlockTrace]) -> None:
        try:
            await self._store.record_batch(traces)
        except Exception:
            _L.debug("trace record_batch failed | count={}", len(traces))

    def _fire_and_forget_observations(
        self,
        decisions: list[AcceptedDecision],
        *,
        request_id: str,
        fallback_group_id: str = "",
    ) -> None:
        if not decisions:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._safe_record_observations(
            decisions,
            request_id=request_id,
            fallback_group_id=fallback_group_id,
        ))
        task.add_done_callback(lambda t: t.result() if not t.cancelled() and not t.exception() else None)

    async def _safe_record_observations(
        self,
        decisions: list[AcceptedDecision],
        *,
        request_id: str,
        fallback_group_id: str = "",
    ) -> None:
        for decision in decisions:
            if not decision.evidence_refs:
                continue
            if decision.source == "slang":
                store = self._slang_store_getter() if self._slang_store_getter else None
                if store is None or not hasattr(store, "record_observation"):
                    continue
                await self._record_slang_observations(
                    store,
                    decision,
                    request_id=request_id,
                    fallback_group_id=fallback_group_id,
                )
            elif decision.source == "style":
                store = self._style_store_getter() if self._style_store_getter else None
                if store is None or not hasattr(store, "record_observation"):
                    continue
                trigger_type = (
                    "profile_inject"
                    if decision.hit_reason == "style_profile_injection"
                    else "expression_inject"
                )
                await self._record_style_observations(
                    store,
                    decision,
                    request_id=request_id,
                    fallback_group_id=fallback_group_id,
                    trigger_type=_observation_trigger_type(trigger_type, decision),
                )
            elif decision.source == "episode":
                store = self._episode_store_getter() if self._episode_store_getter else None
                if store is None or not hasattr(store, "record_observation"):
                    continue
                await self._record_episode_observations(
                    store,
                    decision,
                    request_id=request_id,
                    fallback_group_id=fallback_group_id,
                )

    async def _record_slang_observations(
        self,
        store: Any,
        decision: AcceptedDecision,
        *,
        request_id: str,
        fallback_group_id: str,
    ) -> None:
        for term_id in dict.fromkeys(decision.evidence_refs):
            try:
                reason = (
                    "prompt_inject_trimmed"
                    if decision.decision == "trimmed"
                    else "prompt_inject"
                )
                await store.record_observation(
                    term_id,
                    group_id=decision.group_id or fallback_group_id,
                    raw_text="",
                    context="",
                    reason=f"{reason}:{request_id}",
                )
            except Exception as exc:
                _L.warning(
                    "slang observation record failed | candidate={} ref={} err={}",
                    decision.candidate_id,
                    term_id,
                    exc,
                )

    async def _record_style_observations(
        self,
        store: Any,
        decision: AcceptedDecision,
        *,
        request_id: str,
        fallback_group_id: str,
        trigger_type: str,
    ) -> None:
        for expression_id in decision.evidence_refs:
            try:
                await store.record_observation(
                    expression_id,
                    message_id=request_id,
                    trigger_type=trigger_type,
                    group_id=decision.group_id or fallback_group_id,
                    scope=decision.scope or "group",
                    meta=_observation_meta(decision, request_id=request_id),
                )
            except Exception as exc:
                _L.warning(
                    "style observation record failed | candidate={} ref={} err={}",
                    decision.candidate_id,
                    expression_id,
                    exc,
                )

    async def _record_episode_observations(
        self,
        store: Any,
        decision: AcceptedDecision,
        *,
        request_id: str,
        fallback_group_id: str,
    ) -> None:
        for episode_id in decision.evidence_refs:
            try:
                await store.record_observation(
                    episode_id,
                    message_id=request_id,
                    trigger_type=_observation_trigger_type("episode_inject", decision),
                    group_id=decision.group_id or fallback_group_id,
                    scope=decision.scope or "group",
                    meta=_observation_meta(decision, request_id=request_id),
                )
            except Exception as exc:
                _L.warning(
                    "episode observation record failed | candidate={} ref={} err={}",
                    decision.candidate_id,
                    episode_id,
                    exc,
                )


def _candidate_to_prompt_block(
    candidate: PromptBlockCandidate,
    *,
    text: str | None = None,
    position: str,
) -> PromptBlock:
    return PromptBlock(
        text=candidate.text if text is None else text,
        label=candidate.label,
        position=position,  # type: ignore[arg-type]
        priority=candidate.priority,
        source=candidate.source,
        provider=candidate.provider,
    )


def _accepted_decision(
    candidate: PromptBlockCandidate,
    *,
    char_count: int,
    decision: BudgetDecision = "accepted",
) -> AcceptedDecision:
    return AcceptedDecision(
        candidate_id=candidate.candidate_id,
        source=candidate.source,
        provider=candidate.provider,
        evidence_refs=candidate.evidence_refs,
        metadata=dict(candidate.metadata),
        char_count=char_count,
        group_id=candidate.group_id,
        scope=candidate.scope,
        hit_reason=candidate.hit_reason,
        label=candidate.label,
        decision=decision,
    )


def _observation_trigger_type(trigger_type: str, decision: AcceptedDecision) -> str:
    if decision.decision == "trimmed":
        return f"{trigger_type}_trimmed"
    return trigger_type


def _observation_meta(decision: AcceptedDecision, *, request_id: str) -> dict[str, Any]:
    return {
        "candidate_id": decision.candidate_id,
        "provider": decision.provider,
        "request_id": request_id,
        "label": decision.label,
        "hit_reason": decision.hit_reason,
        "budget_decision": decision.decision,
        "metadata": dict(decision.metadata),
    }
