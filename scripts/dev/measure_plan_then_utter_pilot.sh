#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

USAGE_DB="${USAGE_DB:-$ROOT/storage/usage.db}"
BLOCK_TRACE_DB="${BLOCK_TRACE_DB:-$ROOT/storage/block_trace.db}"
GROUP_ID="${GROUP_ID:-}"
DAYS="${DAYS:-14}"
COST_RATIO_MAX="${COST_RATIO_MAX:-2.5}"
PERSONA_DRIFT_MAX_PP="${PERSONA_DRIFT_MAX_PP:-5}"
MIN_PILOT_PARENTS="${MIN_PILOT_PARENTS:-20}"

python3 - "$USAGE_DB" "$BLOCK_TRACE_DB" "$GROUP_ID" "$DAYS" "$COST_RATIO_MAX" \
  "$PERSONA_DRIFT_MAX_PP" "$MIN_PILOT_PARENTS" <<'PY'
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from statistics import mean

usage_db = Path(sys.argv[1])
block_trace_db = Path(sys.argv[2])
group_id = sys.argv[3].strip()
days = max(1, int(sys.argv[4]))
cost_ratio_max = float(sys.argv[5])
persona_drift_max_pp = float(sys.argv[6])
min_pilot_parents = max(1, int(sys.argv[7]))


def connect_ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def has_table(conn: sqlite3.Connection | None, table: str) -> bool:
    if conn is None:
        return False
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def avg(values: list[float]) -> float:
    return mean(values) if values else 0.0


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * q))
    return ordered[index]


