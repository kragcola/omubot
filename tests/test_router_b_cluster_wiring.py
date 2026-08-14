from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from kernel.router import (
    _maybe_drop_pair_guard,
    _notify_group_scheduler,
    _record_authoritative_runtime_invocation,
    _should_bypass_coalescer,
)
from kernel.types import PluginContext, TriggerContext
from services.agent_runtime.invocation_store import TrustedInvocationRecordV1
from services.coalesce import MessageCoalescer


def _valid_invocation_id(value: int) -> str:
    return f"inv_{value:032x}"


def _receipt(
    *,
    invocation_id: str,
    group_id: str | None,
    user_id: str,
    message_id: str,
) -> TrustedInvocationRecordV1:
    clean_group = str(group_id or "")
    trigger_ref = (
        f"onebot:group:{clean_group}:message:{message_id}"
        if clean_group
        else f"onebot:user:{user_id}:message:{message_id}"
    )
    return TrustedInvocationRecordV1(
        invocation_id=invocation_id,
        trigger_type="message",
        trigger_ref=trigger_ref,
        principal_kind="onebot_user",
        principal_id=user_id,
        session_id=f"group_{clean_group}" if clean_group else f"private_{user_id}",
        group_id=clean_group,
        registry_generation=0,
        granted_scopes=(),
        allowed_target_refs=(),
        onebot_message_refs=(trigger_ref,),
        created_at="2026-08-14T00:00:00+00:00",
    )


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


class _BlockingEnqueueMetricStore(_MetricStore):
    def __init__(self) -> None:
        super().__init__()
        self.enqueued_started = asyncio.Event()

    async def record_runtime_metric(
        self,
        *,
        metric_key: str,
        group_id: str = "",
        amount: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await super().record_runtime_metric(
            metric_key=metric_key,
            group_id=group_id,
            amount=amount,
            metadata=metadata,
        )
        if metric_key == "coalesce_enqueued":
            self.enqueued_started.set()
            await asyncio.Event().wait()


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

    def notify(
        self,
        group_id: str,
        *,
        trigger: object | None = None,
        user_id: str = "",
        message_text: str = "",
        message_id: int | None = None,
        reply_to_sender_id: str = "",
        reply_to_message_id: int | None = None,
        reply_to_self: bool = False,
        at_targets: tuple[str, ...] = (),
        at_self: bool = False,
        is_addressed: bool = False,
        runtime_invocation_id: str | None = None,
    ) -> None:
        call: dict[str, Any] = {
            "group_id": group_id,
            "trigger": trigger,
            "user_id": user_id,
            "message_text": message_text,
        }
        if runtime_invocation_id is not None:
            call["runtime_invocation_id"] = runtime_invocation_id
        self.calls.append(call)


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


class _RuntimeIngress:
    def __init__(
        self,
        *,
        invocation_id: str = _valid_invocation_id(1),
        failure: bool = False,
        receipt_message_id: str | None = None,
    ) -> None:
        self.invocation_id = invocation_id
        self.failure = failure
        self.receipt_message_id = receipt_message_id
        self.calls: list[dict[str, Any]] = []

    async def record_onebot_message(
        self,
        *,
        group_id: str | None,
        user_id: str,
        message_id: str,
    ) -> TrustedInvocationRecordV1:
        self.calls.append({
            "group_id": group_id,
            "user_id": user_id,
            "message_id": message_id,
        })
        if self.failure:
            raise RuntimeError("persistence unavailable")
        return _receipt(
            invocation_id=self.invocation_id,
            group_id=group_id,
            user_id=user_id,
            message_id=self.receipt_message_id or message_id,
        )


class _PerMessageRuntimeIngress(_RuntimeIngress):
    async def record_onebot_message(
        self,
        *,
        group_id: str | None,
        user_id: str,
        message_id: str,
    ) -> TrustedInvocationRecordV1:
        await super().record_onebot_message(
            group_id=group_id,
            user_id=user_id,
            message_id=message_id,
        )
        return _receipt(
            invocation_id=_valid_invocation_id(int(message_id)),
            group_id=group_id,
            user_id=user_id,
            message_id=message_id,
        )


class _BlockingRuntimeIngress:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def record_onebot_message(
        self,
        *,
        group_id: str | None,
        user_id: str,
        message_id: str,
    ) -> TrustedInvocationRecordV1:
        del group_id, user_id, message_id
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def _ctx(
    *,
    pair_guard_enabled: bool = True,
    coalesce_enabled: bool = True,
    guard: object | None = None,
    scheduler: object | None = None,
    coalescer: object | None = None,
    store: object | None = None,
    runtime_ingress: object | None = None,
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
        agent_runtime_host_ingress=runtime_ingress,
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
        "message_text": "hello",
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
        "message_text": "hello world",
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
        "message_text": "hello",
    }]


