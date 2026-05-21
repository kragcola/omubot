from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api.slang import create_slang_router
from kernel.types import PluginContext
from plugins.slang.plugin import SlangPlugin
from services.slang import SlangDatabaseCorruptError, SlangStore

ROOT = Path(__file__).resolve().parents[1]


def _write_corrupt_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"this is not a sqlite database")


def _create_minimal_slang_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE slang_pending_candidates (pending_id TEXT, group_id TEXT)")
        conn.execute("CREATE TABLE slang_terms (term_id TEXT)")
        conn.execute("CREATE TABLE slang_extraction_runs (run_id TEXT)")
        conn.execute(
            "INSERT INTO slang_pending_candidates (pending_id, group_id) VALUES (?, ?)",
            ("pending_1", "993065015"),
        )
        conn.commit()


@pytest.mark.asyncio
async def test_slang_store_init_raises_corrupt_error_on_bad_database(tmp_path: Path) -> None:
    db_path = tmp_path / "slang.db"
    _write_corrupt_db(db_path)

    store = SlangStore(db_path)

    with pytest.raises(SlangDatabaseCorruptError) as captured:
        await store.init()
    assert "slang database corrupt" in str(captured.value)
    assert str(db_path) in str(captured.value)
    assert store.initialized is False


@pytest.mark.asyncio
async def test_slang_plugin_startup_disables_slang_without_crashing_bot(tmp_path: Path) -> None:
    _write_corrupt_db(tmp_path / "slang.db")
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path)

    await plugin.on_startup(ctx)
    try:
        assert plugin.store is None
        assert plugin.register_tools() == []
        assert "database" in plugin._slang_disabled_reason.lower()
        assert ctx.slang_plugin is plugin
        assert getattr(ctx, "slang_store", None) is None
    finally:
        await plugin.on_shutdown(ctx)


def test_slang_api_returns_structured_error_for_corrupt_database(tmp_path: Path) -> None:
    _write_corrupt_db(tmp_path / "slang.db")
    app = FastAPI()
    app.include_router(create_slang_router(ctx=SimpleNamespace(storage_dir=tmp_path)), prefix="/api/admin")
    with TestClient(app) as client:
        response = client.get("/api/admin/slang/pending", params={"group_id": "993065015"})

    assert response.status_code == 503
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "slang_db_corrupt"
    assert payload["repair_script"] == "scripts/dev/slang_db_repair.py"
    assert str(tmp_path / "slang.db") in payload["db_path"]


def test_slang_repair_script_dry_run_and_apply(tmp_path: Path) -> None:
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is required for .recover")

    db_path = tmp_path / "slang.db"
    recovered_path = tmp_path / "slang.recovered.db"
    backup_root = tmp_path / "backups"
    _create_minimal_slang_db(db_path)

    dry_run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/dev/slang_db_repair.py"),
            "--db-path",
            str(db_path),
            "--backup-root",
            str(backup_root),
            "--recovered-path",
            str(recovered_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert dry_run.returncode == 0, dry_run.stdout + dry_run.stderr
    assert "dry-run only" in dry_run.stdout
    assert db_path.exists()
    assert recovered_path.exists()
    assert not list(tmp_path.glob("slang.corrupt-*.db"))

    apply = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/dev/slang_db_repair.py"),
            "--db-path",
            str(db_path),
            "--backup-root",
            str(backup_root),
            "--recovered-path",
            str(recovered_path),
            "--apply",
            "--force",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert apply.returncode == 0, apply.stdout + apply.stderr
    assert "live database replaced" in apply.stdout
    assert db_path.exists()
    assert not recovered_path.exists()
    assert list(tmp_path.glob("slang.corrupt-*.db"))
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM slang_pending_candidates WHERE group_id = ?",
            ("993065015",),
        ).fetchone()[0]
    assert pending_count == 1


def test_slang_repair_script_detects_compose_json_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.dev import _bot_guard

    def fake_which(command: str) -> str | None:
        return "/usr/bin/docker" if command == "docker" else None

    def fake_run(command, capture_output: bool, text: bool):
        assert capture_output is True
        assert text is True
        assert command[:3] == ["docker", "compose", "ps"]
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '{"Service":"napcat","State":"running"}\n'
                '{"Service":"bot","State":"running","Name":"qq-bot"}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(_bot_guard.shutil, "which", fake_which)
    monkeypatch.setattr(_bot_guard.subprocess, "run", fake_run)

    assert _bot_guard.is_bot_running() is True


def test_slang_semantic_smoke_does_not_write_live_sqlite_directly() -> None:
    smoke = (ROOT / "scripts/dev/slang_semantic_smoke.py").read_text(encoding="utf-8")

    assert "/app/storage/slang.db" not in smoke
    assert "sqlite3.connect" not in smoke
    assert "/api/admin/slang/debug/pending/seed" in smoke
