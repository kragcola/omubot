"""SQLite connection helpers shared by service stores.

The bot keeps several small SQLite databases open for long-running tasks.
Keeping connection PRAGMA in one place makes write-heavy stores behave more
consistently without forcing a large storage rewrite.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

_L = logger.bind(channel="sqlite")


async def connect_sqlite(
    db_path: str | Path,
    *,
    row_factory: bool = True,
    busy_timeout_ms: int = 5000,
) -> aiosqlite.Connection:
    """Open a service SQLite connection with Omubot's default PRAGMA set."""
    path = Path(db_path)
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)

    db = await aiosqlite.connect(str(path))
    try:
        if row_factory:
            db.row_factory = aiosqlite.Row

        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    except Exception:
        with contextlib.suppress(Exception):
            await db.close()
        raise
    return db


async def close_with_checkpoint(
    db: aiosqlite.Connection | None,
    *,
    name: str = "?",
) -> None:
    """Best-effort `wal_checkpoint(TRUNCATE)` then close.

    Squashes any committed WAL frames back into the main db file before the
    handle is dropped, so a crash between close and the next open cannot
    replay an out-of-order WAL frame against an inconsistent main file —
    the recurring root cause of slang.db corruption on macOS Docker bind
    mounts. Failures are logged but never propagate; close is still
    attempted.
    """
    if db is None:
        return
    try:
        await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        await db.commit()
    except Exception as exc:
        _L.warning("wal_checkpoint(TRUNCATE) failed | name={} err={}", name, exc)
    try:
        await db.close()
    except Exception as exc:
        _L.warning("aiosqlite close failed | name={} err={}", name, exc)


def close_with_checkpoint_sync(
    db: sqlite3.Connection | None,
    *,
    name: str = "?",
) -> None:
    """Synchronous twin of :func:`close_with_checkpoint` for stores that
    use the stdlib ``sqlite3`` module instead of ``aiosqlite``."""
    if db is None:
        return
    try:
        cur: Any = db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with contextlib.suppress(Exception):
            cur.close()
        db.commit()
    except Exception as exc:
        _L.warning("wal_checkpoint(TRUNCATE) failed | name={} err={}", name, exc)
    try:
        db.close()
    except Exception as exc:
        _L.warning("sqlite3 close failed | name={} err={}", name, exc)

