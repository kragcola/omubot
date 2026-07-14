"""Transactional plugin state changes owned outside the HTTP adapter."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol


class PluginBusPort(Protocol):
    def get_plugin(self, name: str) -> Any | None: ...

    def set_plugin_enabled(self, name: str, enabled: bool) -> bool: ...

    def collect_tools(self) -> Iterable[Any]: ...


class ToolRegistryPort(Protocol):
    def replace_all(self, tools: Iterable[Any]) -> None: ...


class PluginStateStorePort(Protocol):
    def set_enabled(self, name: str, enabled: bool) -> None: ...


class PluginToggleService:
    """Own plugin toggle policy, persistence, and runtime rollback."""

    def __init__(
        self,
        *,
        bus: PluginBusPort,
        tool_registry: ToolRegistryPort | None = None,
        plugin_state_store: PluginStateStorePort | None = None,
        is_locked: Callable[[Any], bool],
        serialize_plugin: Callable[[Any], dict[str, Any]],
    ) -> None:
        self._bus = bus
        self._tool_registry = tool_registry
        self._plugin_state_store = plugin_state_store
        self._is_locked = is_locked
        self._serialize_plugin = serialize_plugin

    def toggle(self, name: str, enabled: bool) -> dict[str, Any]:
        plugin = self._bus.get_plugin(name)
        if plugin is None:
            return {"ok": False, "error": f"Plugin '{name}' not found"}
        if not enabled and self._is_locked(plugin):
            return {"ok": False, "error": "系统级插件无法关闭"}

        toggle_policy = str(getattr(plugin, "toggle_policy", "runtime") or "runtime")
        if toggle_policy == "restart_required":
            if self._plugin_state_store is None:
                return {
                    "ok": False,
                    "error": "插件状态存储不可用，无法保存待重启状态",
                }
            try:
                self._plugin_state_store.set_enabled(name, enabled)
            except Exception as exc:
                return {"ok": False, "error": f"插件待应用状态持久化失败: {exc}"}
            return {
                "ok": True,
                "applied": False,
                "requires_restart": True,
                "pending_enabled": enabled,
                "plugin": self._serialize_plugin(plugin),
            }

        previous_enabled = bool(getattr(plugin, "enabled", False))
        snapshot_runtime_state = getattr(self._bus, "snapshot_runtime_state", None)
        restore_runtime_state = getattr(self._bus, "restore_runtime_state", None)
        bus_snapshot = (
            snapshot_runtime_state() if callable(snapshot_runtime_state) and callable(restore_runtime_state) else None
        )

        def rollback_bus() -> None:
            if bus_snapshot is not None and callable(restore_runtime_state):
                restore_runtime_state(bus_snapshot)
                return
            self._bus.set_plugin_enabled(name, previous_enabled)

        def rollback_bus_safely() -> str:
            try:
                rollback_bus()
            except Exception as rollback_exc:
                return f"；运行态回滚失败: {rollback_exc}"
            return ""

        try:
            state_changed = self._bus.set_plugin_enabled(name, enabled)
        except Exception as exc:
            rollback_error = ""
            if not bool(getattr(self._bus, "runtime_state_transactions_atomic", False)):
                rollback_error = rollback_bus_safely()
            return {
                "ok": False,
                "error": f"插件状态未切换，运行态刷新失败: {exc}{rollback_error}",
            }

        if not state_changed:
            rollback_error = rollback_bus_safely()
            return {
                "ok": False,
                "error": f"Plugin '{name}' not found{rollback_error}",
            }

        try:
            self._refresh_tools()
        except Exception as exc:
            rollback_error = rollback_bus_safely()
            return {
                "ok": False,
                "error": f"插件状态未切换，工具注册表刷新失败: {exc}{rollback_error}",
            }

        if self._plugin_state_store is not None and not self._is_locked(plugin):
            try:
                self._plugin_state_store.set_enabled(name, enabled)
            except Exception as exc:
                rollback_error = rollback_bus_safely()
                try:
                    self._refresh_tools()
                except Exception as rollback_exc:
                    rollback_error = f"；工具注册表回滚失败: {rollback_exc}"
                return {
                    "ok": False,
                    "error": f"插件状态未切换，持久化失败: {exc}{rollback_error}",
                }

        return {"ok": True, "plugin": self._serialize_plugin(plugin)}

    def _refresh_tools(self) -> None:
        registry = self._tool_registry
        if registry is None:
            return
        replace_all = getattr(registry, "replace_all", None)
        if not callable(replace_all):
            raise TypeError("tool registry must provide atomic replace_all")
        replace_all(list(self._bus.collect_tools()))
