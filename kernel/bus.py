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

    def __init__(self) -> None:
        self._plugins: list[AmadeusPlugin] = []
        self._started: bool = False
        self._tick_task: asyncio.Task[None] | None = None

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
        # 插入到第一个 priority 更大的插件之前（稳定排序）
        idx = len(self._plugins)
        for i, p in enumerate(self._plugins):
            if plugin.priority < p.priority:
                idx = i
                break
        self._plugins.insert(idx, plugin)
        _L.info("plugin registered | name={} priority={}", plugin.name, plugin.priority)

    def unregister(self, name: str) -> bool:
        """按名称移除插件。返回 True 表示成功移除。"""
        for i, p in enumerate(self._plugins):
            if p.name == name:
                self._plugins.pop(i)
                _L.info("plugin unregistered | name={}", name)
                return True
        return False

    def get_plugin(self, name: str) -> AmadeusPlugin | None:
        """按名称查找插件。"""
        for p in self._plugins:
            if p.name == name:
                return p
        return None

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
            await self._safe_call(p, p.on_bot_connect(ctx, bot), "on_bot_connect")
        _L.info("bot connect notified | count={}", len(order))

    # ---- 消息管线调度 ----

    async def fire_on_message(self, ctx: MessageContext) -> bool:
        """按优先级调用 on_message，直到有插件返回 True 消费消息。

        返回 True 表示消息已被某插件消费，调用方应停止后续处理。
        """
        for p in self._plugins:
            consumed = await self._safe_call(p, p.on_message(ctx), "on_message")
            if consumed is True:
                _L.debug("message consumed | plugin={} session={}", p.name, ctx.session_id)
                return True
        return False

    async def fire_on_thinker_decision(self, ctx: ThinkerContext) -> None:
        """通知所有插件 thinker 决策结果。"""
        for p in self._plugins:
            await self._safe_call(p, p.on_thinker_decision(ctx), "on_thinker_decision")

    async def fire_on_pre_prompt(self, ctx: PromptContext) -> None:
        """按优先级调用 on_pre_prompt，收集所有插件追加的 PromptBlock。

        各插件通过 ctx.add_block() 追加内容，调用方读取 ctx.blocks 使用。
        """
        for p in self._plugins:
            await self._safe_call(p, p.on_pre_prompt(ctx), "on_pre_prompt")

    async def fire_on_post_reply(self, ctx: ReplyContext) -> None:
        """按优先级调用 on_post_reply。各插件独立执行副作用。"""
        for p in self._plugins:
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
            try:
                routes.extend(p.register_admin_routes())
            except Exception:
                _L.warning("collect_admin_routes failed | plugin={}", p.name, exc_info=True)
        return routes

    # ---- 定时调度 ----

    async def fire_on_tick(self, ctx: PluginContext) -> None:
        """按优先级调用 on_tick。"""
        for p in self._plugins:
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
        3. 独立 .py 文件 → 直接导入，找到 AmadeusPlugin 子类并实例化
           （同名时子目录优先，__init__.py 跳过）

        返回新注册的插件数量。
        """
        dir_path = Path(directory)
        if not dir_path.is_dir():
            _L.warning("plugin dir not found | path={}", dir_path)
            return 0

        discovered_names: set[str] = set()
        count = 0

        # ---- Pass 1: directory-based plugins (priority over .py files) ----
        for subdir in sorted(dir_path.iterdir()):
            if not subdir.is_dir():
                continue
            plugin_file = subdir / "plugin.py"
            if not plugin_file.is_file():
                continue

            plugin_name = subdir.name
            discovered_names.add(plugin_name)
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

        # ---- Pass 2: single-file plugins (.py files without a subdir) ----
        for entry in sorted(dir_path.iterdir()):
            if not entry.is_file():
                continue
            if not entry.name.endswith(".py"):
                continue
            if entry.name.startswith("__"):
                continue

            plugin_name = entry.stem  # filename without .py
            if plugin_name in discovered_names:
                _L.debug(
                    "single-file plugin shadowed by directory | name={}", plugin_name
                )
                continue
            if self.get_plugin(plugin_name) is not None:
                _L.debug("plugin already registered, skipping | name={}", plugin_name)
                continue

            # Check for sidecar .json manifest
            sidecar_json = entry.with_suffix(".json")

            try:
                instance = self._load_plugin_module(
                    plugin_name, entry,
                    manifest_path=sidecar_json if sidecar_json.is_file() else None,
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
        import json
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
        for key in ("name", "version", "description", "priority", "enabled", "author"):
            if key in data:
                setattr(instance, key, data[key])
        if "dependencies" in data and isinstance(data["dependencies"], dict):
            instance.dependencies = data["dependencies"]

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
            return None

        t0 = time.perf_counter()
        try:
            result = await coro
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if elapsed_ms > 5000:
                _L.warning(
                    "hook very slow | plugin={} hook={} elapsed={:.0f}ms",
                    plugin.name, hook_name, elapsed_ms,
                )
            elif elapsed_ms > 100:
                _L.debug(
                    "hook slow | plugin={} hook={} elapsed={:.0f}ms",
                    plugin.name, hook_name, elapsed_ms,
                )
            return result
        except Exception:
            _L.warning(
                "hook error | plugin={} hook={} elapsed={:.0f}ms",
                plugin.name, hook_name, (time.perf_counter() - t0) * 1000,
                exc_info=True,
            )
            return None
