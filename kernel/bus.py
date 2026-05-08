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
import json
import time
from collections import deque
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

from loguru import logger

from kernel.types import (
    AdminRoute,
    AmadeusPlugin,
    Command,
    MessageContext,
    PluginContext,
    PromptContext,
    ReplyContext,
    ThinkerContext,
    Tool,
)

_L = logger.bind(channel="bus")


class PluginBus:
    """插件总线。"""

    _SYSTEM_PLUGIN_WHITELIST = frozenset({"chat", "context", "history_loader", "vision"})
    _SOFT_ISOLATION_HOOKS = frozenset({
        "on_message",
        "on_pre_prompt",
        "on_post_reply",
        "on_thinker_decision",
        "on_tick",
    })
    _ERROR_BURST_LIMIT = 3
    _SLOW_BURST_LIMIT = 4
    _BURST_WINDOW_SECONDS = 120.0
    _SOFT_ISOLATION_COOLDOWN_SECONDS = 90.0

    def __init__(self) -> None:
        self._plugins: list[AmadeusPlugin] = []
        self._started: bool = False
        self._tick_task: asyncio.Task[None] | None = None
        self._health: dict[str, dict[str, Any]] = {}

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

    def register(self, plugin: AmadeusPlugin) -> None:
        """注册一个插件。按 priority 升序排列。

        必须在 on_startup 之前调用。相同 priority 保持注册顺序（稳定排序）。
        """
        if self._started:
            raise RuntimeError(
                f"Cannot register plugin '{plugin.name}' after startup. "
                f"Call register() before fire_on_startup()."
            )
        self._apply_local_manifest(plugin)
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
        _L.info("plugin registered | name={} priority={}", plugin.name, plugin.priority)

    def unregister(self, name: str) -> bool:
        """按名称移除插件。返回 True 表示成功移除。"""
        for i, p in enumerate(self._plugins):
            if p.name == name:
                self._plugins.pop(i)
                self._health.pop(name, None)
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
            _L.warning("locked plugin disable refused | name={} tier={} policy={}", name, getattr(plugin, "tier", ""), getattr(plugin, "toggle_policy", ""))
            return False
        plugin.enabled = enabled
        health = self._ensure_health(name)
        health["enabled"] = enabled
        self._clear_cooldown(health)
        self._refresh_health_state(health, enabled)
        _L.info("plugin state changed | name={} enabled={}", name, enabled)
        return True

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
                "slow_calls": base.get("slow_calls", 0),
                "last_slow_hook": base.get("last_slow_hook", ""),
                "permission_denials": base.get("permission_denials", 0),
                "last_permission_denied": base.get("last_permission_denied", ""),
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
        被禁用的插件跳过 on_startup。
        """
        self._started = True
        order = self._resolve_dependencies()
        for p in order:
            if not p.enabled:
                _L.info("plugin startup skipped (disabled) | name={}", p.name)
                continue
            _L.info("plugin startup | name={} priority={}", p.name, p.priority)
            await self._safe_call(p, p.on_startup(ctx), "on_startup")
        _L.info("all plugins started | count={}", len(order))

    async def fire_on_shutdown(self, ctx: PluginContext) -> None:
        """按依赖倒序调用 on_shutdown（依赖者先关，被依赖者后关）。"""
        order = self._resolve_dependencies()
        for p in reversed(order):
            await self._safe_call(p, p.on_shutdown(ctx), "on_shutdown")
        _L.info("all plugins shut down | count={}", len(order))

    async def fire_on_bot_connect(self, ctx: PluginContext, bot: Any) -> None:
        """按依赖顺序通知所有插件 bot 已连接。"""
        order = self._resolve_dependencies()
        for p in order:
            if not self._has_permission(p, "lifecycle"):
                continue
            await self._safe_call(p, p.on_bot_connect(ctx, bot), "on_bot_connect")
        _L.info("bot connect notified | count={}", len(order))

    # ---- 消息管线调度 ----

    async def fire_on_message(self, ctx: MessageContext) -> bool:
        """按优先级调用 on_message，直到有插件返回 True 消费消息。

        返回 True 表示消息已被某插件消费，调用方应停止后续处理。
        """
        for p in self._plugins:
            if not self._has_permission(p, "message"):
                continue
            consumed = await self._safe_call(p, p.on_message(ctx), "on_message")
            if consumed is True:
                _L.debug("message consumed | plugin={} session={}", p.name, ctx.session_id)
                return True
        return False

    async def fire_on_thinker_decision(self, ctx: ThinkerContext) -> None:
        """通知所有插件 thinker 决策结果。"""
        for p in self._plugins:
            if not self._has_permission(p, "reply"):
                continue
            await self._safe_call(p, p.on_thinker_decision(ctx), "on_thinker_decision")

    async def fire_on_pre_prompt(self, ctx: PromptContext) -> None:
        """按优先级调用 on_pre_prompt，收集所有插件追加的 PromptBlock。

        各插件通过 ctx.add_block() 追加内容，调用方读取 ctx.blocks 使用。
        """
        for p in self._plugins:
            if not self._has_permission(p, "prompt"):
                continue
            await self._safe_call(p, p.on_pre_prompt(ctx), "on_pre_prompt")

    async def fire_on_post_reply(self, ctx: ReplyContext) -> None:
        """按优先级调用 on_post_reply。各插件独立执行副作用。"""
        for p in self._plugins:
            if not self._has_permission(p, "reply"):
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
            if not self._has_permission(p, "tool"):
                continue
            try:
                plugin_tools = p.register_tools()
                tools.extend(plugin_tools)
                if plugin_tools:
                    _L.debug("tools registered | plugin={} count={}", p.name, len(plugin_tools))
            except Exception:
                _L.warning("collect_tools failed | plugin={}", p.name, exc_info=True)
        return tools

    # ---- 命令与 Admin 路由收集 ----

    def collect_commands(self) -> list[Command]:
        """收集所有插件注册的文本命令。"""
        commands: list[Command] = []
        for p in self._plugins:
            if not p.enabled:
                continue
            if not self._has_permission(p, "command"):
                continue
            try:
                commands.extend(p.register_commands())
            except Exception:
                _L.warning("collect_commands failed | plugin={}", p.name, exc_info=True)
        return commands

    def collect_admin_routes(self) -> list[AdminRoute]:
        """收集所有插件注册的 Admin Panel HTTP 路由。"""
        routes: list[AdminRoute] = []
        for p in self._plugins:
            if not p.enabled:
                continue
            if not self._has_permission(p, "admin"):
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
            if not self._has_permission(p, "tick"):
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

        self._tick_task = asyncio.create_task(_loop())
        _L.info("tick loop started | interval={:.0f}s", interval)

    async def stop_tick_loop(self) -> None:
        """停止后台 tick 循环。"""
        if self._tick_task is None:
            return
        if not self._tick_task.done():
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

            plugin_name = subdir.name
            if self.get_plugin(plugin_name) is not None:
                _L.debug("plugin already registered, skipping | name={}", plugin_name)
                continue

            try:
                instance = self._load_plugin_module(
                    plugin_name, plugin_file, plugin_dir=subdir
                )
                if instance is not None:
                    self.register(instance)
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
        *,
        plugin_dir: Path | None = None,
        manifest_path: Path | None = None,
    ) -> AmadeusPlugin | None:
        """导入单个插件模块并实例化 AmadeusPlugin 子类。

        若 plugin_dir 非空且包含 plugin.json，或 manifest_path 指向有效文件，
        解析后用字段覆盖实例属性。
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

                # Apply plugin.json / sidecar .json overrides if present
                resolved_manifest = (
                    manifest_path
                    if (manifest_path is not None and manifest_path.is_file())
                    else (
                        (plugin_dir / "plugin.json")
                        if (plugin_dir is not None and (plugin_dir / "plugin.json").is_file())
                        else None
                    )
                )
                if resolved_manifest is not None:
                    try:
                        manifest_data = json.loads(
                            resolved_manifest.read_text(encoding="utf-8")
                        )
                        self._apply_manifest(instance, manifest_data)
                        _L.debug(
                            "plugin.json applied | name={}", instance.name
                        )
                    except (json.JSONDecodeError, OSError) as e:
                        _L.warning(
                            "plugin.json parse failed | name={} error={}",
                            plugin_name, e,
                        )

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
        manifest_path = (
            module_file.parent / "plugin.json"
            if module_file.name == "plugin.py"
            else (
                module_file.parent / "plugin.json"
                if module_file.name == "__init__.py"
                else module_file.with_suffix(".json")
            )
        )
        if not manifest_path.is_file():
            self._normalize_plugin_lock_policy(instance)
            return
        try:
            self._apply_manifest(instance, json.loads(manifest_path.read_text(encoding="utf-8")))
            _L.debug("plugin manifest applied during register | name={}", instance.name)
        except Exception as exc:
            _L.warning("plugin manifest apply failed during register | name={} error={}", instance.name, exc)
        self._normalize_plugin_lock_policy(instance)

    @classmethod
    def _normalize_tier(cls, name: str, tier: str) -> str:
        if name in cls._SYSTEM_PLUGIN_WHITELIST:
            return "system"
        return "user"

    @classmethod
    def _normalize_toggle_policy(cls, name: str, toggle_policy: str, tier: str) -> str:
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

    def _resolve_dependencies(self) -> list[AmadeusPlugin]:
        """用 Kahn 算法拓扑排序插件依赖图。

        若某插件声明的依赖不存在或版本不兼容，降级为 warning 并跳过该依赖边。
        若存在循环依赖，降级为 warning 并回退到 priority 排序。

        返回拓扑排序后的插件列表。
        """
        from kernel.manifest import check_version

        name_to_plugin: dict[str, AmadeusPlugin] = {p.name: p for p in self._plugins}

        # 构建邻接表和入度，同时校验依赖
        edges: dict[str, list[str]] = {p.name: [] for p in self._plugins}
        in_degree: dict[str, int] = {p.name: 0 for p in self._plugins}

        for p in self._plugins:
            for dep_name, version_constraint in p.dependencies.items():
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
            _L.warning(
                "circular dependency detected, falling back to priority order | "
                "in_cycle={}",
                list(cycle_plugins),
            )
            return sorted(self._plugins, key=lambda p: p.priority)

        return [name_to_plugin[n] for n in sorted_names]

    async def _safe_call(
        self,
        plugin: AmadeusPlugin,
        coro: Awaitable[Any],
        hook_name: str,
    ) -> Any:
        """安全调用插件钩子。异常隔离：单个插件失败不影响其他插件。

        超过 100ms 打 debug 日志，超过 5s 打 warning。
        被禁用的插件跳过执行。
        """
        if not plugin.enabled:
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
        try:
            result = await coro
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self._record_hook_health(health, hook_name, elapsed_ms, error=None)
            budget_ms = self._hook_budget_ms(plugin)
            if elapsed_ms > budget_ms:
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
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
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
                plugin.name, hook_name, elapsed_ms,
                exc_info=True,
            )
            return None

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
            "permission_denials": 0,
            "last_permission_denied": "",
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

    def _has_permission(self, plugin: AmadeusPlugin, permission: str) -> bool:
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
        self._refresh_health_state(health, plugin.enabled)
        _L.debug(
            "plugin permission denied | plugin={} permission={}",
            plugin.name, permission,
        )
        return False

    @staticmethod
    def _hook_budget_ms(plugin: AmadeusPlugin) -> int:
        raw_budget = getattr(plugin, "hook_budget_ms", 5000)
        try:
            budget = int(raw_budget)
        except (TypeError, ValueError):
            return 5000
        return budget if budget > 0 else 5000

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
        if int(health.get("permission_denials", 0) or 0) > 0:
            health["state"] = "permission_limited"
            return remaining
        health["state"] = "healthy"
        return remaining
