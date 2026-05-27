from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dev" / "measure_plan_then_utter_pilot.sh"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _create_usage_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE llm_calls (
            ts TEXT NOT NULL,
            call_type TEXT NOT NULL,
            group_id TEXT,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            prompt_cache_hit_tokens INTEGER NOT NULL,
            prompt_cache_miss_tokens INTEGER NOT NULL,
            reasoning_replay_tokens INTEGER NOT NULL,
            elapsed_s REAL NOT NULL,
            error TEXT
        )"""
    )
    rows = [
        (_now(), "proactive", "100", 150, 50, 100, 50, 0, 1.0, ""),
        (_now(), "proactive", "100", 150, 50, 100, 50, 0, 3.0, ""),
        (_now(), "proactive_plan", "100", 30, 10, 20, 10, 0, 0.5, ""),
        (_now(), "proactive_utter", "100", 40, 20, 20, 20, 0, 0.6, ""),
        (_now(), "proactive_utter", "100", 40, 20, 20, 20, 0, 0.7, ""),
        (_now(), "proactive_plan", "100", 30, 10, 20, 10, 0, 0.5, ""),
        (_now(), "proactive_utter", "100", 40, 20, 20, 20, 0, 0.6, ""),
        (_now(), "proactive_utter", "100", 40, 20, 20, 20, 0, 0.7, ""),
    ]
    conn.executemany("INSERT INTO llm_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def _create_block_trace_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE prompt_block_traces (
            task TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    trace_rows = [
        (
            "proactive_plan",
            {"group_id": "100", "status": "planned", "parent_span_id": "p1", "outlines": ["a", "b"]},
        ),
        ("proactive_utter", {"group_id": "100", "status": "emitted", "parent_span_id": "p1", "utter_index": 0}),
        ("proactive_utter", {"group_id": "100", "status": "emitted", "parent_span_id": "p1", "utter_index": 1}),
        (
            "proactive_plan",
            {"group_id": "100", "status": "planned", "parent_span_id": "p2", "outlines": ["a", "b"]},
        ),
        ("proactive_utter", {"group_id": "100", "status": "emitted", "parent_span_id": "p2", "utter_index": 0}),
        ("proactive_utter", {"group_id": "100", "status": "emitted", "parent_span_id": "p2", "utter_index": 1}),
    ]
    conn.executemany(
        "INSERT INTO prompt_block_traces VALUES (?, ?, ?)",
        [(task, json.dumps(meta), _now()) for task, meta in trace_rows],
    )
    conn.execute(
        """CREATE TABLE humanization_metrics (
            group_id TEXT,
            score REAL NOT NULL,
            axes_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    metrics = [
        ("100", 0.91, {"linguistic_habits": 0.9}, {"generation_path": "baseline"}),
        ("100", 0.90, {"linguistic_habits": 0.9}, {"generation_path": "baseline"}),
        ("100", 0.89, {"linguistic_habits": 0.88}, {"generation_path": "baseline"}),
        ("100", 0.88, {"linguistic_habits": 0.87}, {"generation_path": "plan_then_utter"}),
        ("100", 0.87, {"linguistic_habits": 0.86}, {"generation_path": "plan_then_utter"}),
    ]
    conn.executemany(
        "INSERT INTO humanization_metrics VALUES (?, ?, ?, ?, ?)",
        [
            (group_id, score, json.dumps(axes), json.dumps(meta), _now())
            for group_id, score, axes, meta in metrics
        ],
    )
    conn.commit()
    conn.close()


def _run_script(usage_db: Path, trace_db: Path, *, group_id: str = "100") -> str:
    env = {
        **os.environ,
        "USAGE_DB": str(usage_db),
        "BLOCK_TRACE_DB": str(trace_db),
        "GROUP_ID": group_id,
        "MIN_PILOT_PARENTS": "2",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_plan_then_utter_pilot_measurement_uses_per_reply_cost(tmp_path: Path) -> None:
    usage_db = tmp_path / "usage.db"
    trace_db = tmp_path / "block_trace.db"
    _create_usage_db(usage_db)
    _create_block_trace_db(trace_db)

    output = _run_script(usage_db, trace_db)

    assert "baseline_calls: 2" in output
    assert "pilot_plan_calls: 2" in output
    assert "pilot_utter_calls: 4" in output
    assert "avg_baseline_tokens_per_reply: 200.0" in output
    assert "avg_pilot_tokens_per_reply: 160.0" in output
    assert "pilot_vs_baseline_token_ratio: 0.8" in output
    assert "cost_gate: yes" in output
    assert "pairing_gate: yes" in output
    assert "persona_gate: yes" in output
    assert "decision: eligible_for_p6_11_decision" in output


def test_plan_then_utter_pilot_measurement_reports_no_sample(tmp_path: Path) -> None:
    usage_db = tmp_path / "usage.db"
    trace_db = tmp_path / "block_trace.db"
    _create_usage_db(usage_db)
    _create_block_trace_db(trace_db)

    output = _run_script(usage_db, trace_db, group_id="missing")

    assert "status: no_usage_rows" in output
    assert "cost_gate: no_sample" in output
    assert "pairing_gate: no_sample" in output
    assert "persona_gate: no_sample" in output
    assert "decision: continue_pilot_collect_samples" in output
