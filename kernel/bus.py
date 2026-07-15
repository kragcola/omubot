"""Omubot 内核调度器。

PluginBus 管理插件的注册、生命周期和钩子调度。
核心保证：
- 同一钩子的所有插件按 priority 顺序串行执行
- 单个插件的异常不会影响其他插件
- 所有钩子调用都有日志和耗时记录
- 严格遵守"内核不 import 服务/插件"原则
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Awaitable
from copy import deepcopy
from pathlib import Path
from typing import Any

from loguru import logger

from kernel.background_tasks import (
    BackgroundTaskSupervisor,
    RestartPolicy,
    ShutdownPolicy,
    TaskKind,
    TaskSpec,
)
from kernel.manifest import PluginManifestV3, check_version, load_plugin_manifest
from kernel.types import (
    AdminRoute,
    AmadeusPlugin,
    Command,
    MessageContext,
    PluginContext,
    PluginTier,
    PluginTogglePolicy,
    PromptContext,
    ReplyContext,
    ThinkerContext,
    Tool,
)
from kernel.version import VERSION

_L = logger.bind(channel="bus")


class _HookDeadlineExceeded(Exception):
    """Raised only when PluginBus reaches its own wall-clock deadline."""


_HOOK_FAILED = object()
SYSTEM_PLUGIN_NAMES = frozenset({"chat", "context", "history_loader", "vision"})


class PluginBus:
    """插件总线。"""

    runtime_state_transactions_atomic = True
    _SYSTEM_PLUGIN_WHITELIST = SYSTEM_PLUGIN_NAMES
    _SOFT_ISOLATION_HOOKS = frozenset({
        "on_message",
        "on_pre_prompt",
        "on_post_reply",
        "on_thinker_decision",
        "on_tick",
    })
    _LIFECYCLE_HOOKS = frozenset({"on_startup", "on_shutdown", "on_bot_connect"})
    _ERROR_BURST_LIMIT: int = 3
    _SLOW_BURST_LIMIT: int = 4
    _BURST_WINDOW_SECONDS: float = 120.0
    _SOFT_ISOLATION_COOLDOWN_SECONDS: float = 90.0

    def __init__(
        self,
        *,
        task_supervisor: BackgroundTaskSupervisor | None = None,
        omubot_version: str = VERSION,
    ) -> None:
        self._plugins: list[AmadeusPlugin] = []
        self._started: bool = False
        self._tick_task: asyncio.Task[None] | None = None
        self._task_supervisor = task_supervisor
        self._omubot_version = omubot_version
        self._health: dict[str, dict[str, Any]] = {}
        self._startup_succeeded: set[str] = set()
        self._command_registries: list[Any] = []

    # ---- 属性 ----

    @property
    def plugins(self) -> list[AmadeusPlugin]:
        """返回已注册插件列表（只读）。"""
        return list(self._plugins)

    @property
    def started(self) -> bool:
        """是否已启动（fire_on_startup 已被调用）。"""
        return self._started

    # ---- 注册 ----

    def register(
        self,
        plugin: AmadeusPlugin,
        *,
        manifest: PluginManifestV3 | None = None,
    ) -> None:
        """注册一个插件。按 priority 升序排列。

        必须在 on_startup 之前调用。相同 priority 保持注册顺序（稳定排序）。
        """
        if self._started:
            raise RuntimeError(
                f"Cannot register plugin '{plugin.name}' after startup. "
                f"Call register() before fire_on_startup()."
            )
        if manifest is None:
            self._apply_local_manifest(plugin)
        else:
            if not self._manifest_runtime_compatible(manifest):
                raise ValueError(
                    "plugin requires newer Omubot: "
                    f"plugin={manifest.name} "
                    f"required={manifest.min_omubot_version} "
                    f"current={self._omubot_version}"
                )
            self._apply_manifest(
                plugin,
                manifest.model_dump(by_alias=True, exclude_none=True),
            )
        if self.get_plugin(plugin.name) is not None:
            raise ValueError(f"duplicate plugin runtime name: {plugin.name}")
        self._normalize_plugin_lock_policy(plugin)
        # 插入到第一个 priority 更大的插件之前（稳定排序）
        idx = len(self._plugins)
        for i, p in enumerate(self._plugins):
            if plugin.priority < p.priority:
                idx = i
                break
        if self.is_plugin_locked(plugin) and not plugin.enabled:
            _L.warning("locked plugin declared disabled, forcing enabled | name={}", plugin.name)
            plugin.enabled = True
        self._plugins.insert(idx, plugin)
        health = self._ensure_health(plugin.name)
        health["enabled"] = plugin.enabled
        health["state"] = "disabled" if not plugin.enabled else "healthy"
        try:
            self._refresh_command_registries()
        except Exception:
            self._plugins.pop(idx)
            self._health.pop(plugin.name, None)
            raise
        _L.info("plugin registered | name={} priority={}", plugin.name, plugin.priority)

    def unregister(self, name: str) -> bool:
        """按名称移除插件。返回 True 表示成功移除。"""
        for i, p in enumerate(self._plugins):
            if p.name == name:
                self._plugins.pop(i)
                previous_health = self._health.pop(name, None)
                try:
                    self._refresh_command_registries()
                except Exception:
                    self._plugins.insert(i, p)
                    if previous_health is not None:
                        self._health[name] = previous_health
                    raise
                _L.info("plugin unregistered | name={}", name)
                return True
        return False

    def get_plugin(self, name: str) -> AmadeusPlugin | None:
        """按名称查找插件。"""
        for p in self._plugins:
            if p.name == name:
                return p
        return None

    @staticmethod
    def is_plugin_locked(plugin: AmadeusPlugin | None) -> bool:
        """Return whether a plugin is protected from runtime disable."""
        if plugin is None:
            return False
        name = str(getattr(plugin, "name", "") or "")
        tier = PluginBus._normalize_tier(name, str(getattr(plugin, "tier", "user") or "user"))
        policy = PluginBus._normalize_toggle_policy(
            name,
            str(getattr(plugin, "toggle_policy", "runtime") or "runtime"),
            tier,
        )
        return (
            tier == "system"
            or policy == "locked"
        )

    def set_plugin_enabled(self, name: str, enabled: bool) -> bool:
        """Enable/disable a registered plugin at runtime.

        This gates future hook, tool, command, and admin-route collection.
        Existing long-running resources owned by the plugin are not forcibly
        stopped; plugins that need hard lifecycle reloads should expose their
        own restart-safe controls.
        """
        plugin = self.get_plugin(name)
        if plugin is None:
            return False
        if not enabled and self.is_plugin_locked(plugin):
            _L.warning(
                "locked plugin disable refused | name={} tier={} policy={}",
                name,
                getattr(plugin, "tier", ""),
                getattr(plugin, "toggle_policy", ""),
            )
            return False
        health = self._ensure_health(name)
        if not enabled:
            health["dependency_auto_disabled"] = False
        if enabled and self._started:
            if bool(health.get("startup_failed", False)):
                _L.warning("plugin enable refused after startup failure | name={}", name)
                return False
            if name not in self._startup_succeeded:
                _L.warning("plugin enable refused before successful startup | name={}", name)
                return False
            failed_dependencies = [
                dependency
                for dependency in self._required_dependencies(plugin)
                if dependency not in self._startup_succeeded
            ]
            if failed_dependencies:
                _L.warning(
                    "plugin enable refused after dependency startup failure | "
                    "name={} dependencies={}",
                    name,
                    failed_dependencies,
                )
                return False
        runtime_snapshot = self.snapshot_runtime_state()
        try:
            plugin.enabled = enabled
            health["enabled"] = enabled
            self._clear_cooldown(health)
            self._refresh_health_state(health, enabled)
            self._resolve_dependencies()
            if enabled and not plugin.enabled:
                self._refresh_command_registries()
                _L.warning("plugin enable blocked by dependency contract | name={}", name)
                return False
            self._refresh_command_registries()
        except Exception:
            try:
                self.restore_runtime_state(runtime_snapshot)
            except Exception as rollback_exc:
                _L.error(
                    "plugin state rollback refresh failed | name={} error={}",
                    name,
                    rollback_exc,
                )
            raise
        _L.info("plugin state changed | name={} enabled={}", name, enabled)
        return True

    def snapshot_runtime_state(self) -> dict[str, Any]:
        """Capture mutable toggle state for an Admin transaction rollback."""
        return {
            "plugins": tuple((plugin, bool(plugin.enabled)) for plugin in self._plugins),
            "health": deepcopy(self._health),
            "command_registries": self._snapshot_command_registry_states(),
        }

    def restore_runtime_state(self, snapshot: dict[str, Any]) -> None:
        """Restore a snapshot without re-running dependency side effects."""
        plugin_states = snapshot.get("plugins")
        if not isinstance(plugin_states, tuple) or len(plugin_states) != len(self._plugins):
            raise ValueError("plugin runtime snapshot does not match current registry")
        for current, item in zip(self._plugins, plugin_states, strict=True):
            if not isinstance(item, tuple) or len(item) != 2 or item[0] is not current:
                raise ValueError("plugin runtime snapshot order changed")
        for plugin, enabled in plugin_states:
            plugin.enabled = bool(enabled)
        health = snapshot.get("health")
        if not isinstance(health, dict):
            raise ValueError("plugin runtime snapshot health is invalid")
        self._health = deepcopy(health)
        self._restore_command_registry_states(snapshot.get("command_registries"))

    def bind_command_registry(self, registry: Any) -> None:
        """Prepare and commit a registry before exposing it to Bus transactions."""
        self._validate_command_registry(registry)
        snapshot = registry.snapshot_state()
        try:
            prepared = registry.prepare_refresh()
            registry.commit_refresh(prepared)
        except Exception:
            try:
                registry.restore_state(snapshot)
            except Exception as rollback_exc:
                _L.error(
                    "command registry bind rollback failed | error={}",
                    rollback_exc,
                )
            raise
        if not any(bound is registry for bound in self._command_registries):
            self._command_registries.append(registry)

    def _refresh_command_registries(self) -> None:
        registries = tuple(self._command_registries)
        if not registries:
            return
        snapshots: list[tuple[Any, Any]] = []
        prepared: list[tuple[Any, Any]] = []
        try:
            for registry in registries:
                self._validate_command_registry(registry)
                snapshots.append((registry, registry.snapshot_state()))
                prepared.append((registry, registry.prepare_refresh()))
            for registry, candidate in prepared:
                registry.commit_refresh(candidate)
        except Exception:
            try:
                self._restore_command_registry_states(tuple(snapshots))
            except Exception as rollback_exc:
                _L.error(
                    "command registry transaction rollback failed | error={}",
                    rollback_exc,
                )
            raise

    @staticmethod
    def _validate_command_registry(registry: Any) -> None:
        required_methods = (
            "prepare_refresh",
            "commit_refresh",
            "snapshot_state",
            "restore_state",
        )
        missing = [
            method_name
            for method_name in required_methods
            if not callable(getattr(registry, method_name, None))
        ]
        if missing:
            raise TypeError(
                "command registry must provide transactional methods: "
                + ", ".join(missing)
            )

    def _snapshot_command_registry_states(self) -> tuple[tuple[Any, Any], ...]:
        snapshots: list[tuple[Any, Any]] = []
        for registry in tuple(self._command_registries):
            self._validate_command_registry(registry)
            snapshots.append((registry, registry.snapshot_state()))
        return tuple(snapshots)

    def _restore_command_registry_states(self, raw_snapshots: Any) -> None:
        if raw_snapshots is None:
            return
        if not isinstance(raw_snapshots, tuple):
            raise ValueError("command registry runtime snapshot is invalid")
        current_registries = tuple(self._command_registries)
        errors: list[Exception] = []
        for item in raw_snapshots:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("command registry runtime snapshot item is invalid")
            registry, registry_snapshot = item
            if not any(current is registry for current in current_registries):
                raise ValueError("command registry runtime snapshot owner changed")
            try:
                registry.restore_state(registry_snapshot)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError(
                f"command registry restore failed: {errors[0]}"
            ) from errors[0]

    def command_registry_health(self) -> dict[str, Any]:
        """Aggregate diagnostics from bound command registries."""
        snapshots: list[dict[str, Any]] = []
        for registry in tuple(self._command_registries):
            health_snapshot = getattr(registry, "health_snapshot", None)
            if not callable(health_snapshot):
                continue
            try:
                raw_snapshot = health_snapshot()
            except Exception as exc:
                snapshots.append({
                    "status": "error",
                    "failed_refreshes": 1,
                    "last_error": f"command registry health failed: {exc}",
                })
                continue
            if isinstance(raw_snapshot, dict):
                snapshots.append(dict(raw_snapshot))

        failed_refreshes = sum(
            int(snapshot.get("failed_refreshes", 0) or 0)
            for snapshot in snapshots
        )
        last_error = next(
            (
                str(snapshot.get("last_error", "") or "")
                for snapshot in reversed(snapshots)
                if snapshot.get("last_error")
            ),
            "",
        )
        return {
            "status": (
                "error"
                if any(snapshot.get("status") == "error" for snapshot in snapshots)
                else "ok" if snapshots else "unknown"
            ),
            "registry_count": len(snapshots),
            "failed_refreshes": failed_refreshes,
            "last_error": last_error,
            "registries": snapshots,
        }

    def plugin_health(self) -> list[dict[str, Any]]:
        """Return a serializable health snapshot for Admin/API consumers."""
        snapshots: list[dict[str, Any]] = []
        for plugin in self._plugins:
            base = self._ensure_health(plugin.name)
            cooldown_remaining = self._refresh_health_state(base, plugin.enabled)
            state = "disabled" if not plugin.enabled else str(base.get("state", "healthy") or "healthy")
            display_state, display_label, display_type = self._health_display(state)
            snapshots.append({
                "name": plugin.name,
                "enabled": plugin.enabled,
                "state": state,
                "display_state": display_state,
                "display_label": display_label,
                "display_type": display_type,
                "calls": base.get("calls", 0),
                "errors": base.get("errors", 0),
                "last_error": base.get("last_error", ""),
                "last_hook": base.get("last_hook", ""),
                "last_called_at": base.get("last_called_at", 0.0),
                "last_elapsed_ms": base.get("last_elapsed_ms", 0.0),
                "max_elapsed_ms": base.get("max_elapsed_ms", 0.0),
                "hook_budget_ms": getattr(plugin, "hook_budget_ms", 5000),
                "hook_budgets_ms": dict(getattr(plugin, "hook_budgets_ms", {}) or {}),
                "slow_calls": base.get("slow_calls", 0),
                "last_slow_hook": base.get("last_slow_hook", ""),
                "timeout_calls": base.get("timeout_calls", 0),
                "last_timeout_hook": base.get("last_timeout_hook", ""),
                "dependency_blocked": base.get("dependency_blocked", False),
                "dependency_errors": list(base.get("dependency_errors", [])),
                "optional_dependency_degraded": base.get(
                    "optional_dependency_degraded",
                    False,
                ),
                "optional_dependency_errors": list(
                    base.get("optional_dependency_errors", [])
                ),
                "startup_failed": base.get("startup_failed", False),
                "permission_denials": base.get("permission_denials", 0),
                "last_permission_denied": base.get("last_permission_denied", ""),
                "last_permission_denied_hook": base.get("last_permission_denied_hook", ""),
                "permission_denials_by_hook": {
                    str(permission): {
                        str(hook): int(count or 0)
                        for hook, count in hooks.items()
                    }
                    for permission, hooks in (
                        base.get("permission_denials_by_hook", {}) or {}
                    ).items()
                    if isinstance(hooks, dict)
                },
                "suppressed_calls": base.get("suppressed_calls", 0),
                "last_suppressed_hook": base.get("last_suppressed_hook", ""),
                "last_suppressed_at": base.get("last_suppressed_at", 0.0),
                "cooldown_reason": base.get("cooldown_reason", ""),
                "cooldown_until": base.get("cooldown_until", 0.0) if cooldown_remaining > 0 else 0.0,
                "cooldown_remaining_seconds": round(cooldown_remaining, 2),
                "cooldown_count": base.get("cooldown_count", 0),
                "error_burst_count": base.get("error_burst_count", 0),
                "slow_burst_count": base.get("slow_burst_count", 0),
                "hooks": dict(base.get("hooks", {})),
            })
        return snapshots

    @staticmethod
    def _health_display(state: str) -> tuple[str, str, str]:
        """Map internal health states to owner-friendly labels."""
        if state == "disabled":
            return "disabled", "已停用", "default"
        if state == "healthy":
            return "healthy", "健康", "success"
        if state == "permission_limited":
            return "permission_limited", "按权限运行", "info"
        if state == "throttled":
            return "throttled", "已保护", "warning"
        if state == "degraded":
            return "degraded", "需关注", "warning"
        return state or "unknown", "状态未知", "error"

    # ---- 生命周期调度 ----

    async def fire_on_startup(self, ctx: PluginContext) -> None:
        """按依赖拓扑顺序调用所有插件的 on_startup。

        依赖解析失败时回退到 priority 排序。
        restart_required/locked 的禁用插件跳过；runtime 插件执行禁用态预初始化。
        """
        self._started = True
        self._startup_succeeded.clear()
        order = self._resolve_dependencies(include_startup_state=False)
        for p in order:
            health = self._ensure_health(p.name)
            warm_disabled_runtime = (
                not p.enabled
                and str(getattr(p, "toggle_policy", "runtime") or "runtime") == "runtime"
                and not bool(health.get("dependency_blocked", False))
            )
            if not p.enabled and not warm_disabled_runtime:
                _L.info("plugin startup skipped (disabled) | name={}", p.name)
                continue
            failed_dependencies = [
                name
                for name in self._required_dependencies(p)
                if name not in self._startup_succeeded
            ]
            if failed_dependencies:
                self._block_plugin_after_startup_dependency_failure(
                    p,
                    failed_dependencies,
                )
                continue
            _L.info("plugin startup | name={} priority={}", p.name, p.priority)
            result = await self._safe_call(
                p,
                p.on_startup(ctx),
                "on_startup",
                allow_disabled=warm_disabled_runtime,
            )
            if result is _HOOK_FAILED:
                self._mark_plugin_startup_failed(p)
                continue
            self._startup_succeeded.add(p.name)
            if warm_disabled_runtime:
                _L.info("runtime plugin warm initialized (disabled) | name={}", p.name)
        self._update_optional_dependency_health(
            {plugin.name: plugin for plugin in self._plugins},
            include_startup_state=True,
        )
        self._refresh_command_registries()
        _L.info("all plugins started | count={}", len(order))

    async def fire_on_shutdown(self, ctx: PluginContext) -> None:
        """按依赖倒序调用 on_shutdown（依赖者先关，被依赖者后关）。"""
        order = self._resolve_dependencies()
        for p in reversed(order):
            health = self._ensure_health(p.name)
            cleanup_required = (
                p.name in self._startup_succeeded
                or bool(health.get("startup_failed", False))
            )
            await self._safe_call(
                p,
                p.on_shutdown(ctx),
                "on_shutdown",
                allow_disabled=cleanup_required,
            )
        _L.info("all plugins shut down | count={}", len(order))

    async def fire_on_bot_connect(self, ctx: PluginContext, bot: Any) -> None:
        """按依赖顺序通知所有插件 bot 已连接。"""
        order = self._resolve_dependencies()
        for p in order:
            if not self._has_permission(p, "lifecycle", surface="on_bot_connect"):
                continue
            await self._safe_call(p, p.on_bot_connect(ctx, bot), "on_bot_connect")
        _L.info("bot connect notified | count={}", len(order))

    # ---- 消息管线调度 ----

    async def fire_on_message(self, ctx: MessageContext, *, silent_mode: bool = False) -> bool:
        """按优先级调用 on_message，直到有插件返回 True 消费消息。

        返回 True 表示消息已被某插件消费，调用方应停止后续处理。

        silent_mode=True 时（presence_mode=silent_learn 群），只调用 silent_safe=True
        的插件。这是 silent_learn 契约的内核统一门控——拦截器默认 silent_safe=False
        以防新插件忘记声明而破坏静默。
        """
        for p in self._plugins:
            if not self._has_permission(p, "message", surface="on_message"):
                continue
            if silent_mode and not getattr(p, "silent_safe", False):
                continue
            consumed = await self._safe_call(p, p.on_message(ctx), "on_message")
            if consumed is True:
                _L.debug("message consumed | plugin={} session={}", p.name, ctx.session_id)
                return True
        return False

    async def fire_on_thinker_decision(self, ctx: ThinkerContext) -> None:
        """通知所有插件 thinker 决策结果。"""
        for p in self._plugins:
            if not self._has_permission(p, "reply", surface="on_thinker_decision"):
                continue
            await self._safe_call(p, p.on_thinker_decision(ctx), "on_thinker_decision")

    async def fire_on_pre_prompt(self, ctx: PromptContext) -> None:
        """按优先级调用 on_pre_prompt，收集所有插件追加的 PromptBlock。

        各插件通过 ctx.add_block() 追加内容，调用方读取 ctx.blocks 使用。
        """
        for p in self._plugins:
            if not self._has_permission(p, "prompt", surface="on_pre_prompt"):
                continue
            await self._safe_call(p, p.on_pre_prompt(ctx), "on_pre_prompt")

    async def fire_on_post_reply(self, ctx: ReplyContext) -> None:
        """按优先级调用 on_post_reply。各插件独立执行副作用。"""
        for p in self._plugins:
            if not self._has_permission(p, "reply", surface="on_post_reply"):
                continue
            await self._safe_call(p, p.on_post_reply(ctx), "on_post_reply")

    # ---- 工具收集 ----

    def collect_tools(self) -> list[Tool]:
        """收集所有插件注册的 Tool 实例。

        在 on_startup 之后调用，将结果传给 ToolRegistry。
        跳过被禁用的插件。
        """
        tools: list[Tool] = []
        for p in self._plugins:
            if not p.enabled:
                continue
            if not self._has_permission(p, "tool", surface="register_tools"):
                continue
            try:
                plugin_tools = p.register_tools()
                tools.extend(plugin_tools)
                if plugin_tools:
                    _L.debug("tools registered | plugin={} count={}", p.name, len(plugin_tools))
            except Exception as exc:
                _L.error("collect_tools failed | plugin={}", p.name, exc_info=True)
                raise RuntimeError(
                    f"collect_tools failed for plugin {p.name}: {exc}"
                ) from exc
        return tools

    # ---- 命令与 Admin 路由收集 ----

    def collect_command_bindings(self) -> list[tuple[AmadeusPlugin, Command]]:
        """Collect enabled commands together with their runtime owner."""
        bindings: list[tuple[AmadeusPlugin, Command]] = []
        for p in self._plugins:
            if not p.enabled:
                continue
            if not self._has_permission(p, "command", surface="register_commands"):
                continue
            try:
                bindings.extend((p, command) for command in p.register_commands())
            except Exception as exc:
                _L.error("collect_commands failed | plugin={}", p.name, exc_info=True)
                raise RuntimeError(
                    f"collect_commands failed for plugin {p.name}: {exc}"
                ) from exc
        return bindings

    def collect_commands(self) -> list[Command]:
        """收集所有插件注册的文本命令。"""
        return [command for _, command in self.collect_command_bindings()]

    def collect_admin_routes(self) -> list[AdminRoute]:
        """收集所有插件注册的 Admin Panel HTTP 路由。"""
        routes: list[AdminRoute] = []
        for p in self._plugins:
            if not p.enabled:
                continue
            if not self._has_permission(p, "admin", surface="register_admin_routes"):
                continue
            try:
                routes.extend(p.register_admin_routes())
            except Exception:
                _L.warning("collect_admin_routes failed | plugin={}", p.name, exc_info=True)
        return routes

    # ---- 定时调度 ----

    async def fire_on_tick(self, ctx: PluginContext) -> None:
        """按优先级调用 on_tick。"""
        for p in self._plugins:
            if not self._has_permission(p, "tick", surface="on_tick"):
                continue
            await self._safe_call(p, p.on_tick(ctx), "on_tick")

    def start_tick_loop(self, ctx: PluginContext, interval: float = 60.0) -> None:
        """启动后台 tick 循环，约每 interval 秒调用一次 fire_on_tick。

        幂等：若已有循环在运行则直接返回。
        """
        if self._tick_task is not None:
            return

        async def _loop() -> None:
            while True:
                await asyncio.sleep(interval)
                try:
                    await self.fire_on_tick(ctx)
                except Exception:
                    _L.warning("tick loop error", exc_info=True)

        if self._task_supervisor is None:
            self._tick_task = asyncio.create_task(_loop())
        else:
            self._tick_task = self._task_supervisor.spawn(
                TaskSpec(
                    name="plugin_bus.tick",
                    owner="kernel.plugin_bus",
                    kind=TaskKind.PERIODIC,
                    restart=RestartPolicy.ON_FAILURE,
                    shutdown=ShutdownPolicy.CANCEL,
                    max_restarts=3,
                    backoff_seconds=1.0,
                    max_backoff_seconds=30.0,
                ),
                _loop,
            )
        _L.info("tick loop started | interval={:.0f}s", interval)

    async def stop_tick_loop(self) -> None:
        """停止后台 tick 循环。"""
        if self._tick_task is None:
            return
        if self._task_supervisor is not None:
            await self._task_supervisor.stop_owner("kernel.plugin_bus")
        elif not self._tick_task.done():
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick_task
        self._tick_task = None
        _L.info("tick loop stopped")

    # ---- 发现 ----

    def discover_plugins(self, directory: str | Path) -> int:
        """扫描目录，自动发现并注册插件。

        发现规则：
        1. 子目录包含 plugin.py → 导入并实例化 AmadeusPlugin 子类
        2. 子目录包含 plugin.json → 解析并用字段覆盖实例属性

        返回新注册的插件数量。
        """
        dir_path = Path(directory)
        if not dir_path.is_dir():
            _L.warning("plugin dir not found | path={}", dir_path)
            return 0

        count = 0

        # Omubot 插件中心只支持目录插件。根目录单文件插件不再运行时装载，
        # 旧文件会由 PluginIndexService 标为 legacy_single_file_unsupported。
        for subdir in sorted(dir_path.iterdir()):
            if not subdir.is_dir():
                continue
            plugin_file = subdir / "plugin.py"
            if not plugin_file.is_file():
                continue

            manifest_path = subdir / "plugin.json"
            if not manifest_path.is_file():
                _L.warning(
                    "directory plugin manifest missing | name={} path={}",
                    subdir.name,
                    manifest_path,
                )
                continue
            try:
                manifest = load_plugin_manifest(
                    manifest_path,
                    expected_name=subdir.name,
                )
            except ValueError as exc:
                _L.warning(
                    "directory plugin manifest invalid | name={} error={}",
                    subdir.name,
                    exc,
                )
                continue
            if not self._manifest_runtime_compatible(manifest):
                _L.warning(
                    "plugin requires newer Omubot | name={} required={} current={}",
                    subdir.name,
                    manifest.min_omubot_version,
                    self._omubot_version,
                )
                continue
            if manifest.capability_only:
                _L.debug("manifest-only capability, skipping PluginBus load | name={}", subdir.name)
                continue

            plugin_name = subdir.name
            if self.get_plugin(plugin_name) is not None:
                _L.debug("plugin already registered, skipping | name={}", plugin_name)
                continue

            try:
                instance = self._load_plugin_module(plugin_name, plugin_file)
                if instance is not None:
                    self.register(instance, manifest=manifest)
                    count += 1
            except Exception:
                _L.warning(
                    "plugin discovery failed | name={}", plugin_name, exc_info=True
                )

        if count:
            _L.info("plugins discovered | dir={} count={}", dir_path, count)
        return count

    def _load_plugin_module(
        self,
        plugin_name: str,
        plugin_file: Path,
    ) -> AmadeusPlugin | None:
        """导入单个插件模块并实例化 AmadeusPlugin 子类。

        Manifest overlay 由 register() 使用 discovery 的 typed snapshot 完成。
        """
        import importlib.util
        import sys

        module_name = f"_omubot_plugin_{plugin_name}"
        spec = importlib.util.spec_from_file_location(module_name, plugin_file)
        if spec is None or spec.loader is None:
            _L.warning("plugin spec failed | name={}", plugin_name)
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, AmadeusPlugin)
                and attr is not AmadeusPlugin
            ):
                instance = attr()

                _L.debug("plugin loaded | name={} version={}", instance.name, instance.version)
                return instance

        _L.warning("no AmadeusPlugin subclass found in plugin | name={}", plugin_name)
        return None

    @staticmethod
    def _apply_manifest(instance: AmadeusPlugin, data: dict[str, Any]) -> None:
        """用 plugin.json 数据覆盖插件实例属性。"""
        for key in (
            "name",
            "version",
            "description",
            "priority",
            "enabled",
            "author",
            "category",
            "permissions",
            "settings_schema",
            "capabilities",
            "min_omubot_version",
            "hook_budget_ms",
            "hook_budgets_ms",
            "display_name",
            "tier",
            "toggle_policy",
            "store",
        ):
            if key in data:
                setattr(instance, key, data[key])
        if "config" in data and isinstance(data["config"], dict):
            instance.config_spec = data["config"]
        if "dependencies" in data and isinstance(data["dependencies"], dict):
            instance.dependencies = data["dependencies"]
        for key in ("required_dependencies", "optional_dependencies"):
            if key in data and isinstance(data[key], dict):
                setattr(instance, key, data[key])
        PluginBus._normalize_plugin_lock_policy(instance)

    def _apply_local_manifest(self, instance: AmadeusPlugin) -> None:
        """Apply sidecar manifest for explicitly registered plugins."""
        try:
            module = __import__(instance.__class__.__module__, fromlist=["__name__"])
            raw_module_file = str(getattr(module, "__file__", "") or "")
        except Exception:
            return
        if not raw_module_file:
            return
        module_file = Path(raw_module_file)
        directory_plugin = module_file.name in {"plugin.py", "__init__.py"}
        manifest_path = (
            module_file.parent / "plugin.json"
            if directory_plugin
            else (
                module_file.with_suffix(".json")
            )
        )
        if not manifest_path.is_file():
            if directory_plugin:
                raise ValueError(
                    f"directory plugin manifest missing: {manifest_path}"
                )
            self._normalize_plugin_lock_policy(instance)
            return
        try:
            manifest = load_plugin_manifest(
                manifest_path,
                expected_name=module_file.parent.name if directory_plugin else None,
            )
        except Exception as exc:
            raise ValueError(
                f"invalid plugin manifest during register: {manifest_path}: {exc}"
            ) from exc
        if not self._manifest_runtime_compatible(manifest):
            raise ValueError(
                "plugin requires newer Omubot: "
                f"plugin={manifest.name} required={manifest.min_omubot_version} "
                f"current={self._omubot_version}"
            )
        self._apply_manifest(
            instance,
            manifest.model_dump(by_alias=True, exclude_none=True),
        )
        _L.debug("plugin manifest applied during register | name={}", instance.name)
        self._normalize_plugin_lock_policy(instance)

    def _manifest_runtime_compatible(self, manifest: PluginManifestV3) -> bool:
        minimum = manifest.min_omubot_version
        return not minimum or check_version(self._omubot_version, minimum)

    @classmethod
    def _normalize_tier(cls, name: str, tier: str) -> PluginTier:
        if name in cls._SYSTEM_PLUGIN_WHITELIST:
            return "system"
        return "user"

    @classmethod
    def _normalize_toggle_policy(
        cls,
        name: str,
        toggle_policy: str,
        tier: PluginTier,
    ) -> PluginTogglePolicy:
        if name in cls._SYSTEM_PLUGIN_WHITELIST or tier == "system":
            return "locked"
        if toggle_policy == "restart_required":
            return "restart_required"
        return "runtime"

    @classmethod
    def _normalize_plugin_lock_policy(cls, plugin: AmadeusPlugin | None) -> None:
        if plugin is None:
            return
        name = str(getattr(plugin, "name", "") or "")
        raw_tier = str(getattr(plugin, "tier", "user") or "user")
        raw_policy = str(getattr(plugin, "toggle_policy", "runtime") or "runtime")
        normalized_tier = cls._normalize_tier(name, raw_tier)
        normalized_policy = cls._normalize_toggle_policy(name, raw_policy, normalized_tier)

        if raw_tier != normalized_tier:
            if raw_tier == "system" and name not in cls._SYSTEM_PLUGIN_WHITELIST:
                _L.warning(
                    "system tier downgraded by whitelist | name={} tier={} allowed={}",
                    name, raw_tier, sorted(cls._SYSTEM_PLUGIN_WHITELIST),
                )
            plugin.tier = normalized_tier
        else:
            plugin.tier = normalized_tier

        if raw_policy != normalized_policy:
            if raw_policy == "locked" and name not in cls._SYSTEM_PLUGIN_WHITELIST:
                _L.warning(
                    "locked policy downgraded by whitelist | name={} policy={} allowed={}",
                    name, raw_policy, sorted(cls._SYSTEM_PLUGIN_WHITELIST),
                )
            plugin.toggle_policy = normalized_policy
        else:
            plugin.toggle_policy = normalized_policy

    # ---- 内部 ----

    def _resolve_dependencies(
        self,
        *,
        include_startup_state: bool | None = None,
    ) -> list[AmadeusPlugin]:
        """用 Kahn 算法拓扑排序插件依赖图。

        required 依赖不存在、禁用或版本不兼容时，依赖方 fail-closed。
        optional 依赖不可用时只跳过该依赖边。
        若存在循环依赖，降级为 warning 并回退到 priority 排序。

        返回拓扑排序后的插件列表。
        """
        from kernel.manifest import check_version

        name_to_plugin: dict[str, AmadeusPlugin] = {p.name: p for p in self._plugins}
        if len(name_to_plugin) != len(self._plugins):
            _L.warning("duplicate plugin names bypass dependency ordering")
            return sorted(self._plugins, key=lambda plugin: plugin.priority)

        if include_startup_state is None:
            include_startup_state = self._started and bool(self._startup_succeeded)

        self._restore_dependency_recovered_plugins(name_to_plugin)
        blocked: dict[str, list[str]] = {}
        self._propagate_required_dependency_blocks(name_to_plugin, blocked)

        cycle_nodes = self._required_cycle_nodes(name_to_plugin, blocked)
        if cycle_nodes:
            cycle_description = " -> ".join(sorted(cycle_nodes))
            for name in cycle_nodes:
                blocked[name] = [f"required dependency cycle: {cycle_description}"]
            self._propagate_required_dependency_blocks(name_to_plugin, blocked)

        for plugin in self._plugins:
            health = self._ensure_health(plugin.name)
            errors = blocked.get(plugin.name, [])
            health["dependency_blocked"] = bool(errors)
            health["dependency_errors"] = list(errors)
            if not errors:
                if (
                    health.get("dependency_auto_disabled", False)
                    and self._started
                    and plugin.name not in self._startup_succeeded
                ):
                    health["dependency_auto_disabled"] = False
                continue
            if plugin.enabled:
                health["dependency_auto_disabled"] = True
            plugin.enabled = False
            health["enabled"] = False
            self._refresh_health_state(health, enabled=False)
            for error in errors:
                _L.error("plugin dependency blocked | plugin={} error={}", plugin.name, error)

        # 构建邻接表和入度，同时校验依赖
        edges: dict[str, list[str]] = {p.name: [] for p in self._plugins}
        in_degree: dict[str, int] = {p.name: 0 for p in self._plugins}

        for p in self._plugins:
            for dep_name, version_constraint in self._required_dependencies(p).items():
                dep = name_to_plugin.get(dep_name)
                if dep is None:
                    _L.warning(
                        "dependency not found | plugin={} dependency={}",
                        p.name, dep_name,
                    )
                    continue
                if not dep.enabled:
                    _L.warning(
                        "dependency disabled | plugin={} dependency={}",
                        p.name, dep_name,
                    )
                    continue
                if not check_version(dep.version, version_constraint):
                    _L.warning(
                        "dependency version mismatch | plugin={} dependency={} "
                        "required={} actual={}",
                        p.name, dep_name, version_constraint, dep.version,
                    )
                    continue
                edges[dep_name].append(p.name)
                in_degree[p.name] += 1

        for p in self._plugins:
            for dep_name, version_constraint in self._optional_dependencies(p).items():
                dep = name_to_plugin.get(dep_name)
                if dep is None or not dep.enabled:
                    continue
                if not check_version(dep.version, version_constraint):
                    continue
                if self._would_create_dependency_cycle(edges, dep_name, p.name):
                    _L.warning(
                        "optional dependency cycle ignored | plugin={} dependency={}",
                        p.name,
                        dep_name,
                    )
                    continue
                edges[dep_name].append(p.name)
                in_degree[p.name] += 1

        # Kahn 拓扑排序
        queue: list[str] = [name for name, deg in in_degree.items() if deg == 0]
        sorted_names: list[str] = []

        while queue:
            # 按 priority 排序保证同层稳定性
            queue.sort(key=lambda n: name_to_plugin[n].priority)
            node = queue.pop(0)
            sorted_names.append(node)
            for dependent in edges.get(node, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(sorted_names) != len(self._plugins):
            cycle_plugins = set(p.name for p in self._plugins) - set(sorted_names)
            raise RuntimeError(
                "dependency resolver invariant violated: "
                f"cycle remained after validation: {sorted(cycle_plugins)}"
            )

        self._update_optional_dependency_health(
            name_to_plugin,
            include_startup_state=include_startup_state,
        )
        return [name_to_plugin[n] for n in sorted_names]

    def _update_optional_dependency_health(
        self,
        name_to_plugin: dict[str, AmadeusPlugin],
        *,
        include_startup_state: bool,
    ) -> None:
        from kernel.manifest import check_version

        for plugin in self._plugins:
            errors: list[str] = []
            for dependency_name, constraint in self._optional_dependencies(plugin).items():
                dependency = name_to_plugin.get(dependency_name)
                if dependency is None:
                    errors.append(
                        f"optional dependency not found: {dependency_name}"
                    )
                    continue
                dependency_health = self._ensure_health(dependency_name)
                if dependency_health.get("startup_failed", False):
                    errors.append(
                        f"optional dependency startup failed: {dependency_name}"
                    )
                elif not dependency.enabled:
                    errors.append(
                        f"optional dependency disabled: {dependency_name}"
                    )
                elif not check_version(dependency.version, constraint):
                    errors.append(
                        "optional dependency version mismatch: "
                        f"{dependency_name} required={constraint} "
                        f"actual={dependency.version}"
                    )
                elif (
                    include_startup_state
                    and dependency_name not in self._startup_succeeded
                ):
                    errors.append(
                        f"optional dependency startup unavailable: {dependency_name}"
                    )

            health = self._ensure_health(plugin.name)
            health["optional_dependency_degraded"] = bool(errors)
            health["optional_dependency_errors"] = errors
            self._refresh_health_state(health, enabled=plugin.enabled)

    @staticmethod
    def _would_create_dependency_cycle(
        edges: dict[str, list[str]],
        dependency: str,
        plugin: str,
    ) -> bool:
        if dependency == plugin:
            return True
        pending = [plugin]
        visited: set[str] = set()
        while pending:
            node = pending.pop()
            if node == dependency:
                return True
            if node in visited:
                continue
            visited.add(node)
            pending.extend(edges.get(node, []))
        return False

    def _propagate_required_dependency_blocks(
        self,
        name_to_plugin: dict[str, AmadeusPlugin],
        blocked: dict[str, list[str]],
    ) -> None:
        from kernel.manifest import check_version

        changed = True
        while changed:
            changed = False
            for plugin in self._plugins:
                if plugin.name in blocked:
                    continue
                errors: list[str] = []
                for dep_name, version_constraint in self._required_dependencies(plugin).items():
                    dependency = name_to_plugin.get(dep_name)
                    if dependency is None:
                        errors.append(f"required dependency not found: {dep_name}")
                    elif dep_name in blocked or not dependency.enabled:
                        errors.append(f"required dependency disabled: {dep_name}")
                    elif not check_version(dependency.version, version_constraint):
                        errors.append(
                            "required dependency version mismatch: "
                            f"{dep_name} required={version_constraint} actual={dependency.version}"
                        )
                if errors:
                    blocked[plugin.name] = errors
                    changed = True

    def _restore_dependency_recovered_plugins(
        self,
        name_to_plugin: dict[str, AmadeusPlugin],
    ) -> None:
        from kernel.manifest import check_version

        changed = True
        while changed:
            changed = False
            for plugin in self._plugins:
                health = self._ensure_health(plugin.name)
                if not health.get("dependency_auto_disabled", False):
                    continue
                if self._started and plugin.name not in self._startup_succeeded:
                    continue
                dependencies_ready = all(
                    (dependency := name_to_plugin.get(dependency_name)) is not None
                    and dependency.enabled
                    and check_version(dependency.version, constraint)
                    for dependency_name, constraint in self._required_dependencies(plugin).items()
                )
                if not dependencies_ready:
                    continue
                plugin.enabled = True
                health["enabled"] = True
                health["dependency_auto_disabled"] = False
                health["dependency_blocked"] = False
                health["dependency_errors"] = []
                self._refresh_health_state(health, enabled=True)
                _L.info("plugin dependency recovered | plugin={}", plugin.name)
                changed = True

    def _required_cycle_nodes(
        self,
        name_to_plugin: dict[str, AmadeusPlugin],
        blocked: dict[str, list[str]],
    ) -> set[str]:
        from kernel.manifest import check_version

        graph: dict[str, list[str]] = {}
        for plugin in self._plugins:
            if plugin.name in blocked or not plugin.enabled:
                continue
            graph[plugin.name] = [
                dep_name
                for dep_name, constraint in self._required_dependencies(plugin).items()
                if dep_name not in blocked
                and (dependency := name_to_plugin.get(dep_name)) is not None
                and dependency.enabled
                and check_version(dependency.version, constraint)
            ]

        state: dict[str, int] = {}
        stack: list[str] = []
        stack_index: dict[str, int] = {}
        cycle_nodes: set[str] = set()

        def visit(node: str) -> None:
            state[node] = 1
            stack_index[node] = len(stack)
            stack.append(node)
            for dependency in graph.get(node, []):
                dependency_state = state.get(dependency, 0)
                if dependency_state == 0:
                    visit(dependency)
                elif dependency_state == 1:
                    cycle_nodes.update(stack[stack_index[dependency]:])
            stack.pop()
            stack_index.pop(node, None)
            state[node] = 2

        for node in graph:
            if state.get(node, 0) == 0:
                visit(node)
        return cycle_nodes

    @staticmethod
    def _dependency_map(plugin: AmadeusPlugin, attribute: str) -> dict[str, str]:
        raw_dependencies = getattr(plugin, attribute, {})
        if not isinstance(raw_dependencies, dict):
            return {}
        return {
            str(name): str(constraint)
            for name, constraint in raw_dependencies.items()
            if str(name)
        }

    @classmethod
    def _required_dependencies(cls, plugin: AmadeusPlugin) -> dict[str, str]:
        dependencies = cls._dependency_map(plugin, "dependencies")
        dependencies.update(cls._dependency_map(plugin, "required_dependencies"))
        return dependencies

    @classmethod
    def _optional_dependencies(cls, plugin: AmadeusPlugin) -> dict[str, str]:
        optional = cls._dependency_map(plugin, "optional_dependencies")
        for name in cls._required_dependencies(plugin):
            optional.pop(name, None)
        return optional

    async def _safe_call(
        self,
        plugin: AmadeusPlugin,
        coro: Awaitable[Any],
        hook_name: str,
        *,
        allow_disabled: bool = False,
    ) -> Any:
        """安全调用插件钩子。异常隔离：单个插件失败不影响其他插件。

        超过 100ms 打 debug 日志，超过 5s 打 warning。
        被禁用的插件默认跳过；生命周期清理和 runtime 预初始化可显式放行。
        """
        if not plugin.enabled and not allow_disabled:
            close = getattr(coro, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
            return None

        health = self._ensure_health(plugin.name)
        if self._should_suppress_hook(plugin, health, hook_name):
            self._record_suppressed_call(health, hook_name)
            close = getattr(coro, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
            _L.debug(
                "hook suppressed by cooldown | plugin={} hook={} remaining={:.1f}s",
                plugin.name,
                hook_name,
                self._cooldown_remaining_seconds(health),
            )
            return None

        t0 = time.perf_counter()
        budget_ms = self._hook_budget_ms(plugin, hook_name)
        try:
            result = await self._await_hook(coro, budget_ms=budget_ms)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self._record_hook_health(health, hook_name, elapsed_ms, error=None)
            if budget_ms is not None and elapsed_ms > budget_ms:
                self._record_hook_slow(health, hook_name, elapsed_ms, budget_ms)
                slow_burst_count = self._record_burst_event(
                    health,
                    bucket="slow_events",
                    now=time.time(),
                    window_seconds=self._BURST_WINDOW_SECONDS,
                )
                health["slow_burst_count"] = slow_burst_count
                if hook_name in self._SOFT_ISOLATION_HOOKS and slow_burst_count >= self._SLOW_BURST_LIMIT:
                    self._enter_soft_isolation(
                        plugin,
                        health,
                        reason="slow_burst",
                        burst_count=slow_burst_count,
                    )
                _L.warning(
                    "hook budget exceeded | plugin={} hook={} elapsed={:.0f}ms budget={}ms",
                    plugin.name, hook_name, elapsed_ms, budget_ms,
                )
            elif elapsed_ms > 100:
                _L.debug(
                    "hook slow | plugin={} hook={} elapsed={:.0f}ms",
                    plugin.name, hook_name, elapsed_ms,
                )
            self._refresh_health_state(health, plugin.enabled)
            return result
        except _HookDeadlineExceeded:
            assert budget_ms is not None
            elapsed_ms = (time.perf_counter() - t0) * 1000
            error = f"hook timeout after {budget_ms}ms"
            self._record_hook_health(health, hook_name, elapsed_ms, error=error)
            self._record_hook_slow(health, hook_name, elapsed_ms, budget_ms)
            self._record_hook_timeout(health, hook_name, budget_ms)
            slow_burst_count = self._record_burst_event(
                health,
                bucket="slow_events",
                now=time.time(),
                window_seconds=self._BURST_WINDOW_SECONDS,
            )
            health["slow_burst_count"] = slow_burst_count
            if hook_name in self._SOFT_ISOLATION_HOOKS and slow_burst_count >= self._SLOW_BURST_LIMIT:
                self._enter_soft_isolation(
                    plugin,
                    health,
                    reason="slow_burst",
                    burst_count=slow_burst_count,
                )
            self._refresh_health_state(health, plugin.enabled)
            _L.warning(
                "hook timeout | plugin={} hook={} elapsed={:.0f}ms budget={}ms",
                plugin.name,
                hook_name,
                elapsed_ms,
                budget_ms,
            )
            return _HOOK_FAILED
        except Exception as exc:
            self._record_hook_error(plugin, health, hook_name, t0, exc)
            return _HOOK_FAILED

    async def _await_hook(
        self,
        coro: Awaitable[Any],
        *,
        budget_ms: int | None,
    ) -> Any:
        if budget_ms is None:
            return await coro

        task = asyncio.ensure_future(coro)
        try:
            done, _pending = await asyncio.wait({task}, timeout=budget_ms / 1000)
        except asyncio.CancelledError:
            self._cancel_uncooperative_task(task)
            raise
        if task in done:
            return task.result()

        self._cancel_uncooperative_task(task)
        await asyncio.sleep(0)
        raise _HookDeadlineExceeded

    @staticmethod
    def _cancel_uncooperative_task(task: asyncio.Future[Any]) -> None:
        task.add_done_callback(PluginBus._consume_task_result)
        task.cancel()
        loop = task.get_loop()

        def cancel_again(remaining: int) -> None:
            if task.done():
                return
            task.cancel()
            if remaining > 0:
                loop.call_later(0.01, cancel_again, remaining - 1)
            else:
                _L.error("hook task ignored repeated cancellation")

        loop.call_later(0.01, cancel_again, 7)

    @staticmethod
    def _consume_task_result(task: asyncio.Future[Any]) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            task.result()

    def _ensure_health(self, plugin_name: str) -> dict[str, Any]:
        return self._health.setdefault(plugin_name, {
            "state": "healthy",
            "enabled": True,
            "calls": 0,
            "errors": 0,
            "last_error": "",
            "last_hook": "",
            "last_called_at": 0.0,
            "last_elapsed_ms": 0.0,
            "max_elapsed_ms": 0.0,
            "slow_calls": 0,
            "last_slow_hook": "",
            "timeout_calls": 0,
            "last_timeout_hook": "",
            "dependency_blocked": False,
            "dependency_errors": [],
            "dependency_auto_disabled": False,
            "optional_dependency_degraded": False,
            "optional_dependency_errors": [],
            "startup_failed": False,
            "permission_denials": 0,
            "last_permission_denied": "",
            "last_permission_denied_hook": "",
            "permission_denials_by_hook": {},
            "suppressed_calls": 0,
            "last_suppressed_hook": "",
            "last_suppressed_at": 0.0,
            "cooldown_reason": "",
            "cooldown_until": 0.0,
            "cooldown_until_monotonic": 0.0,
            "cooldown_count": 0,
            "cooldown_triggered_at": 0.0,
            "error_burst_count": 0,
            "slow_burst_count": 0,
            "error_events": deque(),
            "slow_events": deque(),
            "hooks": {},
        })

    def _has_permission(
        self,
        plugin: AmadeusPlugin,
        permission: str,
        *,
        surface: str,
    ) -> bool:
        """Check manifest v2 permissions while keeping legacy plugins compatible."""
        if not plugin.enabled:
            return True
        if permission == "lifecycle":
            return True
        permissions = list(getattr(plugin, "permissions", []) or [])
        if not permissions:
            return True
        if permission in permissions:
            return True

        health = self._ensure_health(plugin.name)
        health["permission_denials"] = int(health.get("permission_denials", 0)) + 1
        health["last_permission_denied"] = permission
        health["last_permission_denied_hook"] = surface
        raw_buckets = health.get("permission_denials_by_hook")
        buckets: dict[str, dict[str, int]] = raw_buckets if isinstance(raw_buckets, dict) else {}
        raw_permission_bucket = buckets.get(permission)
        permission_bucket: dict[str, int] = (
            raw_permission_bucket if isinstance(raw_permission_bucket, dict) else {}
        )
        permission_bucket[surface] = int(permission_bucket.get(surface, 0)) + 1
        buckets[permission] = permission_bucket
        health["permission_denials_by_hook"] = buckets
        self._refresh_health_state(health, plugin.enabled)
        _L.debug(
            "plugin permission denied | plugin={} permission={} surface={}",
            plugin.name, permission, surface,
        )
        return False

    @classmethod
    def _hook_budget_ms(
        cls,
        plugin: AmadeusPlugin,
        hook_name: str,
    ) -> int | None:
        per_hook = getattr(plugin, "hook_budgets_ms", {})
        if isinstance(per_hook, dict) and hook_name in per_hook:
            return cls._positive_budget_ms(per_hook[hook_name], default=None)
        if hook_name in cls._LIFECYCLE_HOOKS:
            return None
        return cls._positive_budget_ms(getattr(plugin, "hook_budget_ms", 5000), default=5000)

    @staticmethod
    def _positive_budget_ms(raw_budget: Any, *, default: int | None) -> int | None:
        try:
            budget = int(raw_budget)
        except (TypeError, ValueError):
            return default
        return budget if budget > 0 else default

    def _record_hook_error(
        self,
        plugin: AmadeusPlugin,
        health: dict[str, Any],
        hook_name: str,
        started_at: float,
        exc: Exception,
    ) -> None:
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        self._record_hook_health(health, hook_name, elapsed_ms, error=str(exc))
        error_burst_count = self._record_burst_event(
            health,
            bucket="error_events",
            now=time.time(),
            window_seconds=self._BURST_WINDOW_SECONDS,
        )
        health["error_burst_count"] = error_burst_count
        if hook_name in self._SOFT_ISOLATION_HOOKS and error_burst_count >= self._ERROR_BURST_LIMIT:
            self._enter_soft_isolation(
                plugin,
                health,
                reason="error_burst",
                burst_count=error_burst_count,
            )
        self._refresh_health_state(health, plugin.enabled)
        _L.warning(
            "hook error | plugin={} hook={} elapsed={:.0f}ms",
            plugin.name,
            hook_name,
            elapsed_ms,
            exc_info=True,
        )
        return None

    def _mark_plugin_startup_failed(self, plugin: AmadeusPlugin) -> None:
        plugin.enabled = False
        health = self._ensure_health(plugin.name)
        health["enabled"] = False
        health["startup_failed"] = True
        self._refresh_health_state(health, enabled=False)
        _L.error("plugin disabled after startup failure | plugin={}", plugin.name)

    def _block_plugin_after_startup_dependency_failure(
        self,
        plugin: AmadeusPlugin,
        failed_dependencies: list[str],
    ) -> None:
        errors = [
            f"required dependency startup failed: {name}"
            for name in failed_dependencies
        ]
        plugin.enabled = False
        health = self._ensure_health(plugin.name)
        health["enabled"] = False
        health["dependency_blocked"] = True
        health["dependency_errors"] = errors
        self._refresh_health_state(health, enabled=False)
        for error in errors:
            _L.error("plugin dependency blocked | plugin={} error={}", plugin.name, error)

    @staticmethod
    def _record_hook_slow(
        health: dict[str, Any],
        hook_name: str,
        elapsed_ms: float,
        budget_ms: int,
    ) -> None:
        health["slow_calls"] = int(health.get("slow_calls", 0)) + 1
        health["last_slow_hook"] = hook_name
        health["state"] = "degraded"

        hooks = health.setdefault("hooks", {})
        hook = hooks.setdefault(hook_name, {
            "calls": 0,
            "errors": 0,
            "last_elapsed_ms": 0.0,
            "max_elapsed_ms": 0.0,
            "slow_calls": 0,
            "suppressed_calls": 0,
            "budget_ms": budget_ms,
        })
        hook["slow_calls"] = int(hook.get("slow_calls", 0)) + 1
        hook["budget_ms"] = budget_ms
        hook["last_over_budget_ms"] = round(max(0.0, elapsed_ms - budget_ms), 2)

    @staticmethod
    def _record_hook_timeout(
        health: dict[str, Any],
        hook_name: str,
        budget_ms: int,
    ) -> None:
        health["timeout_calls"] = int(health.get("timeout_calls", 0)) + 1
        health["last_timeout_hook"] = hook_name
        hooks = health.setdefault("hooks", {})
        hook = hooks.setdefault(hook_name, {})
        hook["timeout_calls"] = int(hook.get("timeout_calls", 0)) + 1
        hook["budget_ms"] = budget_ms

    @staticmethod
    def _record_hook_health(
        health: dict[str, Any],
        hook_name: str,
        elapsed_ms: float,
        *,
        error: str | None,
    ) -> None:
        health["calls"] = int(health.get("calls", 0)) + 1
        health["last_hook"] = hook_name
        health["last_called_at"] = time.time()
        health["last_elapsed_ms"] = round(elapsed_ms, 2)
        health["max_elapsed_ms"] = round(max(float(health.get("max_elapsed_ms", 0.0)), elapsed_ms), 2)
        if error:
            health["errors"] = int(health.get("errors", 0)) + 1
            health["last_error"] = error[:500]
            health["state"] = "degraded"
        else:
            health["state"] = "healthy"

        hooks = health.setdefault("hooks", {})
        hook = hooks.setdefault(hook_name, {
            "calls": 0,
            "errors": 0,
            "last_elapsed_ms": 0.0,
            "max_elapsed_ms": 0.0,
            "suppressed_calls": 0,
        })
        hook["calls"] = int(hook.get("calls", 0)) + 1
        hook["last_elapsed_ms"] = round(elapsed_ms, 2)
        hook["max_elapsed_ms"] = round(max(float(hook.get("max_elapsed_ms", 0.0)), elapsed_ms), 2)
        if error:
            hook["errors"] = int(hook.get("errors", 0)) + 1

    def _should_suppress_hook(
        self,
        plugin: AmadeusPlugin,
        health: dict[str, Any],
        hook_name: str,
    ) -> bool:
        if hook_name not in self._SOFT_ISOLATION_HOOKS:
            return False
        return self._refresh_health_state(health, plugin.enabled) > 0

    def _record_suppressed_call(self, health: dict[str, Any], hook_name: str) -> None:
        health["suppressed_calls"] = int(health.get("suppressed_calls", 0)) + 1
        health["last_suppressed_hook"] = hook_name
        health["last_suppressed_at"] = time.time()
        hooks = health.setdefault("hooks", {})
        hook = hooks.setdefault(hook_name, {
            "calls": 0,
            "errors": 0,
            "last_elapsed_ms": 0.0,
            "max_elapsed_ms": 0.0,
            "suppressed_calls": 0,
        })
        hook["suppressed_calls"] = int(hook.get("suppressed_calls", 0)) + 1

    def _enter_soft_isolation(
        self,
        plugin: AmadeusPlugin,
        health: dict[str, Any],
        *,
        reason: str,
        burst_count: int,
    ) -> None:
        cooldown_seconds = self._SOFT_ISOLATION_COOLDOWN_SECONDS
        health["cooldown_reason"] = reason
        health["cooldown_count"] = int(health.get("cooldown_count", 0)) + 1
        health["cooldown_triggered_at"] = time.time()
        health["cooldown_until"] = health["cooldown_triggered_at"] + cooldown_seconds
        health["cooldown_until_monotonic"] = time.monotonic() + cooldown_seconds
        if reason == "error_burst":
            health["error_burst_count"] = burst_count
        elif reason == "slow_burst":
            health["slow_burst_count"] = burst_count
        self._refresh_health_state(health, plugin.enabled)
        _L.warning(
            "plugin soft isolated | plugin={} reason={} burst={} cooldown={:.0f}s",
            plugin.name,
            reason,
            burst_count,
            cooldown_seconds,
        )

    def _record_burst_event(
        self,
        health: dict[str, Any],
        *,
        bucket: str,
        now: float,
        window_seconds: float,
    ) -> int:
        events = health.get(bucket)
        if not isinstance(events, deque):
            events = deque(events or [])
            health[bucket] = events
        cutoff = now - window_seconds
        while events and events[0] < cutoff:
            events.popleft()
        events.append(now)
        return len(events)

    def _cooldown_remaining_seconds(self, health: dict[str, Any]) -> float:
        remaining = float(health.get("cooldown_until_monotonic", 0.0) or 0.0) - time.monotonic()
        if remaining <= 0 and float(health.get("cooldown_until_monotonic", 0.0) or 0.0) > 0:
            self._clear_cooldown(health)
            return 0.0
        return max(0.0, remaining)

    def _clear_cooldown(self, health: dict[str, Any]) -> None:
        health["cooldown_reason"] = ""
        health["cooldown_until"] = 0.0
        health["cooldown_until_monotonic"] = 0.0

    def _refresh_health_state(self, health: dict[str, Any], enabled: bool) -> float:
        remaining = self._cooldown_remaining_seconds(health)
        if not enabled:
            health["state"] = "disabled"
            return remaining
        if remaining > 0:
            health["state"] = "throttled"
            return remaining
        if int(health.get("errors", 0) or 0) > 0 or int(health.get("slow_calls", 0) or 0) > 0:
            health["state"] = "degraded"
            return remaining
        if bool(health.get("optional_dependency_degraded", False)):
            health["state"] = "degraded"
            return remaining
        if int(health.get("permission_denials", 0) or 0) > 0:
            health["state"] = "permission_limited"
            return remaining
        health["state"] = "healthy"
        return remaining
