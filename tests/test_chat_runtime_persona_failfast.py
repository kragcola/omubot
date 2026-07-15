from __future__ import annotations

from typing import Any

import pytest

import bootstrap.chat_runtime as chat_runtime


class _PersonaRuntimeStub:
    def __init__(self, *, result: bool = True, error: str = "") -> None:
        self.result = result
        self.last_error = error

    def load(self, persona_id: str) -> bool:
        assert persona_id == "fengxiaomeng-v2"
        return self.result


def _initial_loader() -> Any:
    loader = getattr(chat_runtime, "load_initial_persona_or_raise", None)
    assert callable(loader), "chat runtime must expose a fail-fast initial persona loader"
    return loader


def test_initial_persona_load_failure_aborts_startup() -> None:
    loader = _initial_loader()

    with pytest.raises(RuntimeError, match=r"fengxiaomeng-v2.*compile_failed"):
        loader(
            _PersonaRuntimeStub(result=False, error="compile_failed"),
            "fengxiaomeng-v2",
        )


def test_initial_persona_load_exception_keeps_cause() -> None:
    class _RaisingRuntime(_PersonaRuntimeStub):
        def load(self, persona_id: str) -> bool:
            raise ValueError(f"broken {persona_id}")

    loader = _initial_loader()

    with pytest.raises(RuntimeError, match="fengxiaomeng-v2") as caught:
        loader(_RaisingRuntime(), "fengxiaomeng-v2")

    assert isinstance(caught.value.__cause__, ValueError)


def test_initial_persona_load_success_returns_normally() -> None:
    loader = _initial_loader()

    loader(_PersonaRuntimeStub(), "fengxiaomeng-v2")
