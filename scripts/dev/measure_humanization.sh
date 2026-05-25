#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

BLOCK_TRACE_DB="${BLOCK_TRACE_DB:-$ROOT/storage/block_trace.db}"
LEARNING_NORMALIZER_DB="${LEARNING_NORMALIZER_DB:-$ROOT/storage/learning_normalizer.db}"
EPISODIC_DB="${EPISODIC_DB:-$ROOT/storage/episodic.db}"
GROUP_ID="${GROUP_ID:-}"

python3 - "$BLOCK_TRACE_DB" "$LEARNING_NORMALIZER_DB" "$EPISODIC_DB" "$GROUP_ID" <<'PY'
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from statistics import mean

block_trace_db = Path(sys.argv[1])
learning_db = Path(sys.argv[2])
episodic_db = Path(sys.argv[3])
group_id = sys.argv[4].strip()


def connect(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def has_table(conn: sqlite3.Connection | None, table: str) -> bool:
    if conn is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def scalar(conn: sqlite3.Connection | None, sql: str, params: tuple[object, ...] = ()) -> object:
    if conn is None:
        return None
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return row[0]


def load_json(raw: str | None, fallback: object) -> object:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


def humanization_metrics(conn: sqlite3.Connection | None) -> dict[str, object]:
    if not has_table(conn, "humanization_metrics"):
        return {
            "status": "missing_table",
            "total": 0,
            "avg_score": 0.0,
            "rewrite_applied": 0,
            "issue_counts": {},
            "axis_avg": {},
        }
    where = ""
    params: tuple[object, ...] = ()
    if group_id:
        where = "WHERE group_id = ?"
        params = (group_id,)
    rows = list(conn.execute(
        f"SELECT score, axes_json, issues_json, metadata_json FROM humanization_metrics {where}",
        params,
    ))
    issue_counts: dict[str, int] = {}
    axis_values: dict[str, list[float]] = {}
    rewrite_applied = 0
    for row in rows:
        axes = load_json(row["axes_json"], {})
        if isinstance(axes, dict):
            for key, value in axes.items():
                try:
                    axis_values.setdefault(str(key), []).append(float(value))
                except Exception:
                    pass
        issues = load_json(row["issues_json"], [])
        if isinstance(issues, list):
            for issue in issues:
                key = str(issue)
                issue_counts[key] = issue_counts.get(key, 0) + 1
        metadata = load_json(row["metadata_json"], {})
        if isinstance(metadata, dict) and metadata.get("rewrite_applied") is True:
            rewrite_applied += 1
    scores = [float(row["score"]) for row in rows]
    return {
        "status": "ok",
        "total": len(rows),
        "avg_score": round(mean(scores), 4) if scores else 0.0,
        "rewrite_applied": rewrite_applied,
        "issue_counts": issue_counts,
        "axis_avg": {key: round(mean(values), 4) for key, values in sorted(axis_values.items()) if values},
    }


def catchphrase_stats(conn: sqlite3.Connection | None) -> dict[str, object]:
    if not has_table(conn, "learning_normalizer_clusters"):
        return {"status": "missing_table", "cluster_count": 0, "item_rows": 0, "total_items": 0}
    params: tuple[object, ...] = ("catchphrase",)
    cluster_where = "domain = ?"
    item_where = "domain = ?"
    if group_id:
        cluster_where += " AND group_id = ?"
        item_where += " AND group_id = ?"
        params = ("catchphrase", group_id)
    cluster_count = int(scalar(
        conn,
        f"SELECT COUNT(*) FROM learning_normalizer_clusters WHERE {cluster_where}",
        params,
    ) or 0)
    item_row = conn.execute(
        f"SELECT COUNT(*) AS rows, COALESCE(SUM(count), 0) AS total FROM learning_normalizer_items WHERE {item_where}",
        params,
    ).fetchone() if has_table(conn, "learning_normalizer_items") else None
    item_rows = int(item_row["rows"] if item_row else 0)
    total_items = int(item_row["total"] if item_row else 0)
    reused_items = max(0, item_rows - cluster_count)
    return {
        "status": "ok",
        "cluster_count": cluster_count,
        "item_rows": item_rows,
        "total_items": total_items,
        "reused_items": reused_items,
        "reuse_rate": round(reused_items / item_rows, 4) if item_rows else 0.0,
    }


def episode_stats(conn: sqlite3.Connection | None) -> dict[str, object]:
    if not has_table(conn, "episodes"):
        return {"status": "missing_table", "total": 0, "by_state": {}}
    params: tuple[object, ...] = ()
    where = ""
    if group_id:
        where = "WHERE group_id = ?"
        params = (group_id,)
    total = int(scalar(conn, f"SELECT COUNT(*) FROM episodes {where}", params) or 0)
    rows = conn.execute(
        f"SELECT episode_state, COUNT(*) AS cnt FROM episodes {where} GROUP BY episode_state",
        params,
    ).fetchall()
    return {"status": "ok", "total": total, "by_state": {str(row["episode_state"]): int(row["cnt"]) for row in rows}}


def double_haiku_stats(conn: sqlite3.Connection | None) -> dict[str, object]:
    if not has_table(conn, "prompt_block_traces"):
        return {"status": "missing_table", "requests": 0, "paired_requests": 0}
    where = "WHERE decision = 'shadow_only' AND provider IN ('semantic_gate', 'thinker')"
    params: tuple[object, ...] = ()
    if group_id:
        where += " AND json_extract(metadata_json, '$.group_id') = ?"
        params = (group_id,)
    rows = conn.execute(
        f"SELECT request_id, provider, COUNT(*) AS cnt FROM prompt_block_traces {where} GROUP BY request_id, provider",
        params,
    ).fetchall()
    by_request: dict[str, set[str]] = {}
    for row in rows:
        by_request.setdefault(str(row["request_id"]), set()).add(str(row["provider"]))
    paired = sum(1 for providers in by_request.values() if {"semantic_gate", "thinker"} <= providers)
    return {"status": "ok", "requests": len(by_request), "paired_requests": paired}


def print_section(title: str, payload: dict[str, object]) -> None:
    print(f"## {title}")
    for key, value in payload.items():
        if isinstance(value, dict):
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            print(f"{key}: {encoded}")
        else:
            print(f"{key}: {value}")
    print()


block = connect(block_trace_db)
learning = connect(learning_db)
episodic = connect(episodic_db)
try:
    print("# Humanization Measurement")
    print(f"group_filter: {group_id or 'all'}")
    print(f"block_trace_db: {block_trace_db} ({'exists' if block_trace_db.exists() else 'missing'})")
    print(f"learning_normalizer_db: {learning_db} ({'exists' if learning_db.exists() else 'missing'})")
    print(f"episodic_db: {episodic_db} ({'exists' if episodic_db.exists() else 'missing'})")
    print()
    print_section("humanization_metrics", humanization_metrics(block))
    print_section("catchphrase_normalizer", catchphrase_stats(learning))
    print_section("episode_source", episode_stats(episodic))
    print_section("u13_double_haiku", double_haiku_stats(block))
    print("## rollout_gate")
    print("gray_1_24h_window: pending")
    print("gray_2: blocked_until_gray_1_passes")
    print("gray_3: blocked_until_gray_2_passes")
finally:
    for conn in (block, learning, episodic):
        if conn is not None:
            conn.close()
PY
