from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from kernel.router import (
    _maybe_drop_pair_guard,
    _notify_group_scheduler,
    _should_bypass_coalescer,
)
from kernel.types import PluginContext, TriggerContext


class _MetricStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record_runtime_metric(
        self,
        *,
        metric_key: str,
        group_id: str = "",
        amount: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.rows.append({
            "metric_key": metric_key,
            "group_id": group_id,
            "amount": amount,
            "metadata": metadata or {},
        })


class _Guard:
    def __init__(self, *, suppressed: bool = False, inbound_ok: bool = True) -> None:
        self.suppressed = suppressed
        self.inbound_ok = inbound_ok
        self.calls: list[tuple[str, str, str]] = []

    def is_suppressed(self, group_id: str, sender_id: str) -> bool:
        self.calls.append(("suppressed", group_id, sender_id))
        return self.suppressed

    def record_inbound(self, group_id: str, sender_id: str) -> bool:
        self.calls.append(("inbound", group_id, sender_id))
        return self.inbound_ok


class _Scheduler:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def notify(self, group_id: str, *, trigger: object | None = None, user_id: str = "") -> None:
        self.calls.append({
            "group_id": group_id,
            "trigger": trigger,
            "user_id": user_id,
        })


class _Coalescer:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []
        self.discarded: list[tuple[str, str]] = []

    async def enqueue(
        self,
        group_id: str,
        sender_id: str,
        message: Any,
        *,
        on_flush: Any = None,
    ) -> None:
        self.enqueued.append({
            "group_id": group_id,
            "sender_id": sender_id,
            "message": message,
            "on_flush": on_flush,
        })

    async def discard(self, group_id: str, sender_id: str) -> list[Any]:
        self.discarded.append((group_id, sender_id))
        return ["old-1", "old-2"]


def _ctx(
    *,
    pair_guard_enabled: bool = True,
    coalesce_enabled: bool = True,
    guard: object | None = None,
    scheduler: object | None = None,
    coalescer: object | None = None,
    store: object | None = None,
) -> PluginContext:
    ctx = SimpleNamespace(
        config=SimpleNamespace(
            bot_pair_guard=SimpleNamespace(enabled=pair_guard_enabled),
            coalesce=SimpleNamespace(enabled=coalesce_enabled),
        ),
        bot_pair_guard=guard,
        scheduler=scheduler,
        message_coalescer=coalescer,
        block_trace_store=store,
    )
    return cast(PluginContext, ctx)


@pytest.mark.asyncio
async def test_pair_guard_suppressed_path_records_metric_and_drops() -> None:
    store = _MetricStore()
    guard = _Guard(suppressed=True)

    dropped = await _maybe_drop_pair_guard(
        _ctx(guard=guard, store=store),
        group_id="100",
        sender_id="200",
    )

    assert dropped is True
    assert guard.calls == [("suppressed", "100", "200")]
    assert store.rows == [{
        "metric_key": "pair_guard_suppressed",
        "group_id": "100",
        "amount": 1,
        "metadata": {"sender_id": "200"},
    }]


@pytest.mark.asyncio
async def test_pair_guard_inbound_records_metric_when_not_suppressed() -> None:
    store = _MetricStore()
    guard = _Guard(suppressed=False, inbound_ok=True)

    dropped = await _maybe_drop_pair_guard(
        _ctx(guard=guard, store=store),
        group_id="100",
        sender_id="200",
    )

    assert dropped is False
    assert guard.calls == [
        ("suppressed", "100", "200"),
        ("inbound", "100", "200"),
    ]
    assert store.rows[-1]["metric_key"] == "pair_guard_inbound_recorded"


def test_should_bypass_coalescer_for_addressed_or_triggered_messages() -> None:
    assert _should_bypass_coalescer(trigger=None, is_addressed=True) is True
    assert _should_bypass_coalescer(
        trigger=TriggerContext(reason="at", mode="at_mention"),
        is_addressed=False,
    ) is True
    assert _should_bypass_coalescer(trigger=None, is_addressed=False) is False


@pytest.mark.asyncio
async def test_notify_group_scheduler_bypass_discards_bucket_and_records_metric() -> None:
    store = _MetricStore()
    scheduler = _Scheduler()
    coalescer = _Coalescer()
    trigger = TriggerContext(reason="at", mode="at_mention")

    await _notify_group_scheduler(
        _ctx(scheduler=scheduler, coalescer=coalescer, store=store),
        group_id="100",
        user_id="200",
        trigger=trigger,
        is_addressed=True,
        message="hello",
    )

    assert coalescer.discarded == [("100", "200")]
    assert scheduler.calls == [{
        "group_id": "100",
        "trigger": trigger,
        "user_id": "200",
    }]
    assert store.rows[-1]["metric_key"] == "coalesce_bypassed"
    assert store.rows[-1]["metadata"]["discarded_messages"] == 2


@pytest.mark.asyncio
async def test_notify_group_scheduler_enqueue_then_flush_records_metrics() -> None:
    store = _MetricStore()
    scheduler = _Scheduler()
    coalescer = _Coalescer()

    await _notify_group_scheduler(
        _ctx(scheduler=scheduler, coalescer=coalescer, store=store),
        group_id="100",
        user_id="200",
        trigger=None,
        is_addressed=False,
        message="hello",
    )

    assert len(coalescer.enqueued) == 1
    assert store.rows[-1]["metric_key"] == "coalesce_enqueued"
    await coalescer.enqueued[0]["on_flush"](["hello", "world"])

    assert scheduler.calls == [{
        "group_id": "100",
        "trigger": None,
        "user_id": "200",
    }]
    assert store.rows[-1]["metric_key"] == "coalesce_flushed"
    assert store.rows[-1]["metadata"]["message_count"] == 2


@pytest.mark.asyncio
async def test_notify_group_scheduler_without_coalescer_falls_back_directly() -> None:
    scheduler = _Scheduler()

    await _notify_group_scheduler(
        _ctx(coalesce_enabled=False, scheduler=scheduler, coalescer=None, store=None),
        group_id="100",
        user_id="200",
        trigger=None,
        is_addressed=False,
        message="hello",
    )

    assert scheduler.calls == [{
        "group_id": "100",
        "trigger": None,
        "user_id": "200",
    }]
