"""Behavior contracts for the process Router lifecycle seam."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from kernel import router as router_module

LifecycleCallback = Callable[[], Awaitable[None]]


class _LifecycleDriver:
    def __init__(self) -> None:
        self.startup_callbacks: list[LifecycleCallback] = []
        self.shutdown_callbacks: list[LifecycleCallback] = []

    def on_startup(self, callback: LifecycleCallback) -> LifecycleCallback:
        self.startup_callbacks.append(callback)
        return callback

    def on_shutdown(self, callback: LifecycleCallback) -> LifecycleCallback:
        self.shutdown_callbacks.append(callback)
        return callback


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def start(self) -> None:
        self.calls.append("runtime.start")

    async def stop(self) -> None:
        self.calls.append("runtime.stop")


def _install(driver: _LifecycleDriver, runtime: _Runtime) -> None:
    installer = getattr(router_module, "install_lifecycle_handlers", None)
    assert callable(installer), "install_lifecycle_handlers must be implemented"
    installer(driver, runtime)


@pytest.mark.asyncio
async def test_install_lifecycle_handlers_registers_once_and_delegates_exactly() -> None:
    driver = _LifecycleDriver()
    runtime = _Runtime()

    _install(driver, runtime)

    assert len(driver.startup_callbacks) == 1
    assert len(driver.shutdown_callbacks) == 1

    await driver.startup_callbacks[0]()
    await driver.shutdown_callbacks[0]()

    assert runtime.calls == ["runtime.start", "runtime.stop"]


def test_install_lifecycle_handlers_rejects_duplicate_runtime_install() -> None:
    driver = _LifecycleDriver()
    runtime = _Runtime()
    _install(driver, runtime)

    with pytest.raises(RuntimeError, match="already installed"):
        _install(driver, runtime)

    assert len(driver.startup_callbacks) == 1
    assert len(driver.shutdown_callbacks) == 1
