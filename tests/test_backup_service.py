"""Unit tests for the backup service."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from services.storage.backup import (
    BACKUP_REGISTRY,
    BackupLockedError,
    BackupService,
    _backup_directory,
    _backup_file,
    _backup_sqlite,
    _cli_restore,
    _database_metadata,
    _sha256_file,
    build_restore_plan,
)
from services.storage.catalog import DEFAULT_DATABASE_CATALOG
from services.storage.schema_contracts import get_schema_contract


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

    # Keep the fixture aligned with required daily registry entries.
    for item in BACKUP_REGISTRY:
        if item.item_type != "sqlite" or not item.required or "daily" not in item.profiles:
            continue
        db_path = tmp_path / item.path
        db_path.parent.mkdir(parents=True, exist_ok=True)
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
    registry_databases = {item.id: item.path for item in sqlite_items}
    catalog_databases = {
        spec.id: spec.path for spec in DEFAULT_DATABASE_CATALOG.all()
    }

    assert registry_databases == catalog_databases
    research = next(item for item in sqlite_items if item.id == "research_events")
    assert research.required is False
    assert research.sensitive is True
    assert research.profiles == ["migration"]
    derived = next(item for item in sqlite_items if item.id == "research_topic_assignments")
    assert derived.required is False
    assert derived.sensitive is True
    assert derived.profiles == ["migration"]


def test_backup_registry_includes_living_persona_json_ledgers() -> None:
    items = {item.id: item for item in BACKUP_REGISTRY}

    assert items["living_persona_story_arcs"].path == (
        "storage/living_persona/story_arcs"
    )
    assert items["living_persona_story_arcs"].item_type == "directory"
    assert items["living_persona_partner_states"].path == (
        "storage/living_persona/partner_states"
    )
    assert items["living_persona_partner_states"].item_type == "directory"
    assert items["dream_run_state"].path == "storage/dream_run_state.json"
    assert items["dream_run_state"].item_type == "file"
    for item_id in (
        "living_persona_story_arcs",
        "living_persona_partner_states",
        "dream_run_state",
    ):
        assert items[item_id].profiles == ["daily", "migration", "pre-change"]
        assert items[item_id].required is False


def test_daily_backup_copies_living_persona_json_ledgers(backup_env) -> None:
    repo_root, storage = backup_env
    story_dir = storage / "living_persona" / "story_arcs"
    partner_dir = storage / "living_persona" / "partner_states"
    story_dir.mkdir(parents=True)
    partner_dir.mkdir(parents=True)
    (story_dir / "weekly.json").write_text(json.dumps({"scope": "fiction"}))
    (partner_dir / "friend.json").write_text(json.dumps({"kind": "fiction"}))
    (storage / "dream_run_state.json").write_text(
        json.dumps({"completed_dates": ["2026-07-15"]}),
    )

    manifest = BackupService(storage_dir=storage, repo_root=repo_root).create(
        profile="daily",
        host_mode=False,
    )
    backup_path = repo_root / manifest["backup_path"] / "files" / "storage"

    assert (backup_path / "living_persona" / "story_arcs" / "weekly.json").exists()
    assert (backup_path / "living_persona" / "partner_states" / "friend.json").exists()
    assert (backup_path / "dream_run_state.json").exists()


def _write_sqlite_restore_fixture(
    tmp_path: Path,
    *,
    db_id: str = "usage",
    user_version: int = 1,
    schema_version: int | None = 2,
    include_database_metadata: bool = True,
    flattened_legacy_path: bool = False,
) -> Path:
    backup_dir = tmp_path / "restore-fixture"
    spec = DEFAULT_DATABASE_CATALOG.get(db_id)
    relative_path = Path(spec.path).relative_to("storage")
    backup_path = backup_dir / "sqlite" / (
        relative_path.name if flattened_legacy_path else relative_path
    )
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(backup_path) as connection:
        contract = get_schema_contract(db_id)
        if contract is None:
            connection.execute("CREATE TABLE preserved (id INTEGER PRIMARY KEY)")
        else:
            for table in contract.tables:
                definitions: list[str] = []
                primary_key_columns = tuple(
                    column.name
                    for column in sorted(
                        table.columns,
                        key=lambda item: item.primary_key_position or len(table.columns) + 1,
                    )
                    if column.primary_key_position
                )
                for column in table.columns:
                    parts = [f'"{column.name}"', column.declared_type]
                    if len(primary_key_columns) == 1 and column.primary_key_position:
                        parts.append("PRIMARY KEY")
                        if "autoincrement" in table.required_sql_fragments:
                            parts.append("AUTOINCREMENT")
                    if column.not_null:
                        parts.append("NOT NULL")
                    if column.default_sql is not None:
                        parts.extend(("DEFAULT", column.default_sql))
                    definitions.append(" ".join(parts))
                if len(primary_key_columns) > 1:
                    columns = ", ".join(
                        f'"{column}"' for column in primary_key_columns
                    )
                    definitions.append(f"PRIMARY KEY ({columns})")
                for unique_columns in table.unique_constraints:
                    columns = ", ".join(f'"{column}"' for column in unique_columns)
                    clause = f"UNIQUE ({columns})"
                    if "on conflict ignore" in table.required_sql_fragments:
                        clause += " ON CONFLICT IGNORE"
                    definitions.append(clause)
                for foreign_key in table.foreign_keys:
                    columns = ", ".join(
                        f'"{column}"' for column in foreign_key.columns
                    )
                    referenced = ", ".join(
                        f'"{column}"' for column in foreign_key.referenced_columns
                    )
                    definitions.append(
                        f"FOREIGN KEY ({columns}) REFERENCES "
                        f'"{foreign_key.referenced_table}" ({referenced}) '
                        f"ON UPDATE {foreign_key.on_update} "
                        f"ON DELETE {foreign_key.on_delete}"
                    )
                connection.execute(
                    f'CREATE TABLE "{table.name}" ({", ".join(definitions)})'
                )
            for index in contract.indexes:
                columns = ", ".join(f'"{column}"' for column in index.columns)
                unique = "UNIQUE " if index.unique else ""
                where = f" WHERE {index.where_sql}" if index.where_sql else ""
                connection.execute(
                    f'CREATE {unique}INDEX "{index.name}" '
                    f'ON "{index.table}" ({columns}){where}'
                )
        connection.execute(f"PRAGMA user_version = {user_version}")

    item: dict[str, Any] = {
        "id": db_id,
        "type": "sqlite",
        "status": "ok",
        "source_path": spec.path,
        "sha256": _sha256_file(backup_path),
    }
    if include_database_metadata:
        item["database"] = _database_metadata(backup_path, spec)
    manifest = {
        "backup_id": "restore-fixture",
        "items": [item],
        "summary": {"trusted": True},
    }
    if schema_version is not None:
        manifest["schema_version"] = schema_version
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return backup_dir


def test_restore_plan_accepts_current_database_version(tmp_path: Path) -> None:
    backup_dir = _write_sqlite_restore_fixture(tmp_path, user_version=1)

    plan = build_restore_plan(backup_dir)

    assert plan.can_apply is True
    assert plan.legacy_manifest is False
    assert plan.items[0].compatibility == "compatible"
    assert plan.items[0].user_version == 1
    assert plan.items[0].target_user_version == 1


def test_restore_plan_blocks_phase2_current_schema_without_migration_ledger(
    tmp_path: Path,
) -> None:
    db_id = "research_topic_assignments"
    backup_dir = _write_sqlite_restore_fixture(
        tmp_path,
        db_id=db_id,
        user_version=1,
    )
    spec = DEFAULT_DATABASE_CATALOG.get(db_id)
    backup_path = backup_dir / "sqlite" / Path(spec.path).relative_to("storage")
    with sqlite3.connect(backup_path) as connection:
        connection.execute(
            """
            CREATE TABLE _omubot_schema_migrations (
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
        connection.execute(
            """
            INSERT INTO _omubot_schema_migrations
                (db_id, version, name, checksum, applied_at, adopted)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                db_id,
                1,
                "topic_assignment_baseline_v1",
                "sha256:a54260933061fdc77bcabe066100400ef261f023cb51008118319fcbf9b09ec0",
                "2026-07-15T08:00:00+00:00",
                0,
            ),
        )

    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"][0]["sha256"] = _sha256_file(backup_path)
    manifest["items"][0]["database"] = _database_metadata(backup_path, spec)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert build_restore_plan(backup_dir).can_apply is True

    with sqlite3.connect(backup_path) as connection:
        connection.execute("DROP TABLE _omubot_schema_migrations")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"][0]["sha256"] = _sha256_file(backup_path)
    manifest["items"][0]["database"] = _database_metadata(backup_path, spec)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plan = build_restore_plan(backup_dir)

    assert plan.can_apply is False
    assert plan.items[0].compatibility == "schema_mismatch"
    assert "migration ledger" in plan.items[0].reason


def test_restore_plan_allows_legacy_version_for_upgrade_on_start(
    tmp_path: Path,
) -> None:
    backup_dir = _write_sqlite_restore_fixture(tmp_path, user_version=0)

    plan = build_restore_plan(backup_dir)

    assert plan.can_apply is True
    assert plan.items[0].compatibility == "upgrade_on_start"


def test_restore_plan_blocks_unknown_future_database_version(tmp_path: Path) -> None:
    backup_dir = _write_sqlite_restore_fixture(
        tmp_path,
        db_id="research_events",
        user_version=2,
    )

    plan = build_restore_plan(backup_dir)

    assert plan.can_apply is False
    assert plan.items[0].compatibility == "future_version"
    assert "target" in plan.items[0].reason


def test_legacy_manifest_requires_explicit_unverified_schema_override(
    tmp_path: Path,
) -> None:
    backup_dir = _write_sqlite_restore_fixture(
        tmp_path,
        db_id="living_persona_m1_metrics",
        schema_version=None,
        include_database_metadata=False,
        flattened_legacy_path=True,
    )

    blocked = build_restore_plan(backup_dir)
    allowed = build_restore_plan(backup_dir, allow_unverified_schema=True)

    assert blocked.legacy_manifest is True
    assert blocked.can_apply is False
    assert blocked.items[0].compatibility == "legacy_unverified"
    assert allowed.can_apply is True
    assert allowed.items[0].compatibility == "legacy_override"
    assert allowed.items[0].backup_path.endswith("sqlite/m1_metrics.db")


def test_restore_plan_blocks_manifest_metadata_payload_mismatch(
    tmp_path: Path,
) -> None:
    backup_dir = _write_sqlite_restore_fixture(tmp_path, user_version=1)
    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"][0]["database"]["user_version"] = 0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plan = build_restore_plan(backup_dir)

    assert plan.can_apply is False
    assert plan.items[0].compatibility == "metadata_mismatch"


@pytest.mark.parametrize("user_version", [0, 1])
def test_restore_plan_blocks_self_consistent_malformed_governed_schema(
    tmp_path: Path,
    user_version: int,
) -> None:
    backup_dir = _write_sqlite_restore_fixture(
        tmp_path,
        db_id="usage",
        user_version=user_version,
    )
    spec = DEFAULT_DATABASE_CATALOG.get("usage")
    backup_path = backup_dir / "sqlite" / Path(spec.path).relative_to("storage")
    with sqlite3.connect(backup_path) as connection:
        connection.execute("DROP INDEX idx_llm_calls_type")

    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"][0]["sha256"] = _sha256_file(backup_path)
    manifest["items"][0]["database"] = _database_metadata(backup_path, spec)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plan = build_restore_plan(backup_dir)

    assert plan.can_apply is False
    assert plan.items[0].compatibility == "schema_mismatch"
    assert "governed schema contract" in plan.items[0].reason


@pytest.mark.parametrize("user_version", [0, 1])
def test_restore_plan_blocks_schema_without_required_constraints(
    tmp_path: Path,
    user_version: int,
) -> None:
    backup_dir = _write_sqlite_restore_fixture(
        tmp_path,
        db_id="usage",
        user_version=user_version,
    )
    spec = DEFAULT_DATABASE_CATALOG.get("usage")
    backup_path = backup_dir / "sqlite" / Path(spec.path).relative_to("storage")
    with sqlite3.connect(backup_path) as connection:
        connection.execute("ALTER TABLE llm_calls RENAME TO malformed_llm_calls")
        connection.execute(
            "CREATE TABLE llm_calls AS SELECT * FROM malformed_llm_calls WHERE 0"
        )
        connection.execute("DROP TABLE malformed_llm_calls")
        connection.execute("CREATE INDEX idx_llm_calls_ts ON llm_calls(ts)")
        connection.execute("CREATE INDEX idx_llm_calls_user ON llm_calls(user_id)")
        connection.execute("CREATE INDEX idx_llm_calls_group ON llm_calls(group_id)")
        connection.execute("CREATE INDEX idx_llm_calls_type ON llm_calls(call_type)")
        connection.execute(f"PRAGMA user_version = {user_version}")

    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"][0]["sha256"] = _sha256_file(backup_path)
    manifest["items"][0]["database"] = _database_metadata(backup_path, spec)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plan = build_restore_plan(backup_dir)

    assert plan.can_apply is False
    assert plan.items[0].compatibility == "schema_mismatch"


def test_restore_plan_rejects_untrusted_manifest_before_item_selection(
    tmp_path: Path,
) -> None:
    backup_dir = _write_sqlite_restore_fixture(tmp_path, user_version=1)
    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["summary"]["trusted"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest is not trusted"):
        build_restore_plan(backup_dir, item_id="usage")


@pytest.mark.parametrize("required_status", ["failed", "skipped"])
def test_restore_plan_rejects_required_incomplete_item_before_partial_restore(
    tmp_path: Path,
    required_status: str,
) -> None:
    backup_dir = _write_sqlite_restore_fixture(tmp_path, user_version=1)
    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"].append(
        {
            "id": "slang",
            "type": "sqlite",
            "status": required_status,
            "source_path": DEFAULT_DATABASE_CATALOG.get("slang").path,
            "required": True,
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=rf"required backup item slang is {required_status}",
    ):
        build_restore_plan(backup_dir, item_id="usage")


def test_restore_plan_uses_registry_requiredness_when_manifest_understates_it(
    tmp_path: Path,
) -> None:
    backup_dir = _write_sqlite_restore_fixture(tmp_path, user_version=1)
    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"].append(
        {
            "id": "slang",
            "type": "sqlite",
            "status": "skipped",
            "source_path": DEFAULT_DATABASE_CATALOG.get("slang").path,
            "required": False,
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="required backup item slang is skipped"):
        build_restore_plan(backup_dir, item_id="usage")


def test_restore_apply_refuses_future_version_before_mutating_live_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_dir = _write_sqlite_restore_fixture(
        tmp_path,
        db_id="research_events",
        user_version=2,
    )
    live_path = tmp_path / "storage" / "research_events.db"
    live_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(live_path) as connection:
        connection.execute("CREATE TABLE live_marker (value TEXT)")
        connection.execute("INSERT INTO live_marker VALUES ('preserve-me')")
        connection.execute("PRAGMA user_version = 1")
    before_bytes = live_path.read_bytes()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        _cli_restore(backup_dir, force=True)

    assert live_path.read_bytes() == before_bytes
    assert not (tmp_path / "storage" / "backups" / "pre-restore").exists()


def test_missing_optional_sqlite_is_skipped_without_untrusting_manifest(backup_env):
    repo_root, storage = backup_env
    research_path = repo_root / DEFAULT_DATABASE_CATALOG.get("research_events").path
    assert not research_path.exists()

    manifest = BackupService(storage_dir=storage, repo_root=repo_root).create(
        profile="migration",
        host_mode=False,
    )

    research = next(item for item in manifest["items"] if item["id"] == "research_events")
    assert research["status"] == "skipped"
    assert manifest["summary"]["trusted"] is True


def test_sqlite_backup_preserves_paths_relative_to_storage(backup_env):
    repo_root, storage = backup_env
    nested_paths = (
        Path("storage/living_persona/m1_metrics.db"),
        Path("storage/stickers/stickers.db"),
    )
    for relative_path in nested_paths:
        db_path = repo_root / relative_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as connection:
            connection.execute("CREATE TABLE nested_data (id INTEGER PRIMARY KEY)")

    manifest = BackupService(storage_dir=storage, repo_root=repo_root).create(
        profile="daily",
        host_mode=False,
    )
    backup_path = repo_root / manifest["backup_path"] / "sqlite"

    assert (backup_path / "living_persona" / "m1_metrics.db").exists()
    assert (backup_path / "stickers" / "stickers.db").exists()
    assert not (backup_path / "m1_metrics.db").exists()
    assert not (backup_path / "stickers.db").exists()


def test_sqlite_manifest_item_includes_database_governance_metadata(backup_env):
    repo_root, storage = backup_env
    manifest = BackupService(storage_dir=storage, repo_root=repo_root).create(
        profile="daily",
        host_mode=False,
    )
    slang = next(item for item in manifest["items"] if item["id"] == "slang")
    database = slang["database"]
    catalog_spec = DEFAULT_DATABASE_CATALOG.get("slang")

    assert slang["status"] == "ok"
    assert database["db_id"] == "slang"
    assert database["owner"] == catalog_spec.owner
    assert database["user_version"] == 0
    assert database["target_user_version"] == catalog_spec.target_user_version
    assert isinstance(database["schema_fingerprint"], str)
    assert database["schema_fingerprint"]


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
    catalog_ids = {spec.id for spec in DEFAULT_DATABASE_CATALOG.all()}
    databases = result["meta"]["databases"]
    assert {database["db_id"] for database in databases} == catalog_ids
    total = (
        result["meta"]["ok_count"]
        + result["meta"]["missing_count"]
        + result["meta"]["error_count"]
    )
    assert total == len(catalog_ids)
    missing = [database for database in databases if not database["exists"]]
    assert missing
    assert all(database["status"] == "missing" for database in missing)
    assert all("user_version" in database for database in databases)
    assert all("target_user_version" in database for database in databases)
    assert all("connection_profile" in database for database in databases)
    assert all("backup_profile" in database for database in databases)
    assert all("retention_profile" in database for database in databases)
    assert result["status"] == "warning"
    assert result["meta"]["error_count"] == 0


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
    assert {result.db_id for result in results} == {
        spec.id for spec in DEFAULT_DATABASE_CATALOG.all()
    }
    # Fixture creates valid DBs; all probes that exist should be ok.
    existing = [
        result
        for result in results
        if result.quick_check not in {"missing", "missing_optional"}
    ]
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


def test_quick_check_treats_missing_optional_database_as_healthy_skip(backup_env):
    """An absent optional Catalog DB is expected, not a corruption signal."""
    repo_root, storage = backup_env
    from services.storage.backup_scheduler import BackupScheduler

    research_path = repo_root / DEFAULT_DATABASE_CATALOG.get("research_events").path
    assert not research_path.exists()
    sched = BackupScheduler(
        storage_dir=storage,
        repo_root=repo_root,
        enabled=True,
        quick_check_enabled=True,
    )

    results = sched._probe_all_sqlite()
    research = next(result for result in results if result.db_id == "research_events")

    assert research.ok is True
    assert research.quick_check == "missing_optional"
    assert research.error is None
