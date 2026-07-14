"""RED contracts for command registry lifecycle and owner state.

These tests intentionally construct the dispatcher before lifecycle changes.
The observable contract is that a command can never outlive its plugin owner,
even when startup dependency resolution or a runtime toggle changes that
owner after the dispatcher was created.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from kernel.bus import PluginBus
from kernel.types import AmadeusPlugin, Command, PluginContext
from services.command import CommandDispatcher
from services.plugin_state import PluginStateStore
from services.plugin_toggle import PluginToggleService
from services.tools.registry import ToolRegistry


class _CommandPlugin(AmadeusPlugin):
    """Small command owner used to exercise the real bus/dispatcher APIs."""

    def __init__(
        self,
        name: str,
        *,
        enabled: bool = True,
        version: str = "1.0.0",
        required_dependencies: dict[str, str] | None = None,
        startup_error: bool = False,
    ) -> None:
        super().__init__()
        self.name = name
        self.enabled = enabled
        self.version = version
        self.priority = 10
        self.required_dependencies = required_dependencies or {}
        self.startup_error = startup_error
        self.startup_calls = 0
        self.handler = AsyncMock()

    def register_commands(self) -> list[Command]:
        return [Command(name=self.name, handler=self.handler)]

    async def on_startup(self, ctx: PluginContext) -> None:
        del ctx
        self.startup_calls += 1
        if self.startup_error:
            raise RuntimeError(f"{self.name} startup failed")


def _plugin_ctx() -> PluginContext:
    return PluginContext()


def _dispatch(dispatcher: CommandDispatcher, text: str) -> tuple[bool, AsyncMock]:
    bot = SimpleNamespace(send=AsyncMock())
    event = SimpleNamespace()
    plugin_ctx = SimpleNamespace(config=SimpleNamespace(admins={}))
    matched = asyncio.run(
        dispatcher.dispatch(
            bot,
            event,
            text,
            is_private=False,
            user_id="u1",
            group_id="g1",
            plugin_ctx=plugin_ctx,
        )
    )
    return matched, bot.send


def _assert_unknown_consumed(
    matched: bool,
    send: AsyncMock,
    token: str,
) -> None:
    """D-02: stale owner names become a consumed, user-facing unknown slash."""

    assert matched is True
    send.assert_awaited_once()
    assert send.await_args is not None
    assert token in str(send.await_args)


def _toggle_service(
    bus: PluginBus,
    tmp_path: Path,
) -> PluginToggleService:
    return PluginToggleService(
        bus=bus,
        tool_registry=ToolRegistry(),
        plugin_state_store=PluginStateStore(tmp_path / "plugin-state.json"),
        is_locked=PluginBus.is_plugin_locked,
        serialize_plugin=lambda plugin: {
            "name": plugin.name,
            "enabled": plugin.enabled,
        },
    )


def test_command_dispatch_rechecks_owner_after_startup_dependency_failure() -> None:
    """A pre-start dispatcher must not retain a dependency-blocked command."""

    provider = _CommandPlugin("provider", startup_error=True)
    consumer = _CommandPlugin(
        "consumer",
        required_dependencies={"provider": ">=1.0.0"},
    )
    bus = PluginBus()
    bus.register(provider)
    bus.register(consumer)

    # This is the production ordering that exposed the stale snapshot: the
    # dispatcher is assembled before PluginBus startup resolves dependencies.
    dispatcher = CommandDispatcher(bus)
    asyncio.run(bus.fire_on_startup(_plugin_ctx()))

    assert provider.enabled is False
    assert consumer.enabled is False
    assert bus.collect_commands() == []

    matched, send = _dispatch(dispatcher, "/consumer")

    _assert_unknown_consumed(matched, send, "/consumer")
    consumer.handler.assert_not_awaited()


def test_command_dispatch_rechecks_owner_after_runtime_toggle(tmp_path: Path) -> None:
    """Runtime toggle must replace command state in the same transaction."""

    plugin = _CommandPlugin("toggleable")
    bus = PluginBus()
    bus.register(plugin)
    dispatcher = CommandDispatcher(bus)
    asyncio.run(bus.fire_on_startup(_plugin_ctx()))

    payload = _toggle_service(bus, tmp_path).toggle("toggleable", False)

    assert payload["ok"] is True
    assert plugin.enabled is False
    assert bus.collect_commands() == []

    matched, send = _dispatch(dispatcher, "/toggleable")

    _assert_unknown_consumed(matched, send, "/toggleable")
    plugin.handler.assert_not_awaited()


def test_command_dispatch_does_not_resurrect_boot_disabled_owner() -> None:
    """A command collected before startup must honor a disabled owner after it."""

    plugin = _CommandPlugin("boot_disabled", enabled=False)
    bus = PluginBus()
    bus.register(plugin)
    dispatcher = CommandDispatcher(bus)
    asyncio.run(bus.fire_on_startup(_plugin_ctx()))

    assert bus.collect_commands() == []
    matched, send = _dispatch(dispatcher, "/boot_disabled")

    _assert_unknown_consumed(matched, send, "/boot_disabled")
    plugin.handler.assert_not_awaited()


def test_runtime_enable_rejects_dependency_blocked_owner_that_never_started(
    tmp_path: Path,
) -> None:
    """A synchronous toggle cannot expose a plugin whose startup never ran."""

    provider = _CommandPlugin("provider", enabled=False)
    consumer = _CommandPlugin(
        "consumer",
        required_dependencies={"provider": ">=1.0.0"},
    )
    bus = PluginBus()
    bus.register(provider)
    bus.register(consumer)
    dispatcher = CommandDispatcher(bus)
    asyncio.run(bus.fire_on_startup(_plugin_ctx()))

    assert provider.startup_calls == 1
    assert consumer.startup_calls == 0
    service = _toggle_service(bus, tmp_path)
    assert service.toggle("provider", True)["ok"] is True

    payload = service.toggle("consumer", True)
    matched, send = _dispatch(dispatcher, "/consumer")

    assert payload["ok"] is False
    assert consumer.enabled is False
    _assert_unknown_consumed(matched, send, "/consumer")
    consumer.handler.assert_not_awaited()
