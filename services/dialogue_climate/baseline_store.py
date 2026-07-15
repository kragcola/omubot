"""Durable per-(group,user) Dialogue Climate baseline store."""

from __future__ import annotations

import asyncio
import contextlib
import math
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from loguru import logger

from services.dialogue_climate.state import ClimateState

_L = logger.bind(channel="dialogue_climate")
_DEFAULT_DB_PATH = "storage/living_persona/climate_baselines.db"
_SCHEMA_VERSION = 1
_SCHEMA_FINGERPRINT = (
    ("group_id", "TEXT", 1, 1),
    ("user_id", "TEXT", 1, 2),
    ("baseline_energy", "REAL", 1, 0),
    ("baseline_valence", "REAL", 1, 0),
    ("baseline_openness", "REAL", 1, 0),
    ("revision", "INTEGER", 1, 0),
    ("updated_at", "REAL", 1, 0),
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS climate_baselines (
    group_id          TEXT NOT NULL,
    user_id           TEXT NOT NULL,
    baseline_energy   REAL NOT NULL,
    baseline_valence  REAL NOT NULL,
    baseline_openness REAL NOT NULL,
    revision          INTEGER NOT NULL,
    updated_at        REAL NOT NULL,
    PRIMARY KEY (group_id, user_id)
)
"""

_UPSERT = """
INSERT INTO climate_baselines (
    group_id, user_id, baseline_energy, baseline_valence,
    baseline_openness, revision, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(group_id, user_id) DO UPDATE SET
    baseline_energy=excluded.baseline_energy,
    baseline_valence=excluded.baseline_valence,
    baseline_openness=excluded.baseline_openness,
    revision=excluded.revision,
    updated_at=excluded.updated_at
WHERE excluded.revision > climate_baselines.revision
"""


@dataclass(frozen=True, slots=True)
class _PendingBaseline:
    group_id: str
    user_id: str
    baseline_energy: float
    baseline_valence: float
    baseline_openness: float
    updated_at: float
    stage_order: int
    revision: int | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.group_id, self.user_id)

    def db_row(self) -> tuple[Any, ...]:
        if self.revision is None:
            raise RuntimeError("climate baseline revision was not assigned")
        return (
            self.group_id,
            self.user_id,
            self.baseline_energy,
            self.baseline_valence,
            self.baseline_openness,
            self.revision,
            self.updated_at,
        )


def _valid_snapshot_values(
    baselines: tuple[float, float, float],
    updated_at: float,
) -> bool:
    return (
        all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in baselines)
        and math.isfinite(updated_at)
    )


def _schema_fingerprint(conn: sqlite3.Connection) -> tuple[tuple[str, str, int, int], ...]:
    return tuple(
        (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
        for row in conn.execute("PRAGMA table_info(climate_baselines)")
    )


class ClimateBaselineStore:
    """Stage baseline snapshots in memory and flush them in bounded batches."""

    def __init__(
        self,
        db_path: str = _DEFAULT_DB_PATH,
        *,
        flush_interval_s: float = 30.0,
        max_restore_age_s: float = 90 * 86400.0,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._db_path = str(db_path)
        self._flush_interval_s = max(0.1, float(flush_interval_s))
        self._max_restore_age_s = max(0.0, float(max_restore_age_s))
        self._wall_clock = wall_clock
        self._pending: dict[tuple[str, str], _PendingBaseline] = {}
        self._state_lock = threading.Lock()
        self._flush_lock = asyncio.Lock()
        self._schema_task: asyncio.Task[int] | None = None
        self._start_task: asyncio.Task[None] | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._stage_counter = 0
        self._revision_counter: int | None = None
        self._closed = False

    @property
    def pending_count(self) -> int:
        with self._state_lock:
            return len(self._pending)

    async def start(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            if self._start_task is None:
                self._start_task = asyncio.create_task(
                    self._start_impl(),
                    name="dialogue-climate-baseline-start",
                )
            start_task = self._start_task

        await asyncio.shield(start_task)

    async def _start_impl(self) -> None:
        await self._ensure_schema_ready()
        with self._state_lock:
            if self._closed or self._flush_task is not None:
                return
            self._flush_task = asyncio.create_task(
                self._flush_loop(),
                name="dialogue-climate-baseline-flush",
            )

    def stage(
        self,
        *,
        group_id: str | int | None,
        user_id: str | int | None,
        state: ClimateState,
        updated_at: float | None = None,
    ) -> None:
        key = (str(group_id or ""), str(user_id or ""))
        baseline_energy = float(state.baseline_energy)
        baseline_valence = float(state.baseline_valence)
        baseline_openness = float(state.baseline_openness)
        timestamp = float(self._wall_clock() if updated_at is None else updated_at)
        baselines = (baseline_energy, baseline_valence, baseline_openness)
        if not _valid_snapshot_values(baselines, timestamp):
            raise ValueError("invalid climate baseline snapshot")
        with self._state_lock:
            if self._closed:
                raise RuntimeError("climate baseline store is closing or closed")
            self._stage_counter += 1
            self._pending[key] = _PendingBaseline(
                group_id=key[0],
                user_id=key[1],
                baseline_energy=baseline_energy,
                baseline_valence=baseline_valence,
                baseline_openness=baseline_openness,
                updated_at=timestamp,
                stage_order=self._stage_counter,
            )

    def load(
        self,
        *,
        group_id: str | int | None,
        user_id: str | int | None,
    ) -> dict[str, float] | None:
        self._ensure_schema()
        key = (str(group_id or ""), str(user_id or ""))
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT baseline_energy, baseline_valence, baseline_openness, updated_at "
                    "FROM climate_baselines WHERE group_id = ? AND user_id = ?",
                    key,
                ).fetchone()
        except Exception as exc:
            _L.debug("climate baseline load failed | key={} err={}", key, exc)
            return None
        if row is None:
            return None
        try:
            baseline_energy = float(row[0])
            baseline_valence = float(row[1])
            baseline_openness = float(row[2])
            updated_at = float(row[3])
            now = float(self._wall_clock())
        except (TypeError, ValueError):
            return None
        baselines = (baseline_energy, baseline_valence, baseline_openness)
        if not _valid_snapshot_values(baselines, updated_at) or not math.isfinite(now):
            return None
        age = now - updated_at
        if age < -300.0 or age > self._max_restore_age_s:
            return None
        return {
            "baseline_energy": baseline_energy,
            "baseline_valence": baseline_valence,
            "baseline_openness": baseline_openness,
            "updated_at": updated_at,
        }

    async def flush(self) -> int:
        async with self._flush_lock:
            with self._state_lock:
                if not self._pending:
                    return 0
            await self._ensure_schema_ready()
            with self._state_lock:
                if not self._pending:
                    return 0
                rows = self._pending
                self._pending = {}
                assigned_rows: list[_PendingBaseline] = []
                for row in sorted(rows.values(), key=lambda item: item.stage_order):
                    if row.revision is None:
                        if self._revision_counter is None:
                            raise RuntimeError("climate baseline revision counter is not ready")
                        self._revision_counter += 1
                        row = replace(row, revision=self._revision_counter)
                    assigned_rows.append(row)
            try:
                await self._write_rows(tuple(row.db_row() for row in assigned_rows))
            except BaseException:
                with self._state_lock:
                    for row in assigned_rows:
                        current = self._pending.get(row.key)
                        if current is None or current.stage_order <= row.stage_order:
                            self._pending[row.key] = row
                raise
            return len(rows)

    async def _ensure_schema_ready(self) -> int:
        with self._state_lock:
            if self._schema_task is None:
                self._schema_task = asyncio.create_task(
                    asyncio.to_thread(self._ensure_schema),
                    name="dialogue-climate-baseline-schema",
                )
            schema_task = self._schema_task

        max_revision = await asyncio.shield(schema_task)
        with self._state_lock:
            if self._revision_counter is None or self._revision_counter < max_revision:
                self._revision_counter = max_revision
        return max_revision

    async def close(self) -> None:
        with self._state_lock:
            if self._close_task is None:
                self._closed = True
                self._close_task = asyncio.create_task(
                    self._close_impl(),
                    name="dialogue-climate-baseline-close",
                )
            close_task = self._close_task

        await asyncio.shield(close_task)

    async def _close_impl(self) -> None:
        with self._state_lock:
            start_task = self._start_task
        if start_task is not None:
            with contextlib.suppress(Exception):
                await asyncio.shield(start_task)

        with self._state_lock:
            task = self._flush_task
            self._flush_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.flush()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval_s)
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _L.debug("climate baseline periodic flush failed | err={}", exc)

    async def _write_rows(self, rows: tuple[tuple[Any, ...], ...]) -> None:
        await asyncio.to_thread(self._write_rows_sync, rows)

    def _write_rows_sync(self, rows: tuple[tuple[Any, ...], ...]) -> None:
        self._ensure_schema()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executemany(_UPSERT, rows)
            conn.commit()

    def _ensure_schema(self) -> int:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            [current_version] = conn.execute("PRAGMA user_version").fetchone()
            if int(current_version) > _SCHEMA_VERSION:
                raise RuntimeError(
                    "climate baseline database is newer than this runtime: "
                    f"version={current_version}"
                )
            fingerprint = _schema_fingerprint(conn)
            if fingerprint and fingerprint != _SCHEMA_FINGERPRINT:
                raise RuntimeError("climate baseline schema fingerprint mismatch")
            if int(current_version) == _SCHEMA_VERSION and not fingerprint:
                raise RuntimeError("climate baseline schema fingerprint mismatch")
            conn.execute("PRAGMA journal_mode=WAL")
            if not fingerprint:
                conn.execute(_CREATE_TABLE)
                fingerprint = _schema_fingerprint(conn)
            if fingerprint != _SCHEMA_FINGERPRINT:
                raise RuntimeError("climate baseline schema fingerprint mismatch")
            if int(current_version) == 0:
                conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            conn.commit()
            [max_revision] = conn.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM climate_baselines"
            ).fetchone()
            return int(max_revision)


__all__ = ["ClimateBaselineStore"]
