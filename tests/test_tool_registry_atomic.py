"""Behavior contracts for atomic ToolRegistry extension."""

from collections.abc import Iterable
from typing import Any

import pytest

from kernel.types import Tool as KernelTool
from kernel.types import ToolContext as KernelToolContext
from services.tools.base import Tool
from services.tools.context import ToolContext
from services.tools.registry import ToolRegistry


class _NamedTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        return "ok"


def test_service_tool_imports_reexport_the_kernel_abi() -> None:
    assert Tool is KernelTool
    assert ToolContext is KernelToolContext


def _merge_all(registry: ToolRegistry, tools: Iterable[Tool]) -> None:
    merge_all = getattr(registry, "merge_all", None)
    assert callable(merge_all), "ToolRegistry.merge_all must be implemented"
    merge_all(tools)


def _tool_names(registry: ToolRegistry) -> list[str]:
    return [item["function"]["name"] for item in registry.to_openai_tools()]


def test_merge_all_preserves_existing_core_tools() -> None:
    core_clock = _NamedTool("core_clock")
    core_search = _NamedTool("core_search")
    extension = _NamedTool("extension_lookup")
    registry = ToolRegistry()
    registry.register(core_clock)
    registry.register(core_search)

    _merge_all(registry, [extension])

    assert registry.get(core_clock.name) is core_clock
    assert registry.get(core_search.name) is core_search
    assert registry.get(extension.name) is extension


@pytest.mark.parametrize(
    "candidates",
    [
        pytest.param(
            [_NamedTool("candidate"), _NamedTool("candidate")],
            id="duplicate-within-candidates",
        ),
        pytest.param(
            [_NamedTool("candidate"), _NamedTool("core_clock")],
            id="duplicate-with-existing-registry",
        ),
    ],
)
def test_merge_all_duplicate_rejection_leaves_registry_unchanged(
    candidates: list[Tool],
) -> None:
    core_clock = _NamedTool("core_clock")
    core_search = _NamedTool("core_search")
    registry = ToolRegistry()
    registry.register(core_clock)
    registry.register(core_search)
    names_before = _tool_names(registry)

    with pytest.raises(ValueError, match="already registered"):
        _merge_all(registry, candidates)

    assert _tool_names(registry) == names_before
    assert registry.get(core_clock.name) is core_clock
    assert registry.get(core_search.name) is core_search
    assert registry.get("candidate") is None


def test_merge_all_appends_after_existing_tools_in_candidate_order() -> None:
    core_clock = _NamedTool("core_clock")
    core_search = _NamedTool("core_search")
    extension_lookup = _NamedTool("extension_lookup")
    extension_write = _NamedTool("extension_write")
    registry = ToolRegistry()
    registry.register(core_clock)
    registry.register(core_search)

    _merge_all(registry, [extension_lookup, extension_write])

    assert _tool_names(registry) == [
        "core_clock",
        "core_search",
        "extension_lookup",
        "extension_write",
    ]


def test_registry_generation_changes_only_after_successful_catalog_mutation() -> None:
    registry = ToolRegistry()
    snapshot_catalog = getattr(registry, "snapshot_catalog", None)
    assert callable(snapshot_catalog)
    assert snapshot_catalog() == (0, ())

    first = _NamedTool("first")
    registry.register(first)
    assert snapshot_catalog() == (1, (first,))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(_NamedTool("first"))
    assert snapshot_catalog() == (1, (first,))

    registry.merge_all(())
    assert snapshot_catalog() == (1, (first,))

    replacement = _NamedTool("replacement")
    registry.replace_all((replacement,))
    assert snapshot_catalog() == (2, (replacement,))

    registry.clear()
    assert snapshot_catalog() == (3, ())
    registry.clear()
    assert snapshot_catalog() == (3, ())
