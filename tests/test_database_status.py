"""Read-only DatabaseCatalog status and capacity contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from services.storage.catalog import DEFAULT_DATABASE_CATALOG
from services.storage.status import inspect_database_catalog


def _create_database(path: Path, *, user_version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE preserved (id INTEGER PRIMARY KEY)")
        connection.execute(f"PRAGMA user_version = {user_version}")


def test_status_snapshot_covers_exact_catalog_with_profiles(tmp_path: Path) -> None:
    usage_path = DEFAULT_DATABASE_CATALOG.resolve(tmp_path, "usage")
    _create_database(usage_path, user_version=1)

    snapshot = inspect_database_catalog(tmp_path)
    by_id = {item.db_id: item for item in snapshot.items}
    usage = by_id["usage"]

    assert set(by_id) == {spec.id for spec in DEFAULT_DATABASE_CATALOG.all()}
    assert snapshot.to_dict()["summary"]["total"] == 21
    assert usage.exists is True
    assert usage.status == "ok"
    assert usage.user_version == 1
    assert usage.target_user_version == 1
    assert usage.version_status == "current"
    assert usage.owner == DEFAULT_DATABASE_CATALOG.get("usage").owner
    assert usage.connection_profile == "wal_normal"
    assert usage.backup_profile == "daily"
    assert usage.retention_profile == "forever"
    assert usage.size_bytes > 0
    assert by_id["research_events"].exists is False
    assert by_id["research_events"].status == "missing"


def test_status_inspection_does_not_mutate_legacy_database(tmp_path: Path) -> None:
    db_path = DEFAULT_DATABASE_CATALOG.resolve(tmp_path, "block_trace")
    _create_database(db_path, user_version=0)
    before_bytes = db_path.read_bytes()

    snapshot = inspect_database_catalog(tmp_path)
    item = next(row for row in snapshot.items if row.db_id == "block_trace")

    assert item.status == "ok"
    assert item.version_status == "legacy"
    assert db_path.read_bytes() == before_bytes
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert tables == {"preserved"}


def test_unknown_future_version_is_reported_as_error_without_downgrade(
    tmp_path: Path,
) -> None:
    db_path = DEFAULT_DATABASE_CATALOG.resolve(tmp_path, "research_events")
    _create_database(db_path, user_version=2)

    snapshot = inspect_database_catalog(tmp_path)
    item = next(row for row in snapshot.items if row.db_id == "research_events")

    assert item.status == "error"
    assert item.version_status == "future"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_status_reads_uncheckpointed_wal_state_and_capacity(tmp_path: Path) -> None:
    db_path = DEFAULT_DATABASE_CATALOG.resolve(tmp_path, "usage")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    writer = sqlite3.connect(db_path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE wal_only (id INTEGER PRIMARY KEY)")
        writer.execute("PRAGMA user_version=1")
        writer.commit()

        wal_path = Path(f"{db_path}-wal")
        assert wal_path.exists()
        snapshot = inspect_database_catalog(tmp_path)
        usage = next(row for row in snapshot.items if row.db_id == "usage")

        sidecar_bytes = sum(
            path.stat().st_size
            for path in (wal_path, Path(f"{db_path}-shm"))
            if path.exists()
        )
        assert usage.user_version == 1
        assert usage.version_status == "current"
        assert usage.journal_mode == "wal"
        assert usage.quick_check == "ok"
        assert usage.size_bytes >= db_path.stat().st_size + sidecar_bytes
    finally:
        writer.close()
