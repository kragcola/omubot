import json
import shutil
import sqlite3
from pathlib import Path
from types import TracebackType

import pytest

from services.storage.backup import _database_metadata, _sha256_file
from services.storage.catalog import DEFAULT_DATABASE_CATALOG
from tools import living_persona_remediation as remediation


def test_living_persona_remediation_tool_exists() -> None:
    assert Path("tools/living_persona_remediation.py").is_file()


def _seed_cards(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE memory_cards (
                card_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                scope TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                status TEXT NOT NULL,
                content TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        )
        connection.executemany(
            "INSERT INTO memory_cards VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("global_ok", "dream_reflection", "global", "global", "active", "keep", "old"),
                ("group_ok", "dream_reflection", "group", "984198159", "active", "keep", "old"),
                ("bad_global", "dream_reflection", "global", "self", "active", "expire", "old"),
                ("bad_group", "dream_reflection", "group", "wxs", "active", "expire", "old"),
                ("other_source", "manual", "group", "wxs", "active", "keep", "old"),
                ("already_expired", "dream_reflection", "group", "wxs", "expired", "keep", "old"),
            ],
        )


def _write_trusted_backup(root: Path, backup_id: str) -> None:
    target = root / "pre-change" / "2026-07-15"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(json.dumps({
        "backup_id": backup_id,
        "summary": {"trusted": True},
        "items": [
            {"id": "memory_cards", "status": "ok"},
            {"id": "living_persona_story_arcs", "status": "ok"},
            {"id": "living_persona_partner_states", "status": "ok"},
        ],
    }))


def _write_v2_memory_backup(
    root: Path,
    backup_id: str,
    source_db: Path,
    *,
    payload_state: str = "valid",
) -> Path:
    target = root / "pre-change" / backup_id
    payload = target / "sqlite" / "memory_cards.db"
    payload.parent.mkdir(parents=True)
    shutil.copy2(source_db, payload)
    spec = DEFAULT_DATABASE_CATALOG.get("memory_cards")
    manifest = {
        "schema_version": 2,
        "backup_id": backup_id,
        "items": [{
            "id": "memory_cards",
            "type": "sqlite",
            "status": "ok",
            "source_path": spec.path,
            "sha256": _sha256_file(payload),
            "database": _database_metadata(payload, spec),
        }],
        "summary": {"trusted": True},
    }
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if payload_state == "missing":
        payload.unlink()
    elif payload_state == "sha_mismatch":
        with payload.open("ab") as handle:
            handle.write(b"tampered-after-manifest")
    return target


def test_dry_run_reports_only_active_invalid_dream_scopes(tmp_path: Path) -> None:
    db_path = tmp_path / "memory_cards.db"
    _seed_cards(db_path)

    plan = remediation.build_plan(db_path, allowed_group_ids={"984198159", "993065015"})

    assert plan["quick_check"] == "ok"
    assert plan["invalid_active_count"] == 2
    assert plan["valid_active_count"] == 2
    assert plan["candidate_card_ids"] == ["bad_global", "bad_group"]
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_cards WHERE status = 'expired'",
        ).fetchone()[0] == 1


def test_apply_refuses_empty_allowlist_even_with_trusted_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "memory_cards.db"
    backup_root = tmp_path / "backups"
    _seed_cards(db_path)
    _write_v2_memory_backup(backup_root, "pre-change-empty-allow", db_path)

    with pytest.raises(ValueError, match="non-empty allowed_group_ids"):
        remediation.apply_plan(
            db_path,
            allowed_group_ids=set(),
            backup_root=backup_root,
            confirmed_backup_id="pre-change-empty-allow",
        )

    with sqlite3.connect(db_path) as connection:
        statuses = dict(connection.execute(
            "SELECT card_id, status FROM memory_cards",
        ).fetchall())
    assert statuses["bad_global"] == "active"
    assert statuses["bad_group"] == "active"
    assert statuses["group_ok"] == "active"


def test_apply_requires_trusted_memory_backup_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "memory_cards.db"
    backup_root = tmp_path / "backups"
    _seed_cards(db_path)
    _write_v2_memory_backup(
        backup_root,
        "pre-change-verified",
        db_path,
    )

    with pytest.raises(ValueError, match="trusted backup"):
        remediation.apply_plan(
            db_path,
            allowed_group_ids={"984198159"},
            backup_root=backup_root,
            confirmed_backup_id="missing",
        )

    first = remediation.apply_plan(
        db_path,
        allowed_group_ids={"984198159"},
        backup_root=backup_root,
        confirmed_backup_id="pre-change-verified",
    )
    second = remediation.apply_plan(
        db_path,
        allowed_group_ids={"984198159"},
        backup_root=backup_root,
        confirmed_backup_id="pre-change-verified",
    )

    assert first["expired_count"] == 2
    assert second["expired_count"] == 0
    with sqlite3.connect(db_path) as connection:
        rows = dict(connection.execute(
            "SELECT card_id, status FROM memory_cards ORDER BY card_id",
        ).fetchall())
        assert rows["bad_global"] == "expired"
        assert rows["bad_group"] == "expired"
        assert rows["global_ok"] == "active"
        assert rows["group_ok"] == "active"
        assert rows["other_source"] == "active"


def test_apply_rejects_manifest_only_backup_without_sqlite_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "memory_cards.db"
    backup_root = tmp_path / "backups"
    _seed_cards(db_path)
    _write_trusted_backup(backup_root, "manifest-only")

    with pytest.raises(ValueError, match="trusted backup"):
        remediation.apply_plan(
            db_path,
            allowed_group_ids={"984198159"},
            backup_root=backup_root,
            confirmed_backup_id="manifest-only",
        )

    with sqlite3.connect(db_path) as connection:
        statuses = dict(connection.execute(
            "SELECT card_id, status FROM memory_cards",
        ).fetchall())
    assert statuses["bad_global"] == "active"
    assert statuses["bad_group"] == "active"


