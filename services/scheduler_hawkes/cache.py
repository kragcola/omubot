"""Lightweight group heat cache used by RWS.

This is not a full Bayesian Hawkes fitter yet. It provides the production
cache contract and a stable rho proxy, so the scheduler can consume the signal
while heavier offline fitting remains replaceable behind the same API.
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hawkes_cache (
    group_id TEXT PRIMARY KEY,
    rho REAL NOT NULL,
    message_count INTEGER NOT NULL,
    window_s REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""


@dataclass(frozen=True, slots=True)
class HawkesSnapshot:
    group_id: str
    rho: float
    message_count: int
    window_s: float
    updated_at: float


class HawkesCache:
    def __init__(self, path: str | Path = "storage/hawkes_cache.db") -> None:
        self.path = Path(path)

    def upsert(self, snapshot: HawkesSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute(_SCHEMA)
            db.execute(
                "INSERT INTO hawkes_cache (group_id, rho, message_count, window_s, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(group_id) DO UPDATE SET "
                "rho=excluded.rho, message_count=excluded.message_count, "
                "window_s=excluded.window_s, updated_at=excluded.updated_at",
                (
                    snapshot.group_id,
                    float(max(0.0, min(1.0, snapshot.rho))),
                    int(snapshot.message_count),
                    float(snapshot.window_s),
                    float(snapshot.updated_at),
                ),
            )

    def load(self, group_id: str, *, max_age_s: float = 900.0) -> HawkesSnapshot | None:
        if not self.path.exists():
            return None
        with sqlite3.connect(self.path) as db:
            db.execute(_SCHEMA)
            row = db.execute(
                "SELECT group_id, rho, message_count, window_s, updated_at FROM hawkes_cache WHERE group_id = ?",
                (str(group_id),),
            ).fetchone()
        if row is None or time.time() - float(row[4]) > max_age_s:
            return None
        return HawkesSnapshot(str(row[0]), float(row[1]), int(row[2]), float(row[3]), float(row[4]))


def estimate_rho_from_times(times: Iterable[float], *, now: float | None = None, window_s: float = 3600.0) -> float:
    now = float(time.time() if now is None else now)
    cutoff = now - max(1.0, float(window_s))
    recent = sorted(float(ts) for ts in times if cutoff <= float(ts) <= now)
    if len(recent) <= 1:
        return 0.0
    gaps = [max(0.001, b - a) for a, b in pairwise(recent)]
    mean_gap = sum(gaps) / len(gaps)
    rate_per_min = 60.0 / max(mean_gap, 0.001)
    burst = sum(1 for gap in gaps if gap <= 20.0) / len(gaps)
    density = 1.0 - math.exp(-rate_per_min / 6.0)
    return max(0.0, min(0.99, 0.55 * density + 0.45 * burst))


def snapshot_from_times(
    group_id: str,
    times: Iterable[float],
    *,
    now: float | None = None,
    window_s: float = 3600.0,
) -> HawkesSnapshot:
    current = float(time.time() if now is None else now)
    cutoff = current - max(1.0, float(window_s))
    recent = [float(ts) for ts in times if cutoff <= float(ts) <= current]
    return HawkesSnapshot(
        group_id=str(group_id),
        rho=estimate_rho_from_times(recent, now=current, window_s=window_s),
        message_count=len(recent),
        window_s=float(window_s),
        updated_at=current,
    )
