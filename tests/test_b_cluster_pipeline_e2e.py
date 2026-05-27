from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from kernel.bot_pair_guard import BotPairLoopGuard
from kernel.config import GroupConfig
from kernel.router import _maybe_drop_pair_guard, _notify_group_scheduler
from kernel.types import PluginContext
from services.block_trace.store import BlockTraceStore
from services.coalesce import MessageCoalescer
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot
from services.scheduler import GroupChatScheduler


class _Runtime:
    def identity_snapshot(self) -> IdentitySnapshot:
        return IdentitySnapshot(id="bot", name="bot", personality="p", proactive="on")


class _LLM:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, **kwargs: Any) -> str | None:  # type: ignore[override]
        del kwargs
        self.calls += 1
        return None


def _ctx(
    *,
    scheduler: GroupChatScheduler,
    guard: BotPairLoopGuard,
    coalescer: MessageCoalescer,
    store: BlockTraceStore,
) -> PluginContext:
    ctx = SimpleNamespace(
        config=SimpleNamespace(
            bot_pair_guard=SimpleNamespace(enabled=True),
            coalesce=SimpleNamespace(enabled=True),
        ),
        scheduler=scheduler,
        bot_pair_guard=guard,
        message_coalescer=coalescer,
        block_trace_store=store,
    )
    return cast(PluginContext, ctx)


async def _wait_until(predicate, *, timeout: float = 0.5) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=timeout)


@pytest.mark.asyncio
async def test_b_cluster_pipeline_records_ingress_and_outbound_metrics(tmp_path) -> None:
    store = BlockTraceStore(db_path=tmp_path / "trace.db")
    await store.init()
    llm = _LLM()
    scheduler = GroupChatScheduler(
        llm=cast(Any, llm),
        timeline=GroupTimeline(),
        persona_runtime=_Runtime(),  # type: ignore[arg-type]
        group_config=GroupConfig(talk_value=1.0, planner_smooth=0.0),
        bot_pair_guard=BotPairLoopGuard(
            self_id="1",
            known_other_bots={"100": ["2"]},
            max_per_minute=3,
            cooldown_seconds=60,
        ),
        block_trace_store=store,
    )
    scheduler.set_bot(cast(Any, SimpleNamespace(self_id="1", send_group_msg=AsyncMock())))
    guard = cast(BotPairLoopGuard, scheduler._bot_pair_guard)
    coalescer = MessageCoalescer(idle_window_seconds=0.01, max_window_seconds=0.05)
    ctx = _ctx(scheduler=scheduler, guard=guard, coalescer=coalescer, store=store)

    try:
        for idx in range(4):
            dropped = await _maybe_drop_pair_guard(ctx, group_id="100", sender_id="2")
            assert dropped is False
            if idx < 3:
                assert guard.is_suppressed("100", "2") is False
        assert await _maybe_drop_pair_guard(ctx, group_id="100", sender_id="2") is True

        await _notify_group_scheduler(
            ctx,
            group_id="100",
            user_id="200",
            trigger=None,
            is_addressed=False,
            message="hello",
        )
        await _notify_group_scheduler(
            ctx,
            group_id="100",
            user_id="200",
            trigger=None,
            is_addressed=False,
            message="world",
        )
        await _wait_until(lambda: llm.calls == 1)

        await scheduler._send_to_group("100", "outbound", target_user_id="2")
        stats = await store.stats()

        assert stats["pair_guard_inbound_recorded"] == 4
        assert stats["pair_guard_suppressed"] == 1
        assert stats["coalesce_enqueued"] == 2
        assert stats["coalesce_flushed"] == 1
        assert stats["pair_guard_outbound_recorded"] == 1
    finally:
        await scheduler.close()
        await coalescer.close()
        await store.close()
