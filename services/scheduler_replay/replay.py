"""Counterfactual replay primitives for scheduler decisions."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

ReplayLabel = Literal["real_better", "counterfactual_better", "indistinguishable"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduler_replay_runs (
    run_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    summary_json TEXT NOT NULL,
    created_at REAL NOT NULL
)
"""


@dataclass(frozen=True, slots=True)
class ReplaySample:
    group_id: str
    message_id: int | None
    created_at: float
    actual_decision: str
    counterfactual_decision: str
    context: str


@dataclass(frozen=True, slots=True)
class ReplayJudgement:
    sample: ReplaySample
    label: ReplayLabel
    reason: str = ""


class ReplayStore:
    def __init__(self, path: str | Path = "storage/scheduler_replay.db") -> None:
        self.path = Path(path)

    def record_run(self, *, run_id: str, group_id: str, judgements: Iterable[ReplayJudgement]) -> dict[str, int]:
        items = list(judgements)
        summary = summarize_judgements(items)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute(_SCHEMA)
            db.execute(
                "INSERT OR REPLACE INTO scheduler_replay_runs "
                "(run_id, group_id, sample_count, summary_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, group_id, len(items), json.dumps(summary, ensure_ascii=False), time.time()),
            )
        return summary

    def list_runs(self, *, limit: int = 20) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        with sqlite3.connect(self.path) as db:
            db.execute(_SCHEMA)
            rows = db.execute(
                "SELECT run_id, group_id, sample_count, summary_json, created_at "
                "FROM scheduler_replay_runs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(100, int(limit))),),
            ).fetchall()
        return [
            {
                "run_id": row[0],
                "group_id": row[1],
                "sample_count": row[2],
                "summary": json.loads(row[3] or "{}"),
                "created_at": row[4],
            }
            for row in rows
        ]


def summarize_judgements(judgements: Iterable[ReplayJudgement]) -> dict[str, int]:
    summary = {"real_better": 0, "counterfactual_better": 0, "indistinguishable": 0}
    for item in judgements:
        summary[item.label] = summary.get(item.label, 0) + 1
    return summary


def make_counterfactual_sample(
    *,
    group_id: str,
    message_id: int | None,
    created_at: float,
    actual_decision: str,
    context: str,
) -> ReplaySample:
    actual = "reply" if actual_decision == "reply" else "skip"
    counterfactual = "skip" if actual == "reply" else "reply"
    return ReplaySample(
        group_id=str(group_id),
        message_id=message_id,
        created_at=float(created_at),
        actual_decision=actual,
        counterfactual_decision=counterfactual,
        context=context,
    )


def judgement_to_dict(judgement: ReplayJudgement) -> dict[str, object]:
    return {
        "sample": asdict(judgement.sample),
        "label": judgement.label,
        "reason": judgement.reason,
    }
