"""Behavior contracts for the process-level application composition."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pytest

from bootstrap import application as application_module
from bootstrap.application import ApplicationRuntime


@dataclass
class _Context:
    llm_client: object


class _Bus:
    def __init__(
        self,
        calls: list[str],
        tools: Iterable[object],
        *,
        tick_error: BaseException | None = None,
        shutdown_error: BaseException | None = None,
    ) -> None:
        self._calls = calls
        self._tools = list(tools)
        self.tick_error = tick_error
        self.shutdown_error = shutdown_error

    async def fire_on_startup(self, ctx: _Context) -> None:
        self._calls.append("bus.fire_on_startup")

    def collect_tools(self) -> list[object]:
        self._calls.append("bus.collect_tools")
        return list(self._tools)

    async def stop_tick_loop(self) -> None:
        self._calls.append("bus.stop_tick_loop")
        if self.tick_error is not None:
            raise self.tick_error

    async def fire_on_shutdown(self, ctx: _Context) -> None:
        self._calls.append("bus.fire_on_shutdown")
        if self.shutdown_error is not None:
            raise self.shutdown_error


class _Registry:
    def __init__(
        self,
        calls: list[str],
        tools: Iterable[object],
        *,
        merge_error: BaseException | None = None,
        restore_error: BaseException | None = None,
    ) -> None:
        self._calls = calls
        self.tools = list(tools)
        self.merge_error = merge_error
        self.restore_error = restore_error

    def snapshot_tools(self) -> tuple[object, ...]:
        return tuple(self.tools)

    def merge_all(self, tools: Iterable[object]) -> None:
        self._calls.append("registry.merge_all")
        if self.merge_error is not None:
            raise self.merge_error
        self.tools.extend(tools)

    def replace_all(self, tools: Iterable[object]) -> None:
        self._calls.append("registry.restore")
        if self.restore_error is not None:
            raise self.restore_error
        self.tools = list(tools)


class _Backup:
    def __init__(
        self,
        calls: list[str],
        *,
        start_error: BaseException | None = None,
        stop_error: BaseException | None = None,
    ) -> None:
        self._calls = calls
        self.start_error = start_error
        self.stop_error = stop_error

    async def start(self) -> None:
        self._calls.append("backup.start")
        if self.start_error is not None:
            raise self.start_error

    async def stop(self) -> None:
        self._calls.append("backup.stop")
        if self.stop_error is not None:
            raise self.stop_error


class _Admin:
    def __init__(self, calls: list[str], chat_sentinel: object) -> None:
        self._calls = calls
        self._chat_sentinel = chat_sentinel

    def install(self, ctx: _Context) -> None:
        state = "chat-ready" if ctx.llm_client is self._chat_sentinel else "chat-missing"
        self._calls.append(f"admin.install:{state}")


class _Supervisor:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def start(self) -> None:
        self._calls.append("supervisor.start")

    async def stop(self) -> None:
        self._calls.append("supervisor.stop")


class _LearningCoordinatorLifecycle:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def start(self) -> None:
        self._calls.append("learning.start")

    async def stop(self) -> None:
        self._calls.append("learning.stop")


class _MemoryConsolidatorLifecycle:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def start(self) -> None:
        self._calls.append("memory.start")

    async def stop(self) -> None:
        self._calls.append("memory.stop")


def _compose(
    *,
    ctx: _Context,
    bus: _Bus,
    registry: _Registry,
    backup: _Backup,
    admin: _Admin,
    task_supervisor: _Supervisor | None = None,
    memory_consolidator_lifecycle: _MemoryConsolidatorLifecycle | None = None,
    learning_extract_coordinator: _LearningCoordinatorLifecycle | None = None,
) -> ApplicationRuntime:
    compose = getattr(application_module, "compose_application_runtime", None)
    assert callable(compose), "compose_application_runtime must be implemented"
    dependencies: dict[str, Any] = {
        "ctx": ctx,
        "bus": bus,
        "registry": registry,
        "backup": backup,
        "admin": admin,
    }
    if task_supervisor is not None:
        dependencies["task_supervisor"] = task_supervisor
    if memory_consolidator_lifecycle is not None:
        dependencies["memory_consolidator_lifecycle"] = memory_consolidator_lifecycle
    if learning_extract_coordinator is not None:
        dependencies["learning_extract_coordinator"] = learning_extract_coordinator
    runtime: Any = compose(
        **dependencies,
    )
    assert isinstance(runtime, ApplicationRuntime)
    return runtime


@pytest.mark.asyncio
async def test_memory_consolidator_starts_after_and_stops_before_plugin_bus() -> None:
    calls: list[str] = []
    sentinel = object()
    memory = _MemoryConsolidatorLifecycle(calls)
    runtime = _compose(
        ctx=_Context(llm_client=sentinel),
        bus=_Bus(calls, []),
        registry=_Registry(calls, []),
        backup=_Backup(calls),
        admin=_Admin(calls, sentinel),
        memory_consolidator_lifecycle=memory,
    )

    await runtime.start()
    await runtime.stop()

    assert calls.index("bus.fire_on_startup") < calls.index("memory.start")
    assert calls.index("memory.stop") < calls.index("bus.fire_on_shutdown")


@pytest.mark.asyncio
async def test_composed_supervisor_starts_first_and_stops_last() -> None:
    calls: list[str] = []
    sentinel = object()
    supervisor = _Supervisor(calls)
    runtime = _compose(
        ctx=_Context(llm_client=sentinel),
        bus=_Bus(calls, []),
        registry=_Registry(calls, []),
        backup=_Backup(calls),
        admin=_Admin(calls, sentinel),
        task_supervisor=supervisor,
    )

    await runtime.start()
    await runtime.stop()

    assert calls[0] == "supervisor.start"
    assert calls[-1] == "supervisor.stop"


@pytest.mark.asyncio
async def test_learning_extract_tasks_stop_before_plugin_owned_stores() -> None:
    calls: list[str] = []
    sentinel = object()
    learning = _LearningCoordinatorLifecycle(calls)
    runtime = _compose(
        ctx=_Context(llm_client=sentinel),
        bus=_Bus(calls, []),
        registry=_Registry(calls, []),
        backup=_Backup(calls),
        admin=_Admin(calls, sentinel),
        learning_extract_coordinator=learning,
    )

    await runtime.start()
    await runtime.stop()

    assert calls.index("learning.stop") < calls.index("bus.fire_on_shutdown")


@pytest.mark.asyncio
async def test_composed_startup_orders_services_and_installs_admin_last() -> None:
    calls: list[str] = []
    chat_sentinel = object()
    core_tool = object()
    plugin_tool = object()
    registry = _Registry(calls, [core_tool])
    runtime = _compose(
        ctx=_Context(llm_client=chat_sentinel),
        bus=_Bus(calls, [plugin_tool]),
        registry=registry,
        backup=_Backup(calls),
        admin=_Admin(calls, chat_sentinel),
    )

    await runtime.start()

    assert calls == [
        "bus.fire_on_startup",
        "bus.collect_tools",
        "registry.merge_all",
        "backup.start",
        "admin.install:chat-ready",
    ]
    assert registry.tools == [core_tool, plugin_tool]


@pytest.mark.asyncio
async def test_merge_failure_skips_backup_and_admin_then_shuts_down_bus() -> None:
    calls: list[str] = []
    merge_error = ValueError("duplicate plugin tool")
    chat_sentinel = object()
    core_tool = object()
    registry = _Registry(calls, [core_tool], merge_error=merge_error)
    runtime = _compose(
        ctx=_Context(llm_client=chat_sentinel),
        bus=_Bus(calls, [object()]),
        registry=registry,
        backup=_Backup(calls),
        admin=_Admin(calls, chat_sentinel),
    )

    with pytest.raises(ValueError) as raised:
        await runtime.start()

    assert raised.value is merge_error
    assert calls == [
        "bus.fire_on_startup",
        "bus.collect_tools",
        "registry.merge_all",
        "registry.restore",
        "bus.fire_on_shutdown",
    ]
    assert registry.tools == [core_tool]


@pytest.mark.asyncio
async def test_backup_failure_restores_start_call_registry_and_shuts_down_bus() -> None:
    calls: list[str] = []
    backup_error = RuntimeError("backup unavailable")
    chat_sentinel = object()
    core_tool = object()
    late_core_tool = object()
    plugin_tool = object()
    registry = _Registry(calls, [core_tool])
    runtime = _compose(
        ctx=_Context(llm_client=chat_sentinel),
        bus=_Bus(calls, [plugin_tool]),
        registry=registry,
        backup=_Backup(calls, start_error=backup_error),
        admin=_Admin(calls, chat_sentinel),
    )
    registry.tools.append(late_core_tool)

    with pytest.raises(RuntimeError) as raised:
        await runtime.start()

    assert raised.value is backup_error
    assert calls == [
        "bus.fire_on_startup",
        "bus.collect_tools",
        "registry.merge_all",
        "backup.start",
        "backup.stop",
        "registry.restore",
        "bus.fire_on_shutdown",
    ]
    assert registry.tools == [core_tool, late_core_tool]


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_step", ["tick", "backup", "registry", "bus"])
async def test_shutdown_attempts_every_step_once_despite_each_failure(
    failing_step: str,
) -> None:
    calls: list[str] = []
    stop_error = RuntimeError(f"{failing_step} stop failed")
    chat_sentinel = object()
    core_tool = object()
    plugin_tool = object()
    bus = _Bus(
        calls,
        [plugin_tool],
        tick_error=stop_error if failing_step == "tick" else None,
        shutdown_error=stop_error if failing_step == "bus" else None,
    )
    registry = _Registry(
        calls,
        [core_tool],
        restore_error=stop_error if failing_step == "registry" else None,
    )
    backup = _Backup(
        calls,
        stop_error=stop_error if failing_step == "backup" else None,
    )
    runtime = _compose(
        ctx=_Context(llm_client=chat_sentinel),
        bus=bus,
        registry=registry,
        backup=backup,
        admin=_Admin(calls, chat_sentinel),
    )
    await runtime.start()
    calls.clear()

    with pytest.raises(BaseException) as raised:
        await runtime.stop()

    assert raised.value is stop_error
    assert calls == [
        "bus.stop_tick_loop",
        "backup.stop",
        "registry.restore",
        "bus.fire_on_shutdown",
    ]
    calls_after_first_stop = list(calls)

    await runtime.stop()

    assert calls == calls_after_first_stop
