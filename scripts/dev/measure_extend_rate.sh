#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

MESSAGES_DB="${GROUP_MESSAGES_DB:-${MESSAGES_DB:-$ROOT/storage/messages.db}}"
USAGE_DB="${USAGE_DB:-$ROOT/storage/usage.db}"
GROUP_ID="${GROUP_ID:-}"
LIMIT="${LIMIT:-200}"
REGISTER="${REGISTER:-neutral}"
SLOT_ENERGY="${SLOT_ENERGY:-1.0}"
EXTEND_RATE_MAX="${EXTEND_RATE_MAX:-0.25}"
USAGE_LIMIT="${USAGE_LIMIT:-500}"

python3 - "$ROOT" "$MESSAGES_DB" "$USAGE_DB" "$GROUP_ID" "$LIMIT" "$REGISTER" \
  "$SLOT_ENERGY" "$EXTEND_RATE_MAX" "$USAGE_LIMIT" <<'PY'
from __future__ import annotations

import json
import importlib.util
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean, median

root = Path(sys.argv[1])
messages_db = Path(sys.argv[2])
usage_db = Path(sys.argv[3])
group_id = sys.argv[4].strip()
limit = int(sys.argv[5])
register = sys.argv[6].strip() or "neutral"
slot_energy = float(sys.argv[7])
extend_rate_max = float(sys.argv[8])
usage_limit = int(sys.argv[9])

sys.path.insert(0, str(root))

pause_extend_path = root / "services/humanization/pause_extend.py"
spec = importlib.util.spec_from_file_location("omu_pause_extend", pause_extend_path)
if spec is None or spec.loader is None:
    raise SystemExit(f"failed to load pause_extend.py: {pause_extend_path}")
pause_extend_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pause_extend_module
spec.loader.exec_module(pause_extend_module)
PauseExtend = pause_extend_module.PauseExtend


def connect_ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fmt(value: float) -> float:
    return round(value, 4)


def ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def iso(ts: float | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def avg(values: list[float]) -> float:
    return mean(values) if values else 0.0


def med(values: list[float]) -> float:
    return median(values) if values else 0.0


def print_section(title: str, payload: dict[str, object]) -> None:
    print(f"## {title}")
    for key, value in payload.items():
        if isinstance(value, float):
            print(f"{key}: {fmt(value)}")
        else:
            print(f"{key}: {value}")
    print()


def has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def actual_usage() -> dict[str, object]:
    conn = connect_ro(usage_db)
    if conn is None:
        return {"status": "missing_usage_db"}
    try:
        if not has_table(conn, "llm_calls"):
            return {"status": "missing_llm_calls"}
        where = ["call_type IN ('proactive', 'main', 'proactive_extend')"]
        params: list[object] = []
        if group_id:
            where.append("group_id = ?")
            params.append(group_id)
        params.append(max(1, usage_limit))
        rows = conn.execute(
            f"""
            SELECT ts, call_type, group_id, input_tokens, output_tokens, elapsed_s
            FROM llm_calls
            WHERE {' AND '.join(where)}
            ORDER BY ts DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    finally:
        conn.close()

    primary = [row for row in rows if str(row["call_type"]) in {"proactive", "main"}]
    extends = [row for row in rows if str(row["call_type"]) == "proactive_extend"]
    extend_rate = ratio(len(extends), len(primary))
    token_rate = ratio(
        sum(int(row["input_tokens"] or 0) + int(row["output_tokens"] or 0) for row in extends),
        sum(int(row["input_tokens"] or 0) + int(row["output_tokens"] or 0) for row in primary),
    )
    return {
        "status": "ok" if rows else "no_usage_sample",
        "usage_rows": len(rows),
        "primary_replies": len(primary),
        "extend_calls": len(extends),
        "extend_rate": extend_rate,
        "extend_rate_pct": pct(extend_rate),
        "extend_token_rate": token_rate,
        "extend_token_rate_pct": pct(token_rate),
        "avg_extend_elapsed_s": avg([float(row["elapsed_s"] or 0.0) for row in extends]),
        "gate": "yes" if extend_rate <= extend_rate_max else "no",
    }


def load_assistant_sample() -> list[sqlite3.Row]:
    conn = connect_ro(messages_db)
    if conn is None:
        raise SystemExit(f"missing messages db: {messages_db}")
    try:
        if not has_table(conn, "group_messages"):
            raise SystemExit("missing table: group_messages")
        where = [
            "role = 'assistant'",
            "group_id NOT LIKE 'session:%'",
            "TRIM(COALESCE(content_text, '')) != ''",
        ]
        params: list[object] = []
        if group_id:
            where.append("group_id = ?")
            params.append(group_id)
        params.append(max(1, limit))
        return conn.execute(
            f"""
            SELECT id, group_id, role, speaker, content_text, created_at
            FROM group_messages
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    finally:
        conn.close()


def load_context_rows(sample: list[sqlite3.Row]) -> list[sqlite3.Row]:
    if not sample:
        return []
    conn = connect_ro(messages_db)
    if conn is None:
        raise SystemExit(f"missing messages db: {messages_db}")
    try:
        group_ids = sorted({str(row["group_id"]) for row in sample})
        placeholders = ",".join("?" for _ in group_ids)
        start_at = min(float(row["created_at"]) for row in sample) - 60.0
        end_at = max(float(row["created_at"]) for row in sample) + 30.0
        return conn.execute(
            f"""
            SELECT id, group_id, role, speaker, content_text, created_at
            FROM group_messages
            WHERE group_id IN ({placeholders})
              AND created_at BETWEEN ? AND ?
              AND TRIM(COALESCE(content_text, '')) != ''
            ORDER BY group_id ASC, created_at ASC, id ASC
            """,
            (*group_ids, start_at, end_at),
        ).fetchall()
    finally:
        conn.close()


def offline_estimate() -> dict[str, object]:
    sample = list(reversed(load_assistant_sample()))
    if not sample:
        return {"status": "no_message_sample", "sample_replies": 0}
    rows = load_context_rows(sample)

    decisioner = PauseExtend()
    by_group: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_group.setdefault(str(row["group_id"]), []).append(row)

    should_count = 0
    interrupted_count = 0
    would_extend_count = 0
    wait_values: list[float] = []
    reply_lengths: list[float] = []
    reasons: Counter[str] = Counter()
    examples: list[dict[str, object]] = []
    created_values: list[float] = []

    for row in sample:
        gid = str(row["group_id"])
        text = str(row["content_text"] or "").strip()
        at = float(row["created_at"])
        created_values.append(at)
        reply_lengths.append(float(len(text)))
        peers = by_group.get(gid, [])
        recent_count = sum(
            1
            for peer in peers
            if at - 60.0 <= float(peer["created_at"]) <= at
        )
        heat = min(1.0, recent_count / 12.0)
        decision = decisioner.decide(
            text,
            register=register,
            slot={"energy": slot_energy},
            group_state={"heat": heat, "user_replied": False},
        )
        reasons.update(decision.reasons)
        wait_values.append(float(decision.wait_seconds))
        if not decision.should_extend:
            continue

        should_count += 1
        next_user = next(
            (
                peer
                for peer in peers
                if float(peer["created_at"]) > at
                and str(peer["role"]) == "user"
            ),
            None,
        )
        interrupted = bool(
            next_user is not None
            and float(next_user["created_at"]) - at <= float(decision.wait_seconds)
        )
        interrupted_count += int(interrupted)
        if interrupted:
            continue

        would_extend_count += 1
        if len(examples) < 5:
            examples.append(
                {
                    "group_id": gid,
                    "reply_id": int(row["id"]),
                    "wait_seconds": decision.wait_seconds,
                    "reasons": list(decision.reasons),
                    "preview": text[:80],
                }
            )

    sample_count = len(sample)
    decision_rate = ratio(should_count, sample_count)
    interrupted_rate = ratio(interrupted_count, should_count)
    estimated_extend_rate = ratio(would_extend_count, sample_count)
    return {
        "status": "ok",
        "sample_replies": sample_count,
        "sample_window_start": iso(min(created_values) if created_values else None),
        "sample_window_end": iso(max(created_values) if created_values else None),
        "decision_should_extend": should_count,
        "decision_rate": decision_rate,
        "decision_rate_pct": pct(decision_rate),
        "interrupted_by_user": interrupted_count,
        "interrupted_rate_among_decisions": interrupted_rate,
        "interrupted_rate_among_decisions_pct": pct(interrupted_rate),
        "estimated_extend_calls": would_extend_count,
        "estimated_extend_rate": estimated_extend_rate,
        "estimated_extend_rate_pct": pct(estimated_extend_rate),
        "avg_wait_seconds": avg(wait_values),
        "p50_wait_seconds": med(wait_values),
        "avg_reply_chars": avg(reply_lengths),
        "reason_top": json.dumps(dict(reasons.most_common(8)), ensure_ascii=False),
        "examples": json.dumps(examples, ensure_ascii=False),
        "gate": "yes" if estimated_extend_rate <= extend_rate_max else "no",
    }


actual = actual_usage()
offline = offline_estimate()
actual_gate = actual.get("gate") in {"yes", None}
offline_gate = offline.get("gate") in {"yes", None}

print("# Pause Extend Rate Measurement")
print(f"messages_db: {messages_db} ({'exists' if messages_db.exists() else 'missing'})")
print(f"usage_db: {usage_db} ({'exists' if usage_db.exists() else 'missing'})")
print(f"group_filter: {group_id or 'all'}")
print(f"limit: {limit}")
print(f"usage_limit: {usage_limit}")
print(f"register: {register}")
print(f"slot_energy: {slot_energy}")
print(f"extend_rate_max: {extend_rate_max} ({pct(extend_rate_max)})")
print()

print_section("actual_usage", actual)
print_section("offline_estimate", offline)
print_section(
    "gray_gate",
    {
        "actual_usage_gate": "yes" if actual_gate else "no",
        "offline_estimate_gate": "yes" if offline_gate else "no",
        "overall_gate": "yes" if actual_gate and offline_gate else "no",
    },
)
PY
