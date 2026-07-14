"""Contracts for late AffectionEngine dependency wiring."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from bootstrap import chat_runtime as chat_runtime_module


class _AffectionEngineProbe:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls

    def set_group_memory_config(self, config: object) -> None:
        self._calls.append(("engine.group_memory", config))

    def set_runtime_state_bus(self, bus: object | None) -> None:
        self._calls.append(("engine.runtime_state", bus))


def _load_and_wire(
    ctx: object,
    *,
    config_path: str,
    loader: Callable[[str], object],
) -> object:
    helper = getattr(chat_runtime_module, "load_and_wire_group_memory_config", None)
    assert callable(helper), "load_and_wire_group_memory_config must be implemented"
    result: Any = helper(ctx, config_path=config_path, loader=loader)
    return result


def test_load_and_wire_group_memory_config_injects_affection_dependencies() -> None:
    calls: list[tuple[str, object]] = []
    group_memory_config = object()
    runtime_state_bus = object()
    engine = _AffectionEngineProbe(calls)
    ctx = SimpleNamespace(
        affection_engine=engine,
        group_memory_config=None,
        runtime_state=runtime_state_bus,
    )
    config_path = "test/group-memory.json"

    def loader(path: str) -> object:
        calls.append(("loader", path))
        return group_memory_config

    result = _load_and_wire(ctx, config_path=config_path, loader=loader)

    assert result is group_memory_config
    assert ctx.group_memory_config is group_memory_config
    assert calls == [
        ("loader", config_path),
        ("engine.group_memory", group_memory_config),
        ("engine.runtime_state", runtime_state_bus),
    ]


def test_load_and_wire_group_memory_config_handles_disabled_affection() -> None:
    calls: list[tuple[str, object]] = []
    group_memory_config = object()
    ctx = SimpleNamespace(
        affection_engine=None,
        group_memory_config=None,
        runtime_state=object(),
    )
    config_path = "test/group-memory-disabled.json"

    def loader(path: str) -> object:
        calls.append(("loader", path))
        return group_memory_config

    result = _load_and_wire(ctx, config_path=config_path, loader=loader)

    assert result is group_memory_config
    assert ctx.group_memory_config is group_memory_config
    assert calls == [("loader", config_path)]
