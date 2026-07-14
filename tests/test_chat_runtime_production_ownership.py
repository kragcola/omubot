"""Production ownership contracts for the chat runtime factory."""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace

import pytest

from bootstrap.chat_runtime import (
    ChatRuntimeAssembly,
    build_chat_runtime,
    create_chat_runtime_assembly,
)


class _BlockingCloseResource:
    def __init__(self) -> None:
        self.close_calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.completed = asyncio.Event()

    async def close(self) -> None:
        self.close_calls += 1
        self.entered.set()
        await self.release.wait()
        self.completed.set()


class _CloseProbe:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def test_production_builder_publishes_the_scheduler_talk_schedule_to_context() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(build_chat_runtime)))
    publish_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "assembly"
        and node.func.attr == "publish"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "talk_schedule"
        and isinstance(node.args[1], ast.Name)
        and node.args[1].id == "talk_schedule"
    ]
    scheduler_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "GroupChatScheduler"
    ]

    assert len(publish_calls) == 1
    assert len(scheduler_calls) == 1
    talk_schedule_keywords = [
        keyword.value
        for keyword in scheduler_calls[0].keywords
        if keyword.arg == "talk_schedule"
    ]
    assert len(talk_schedule_keywords) == 1
    assert isinstance(talk_schedule_keywords[0], ast.Name)
    assert talk_schedule_keywords[0].id == "talk_schedule"
    assert publish_calls[0].lineno < scheduler_calls[0].lineno


@pytest.mark.asyncio
async def test_production_close_cancellation_propagates_while_shared_cleanup_continues() -> None:
    ctx = SimpleNamespace()
    resource = _BlockingCloseResource()

    async def builder(assembly: ChatRuntimeAssembly) -> None:
        assembly.publish("message_coalescer", resource)

    assembly = create_chat_runtime_assembly(ctx, builder)
    await assembly.start()

    first_close = asyncio.create_task(assembly.close())
    second_close: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(resource.entered.wait(), timeout=1.0)

        first_close.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_close

        assert resource.close_calls == 1
        assert not resource.completed.is_set()

        second_close = asyncio.create_task(assembly.close())
        await asyncio.sleep(0)
        assert not second_close.done(), "a second close must await the in-flight cleanup"

        resource.release.set()
        await second_close

        assert resource.completed.is_set()
        assert resource.close_calls == 1
    finally:
        resource.release.set()
        for task in (first_close, second_close):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_production_factory_does_not_close_borrowed_scheduler_from_context() -> None:
    borrowed_scheduler = _CloseProbe()
    ctx = SimpleNamespace(scheduler=borrowed_scheduler)

    async def builder(_assembly: ChatRuntimeAssembly) -> None:
        return None

    assembly = create_chat_runtime_assembly(ctx, builder)
    await assembly.start()
    await assembly.close()

    assert borrowed_scheduler.close_calls == 0


@pytest.mark.asyncio
async def test_builder_published_owned_llm_is_closed_once_after_later_start_failure() -> None:
    ctx = SimpleNamespace()
    llm_client = _CloseProbe()
    startup_error = RuntimeError("failure after llm creation")

    async def builder(assembly: ChatRuntimeAssembly) -> None:
        assembly.publish("llm_client", llm_client)
        assembly.own("builder_llm_client", llm_client.close)
        raise startup_error

    assembly = create_chat_runtime_assembly(ctx, builder)

    with pytest.raises(RuntimeError) as raised:
        await assembly.start()

    assert raised.value is startup_error
    assert llm_client.close_calls == 1
    assert not hasattr(ctx, "llm_client")
