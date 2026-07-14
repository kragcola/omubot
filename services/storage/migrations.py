"""Transactional, per-database SQLite schema migration ledger."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from services.storage.catalog import ConnectionProfile
from services.storage.sqlite import connect_sqlite, read_user_version_read_only

MigrationApply = Callable[[aiosqlite.Connection], Awaitable[None]]
MigrationVerify = Callable[[aiosqlite.Connection], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    checksum: str
    apply: MigrationApply
    verify: MigrationVerify
    adopt_existing: bool = False


@dataclass(frozen=True, slots=True)
class MigrationResult:
    current_version: int
    applied: tuple[int, ...]
    adopted: tuple[int, ...] = ()


class MigrationRunner:
    def __init__(
        self,
        *,
        db_path: str | Path,
        db_id: str,
        profile: ConnectionProfile = ConnectionProfile.WAL_NORMAL,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_id = db_id
        self._profile = profile

    async def ensure(self, migrations: tuple[Migration, ...]) -> MigrationResult:
        ordered = _validate_migrations(migrations)
        preflight_version = await read_user_version_read_only(self._db_path)
        if preflight_version > len(ordered):
            raise ValueError(
                f"database {self._db_id} is newer than this runtime: "
                f"current={preflight_version} target={len(ordered)}"
            )
        db = await connect_sqlite(self._db_path, profile=self._profile)
        applied: list[int] = []
        adopted: list[int] = []
        try:
            await db.execute("BEGIN IMMEDIATE")
            has_existing_schema = await _has_user_schema(db)
            await _create_ledger(db)
            current_version = await _read_user_version(db)
            await self._verify_applied(db, ordered, current_version)

            for migration in ordered:
                if migration.version <= current_version:
                    continue
                if migration.version == 1 and current_version == 0 and has_existing_schema:
                    if not migration.adopt_existing or not await migration.verify(db):
                        raise ValueError(f"legacy schema fingerprint mismatch: {self._db_id}")
                    await self._record_migration(db, migration, adopted=True)
                    await db.execute("PRAGMA user_version=1")
                    current_version = 1
                    adopted.append(1)
                    continue
                await migration.apply(db)
                if not await migration.verify(db):
                    raise RuntimeError(
                        f"migration verification failed: {self._db_id} v{migration.version}"
                    )
                await self._record_migration(db, migration, adopted=False)
                await db.execute(f"PRAGMA user_version={migration.version}")
                current_version = migration.version
                applied.append(migration.version)
            await db.commit()
            return MigrationResult(
                current_version=current_version,
                applied=tuple(applied),
                adopted=tuple(adopted),
            )
        except BaseException:
            await _shielded_rollback(db)
            raise
        finally:
            await _shielded_close(db)

    async def _verify_applied(
        self,
        db: aiosqlite.Connection,
        migrations: tuple[Migration, ...],
        current_version: int,
    ) -> None:
        by_version = {migration.version: migration for migration in migrations}
        if current_version > len(migrations):
            raise ValueError(
                f"database {self._db_id} is newer than this runtime: "
                f"current={current_version} target={len(migrations)}"
            )
        for version in range(1, current_version + 1):
            migration = by_version[version]
            cursor = await db.execute(
                """
                SELECT name, checksum
                FROM _omubot_schema_migrations
                WHERE db_id = ? AND version = ?
                """,
                (self._db_id, version),
            )
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
            if row is None:
                raise ValueError(f"missing migration ledger row: {self._db_id} v{version}")
            if row["name"] != migration.name or row["checksum"] != migration.checksum:
                raise ValueError(f"migration ledger drift: {self._db_id} v{version}")
            if not await migration.verify(db):
                raise RuntimeError(f"applied schema verification failed: {self._db_id} v{version}")

    async def _record_migration(
        self,
        db: aiosqlite.Connection,
        migration: Migration,
        *,
        adopted: bool,
    ) -> None:
        await db.execute(
            """
            INSERT INTO _omubot_schema_migrations (
                db_id, version, name, checksum, applied_at, adopted
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                self._db_id,
                migration.version,
                migration.name,
                migration.checksum,
                datetime.now(UTC).isoformat(),
                int(adopted),
            ),
        )


def _validate_migrations(migrations: tuple[Migration, ...]) -> tuple[Migration, ...]:
    ordered = tuple(sorted(migrations, key=lambda migration: migration.version))
    versions = tuple(migration.version for migration in ordered)
    expected = tuple(range(1, len(ordered) + 1))
    if versions != expected:
        raise ValueError(f"migration versions must be contiguous from 1: {versions}")
    for migration in ordered:
        if not migration.name.strip() or not migration.checksum.strip():
            raise ValueError(f"migration v{migration.version} requires name and checksum")
    return ordered


async def _create_ledger(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS _omubot_schema_migrations (
            db_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            adopted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (db_id, version)
        )
        """
    )


async def _has_user_schema(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type IN ('table', 'index', 'view', 'trigger')
          AND name NOT LIKE 'sqlite_%'
          AND name != '_omubot_schema_migrations'
        LIMIT 1
        """
    )
    try:
        return await cursor.fetchone() is not None
    finally:
        await cursor.close()


async def _read_user_version(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("PRAGMA user_version")
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    return int(row[0]) if row is not None else 0


async def _shielded_rollback(db: aiosqlite.Connection) -> None:
    task = asyncio.create_task(db.rollback())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task


async def _shielded_close(db: aiosqlite.Connection) -> None:
    task = asyncio.create_task(db.close())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
