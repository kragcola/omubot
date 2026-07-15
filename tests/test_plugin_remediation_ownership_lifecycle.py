"""RED contracts for plugin capability and lifecycle ownership remediation."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api.dream import create_dream_router
from bootstrap import chat_runtime as chat_runtime_module
from kernel import config as kernel_config
from kernel.bus import PluginBus
from kernel.router import _semantic_gate_familiarity
from plugins.affection.models import AffectionProfile
from plugins.affection.plugin import AffectionPlugin
from plugins.calendar_context.plugin import CalendarContextPlugin
from plugins.calendar_context.service import BirthdayEntry, DayContext
from plugins.datetime import plugin as datetime_plugin_module
from plugins.datetime.plugin import DateTimeConfig, DateTimePlugin
from plugins.dream import plugin as dream_plugin_module
from plugins.dream.plugin import DreamConfig, DreamPlugin
from plugins.history_loader.plugin import _contains_debug_command
from plugins.schedule.mood import MoodEngine
from plugins.schedule.plugin import SchedulePlugin
from services import learning_settings
from services import memory_consolidator as memory_consolidator_module
from services.llm.client import LLMClient

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_slang_and_style_periodic_extract_share_service_coordinator() -> None:
    for relative_path in ("plugins/slang/plugin.py", "plugins/style/plugin.py"):
        source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not any(module.startswith("admin.routes") for module in imports)
        assert "run_coordinated_extract" in source


class _AffectionStoreProbe:
    def get(self, user_id: str) -> SimpleNamespace:
        assert user_id == "u1"
        return SimpleNamespace(
            total_interactions=5,
            custom_nickname="小明",
            group_nickname="",
            tier="friend",
            score=60.0,
        )


class _AffectionEngineProbe:
    def __init__(self) -> None:
        self._store = _AffectionStoreProbe()


class _MoodProfileProbe:
    label = "放松"
    energy = 0.8
    valence = 0.4
    openness = 0.7
    tension = 0.1
    anomaly_reason = ""


def test_llm_direct_consumers_follow_effective_plugin_state() -> None:
    """Disabling a plugin must also disable direct LLM capability consumers."""

    bus = PluginBus()
    affection = AffectionPlugin()
    schedule = SchedulePlugin()
    bus.register(affection)
    bus.register(schedule)

    client = object.__new__(LLMClient)
    client._bus = bus
    client._affection_engine = _AffectionEngineProbe()
    client._mood_getter = lambda **_kwargs: _MoodProfileProbe()

    assert client._build_thinker_affection_text("u1")
    assert client._build_thinker_mood_text(group_id="g1", session_id="group_g1")
    assert client._build_provider_mood_fit_target(group_id="g1", session_id="group_g1") is not None

    assert bus.set_plugin_enabled("affection", False) is True
    assert bus.set_plugin_enabled("schedule", False) is True

    assert client._build_thinker_affection_text("u1") == ""
    assert client._build_thinker_mood_text(group_id="g1", session_id="group_g1") == ""
    assert client._build_provider_mood_fit_target(group_id="g1", session_id="group_g1") is None


def test_thinker_affection_text_uses_current_group_nicknames_model() -> None:
    bus = PluginBus()
    bus.register(AffectionPlugin())
    profile = AffectionProfile(
        user_id="u1",
        score=60.0,
        total_interactions=5,
        group_nicknames={"group_100": "小明"},
    )
    client = object.__new__(LLMClient)
    client._bus = bus
    client._affection_engine = SimpleNamespace(
        _store=SimpleNamespace(get=lambda user_id: profile if user_id == "u1" else None),
    )

    text = client._build_thinker_affection_text("u1")

    assert "称呼：小明" in text
    assert "score=60" in text


def test_effective_plugin_state_resolver_prefers_bus_state_over_config() -> None:
    """Persisted/bus disabled state must win over an enabled plugin config file."""

    resolver = getattr(chat_runtime_module, "resolve_effective_plugin_enabled", None)
    assert callable(resolver), (
        "bootstrap.chat_runtime must expose resolve_effective_plugin_enabled(ctx, name, config_enabled=...)"
    )

    owners = {
        "affection": SimpleNamespace(enabled=False),
        "schedule": SimpleNamespace(enabled=False),
    }
    ctx = SimpleNamespace(bus=SimpleNamespace(get_plugin=owners.get))

    assert resolver(ctx, "affection", config_enabled=True) is False
    assert resolver(ctx, "schedule", config_enabled=True) is False
    assert resolver(ctx, "missing", config_enabled=True) is True


def test_router_ignores_affection_familiarity_slot_after_runtime_disable() -> None:
    bus = PluginBus()
    affection = AffectionPlugin()
    bus.register(affection)
    runtime_state = SimpleNamespace(
        get=lambda *_args, **_kwargs: SimpleNamespace(
            value={"familiarity": 0.75},
        ),
    )
    ctx = SimpleNamespace(bus=bus, runtime_state=runtime_state, affection_enabled=True)

    assert _semantic_gate_familiarity(cast(Any, ctx), "u1") == 0.75

    assert bus.set_plugin_enabled("affection", False) is True

    assert _semantic_gate_familiarity(cast(Any, ctx), "u1") is None


@pytest.mark.asyncio
async def test_schedule_ignores_retained_affection_engine_after_runtime_disable() -> None:
    bus = PluginBus()
    affection = AffectionPlugin()
    schedule = SchedulePlugin()
    bus.register(affection)
    bus.register(schedule)
    familiarity_score = MagicMock(return_value=0.65)
    ctx = SimpleNamespace(
        bus=bus,
        affection_enabled=True,
        affection_engine=SimpleNamespace(familiarity_score=familiarity_score),
        mood_engine=None,
        schedule_store=None,
        schedule_gen=None,
        timeline=None,
        schedule_event_replan_enabled=False,
        climate_sensor_hub=None,
        climate_engine=None,
        dialogue_climate_m4_enabled=False,
        calendar_service=None,
        story_arc_store=None,
    )
    await schedule.on_startup(cast(Any, ctx))

    assert schedule._resolve_familiarity("u1") == 0.65
    assert bus.set_plugin_enabled("affection", False) is True

    assert schedule._resolve_familiarity("u1") is None
    familiarity_score.assert_called_once_with("u1")


class _ScheduleGeneratorProbe:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.ensure_today_calls = 0

    def start(self, api_call: Any) -> None:
        assert callable(api_call)
        self.start_calls += 1

    async def ensure_today(self, api_call: Any) -> bool:
        assert callable(api_call)
        self.ensure_today_calls += 1
        return True

    async def stop(self) -> None:
        self.stop_calls += 1


@pytest.mark.asyncio
async def test_schedule_plugin_owns_generator_start_and_stop() -> None:
    """The component that starts ScheduleGenerator must also stop it exactly once."""

    generator = _ScheduleGeneratorProbe()
    schedule_store = SimpleNamespace(current=object(), load=lambda _date: object())
    ctx = SimpleNamespace(
        schedule_enabled=True,
        schedule_gen=generator,
        schedule_store=schedule_store,
        mood_engine=None,
        timeline=None,
        llm_client=SimpleNamespace(_call=AsyncMock()),
        dialogue_climate_m4_enabled=False,
        schedule_event_replan_enabled=False,
        story_arc_store=None,
        climate_sensor_hub=None,
        climate_engine=None,
        affection_engine=None,
        calendar_service=None,
    )
    plugin = SchedulePlugin()

    await plugin.on_startup(cast(Any, ctx))
    await plugin.on_bot_connect(cast(Any, ctx), SimpleNamespace())
    await plugin.on_shutdown(cast(Any, ctx))
    await plugin.on_shutdown(cast(Any, ctx))

    assert generator.start_calls == 1
    assert generator.stop_calls == 1


def _plugin_import_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if module == "plugins" or module.startswith("plugins."):
                    relative = path.relative_to(_REPO_ROOT)
                    violations.append(f"{relative}:{getattr(node, 'lineno', 0)} -> {module}")
    return violations


def test_kernel_and_services_do_not_import_plugin_domains() -> None:
    """Lower architectural layers must not depend on replaceable plugin packages."""

    violations = [
        *_plugin_import_violations(_REPO_ROOT / "kernel"),
        *_plugin_import_violations(_REPO_ROOT / "services"),
    ]

    assert violations == [], "reverse plugin imports:\n" + "\n".join(violations)


def test_mood_engine_accepts_calendar_context_day_context() -> None:
    """Schedule mood rendering must consume the canonical calendar DayContext type."""

    day = DayContext(
        date="2026-09-09",
        weekday=2,
        day_type="holiday",
        holiday_name="生日假",
        birthdays=[
            BirthdayEntry(
                name_cn="凤笑梦",
                name_jp="鳳えむ",
                group="Wonderlands×Showtime",
                is_wxs_member=True,
            )
        ],
        self_names={"凤笑梦"},
    )

    lines = MoodEngine._build_day_context_lines(day)

    assert any("生日假" in line for line in lines)
    assert any("你的生日" in line for line in lines)


@pytest.mark.asyncio
async def test_datetime_plugin_injects_calendar_context_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DateTimeTool must receive the CalendarContext owner instead of a legacy module global."""

    calendar_service = SimpleNamespace(get_day_context=lambda _now: None)
    monkeypatch.setattr(
        datetime_plugin_module,
        "load_plugin_config",
        lambda *_args, **_kwargs: DateTimeConfig(),
    )
    ctx = SimpleNamespace(schedule_store=None, calendar_service=calendar_service)
    plugin = DateTimePlugin()

    await plugin.on_startup(cast(Any, ctx))
    tool = plugin.register_tools()[0]

    assert getattr(tool, "_calendar_service", None) is calendar_service


