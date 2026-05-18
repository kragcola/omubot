"""Guard tests: keep ``LLMTask`` Literal in sync across the codebase.

The Python ``LLMTask`` Literal in ``services/llm/llm_request.py`` is the single
source of truth for valid task names. Several other places mirror this list:

- ``ProviderTaskKey`` Literal in ``admin/frontend/src/views/system/helpers/types.ts``
  — drives type checking on the admin API payload.
- ``providerTaskOrder`` array in ``admin/frontend/src/views/system/components/SystemProviders.vue``
  — drives the order in which the per-task profile selector renders.
- ``providerTaskLabels`` map in the same file — supplies Chinese labels for each task.

These are easy to drift if a new ``LLMTask`` value is added but the admin UI is
forgotten. This module asserts all four lists agree, parsing the TS / Vue files
directly so the test stays cheap and does not require a Node toolchain.
"""

from __future__ import annotations

import re
from pathlib import Path

from services.llm.llm_request import all_llm_tasks

_REPO = Path(__file__).resolve().parent.parent
_TYPES_TS = _REPO / "admin/frontend/src/views/system/helpers/types.ts"
_PROVIDERS_VUE = _REPO / "admin/frontend/src/views/system/components/SystemProviders.vue"


def _extract_provider_task_key_literal() -> set[str]:
    text = _TYPES_TS.read_text(encoding="utf-8")
    match = re.search(
        r"export type ProviderTaskKey\s*=\s*((?:\s*\|\s*'[^']+')+)",
        text,
    )
    assert match, "ProviderTaskKey Literal not found in types.ts"
    body = match.group(1)
    return set(re.findall(r"'([^']+)'", body))


def _extract_provider_task_order(text: str) -> list[str]:
    match = re.search(
        r"const providerTaskOrder:\s*ProviderTaskKey\[\]\s*=\s*\[(.*?)\]",
        text,
        flags=re.DOTALL,
    )
    assert match, "providerTaskOrder array not found in SystemProviders.vue"
    return re.findall(r"'([^']+)'", match.group(1))


def _extract_provider_task_labels(text: str) -> set[str]:
    match = re.search(
        r"const providerTaskLabels:\s*Record<ProviderTaskKey,\s*string>\s*=\s*\{(.*?)\n\}",
        text,
        flags=re.DOTALL,
    )
    assert match, "providerTaskLabels object not found in SystemProviders.vue"
    body = match.group(1)
    # Object keys are bare identifiers (no quotes) per the existing source.
    return set(re.findall(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:", body, flags=re.MULTILINE))


def test_provider_task_key_matches_llm_task_literal() -> None:
    """Admin TS Literal must list exactly the same tasks as the Python Literal."""
    py_tasks = set(all_llm_tasks())
    ts_tasks = _extract_provider_task_key_literal()
    missing_in_ts = py_tasks - ts_tasks
    extra_in_ts = ts_tasks - py_tasks
    assert not missing_in_ts, (
        f"Task(s) declared in services/llm/llm_request.py LLMTask but not in "
        f"admin/frontend/src/views/system/helpers/types.ts ProviderTaskKey: "
        f"{sorted(missing_in_ts)}"
    )
    assert not extra_in_ts, (
        f"Task(s) declared in admin/frontend/src/views/system/helpers/types.ts "
        f"ProviderTaskKey but not in services/llm/llm_request.py LLMTask: "
        f"{sorted(extra_in_ts)}"
    )


def test_system_providers_vue_task_order_covers_all_tasks() -> None:
    py_tasks = set(all_llm_tasks())
    text = _PROVIDERS_VUE.read_text(encoding="utf-8")
    order = _extract_provider_task_order(text)
    order_set = set(order)
    missing = py_tasks - order_set
    extra = order_set - py_tasks
    assert not missing, (
        f"Task(s) missing from providerTaskOrder in SystemProviders.vue: {sorted(missing)}"
    )
    assert not extra, (
        f"Task(s) in providerTaskOrder not declared in LLMTask: {sorted(extra)}"
    )
    assert len(order) == len(order_set), (
        f"providerTaskOrder contains duplicates: {order}"
    )


def test_system_providers_vue_task_labels_cover_all_tasks() -> None:
    py_tasks = set(all_llm_tasks())
    text = _PROVIDERS_VUE.read_text(encoding="utf-8")
    labels = _extract_provider_task_labels(text)
    missing = py_tasks - labels
    extra = labels - py_tasks
    assert not missing, (
        f"Task(s) missing from providerTaskLabels in SystemProviders.vue: {sorted(missing)}"
    )
    assert not extra, (
        f"Task(s) in providerTaskLabels not declared in LLMTask: {sorted(extra)}"
    )
