"""Dialogue Climate M2/M3 — durable ClimateState metrics recorder.

Mirrors ``m1_metrics.py`` but records the full six-dimension ClimateState. The
M2 ``ClimateEngine`` keeps state in memory only; this recorder persists a row on
every sensor-driven signal so the M3 gray-run survives restarts and lets us see
the real multi-dimension distribution per (group, user) — the data the user
asked to observe once ``m3_sensors_enabled`` is on.

Design constraints (same as m1_metrics):
- Fully optional: wired as a callback; ``None`` by default so the default path
  and every unit test are byte-for-byte unaffected.
- Never raises into the reply path: all writes swallow errors, log at debug.
- WAL mode so a read CLI never blocks the live writer.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from loguru import logger

_L = logger.bind(channel="dialogue_climate")

_DEFAULT_DB_PATH = "storage/living_persona/m2_climate.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS m2_climate_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    monotonic_ts  REAL    NOT NULL,
    group_id      TEXT    NOT NULL DEFAULT '',
    user_id       TEXT    NOT NULL DEFAULT '',
    signal_dim    TEXT    NOT NULL DEFAULT '',
    signal_delta  REAL    NOT NULL DEFAULT 0.0,
    signal_target REAL,
    signal_source TEXT    NOT NULL DEFAULT '',
    energy        REAL    NOT NULL DEFAULT 0.5,
    valence       REAL    NOT NULL DEFAULT 0.5,
    openness      REAL    NOT NULL DEFAULT 0.5,
    tension       REAL    NOT NULL DEFAULT 0.0,
    trust         REAL    NOT NULL DEFAULT 0.5,
    familiarity   REAL    NOT NULL DEFAULT 0.0
)
"""

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_m2_events_ts ON m2_climate_events (ts)",
    "CREATE INDEX IF NOT EXISTS idx_m2_events_key ON m2_climate_events (group_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_m2_events_dim ON m2_climate_events (signal_dim)",
)

_INSERT = """
INSERT INTO m2_climate_events
    (ts, monotonic_ts, group_id, user_id, signal_dim, signal_delta, signal_target, signal_source,
     energy, valence, openness, tension, trust, familiarity)
VALUES (:ts, :monotonic_ts, :group_id, :user_id, :signal_dim, :signal_delta, :signal_target, :signal_source,
        :energy, :valence, :openness, :tension, :trust, :familiarity)
"""

class ClimateMetricsRecorder:
    """Synchronous, best-effort durable recorder for ClimateState snapshots."""

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._db: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection | None:
        if self._db is not None:
            return self._db
        try:
            import os

            os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(_CREATE_TABLE)
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(m2_climate_events)").fetchall()
            }
            if "signal_target" not in columns:
                conn.execute("ALTER TABLE m2_climate_events ADD COLUMN signal_target REAL")
            for idx in _CREATE_INDEXES:
                conn.execute(idx)
            conn.commit()
            self._db = conn
        except Exception as exc:  # pragma: no cover - defensive
            _L.debug("m2 metrics connect failed | path={} err={}", self._db_path, exc)
            self._db = None
        return self._db

    def close(self) -> None:
        if self._db is not None:
            with contextlib.suppress(Exception):  # pragma: no cover - defensive
                self._db.close()
            self._db = None

    def record_signal(
        self,
        *,
        group_id: str | int | None,
        user_id: str | int | None,
        signal_dim: str,
        signal_delta: float,
        signal_target: float | None = None,
        signal_source: str,
        state: Any,
        monotonic_ts: float | None = None,
    ) -> None:
        """Persist one signal + the resulting ClimateState snapshot."""
        conn = self._connect()
        if conn is None:
            return
        try:
            conn.execute(
                _INSERT,
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "monotonic_ts": float(
                        monotonic_ts if monotonic_ts is not None else time.monotonic()
                    ),
                    "group_id": str(group_id or ""),
                    "user_id": str(user_id or ""),
                    "signal_dim": str(signal_dim or ""),
                    "signal_delta": float(signal_delta),
                    "signal_target": (
                        None if signal_target is None else float(signal_target)
                    ),
                    "signal_source": str(signal_source or ""),
                    "energy": float(getattr(state, "energy", 0.5)),
                    "valence": float(getattr(state, "valence", 0.5)),
                    "openness": float(getattr(state, "openness", 0.5)),
                    "tension": float(getattr(state, "tension", 0.0)),
                    "trust": float(getattr(state, "trust", 0.5)),
                    "familiarity": float(getattr(state, "familiarity", 0.0)),
                },
            )
            conn.commit()
        except Exception as exc:  # pragma: no cover - defensive
            _L.debug("m2 metrics write failed | dim={} err={}", signal_dim, exc)

    def rows(self, *, group_id: str | int | None = None) -> list[dict[str, Any]]:
        """Return all events (optionally for one group) ordered by time."""
        conn = self._connect()
        if conn is None:
            return []
        try:
            if group_id is None:
                cur = conn.execute(
                    "SELECT * FROM m2_climate_events ORDER BY monotonic_ts ASC, id ASC"
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM m2_climate_events WHERE group_id = ? "
                    "ORDER BY monotonic_ts ASC, id ASC",
                    (str(group_id),),
                )
            return [dict(r) for r in cur.fetchall()]
        except Exception as exc:  # pragma: no cover - defensive
            _L.debug("m2 metrics read failed | err={}", exc)
            return []

    def summary(self, *, group_id: str | int | None = None) -> dict[str, Any]:
        """Aggregate per-dimension stats from durable events (calibration)."""
        return summarize_climate_events(self.rows(group_id=group_id))


# -- pure aggregation (unit-testable without a DB) ---------------------------

_DIMS = ("energy", "valence", "openness", "tension", "trust", "familiarity")


def summarize_climate_events(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Reduce raw climate events into per-dimension calibration signals."""
    events = list(rows)
    n = len(events)
    keys = {(str(e.get("group_id", "")), str(e.get("user_id", ""))) for e in events}
    source_counts: dict[str, int] = {}
    for e in events:
        src = str(e.get("signal_source", ""))
        source_counts[src] = source_counts.get(src, 0) + 1
    peaks: dict[str, float] = {}
    for dim in _DIMS:
        peaks[dim] = max((float(e.get(dim, 0.0)) for e in events), default=0.0)
    return {
        "event_count": n,
        "key_count": len(keys),
        "source_counts": source_counts,
        "peak": peaks,
    }


__all__ = ["ClimateMetricsRecorder", "summarize_climate_events"]