@pytest.mark.asyncio
async def test_calendar_context_plugin_owns_birthday_tick_without_dream() -> None:
    """Birthday greetings must run from CalendarContext even when Dream is absent."""

    greeter = SimpleNamespace(check_and_greet=AsyncMock(return_value=["u1"]))
    bot = SimpleNamespace()
    llm_client = SimpleNamespace()
    calendar_service = SimpleNamespace()
    ctx = SimpleNamespace(
        bot=bot,
        llm_client=llm_client,
        scheduler=SimpleNamespace(is_muted=lambda _group_id: False),
        calendar_service=calendar_service,
        birthday_greeter=greeter,
    )
    plugin = CalendarContextPlugin()
    cast(Any, plugin)._service = calendar_service
    cast(Any, plugin)._greeter = greeter

    await plugin.on_bot_connect(cast(Any, ctx), bot)
    await plugin.on_tick(cast(Any, ctx))

    greeter.check_and_greet.assert_awaited_once()
    args, kwargs = greeter.check_and_greet.await_args
    assert args == (bot,)
    assert kwargs["llm_client"] is llm_client
    assert kwargs["group_allowed"]("100") is True


def _manifest_permissions(name: str) -> set[str]:
    payload = json.loads((_REPO_ROOT / "plugins" / name / "plugin.json").read_text(encoding="utf-8"))
    return {str(permission) for permission in payload.get("permissions", [])}


