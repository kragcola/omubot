"""Contracts for exactly-once installation of the public Router entrypoint."""

from __future__ import annotations

from typing import Any

import pytest

from kernel import router as router_module


class _Driver:
    def __init__(self) -> None:
        self.startup_callbacks: list[Any] = []
        self.shutdown_callbacks: list[Any] = []
        self.connect_callbacks: list[Any] = []
        self.message_callbacks: list[Any] = []
        self.notice_callbacks: list[Any] = []

    @property
    def callback_counts(self) -> tuple[int, int, int, int, int]:
        return (
            len(self.startup_callbacks),
            len(self.shutdown_callbacks),
            len(self.connect_callbacks),
            len(self.message_callbacks),
            len(self.notice_callbacks),
        )


class _NoRegistrationDriver(_Driver):
    def on_startup(self, callback: Any) -> Any:
        raise AssertionError("setup_routers registered callbacks before claiming driver")


class _ClaimIntercept(Exception):
    pass


def _claim(driver: _Driver) -> None:
    claim = getattr(router_module, "claim_router_install", None)
    assert callable(claim), "claim_router_install must be implemented"
    claim(driver)


def _claim_from_setup_mode(driver: _Driver, mode: str) -> None:
    if mode == "legacy":
        _claim(driver)
        return
    if mode == "runtime":
        _claim(driver)
        return
    raise ValueError(f"unknown setup mode: {mode}")


@pytest.mark.parametrize(
    ("first_mode", "second_mode"),
    [
        pytest.param("legacy", "legacy", id="legacy-to-legacy"),
        pytest.param("legacy", "runtime", id="legacy-to-runtime"),
        pytest.param("runtime", "legacy", id="runtime-to-legacy"),
    ],
)
def test_claim_router_install_rejects_every_mixed_duplicate_mode(
    first_mode: str,
    second_mode: str,
) -> None:
    driver = _Driver()

    _claim_from_setup_mode(driver, first_mode)
    callbacks_after_first = driver.callback_counts

    with pytest.raises(
        RuntimeError,
        match="already installed",
    ):
        _claim_from_setup_mode(driver, second_mode)

    assert callbacks_after_first == (0, 0, 0, 0, 0)
    assert driver.callback_counts == callbacks_after_first


@pytest.mark.parametrize(
    "runtime",
    [pytest.param(None, id="legacy"), pytest.param(object(), id="runtime")],
)
def test_setup_routers_claims_driver_before_any_callback_registration(
    runtime: object | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = getattr(router_module, "claim_router_install", None)
    assert callable(claim), "claim_router_install must be implemented"
    driver = _NoRegistrationDriver()
    claimed: list[_NoRegistrationDriver] = []

    def intercept_claim(candidate: _NoRegistrationDriver) -> None:
        claimed.append(candidate)
        raise _ClaimIntercept

    monkeypatch.setattr(router_module, "claim_router_install", intercept_claim)
    monkeypatch.setattr(router_module, "get_driver", lambda: driver)

    with pytest.raises(_ClaimIntercept):
        router_module.setup_routers(
            bus=object(),  # type: ignore[arg-type]
            ctx=object(),  # type: ignore[arg-type]
            runtime=runtime,
        )

    assert claimed == [driver]
    assert driver.callback_counts == (0, 0, 0, 0, 0)
