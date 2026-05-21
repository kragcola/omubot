"""Regression tests for `services.storage.sqlite` close helpers.

These exist primarily because `close_with_checkpoint` is called from store
shutdown paths that are themselves wrapped in `wait_for(...)` during graceful
bot shutdown — see Agent Discipline D2. A cancellation mid-checkpoint must
not corrupt the on-disk state or leave an aiosqlite connection thread alive.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from pathlib import Path

import pytest

from services.storage import (
    close_with_checkpoint,
    close_with_checkpoint_sync,
    connect_sqlite,
)


@pytest.mark.asyncio
async def test_close_with_checkpoint_truncates_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "checkpoint.db"
    db = await connect_sqlite(db_path)
    await db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, payload TEXT)")
    await db.executemany(
        "INSERT INTO t (payload) VALUES (?)",
        [(f"row-{i}",) for i in range(64)],
    )
    await db.commit()

    wal = db_path.with_suffix(db_path.suffix + "-wal")
    assert wal.exists(), "WAL file should exist after writes in WAL mode"
    assert wal.stat().st_size > 0

    await close_with_checkpoint(db, name="test")

    # After TRUNCATE checkpoint + close, the WAL must be empty (or removed).
    if wal.exists():
        assert wal.stat().st_size == 0, "WAL must be truncated to 0 bytes"

    # Reopen and verify all rows survived.
    with sqlite3.connect(str(db_path)) as verify:
        (count,) = verify.execute("SELECT COUNT(*) FROM t").fetchone()
    assert count == 64


@pytest.mark.asyncio
async def test_close_with_checkpoint_handles_none() -> None:
    # Guard against double-close / never-initialized stores.
    await close_with_checkpoint(None, name="none")


@pytest.mark.asyncio
async def test_close_with_checkpoint_cancel_path(tmp_path: Path) -> None:
    """Cancelling close_with_checkpoint mid-flight must not leave the on-disk
    db corrupt nor crash the caller. The connection may end up half-closed,
    but a fresh connect_sqlite() against the same path must still succeed."""
    db_path = tmp_path / "cancel.db"
    db = await connect_sqlite(db_path)
    await db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, payload TEXT)")
    await db.executemany(
        "INSERT INTO t (payload) VALUES (?)",
        [(f"row-{i}",) for i in range(32)],
    )
    await db.commit()

    # Force the close to race against an immediate cancellation.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            close_with_checkpoint(db, name="cancel"),
            timeout=0.0001,
        )

    # Best-effort: try to close again cleanly. Either path must leave the file
    # readable by a brand-new connection.
    with contextlib.suppress(Exception):
        await db.close()

    fresh = await connect_sqlite(db_path)
    try:
        cur = await fresh.execute("PRAGMA quick_check")
        row = await cur.fetchone()
        await cur.close()
        assert row is not None and row[0] == "ok"

        cur = await fresh.execute("SELECT COUNT(*) FROM t")
        row = await cur.fetchone()
        await cur.close()
        assert row is not None and row[0] == 32
    finally:
        await close_with_checkpoint(fresh, name="cancel-verify")


def test_close_with_checkpoint_sync_truncates_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "sync.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, payload TEXT)")
    db.executemany(
        "INSERT INTO t (payload) VALUES (?)",
        [(f"row-{i}",) for i in range(32)],
    )
    db.commit()

    wal = db_path.with_suffix(db_path.suffix + "-wal")
    assert wal.exists()
    assert wal.stat().st_size > 0

    close_with_checkpoint_sync(db, name="sync")

    if wal.exists():
        assert wal.stat().st_size == 0


def test_close_with_checkpoint_sync_handles_none() -> None:
    close_with_checkpoint_sync(None, name="none")
