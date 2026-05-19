"""LLM pipeline taxonomy.

Groups the 18 :data:`services.llm.llm_request.LLMTask` values into 4
operational pipelines. Used by the admin dashboard cache-hit panel so
operators can spot which pipeline is dragging the cache hit rate down
without having to scan all 18 tasks individually.

Pipelines are stable identifiers — the admin frontend hard-codes the
keys (``core_chat`` / ``slang`` / ``learning`` / ``memory_graph``) in
its TypeScript ``CachePipelineGroup.key`` Literal. A guard test in
``tests/test_llm_pipelines.py`` enforces that every ``LLMTask`` is
assigned to exactly one pipeline; adding a new ``LLMTask`` without
updating this file fails pytest.

The :data:`_CALL_TYPE_ALIASES` mapping is a transitional concern: the
``llm_calls`` table still contains rows written by spine-pre-D-later
call sites with ``call_type='chat'`` or ``'proactive'`` (both folded
into ``main`` semantically). These aliases let the dashboard treat
historical data correctly. Once the legacy buckets stop appearing
(and ``test_chat_records_usage`` / ``test_compact_records_usage`` are
verified to be writing ``main`` / ``compact`` from the spine),
this mapping should be removed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMPipeline:
    """A grouping of :data:`LLMTask` values that share an operational role.

    Attributes
    ----------
    key:
        Stable identifier used by the admin frontend. Must be one of
        the four values in ``CachePipelineGroup.key`` Literal.
    label:
        Chinese label shown in the dashboard pipeline row header.
    tasks:
        Tuple of :data:`LLMTask` values that belong to this pipeline.
        Order is preserved when filling the per-task detail list, so
        operators see related tasks adjacent (e.g. all four slang
        sub-tasks together).
    """

    key: str
    label: str
    tasks: tuple[str, ...]


# Order here drives admin dashboard render order. Keep core_chat first
# — it's the only pipeline that runs on every message and dominates
# the operator's attention.
LLM_PIPELINES: tuple[LLMPipeline, ...] = (
    LLMPipeline(
        "core_chat",
        "主聊天链路",
        ("main", "thinker", "compact", "reply_gate"),
    ),
    LLMPipeline(
        "slang",
        "黑话治理",
        ("slang", "slang_review", "slang_drift", "slang_semantic"),
    ),
    LLMPipeline(
        "learning",
        "学习与工具",
        ("style", "memo", "chat_private", "bilibili_intent", "element_detect", "vision"),
    ),
    LLMPipeline(
        "memory_graph",
        "多层记忆 (预留)",
        ("graph_review", "graph_edge_classifier", "reflection_consolidator", "episode_summarizer"),
    ),
)


# Transitional: legacy ``call_type`` values that should be folded into
# a canonical :data:`LLMTask` for pipeline aggregation. ``chat`` and
# ``proactive`` were the call_type written before the D-later spine
# migration; they all represent main-chat invocations. ``compact``
# stays unchanged. Remove this mapping once historical rows older than
# the spine cutoff are pruned and no fresh rows carry these values.
_CALL_TYPE_ALIASES: Mapping[str, str] = {
    "chat": "main",
    "proactive": "main",
}


def pipeline_for_task(task: str) -> LLMPipeline | None:
    """Return the pipeline that owns ``task``, or None if unclassified.

    Caller decides what to do with unclassified tasks (the dashboard
    endpoint counts them in ``overall`` but excludes them from any
    pipeline).
    """
    for pipeline in LLM_PIPELINES:
        if task in pipeline.tasks:
            return pipeline
    return None


def all_pipeline_tasks() -> set[str]:
    """Return the union of all task names across pipelines.

    Used by the guard test in ``tests/test_llm_pipelines.py`` to verify
    that every ``LLMTask`` Literal value is assigned to exactly one
    pipeline.
    """
    result: set[str] = set()
    for pipeline in LLM_PIPELINES:
        result.update(pipeline.tasks)
    return result


def resolve_call_type(raw: str) -> str:
    """Normalize a raw ``llm_calls.call_type`` value to a canonical task.

    Returns the alias target when ``raw`` matches a known transitional
    value, otherwise returns ``raw`` unchanged. Unknown values are
    returned as-is so the caller can decide whether to drop them.
    """
    return _CALL_TYPE_ALIASES.get(raw, raw)
