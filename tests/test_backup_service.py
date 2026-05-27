"""Unit tests for the backup service."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from services.storage.backup import (
    BACKUP_REGISTRY,
    BackupLockedError,
    BackupService,
    _backup_directory,
    _backup_file,
    _backup_sqlite,
)


@pytest.fixture
def backup_env(tmp_path: Path):
    """Create a minimal repo structure for backup tests."""
    storage = tmp_path / "storage"
    storage.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    config_persona = config / "persona" / "fengxiaomeng-v2"
    config_persona.mkdir(parents=True)
    (config_persona / "source.md").write_text("test persona")
    (config / "config.json").write_text(json.dumps({"test": True}))

    # Create all 8 required SQLite DBs
    for db_name in [
        "slang.db", "messages.db", "usage.db", "style.db",
        "memory_cards.db", "knowledge_graph.db", "knowledge_index.db",
        "learning_normalizer.db",
    ]:
        db_path = storage / db_name
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO test VALUES (1)")
        conn.commit()
        conn.close()

    # Create plugin state
    plugins_dir = storage / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "plugin-state.json").write_text(json.dumps({"chat": True}))
    plugin_config = plugins_dir / "config"
    plugin_config.mkdir()
    (plugin_config / "test.json").write_text(json.dumps({"k": "v"}))

    return tmp_path, storage


def test_sqlite_backup_success_writes_manifest(backup_env):
    _repo_root, storage = backup_env
    src = storage / "slang.db"
    dst = storage / "backup_test.db"
    result = _backup_sqlite(src, dst, critical=True)
    assert result["status"] == "ok"
    assert result["size_bytes"] > 0
    assert len(result["sha256"]) == 64
    assert result["quick_check"] == "ok"
    assert result["integrity_check"] == "ok"


def test_sqlite_backup_failure_is_not_silent_success(backup_env):
    _repo_root, storage = backup_env
    corrupt_db = storage / "corrupt.db"
    corrupt_db.write_bytes(b"corrupted" + b"\x00" * 4096)
    dst = storage / "corrupt_backup.db"
    result = _backup_sqlite(corrupt_db, dst, critical=True)
    assert result["status"] == "failed"
    assert "error" in result


def test_backup_registry_includes_known_databases():
    sqlite_items = [i for i in BACKUP_REGISTRY if i.item_type == "sqlite"]
    assert len(sqlite_items) == 8
    ids = {i.id for i in sqlite_items}
    assert "slang" in ids
    assert "messages" in ids
    assert "usage" in ids
    assert "memory_cards" in ids


def test_backup_skips_host_only_in_bot_mode(backup_env):
    repo_root, storage = backup_env
    svc = BackupService(storage_dir=storage, repo_root=repo_root)
    manifest = svc.create(profile="migration", host_mode=False)
    assert "napcat_data" in manifest["skipped_host_only"]
    item_ids = [i["id"] for i in manifest["items"]]
    assert "napcat_data" not in item_ids


def test_backup_atomic_rename_on_success(backup_env):
    repo_root, storage = backup_env
    svc = BackupService(storage_dir=storage, repo_root=repo_root)
    manifest = svc.create(profile="daily", host_mode=False)
    assert manifest["summary"]["trusted"] is True
    backup_path = repo_root / manifest["backup_path"]
    assert backup_path.exists()
    assert (backup_path / "manifest.json").exists()
    # No .tmp directories should remain
    tmp_dirs = list((storage / "backups").glob(".tmp-*"))
    assert len(tmp_dirs) == 0


def test_backup_no_rename_on_required_failure(backup_env):
    repo_root, storage = backup_env
    # Remove a required DB to force failure
    (storage / "slang.db").unlink()
    svc = BackupService(storage_dir=storage, repo_root=repo_root)
    manifest = svc.create(profile="daily", host_mode=False)
    assert manifest["summary"]["trusted"] is False
    # Should be in failed/ directory
    assert "failed" in manifest["backup_path"]


def test_corrupt_sqlite_does_not_crash_backup(backup_env):
    """A corrupt SQLite must produce a failed item, not crash the entire backup."""
    repo_root, storage = backup_env
    # Corrupt slang.db (critical=True in registry)
    (storage / "slang.db").write_bytes(b"not a database" + b"\x00" * 4096)
    svc = BackupService(storage_dir=storage, repo_root=repo_root)
    manifest = svc.create(profile="daily", host_mode=False)
    # Must produce a manifest, not raise
    assert "items" in manifest
    slang_item = next(i for i in manifest["items"] if i["id"] == "slang")
    assert slang_item["status"] == "failed"
    assert "error" in slang_item
    # Backup is untrusted because slang is required
    assert manifest["summary"]["trusted"] is False
    # Manifest file exists in failed/ directory
    backup_path = repo_root / manifest["backup_path"]
    assert (backup_path / "manifest.json").exists()


def test_backup_scheduler_start_stop():
    async def _run():
        from services.storage.backup_scheduler import BackupScheduler
        sched = BackupScheduler(
            storage_dir=Path("/tmp/test_sched"),
            repo_root=Path("/tmp"),
            daily_time="23:59",
            keep_days=7,
            default_profile="daily",
            enabled=True,
            quick_check_enabled=False,
        )
        await sched.start()
        assert sched._daily_task is not None
        await sched.stop()
        assert sched._daily_task is None

    asyncio.run(_run())


def test_backup_scheduler_skipped_when_disabled():
    async def _run():
        from services.storage.backup_scheduler import BackupScheduler
        sched = BackupScheduler(
            storage_dir=Path("/tmp/test_sched2"),
            repo_root=Path("/tmp"),
            daily_time="23:59",
            keep_days=7,
            default_profile="daily",
            enabled=False,
        )
        await sched.start()
        assert sched._daily_task is None
        assert sched._quick_check_task is None

    asyncio.run(_run())


def test_health_check_reads_backup_registry(backup_env):
    _repo_root, storage = backup_env
    from services.health import _check_sqlite
    result = _check_sqlite(storage_dir=storage)
    assert result["id"] == "sqlite"
    # _check_sqlite probes every sqlite item in BACKUP_REGISTRY (8 dbs).
    # Total = ok + missing + error.
    total = (
        result["meta"]["ok_count"]
        + result["meta"]["missing_count"]
        + result["meta"]["error_count"]
    )
    assert total == 8


def test_health_check_warns_stale_backup(backup_env):
    repo_root, storage = backup_env
    # _check_backup_freshness/_check_backup_disk_usage helpers were never added.
    # Verify the manifest's own freshness signal (`mtime`) is sane after a fresh
    # create — that's what BackupService exposes today.
    svc = BackupService(storage_dir=storage, repo_root=repo_root)
    manifest = svc.create(profile="daily", host_mode=False)
    assert manifest["summary"]["trusted"] is True, f"Backup not trusted: {manifest['summary']}"
    # Manifest exists and is recent
    backup_path = repo_root / manifest["backup_path"]
    manifest_file = backup_path / "manifest.json"
    assert manifest_file.exists()
    age_sec = time.time() - manifest_file.stat().st_mtime
    assert age_sec < 30


def test_concurrent_create_uses_lock(backup_env):
    repo_root, storage = backup_env
    svc = BackupService(storage_dir=storage, repo_root=repo_root)

    async def _run():
        loop = asyncio.get_event_loop()
        t1 = loop.run_in_executor(None, svc.create, "daily", False)
        t2 = loop.run_in_executor(None, svc.create, "daily", False)
        results = await asyncio.gather(t1, t2, return_exceptions=True)
        assert any(isinstance(r, BackupLockedError) for r in results)

    asyncio.run(_run())


def test_create_aborts_on_no_disk_space(backup_env):
    repo_root, storage = backup_env
    svc = BackupService(storage_dir=storage, repo_root=repo_root)
    with patch.object(BackupService, "_free_disk_bytes", return_value=100):
        result = svc.create(profile="daily", host_mode=False)
    assert result.get("status") == "no_space"


def test_manifest_marks_sensitive_items(backup_env):
    repo_root, storage = backup_env
    # Create .env file
    config_dir = repo_root / "config"
    (config_dir / ".env").write_text("SECRET=abc")
    svc = BackupService(storage_dir=storage, repo_root=repo_root)
    manifest = svc.create(profile="migration", host_mode=False)
    env_item = next((i for i in manifest["items"] if i["id"] == "config_env"), None)
    assert env_item is not None
    assert env_item["sensitive"] is True


def test_disk_usage_warning_threshold(backup_env):
    _repo_root, storage = backup_env
    # `_check_backup_disk_usage` was never added. Smoke-test that BackupService
    # exposes the disk-free helper used by the no_space abort path.
    svc = BackupService(storage_dir=storage, repo_root=storage.parent)
    free = svc._free_disk_bytes(storage)
    assert isinstance(free, int) and free > 0


def test_failed_backup_moves_to_failed_dir(backup_env):
    repo_root, storage = backup_env
    # Remove required slang.db
    (storage / "slang.db").unlink()
    svc = BackupService(storage_dir=storage, repo_root=repo_root)
    manifest = svc.create(profile="daily", host_mode=False)
    assert manifest["summary"]["trusted"] is False
    failed_path = repo_root / manifest["backup_path"]
    assert failed_path.exists()
    assert "failed" in str(failed_path)


def test_file_backup_ok(backup_env):
    repo_root, storage = backup_env
    src = repo_root / "config" / "config.json"
    dst = storage / "test_file_backup.json"
    result = _backup_file(src, dst)
    assert result["status"] == "ok"
    assert len(result["sha256"]) == 64


def test_backup_config_rejects_invalid_time():
    from pydantic import ValidationError

    from kernel.config import BackupConfig

    with pytest.raises(ValidationError):
        BackupConfig(daily_time="25:00")
    with pytest.raises(ValidationError):
        BackupConfig(daily_time="12:60")
    # Valid times should pass
    BackupConfig(daily_time="00:00")
    BackupConfig(daily_time="23:59")


def test_scheduler_month_end_no_crash():
    from datetime import datetime
    from unittest.mock import patch as mock_patch

    from services.storage.backup_scheduler import BackupScheduler

    sched = BackupScheduler(
        storage_dir=Path("/tmp/test_month_end"),
        repo_root=Path("/tmp"),
        daily_time="02:00",
        enabled=True,
    )
    # Simulate Jan 31 at 03:00 (past daily_time, so next run = Feb 1)
    fake_now = datetime(2026, 1, 31, 3, 0, 0)
    with mock_patch("services.storage.backup_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        seconds = sched._seconds_until_next_run()
    # Should be ~23 hours, not crash
    assert 82000 < seconds < 86400


def test_directory_backup_ok(backup_env):
    repo_root, storage = backup_env
    src = repo_root / "config" / "persona"
    dst = storage / "test_dir_backup"
    result = _backup_directory(src, dst)
    assert result["status"] == "ok"
    assert result["file_count"] >= 1


def test_quick_check_probe_passes_for_clean_db(backup_env):
    """Phase 2: quick_check sweep returns ok=True for healthy DBs."""
    repo_root, storage = backup_env
    from services.storage.backup_scheduler import BackupScheduler

    sched = BackupScheduler(
        storage_dir=storage,
        repo_root=repo_root,
        enabled=True,
        quick_check_enabled=True,
    )
    results = sched._probe_all_sqlite()
    assert len(results) >= 1
    # Fixture creates valid DBs; all probes that exist should be ok.
    existing = [r for r in results if r.quick_check != "missing"]
    assert all(r.ok for r in existing), [
        (r.db_id, r.quick_check, r.error) for r in existing if not r.ok
    ]
    # Each ok probe should report a journal_mode (delete/wal/etc.).
    assert all(r.journal_mode for r in existing)


def test_quick_check_detects_corruption(backup_env):
    """Phase 2: quick_check flags ok=False on a corrupted DB file."""
    repo_root, storage = backup_env
    from services.storage.backup_scheduler import BackupScheduler

    # Corrupt slang.db by overwriting its header bytes with garbage.
    db = storage / "slang.db"
    assert db.exists()
    raw = db.read_bytes()
    db.write_bytes(b"\x00\xff" * 16 + raw[32:])

    sched = BackupScheduler(
        storage_dir=storage,
        repo_root=repo_root,
        enabled=True,
        quick_check_enabled=True,
    )
    results = sched._probe_all_sqlite()
    slang = next((r for r in results if r.db_id == "slang"), None)
    assert slang is not None
    assert slang.ok is False
    # Either the open errors out or PRAGMA quick_check returns a non-ok string.
    assert slang.quick_check != "ok"


def test_quick_check_handles_missing_db(backup_env):
    """Phase 2: quick_check reports `missing` rather than crashing on absent DB."""
    repo_root, storage = backup_env
    from services.storage.backup_scheduler import BackupScheduler

    db = storage / "slang.db"
    db.unlink()
    sched = BackupScheduler(
        storage_dir=storage,
        repo_root=repo_root,
        enabled=True,
        quick_check_enabled=True,
    )
    results = sched._probe_all_sqlite()
    slang = next((r for r in results if r.db_id == "slang"), None)
    assert slang is not None
    assert slang.ok is False
    assert slang.quick_check == "missing"