@pytest.mark.parametrize("payload_state", ["missing", "sha_mismatch"])
def test_apply_rejects_trusted_v2_manifest_with_unusable_memory_payload(
    tmp_path: Path,
    payload_state: str,
) -> None:
    db_path = tmp_path / "memory_cards.db"
    backup_root = tmp_path / "backups"
    _seed_cards(db_path)
    backup_id = f"trusted-v2-{payload_state}"
    _write_v2_memory_backup(
        backup_root,
        backup_id,
        db_path,
        payload_state=payload_state,
    )

    with pytest.raises(ValueError, match="trusted backup"):
        remediation.apply_plan(
            db_path,
            allowed_group_ids={"984198159"},
            backup_root=backup_root,
            confirmed_backup_id=backup_id,
        )

    with sqlite3.connect(db_path) as connection:
        statuses = dict(connection.execute(
            "SELECT card_id, status FROM memory_cards",
        ).fetchall())
    assert statuses["bad_global"] == "active"
    assert statuses["bad_group"] == "active"


def test_apply_rechecks_invalid_scope_inside_write_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "memory_cards.db"
    backup_root = tmp_path / "backups"
    _seed_cards(db_path)
    backup_id = "trusted-v2-toctou"
    _write_v2_memory_backup(backup_root, backup_id, db_path)
    original_build_plan = remediation.build_plan
    calls = 0

    def build_plan_then_repair(*args, **kwargs):
        nonlocal calls
        plan = original_build_plan(*args, **kwargs)
        calls += 1
        if calls == 1:
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "UPDATE memory_cards SET scope = 'global', scope_id = 'global' "
                    "WHERE card_id = 'bad_group'",
                )
        return plan

    monkeypatch.setattr(remediation, "build_plan", build_plan_then_repair)

    result = remediation.apply_plan(
        db_path,
        allowed_group_ids={"984198159"},
        backup_root=backup_root,
        confirmed_backup_id=backup_id,
    )

    with sqlite3.connect(db_path) as connection:
        repaired = connection.execute(
            "SELECT scope, scope_id, status FROM memory_cards WHERE card_id = 'bad_group'",
        ).fetchone()
    assert repaired == ("global", "global", "active")
    assert result["expired_count"] == 1


def test_apply_rejects_duplicate_backup_id_manifests(tmp_path: Path) -> None:
    db_path = tmp_path / "memory_cards.db"
    backup_root = tmp_path / "backups"
    _seed_cards(db_path)
    backup_id = "duplicate-backup-id"
    _write_v2_memory_backup(backup_root, backup_id, db_path)
    # Second manifest with the same backup_id under another tree path.
    _write_v2_memory_backup(backup_root / "alt-root", backup_id, db_path)

    with pytest.raises(ValueError, match="trusted backup"):
        remediation.apply_plan(
            db_path,
            allowed_group_ids={"984198159"},
            backup_root=backup_root,
            confirmed_backup_id=backup_id,
        )

    with sqlite3.connect(db_path) as connection:
        statuses = dict(connection.execute(
            "SELECT card_id, status FROM memory_cards",
        ).fetchall())
    assert statuses["bad_global"] == "active"
    assert statuses["bad_group"] == "active"


def test_apply_aborts_when_live_quick_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "memory_cards.db"
    backup_root = tmp_path / "backups"
    _seed_cards(db_path)
    backup_id = "trusted-v2-quick-check"
    _write_v2_memory_backup(backup_root, backup_id, db_path)

    real_connect = sqlite3.connect
    live_db = db_path.resolve()

    class _WriteConnProxy:
        """Proxy only the live write connection so PRAGMA quick_check can fail
        without patching the immutable sqlite3.Connection type."""

        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner

        def execute(self, sql, *args, **kwargs):  # type: ignore[no-untyped-def]
            if "PRAGMA quick_check" in str(sql):
                class _Row:
                    def __getitem__(self, index: int) -> str:
                        return "corruption detected near page 1"

                return type("Cursor", (), {"fetchone": staticmethod(lambda: _Row())})()
            return self._inner.execute(sql, *args, **kwargs)

        def rollback(self) -> None:
            self._inner.rollback()

        def commit(self) -> None:
            self._inner.commit()

        def close(self) -> None:
            self._inner.close()

        def __enter__(self) -> "_WriteConnProxy":
            self._inner.__enter__()
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            self._inner.__exit__(exc_type, exc, tb)

    def connect_proxy(*args, **kwargs):  # type: ignore[no-untyped-def]
        connection = real_connect(*args, **kwargs)
        target = str(args[0]) if args else ""
        # apply_plan mutates via plain path connect on the live db (not URI mode=ro).
        if target == str(db_path) or target == str(live_db):
            return _WriteConnProxy(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect_proxy)

    with pytest.raises(ValueError, match="quick_check failed"):
        remediation.apply_plan(
            db_path,
            allowed_group_ids={"984198159"},
            backup_root=backup_root,
            confirmed_backup_id=backup_id,
        )

    with real_connect(db_path) as connection:
        statuses = dict(connection.execute(
            "SELECT card_id, status FROM memory_cards",
        ).fetchall())
    assert statuses["bad_global"] == "active"
    assert statuses["bad_group"] == "active"
    assert statuses["global_ok"] == "active"
    assert statuses["group_ok"] == "active"
