"""Process-level application lifecycle ownership."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from kernel.background_tasks import BackgroundTaskSupervisor
    from kernel.bus import PluginBus


class LifecycleComponent(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class ProcessPluginBus(Protocol):
    async def fire_on_startup(self, ctx: Any) -> None: ...

    def collect_tools(self) -> list[Any]: ...

    async def stop_tick_loop(self) -> None: ...

    async def fire_on_shutdown(self, ctx: Any) -> None: ...


class ToolRegistryLifecycle(Protocol):
    def snapshot_tools(self) -> tuple[Any, ...]: ...

    def merge_all(self, tools: Iterable[Any]) -> None: ...

    def replace_all(self, tools: Iterable[Any]) -> None: ...


class BackupLifecycle(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class AdminInstaller(Protocol):
    def install(self, ctx: Any) -> Any: ...


@dataclass(frozen=True)
class ApplicationPaths:
    repo_root: Path
    storage_dir: Path
    plugin_root: Path
    plugin_data_dir: Path
    config_path: str


@dataclass
class ApplicationAssembly:
    context: Any
    bus: Any
    runtime: ApplicationRuntime
    connection_pipeline: Any
    plugin_state_store: Any
    plugin_config_store: Any


class ApplicationRuntime:
    """Own process-level lifecycle components."""

    def __init__(self, components: Sequence[LifecycleComponent]) -> None:
        self._components = tuple(components)
        self._started_components: list[LifecycleComponent] = []
        self._started = False
        self._stopped = False
        self._stop_task: asyncio.Task[list[BaseException]] | None = None
        self._stop_result_consumed = False
        self._lifecycle_lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started:
                return
            if self._stopped:
                raise RuntimeError("application runtime cannot restart after stop")

            try:
                for component in self._components:
                    self._started_components.append(component)
                    await component.start()
            except BaseException:
                self._stopped = True
                stop_task = self._ensure_stop_task()
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(stop_task)
                self._stop_result_consumed = True
                raise
            self._started = True

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if self._stop_task is None and self._stopped:
                return
            self._stopped = True
            errors = await asyncio.shield(self._ensure_stop_task())
            self._started = False
            if self._stop_result_consumed:
                return
            self._stop_result_consumed = True
            if len(errors) == 1:
                raise errors[0]
            if errors:
                raise BaseExceptionGroup("application shutdown failed", errors)

    def _ensure_stop_task(self) -> asyncio.Task[list[BaseException]]:
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop_acquired())
        return self._stop_task

    async def _stop_acquired(self) -> list[BaseException]:
        errors: list[BaseException] = []
        while self._started_components:
            component = self._started_components.pop()
            try:
                await component.stop()
            except BaseException as exc:
                errors.append(exc)
        return errors


class _PluginBusLifecycle:
    def __init__(self, ctx: Any, bus: ProcessPluginBus) -> None:
        self._ctx = ctx
        self._bus = bus

    async def start(self) -> None:
        await self._bus.fire_on_startup(self._ctx)

    async def stop(self) -> None:
        await self._bus.fire_on_shutdown(self._ctx)


class _PluginToolMergeLifecycle:
    def __init__(self, bus: ProcessPluginBus, registry: ToolRegistryLifecycle) -> None:
        self._bus = bus
        self._registry = registry
        self._snapshot: tuple[Any, ...] | None = None

    async def start(self) -> None:
        self._snapshot = self._registry.snapshot_tools()
        self._registry.merge_all(self._bus.collect_tools())

    async def stop(self) -> None:
        if self._snapshot is not None:
            self._registry.replace_all(self._snapshot)
            self._snapshot = None


class _BackupLifecycle:
    def __init__(self, backup: BackupLifecycle) -> None:
        self._backup = backup

    async def start(self) -> None:
        await self._backup.start()

    async def stop(self) -> None:
        await self._backup.stop()


class _TickCleanupLifecycle:
    def __init__(self, bus: ProcessPluginBus) -> None:
        self._bus = bus

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        await self._bus.stop_tick_loop()


class _AdminCommitLifecycle:
    def __init__(self, ctx: Any, admin: AdminInstaller) -> None:
        self._ctx = ctx
        self._admin = admin

    async def start(self) -> None:
        result = self._admin.install(self._ctx)
        if inspect.isawaitable(result):
            await result
        commit = getattr(self._ctx, "chat_runtime_commit", None)
        if callable(commit):
            result = commit()
            if inspect.isawaitable(result):
                await result

    async def stop(self) -> None:
        return None


def compose_application_runtime(
    *,
    ctx: Any,
    bus: ProcessPluginBus,
    registry: ToolRegistryLifecycle,
    backup: BackupLifecycle,
    admin: AdminInstaller,
    task_supervisor: BackgroundTaskSupervisor | None = None,
    memory_consolidator_lifecycle: LifecycleComponent | None = None,
    learning_extract_coordinator: LifecycleComponent | None = None,
) -> ApplicationRuntime:
    """Compose process lifecycle owners without installing host handlers."""
    components: list[LifecycleComponent] = []
    if task_supervisor is not None:
        components.append(task_supervisor)
    components.append(_PluginBusLifecycle(ctx, bus))
    if memory_consolidator_lifecycle is not None:
        components.append(memory_consolidator_lifecycle)
    components.extend(
        [
            _PluginToolMergeLifecycle(bus, registry),
            _BackupLifecycle(backup),
            _TickCleanupLifecycle(bus),
            _AdminCommitLifecycle(ctx, admin),
        ]
    )
    if learning_extract_coordinator is not None:
        components.append(learning_extract_coordinator)
    return ApplicationRuntime(components)


def build_plugin_bus(
    *,
    plugin_root: Path,
    persisted_states: Mapping[str, bool],
    config_disabled: Sequence[str],
    task_supervisor: BackgroundTaskSupervisor | None = None,
) -> PluginBus:
    """Build the current plugin set without starting plugin lifecycles."""
    from kernel.bus import PluginBus
    from plugins.affection.plugin import AffectionPlugin
    from plugins.chat.plugin import ChatPlugin
    from plugins.dream.plugin import DreamPlugin
    from plugins.echo.plugin import EchoPlugin
    from plugins.element_detector.plugin import ElementDetectorPlugin
    from plugins.memo.plugin import MemoPlugin
    from plugins.schedule.plugin import SchedulePlugin
    from plugins.sticker.plugin import StickerPlugin

    bus = PluginBus(task_supervisor=task_supervisor)
    for plugin in (
        ChatPlugin(),
        AffectionPlugin(),
        DreamPlugin(),
        EchoPlugin(),
        ElementDetectorPlugin(),
        MemoPlugin(),
        SchedulePlugin(),
        StickerPlugin(),
    ):
        bus.register(plugin)
    bus.discover_plugins(plugin_root)

    for name, enabled in persisted_states.items():
        bus.set_plugin_enabled(name, enabled)
    for name in config_disabled:
        bus.set_plugin_enabled(name, False)
    return bus


def migrate_legacy_plugin_enabled_override(
    plugin_config_store: Any,
    plugin_state_store: Any,
    *,
    plugin_name: str,
) -> bool:
    """Move a retired per-plugin ``enabled`` override to the canonical state store."""
    values = plugin_config_store.get(plugin_name)
    if "enabled" not in values:
        return False

    legacy_enabled = values.pop("enabled")
    if plugin_state_store.get(plugin_name) is None:
        if isinstance(legacy_enabled, str):
            enabled = legacy_enabled.strip().lower() in {"1", "true", "yes", "on"}
        else:
            enabled = bool(legacy_enabled)
        plugin_state_store.set_enabled(plugin_name, enabled)
    plugin_config_store.set_values(plugin_name, values)
    return True


class _ContextToolRegistry:
    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    def _registry(self) -> Any:
        registry = getattr(self._ctx, "tool_registry", None)
        if registry is None:
            raise RuntimeError("chat startup did not provide a tool registry")
        return registry

    def snapshot_tools(self) -> tuple[Any, ...]:
        return self._registry().snapshot_tools()

    def merge_all(self, tools: Iterable[Any]) -> None:
        self._registry().merge_all(tools)

    def replace_all(self, tools: Iterable[Any]) -> None:
        self._registry().replace_all(tools)


class _AdminRouterInstaller:
    def __init__(self, app: Any, *, config_path: str) -> None:
        self._app = app
        self._config_path = config_path
        self._installed = False

    def install(self, ctx: Any) -> None:
        if self._installed:
            raise RuntimeError("admin router already installed")
        from admin import create_admin_router

        router = create_admin_router(ctx, config_path=self._config_path)
        self._app.include_router(router)
        self._installed = True


def build_application(
    *,
    config: Any,
    paths: ApplicationPaths,
    app: Any,
    clock: Callable[[], float] = time.time,
) -> ApplicationAssembly:
    """Build the process composition without starting plugin lifecycles."""
    from loguru import logger

    from kernel.background_tasks import BackgroundTaskSupervisor
    from kernel.types import PluginContext
    from services import learning_settings
    from services.command import CommandDispatcher
    from services.errors import RuntimeErrorStore
    from services.group.outbound_access_guard import OutboundGroupAccessGuard
    from services.humanization import HUMANIZATION_CONTRACT, create_humanization_state_bus
    from services.learning_extract_coordinator import LearningExtractCoordinator
    from services.media.vision import VisionClient
    from services.memory_consolidator import MemoryConsolidatorLifecycle
    from services.plugin_config import PluginConfigStore
    from services.plugin_state import PluginStateStore
    from services.protocol_trace import ProtocolConnectionHistory, ProtocolTraceStore
    from services.routing import RuntimeConnectionPipeline
    from services.storage.backup_scheduler import BackupScheduler

    paths.plugin_data_dir.mkdir(parents=True, exist_ok=True)
    ctx = PluginContext(
        config=config,
        storage_dir=paths.storage_dir,
        plugin_data_dir=paths.plugin_data_dir,
    )
    task_supervisor = BackgroundTaskSupervisor()
    ctx.background_task_supervisor = task_supervisor
    learning_extract_coordinator = LearningExtractCoordinator(
        task_supervisor=task_supervisor,
    )
    ctx.learning_extract_coordinator = learning_extract_coordinator

    def is_group_muted(group_id: str) -> bool:
        scheduler = getattr(ctx, "scheduler", None)
        checker = getattr(scheduler, "is_muted", None)
        return bool(callable(checker) and checker(str(group_id)))

    ctx.outbound_group_access_guard = OutboundGroupAccessGuard(
        config.group,
        is_group_muted=is_group_muted,
    )
    ctx.protocol_trace = ProtocolTraceStore(max_items=120)
    ctx.protocol_connections = ProtocolConnectionHistory(max_items=80)
    ctx.runtime_errors = RuntimeErrorStore(max_events=300, max_groups=120)
    ctx.humanization_contract = HUMANIZATION_CONTRACT
    ctx.runtime_state = create_humanization_state_bus()
    ctx.bot_start_time = clock()

    backup_scheduler = BackupScheduler(
        storage_dir=paths.storage_dir,
        repo_root=paths.repo_root,
        daily_time=config.backup.daily_time,
        keep_days=config.backup.keep_days,
        default_profile=config.backup.default_profile,
        enabled=config.backup.enabled,
        quick_check_enabled=config.backup.quick_check_enabled,
        quick_check_interval_minutes=config.backup.quick_check_interval_minutes,
        task_supervisor=task_supervisor,
    )
    ctx.backup_scheduler = backup_scheduler

    if config.vision.qwen.api_key:
        ctx.vision_client = VisionClient(
            base_url=config.vision.qwen.base_url,
            api_key=config.vision.qwen.api_key,
            model=config.vision.qwen.model,
            timeout_s=15.0,
            max_tokens=config.vision.describe_max_tokens,
        )
        logger.info(
            "Qwen VL vision enabled | model={} base_url={}",
            config.vision.qwen.model,
            config.vision.qwen.base_url,
        )
    else:
        ctx.vision_client = None
        logger.info("Qwen VL vision disabled (no api_key)")

    plugin_config_store = PluginConfigStore(
        paths.plugin_data_dir / "config",
        plugin_root=paths.plugin_root,
    )
    plugin_state_store = PluginStateStore(paths.plugin_data_dir / "plugin-state.json")
    migrate_legacy_plugin_enabled_override(
        plugin_config_store,
        plugin_state_store,
        plugin_name="food",
    )
    bus = build_plugin_bus(
        plugin_root=paths.plugin_root,
        persisted_states=plugin_state_store.load(),
        config_disabled=config.kernel.disabled_plugins,
        task_supervisor=task_supervisor,
    )
    ctx.plugin_state_store = plugin_state_store
    ctx.plugin_config_store = plugin_config_store
    ctx.bus = bus
    ctx.command_dispatcher = CommandDispatcher(bus)

    memory_consolidator_lifecycle = MemoryConsolidatorLifecycle(
        ctx=ctx,
        settings_loader=learning_settings.load,
        monotonic=time.monotonic,
        event_boundary_enabled=lambda: os.getenv("EBR_ENABLED", "true").strip().lower()
        not in {"0", "false", "no", "off"},
        task_supervisor=task_supervisor,
    )
    ctx.memory_consolidator_lifecycle = memory_consolidator_lifecycle

    runtime = compose_application_runtime(
        ctx=ctx,
        bus=bus,
        registry=_ContextToolRegistry(ctx),
        backup=backup_scheduler,
        admin=_AdminRouterInstaller(app, config_path=paths.config_path),
        task_supervisor=task_supervisor,
        memory_consolidator_lifecycle=memory_consolidator_lifecycle,
        learning_extract_coordinator=learning_extract_coordinator,
    )
    connection_pipeline = RuntimeConnectionPipeline(ctx, bus)
    ctx.connection_pipeline = connection_pipeline
    return ApplicationAssembly(
        context=ctx,
        bus=bus,
        runtime=runtime,
        connection_pipeline=connection_pipeline,
        plugin_state_store=plugin_state_store,
        plugin_config_store=plugin_config_store,
    )


def install_application(assembly: ApplicationAssembly) -> None:
    """Install host handlers for a built application exactly once."""
    from kernel.router import setup_routers

    setup_routers(
        assembly.bus,
        assembly.context,
        runtime=assembly.runtime,
        connection_pipeline=assembly.connection_pipeline,
    )