def test_calendar_context_manifest_declares_birthday_tick_permissions() -> None:
    assert {"tick", "network"} <= _manifest_permissions("calendar_context")


def test_dream_manifest_no_longer_claims_foreign_tick_work() -> None:
    assert "tick" not in _manifest_permissions("dream")


@pytest.mark.asyncio
async def test_dream_tick_does_not_run_birthday_greeter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dream lifecycle must not drive CalendarContext work."""

    greeter = SimpleNamespace(check_and_greet=AsyncMock(return_value=[]))
    monkeypatch.setattr(
        learning_settings,
        "load",
        lambda _storage_dir: {"consolidator": {"auto_enabled": False}},
    )
    plugin = DreamPlugin()
    cast(Any, plugin)._bot = SimpleNamespace()
    ctx = SimpleNamespace(
        birthday_greeter=greeter,
        llm_client=SimpleNamespace(),
        storage_dir="storage",
    )

    await plugin.on_tick(cast(Any, ctx))

    greeter.check_and_greet.assert_not_awaited()


@pytest.mark.asyncio
async def test_dream_tick_does_not_run_memory_consolidator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dream lifecycle must not drive generic memory consolidation."""

    consolidator = SimpleNamespace(run_once=AsyncMock())
    message_log = SimpleNamespace(list_group_ids=AsyncMock(return_value=["g1"]))
    monkeypatch.setattr(
        learning_settings,
        "load",
        lambda _storage_dir: {
            "consolidator": {
                "auto_enabled": True,
                "interval_minutes": 0,
            }
        },
    )
    monkeypatch.setenv("EBR_ENABLED", "false")
    monkeypatch.setattr(dream_plugin_module.time, "monotonic", lambda: 100.0)
    plugin = DreamPlugin()
    cast(Any, plugin)._last_consolidator_monotonic = 0.0
    ctx = SimpleNamespace(
        birthday_greeter=None,
        storage_dir="storage",
        memory_consolidator=consolidator,
        msg_log=message_log,
        mood_engine=None,
    )

    await plugin.on_tick(cast(Any, ctx))

    consolidator.run_once.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_consolidator_has_independent_lifecycle_owner() -> None:
    """Memory consolidation must remain schedulable without constructing DreamPlugin."""

    owner_type = getattr(memory_consolidator_module, "MemoryConsolidatorLifecycle", None)
    assert callable(owner_type), "services.memory_consolidator must export MemoryConsolidatorLifecycle"

    consolidator = SimpleNamespace(run_once=AsyncMock())
    message_log = SimpleNamespace(list_group_ids=AsyncMock(return_value=["g1"]))
    ctx = SimpleNamespace(
        storage_dir="storage",
        memory_consolidator=consolidator,
        msg_log=message_log,
        mood_engine=None,
    )
    owner = owner_type(
        ctx=ctx,
        settings_loader=lambda _storage_dir: {
            "consolidator": {
                "auto_enabled": True,
                "interval_minutes": 0,
            }
        },
        monotonic=lambda: 100.0,
        event_boundary_enabled=lambda: False,
    )
    tick_once = getattr(owner, "tick_once", None)
    assert callable(tick_once), "MemoryConsolidatorLifecycle must expose async tick_once()"
    assert callable(getattr(owner, "start", None))
    assert callable(getattr(owner, "stop", None))

    await cast(Any, tick_once)()

    consolidator.run_once.assert_awaited_once_with(
        group_id="g1",
        triggered_by="periodic_tick",
        max_batches=1,
        batch_size=30,
    )


