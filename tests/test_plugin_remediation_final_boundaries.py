from __future__ import annotations

import importlib
import inspect
import json
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.routes.api.plugins import create_plugins_router
from kernel.bus import PluginBus
from plugins.calendar_context.service import BirthdayEntry, DayContext
from plugins.schedule.generator import ScheduleGenerator
from plugins.schedule.mood import MoodEngine
from services.health import collect_service_health

PLUGIN_ROOT = Path("/Volumes/OmubotDisk/omubot/plugins")
NOW = datetime(2026, 7, 14, 12, 0, 0)


class _ScheduleStoreStub:
    pass


def test_schedule_consumers_fail_closed_without_calendar_service() -> None:
    generator = ScheduleGenerator(
        store=cast(Any, _ScheduleStoreStub()),
        calendar_service=None,
    )
    mood = MoodEngine(calendar_service=None)

    assert generator._day_context(NOW) is None, (
        "ScheduleGenerator must not fall back to plugins.schedule.calendar"
    )
    assert mood._day_context(NOW) is None, (
        "MoodEngine must not fall back to plugins.schedule.calendar"
    )


def test_schedule_package_preserves_canonical_calendar_exports() -> None:
    schedule_package = importlib.import_module("plugins.schedule")
    canonical = importlib.import_module("plugins.calendar_context.service")

    assert hasattr(schedule_package, "BirthdayEntry")
    assert hasattr(schedule_package, "DayContext")
    assert hasattr(schedule_package, "get_day_context")
    assert schedule_package.BirthdayEntry is canonical.BirthdayEntry
    assert schedule_package.DayContext is canonical.DayContext
    assert isinstance(schedule_package.get_day_context(NOW), canonical.DayContext)


def test_retained_schedule_calendar_is_a_canonical_compatibility_shim() -> None:
    try:
        legacy = importlib.import_module("plugins.schedule.calendar")
    except ModuleNotFoundError as exc:
        if exc.name == "plugins.schedule.calendar":
            return
        raise

    canonical = importlib.import_module("plugins.calendar_context.service")

    assert legacy.BirthdayEntry is canonical.BirthdayEntry
    assert legacy.DayContext is canonical.DayContext
    assert isinstance(legacy.get_day_context(NOW), canonical.DayContext)


def test_schedule_calendar_delegates_to_canonical_runtime_provider() -> None:
    legacy = importlib.import_module("plugins.schedule.calendar")
    runtime_spec = find_spec("plugins.calendar_context.runtime")
    assert runtime_spec is not None, "calendar_context must own the active provider registry"
    runtime = importlib.import_module("plugins.calendar_context.runtime")
    publish = getattr(runtime, "publish_calendar_service", None)
    unpublish = getattr(runtime, "unpublish_calendar_service", None)
    assert callable(publish), "calendar_context must own the active provider registry"
    assert callable(unpublish), "calendar_context must own provider cleanup"

    expected = DayContext(
        date="2026-07-14",
        weekday=1,
        day_type="holiday",
        holiday_name="runtime-provider",
    )
    provider = SimpleNamespace(get_day_context=lambda _now: expected)
    publish(provider)
    try:
        assert legacy.get_day_context(NOW) is expected
    finally:
        unpublish(provider)


def test_legacy_identity_reads_active_canonical_service_state() -> None:
    legacy = importlib.import_module("plugins.schedule.calendar")
    runtime = importlib.import_module("plugins.calendar_context.runtime")
    canonical = importlib.import_module("plugins.calendar_context.service")
    service = canonical.CalendarContextService()
    runtime.publish_calendar_service(service)
    service.set_self_names("canonical-name")
    try:
        assert legacy.get_self_name() == "canonical-name"
    finally:
        runtime.unpublish_calendar_service(service)
        runtime.set_calendar_self_names()


@pytest.mark.asyncio
async def test_calendar_plugin_publishes_and_withdraws_compat_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar_plugin = importlib.import_module("plugins.calendar_context.plugin")
    legacy = importlib.import_module("plugins.schedule.calendar")
    config = calendar_plugin.CalendarContextConfig(
        cache_dir=str(tmp_path),
        auto_fetch_missing_year=False,
        official_source_enabled=False,
        fallback_local_holiday_lib=False,
    )
    monkeypatch.setattr(calendar_plugin, "load_plugin_config", lambda *_args: config)
    ctx = SimpleNamespace(
        identity=SimpleNamespace(name="canonical-name"),
        calendar_service=None,
        birthday_greeter=None,
    )
    plugin = calendar_plugin.CalendarContextPlugin()

    await plugin.on_startup(ctx)
    assert legacy.get_day_context(datetime(2026, 10, 1)).holiday_name == "国庆节"
    assert legacy.get_self_name() == "canonical-name"

    await plugin.on_shutdown(ctx)
    assert legacy.get_day_context(datetime(2026, 10, 1)).holiday_name == ""


