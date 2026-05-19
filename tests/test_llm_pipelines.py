"""Guard tests for the LLM pipeline taxonomy.

Ensures every :data:`LLMTask` Literal value is assigned to exactly one
pipeline in :data:`LLM_PIPELINES`. Adding a new ``LLMTask`` without
updating ``services/llm/llm_pipelines.py`` will fail the first test;
this is the same shape of guard we use in
``tests/test_llm_task_admin_sync.py`` for the admin TS Literal.
"""

from __future__ import annotations

from collections import Counter

from services.llm.llm_pipelines import (
    LLM_PIPELINES,
    all_pipeline_tasks,
    pipeline_for_task,
    resolve_call_type,
)
from services.llm.llm_request import all_llm_tasks


def test_all_llm_tasks_are_covered_by_pipelines() -> None:
    py_tasks = set(all_llm_tasks())
    pipeline_tasks = all_pipeline_tasks()
    missing = py_tasks - pipeline_tasks
    extra = pipeline_tasks - py_tasks
    assert not missing, (
        f"LLMTask(s) not assigned to any pipeline: {sorted(missing)}. "
        f"Add them to one of the LLM_PIPELINES tuples in "
        f"services/llm/llm_pipelines.py."
    )
    assert not extra, (
        f"Pipeline references unknown task name(s) (not in LLMTask): "
        f"{sorted(extra)}"
    )


def test_pipelines_have_no_overlap() -> None:
    """Each task must belong to exactly one pipeline."""
    counts = Counter()
    for pipeline in LLM_PIPELINES:
        counts.update(pipeline.tasks)
    duplicates = {task: n for task, n in counts.items() if n > 1}
    assert not duplicates, (
        f"Task(s) appear in multiple pipelines: {duplicates}. "
        f"Each LLMTask must belong to exactly one pipeline."
    )


def test_pipeline_keys_are_unique_and_stable() -> None:
    """Frontend hard-codes these four keys in CachePipelineGroup.key Literal."""
    keys = [pipeline.key for pipeline in LLM_PIPELINES]
    assert len(keys) == len(set(keys)), f"duplicate pipeline keys: {keys}"
    assert set(keys) == {"core_chat", "slang", "learning", "memory_graph"}, (
        f"pipeline keys drifted from frontend Literal: {keys}. "
        f"If you intentionally added/removed a pipeline, update "
        f"admin/frontend/src/views/dashboard/types.ts CachePipelineGroup.key."
    )


def test_pipeline_for_task_returns_owner() -> None:
    assert pipeline_for_task("main").key == "core_chat"
    assert pipeline_for_task("slang_review").key == "slang"
    assert pipeline_for_task("memo").key == "learning"
    assert pipeline_for_task("graph_review").key == "memory_graph"
    assert pipeline_for_task("unknown_task") is None


def test_resolve_call_type_folds_legacy_aliases() -> None:
    # Transitional: spine-pre-D-later main-chat call_type folds to 'main'.
    assert resolve_call_type("chat") == "main"
    assert resolve_call_type("proactive") == "main"
    # Canonical task names pass through untouched.
    assert resolve_call_type("main") == "main"
    assert resolve_call_type("slang_review") == "slang_review"
    # Unknown values pass through (caller decides whether to drop).
    assert resolve_call_type("dream") == "dream"
