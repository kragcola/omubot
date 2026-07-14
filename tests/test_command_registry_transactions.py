"""RED contracts for transactional command-registry coordination."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api.plugins import create_plugins_router
from kernel.bus import PluginBus
from kernel.types import AmadeusPlugin, Command
from services.command import CommandDispatcher
from services.plugin_state import PluginStateStore
from services.tools.base import Tool
from services.tools.registry import ToolRegistry


class _NamedTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, ctx: Any, **kwargs: Any) -> str:
        del ctx, kwargs
        return "ok"


class _CommandToolPlugin(AmadeusPlugin):
    def __init__(
        self,
        name: str,
        *,
        enabled: bool = True,
        tool: Tool | None = None,
    ) -> None:
        super().__init__()
        self.name = name
        self.enabled = enabled
        self.priority = 10
        self.required_dependencies: dict[str, str] = {}
        self.command = Command(name=name, handler=AsyncMock())
        self.tool = tool

    def register_commands(self) -> list[Command]:
        return [self.command]

    def register_tools(self) -> Any:
        return [] if self.tool is None else [self.tool]


class _ExplodingToolProviderPlugin(_CommandToolPlugin):
    def register_tools(self) -> Any:
        if self.enabled:
            raise RuntimeError("provider exploded")
        return []


class _TransactionalCommandRegistry:
    """Observable fake supporting both the target and legacy registry APIs."""

    def __init__(self, bus: PluginBus, name: str) -> None:
        self._bus = bus
        self.name = name
        self.active: tuple[Command, ...] = ()
        self.pending: tuple[Command, ...] | None = None
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.prepare_count = 0
        self.commit_count = 0
        self.restore_count = 0
        self.legacy_refresh_count = 0
        self.prepare_failures: list[int] = []
        self.fail_prepare_calls: set[int] = set()
        self.fail_commit_calls: set[int] = set()

    def _candidate(self) -> tuple[Command, ...]:
        return tuple(self._bus.collect_commands())

    @staticmethod
    def _names(commands: Iterable[Command]) -> tuple[str, ...]:
        return tuple(command.name for command in commands)

    def prepare_refresh(self) -> tuple[Command, ...]:
        self.prepare_count += 1
        candidate = self._candidate()
        self.calls.append(("prepare", self._names(candidate)))
        if self.prepare_count in self.fail_prepare_calls:
            self.prepare_failures.append(self.prepare_count)
            raise RuntimeError(f"{self.name} prepare failure")
        self.pending = candidate
        return candidate

    def commit_refresh(
        self,
        prepared: Iterable[Command] | None = None,
    ) -> None:
        self.commit_count += 1
        candidate = tuple(prepared) if prepared is not None else self.pending
        assert candidate is not None, "commit_refresh requires a prepared snapshot"
        self.calls.append(("commit", self._names(candidate)))
        if self.commit_count in self.fail_commit_calls:
            raise RuntimeError(f"{self.name} commit failure")
        self.active = candidate
        self.pending = None

    def snapshot_state(self) -> tuple[Command, ...]:
        self.calls.append(("snapshot", self._names(self.active)))
        return self.active

    def restore_state(self, snapshot: Iterable[Command]) -> None:
        restored = tuple(snapshot)
        self.restore_count += 1
        self.calls.append(("restore", self._names(restored)))
        self.active = restored
        self.pending = None

    def refresh(self) -> None:
        """Keep old one-phase callers observable so RED is an assertion failure."""

        candidate = self._candidate()
        self.legacy_refresh_count += 1
        self.calls.append(("legacy_refresh", self._names(candidate)))
        self.active = candidate
        self.pending = None


class _UnhashableTransactionalCommandRegistry(_TransactionalCommandRegistry):
    """Valid transactional registry that deliberately cannot be hashed."""

    __hash__: Any = None


class _FailingOnceToolRegistry(ToolRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.replace_calls: list[tuple[str, ...]] = []
        self._failed = False

    def replace_all(self, tools: Iterable[Tool]) -> None:
        candidate = tuple(tools)
        self.replace_calls.append(tuple(tool.name for tool in candidate))
        if not self._failed:
            self._failed = True
            raise RuntimeError("primary tool replace_all failure")
        super().replace_all(candidate)


def _require_callable(value: object, name: str) -> Callable[..., Any]:
    method = getattr(value, name, None)
    assert callable(method), f"{type(value).__name__}.{name} must be callable"
    return cast(Callable[..., Any], method)


def _bind(bus: PluginBus, registry: _TransactionalCommandRegistry) -> None:
    _require_callable(bus, "bind_command_registry")(registry)


def _health_snapshot(bus: PluginBus) -> dict[str, dict[str, Any]]:
    return {
        item["name"]: {
            "enabled": item["enabled"],
            "state": item["state"],
            "dependency_blocked": item["dependency_blocked"],
            "dependency_errors": tuple(item["dependency_errors"]),
        }
        for item in bus.plugin_health()
    }


def _bus_snapshot(bus: PluginBus) -> dict[str, Any]:
    return {
        "plugins": tuple((plugin.name, plugin.enabled) for plugin in bus.plugins),
        "commands": tuple(command.name for command in bus.collect_commands()),
        "tools": tuple(tool.name for tool in bus.collect_tools()),
        "health": _health_snapshot(bus),
    }


def test_command_dispatcher_exposes_transactional_registry_protocol() -> None:
    dispatcher = CommandDispatcher(PluginBus())

    for method_name in (
        "prepare_refresh",
        "commit_refresh",
        "snapshot_state",
        "restore_state",
    ):
        _require_callable(dispatcher, method_name)


def test_second_registry_prepare_failure_commits_neither_registry() -> None:
    plugin = _CommandToolPlugin("candidate", enabled=False)
    bus = PluginBus()
    bus.register(plugin)
    first = _TransactionalCommandRegistry(bus, "first")
    second = _TransactionalCommandRegistry(bus, "second")
    _bind(bus, first)
    _bind(bus, second)
    before_bus = _bus_snapshot(bus)
    before_active = (first.snapshot_state(), second.snapshot_state())
    before_prepares = (first.prepare_count, second.prepare_count)
    before_commits = (first.commit_count, second.commit_count)
    second.fail_prepare_calls.add(second.prepare_count + 1)

    with pytest.raises(RuntimeError, match="second prepare failure"):
        bus.set_plugin_enabled(plugin.name, True)

    assert first.prepare_count == before_prepares[0] + 1
    assert second.prepare_count == before_prepares[1] + 1
    assert (first.commit_count, second.commit_count) == before_commits
    assert first.snapshot_state() == before_active[0]
    assert second.snapshot_state() == before_active[1]
    assert _bus_snapshot(bus) == before_bus
    assert plugin.enabled is False


def test_unhashable_second_registry_commit_failure_restores_all_state() -> None:
    plugin = _CommandToolPlugin("candidate", enabled=False)
    bus = PluginBus()
    bus.register(plugin)
    first = _UnhashableTransactionalCommandRegistry(bus, "first")
    second = _UnhashableTransactionalCommandRegistry(bus, "second")
    _bind(bus, first)
    _bind(bus, second)
    before_bus = _bus_snapshot(bus)
    before_active = (first.snapshot_state(), second.snapshot_state())
    before_prepares = (first.prepare_count, second.prepare_count)
    before_commits = (first.commit_count, second.commit_count)
    second.fail_commit_calls.add(second.commit_count + 1)

    with pytest.raises(RuntimeError, match="second commit failure") as exc_info:
        bus.set_plugin_enabled(plugin.name, True)

    assert type(exc_info.value) is RuntimeError
    assert first.prepare_count == before_prepares[0] + 1
    assert second.prepare_count == before_prepares[1] + 1
    assert first.commit_count == before_commits[0] + 1
    assert second.commit_count == before_commits[1] + 1
    assert first.snapshot_state() == before_active[0]
    assert second.snapshot_state() == before_active[1]
    assert _bus_snapshot(bus) == before_bus
    assert plugin.enabled is False


@pytest.mark.parametrize("failure_phase", ["prepare", "commit"])
def test_failed_bind_does_not_retain_registry_or_block_later_register(
    failure_phase: str,
) -> None:
    stable = _CommandToolPlugin("stable")
    bus = PluginBus()
    bus.register(stable)
    registry = _TransactionalCommandRegistry(bus, "bind")
    if failure_phase == "prepare":
        registry.fail_prepare_calls.add(1)
    else:
        registry.fail_commit_calls.add(1)

    with pytest.raises(RuntimeError, match=rf"bind {failure_phase} failure"):
        _bind(bus, registry)

    assert registry.snapshot_state() == ()
    calls_after_failed_bind = tuple(registry.calls)
    unrelated = _CommandToolPlugin(f"after_{failure_phase}")

    bus.register(unrelated)

    assert bus.get_plugin(unrelated.name) is unrelated
    assert tuple(registry.calls) == calls_after_failed_bind


def test_tool_replace_failure_restores_saved_command_snapshot_without_reprepare(
    tmp_path: Path,
) -> None:
    old_tool = _NamedTool("old_tool")
    candidate_tool = _NamedTool("candidate_tool")
    stable = _CommandToolPlugin("stable", tool=old_tool)
    candidate = _CommandToolPlugin(
        "candidate",
        enabled=False,
        tool=candidate_tool,
    )
    bus = PluginBus()
    bus.register(stable)
    bus.register(candidate)
    command_registry = _TransactionalCommandRegistry(bus, "commands")
    _bind(bus, command_registry)
    tool_registry = _FailingOnceToolRegistry()
    tool_registry.register(old_tool)
    state_store = PluginStateStore(tmp_path / "plugin-state.json")
    state_store.set_enabled(candidate.name, False)
    before_bus = _bus_snapshot(bus)
    before_commands = command_registry.snapshot_state()
    before_prepare_count = command_registry.prepare_count
    before_commit_count = command_registry.commit_count
    before_restore_count = command_registry.restore_count
    before_legacy_count = command_registry.legacy_refresh_count
    command_registry.fail_prepare_calls.add(before_prepare_count + 2)
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            bus=bus,
            tool_registry=tool_registry,
            plugin_state_store=state_store,
        ),
        prefix="/api/admin",
    )

    response = TestClient(app).post(
        f"/api/admin/plugins/{candidate.name}/state",
        json={"enabled": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert "primary tool replace_all failure" in str(payload["error"])
    assert "prepare failure" not in str(payload["error"])
    assert command_registry.prepare_count == before_prepare_count + 1
    assert command_registry.commit_count == before_commit_count + 1
    assert command_registry.restore_count == before_restore_count + 1
    assert command_registry.legacy_refresh_count == before_legacy_count
    assert command_registry.prepare_failures == []
    assert command_registry.snapshot_state() == before_commands
    assert _bus_snapshot(bus) == before_bus
    assert tool_registry.get(old_tool.name) is old_tool
    assert tool_registry.get(candidate_tool.name) is None
    assert state_store.get(candidate.name) is False


def test_tool_provider_failure_returns_owner_error_and_restores_all_state(
    tmp_path: Path,
) -> None:
    old_tool = _NamedTool("old_tool")
    incomplete_tool = _NamedTool("incomplete_tool")
    stable = _CommandToolPlugin("stable", tool=old_tool)
    candidate = _ExplodingToolProviderPlugin(
        "exploding_owner",
        enabled=False,
        tool=incomplete_tool,
    )
    bus = PluginBus()
    bus.register(stable)
    bus.register(candidate)
    command_registry = _TransactionalCommandRegistry(bus, "commands")
    _bind(bus, command_registry)
    tool_registry = ToolRegistry()
    tool_registry.register(old_tool)
    state_store = PluginStateStore(tmp_path / "plugin-state.json")
    state_store.set_enabled(candidate.name, False)
    before_bus = _bus_snapshot(bus)
    before_commands = command_registry.snapshot_state()
    before_tools = tool_registry.snapshot_tools()
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            bus=bus,
            tool_registry=tool_registry,
            plugin_state_store=state_store,
        ),
        prefix="/api/admin",
    )

    response = TestClient(app).post(
        f"/api/admin/plugins/{candidate.name}/state",
        json={"enabled": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    error = str(payload["error"])
    assert candidate.name in error
    assert "provider exploded" in error
    assert candidate.enabled is False
    assert state_store.get(candidate.name) is False
    assert command_registry.snapshot_state() == before_commands
    assert tool_registry.snapshot_tools() == before_tools
    assert tool_registry.get(old_tool.name) is old_tool
    assert tool_registry.get(incomplete_tool.name) is None
    assert _bus_snapshot(bus) == before_bus


@pytest.mark.parametrize("operation", ["register", "unregister", "set-enable"])
def test_prepare_failure_keeps_all_registry_active_snapshots_consistent(
    operation: str,
) -> None:
    anchor = _CommandToolPlugin("anchor")
    target = _CommandToolPlugin("target", enabled=operation != "set-enable")
    bus = PluginBus()
    bus.register(anchor)
    if operation != "register":
        bus.register(target)
    first = _TransactionalCommandRegistry(bus, "first")
    second = _TransactionalCommandRegistry(bus, "second")
    _bind(bus, first)
    _bind(bus, second)
    before_bus = _bus_snapshot(bus)
    before_active = (first.snapshot_state(), second.snapshot_state())
    before_prepares = (first.prepare_count, second.prepare_count)
    before_commits = (first.commit_count, second.commit_count)
    second.fail_prepare_calls.add(second.prepare_count + 1)

    with pytest.raises(RuntimeError, match="second prepare failure"):
        if operation == "register":
            bus.register(target)
        elif operation == "unregister":
            bus.unregister(target.name)
        else:
            bus.set_plugin_enabled(target.name, True)

    assert first.prepare_count == before_prepares[0] + 1
    assert second.prepare_count == before_prepares[1] + 1
    assert (first.commit_count, second.commit_count) == before_commits
    assert first.snapshot_state() == second.snapshot_state()
    assert first.snapshot_state() == before_active[0]
    assert second.snapshot_state() == before_active[1]
    assert _bus_snapshot(bus) == before_bus