@pytest.mark.asyncio
async def test_configured_host_ingress_persists_then_forwards_exact_id_to_group_scheduler() -> None:
    scheduler = _Scheduler()
    ingress = _RuntimeIngress(invocation_id=_valid_invocation_id(789))

    await _notify_group_scheduler(
        _ctx(
            coalesce_enabled=False,
            scheduler=scheduler,
            runtime_ingress=ingress,
        ),
        group_id="100",
        user_id="200",
        trigger=None,
        is_addressed=False,
        message="hello",
        event=SimpleNamespace(message_id=300),
    )

    assert ingress.calls == [{
        "group_id": "100",
        "user_id": "200",
        "message_id": "300",
    }]
    assert scheduler.calls == [{
        "group_id": "100",
        "trigger": None,
        "user_id": "200",
        "message_text": "hello",
        "runtime_invocation_id": _valid_invocation_id(789),
    }]


@pytest.mark.asyncio
async def test_configured_host_ingress_failure_short_circuits_group_scheduler() -> None:
    scheduler = _Scheduler()
    ingress = _RuntimeIngress(failure=True)

    await _notify_group_scheduler(
        _ctx(
            coalesce_enabled=False,
            scheduler=scheduler,
            runtime_ingress=ingress,
        ),
        group_id="100",
        user_id="200",
        trigger=None,
        is_addressed=False,
        message="legacy llm must not run",
        event=SimpleNamespace(message_id=300),
    )

    assert len(ingress.calls) == 1
    assert scheduler.calls == []


@pytest.mark.asyncio
async def test_configured_host_ingress_coalesced_flush_carries_latest_trigger_id() -> None:
    scheduler = _Scheduler()
    coalescer = _Coalescer()
    ingress = _PerMessageRuntimeIngress()
    ctx = _ctx(scheduler=scheduler, coalescer=coalescer, runtime_ingress=ingress)

    await _notify_group_scheduler(
        ctx,
        group_id="100",
        user_id="200",
        trigger=None,
        is_addressed=False,
        message="first",
        event=SimpleNamespace(message_id=300),
    )
    await _notify_group_scheduler(
        ctx,
        group_id="100",
        user_id="200",
        trigger=None,
        is_addressed=False,
        message="latest",
        event=SimpleNamespace(message_id=301),
    )
    await coalescer.enqueued[-1]["on_flush"](["first", "latest"])

    assert scheduler.calls[-1]["message_text"] == "first latest"
    assert scheduler.calls[-1]["runtime_invocation_id"] == _valid_invocation_id(301)