def fmt(value: float) -> float:
    return round(value, 4)


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def load_json(raw: str | None) -> dict[str, object]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def print_section(title: str, payload: dict[str, object]) -> None:
    print(f"## {title}")
    for key, value in payload.items():
        if isinstance(value, float):
            print(f"{key}: {fmt(value)}")
        elif isinstance(value, dict):
            print(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
        else:
            print(f"{key}: {value}")
    print()


def usage_summary() -> dict[str, object]:
    conn = connect_ro(usage_db)
    if conn is None:
        return {"status": "missing_usage_db"}
    try:
        if not has_table(conn, "llm_calls"):
            return {"status": "missing_llm_calls"}
        where = [
            "call_type IN ('proactive', 'main', 'proactive_plan', 'proactive_utter')",
            "ts >= datetime('now', ?)",
        ]
        params: list[object] = [f"-{days} days"]
        if group_id:
            where.append("group_id = ?")
            params.append(group_id)
        rows = conn.execute(
            f"""
            SELECT call_type, input_tokens, output_tokens, prompt_cache_hit_tokens,
                   prompt_cache_miss_tokens, reasoning_replay_tokens, elapsed_s, error
            FROM llm_calls
            WHERE {' AND '.join(where)}
            """,
            tuple(params),
        ).fetchall()
    finally:
        conn.close()

    by_type: dict[str, dict[str, object]] = {}
    for call_type in ("proactive", "main", "proactive_plan", "proactive_utter"):
        typed = [row for row in rows if str(row["call_type"]) == call_type]
        latencies = [float(row["elapsed_s"] or 0.0) for row in typed]
        errors = [row for row in typed if str(row["error"] or "").strip()]
        input_tokens = sum(int(row["input_tokens"] or 0) for row in typed)
        output_tokens = sum(int(row["output_tokens"] or 0) for row in typed)
        cache_hit = sum(int(row["prompt_cache_hit_tokens"] or 0) for row in typed)
        cache_miss = sum(int(row["prompt_cache_miss_tokens"] or 0) for row in typed)
        replay = sum(int(row["reasoning_replay_tokens"] or 0) for row in typed)
        by_type[call_type] = {
            "count": len(typed),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "prompt_cache_hit_tokens": cache_hit,
            "prompt_cache_miss_tokens": cache_miss,
            "reasoning_replay_tokens": replay,
            "cache_hit_pct": pct(ratio(cache_hit, cache_hit + cache_miss)),
            "elapsed_avg_s": avg(latencies),
            "elapsed_p50_s": percentile(latencies, 0.5),
            "elapsed_p95_s": percentile(latencies, 0.95),
            "error_rate_pct": pct(ratio(len(errors), len(typed))),
        }

    baseline_count = int(by_type["proactive"]["count"]) + int(by_type["main"]["count"])
    baseline_tokens = int(by_type["proactive"]["total_tokens"]) + int(by_type["main"]["total_tokens"])
    plan_count = int(by_type["proactive_plan"]["count"])
    utter_count = int(by_type["proactive_utter"]["count"])
    pilot_count = plan_count + utter_count
    pilot_tokens = (
        int(by_type["proactive_plan"]["total_tokens"])
        + int(by_type["proactive_utter"]["total_tokens"])
    )
    avg_baseline_tokens = ratio(baseline_tokens, baseline_count)
    avg_pilot_tokens = ratio(pilot_tokens, plan_count)
    cost_ratio = ratio(avg_pilot_tokens, avg_baseline_tokens)
    return {
        "status": "ok" if rows else "no_usage_rows",
        "window_days": days,
        "group_filter": group_id or "all",
        "baseline_calls": baseline_count,
        "pilot_plan_calls": plan_count,
        "pilot_utter_calls": utter_count,
        "pilot_calls": pilot_count,
        "avg_baseline_tokens_per_reply": avg_baseline_tokens,
        "avg_pilot_tokens_per_reply": avg_pilot_tokens,
        "pilot_vs_baseline_token_ratio": cost_ratio,
        "cost_gate": (
            "no_sample"
            if plan_count == 0 or baseline_count == 0
            else "yes"
            if cost_ratio <= cost_ratio_max
            else "no"
        ),
        "by_type": by_type,
    }


def trace_summary() -> dict[str, object]:
    conn = connect_ro(block_trace_db)
    if conn is None:
        return {"status": "missing_block_trace_db"}
    try:
        if not has_table(conn, "prompt_block_traces"):
            return {"status": "missing_prompt_block_traces"}
        where = [
            "task IN ('proactive_plan', 'proactive_utter')",
            "created_at >= datetime('now', ?)",
        ]
        params: list[object] = [f"-{days} days"]
        if group_id:
            where.append("json_extract(metadata_json, '$.group_id') = ?")
            params.append(group_id)
        rows = conn.execute(
            f"""
            SELECT task, metadata_json, created_at
            FROM prompt_block_traces
            WHERE {' AND '.join(where)}
            """,
            tuple(params),
        ).fetchall()
    finally:
        conn.close()

    statuses: Counter[str] = Counter()
    by_task: Counter[str] = Counter()
    parent_to_tasks: dict[str, set[str]] = {}
    plan_outlines: list[int] = []
    utter_indices: list[int] = []
    for row in rows:
        task = str(row["task"])
        meta = load_json(row["metadata_json"])
        status = str(meta.get("status") or "unknown")
        parent = str(meta.get("parent_span_id") or "")
        by_task[task] += 1
        statuses[status] += 1
        if parent:
            parent_to_tasks.setdefault(parent, set()).add(task)
        if task == "proactive_plan":
            outlines = meta.get("outlines")
            if isinstance(outlines, list):
                plan_outlines.append(len(outlines))
        if task == "proactive_utter":
            try:
                utter_indices.append(int(meta.get("utter_index", -1)))
            except Exception:
                pass

    paired = sum(
        1 for tasks in parent_to_tasks.values()
        if {"proactive_plan", "proactive_utter"} <= tasks
    )
    return {
        "status": "ok" if rows else "no_pilot_trace_rows",
        "trace_rows": len(rows),
        "distinct_parent_spans": len(parent_to_tasks),
        "paired_parent_spans": paired,
        "by_task": dict(sorted(by_task.items())),
        "status_distribution": dict(sorted(statuses.items())),
        "avg_plan_outline_count": avg([float(value) for value in plan_outlines]),
        "max_utter_index_seen": max(utter_indices) if utter_indices else -1,
        "pairing_gate": (
            "no_sample"
            if not parent_to_tasks
            else "yes"
            if paired == len(parent_to_tasks)
            else "no"
        ),
    }


def persona_summary() -> dict[str, object]:
    conn = connect_ro(block_trace_db)
    if conn is None:
        return {"status": "missing_block_trace_db"}
    try:
        if not has_table(conn, "humanization_metrics"):
            return {
                "status": "missing_humanization_metrics",
                "sample_tasks": 0,
                "persona_score_avg": 0.0,
                "persona_drift_pp": 0.0,
                "persona_gate": "no_sample",
            }
        where = ["created_at >= datetime('now', ?)"]
        params: list[object] = [f"-{days} days"]
        if group_id:
            where.append("group_id = ?")
            params.append(group_id)
        rows = conn.execute(
            f"""
            SELECT score, axes_json, metadata_json, created_at
            FROM humanization_metrics
            WHERE {' AND '.join(where)}
            """,
            tuple(params),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "status": "no_persona_sample",
            "sample_tasks": 0,
            "persona_score_avg": 0.0,
            "persona_drift_pp": 0.0,
            "persona_gate": "no_sample",
        }

    scores = [float(row["score"] or 0.0) for row in rows]
    baseline_scores: list[float] = []
    pilot_scores: list[float] = []
    axes: dict[str, list[float]] = {}
    for row in rows:
        meta = load_json(row["metadata_json"])
        score = float(row["score"] or 0.0)
        path = str(meta.get("generation_path") or meta.get("source") or "")
        if path == "plan_then_utter":
            pilot_scores.append(score)
        else:
            baseline_scores.append(score)
        axes_payload = load_json(row["axes_json"])
        for key, value in axes_payload.items():
            try:
                axes.setdefault(str(key), []).append(float(value))
            except Exception:
                pass
    baseline_avg = avg(baseline_scores)
    pilot_avg = avg(pilot_scores)
    drift_pp = max(0.0, (baseline_avg - pilot_avg) * 100.0) if pilot_scores and baseline_scores else 0.0
    return {
        "status": "ok",
        "sample_tasks": len(rows),
        "baseline_tasks": len(baseline_scores),
        "pilot_tasks": len(pilot_scores),
        "persona_score_avg": avg(scores),
        "baseline_score_avg": baseline_avg,
        "pilot_score_avg": pilot_avg,
        "persona_drift_pp": drift_pp,
        "axis_avg": {key: avg(values) for key, values in sorted(axes.items())},
        "persona_gate": (
            "no_sample"
            if len(rows) < 5 or not pilot_scores or not baseline_scores
            else "yes"
            if drift_pp <= persona_drift_max_pp
            else "no"
        ),
    }


def decision(usage: dict[str, object], traces: dict[str, object], persona: dict[str, object]) -> dict[str, object]:
    pilot_calls = int(usage.get("pilot_calls") or 0)
    parent_spans = int(traces.get("distinct_parent_spans") or 0)
    cost_gate = str(usage.get("cost_gate") or "no_sample")
    pairing_gate = str(traces.get("pairing_gate") or "no_sample")
    persona_gate = str(persona.get("persona_gate") or "no_sample")
    hard_fail = "no" in {cost_gate, pairing_gate, persona_gate}
    enough_sample = pilot_calls > 0 and parent_spans >= min_pilot_parents
    if hard_fail:
        result = "rollback_or_disable_plan_then_utter"
    elif not enough_sample or "no_sample" in {cost_gate, pairing_gate, persona_gate}:
        result = "continue_pilot_collect_samples"
    else:
        result = "eligible_for_p6_11_decision"
    return {
        "decision": result,
        "pilot_calls": pilot_calls,
        "distinct_parent_spans": parent_spans,
        "min_pilot_parent_spans": min_pilot_parents,
        "cost_gate": cost_gate,
        "pairing_gate": pairing_gate,
        "persona_gate": persona_gate,
        "rollback_flag": "humanization.plan_then_utter.enabled=false",
    }


print("# Plan-then-Utter Pilot Measurement")
print(f"usage_db: {usage_db} ({'exists' if usage_db.exists() else 'missing'})")
print(f"block_trace_db: {block_trace_db} ({'exists' if block_trace_db.exists() else 'missing'})")
print(f"group_filter: {group_id or 'all'}")
print(f"window_days: {days}")
print(f"cost_ratio_max: {cost_ratio_max}")
print(f"persona_drift_max_pp: {persona_drift_max_pp}")
print(f"min_pilot_parent_spans: {min_pilot_parents}")
print()

usage = usage_summary()
traces = trace_summary()
persona = persona_summary()
print_section("usage_cost_latency", usage)
print_section("block_trace_pairing", traces)
print_section("persona_score_baseline", persona)
print_section("rollout_gate", decision(usage, traces, persona))
PY
