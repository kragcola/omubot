"""SQLite connection helpers shared by service stores.

The bot keeps several small SQLite databases open for long-running tasks.
Keeping connection PRAGMA in one place makes write-heavy stores behave more
consistently without forcing a large storage rewrite.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite


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
