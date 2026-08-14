from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api.plugins import create_plugins_router
from kernel.bus import PluginBus
from kernel.types import AmadeusPlugin
from services.plugin_state import PluginStateStore
from services.plugin_toggle import PluginToggleService
from services.tools.base import Tool
from services.tools.registry import ToolRegistry


class _RestartPlugin(AmadeusPlugin):
    name = "restart_demo"
    toggle_policy = "restart_required"


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
        return "ok"


class _ToolPlugin(AmadeusPlugin):
    def __init__(
        self,
        *,
        name: str,
        tools: list[Any],
        enabled: bool,
        priority: int,
    ) -> None:
        self.name = name
        self._tools = tools
        self.enabled = enabled
        self.priority = priority
        self.required_dependencies: dict[str, str] = {}

    def register_tools(self) -> Any:
        return list(self._tools)


class _TrackingRegistry:
    def __init__(self) -> None:
        self.clear_calls = 0
        self.registered: list[Any] = []

    def clear(self) -> None:
        self.clear_calls += 1

    def register(self, tool: Any) -> None:
        self.registered.append(tool)


class _FailingNonAtomicRegistry:
    def __init__(self, tools: list[Any], *, fail_on: str) -> None:
        self.clear_calls = 0
        self.registered = list(tools)
        self._fail_on = fail_on

    def clear(self) -> None:
        self.clear_calls += 1
        self.registered.clear()

    def register(self, tool: Any) -> None:
        if getattr(tool, "name", None) == self._fail_on:
            raise RuntimeError("injected register failure")
        self.registered.append(tool)


class _FailingPluginStateStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def set_enabled(self, name: str, enabled: bool) -> None:
        self.calls.append((name, enabled))
        raise RuntimeError("state persistence failed")


class _WriteThenRaisePluginStateStore:
    def __init__(self, initial: bool) -> None:
        self.value: bool | None = initial
        self.calls: list[tuple[str, bool]] = []
        self._fail_next = True

    def get(self, name: str) -> bool | None:
        return self.value

    def set_enabled(self, name: str, enabled: bool) -> None:
        self.calls.append((name, enabled))
        self.value = enabled
        if self._fail_next:
            self._fail_next = False
            raise RuntimeError("state persisted before failure")

    def clear_override(self, name: str) -> None:
        self.value = None


