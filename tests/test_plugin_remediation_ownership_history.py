"""RED contracts for moving history backfill into the connection pipeline."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bootstrap.application import build_plugin_bus
from kernel.types import AmadeusPlugin
from services.routing.connection_pipeline import RuntimeConnectionPipeline

_REPO_ROOT = Path(__file__).resolve().parents[1]

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
    "worldbook",
    "dream",
    "qzone_journal",
    "bilibili",
    "echo",
    "element_detector",
    "debug_commands",
]

HistoryBackfillStage = Callable[[Any, Any], Awaitable[None]]


class _Bot:
    self_id = 123456

    async def get_group_list(self) -> list[dict[str, object]]:
        return []


class _PersonaRuntime:
    def bind_bot_self_id(self, self_id: str) -> None:
        pass


class _Scheduler:
    def set_bot(self, bot: object) -> None:
        pass


class _Context:
    def __init__(self) -> None:
        self.bot: object | None = None
        self.llm_client = SimpleNamespace(_bot_self_id=None)
        self.state_board = SimpleNamespace(bot_self_id=None)
        self.persona_runtime = _PersonaRuntime()
        self.scheduler = _Scheduler()
        self.startup_triggered = False
        self.admins: dict[str, object] = {}
        self.config = SimpleNamespace(group=None)
        self.group_inventory: dict[str, dict[str, object]] = {}


class _Bus:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def fire_on_bot_connect(self, ctx: object, bot: object) -> None:
        self._events.append("bus.fire_on_bot_connect")

    def start_tick_loop(self, ctx: object) -> None:
        pass


def _pipeline_with_history_stage(
    ctx: _Context,
    bus: _Bus,
    stage: HistoryBackfillStage,
) -> Any:
    pipeline_type: Any = RuntimeConnectionPipeline
    if "history_backfill_stage" in inspect.signature(pipeline_type).parameters:
        return pipeline_type(ctx, bus, history_backfill_stage=stage)

    pipeline = pipeline_type(ctx, bus)
    pipeline._history_backfill_stage = stage
    return pipeline


def _read_history_status(pipeline: Any) -> object:
    status = getattr(pipeline, "history_backfill_status", None)
    return status() if callable(status) else status


def test_build_plugin_bus_excludes_history_loader_stage() -> None:
    bus = build_plugin_bus(
        plugin_root=_REPO_ROOT / "plugins",
        persisted_states={},
        config_disabled=[],
    )

    names = [plugin.name for plugin in bus.plugins]

    assert names == EXPECTED_PLUGIN_ORDER
    assert len(names) == 24
    assert len(set(names)) == len(names)
    assert bus.get_plugin("history_loader") is None


def test_history_loader_module_owns_no_plugin_subclass() -> None:
    module = importlib.import_module("plugins.history_loader.plugin")

    owned_plugin_classes = [
        candidate.__name__
        for _, candidate in inspect.getmembers(module, inspect.isclass)
        if candidate.__module__ == module.__name__
        and issubclass(candidate, AmadeusPlugin)
    ]

    assert owned_plugin_classes == []


def test_history_loader_package_exports_helpers_without_plugin_class() -> None:
    package = importlib.import_module("plugins.history_loader")

    assert callable(getattr(package, "load_group_history", None))
    assert callable(getattr(package, "_extract_content", None))
    assert not hasattr(package, "HistoryLoaderPlugin")


@pytest.mark.asyncio
async def test_connection_pipeline_runs_history_backfill_before_plugin_hooks() -> None:
    events: list[str] = []
    stage_calls: list[tuple[object, object]] = []
    ctx = _Context()
    bus = _Bus(events)
    bot = _Bot()

    async def history_backfill_stage(stage_ctx: object, stage_bot: object) -> None:
        stage_calls.append((stage_ctx, stage_bot))
        events.append("history_backfill_stage")

    pipeline = _pipeline_with_history_stage(ctx, bus, history_backfill_stage)

    await pipeline.on_connect(bot)

    assert stage_calls == [(ctx, bot)]
    assert events == [
        "history_backfill_stage",
        "bus.fire_on_bot_connect",
    ]
    status = _read_history_status(pipeline)
    assert isinstance(status, dict)
    assert status["status"] == "success"
    assert status["runs"] == 1
    assert status["last_error"] == ""


@pytest.mark.asyncio
async def test_history_backfill_failure_is_observable_and_does_not_block_hooks() -> None:
    events: list[str] = []
    stage_calls: list[tuple[object, object]] = []
    ctx = _Context()
    bus = _Bus(events)
    bot = _Bot()

    async def failing_history_backfill_stage(
        stage_ctx: object,
        stage_bot: object,
    ) -> None:
        stage_calls.append((stage_ctx, stage_bot))
        events.append("history_backfill_stage")
        raise RuntimeError("history backfill boom")

    pipeline = _pipeline_with_history_stage(ctx, bus, failing_history_backfill_stage)

    try:
        await pipeline.on_connect(bot)
    except RuntimeError as exc:
        raise AssertionError(
            "history backfill failure must not escape RuntimeConnectionPipeline.on_connect"
        ) from exc

    assert stage_calls == [(ctx, bot)]
    assert events == [
        "history_backfill_stage",
        "bus.fire_on_bot_connect",
    ]
    status = _read_history_status(pipeline)
    assert isinstance(status, dict)
    assert status["status"] == "failed"
    assert status["runs"] == 1
    assert "history backfill boom" in status["last_error"]
