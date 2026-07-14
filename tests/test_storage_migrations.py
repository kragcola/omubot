"""Public behavior contract for transactional SQLite migrations."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest


def _load_api() -> tuple[Any, Any]:
    try:
        from services.storage.migrations import Migration, MigrationRunner
    except ImportError as exc:
        pytest.fail(
            "missing expected services.storage.migrations public API",
            pytrace=False,
        )
        raise AssertionError("unreachable") from exc
    return Migration, MigrationRunner


def _user_version(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("PRAGMA user_version").fetchone()
    assert row is not None
    return int(row[0])


def _journal_mode(db_path: Path) -> str:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("PRAGMA journal_mode").fetchone()
    assert row is not None
    return str(row[0])


def _table_exists(db_path: Path, table_name: str) -> bool:
    if not db_path.exists():
        return False
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
    return row is not None


def _ledger_rows(db_path: Path) -> list[tuple[str, int, str, str]]:
    if not _table_exists(db_path, "_omubot_schema_migrations"):
        return []
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT db_id, version, name, checksum
            FROM _omubot_schema_migrations
            ORDER BY version
            """
        ).fetchall()
    return [(str(db_id), int(version), str(name), str(checksum)) for db_id, version, name, checksum in rows]


def _ledger_rows_with_adoption(db_path: Path) -> list[tuple[str, int, str, str, int]]:
    if not _table_exists(db_path, "_omubot_schema_migrations"):
        return []
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT db_id, version, name, checksum, adopted
            FROM _omubot_schema_migrations
            ORDER BY version
            """
        ).fetchall()
    return [
        (str(db_id), int(version), str(name), str(checksum), int(adopted))
        for db_id, version, name, checksum, adopted in rows
    ]


def _widget_ids(db_path: Path) -> list[int]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT id FROM widgets ORDER BY id").fetchall()
    return [int(row[0]) for row in rows]


def _migration(
    *,
    version: int = 1,
    apply: Any,
    checksum: str = "sha256:test-v1",
    verify: Any = None,
    adopt_existing: bool = False,
) -> Any:
    migration, _ = _load_api()

    async def default_verify(connection: Any) -> bool:
        cursor = await connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'widgets'"
        )
        return await cursor.fetchone() is not None

    migration_kwargs = {
        "version": version,
        "name": "create_widgets",
        "checksum": checksum,
        "apply": apply,
        "verify": verify or default_verify,
    }
    if adopt_existing:
        migration_kwargs["adopt_existing"] = True
    return migration(
        **migration_kwargs,
    )


@pytest.mark.asyncio
async def test_fresh_migration_applies_schema_version_and_ledger(tmp_path: Path) -> None:
    _, migration_runner = _load_api()
    db_path = tmp_path / "fresh.db"

    async def apply(connection: Any) -> None:
        await connection.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")

    result = await migration_runner(db_path=db_path, db_id="test_db").ensure((_migration(apply=apply),))

    assert result.applied == (1,)
    assert _table_exists(db_path, "widgets")
    assert _user_version(db_path) == 1
    assert _ledger_rows(db_path) == [("test_db", 1, "create_widgets", "sha256:test-v1")]


@pytest.mark.asyncio
async def test_reopening_an_applied_migration_is_idempotent(tmp_path: Path) -> None:
    _, migration_runner = _load_api()
    db_path = tmp_path / "reopen.db"
    apply_calls = 0

    async def apply(connection: Any) -> None:
        nonlocal apply_calls
        apply_calls += 1
        await connection.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")

    migration = _migration(apply=apply)
    await migration_runner(db_path=db_path, db_id="test_db").ensure((migration,))

    result = await migration_runner(db_path=db_path, db_id="test_db").ensure((migration,))

    assert result.applied == ()
    assert apply_calls == 1
    assert _user_version(db_path) == 1
    assert _ledger_rows(db_path) == [("test_db", 1, "create_widgets", "sha256:test-v1")]


@pytest.mark.asyncio
async def test_apply_exception_rolls_back_schema_ledger_and_version(tmp_path: Path) -> None:
    _, migration_runner = _load_api()
    db_path = tmp_path / "failed.db"
    failure = RuntimeError("migration failed")

    async def apply(connection: Any) -> None:
        await connection.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
        raise failure

    with pytest.raises(RuntimeError) as raised:
        await migration_runner(db_path=db_path, db_id="test_db").ensure((_migration(apply=apply),))

    assert raised.value is failure
    assert not _table_exists(db_path, "widgets")
    assert _ledger_rows(db_path) == []
    assert _user_version(db_path) == 0


@pytest.mark.asyncio
async def test_apply_cancellation_rolls_back_and_propagates(tmp_path: Path) -> None:
    _, migration_runner = _load_api()
    db_path = tmp_path / "cancelled.db"
    cancellation = asyncio.CancelledError("migration cancelled")

    async def apply(connection: Any) -> None:
        await connection.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
        raise cancellation

    with pytest.raises(asyncio.CancelledError) as raised:
        await migration_runner(db_path=db_path, db_id="test_db").ensure((_migration(apply=apply),))

    assert raised.value is cancellation
    assert not _table_exists(db_path, "widgets")
    assert _ledger_rows(db_path) == []
    assert _user_version(db_path) == 0


@pytest.mark.asyncio
async def test_migrations_must_start_at_one_without_writing(tmp_path: Path) -> None:
    _, migration_runner = _load_api()
    db_path = tmp_path / "non_contiguous.db"
    apply_called = False

    async def apply(connection: Any) -> None:
        nonlocal apply_called
        apply_called = True
        await connection.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")

    with pytest.raises(ValueError):
        await migration_runner(db_path=db_path, db_id="test_db").ensure(
            (_migration(version=2, apply=apply, checksum="sha256:test-v2"),)
        )

    assert not apply_called
    assert not db_path.exists()


@pytest.mark.asyncio
async def test_verified_legacy_schema_is_adopted_without_applying(tmp_path: Path) -> None:
    _, migration_runner = _load_api()
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO widgets (id) VALUES (17)")

    apply_called = False

    async def apply(connection: Any) -> None:
        nonlocal apply_called
        apply_called = True
        await connection.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")

    result = await migration_runner(db_path=db_path, db_id="test_db").ensure(
        (_migration(apply=apply, adopt_existing=True),)
    )

    assert result.applied == ()
    assert result.adopted == (1,)
    assert not apply_called
    assert _widget_ids(db_path) == [17]
    assert _user_version(db_path) == 1
    assert _ledger_rows_with_adoption(db_path) == [
        ("test_db", 1, "create_widgets", "sha256:test-v1", 1)
    ]


@pytest.mark.asyncio
async def test_partial_legacy_schema_is_rejected_without_applying(tmp_path: Path) -> None:
    _, migration_runner = _load_api()
    db_path = tmp_path / "partial.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE partial_widgets (id INTEGER PRIMARY KEY)")

    apply_called = False

    async def apply(connection: Any) -> None:
        nonlocal apply_called
        apply_called = True
        await connection.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")

    async def verify(_connection: Any) -> bool:
        return False

    with pytest.raises(ValueError):
        await migration_runner(db_path=db_path, db_id="test_db").ensure(
            (_migration(apply=apply, verify=verify, adopt_existing=True),)
        )

    assert not apply_called
    assert _table_exists(db_path, "partial_widgets")
    assert not _table_exists(db_path, "widgets")
    assert _user_version(db_path) == 0
    assert _ledger_rows(db_path) == []


@pytest.mark.asyncio
async def test_empty_database_applies_even_when_adoption_is_allowed(tmp_path: Path) -> None:
    _, migration_runner = _load_api()
    db_path = tmp_path / "empty.db"
    apply_calls = 0

    async def apply(connection: Any) -> None:
        nonlocal apply_calls
        apply_calls += 1
        await connection.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")

    result = await migration_runner(db_path=db_path, db_id="test_db").ensure(
        (_migration(apply=apply, adopt_existing=True),)
    )

    assert result.applied == (1,)
    assert result.adopted == ()
    assert apply_calls == 1
    assert _user_version(db_path) == 1
    assert _ledger_rows_with_adoption(db_path) == [
        ("test_db", 1, "create_widgets", "sha256:test-v1", 0)
    ]


@pytest.mark.asyncio
async def test_checksum_drift_fails_without_changing_schema_or_version(tmp_path: Path) -> None:
    _, migration_runner = _load_api()
    db_path = tmp_path / "checksum_drift.db"
    apply_calls = 0

    async def apply(connection: Any) -> None:
        nonlocal apply_calls
        apply_calls += 1
        await connection.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")

    migration = _migration(apply=apply)
    await migration_runner(db_path=db_path, db_id="test_db").ensure((migration,))
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO widgets (id) VALUES (23)")
        connection.execute(
            "UPDATE _omubot_schema_migrations SET checksum = ? WHERE version = 1",
            ("sha256:tampered",),
        )

    with pytest.raises(ValueError):
        await migration_runner(db_path=db_path, db_id="test_db").ensure((migration,))

    assert apply_calls == 1
    assert _widget_ids(db_path) == [23]
    assert _user_version(db_path) == 1
    assert _ledger_rows(db_path) == [("test_db", 1, "create_widgets", "sha256:tampered")]


@pytest.mark.asyncio
async def test_newer_database_version_fails_without_downgrade(tmp_path: Path) -> None:
    _, migration_runner = _load_api()
    db_path = tmp_path / "newer.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO widgets (id) VALUES (31)")
        connection.execute("PRAGMA user_version=2")
    assert _journal_mode(db_path) == "delete"

    apply_called = False

    async def apply(connection: Any) -> None:
        nonlocal apply_called
        apply_called = True
        await connection.execute("CREATE TABLE replacement (id INTEGER PRIMARY KEY)")

    with pytest.raises(ValueError):
        await migration_runner(db_path=db_path, db_id="test_db").ensure((_migration(apply=apply),))

    assert not apply_called
    assert _widget_ids(db_path) == [31]
    assert not _table_exists(db_path, "replacement")
    assert _user_version(db_path) == 2
    assert _journal_mode(db_path) == "delete"
    assert _ledger_rows(db_path) == []


@pytest.mark.asyncio
async def test_concurrent_runners_apply_migration_once(tmp_path: Path) -> None:
    _, migration_runner = _load_api()
    db_path = tmp_path / "concurrent.db"
    apply_calls = 0
    apply_started = asyncio.Event()
    release_apply = asyncio.Event()

    async def apply(connection: Any) -> None:
        nonlocal apply_calls
        apply_calls += 1
        apply_started.set()
        await release_apply.wait()
        await connection.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")

    migration = _migration(apply=apply)
    first = asyncio.create_task(migration_runner(db_path=db_path, db_id="test_db").ensure((migration,)))
    await asyncio.wait_for(apply_started.wait(), timeout=1)
    second = asyncio.create_task(migration_runner(db_path=db_path, db_id="test_db").ensure((migration,)))
    await asyncio.sleep(0)
    release_apply.set()

    results = await asyncio.gather(first, second)

    assert sorted(result.applied for result in results) == [(), (1,)]
    assert apply_calls == 1
    assert _user_version(db_path) == 1
    assert _ledger_rows(db_path) == [("test_db", 1, "create_widgets", "sha256:test-v1")]
