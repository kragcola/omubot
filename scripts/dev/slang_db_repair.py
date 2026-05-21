#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.dev._bot_guard import assert_bot_stopped, is_bot_running  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recover and validate a corrupt slang SQLite database.")
    sub = parser.add_subparsers(dest="command")

    # Default (legacy) mode — recover + validate + optional apply
    p_recover = sub.add_parser("recover", help="Recover a corrupt DB using sqlite3 .recover")
    p_recover.add_argument("--db-path", default=str(ROOT / "storage" / "slang.db"))
    p_recover.add_argument("--backup-root", default=str(ROOT / "storage" / "backups"))
    p_recover.add_argument("--recovered-path", default=str(ROOT / "storage" / "slang.recovered.db"))
    p_recover.add_argument("--verify-group-id", default="993065015")
    p_recover.add_argument("--apply", action="store_true", help="Replace the live database after successful recovery.")
    p_recover.add_argument("--force", action="store_true", help="Bypass the live bot process guard.")

    # Rebuild terms from revisions
    p_rebuild = sub.add_parser("rebuild-terms-from-revisions",
                               help="Rebuild slang_terms from slang_term_revisions.after_json")
    p_rebuild.add_argument("--src", required=True, help="Source DB (with revisions table)")
    p_rebuild.add_argument("--dst", required=True, help="Output DB path")
    p_rebuild.add_argument("--force", action="store_true", help="Bypass the live bot process guard.")

    # Validate
    p_validate = sub.add_parser("validate", help="Validate a slang DB and report counts")
    p_validate.add_argument("--db", required=True, help="DB to validate")

    # Legacy: no subcommand = recover mode (backwards compat)
    parser.add_argument("--db-path", default=str(ROOT / "storage" / "slang.db"))
    parser.add_argument("--backup-root", default=str(ROOT / "storage" / "backups"))
    parser.add_argument("--recovered-path", default=str(ROOT / "storage" / "slang.recovered.db"))
    parser.add_argument("--verify-group-id", default="993065015")
    parser.add_argument("--apply", action="store_true", help="Replace the live database after successful recovery.")
    parser.add_argument("--force", action="store_true", help="Bypass the live bot process guard.")

    return parser.parse_args()


def _which_or_fail(command: str) -> str:
    path = shutil.which(command)
    if not path:
        raise RuntimeError(f"{command} not found in PATH")
    return path


def _copy_if_exists(source: Path, target_dir: Path) -> list[Path]:
    copied: list[Path] = []
    if source.exists():
        target = target_dir / source.name
        shutil.copy2(source, target)
        copied.append(target)
    return copied


