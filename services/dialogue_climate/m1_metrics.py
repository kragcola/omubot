"""Durable M1 dialogue-climate tension metrics recorder.

Part A M1 stores tension as a pure in-memory closed-form state (see
``plugins/schedule/mood.py``); its counters reset on every restart and report
the *configured* tau rather than the *observed* decay. That is enough for unit
tests but useless for the 1-2 week gray-run calibration that Part A R7/R8 make a
hard precondition for greenlighting M2.

This recorder persists every M1 event (inject / prompt-trigger) to a small
SQLite table so the gray-run survives restarts and lets us reconstruct three
calibration signals from real data:

1. injection count + arrival pattern (burst inter-arrival vs half-life),
2. prompt-trigger rate (how often resolved tension crosses the threshold),
3. *observed* half-life — fit from consecutive injections on the same key by
   inverting the closed-form decay between the prior resolved tension and the
   value seen at the next injection.

Design constraints (mirrors ``services/llm/usage.py`` patterns, but synchronous
because the MoodEngine hooks that call it are synchronous and burst-gated /
low-frequency):

- Fully optional: wired as a callback into MoodEngine; ``None`` by default so the
  default path and every unit test are byte-for-byte unaffected.
- Never raises into the reply path: all writes swallow errors and log at debug.
- WAL mode so the gray-run read CLI never blocks the live writer.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

from loguru import logger

_L = logger.bind(channel="dialogue_climate")

_DEFAULT_DB_PATH = "storage/living_persona/m1_metrics.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS m1_tension_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    monotonic_ts    REAL    NOT NULL,
    group_id        TEXT    NOT NULL DEFAULT '',
    session_id      TEXT    NOT NULL DEFAULT '',
    event_type      TEXT    NOT NULL,
    tension_after   REAL    NOT NULL DEFAULT 0.0,
    tension_before  REAL    NOT NULL DEFAULT 0.0,
    delta           REAL    NOT NULL DEFAULT 0.0,
    mention_count   INTEGER NOT NULL DEFAULT 0,
    poke_count      INTEGER NOT NULL DEFAULT 0,
    tau_s           REAL    NOT NULL DEFAULT 0.0,
    threshold       REAL    NOT NULL DEFAULT 0.0
)
"""

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_m1_events_ts ON m1_tension_events (ts)",
    "CREATE INDEX IF NOT EXISTS idx_m1_events_key ON m1_tension_events (group_id, session_id)",
    "CREATE INDEX IF NOT EXISTS idx_m1_events_type ON m1_tension_events (event_type)",
)

_INSERT = """
INSERT INTO m1_tension_events
    (ts, monotonic_ts, group_id, session_id, event_type,
     tension_after, tension_before, delta, mention_count, poke_count, tau_s, threshold)
VALUES (:ts, :monotonic_ts, :group_id, :session_id, :event_type,
        :tension_after, :tension_before, :delta, :mention_count, :poke_count, :tau_s, :threshold)
"""

EVENT_INJECT = "inject"
EVENT_TRIGGER = "trigger"