@pytest.mark.asyncio
async def test_private_host_ingress_uses_private_onebot_identity_and_fails_closed() -> None:
    ingress = _RuntimeIngress(invocation_id=_valid_invocation_id(456))
    configured, invocation_id = await _record_authoritative_runtime_invocation(
        _ctx(runtime_ingress=ingress),
        group_id=None,
        user_id="200",
        message_id=300,
    )

    assert configured is True
    assert invocation_id == _valid_invocation_id(456)
    assert ingress.calls == [{
        "group_id": None,
        "user_id": "200",
        "message_id": "300",
    }]

    failing = _RuntimeIngress(failure=True)
    configured, invocation_id = await _record_authoritative_runtime_invocation(
        _ctx(runtime_ingress=failing),
        group_id=None,
        user_id="200",
        message_id=300,
    )
    assert configured is True
    assert invocation_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("group_id", ["100", None])
async def test_configured_host_ingress_rejects_missing_onebot_message_identity(
    group_id: str | None,
) -> None:
    ingress = _RuntimeIngress(invocation_id=_valid_invocation_id(457))

    configured, invocation_id = await _record_authoritative_runtime_invocation(
        _ctx(runtime_ingress=ingress),
        group_id=group_id,
        user_id="200",
        message_id=None,
    )

    assert configured is True
    assert invocation_id is None
    assert ingress.calls == []


@pytest.mark.asyncio
async def test_group_host_ingress_cancellation_never_reaches_scheduler() -> None:
    scheduler = _Scheduler()
    ingress = _BlockingRuntimeIngress()
    task = asyncio.create_task(
        _notify_group_scheduler(
            _ctx(
                coalesce_enabled=False,
                scheduler=scheduler,
                runtime_ingress=ingress,
            ),
            group_id="100",
            user_id="200",
            trigger=None,
            is_addressed=False,
            message="cancelled before legacy llm",
            event=SimpleNamespace(message_id=300),
        )
    )
    await ingress.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert scheduler.calls == []


@pytest.mark.asyncio
async def test_group_coalescer_cancellation_discards_pending_authoritative_invocation() -> None:
    scheduler = _Scheduler()
    store = _BlockingEnqueueMetricStore()
    coalescer = MessageCoalescer(idle_window_seconds=0.05, max_window_seconds=0.1)
    task = asyncio.create_task(
        _notify_group_scheduler(
            _ctx(
                scheduler=scheduler,
                coalescer=coalescer,
                store=store,
                runtime_ingress=_RuntimeIngress(invocation_id=_valid_invocation_id(123)),
            ),
            group_id="100",
            user_id="200",
            trigger=None,
            is_addressed=False,
            message="cancelled after coalescing",
            event=SimpleNamespace(message_id=300),
        )
    )
    await asyncio.wait_for(store.enqueued_started.wait(), timeout=1.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.12)

    assert scheduler.calls == []
    assert coalescer._buckets == {}


@pytest.mark.asyncio
async def test_configured_host_ingress_rejects_noncanonical_invocation_id() -> None:
    scheduler = _Scheduler()
    ingress = _RuntimeIngress(invocation_id="arbitrary-id")

    await _notify_group_scheduler(
        _ctx(
            coalesce_enabled=False,
            scheduler=scheduler,
            runtime_ingress=ingress,
        ),
        group_id="100",
        user_id="200",
        trigger=None,
        is_addressed=False,
        message="must not receive a fabricated ID",
        event=SimpleNamespace(message_id=300),
    )

    assert len(ingress.calls) == 1
    assert scheduler.calls == []


@pytest.mark.asyncio
async def test_configured_host_ingress_rejects_record_bound_to_another_message() -> None:
    scheduler = _Scheduler()
    ingress = _RuntimeIngress(
        invocation_id=_valid_invocation_id(300),
        receipt_message_id="299",
    )

    await _notify_group_scheduler(
        _ctx(
            coalesce_enabled=False,
            scheduler=scheduler,
            runtime_ingress=ingress,
        ),
        group_id="100",
        user_id="200",
        trigger=None,
        is_addressed=False,
        message="must not receive a stale record",
        event=SimpleNamespace(message_id=300),
    )

    assert len(ingress.calls) == 1
    assert scheduler.calls == []
