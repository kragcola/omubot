#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MESSAGES_DB="${GROUP_MESSAGES_DB:-${MESSAGES_DB:-$ROOT/storage/messages.db}}"
BLOCK_TRACE_DB="${BLOCK_TRACE_DB:-$ROOT/storage/block_trace.db}"
GROUP_ID="${GROUP_ID:-}"
LIMIT="${LIMIT:-200}"
SEGMENT_GAP_S="${SEGMENT_GAP_S:-10}"
REPLY_DELAY_MAX_S="${REPLY_DELAY_MAX_S:-600}"
python3 - "$MESSAGES_DB" "$BLOCK_TRACE_DB" "$GROUP_ID" "$LIMIT" "$SEGMENT_GAP_S" "$REPLY_DELAY_MAX_S" <<'PY'
import sqlite3, sys
from collections import Counter
from pathlib import Path
from statistics import mean, median

db, trace_db = Path(sys.argv[1]), Path(sys.argv[2])
gid, limit = sys.argv[3].strip(), int(sys.argv[4])
seg_gap, max_delay = float(sys.argv[5]), float(sys.argv[6])
def percentile(values, q):
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * q))]

def finish(current, replies):
    if current and current["delay"] is not None:
        replies.append(current)
if not db.exists():
    raise SystemExit(f"missing messages db: {db}")
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
where, params = ("WHERE group_id = ?", (gid,)) if gid else ("", ())
rows = conn.execute(
    f"SELECT group_id, role, created_at FROM group_messages {where} ORDER BY group_id, created_at",
    params,
)
replies, prev_group, prev_user_at, current = [], "", None, None
for row in rows:
    group, role, at = str(row["group_id"]), str(row["role"]), float(row["created_at"])
    if group != prev_group:
        finish(current, replies); prev_group, prev_user_at, current = group, None, None
    if role == "user":
        finish(current, replies); prev_user_at, current = at, None; continue
    if role != "assistant":
        continue
    if current and at - current["last_at"] <= seg_gap:
        current["segments"] += 1; current["gaps"].append(at - current["last_at"]); current["last_at"] = at; continue
    finish(current, replies)
    delay = None if prev_user_at is None else at - prev_user_at
    current = {"delay": delay if delay is not None and 0 <= delay <= max_delay else None, "last_at": at, "segments": 1, "gaps": []}
finish(current, replies)
sample = replies[-limit:]
delays = [r["delay"] for r in sample if r["delay"] is not None]
gaps = [gap for r in sample for gap in r["gaps"]]
dist = Counter(r["segments"] for r in sample)
print("# rhythm_baseline")
for key, value in {"messages_db": f"{db} ({'exists' if db.exists() else 'missing'})", "block_trace_db": f"{trace_db} ({'exists' if trace_db.exists() else 'missing'})", "group_filter": gid or "all", "sample_replies": len(sample), "reply_delay_s_avg": round(mean(delays), 3) if delays else 0.0, "reply_delay_s_p50": round(median(delays), 3) if delays else 0.0, "reply_delay_s_p95": round(percentile(delays, 0.95), 3), "inter_segment_gap_count": len(gaps), "inter_segment_gap_s_avg": round(mean(gaps), 3) if gaps else 0.0, "inter_segment_gap_s_p95": round(percentile(gaps, 0.95), 3), "segment_count_distribution": ", ".join(f"{k}={dist[k]}" for k in sorted(dist))}.items():
    print(f"{key}: {value}")
PY