class _WriteThenRaiseRealPluginStateStore(PluginStateStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self._fail_next = True

    def set_enabled(self, name: str, enabled: bool) -> None:
        super().set_enabled(name, enabled)
        if self._fail_next:
            self._fail_next = False
            raise RuntimeError("state persisted before failure")


def test_restart_toggle_service_persists_without_hot_apply(tmp_path: Path) -> None:
    bus = PluginBus()
    plugin = _RestartPlugin()
    bus.register(plugin)
    state_store = PluginStateStore(tmp_path / "plugin-state.json")
    registry = _TrackingRegistry()
    service = PluginToggleService(
        bus=bus,
        tool_registry=cast(Any, registry),
        plugin_state_store=state_store,
        is_locked=PluginBus.is_plugin_locked,
        serialize_plugin=lambda value: {"name": value.name, "enabled": value.enabled},
    )

    payload = service.toggle("restart_demo", False)

    assert payload == {
        "ok": True,
        "applied": False,
        "requires_restart": True,
        "pending_enabled": False,
        "plugin": {"name": "restart_demo", "enabled": True},
    }
    assert plugin.enabled is True
    assert state_store.get("restart_demo") is False
    assert registry.clear_calls == 0


def test_restart_toggle_without_state_store_fails_closed() -> None:
    bus = PluginBus()
    plugin = _RestartPlugin()
    bus.register(plugin)
    service = PluginToggleService(
        bus=bus,
        plugin_state_store=None,
        is_locked=PluginBus.is_plugin_locked,
        serialize_plugin=lambda value: {"name": value.name, "enabled": value.enabled},
    )

    payload = service.toggle("restart_demo", False)

    assert payload == {
        "ok": False,
        "error": "插件状态存储不可用，无法保存待重启状态",
    }
    assert plugin.enabled is True


def test_admin_restart_toggle_persists_without_hot_apply(tmp_path: Path) -> None:
    bus = PluginBus()
    plugin = _RestartPlugin()
    bus.register(plugin)
    state_store = PluginStateStore(tmp_path / "plugin-state.json")
    registry = _TrackingRegistry()
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            bus=bus,
            tool_registry=registry,
            plugin_state_store=state_store,
        ),
        prefix="/api/admin",
    )

    response = TestClient(app).post(
        "/api/admin/plugins/restart_demo/state",
        json={"enabled": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "ok",
        "applied",
        "requires_restart",
        "pending_enabled",
        "plugin",
    }
    assert payload["ok"] is True
    assert payload["applied"] is False
    assert payload["requires_restart"] is True
    assert payload["pending_enabled"] is False
    assert payload["plugin"]["persistent_enabled"] is False
    assert plugin.enabled is True
    assert state_store.get("restart_demo") is False
    assert registry.clear_calls == 0


def test_admin_runtime_toggle_preserves_base_sha_http_contract(tmp_path: Path) -> None:
    plugin = _ToolPlugin(
        name="runtime_demo",
        tools=[_NamedTool("runtime_tool")],
        enabled=True,
        priority=10,
    )
    bus = PluginBus()
    bus.register(plugin)
    state_store = PluginStateStore(tmp_path / "plugin-state.json")
    registry = ToolRegistry()
    for tool in bus.collect_tools():
        registry.register(cast(Any, tool))
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            bus=bus,
            tool_registry=registry,
            plugin_state_store=state_store,
        ),
        prefix="/api/admin",
    )

    response = TestClient(app).post(
        "/api/admin/plugins/runtime_demo/state",
        json={"enabled": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"ok", "plugin"}
    assert payload["ok"] is True
    assert payload["plugin"]["enabled"] is False
    assert payload["plugin"]["persistent_enabled"] is False
    assert registry.empty is True
    assert state_store.get("runtime_demo") is False


def test_runtime_toggle_preserves_non_plugin_base_tools(tmp_path: Path) -> None:
    base_tool = _NamedTool("base_interaction")
    plugin_tool = _NamedTool("runtime_tool")
    plugin = _ToolPlugin(
        name="runtime_demo",
        tools=[plugin_tool],
        enabled=True,
        priority=10,
    )
    bus = PluginBus()
    bus.register(plugin)
    state_store = PluginStateStore(tmp_path / "plugin-state.json")
    registry = ToolRegistry()
    registry.register(base_tool)
    registry.register(plugin_tool)
    service = PluginToggleService(
        bus=bus,
        tool_registry=registry,
        plugin_state_store=state_store,
        is_locked=PluginBus.is_plugin_locked,
        serialize_plugin=lambda value: {
            "name": value.name,
            "enabled": value.enabled,
        },
    )

    disabled = service.toggle(plugin.name, False)

    assert disabled["ok"] is True
    assert registry.snapshot_tools() == (base_tool,)
    assert registry.get("base_interaction") is base_tool
    assert registry.get("runtime_tool") is None

    enabled = service.toggle(plugin.name, True)

    assert enabled["ok"] is True
    assert registry.snapshot_tools() == (base_tool, plugin_tool)
    assert registry.get("base_interaction") is base_tool
    assert registry.get("runtime_tool") is plugin_tool


def test_persistence_post_write_failure_restores_base_tools_and_state() -> None:
    base_tool = _NamedTool("base_interaction")
    plugin_tool = _NamedTool("runtime_tool")
    plugin = _ToolPlugin(
        name="runtime_demo",
        tools=[plugin_tool],
        enabled=True,
        priority=10,
    )
    bus = PluginBus()
    bus.register(plugin)
    state_store = _WriteThenRaisePluginStateStore(initial=True)
    registry = ToolRegistry()
    registry.register(base_tool)
    registry.register(plugin_tool)
    service = PluginToggleService(
        bus=bus,
        tool_registry=registry,
        plugin_state_store=state_store,
        is_locked=PluginBus.is_plugin_locked,
        serialize_plugin=lambda value: {
            "name": value.name,
            "enabled": value.enabled,
        },
    )

    payload = service.toggle(plugin.name, False)

    assert payload["ok"] is False
    assert plugin.enabled is True
    assert state_store.value is True
    assert state_store.calls == [(plugin.name, False), (plugin.name, True)]
    assert registry.snapshot_tools() == (base_tool, plugin_tool)


def test_persistence_post_write_failure_restores_missing_override(
    tmp_path: Path,
) -> None:
    plugin_tool = _NamedTool("runtime_tool")
    plugin = _ToolPlugin(
        name="runtime_demo",
        tools=[plugin_tool],
        enabled=True,
        priority=10,
    )
    bus = PluginBus()
    bus.register(plugin)
    state_store = _WriteThenRaiseRealPluginStateStore(
        tmp_path / "plugin-state.json"
    )
    registry = ToolRegistry()
    registry.register(plugin_tool)
    service = PluginToggleService(
        bus=bus,
        tool_registry=registry,
        plugin_state_store=state_store,
        is_locked=PluginBus.is_plugin_locked,
        serialize_plugin=lambda value: {
            "name": value.name,
            "enabled": value.enabled,
        },
    )
    assert state_store.get(plugin.name) is None

    payload = service.toggle(plugin.name, False)

    assert payload["ok"] is False
    assert plugin.enabled is True
    assert state_store.get(plugin.name) is None
    assert registry.snapshot_tools() == (plugin_tool,)


def test_runtime_enable_duplicate_tool_rolls_back_all_state(tmp_path: Path) -> None:
    old_shared = _NamedTool("shared")
    old_tail = _NamedTool("tail")
    conflicting = _NamedTool("shared")
    existing = _ToolPlugin(
        name="existing_tools",
        tools=[old_shared, old_tail],
        enabled=True,
        priority=10,
    )
    candidate = _ToolPlugin(
        name="duplicate_tools",
        tools=[conflicting],
        enabled=False,
        priority=5,
    )
    bus = PluginBus()
    bus.register(existing)
    bus.register(candidate)
    state_store = PluginStateStore(tmp_path / "plugin-state.json")
    state_store.set_enabled(candidate.name, False)
    registry = ToolRegistry()
    for tool in bus.collect_tools():
        registry.register(cast(Any, tool))
    before = {
        "tool_names": [item["function"]["name"] for item in registry.to_openai_tools()],
        "shared_is_old": registry.get("shared") is old_shared,
        "plugin_enabled": candidate.enabled,
        "persisted_enabled": state_store.get(candidate.name),
    }
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            bus=bus,
            tool_registry=registry,
            plugin_state_store=state_store,
        ),
        prefix="/api/admin",
    )

    response = TestClient(app).post(
        f"/api/admin/plugins/{candidate.name}/state",
        json={"enabled": True},
    )

    assert response.status_code == 200
    assert set(response.json()) == {"ok", "error"}
    assert response.json()["ok"] is False
    after = {
        "tool_names": [item["function"]["name"] for item in registry.to_openai_tools()],
        "shared_is_old": registry.get("shared") is old_shared,
        "plugin_enabled": candidate.enabled,
        "persisted_enabled": state_store.get(candidate.name),
    }
    assert after == before


