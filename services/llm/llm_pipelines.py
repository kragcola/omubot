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
from typing import Any


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


def build_cache_pipelines_payload(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate per-call_type rows into the 4-pipeline payload.

    Takes the output of ``UsageTracker.cache_hit_by_call_type()`` and
    returns the same ``{period, overall, pipelines}`` shape as the
    ``/api/admin/dashboard/cache-pipelines`` REST endpoint. Shared
    between the REST handler and SSE event stream so the computation
    is defined once.
    """

    def _pct(hit: int, miss: int) -> float | None:
        denom = hit + miss
        return (hit / denom) if denom > 0 else None

    pipeline_buckets: dict[str, dict[str, dict[str, int]]] = {}
    for pipeline in LLM_PIPELINES:
        pipeline_buckets[pipeline.key] = {
            task: {"calls": 0, "hit_tokens": 0, "miss_tokens": 0}
            for task in pipeline.tasks
        }

    overall = {"calls": 0, "hit_tokens": 0, "miss_tokens": 0}

    for row in rows:
        raw_call_type = str(row.get("call_type", "") or "")
        if not raw_call_type:
            continue
        calls = int(row.get("calls", 0) or 0)
        hit_tokens = int(row.get("hit_tokens", 0) or 0)
        miss_tokens = int(row.get("miss_tokens", 0) or 0)

        overall["calls"] += calls
        overall["hit_tokens"] += hit_tokens
        overall["miss_tokens"] += miss_tokens

        task = resolve_call_type(raw_call_type)
        pipeline = pipeline_for_task(task)
        if pipeline is None:
            continue

        bucket = pipeline_buckets[pipeline.key][task]
        bucket["calls"] += calls
        bucket["hit_tokens"] += hit_tokens
        bucket["miss_tokens"] += miss_tokens

    pipelines_payload: list[dict[str, Any]] = []
    for pipeline in LLM_PIPELINES:
        per_task: list[dict[str, Any]] = []
        p_calls = p_hit = p_miss = 0
        for task in pipeline.tasks:
            bucket = pipeline_buckets[pipeline.key][task]
            per_task.append({
                "task": task,
                "calls": bucket["calls"],
                "hit_tokens": bucket["hit_tokens"],
                "miss_tokens": bucket["miss_tokens"],
                "hit_pct": _pct(bucket["hit_tokens"], bucket["miss_tokens"]),
            })
            p_calls += bucket["calls"]
            p_hit += bucket["hit_tokens"]
            p_miss += bucket["miss_tokens"]

        pipelines_payload.append({
            "key": pipeline.key,
            "label": pipeline.label,
            "tasks": list(pipeline.tasks),
            "calls": p_calls,
            "hit_tokens": p_hit,
            "miss_tokens": p_miss,
            "hit_pct": _pct(p_hit, p_miss),
            "per_task": per_task,
        })

    return {
        "overall": {
            **overall,
            "hit_pct": _pct(overall["hit_tokens"], overall["miss_tokens"]),
        },
        "pipelines": pipelines_payload,
    }


def fold_recent_into_pipelines(
    payload: dict[str, Any],
    recent_per_call_type: list[dict[str, Any]],
    recent_overall: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Merge per-call_type recent samples into a pipeline payload.

    Mutates ``payload`` (the output of
    :func:`build_cache_pipelines_payload`) in place — adds a ``recent``
    dict to each pipeline (with up to ``limit`` samples re-sorted by ts
    DESC across the pipeline's tasks) and to ``overall`` (the rolling
    last ``limit`` rows period-wide). Returns the same payload for
    chaining.

    ``samples[].hit_pct`` is the per-call ratio
    (``hit / (hit + miss)``); ``recent.hit_pct`` is weighted across the
    samples (``SUM(hit) / SUM(hit + miss)``) so it matches the overall
    pipeline ``hit_pct`` formula. Both are ``None`` when the denominator
    is zero.
    """

    def _sample(row: dict[str, Any]) -> dict[str, Any]:
        hit = int(row.get("hit", 0) or 0)
        miss = int(row.get("miss", 0) or 0)
        denom = hit + miss
        return {
            "ts": str(row.get("ts", "") or ""),
            "task": resolve_call_type(str(row.get("call_type", "") or "")),
            "hit_pct": (hit / denom) if denom > 0 else None,
            "hit_tokens": hit,
            "miss_tokens": miss,
        }

    def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        hit = sum(int(r.get("hit", 0) or 0) for r in rows)
        miss = sum(int(r.get("miss", 0) or 0) for r in rows)
        denom = hit + miss
        return {
            "calls": len(rows),
            "hit_tokens": hit,
            "miss_tokens": miss,
            "hit_pct": (hit / denom) if denom > 0 else None,
            "samples": [_sample(r) for r in rows],
        }

    buckets: dict[str, list[dict[str, Any]]] = {p.key: [] for p in LLM_PIPELINES}
    for row in recent_per_call_type:
        task = resolve_call_type(str(row.get("call_type", "") or ""))
        pipeline = pipeline_for_task(task)
        if pipeline is None:
            continue
        buckets[pipeline.key].append(row)

    for pipeline_payload in payload.get("pipelines", []):
        pipeline_rows = sorted(
            buckets.get(pipeline_payload["key"], []),
            key=lambda r: str(r.get("ts", "") or ""),
            reverse=True,
        )[:limit]
        pipeline_payload["recent"] = _summarize(pipeline_rows)

    overall_rows = sorted(
        recent_overall,
        key=lambda r: str(r.get("ts", "") or ""),
        reverse=True,
    )[:limit]
    payload.setdefault("overall", {})
    payload["overall"]["recent"] = _summarize(overall_rows)

    return payload
