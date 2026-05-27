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
    build_cache_pipelines_payload,
    fold_recent_into_pipelines,
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
    main_pipeline = pipeline_for_task("main")
    slang_pipeline = pipeline_for_task("slang_review")
    memo_pipeline = pipeline_for_task("memo")
    graph_pipeline = pipeline_for_task("graph_review")
    assert main_pipeline is not None
    assert slang_pipeline is not None
    assert memo_pipeline is not None
    assert graph_pipeline is not None
    assert main_pipeline.key == "core_chat"
    assert slang_pipeline.key == "slang"
    assert memo_pipeline.key == "learning"
    assert graph_pipeline.key == "memory_graph"
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


def _empty_pipeline_payload() -> dict:
    """Helper: build a fresh payload from no rows so each test gets its own."""
    return build_cache_pipelines_payload([])


def test_fold_recent_into_pipelines_assigns_samples() -> None:
    """Per-call_type rows fold into the right pipeline buckets, sorted DESC, capped at limit."""
    # Multiple rows per call_type with controllable timestamps. Each call_type
    # has 6 rows so we can verify the per-pipeline limit (5) trims correctly
    # AFTER folding multiple call_types together.
    recent_per_call_type = [
        # core_chat: main + thinker (would be 6+6=12 in the bucket; trim to 5)
        {"ts": "2026-05-19T13:00:11Z", "call_type": "main",       "hit": 800, "miss": 200},
        {"ts": "2026-05-19T13:00:10Z", "call_type": "main",       "hit": 700, "miss": 300},
        {"ts": "2026-05-19T13:00:09Z", "call_type": "main",       "hit": 0,   "miss": 0},     # null hit_pct
        {"ts": "2026-05-19T13:00:08Z", "call_type": "main",       "hit": 900, "miss": 100},
        {"ts": "2026-05-19T13:00:07Z", "call_type": "main",       "hit": 500, "miss": 500},
        {"ts": "2026-05-19T13:00:12Z", "call_type": "thinker",    "hit": 100, "miss": 900},
        {"ts": "2026-05-19T13:00:06Z", "call_type": "thinker",    "hit": 400, "miss": 600},
        # legacy alias chat → main; should fold into core_chat bucket
        {"ts": "2026-05-19T13:00:13Z", "call_type": "chat",       "hit": 1000, "miss": 0},
        # slang pipeline
        {"ts": "2026-05-19T13:00:05Z", "call_type": "slang_review", "hit": 0,   "miss": 700},
        {"ts": "2026-05-19T13:00:04Z", "call_type": "slang",        "hit": 200, "miss": 600},
        # learning pipeline (single row)
        {"ts": "2026-05-19T13:00:03Z", "call_type": "memo", "hit": 50, "miss": 950},
        # unknown call_type — must be dropped (no pipeline owns it)
        {"ts": "2026-05-19T13:00:02Z", "call_type": "dream", "hit": 1000, "miss": 0},
    ]
    recent_overall = recent_per_call_type[:6]  # not the focus of this test

    payload = _empty_pipeline_payload()
    folded = fold_recent_into_pipelines(payload, recent_per_call_type, recent_overall, limit=5)

    pipelines = {p["key"]: p for p in folded["pipelines"]}

    # core_chat: 8 source rows (5 main + 2 thinker + 1 chat-alias). Capped at 5.
    cc = pipelines["core_chat"]["recent"]
    assert cc["calls"] == 5
    assert len(cc["samples"]) == 5
    # Sorted by ts DESC across the whole pipeline.
    timestamps = [s["ts"] for s in cc["samples"]]
    assert timestamps == sorted(timestamps, reverse=True)
    # Newest sample is the chat-alias row, but task name is normalized to "main"
    assert cc["samples"][0]["ts"] == "2026-05-19T13:00:13Z"
    assert cc["samples"][0]["task"] == "main"  # alias resolved
    # Weighted hit_pct = sum(hit) / sum(hit+miss) over the kept 5 rows.
    kept_rows = [
        (1000, 0),    # 13:13 chat alias
        (100, 900),   # 13:12 thinker
        (800, 200),   # 13:11 main
        (700, 300),   # 13:10 main
        (0, 0),       # 13:09 main
    ]
    total_hit = sum(h for h, _ in kept_rows)
    total_miss = sum(m for _, m in kept_rows)
    assert cc["hit_tokens"] == total_hit
    assert cc["miss_tokens"] == total_miss
    assert cc["hit_pct"] == total_hit / (total_hit + total_miss)

    # Per-call null hit_pct preserved when hit + miss == 0
    null_sample = next(s for s in cc["samples"] if s["ts"] == "2026-05-19T13:00:09Z")
    assert null_sample["hit_pct"] is None

    # slang pipeline: 2 rows, both kept, sorted DESC.
    sl = pipelines["slang"]["recent"]
    assert sl["calls"] == 2
    assert [s["ts"] for s in sl["samples"]] == [
        "2026-05-19T13:00:05Z", "2026-05-19T13:00:04Z",
    ]
    # weighted: hit=200, miss=1300 → 200/1500
    assert sl["hit_pct"] == 200 / 1500

    # learning: only 1 row.
    le = pipelines["learning"]["recent"]
    assert le["calls"] == 1
    assert le["samples"][0]["task"] == "memo"

    # memory_graph: no rows ever land here in this fixture.
    mg = pipelines["memory_graph"]["recent"]
    assert mg["calls"] == 0
    assert mg["samples"] == []
    assert mg["hit_pct"] is None


