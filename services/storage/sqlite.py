"""SQLite connection helpers shared by service stores.

The bot keeps several small SQLite databases open for long-running tasks.
Keeping connection PRAGMA in one place makes write-heavy stores behave more
consistently without forcing a large storage rewrite.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

from services.storage.catalog import ConnectionProfile

_L = logger.bind(channel="sqlite")


async def connect_sqlite(
    db_path: str | Path,
    *,
    row_factory: bool = True,
    busy_timeout_ms: int = 5000,
    profile: ConnectionProfile = ConnectionProfile.WAL_NORMAL,
) -> aiosqlite.Connection:
    """Open a service SQLite connection with a declared PRAGMA profile."""
    if not isinstance(profile, ConnectionProfile):
        raise ValueError(f"invalid SQLite connection profile: {profile!r}")

    path = Path(db_path)
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)

    db = await aiosqlite.connect(str(path))
    try:
        if row_factory:
            db.row_factory = aiosqlite.Row

        if profile is ConnectionProfile.WAL_NORMAL:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
        elif profile is ConnectionProfile.DELETE_FULL:
            await db.execute("PRAGMA journal_mode=DELETE")
            await db.execute("PRAGMA synchronous=FULL")
        else:
            await db.execute("PRAGMA journal_mode=DELETE")
            await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    except BaseException:
        await _close_failed_connection(db)
        raise
    return db


async def _close_failed_connection(db: aiosqlite.Connection) -> None:
    close_task = asyncio.create_task(
        db.close(),
        name="sqlite-connect-failure-close",
    )
    while not close_task.done():
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            continue
    with contextlib.suppress(BaseException):
        close_task.result()


async def read_user_version_read_only(db_path: str | Path) -> int:
    """Read an existing database version without applying connection profiles."""
    path = Path(db_path)
    if not path.exists():
        return 0
    uri = f"{path.resolve().as_uri()}?mode=ro"
    db = await aiosqlite.connect(uri, uri=True)
    try:
        cursor = await db.execute("PRAGMA user_version")
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return int(row[0]) if row is not None else 0
    finally:
        await db.close()


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
