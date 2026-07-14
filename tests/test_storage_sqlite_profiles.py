"""Behavior contract for the shared SQLite connection profiles."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any, cast

import aiosqlite
import pytest

from services.storage import connect_sqlite
from services.storage.catalog import ConnectionProfile


async def _pragma_value(db: aiosqlite.Connection, name: str) -> object:
    cursor = await db.execute(f"PRAGMA {name}")
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    assert row is not None
    return row[0]


@pytest.mark.asyncio
async def test_connect_sqlite_without_profile_preserves_wal_normal_defaults(
    tmp_path: Path,
) -> None:
    db = await connect_sqlite(
        tmp_path / "default.db",
        busy_timeout_ms=1379,
    )
    try:
        assert await _pragma_value(db, "journal_mode") == "wal"
        assert await _pragma_value(db, "synchronous") == 1
        assert await _pragma_value(db, "foreign_keys") == 1
        assert await _pragma_value(db, "busy_timeout") == 1379
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_connect_sqlite_wal_normal_profile_applies_wal_normal_pragmas(
    tmp_path: Path,
) -> None:
    db = await connect_sqlite(
        tmp_path / "wal-normal.db",
        profile=ConnectionProfile.WAL_NORMAL,
        busy_timeout_ms=2468,
    )
    try:
        assert await _pragma_value(db, "journal_mode") == "wal"
        assert await _pragma_value(db, "synchronous") == 1
        assert await _pragma_value(db, "foreign_keys") == 1
        assert await _pragma_value(db, "busy_timeout") == 2468
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_connect_sqlite_delete_full_profile_applies_delete_full_pragmas(
    tmp_path: Path,
) -> None:
    db = await connect_sqlite(
        tmp_path / "delete-full.db",
        profile=ConnectionProfile.DELETE_FULL,
    )
    try:
        assert await _pragma_value(db, "journal_mode") == "delete"
        assert await _pragma_value(db, "synchronous") == 2
        assert await _pragma_value(db, "foreign_keys") == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_connect_sqlite_short_lived_profile_applies_delete_normal_pragmas(
    tmp_path: Path,
) -> None:
    db = await connect_sqlite(
        tmp_path / "short-lived.db",
        profile=ConnectionProfile.SHORT_LIVED,
    )
    try:
        assert await _pragma_value(db, "journal_mode") == "delete"
        assert await _pragma_value(db, "synchronous") == 1
        assert await _pragma_value(db, "foreign_keys") == 1
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("row_factory", [True, False])
async def test_connect_sqlite_profile_preserves_row_factory_option(
    tmp_path: Path,
    *,
    row_factory: bool,
) -> None:
    db = await connect_sqlite(
        tmp_path / f"row-factory-{row_factory}.db",
        profile=ConnectionProfile.SHORT_LIVED,
        row_factory=row_factory,
    )
    try:
        cursor = await db.execute("SELECT 7 AS value")
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()

        assert row is not None
        if row_factory:
            assert db.row_factory is sqlite3.Row
            assert row["value"] == 7
        else:
            assert db.row_factory is None
            assert row == (7,)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_connect_sqlite_rejects_raw_string_profile_without_leaking_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.storage.sqlite as sqlite_module

    opened: list[aiosqlite.Connection] = []
    real_connect = sqlite_module.aiosqlite.connect

    async def tracked_connect(*args: Any, **kwargs: Any) -> aiosqlite.Connection:
        db = await real_connect(*args, **kwargs)
        opened.append(db)
        return db

    monkeypatch.setattr(sqlite_module.aiosqlite, "connect", tracked_connect)

    try:
        with pytest.raises(ValueError):
            await sqlite_module.connect_sqlite(
                tmp_path / "invalid-profile.db",
                profile=cast(Any, ConnectionProfile.DELETE_FULL.value),
            )

        for db in opened:
            with pytest.raises(ValueError):
                await db.execute("SELECT 1")
    finally:
        for db in opened:
            with contextlib.suppress(Exception):
                await db.close()