@pytest.mark.asyncio
async def test_memory_consolidator_stop_survives_cancelled_caller() -> None:
    started = asyncio.Event()
    cancellation_cleanup = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def blocked_loop() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancellation_cleanup.set()
            await release_cleanup.wait()

    owner = memory_consolidator_module.MemoryConsolidatorLifecycle(
        ctx=SimpleNamespace(storage_dir="storage"),
        settings_loader=lambda _storage_dir: {},
        monotonic=lambda: 0.0,
        event_boundary_enabled=lambda: False,
    )
    cast(Any, owner)._loop = blocked_loop
    await owner.start()
    await started.wait()

    first_stop = asyncio.create_task(owner.stop())
    await cancellation_cleanup.wait()
    first_stop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_stop

    release_cleanup.set()
    await owner.stop()

    assert owner._task is None
    assert owner._stop_task is not None and owner._stop_task.done()
    assert owner._stopped is True
    assert owner._stopping is False


@pytest.mark.asyncio
async def test_dream_plugin_publishes_and_unpublishes_runtime_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DreamPlugin must publish the exact running handle consumed by Admin."""

    agent = SimpleNamespace(stop=AsyncMock())
    monkeypatch.setattr(
        kernel_config,
        "load_plugin_config",
        lambda *_args, **_kwargs: DreamConfig(enabled=True),
    )
    monkeypatch.setattr(dream_plugin_module, "DreamAgent", lambda **_kwargs: agent)
    monkeypatch.setattr(dream_plugin_module, "setup_dream_logger", lambda _log_dir: None)
    ctx = SimpleNamespace(
        config=SimpleNamespace(log=SimpleNamespace(dir="storage/logs")),
        card_store=SimpleNamespace(),
        sticker_store=None,
        prompt_builder=SimpleNamespace(invalidate=lambda: None),
        runtime_state=None,
        vision_client=None,
        schedule_store=None,
        story_arc_store=None,
        msg_log=None,
        mood_engine=None,
        background_task_supervisor=None,
        dream=None,
    )
    plugin = DreamPlugin()

    await plugin.on_startup(cast(Any, ctx))

    assert ctx.dream is agent
    app = FastAPI()
    app.include_router(create_dream_router(dream_agent=ctx.dream), prefix="/api/admin")
    response = TestClient(app).get("/api/admin/dream")
    assert response.status_code == 200
    assert response.json()["available"] is True

    await plugin.on_shutdown(cast(Any, ctx))

    agent.stop.assert_awaited_once()
    assert ctx.dream is None


def test_dream_admin_trigger_uses_public_runtime_handle() -> None:
    """The control plane must call a public one-shot method, not private internals."""

    handle = SimpleNamespace(trigger_once=AsyncMock())
    app = FastAPI()
    app.include_router(create_dream_router(dream_agent=handle), prefix="/api/admin")

    response = TestClient(app).post("/api/admin/dream/trigger")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    handle.trigger_once.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/debug", True),
        ("  /debug save", True),
        ("/debugger", False),
        ("正文里提到 /debug 但不是命令", False),
    ],
)
def test_history_debug_filter_matches_exact_command_boundary(text: str, expected: bool) -> None:
    segments = [{"type": "text", "data": {"text": text}}]

    assert _contains_debug_command(segments) is expected
