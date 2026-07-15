"""Process integration contracts for building and installing an application."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bootstrap import application as application_module
from bootstrap.application import (
    ApplicationAssembly,
    ApplicationPaths,
    ApplicationRuntime,
    build_application,
    install_application,
)
from kernel import router as router_module
from kernel.background_tasks import BackgroundTaskSupervisor
from kernel.config import BotConfig
from services.learning_extract_coordinator import LearningExtractCoordinator
from services.memory_consolidator import MemoryConsolidatorLifecycle
from services.routing.connection_pipeline import RuntimeConnectionPipeline

EXPECTED_PLUGIN_ORDER = [
    "calendar_context",
    "chat",
    "datetime",
    "group_admin",
    "http_api",
    "web_fetch",
    "web_search",
    "context",
    "knowledge",
    "affection",
    "schedule",
    "food",
    "memo",
    "sticker",
    "slang",
    "style",
    "social_narrative",
    "dream",
    "bilibili",
    "echo",
    "element_detector",
    "debug_commands",
]


class _FakeApp:
    def __init__(self) -> None:
        self.include_calls: list[Any] = []

    def include_router(self, router: Any) -> None:
        self.include_calls.append(router)


def _paths(tmp_path: Path) -> ApplicationPaths:
    repo_root = Path(__file__).resolve().parents[1]
    return ApplicationPaths(
        repo_root=tmp_path / "repo",
        storage_dir=tmp_path / "storage",
        plugin_root=repo_root / "plugins",
        plugin_data_dir=tmp_path / "storage" / "plugins",
        config_path=str(tmp_path / "config.toml"),
    )


def test_build_application_wires_one_unstarted_process_assembly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    runtime = ApplicationRuntime([])

    def capture_runtime(**dependencies: Any) -> ApplicationRuntime:
        captured.update(dependencies)
        return runtime

    monkeypatch.setattr(
        application_module,
        "compose_application_runtime",
        capture_runtime,
    )
    config = BotConfig()
    app = _FakeApp()

    assembly = build_application(
        config=config,
        paths=_paths(tmp_path),
        app=app,
        clock=lambda: 1234.5,
    )

    ctx = assembly.context
    assert assembly.runtime is runtime
    assert captured["ctx"] is ctx
    assert captured["bus"] is assembly.bus
    assert captured["backup"] is ctx.backup_scheduler
    assert isinstance(ctx.background_task_supervisor, BackgroundTaskSupervisor)
    assert captured["task_supervisor"] is ctx.background_task_supervisor
    assert isinstance(ctx.learning_extract_coordinator, LearningExtractCoordinator)
    assert (
        ctx.learning_extract_coordinator._task_supervisor
        is ctx.background_task_supervisor
    )
    assert (
        captured["learning_extract_coordinator"]
        is ctx.learning_extract_coordinator
    )
    assert isinstance(
        ctx.memory_consolidator_lifecycle,
        MemoryConsolidatorLifecycle,
    )
    assert (
        captured["memory_consolidator_lifecycle"]
        is ctx.memory_consolidator_lifecycle
    )
    assert assembly.bus._task_supervisor is ctx.background_task_supervisor
    assert ctx.backup_scheduler._task_supervisor is ctx.background_task_supervisor
    assert ctx.bus is assembly.bus
    assert ctx.plugin_state_store is assembly.plugin_state_store
    assert ctx.plugin_config_store is assembly.plugin_config_store
    assert [plugin.name for plugin in assembly.bus.plugins] == EXPECTED_PLUGIN_ORDER
    assert len(assembly.bus.plugins) == 22
    assert assembly.bus.started is False
    assert ctx.bot_start_time == 1234.5
    assert ctx.outbound_group_access_guard is not None
    assert ctx.protocol_trace is not None
    assert ctx.runtime_errors is not None
    assert ctx.vision_client is None
    assert isinstance(assembly.connection_pipeline, RuntimeConnectionPipeline)
    assert ctx.connection_pipeline is assembly.connection_pipeline
    assert app.include_calls == []


def test_install_application_passes_exact_assembly_to_router_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, Any, Any, Any]] = []

    def capture_setup(
        bus: Any,
        ctx: Any,
        *,
        runtime: Any = None,
        connection_pipeline: Any = None,
    ) -> None:
        calls.append((bus, ctx, runtime, connection_pipeline))

    monkeypatch.setattr(router_module, "setup_routers", capture_setup)
    context = object()
    bus = object()
    runtime = ApplicationRuntime([])
    connection_pipeline = object()
    assembly = ApplicationAssembly(
        context=context,
        bus=bus,
        runtime=runtime,
        plugin_state_store=object(),
        plugin_config_store=object(),
        connection_pipeline=connection_pipeline,
    )

    install_application(assembly)

    assert calls == [(bus, context, runtime, connection_pipeline)]
