#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

MESSAGES_DB="${GROUP_MESSAGES_DB:-${MESSAGES_DB:-$ROOT/storage/messages.db}}"
GROUP_ID="${GROUP_ID:-}"
LIMIT="${LIMIT:-200}"
REGISTER="${REGISTER:-neutral}"
MOOD="${MOOD:-neutral}"
SLOT_ENERGY="${SLOT_ENERGY:-1.0}"
STREAM_CHUNK_CHARS="${STREAM_CHUNK_CHARS:-8}"
STREAM_MIN_CHARS="${STREAM_MIN_CHARS:-6}"
STREAM_SOFT_CHARS="${STREAM_SOFT_CHARS:-24}"
STREAM_HARD_CHARS="${STREAM_HARD_CHARS:-54}"
STREAM_MAX_SEGMENTS="${STREAM_MAX_SEGMENTS:-0}"
NATURAL_MAX_SEGMENT_CHARS="${NATURAL_MAX_SEGMENT_CHARS:-20}"

python3 - "$ROOT" "$MESSAGES_DB" "$GROUP_ID" "$LIMIT" "$REGISTER" "$MOOD" \
  "$SLOT_ENERGY" "$STREAM_CHUNK_CHARS" "$STREAM_MIN_CHARS" "$STREAM_SOFT_CHARS" \
  "$STREAM_HARD_CHARS" "$STREAM_MAX_SEGMENTS" "$NATURAL_MAX_SEGMENT_CHARS" <<'PY'
from __future__ import annotations

import json
import random
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean, median, pvariance

root = Path(sys.argv[1])
messages_db = Path(sys.argv[2])
group_id = sys.argv[3].strip()
limit = int(sys.argv[4])
register = sys.argv[5].strip() or "neutral"
mood = sys.argv[6].strip() or "neutral"
slot_energy = float(sys.argv[7])
stream_chunk_chars = max(1, int(sys.argv[8]))
stream_min_chars = max(1, int(sys.argv[9]))
stream_soft_chars = max(stream_min_chars, int(sys.argv[10]))
stream_hard_chars = max(stream_soft_chars, int(sys.argv[11]))
stream_max_segments = max(0, int(sys.argv[12]))
natural_max_segment_chars = max(1, int(sys.argv[13]))

sys.path.insert(0, str(root))

from services.llm.segmentation import (  # noqa: E402
    ReplySegmentationConfig,
    inter_segment_delay,
    reply_segment_plan,
)
from services.llm.streaming_segmenter import (  # noqa: E402
    StreamingSegmenter,
    StreamingSegmenterConfig,
)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * q))
    return ordered[index]


def avg(values: list[float]) -> float:
    return mean(values) if values else 0.0


def med(values: list[float]) -> float:
    return median(values) if values else 0.0


def var(values: list[float]) -> float:
    return pvariance(values) if len(values) > 1 else 0.0


def ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def fmt(value: float) -> float:
    return round(value, 4)


def dist(values: list[int]) -> str:
    counts = Counter(values)
    return json.dumps({str(key): counts[key] for key in sorted(counts)}, ensure_ascii=False)


def iso(ts: float | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def compact(text: str) -> str:
    return "".join(str(text).split())


def print_section(title: str, payload: dict[str, object]) -> None:
    print(f"## {title}")
    for key, value in payload.items():
        if isinstance(value, float):
            print(f"{key}: {fmt(value)}")
        else:
            print(f"{key}: {value}")
    print()


def connect_messages_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"missing messages db: {path}")
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_replies(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='group_messages'",
    ).fetchone() is None:
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
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT id, group_id, content_text, created_at
        FROM group_messages
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return list(reversed(rows))


def natural_segments(text: str, seed: int) -> tuple[list[str], list[float]]:
    cfg = ReplySegmentationConfig(max_segment_chars=natural_max_segment_chars)
    plan = reply_segment_plan(
        text,
        cfg,
        register=register,
        slot_energy=slot_energy,
        mood_label=mood,
        rng=random.Random(seed),
    )
    return plan.segments, plan.inter_segment_delays


def streaming_segments(text: str) -> tuple[list[str], list[float]]:
    segmenter = StreamingSegmenter(
        StreamingSegmenterConfig(
            min_chars=stream_min_chars,
            soft_chars=stream_soft_chars,
            hard_chars=stream_hard_chars,
            max_segments=stream_max_segments,
        ),
        register=register,
        mood=mood,
    )
    segments: list[str] = []
    for start in range(0, len(text), stream_chunk_chars):
        segments.extend(segmenter.push(text[start : start + stream_chunk_chars]))
    segments.extend(segmenter.finish())
    delays = [
        inter_segment_delay(segment, register=register, slot_energy=slot_energy, mood_label=mood)
        for segment in segments[:-1]
    ]
    return segments, delays


conn = connect_messages_db(messages_db)
try:
    rows = load_replies(conn)
finally:
    conn.close()

natural_counts: list[int] = []
streaming_counts: list[int] = []
natural_lengths: list[float] = []
streaming_lengths: list[float] = []
natural_delays: list[float] = []
streaming_delays: list[float] = []
natural_first_lengths: list[float] = []
streaming_first_lengths: list[float] = []
abs_count_deltas: list[float] = []
same_count = 0
streaming_more = 0
streaming_less = 0
streaming_over_3 = 0
natural_over_3 = 0
streaming_preserved = 0
total_chars = 0

