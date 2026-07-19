"""Behavior contracts for building the process PluginBus."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from bootstrap import application as application_module
from kernel.background_tasks import BackgroundTaskSupervisor
from kernel.bus import PluginBus

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


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[1] / "plugins"


def _build_plugin_bus(
    *,
    persisted_states: Mapping[str, bool],
    config_disabled: Sequence[str],
) -> PluginBus:
    builder = getattr(application_module, "build_plugin_bus", None)
    assert callable(builder), "build_plugin_bus must be implemented"
    bus: Any = builder(
        plugin_root=_plugin_root(),
        persisted_states=persisted_states,
        config_disabled=config_disabled,
    )
    assert isinstance(bus, PluginBus)
    return bus


def test_build_plugin_bus_preserves_exact_unstarted_plugin_order() -> None:
    bus = _build_plugin_bus(persisted_states={}, config_disabled=[])

    names = [plugin.name for plugin in bus.plugins]
    assert names == EXPECTED_PLUGIN_ORDER
    assert len(names) == 24
    assert len(set(names)) == len(names)
    assert bus.started is False


def test_build_plugin_bus_injects_process_task_supervisor() -> None:
    supervisor = BackgroundTaskSupervisor()
    builder = application_module.build_plugin_bus

    bus = builder(
        plugin_root=_plugin_root(),
        persisted_states={},
        config_disabled=[],
        task_supervisor=supervisor,
    )

    assert bus._task_supervisor is supervisor


def test_build_plugin_bus_applies_persisted_then_config_with_locked_fail_closed() -> None:
    bus = _build_plugin_bus(
        persisted_states={
            "echo": True,
            "affection": False,
            "chat": False,
            "context": False,
            "history_loader": False,
            "unknown_persisted": False,
        },
        config_disabled=[
            "echo",
            "chat",
            "context",
            "history_loader",
            "unknown_config",
        ],
    )

    echo = bus.get_plugin("echo")
    affection = bus.get_plugin("affection")
    assert echo is not None
    assert echo.enabled is False
    assert affection is not None
    assert affection.enabled is False
    for locked_name in ("chat", "context"):
        plugin = bus.get_plugin(locked_name)
        assert plugin is not None
        assert bus.is_plugin_locked(plugin) is True
        assert plugin.enabled is True
    assert bus.get_plugin("history_loader") is None
    assert bus.get_plugin("unknown_persisted") is None
    assert bus.get_plugin("unknown_config") is None
