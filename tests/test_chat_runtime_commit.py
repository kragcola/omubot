"""Commit-boundary contracts for chat and process runtime assembly."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from types import SimpleNamespace
from typing import Any

import pytest

from bootstrap.application import compose_application_runtime
from bootstrap.chat_runtime import ChatRuntimeAssembly

Action = Callable[[], Awaitable[None] | None]


def _register_commit_action(
    assembly: ChatRuntimeAssembly,
    name: str,
    action: Action,
) -> None:
    register = getattr(assembly, "commit_action", None)
    assert callable(register), "ChatRuntimeAssembly.commit_action must be implemented"
    register(name, action)


async def _commit(assembly: ChatRuntimeAssembly) -> None:
    commit = getattr(assembly, "commit", None)
    assert callable(commit), "ChatRuntimeAssembly.commit must be implemented"
    result = commit()
    if isinstance(result, Awaitable):
        await result


@pytest.mark.asyncio
async def test_uncommitted_close_runs_failure_rollback_without_commit_actions() -> None:
    calls: list[str] = []

    async def builder(_assembly: ChatRuntimeAssembly) -> None:
        return None

    async def rollback_action() -> None:
        calls.append("failure.rollback")

    async def commit_action() -> None:
        calls.append("success.commit")

    assembly = ChatRuntimeAssembly(SimpleNamespace(), builder)
    assembly.rollback("failure", rollback_action)
    _register_commit_action(assembly, "success", commit_action)

    await assembly.start()
    await assembly.close()

    assert calls == ["failure.rollback"]


@pytest.mark.asyncio
async def test_commit_runs_actions_in_registration_order_and_clears_failure_rollback() -> None:
    calls: list[str] = []

    async def builder(_assembly: ChatRuntimeAssembly) -> None:
        return None

    async def rollback_action() -> None:
        calls.append("failure.rollback")

    async def first_commit() -> None:
        calls.append("first.commit")

    async def second_commit() -> None:
        calls.append("second.commit")

    assembly = ChatRuntimeAssembly(SimpleNamespace(), builder)
    assembly.rollback("failure", rollback_action)
    _register_commit_action(assembly, "first", first_commit)
    _register_commit_action(assembly, "second", second_commit)

    await assembly.start()
    await _commit(assembly)
    await _commit(assembly)
    await assembly.close()

    assert calls == ["first.commit", "second.commit"]


@pytest.mark.asyncio
async def test_commit_retry_resumes_from_first_incomplete_action_without_replay() -> None:
    calls: list[str] = []
    second_attempts = 0
    commit_error = RuntimeError("second commit action failed")

    async def builder(_assembly: ChatRuntimeAssembly) -> None:
        return None

    async def first_commit() -> None:
        calls.append("first.commit")

    async def second_commit() -> None:
        nonlocal second_attempts
        second_attempts += 1
        calls.append("second.commit")
        if second_attempts == 1:
            raise commit_error

    assembly = ChatRuntimeAssembly(SimpleNamespace(), builder)
    _register_commit_action(assembly, "first", first_commit)
    _register_commit_action(assembly, "second", second_commit)
    await assembly.start()

    with pytest.raises(RuntimeError) as raised:
        await _commit(assembly)

    assert raised.value is commit_error
    assert calls == ["first.commit", "second.commit"]

    await _commit(assembly)
    await _commit(assembly)

    assert calls == ["first.commit", "second.commit", "second.commit"]
    assert second_attempts == 2


class _CommitContext:
    def __init__(self, calls: list[str], commit_error: BaseException) -> None:
        self._calls = calls
        self._commit_error = commit_error

    async def chat_runtime_commit(self) -> None:
        self._calls.append("chat.commit")
        raise self._commit_error


class _Bus:
    def __init__(self, calls: list[str], tools: Iterable[object]) -> None:
        self._calls = calls
        self._tools = list(tools)

    async def fire_on_startup(self, ctx: Any) -> None:
        self._calls.append("bus.start")

    def collect_tools(self) -> list[object]:
        self._calls.append("bus.collect")
        return list(self._tools)

    async def stop_tick_loop(self) -> None:
        self._calls.append("tick.stop")

    async def fire_on_shutdown(self, ctx: Any) -> None:
        self._calls.append("bus.stop")


class _Registry:
    def __init__(self, calls: list[str], tools: Iterable[object]) -> None:
        self._calls = calls
        self.tools = list(tools)

    def snapshot_tools(self) -> tuple[object, ...]:
        return tuple(self.tools)

    def merge_all(self, tools: Iterable[object]) -> None:
        self._calls.append("registry.merge")
        self.tools.extend(tools)

    def replace_all(self, tools: Iterable[object]) -> None:
        self._calls.append("registry.restore")
        self.tools = list(tools)


class _Backup:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def start(self) -> None:
        self._calls.append("backup.start")

    async def stop(self) -> None:
        self._calls.append("backup.stop")


class _Admin:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def install(self, ctx: Any) -> None:
        self._calls.append("admin.install")


@pytest.mark.asyncio
async def test_admin_commit_failure_preserves_error_and_compensates_started_components() -> None:
    calls: list[str] = []
    commit_error = RuntimeError("chat commit failed")
    core_tool = object()
    registry = _Registry(calls, [core_tool])
    runtime = compose_application_runtime(
        ctx=_CommitContext(calls, commit_error),
        bus=_Bus(calls, [object()]),
        registry=registry,
        backup=_Backup(calls),
        admin=_Admin(calls),
    )

    with pytest.raises(RuntimeError) as raised:
        await runtime.start()

    assert raised.value is commit_error
    assert calls == [
        "bus.start",
        "bus.collect",
        "registry.merge",
        "backup.start",
        "admin.install",
        "chat.commit",
        "tick.stop",
        "backup.stop",
        "registry.restore",
        "bus.stop",
    ]
    assert registry.tools == [core_tool]
