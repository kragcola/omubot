"""SQLite connection helpers shared by service stores.

The bot keeps several small SQLite databases open for long-running tasks.
Keeping connection PRAGMA in one place makes write-heavy stores behave more
consistently without forcing a large storage rewrite.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
from loguru import logger


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
    if row_factory:
        db.row_factory = aiosqlite.Row

    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return db


async def close_with_checkpoint(db: aiosqlite.Connection, *, name: str = "?") -> None:
    """Best-effort wal_checkpoint(TRUNCATE) before close.

    macOS Docker bind-mount + WAL has a known fsync ordering hazard: a crash
    between close and next open can replay an out-of-order WAL frame on top of
    the main database. Running TRUNCATE squashes the WAL into the main file so
    the next open starts from a single consistent file. The checkpoint is
    advisory — failures are logged and close still proceeds.
    """
    if db is None:
        return
    try:
        await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as exc:
        logger.warning("wal_checkpoint truncate failed for {}: {}", name, exc)
    await db.close()


def close_with_checkpoint_sync(db: sqlite3.Connection, *, name: str = "?") -> None:
    """Sync companion of :func:`close_with_checkpoint` for stores that hold a
    plain :mod:`sqlite3` connection (e.g. KnowledgeIndexStore)."""
    if db is None:
        return
    try:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as exc:
        logger.warning("wal_checkpoint truncate failed for {}: {}", name, exc)
    db.close()