def test_ordinary_character_birthday_does_not_add_self_birthday_energy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("plugins.schedule.mood.now_cst", lambda: NOW)
    monkeypatch.setattr("plugins.schedule.mood.random.uniform", lambda _start, _end: 0.0)
    monkeypatch.setattr("plugins.schedule.mood.random.random", lambda: 1.0)
    ordinary = DayContext(
        date="2026-07-14",
        weekday=1,
        day_type="school_day",
        birthdays=[BirthdayEntry(name_cn="测试角色", name_jp="test", group="test")],
    )
    neutral = DayContext(date="2026-07-14", weekday=1, day_type="school_day")

    ordinary_profile = MoodEngine(
        anomaly_chance=0.0,
        calendar_service=SimpleNamespace(get_day_context=lambda _now: ordinary),
    ).evaluate(None, recent_interaction_count=1)
    neutral_profile = MoodEngine(
        anomaly_chance=0.0,
        calendar_service=SimpleNamespace(get_day_context=lambda _now: neutral),
    ).evaluate(None, recent_interaction_count=1)

    assert ordinary_profile.energy == neutral_profile.energy


def test_schedule_manifest_requires_calendar_context_service() -> None:
    manifest = json.loads((PLUGIN_ROOT / "schedule" / "plugin.json").read_text())
    required = manifest.get("required_dependencies")

    assert isinstance(required, dict), "required_dependencies must be an object"
    assert "calendar_context" in required, (
        "schedule must declare calendar_context as a required dependency"
    )
    constraint = required["calendar_context"]
    assert isinstance(constraint, str) and constraint.strip(), (
        "calendar_context must have a non-empty version constraint"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pipeline_status", "expected_health"),
    [
        ({"status": "success", "runs": 2, "last_error": ""}, "ok"),
        ({"status": "failed", "runs": 2, "last_error": "backfill failed"}, "error"),
        ({"status": "running", "runs": 2, "last_error": ""}, "ok"),
        ({"status": "idle", "runs": 0, "last_error": ""}, "unknown"),
    ],
)
async def test_service_health_reports_history_backfill_runtime_status(
    pipeline_status: dict[str, Any],
    expected_health: str,
) -> None:
    ctx = SimpleNamespace(
        connection_pipeline=SimpleNamespace(history_backfill_status=pipeline_status)
    )

    payload = await collect_service_health(ctx=ctx)
    history = next(
        (item for item in payload["services"] if item.get("id") == "history_backfill"),
        None,
    )

    assert history is not None, (
        "collect_service_health must expose the history_backfill pipeline service"
    )
    assert history["status"] == expected_health
    if pipeline_status["status"] == "success":
        assert history["meta"] == {
            "status": "success",
            "runs": 2,
            "last_error": "",
        }
    if pipeline_status["status"] == "failed":
        assert any(
            alert.get("source") == "history_backfill"
            for alert in payload.get("alerts", [])
        ), "failed history backfill must produce a history_backfill alert"


def _plugins_client(pipeline_status: dict[str, Any]) -> TestClient:
    parameters = inspect.signature(create_plugins_router).parameters
    assert "ctx" in parameters, (
        "create_plugins_router must accept ctx so capability health is runtime-backed"
    )

    ctx = SimpleNamespace(
        connection_pipeline=SimpleNamespace(history_backfill_status=pipeline_status)
    )
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            bus=PluginBus(),
            ctx=ctx,
            plugin_root=PLUGIN_ROOT,
        ),
        prefix="/api/admin",
    )
    return TestClient(app)


@pytest.mark.parametrize(
    ("pipeline_status", "expected_state"),
    [
        ({"status": "success", "runs": 2, "last_error": ""}, "healthy"),
        (
            {"status": "failed", "runs": 2, "last_error": "backfill failed"},
            "degraded",
        ),
        ({"status": "idle", "runs": 0, "last_error": ""}, "unknown"),
    ],
)
def test_history_loader_plugin_health_uses_pipeline_status_in_list_and_detail(
    pipeline_status: dict[str, Any],
    expected_state: str,
) -> None:
    client = _plugins_client(pipeline_status)

    list_response = client.get("/api/admin/plugins?include_system=true")
    detail_response = client.get("/api/admin/plugins/history_loader")
    assert list_response.status_code == 200
    assert detail_response.status_code == 200

    history_list = next(
        item
        for item in list_response.json()["plugins"]
        if item["name"] == "history_loader"
    )
    history_detail = detail_response.json()

    assert history_list["health"]["state"] == expected_state
    assert history_detail["health"]["state"] == expected_state
    if pipeline_status["status"] == "failed":
        assert pipeline_status["last_error"] in json.dumps(
            history_list,
            ensure_ascii=False,
        )
        assert pipeline_status["last_error"] in json.dumps(
            history_detail,
            ensure_ascii=False,
        )