def _backup_live_files(db_path: Path, backup_root: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = backup_root / f"slang-corrupt-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    copied.extend(_copy_if_exists(db_path, backup_dir))
    copied.extend(_copy_if_exists(Path(f"{db_path}-wal"), backup_dir))
    copied.extend(_copy_if_exists(Path(f"{db_path}-shm"), backup_dir))
    manifest = {
        "db_path": str(db_path),
        "created_at": timestamp,
        "files": [
            {
                "name": item.name,
                "size": item.stat().st_size,
                "mtime": item.stat().st_mtime,
            }
            for item in copied
        ],
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return backup_dir


def _sqlite_recover(source_db: Path, recovered_db: Path) -> None:
    sqlite3_bin = _which_or_fail("sqlite3")
    if recovered_db.exists():
        recovered_db.unlink()
    # Keep `.recover` output as raw bytes — corrupt b-tree pages can contain
    # non-UTF-8 fragments (BLOBs, partial unicode codepoints) that would crash
    # `text=True` decoding before we ever reach the second sqlite3 process.
    recover = subprocess.run(
        [sqlite3_bin, str(source_db), ".recover"],
        capture_output=True,
        text=False,
    )
    stdout_bytes: bytes = recover.stdout or b""
    stderr_bytes: bytes = recover.stderr or b""
    if recover.returncode != 0 and not stdout_bytes.strip():
        raise RuntimeError(
            stderr_bytes.decode("utf-8", errors="replace").strip()
            or stdout_bytes.decode("utf-8", errors="replace").strip()
            or f"sqlite3 .recover failed with exit {recover.returncode}"
        )
    if not stdout_bytes.strip():
        raise RuntimeError("sqlite3 .recover returned empty SQL output")
    restore = subprocess.run(
        [sqlite3_bin, str(recovered_db)],
        input=stdout_bytes,
        capture_output=True,
        text=False,
    )
    if restore.returncode != 0:
        restore_err = (restore.stderr or b"").decode("utf-8", errors="replace").strip()
        restore_out = (restore.stdout or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(restore_err or restore_out or "sqlite3 restore failed")


def _validate_recovered_db(db_path: Path, verify_group_id: str) -> dict[str, Any]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        integrity_rows = conn.execute("PRAGMA integrity_check;").fetchall()
        quick_rows = conn.execute("PRAGMA quick_check;").fetchall()
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM slang_pending_candidates WHERE group_id = ?",
            (verify_group_id,),
        ).fetchone()[0]
        term_count = conn.execute("SELECT COUNT(*) FROM slang_terms").fetchone()[0]
        run_count = conn.execute("SELECT COUNT(*) FROM slang_extraction_runs").fetchone()[0]
    integrity_values = [str(row[0]) for row in integrity_rows if row and row[0] is not None]
    quick_values = [str(row[0]) for row in quick_rows if row and row[0] is not None]
    if not integrity_values or any(value.lower() != "ok" for value in integrity_values):
        raise RuntimeError(f"integrity_check failed: {integrity_values or ['<empty>']}")
    if not quick_values or any(value.lower() != "ok" for value in quick_values):
        raise RuntimeError(f"quick_check failed: {quick_values or ['<empty>']}")
    return {
        "integrity_check": integrity_values,
        "quick_check": quick_values,
        "pending_count": int(pending_count or 0),
        "term_count": int(term_count or 0),
        "run_count": int(run_count or 0),
    }


def _replace_live_db(source_db: Path, recovered_db: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    corrupt_path = source_db.with_name(f"{source_db.stem}.corrupt-{timestamp}{source_db.suffix}")
    source_db.replace(corrupt_path)
    recovered_db.replace(source_db)
    wal_path = Path(f"{source_db}-wal")
    shm_path = Path(f"{source_db}-shm")
    if wal_path.exists():
        wal_path.unlink()
    if shm_path.exists():
        shm_path.unlink()
    return corrupt_path


def _rebuild_terms_from_revisions(src_path: Path, dst_path: Path) -> dict[str, Any]:
    """Rebuild slang_terms from slang_term_revisions.after_json snapshots."""
    if dst_path.exists():
        dst_path.unlink()

    src_conn = sqlite3.connect(str(src_path))
    src_conn.row_factory = sqlite3.Row

    rows = src_conn.execute("""
        SELECT term_id, after_json, created_at
        FROM slang_term_revisions
        WHERE after_json IS NOT NULL AND after_json != ''
        ORDER BY created_at ASC
    """).fetchall()
    src_conn.close()

    latest: dict[str, tuple[str, str]] = {}
    for row in rows:
        tid = row["term_id"]
        latest[tid] = (row["after_json"], row["created_at"])

    dst_conn = sqlite3.connect(str(dst_path))
    dst_conn.execute("PRAGMA journal_mode=WAL")

    dst_conn.execute("""
        CREATE TABLE IF NOT EXISTS slang_terms (
            term_id TEXT PRIMARY KEY,
            term_key TEXT NOT NULL,
            term TEXT NOT NULL,
            meaning TEXT DEFAULT '',
            aliases TEXT DEFAULT '[]',
            scope TEXT DEFAULT 'general',
            group_id TEXT,
            confidence REAL DEFAULT 0.5,
            status TEXT DEFAULT 'candidate',
            usage_count INTEGER DEFAULT 0,
            unique_users INTEGER DEFAULT 0,
            first_seen TEXT,
            last_seen TEXT,
            created_at TEXT,
            updated_at TEXT,
            source TEXT DEFAULT 'unknown',
            repeat_policy TEXT DEFAULT 'default',
            notes TEXT DEFAULT '',
            meta TEXT DEFAULT '{}'
        )
    """)

    rebuilt = 0
    skipped = 0
    errors: list[str] = []

    for term_id, (after_json, _ts) in latest.items():
        try:
            data = json.loads(after_json)
        except (json.JSONDecodeError, TypeError) as e:
            errors.append(f"{term_id}: {e}")
            skipped += 1
            continue

        dst_conn.execute("""
            INSERT OR REPLACE INTO slang_terms
            (term_id, term_key, term, meaning, aliases, scope, group_id,
             confidence, status, usage_count, unique_users, first_seen, last_seen,
             created_at, updated_at, source, repeat_policy, notes, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("term_id", term_id),
            data.get("term_key", ""),
            data.get("term", ""),
            data.get("meaning", ""),
            json.dumps(data.get("aliases", []), ensure_ascii=False),
            data.get("scope", "general"),
            data.get("group_id"),
            data.get("confidence", 0.5),
            data.get("status", "candidate"),
            data.get("usage_count", 0),
            data.get("unique_users", 0),
            data.get("first_seen"),
            data.get("last_seen"),
            data.get("created_at"),
            data.get("updated_at"),
            data.get("source", "unknown"),
            data.get("repeat_policy", "default"),
            data.get("notes", ""),
            json.dumps(data.get("meta", {}), ensure_ascii=False),
        ))
        rebuilt += 1

    dst_conn.commit()
    dst_conn.close()

    return {
        "total_revisions": len(rows),
        "unique_terms": len(latest),
        "rebuilt": rebuilt,
        "skipped": skipped,
        "errors": errors[:10],
    }


def _validate_db_counts(db_path: Path) -> dict[str, Any]:
    """Report counts for all slang tables."""
    conn = sqlite3.connect(str(db_path))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]

    counts: dict[str, int] = {}
    for table in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
        counts[table] = count

    status_dist: dict[str, int] = {}
    if "slang_terms" in tables:
        for row in conn.execute("SELECT status, COUNT(*) FROM slang_terms GROUP BY status"):
            status_dist[row[0]] = row[1]

    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()

    return {
        "integrity_check": integrity,
        "tables": counts,
        "status_distribution": status_dist,
    }


def main() -> int:
    args = _parse_args()

    if args.command == "rebuild-terms-from-revisions":
        src = Path(args.src)
        dst = Path(args.dst)
        if not src.exists():
            print(f"[fail] source DB not found: {src}")
            return 1
        # Rebuild always writes to `dst`. If the operator points dst at a
        # live db (e.g. --dst storage/slang.db) while the bot is running,
        # we'd reproduce the cross-process locking hazard. Guard the write.
        assert_bot_stopped(action="rebuild slang terms from revisions",
                           force=getattr(args, "force", False))
        report = _rebuild_terms_from_revisions(src, dst)
        print(f"[ok]   rebuilt {report['rebuilt']} terms from {report['total_revisions']} revisions")
        print(f"       skipped: {report['skipped']}")
        if report["errors"]:
            print(f"       errors: {report['errors'][:3]}")
        return 0

    if args.command == "validate":
        db = Path(args.db)
        if not db.exists():
            print(f"[fail] DB not found: {db}")
            return 1
        report = _validate_db_counts(db)
        print(f"[ok]   integrity: {report['integrity_check']}")
        print("       tables:")
        for table, count in report["tables"].items():
            print(f"         {table}: {count}")
        if report["status_distribution"]:
            print("       status distribution:")
            for status, count in report["status_distribution"].items():
                print(f"         {status}: {count}")
        return 0

    # Default: recover mode (legacy or explicit "recover" subcommand)
    db_path = Path(args.db_path)
    backup_root = Path(args.backup_root)
    recovered_path = Path(args.recovered_path)

    if not db_path.exists():
        print(f"[fail] database not found: {db_path}")
        return 1

    compose_available = bool(shutil.which("docker") or shutil.which("docker-compose"))
    bot_running = is_bot_running() if compose_available else False
    print(f"[info] bot running: {bot_running}")
    if args.apply and not compose_available and not args.force:
        print("[fail] docker compose not available; cannot verify bot state for --apply")
        return 2
    if args.apply:
        # Apply replaces the live db file; refuse unless the bot is stopped.
        assert_bot_stopped(action="apply recovered slang.db", force=args.force)

    backup_dir = _backup_live_files(db_path, backup_root)
    print(f"[ok]   backed up live files to {backup_dir}")

    try:
        _sqlite_recover(db_path, recovered_path)
        print(f"[ok]   recovered database written to {recovered_path}")
        summary = _validate_recovered_db(recovered_path, str(args.verify_group_id))
        print(
            "[ok]   recovery validated | "
            f"integrity={summary['integrity_check'][0]} quick={summary['quick_check'][0]} "
            f"terms={summary['term_count']} runs={summary['run_count']} "
            f"pending[{args.verify_group_id}]={summary['pending_count']}"
        )
    except Exception as exc:
        print(f"[fail] {exc}")
        return 1

    if not args.apply:
        print("[info] dry-run only; original database left untouched")
        return 0

    try:
        corrupt_path = _replace_live_db(db_path, recovered_path)
        summary = _validate_recovered_db(db_path, str(args.verify_group_id))
        print(
            "[ok]   live database replaced | "
            f"corrupt_backup={corrupt_path} integrity={summary['integrity_check'][0]} "
            f"pending[{args.verify_group_id}]={summary['pending_count']}"
        )
    except Exception as exc:
        print(f"[fail] apply failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