def test_runtime_toggle_rejects_non_atomic_registry_before_mutation(tmp_path: Path) -> None:
    original = _NamedTool("original")
    candidate = _ToolPlugin(
        name="candidate",
        tools=[_NamedTool("first"), _NamedTool("failure")],
        enabled=False,
        priority=10,
    )
    bus = PluginBus()
    bus.register(candidate)
    state_store = PluginStateStore(tmp_path / "plugin-state.json")
    state_store.set_enabled(candidate.name, False)
    registry = _FailingNonAtomicRegistry([original], fail_on="failure")
    service = PluginToggleService(
        bus=bus,
        tool_registry=cast(Any, registry),
        plugin_state_store=state_store,
        is_locked=PluginBus.is_plugin_locked,
        serialize_plugin=lambda value: {"name": value.name, "enabled": value.enabled},
    )

    payload = service.toggle(candidate.name, True)

    assert set(payload) == {"ok", "error"}
    assert payload["ok"] is False
    assert "atomic replace_all" in payload["error"]
    assert candidate.enabled is False
    assert state_store.get(candidate.name) is False
    assert registry.clear_calls == 0
    assert registry.registered == [original]


def test_runtime_disable_persistence_failure_restores_dependency_state() -> None:
    provider = _ToolPlugin(
        name="provider",
        tools=[],
        enabled=True,
        priority=10,
    )
    consumer = _ToolPlugin(
        name="consumer",
        tools=[],
        enabled=True,
        priority=20,
    )
    consumer.required_dependencies = {provider.name: ">=0.1.0"}
    bus = PluginBus()
    bus.register(provider)
    bus.register(consumer)
    state_store = _FailingPluginStateStore()

    def snapshot() -> dict[str, Any]:
        health = {item["name"]: item for item in bus.plugin_health()}
        return {
            "provider_enabled": provider.enabled,
            "consumer_enabled": consumer.enabled,
            "provider_health": {
                "enabled": health[provider.name]["enabled"],
                "state": health[provider.name]["state"],
                "dependency_blocked": health[provider.name]["dependency_blocked"],
                "dependency_errors": health[provider.name]["dependency_errors"],
            },
            "consumer_health": {
                "enabled": health[consumer.name]["enabled"],
                "state": health[consumer.name]["state"],
                "dependency_blocked": health[consumer.name]["dependency_blocked"],
                "dependency_errors": health[consumer.name]["dependency_errors"],
            },
        }

    before = snapshot()
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            bus=bus,
            plugin_state_store=state_store,
        ),
        prefix="/api/admin",
    )

    response = TestClient(app).post(
        f"/api/admin/plugins/{provider.name}/state",
        json={"enabled": False},
    )

    assert response.status_code == 200
    assert set(response.json()) == {"ok", "error"}
    assert response.json()["ok"] is False
    assert state_store.calls == [(provider.name, False)]
    assert snapshot() == before


def test_plugin_manifest_toggle_policy_matches_resource_lifecycle() -> None:
    expected_restart = {
        "calendar_context",
        "debug_commands",
        "dream",
        "food",
        "knowledge",
        "memo",
        "qzone_journal",
        "schedule",
        "slang",
        "social_narrative",
        "sticker",
            "style",
            "worldbook",
        }
    expected_runtime = {
        "affection",
        "bilibili",
        "datetime",
        "echo",
        "element_detector",
        "group_admin",
        "http_api",
        "web_fetch",
        "web_search",
    }
    expected_locked = {"chat", "context", "history_loader", "vision"}
    actual: dict[str, str] = {}
    for manifest_path in Path("plugins").glob("*/plugin.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual[str(manifest["name"])] = str(manifest["toggle_policy"])

    assert {name for name, policy in actual.items() if policy == "restart_required"} == expected_restart
    assert {name for name, policy in actual.items() if policy == "runtime"} == expected_runtime
    assert {name for name, policy in actual.items() if policy == "locked"} == expected_locked