class M1MetricsRecorder:
    """Synchronous, best-effort durable recorder for M1 tension events.

    The MoodEngine calls ``record_inject`` / ``record_trigger`` from its
    synchronous hooks. Both open the (lazily-initialised) connection and write a
    single row, swallowing any error so the live reply path is never disturbed.
    """

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
            for idx in _CREATE_INDEXES:
                conn.execute(idx)
            conn.commit()
            self._db = conn
        except Exception as exc:  # pragma: no cover - defensive
            _L.debug("m1 metrics connect failed | path={} err={}", self._db_path, exc)
            self._db = None
        return self._db

    def close(self) -> None:
        if self._db is not None:
            with contextlib.suppress(Exception):  # pragma: no cover - defensive
                self._db.close()
            self._db = None

    def _write(self, row: dict[str, Any]) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            conn.execute(_INSERT, row)
            conn.commit()
        except Exception as exc:  # pragma: no cover - defensive
            _L.debug("m1 metrics write failed | type={} err={}", row.get("event_type"), exc)

    def record_inject(
        self,
        *,
        group_id: str | int | None,
        session_id: str,
        tension_before: float,
        tension_after: float,
        delta: float,
        mention_count: int = 0,
        poke_count: int = 0,
        tau_s: float = 0.0,
        threshold: float = 0.0,
        monotonic_ts: float | None = None,
    ) -> None:
        self._write(
            {
                "ts": datetime.now(UTC).isoformat(),
                "monotonic_ts": float(monotonic_ts if monotonic_ts is not None else time.monotonic()),
                "group_id": str(group_id or ""),
                "session_id": str(session_id or ""),
                "event_type": EVENT_INJECT,
                "tension_after": float(tension_after),
                "tension_before": float(tension_before),
                "delta": float(delta),
                "mention_count": int(mention_count or 0),
                "poke_count": int(poke_count or 0),
                "tau_s": float(tau_s),
                "threshold": float(threshold),
            }
        )

    def record_trigger(
        self,
        *,
        group_id: str | int | None,
        session_id: str,
        tension_after: float,
        threshold: float = 0.0,
        tau_s: float = 0.0,
        monotonic_ts: float | None = None,
    ) -> None:
        self._write(
            {
                "ts": datetime.now(UTC).isoformat(),
                "monotonic_ts": float(monotonic_ts if monotonic_ts is not None else time.monotonic()),
                "group_id": str(group_id or ""),
                "session_id": str(session_id or ""),
                "event_type": EVENT_TRIGGER,
                "tension_after": float(tension_after),
                "tension_before": float(tension_after),
                "delta": 0.0,
                "mention_count": 0,
                "poke_count": 0,
                "tau_s": float(tau_s),
                "threshold": float(threshold),
            }
        )

    # -- read side: gray-run calibration --------------------------------------

    def rows(self, *, group_id: str | int | None = None) -> list[dict[str, Any]]:
        """Return all events (optionally for one group) ordered by time."""
        conn = self._connect()
        if conn is None:
            return []
        try:
            if group_id is None:
                cur = conn.execute(
                    "SELECT * FROM m1_tension_events ORDER BY monotonic_ts ASC, id ASC"
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM m1_tension_events WHERE group_id = ? "
                    "ORDER BY monotonic_ts ASC, id ASC",
                    (str(group_id),),
                )
            return [dict(r) for r in cur.fetchall()]
        except Exception as exc:  # pragma: no cover - defensive
            _L.debug("m1 metrics read failed | err={}", exc)
            return []

    def summary(self, *, group_id: str | int | None = None) -> dict[str, Any]:
        """Aggregate calibration signals from durable events.

        ``observed_half_life_s`` is fit from consecutive injections on the same
        (group, session) key: between two injections the stored state decays as
        ``before_next = prev_after * exp(-dt / tau)``, so each adjacent pair where
        the tension actually fell yields ``tau = dt / ln(prev_after / before_next)``
        and a half-life of ``ln(2) * tau``. We aggregate the median over all such
        pairs to stay robust against bursts and rounding.
        """
        return summarize_events(self.rows(group_id=group_id))



# -- pure aggregation (unit-testable without a DB) ---------------------------

def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def summarize_events(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Reduce raw M1 events into gray-run calibration signals.

    Pure function over event dicts so it can be unit-tested without a DB.
    """
    import math

    events = list(rows)
    injects = [e for e in events if e.get("event_type") == EVENT_INJECT]
    triggers = [e for e in events if e.get("event_type") == EVENT_TRIGGER]

    injection_count = len(injects)
    trigger_count = len(triggers)
    trigger_rate = (trigger_count / injection_count) if injection_count else 0.0

    # Per-key sequences for observed half-life + inter-arrival.
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for e in injects:
        key = (str(e.get("group_id", "")), str(e.get("session_id", "")))
        by_key.setdefault(key, []).append(e)

    tau_samples: list[float] = []
    interarrival_samples: list[float] = []
    for seq in by_key.values():
        seq.sort(key=lambda e: (float(e.get("monotonic_ts", 0.0)), int(e.get("id", 0))))
        for prev, cur in pairwise(seq):
            dt = float(cur.get("monotonic_ts", 0.0)) - float(prev.get("monotonic_ts", 0.0))
            if dt <= 0:
                continue
            interarrival_samples.append(dt)
            prev_after = float(prev.get("tension_after", 0.0))
            before_next = float(cur.get("tension_before", 0.0))
            # Valid decay sample: tension fell strictly between two injections.
            if prev_after > 0.0 and 0.0 < before_next < prev_after:
                ratio = prev_after / before_next
                if ratio > 1.0:
                    tau = dt / math.log(ratio)
                    if tau > 0.0:
                        tau_samples.append(tau)

    observed_tau_s = _median(tau_samples)
    observed_half_life_s = math.log(2.0) * observed_tau_s if observed_tau_s else 0.0
    configured_tau_s = float(injects[0].get("tau_s", 0.0)) if injects else 0.0
    configured_half_life_s = math.log(2.0) * configured_tau_s if configured_tau_s else 0.0

    peak_tension = max((float(e.get("tension_after", 0.0)) for e in injects), default=0.0)

    return {
        "injection_count": injection_count,
        "prompt_trigger_count": trigger_count,
        "prompt_trigger_rate": trigger_rate,
        "key_count": len(by_key),
        "decay_sample_count": len(tau_samples),
        "observed_tau_s": observed_tau_s,
        "observed_half_life_s": observed_half_life_s,
        "configured_tau_s": configured_tau_s,
        "configured_half_life_s": configured_half_life_s,
        "median_interarrival_s": _median(interarrival_samples),
        "peak_tension": peak_tension,
    }
