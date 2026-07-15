"""Behavior contracts for transactional chat runtime assembly."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bootstrap.chat_runtime import ChatRuntimeAssembly, create_chat_runtime_assembly


def _assert_single_shutdown_error(actual: BaseException, expected: BaseException) -> None:
    if actual is expected:
        return
    assert isinstance(actual, BaseExceptionGroup)
    assert actual.exceptions == (expected,)


@pytest.mark.asyncio
async def test_start_failure_closes_owned_resources_in_reverse_and_restores_existing_fields() -> None:
    original_context_service = object()
    ctx = SimpleNamespace(context_service=original_context_service)
    calls: list[str] = []
    startup_error = RuntimeError("chat assembly failed")

    async def builder(assembly: ChatRuntimeAssembly) -> None:
        assembly.publish("context_service", object())

        async def close_first() -> None:
            calls.append("first.close")

        async def close_second() -> None:
            calls.append("second.close")

        assembly.own("first", close_first)
        assembly.own("second", close_second)
        raise startup_error

    assembly = ChatRuntimeAssembly(ctx, builder)

    with pytest.raises(RuntimeError) as raised:
        await assembly.start()

    assert raised.value is startup_error
    assert calls == ["second.close", "first.close"]
    assert ctx.context_service is original_context_service


@pytest.mark.asyncio
async def test_start_failure_removes_fields_that_did_not_previously_exist() -> None:
    ctx = SimpleNamespace()
    startup_error = RuntimeError("publish failed")

    async def builder(assembly: ChatRuntimeAssembly) -> None:
        assembly.publish("new_runtime_service", object())
        raise startup_error

    assembly = ChatRuntimeAssembly(ctx, builder)

    with pytest.raises(RuntimeError) as raised:
        await assembly.start()

    assert raised.value is startup_error
    assert not hasattr(ctx, "new_runtime_service")


@pytest.mark.asyncio
async def test_start_cancellation_propagates_after_cleanup_without_context_leaks() -> None:
    ctx = SimpleNamespace()
    builder_ready = asyncio.Event()
    cleanup_done = asyncio.Event()
    never_complete = asyncio.Event()

    async def builder(assembly: ChatRuntimeAssembly) -> None:
        assembly.publish("temporary_service", object())

        async def close_temporary_service() -> None:
            cleanup_done.set()

        assembly.own("temporary_service", close_temporary_service)
        builder_ready.set()
        await never_complete.wait()

    assembly = ChatRuntimeAssembly(ctx, builder)
    startup_task = asyncio.create_task(assembly.start())
    await asyncio.sleep(0)
    assert builder_ready.is_set(), "start() must enter the supplied builder"

    startup_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup_task

    assert cleanup_done.is_set()
    assert not hasattr(ctx, "temporary_service")


@pytest.mark.asyncio
async def test_close_continues_after_finalizer_failure_and_is_idempotent() -> None:
    ctx = SimpleNamespace()
    calls: list[str] = []
    close_error = RuntimeError("second finalizer failed")

    async def builder(assembly: ChatRuntimeAssembly) -> None:
        async def close_first() -> None:
            calls.append("first.close")

        async def close_second() -> None:
            calls.append("second.close")
            raise close_error

        async def close_third() -> None:
            calls.append("third.close")

        assembly.own("first", close_first)
        assembly.own("second", close_second)
        assembly.own("third", close_third)

    assembly = ChatRuntimeAssembly(ctx, builder)
    await assembly.start()

    with pytest.raises(BaseException) as raised:
        await assembly.close()

    _assert_single_shutdown_error(raised.value, close_error)
    assert calls == ["third.close", "second.close", "first.close"]

    await assembly.close()
    assert calls == ["third.close", "second.close", "first.close"]


@pytest.mark.asyncio
async def test_normal_close_preserves_context_service_replaced_after_chat_startup() -> None:
    original_context_service = object()
    chat_context_service = object()
    context_plugin_service = object()
    ctx = SimpleNamespace(context_service=original_context_service)
    calls: list[str] = []

    async def builder(assembly: ChatRuntimeAssembly) -> None:
        assembly.publish("context_service", chat_context_service)

        async def close_chat_service() -> None:
            calls.append("chat.close")

        assembly.own("chat", close_chat_service)

    assembly = ChatRuntimeAssembly(ctx, builder)
    await assembly.start()
    assert ctx.context_service is chat_context_service

    ctx.context_service = context_plugin_service
    await assembly.close()

    assert calls == ["chat.close"]
    assert ctx.context_service is context_plugin_service


@pytest.mark.asyncio
async def test_production_assembly_closes_producer_before_climate_baseline_store() -> None:
    calls: list[str] = []

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        async def close(self) -> None:
            calls.append(f"{self.name}.close")

    ctx = SimpleNamespace(
        climate_baseline_store=None,
        climate_engine=None,
        message_coalescer=None,
    )

    async def builder(_assembly: ChatRuntimeAssembly) -> None:
        ctx.message_coalescer = Resource("producer")
        ctx.climate_baseline_store = Resource("baseline")

    assembly = create_chat_runtime_assembly(ctx, builder)
    await assembly.start()
    await assembly.close()

    assert calls == ["producer.close", "baseline.close"]
