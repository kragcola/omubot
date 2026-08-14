"""Transactional plugin state changes owned outside the HTTP adapter."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol


class PluginBusPort(Protocol):
    def get_plugin(self, name: str) -> Any | None: ...

    def set_plugin_enabled(self, name: str, enabled: bool) -> bool: ...

    def collect_tools(self) -> Iterable[Any]: ...


class ToolRegistryPort(Protocol):
    def snapshot_tools(self) -> tuple[Any, ...]: ...

    def replace_all(self, tools: Iterable[Any]) -> None: ...


class PluginStateStorePort(Protocol):
    def get(self, name: str) -> bool | None: ...

    def set_enabled(self, name: str, enabled: bool) -> None: ...

    def clear_override(self, name: str) -> None: ...


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

        previous_enabled = bool(getattr(plugin, "enabled", False))
        toggle_policy = str(getattr(plugin, "toggle_policy", "runtime") or "runtime")
        if toggle_policy == "restart_required":
            if self._plugin_state_store is None:
                return {
                    "ok": False,
                    "error": "插件状态存储不可用，无法保存待重启状态",
                }
            try:
                previous_persisted = self._read_persisted_state(
                    name,
                    fallback=previous_enabled,
                )
            except Exception as exc:
                return {"ok": False, "error": f"插件当前持久化状态读取失败: {exc}"}
            try:
                self._plugin_state_store.set_enabled(name, enabled)
            except Exception as exc:
                rollback_error = self._restore_persisted_state_after_failure(
                    name,
                    previous=previous_persisted,
                )
                return {
                    "ok": False,
                    "error": f"插件待应用状态持久化失败: {exc}{rollback_error}",
                }
            return {
                "ok": True,
                "applied": False,
                "requires_restart": True,
                "pending_enabled": enabled,
                "plugin": self._serialize_plugin(plugin),
            }

        previous_persisted = previous_enabled
        if self._plugin_state_store is not None and not self._is_locked(plugin):
            try:
                previous_persisted = self._read_persisted_state(
                    name,
                    fallback=previous_enabled,
                )
            except Exception as exc:
                return {"ok": False, "error": f"插件当前持久化状态读取失败: {exc}"}
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
            base_tools = self._snapshot_base_tools()
        except Exception as exc:
            return {
                "ok": False,
                "error": f"插件状态未切换，工具注册表刷新失败: {exc}",
            }

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
            self._refresh_tools(base_tools)
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
                rollback_error += self._restore_persisted_state_after_failure(
                    name,
                    previous=previous_persisted,
                )
                try:
                    self._refresh_tools(base_tools)
                except Exception as rollback_exc:
                    rollback_error = f"；工具注册表回滚失败: {rollback_exc}"
                return {
                    "ok": False,
                    "error": f"插件状态未切换，持久化失败: {exc}{rollback_error}",
                }

        return {"ok": True, "plugin": self._serialize_plugin(plugin)}

    def _read_persisted_state(
        self,
        name: str,
        *,
        fallback: bool,
    ) -> bool | None:
        store = self._plugin_state_store
        if store is None:
            return fallback
        get_state = getattr(store, "get", None)
        if not callable(get_state):
            return fallback
        value = get_state(name)
        if value is None or isinstance(value, bool):
            return value
        raise TypeError("plugin persisted state must be bool or None")

    def _restore_persisted_state_after_failure(
        self,
        name: str,
        *,
        previous: bool | None,
    ) -> str:
        store = self._plugin_state_store
        if store is None:
            return ""
        get_state = getattr(store, "get", None)
        if not callable(get_state):
            return ""
        try:
            if get_state(name) == previous:
                return ""
            if previous is None:
                clear_override = getattr(store, "clear_override", None)
                if not callable(clear_override):
                    return "；持久化状态不支持恢复为无 override"
                clear_override(name)
            else:
                store.set_enabled(name, previous)
            if get_state(name) != previous:
                return "；持久化状态回滚后复读不一致"
        except Exception as exc:
            try:
                if get_state(name) == previous:
                    return ""
            except Exception:
                pass
            return f"；持久化状态回滚失败: {exc}"
        return ""

    def _snapshot_base_tools(self) -> tuple[Any, ...]:
        registry = self._tool_registry
        if registry is None:
            return ()
        snapshot_tools = getattr(registry, "snapshot_tools", None)
        replace_all = getattr(registry, "replace_all", None)
        if not callable(snapshot_tools) or not callable(replace_all):
            raise TypeError(
                "tool registry must provide atomic replace_all and snapshot_tools"
            )
        plugin_tool_names = {
            str(getattr(tool, "name", "")) for tool in self._bus.collect_tools()
        }
        return tuple(
            tool
            for tool in registry.snapshot_tools()
            if str(getattr(tool, "name", "")) not in plugin_tool_names
        )

    def _refresh_tools(self, base_tools: Iterable[Any]) -> None:
        registry = self._tool_registry
        if registry is None:
            return
        replace_all = getattr(registry, "replace_all", None)
        if not callable(replace_all):
            raise TypeError("tool registry must provide atomic replace_all")
        replace_all([*base_tools, *self._bus.collect_tools()])
