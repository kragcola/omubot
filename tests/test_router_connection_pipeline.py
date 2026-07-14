"""Behavior contracts for Router connection-event delegation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from kernel import router as router_module

ConnectionCallback = Callable[[object], Awaitable[None]]


class _ConnectionDriver:
    def __init__(self) -> None:
        self.connect_callbacks: list[ConnectionCallback] = []
        self.disconnect_callbacks: list[ConnectionCallback] = []

    def on_bot_connect(self, callback: ConnectionCallback) -> ConnectionCallback:
        self.connect_callbacks.append(callback)
        return callback

    def on_bot_disconnect(self, callback: ConnectionCallback) -> ConnectionCallback:
        self.disconnect_callbacks.append(callback)
        return callback


class _ConnectOnlyDriver:
    def __init__(self) -> None:
        self.connect_callbacks: list[ConnectionCallback] = []

    def on_bot_connect(self, callback: ConnectionCallback) -> ConnectionCallback:
        self.connect_callbacks.append(callback)
        return callback


class _Pipeline:
    """Minimal boundary fake with no PluginContext business fields."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def on_connect(self, event: object) -> None:
        self.calls.append(("connect", event))

    async def on_disconnect(self, event: object) -> None:
        self.calls.append(("disconnect", event))

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"connection wrapper accessed pipeline business field: {name}")


def _install(driver: object, pipeline: _Pipeline) -> None:
    installer = getattr(router_module, "install_connection_handlers", None)
    assert callable(installer), "install_connection_handlers must be implemented"
    installer(driver, pipeline)


@pytest.mark.asyncio
async def test_connection_handlers_register_once_and_forward_events_in_order() -> None:
    driver = _ConnectionDriver()
    pipeline = _Pipeline()
    connected_bot = object()
    disconnected_bot = object()

    _install(driver, pipeline)

    assert len(driver.connect_callbacks) == 1
    assert len(driver.disconnect_callbacks) == 1

    await driver.connect_callbacks[0](connected_bot)
    await driver.disconnect_callbacks[0](disconnected_bot)

    assert pipeline.calls == [
        ("connect", connected_bot),
        ("disconnect", disconnected_bot),
    ]
    assert pipeline.calls[0][1] is connected_bot
    assert pipeline.calls[1][1] is disconnected_bot


@pytest.mark.asyncio
async def test_connection_handlers_install_when_driver_has_no_disconnect_hook() -> None:
    driver = _ConnectOnlyDriver()
    pipeline = _Pipeline()
    connected_bot = object()

    _install(driver, pipeline)

    assert len(driver.connect_callbacks) == 1
    await driver.connect_callbacks[0](connected_bot)
    assert pipeline.calls == [("connect", connected_bot)]
    assert pipeline.calls[0][1] is connected_bot