def test_fold_recent_overall_handles_zero_denominator_samples() -> None:
    """overall.recent computes weighted pct + samples carry per-call null."""
    recent_overall = [
        {"ts": "2026-05-19T12:00:05Z", "call_type": "main",         "hit": 800, "miss": 200},  # 80%
        {"ts": "2026-05-19T12:00:04Z", "call_type": "thinker",      "hit": 0,   "miss": 0},    # null
        {"ts": "2026-05-19T12:00:03Z", "call_type": "slang_review", "hit": 0,   "miss": 500},  # 0%
        {"ts": "2026-05-19T12:00:02Z", "call_type": "memo",         "hit": 100, "miss": 0},    # 100%
        {"ts": "2026-05-19T12:00:01Z", "call_type": "main",         "hit": 0,   "miss": 0},    # null
    ]

    payload = _empty_pipeline_payload()
    folded = fold_recent_into_pipelines(payload, [], recent_overall, limit=5)

    overall_recent = folded["overall"]["recent"]
    assert overall_recent["calls"] == 5
    # Newest first.
    assert [s["ts"] for s in overall_recent["samples"]] == [
        "2026-05-19T12:00:05Z",
        "2026-05-19T12:00:04Z",
        "2026-05-19T12:00:03Z",
        "2026-05-19T12:00:02Z",
        "2026-05-19T12:00:01Z",
    ]
    # Per-call null preserved.
    pcts = [s["hit_pct"] for s in overall_recent["samples"]]
    assert pcts[0] == 0.8
    assert pcts[1] is None
    assert pcts[2] == 0.0
    assert pcts[3] == 1.0
    assert pcts[4] is None

    # Weighted: hit=900, miss=700 → 900/1600 (the two zero-denom samples
    # contribute nothing to numerator or denominator, exactly like
    # build_cache_pipelines_payload's hit_pct).
    assert overall_recent["hit_tokens"] == 900
    assert overall_recent["miss_tokens"] == 700
    assert overall_recent["hit_pct"] == 900 / 1600


def test_fold_recent_into_pipelines_empty_inputs_yield_empty_recent() -> None:
    """No recent data anywhere → every pipeline + overall has calls=0, samples=[], hit_pct=None."""
    payload = _empty_pipeline_payload()
    folded = fold_recent_into_pipelines(payload, [], [], limit=5)
    for p in folded["pipelines"]:
        assert p["recent"]["calls"] == 0
        assert p["recent"]["samples"] == []
        assert p["recent"]["hit_pct"] is None
    assert folded["overall"]["recent"]["calls"] == 0
    assert folded["overall"]["recent"]["samples"] == []
    assert folded["overall"]["recent"]["hit_pct"] is None