for index, row in enumerate(rows):
    text = str(row["content_text"] or "").strip()
    if not text:
        continue

    natural, natural_pause = natural_segments(text, seed=int(row["id"]) + index)
    streaming, streaming_pause = streaming_segments(text)

    natural_count = len(natural)
    streaming_count = len(streaming)
    natural_counts.append(natural_count)
    streaming_counts.append(streaming_count)
    natural_lengths.extend(float(len(segment)) for segment in natural)
    streaming_lengths.extend(float(len(segment)) for segment in streaming)
    natural_delays.extend(float(delay) for delay in natural_pause)
    streaming_delays.extend(float(delay) for delay in streaming_pause)
    if natural:
        natural_first_lengths.append(float(len(natural[0])))
    if streaming:
        streaming_first_lengths.append(float(len(streaming[0])))

    delta = streaming_count - natural_count
    abs_count_deltas.append(float(abs(delta)))
    same_count += int(delta == 0)
    streaming_more += int(delta > 0)
    streaming_less += int(delta < 0)
    streaming_over_3 += int(streaming_count > 3)
    natural_over_3 += int(natural_count > 3)
    streaming_preserved += int(compact("".join(streaming)) == compact(text))
    total_chars += len(text)

sample_count = len(natural_counts)
created_values = [float(row["created_at"]) for row in rows] if rows else []
natural_variance = var(natural_lengths)
streaming_variance = var(streaming_lengths)
streaming_delay_p50 = med(streaming_delays)
streaming_delay_target = (
    "insufficient"
    if not streaming_delays
    else "yes"
    if 1.5 <= streaming_delay_p50 <= 3.5
    else "no"
)

print("# Streaming vs Natural Measurement")
print(f"messages_db: {messages_db} ({'exists' if messages_db.exists() else 'missing'})")
print(f"group_filter: {group_id or 'all'}")
print(f"limit: {limit}")
print(f"sample_replies: {sample_count}")
print(f"sample_window_start: {iso(min(created_values) if created_values else None)}")
print(f"sample_window_end: {iso(max(created_values) if created_values else None)}")
print(f"register: {register}")
print(f"mood: {mood}")
print(f"slot_energy: {slot_energy}")
print(f"stream_chunk_chars: {stream_chunk_chars}")
print(f"stream_min_chars: {stream_min_chars}")
print(f"stream_soft_chars: {stream_soft_chars}")
print(f"stream_hard_chars: {stream_hard_chars}")
print(f"stream_max_segments: {stream_max_segments}")
print(f"natural_max_segment_chars: {natural_max_segment_chars}")
print()

if sample_count == 0:
    print("status: no_sample")
    raise SystemExit(0)

print_section(
    "segment_counts",
    {
        "natural_avg": avg([float(value) for value in natural_counts]),
        "streaming_avg": avg([float(value) for value in streaming_counts]),
        "natural_p50": med([float(value) for value in natural_counts]),
        "streaming_p50": med([float(value) for value in streaming_counts]),
        "natural_p95": percentile([float(value) for value in natural_counts], 0.95),
        "streaming_p95": percentile([float(value) for value in streaming_counts], 0.95),
        "natural_distribution": dist(natural_counts),
        "streaming_distribution": dist(streaming_counts),
    },
)

print_section(
    "segment_lengths",
    {
        "total_chars": total_chars,
        "natural_segment_total": len(natural_lengths),
        "streaming_segment_total": len(streaming_lengths),
        "natural_mean_chars": avg(natural_lengths),
        "streaming_mean_chars": avg(streaming_lengths),
        "natural_p50_chars": med(natural_lengths),
        "streaming_p50_chars": med(streaming_lengths),
        "natural_p95_chars": percentile(natural_lengths, 0.95),
        "streaming_p95_chars": percentile(streaming_lengths, 0.95),
        "natural_variance_chars2": natural_variance,
        "streaming_variance_chars2": streaming_variance,
        "variance_delta_chars2": streaming_variance - natural_variance,
        "variance_ratio_streaming_to_natural": ratio(streaming_variance, natural_variance),
        "natural_first_segment_p50_chars": med(natural_first_lengths),
        "streaming_first_segment_p50_chars": med(streaming_first_lengths),
    },
)

print_section(
    "inter_segment_delays",
    {
        "natural_delay_count": len(natural_delays),
        "streaming_delay_count": len(streaming_delays),
        "natural_delay_s_avg": avg(natural_delays),
        "streaming_delay_s_avg": avg(streaming_delays),
        "natural_delay_s_p50": med(natural_delays),
        "streaming_delay_s_p50": streaming_delay_p50,
        "natural_delay_s_p95": percentile(natural_delays, 0.95),
        "streaming_delay_s_p95": percentile(streaming_delays, 0.95),
    },
)

print_section(
    "perception_proxy",
    {
        "same_segment_count_rate": ratio(float(same_count), float(sample_count)),
        "streaming_more_segments_rate": ratio(float(streaming_more), float(sample_count)),
        "streaming_less_segments_rate": ratio(float(streaming_less), float(sample_count)),
        "avg_abs_segment_count_delta": avg(abs_count_deltas),
        "natural_over_3_segments_rate": ratio(float(natural_over_3), float(sample_count)),
        "streaming_over_3_segments_rate": ratio(float(streaming_over_3), float(sample_count)),
        "streaming_text_preserved_rate": ratio(float(streaming_preserved), float(sample_count)),
        "user_subjective_review": "pending",
    },
)

print_section(
    "gray_gate",
    {
        "streaming_length_variance_target": ">=50",
        "streaming_length_variance_target_met": "yes" if streaming_variance >= 50 else "no",
        "streaming_delay_p50_target": "1.5..3.5s",
        "streaming_delay_p50_target_met": streaming_delay_target,
        "rollback": "set profile=economy or humanization.streaming_segment.enabled=false, then restart",
    },
)
PY
