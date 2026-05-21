"""PromptBudgetManager — prioritize, trim, and trace prompt blocks."""

from __future__ import annotations

import asyncio
import secrets

from loguru import logger

from kernel.types import PromptBlock
from services.admin_events import publish_block_trace_recorded
from services.block_trace.store import BlockTraceStore
from services.block_trace.types import BudgetDecision, PromptBlockTrace

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
    ) -> None:
        self._store = trace_store
        self._budgets = {
            "static": max_static_chars,
            "stable": max_stable_chars,
            "dynamic": max_dynamic_chars,
        }

    def process(
        self,
        blocks: list[PromptBlock],
        *,
        request_id: str,
        task: str = "main",
        session_id: str = "",
        group_id: str | None = None,
    ) -> list[PromptBlock]:
        """Prioritize, trim, trace. Returns surviving blocks in priority order."""
        buckets: dict[str, list[PromptBlock]] = {
            "static": [],
            "stable": [],
            "dynamic": [],
        }
        for b in blocks:
            key = b.position if b.position in buckets else "dynamic"
            buckets[key].append(b)

        for key in buckets:
            buckets[key].sort(key=lambda b: b.priority)

        surviving: list[PromptBlock] = []
        traces: list[PromptBlockTrace] = []

        for position in ("static", "stable", "dynamic"):
            budget = self._budgets[position]
            used = 0
            for b in buckets[position]:
                cid = "pbc_" + secrets.token_hex(6)
                char_count = len(b.text)
                remaining = budget - used

                if remaining >= char_count:
                    decision: BudgetDecision = "accepted"
                    reason = f"accepted: {char_count} chars within budget ({remaining} remaining)"
                    surviving.append(b)
                    used += char_count
                elif remaining > 0:
                    decision = "trimmed"
                    trimmed = PromptBlock(
                        text=b.text[:remaining],
                        label=b.label,
                        position=b.position,
                        priority=b.priority,
                        source=b.source,
                        provider=b.provider,
                    )
                    reason = f"trimmed: {char_count} -> {remaining} chars (budget exhausted)"
                    surviving.append(trimmed)
                    used += remaining
                else:
                    decision = "rejected"
                    reason = f"rejected: no budget left (need {char_count}, have 0)"

                traces.append(PromptBlockTrace(
                    trace_id="bt_" + secrets.token_hex(6),
                    request_id=request_id,
                    task=task,
                    source=b.source or "unknown",
                    provider=b.provider or (b.source + "_plugin" if b.source else "unknown"),
                    candidate_id=cid,
                    decision=decision,
                    hit_reason=b.label,
                    evidence_refs=(),
                    token_estimate=char_count // 3,
                    char_count=char_count,
                    position=b.position,
                    label=b.label,
                    priority=b.priority,
                    budget_reason=reason,
                ))

        if traces:
            self._fire_and_forget_record(traces)
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

        return surviving

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
