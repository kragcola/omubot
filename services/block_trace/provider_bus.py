"""PromptProviderBus — registry and runner for ContextProviders."""

from __future__ import annotations

import asyncio
import secrets

from loguru import logger

from kernel.types import PromptBlock
from services.admin_events import publish_block_trace_recorded
from services.block_trace.providers import ContextProvider, QueryContext
from services.block_trace.store import BlockTraceStore
from services.block_trace.types import PromptBlockCandidate, PromptBlockTrace

_L = logger.bind(channel="provider_bus")


class PromptProviderBus:
    """Registry of ContextProviders with shadow and active run modes.

    - shadow: providers run and traces are recorded as shadow_only (no prompt impact)
    - active: providers produce PromptBlocks injected into the prompt
    - off: providers are not called
    """

    def __init__(self, trace_store: BlockTraceStore) -> None:
        self._providers: list[ContextProvider] = []
        self._store = trace_store
        self.mode: str = "shadow"

    def register(self, provider: ContextProvider) -> None:
        self._providers.append(provider)
        _L.debug("provider registered | name={}", provider.name)

    def has_provider(self, name: str) -> bool:
        return any(p.name == name for p in self._providers)

    async def run_all(self, ctx: QueryContext) -> list[PromptBlockCandidate]:
        if not self._providers:
            return []
        results = await asyncio.gather(
            *(p.provide(ctx) for p in self._providers),
            return_exceptions=True,
        )
        candidates: list[PromptBlockCandidate] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                _L.warning(
                    "provider error | name={} err={}",
                    self._providers[i].name, result,
                )
                continue
            candidates.extend(result)
        return candidates

    async def run_shadow(self, ctx: QueryContext) -> None:
        candidates = await self.run_all(ctx)
        if not candidates:
            return
        traces = [
            PromptBlockTrace(
                trace_id="bt_" + secrets.token_hex(6),
                request_id=ctx.request_id,
                task="main",
                source=c.source,
                provider=c.provider,
                candidate_id=c.candidate_id,
                decision="shadow_only",
                hit_reason=c.hit_reason,
                evidence_refs=c.evidence_refs,
                token_estimate=c.char_count // 3,
                char_count=c.char_count,
                position=c.position,
                label=c.label,
                priority=c.priority,
                budget_reason="shadow: provider dual-run, not injected",
            )
            for c in candidates
        ]
        try:
            await self._store.record_batch(traces)
        except Exception:
            _L.debug("shadow trace record failed | count={}", len(traces))
        publish_block_trace_recorded(
            request_id=ctx.request_id,
            count=len(traces),
            shadow_only=len(traces),
        )
        _L.debug(
            "shadow run | req={} candidates={}",
            ctx.request_id[:24], len(candidates),
        )

    async def run_active(self, ctx: QueryContext) -> list[PromptBlock]:
        candidates = await self.run_all(ctx)
        return [
            PromptBlock(
                text=c.text,
                label=c.label,
                position=c.position,
                priority=c.priority,
                source=c.source,
                provider=c.provider,
            )
            for c in candidates
        ]
