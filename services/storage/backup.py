"""Backup service — registry, creation, verification, manifest."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

_L = logger.bind(channel="backup")


@dataclass
class BackupItem:
    id: str
    path: str
    item_type: str  # "sqlite" | "file" | "directory"
    required: bool = True
    critical: bool = False
    migratable: bool = True
    profiles: list[str] = field(default_factory=lambda: ["daily"])
    host_only: bool = False
    sensitive: bool = False
    restore_note: str = ""


BACKUP_REGISTRY: list[BackupItem] = [
    # --- SQLite databases ---
    BackupItem("slang", "storage/slang.db", "sqlite", critical=True,
               profiles=["daily", "migration", "pre-change"],
               restore_note="停止 bot → 替换 → 删除 .db-wal/.db-shm → 启动"),
    BackupItem("messages", "storage/messages.db", "sqlite", critical=True,
               profiles=["daily", "migration", "pre-change"]),
    BackupItem("usage", "storage/usage.db", "sqlite",
               profiles=["daily", "migration", "pre-change"]),
    BackupItem("style", "storage/style.db", "sqlite",
               profiles=["daily", "migration", "pre-change"]),
    BackupItem("memory_cards", "storage/memory_cards.db", "sqlite", critical=True,
               profiles=["daily", "migration", "pre-change"]),
    BackupItem("knowledge_graph", "storage/knowledge_graph.db", "sqlite",
               profiles=["daily", "migration", "pre-change"]),
    BackupItem("knowledge_index", "storage/knowledge_index.db", "sqlite",
               profiles=["daily", "migration", "pre-change"]),
    BackupItem("learning_normalizer", "storage/learning_normalizer.db", "sqlite",
               profiles=["daily", "migration", "pre-change"]),
    # --- Config ---
    BackupItem("config_json", "config/config.json", "file",
               required=False, profiles=["daily", "migration", "pre-change"],
               restore_note="Bot 主业务配置，优先级高于 config.toml"),
    BackupItem("config_toml", "config/config.toml", "file",
               required=False, profiles=["migration"]),
    BackupItem("config_env", "config/.env", "file",
               required=False, sensitive=True, profiles=["migration"],
               restore_note="含 secret，迁移时需人工确认"),
    BackupItem("group_policy", "config/group-policy.json", "file",
               required=False, profiles=["daily", "migration", "pre-change"]),
    BackupItem("group_memory", "config/group-memory.json", "file",
               required=False, profiles=["migration"]),
    BackupItem("talk_schedule", "config/talk_schedule.json", "file",
               required=False, profiles=["migration"]),
    BackupItem("soul", "config/soul", "directory",
               profiles=["daily", "migration"],
               restore_note="人格与指令文件，丢失会导致 bot 行为异常"),
    # --- Plugin state ---
    BackupItem("plugin_state", "storage/plugins/plugin-state.json", "file",
               profiles=["daily", "migration", "pre-change"]),
    BackupItem("plugin_config", "storage/plugins/config", "directory",
               required=False, profiles=["daily", "migration", "pre-change"]),
    BackupItem("plugin_calendar", "storage/plugins/calendar_context", "directory",
               required=False, profiles=["migration"]),
    # --- Storage state ---
    BackupItem("groups", "storage/groups", "directory",
               required=False, profiles=["daily", "migration"]),
    # --- File assets ---
    BackupItem("stickers", "storage/stickers", "directory",
               required=False, profiles=["migration"]),
    BackupItem("schedule", "storage/schedule", "directory",
               required=False, profiles=["migration"]),
    BackupItem("affection", "storage/affection", "directory",
               required=False, profiles=["migration"]),
    # --- Diagnostic ---
    BackupItem("storage_logs", "storage/logs", "directory",
               required=False, profiles=["diagnostic"]),
    # --- Host-only ---
    BackupItem("napcat_data", "napcat/data", "directory",
               required=False, migratable=False, host_only=True, sensitive=True,
               profiles=["migration"],
               restore_note="含 QQ 登录凭证；迁移后仍可能触发重新扫码"),
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _backup_sqlite(src: Path, dst: Path, *, critical: bool = False) -> dict:
    """Hot-backup a SQLite DB using the .backup() API. Never degrades to cp."""
    if not src.exists():
        return {"status": "failed", "error": f"source not found: {src}"}

    dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        conn_src = sqlite3.connect(str(src))
        conn_dst = sqlite3.connect(str(dst))
        try:
            conn_src.backup(conn_dst)
        finally:
            conn_dst.close()
            conn_src.close()
    except (sqlite3.DatabaseError, sqlite3.OperationalError) as e:
        return {"status": "failed", "error": f"sqlite backup failed: {e}"}

    # Verify the backup
    try:
        conn_check = sqlite3.connect(str(dst))
        try:
            qc = conn_check.execute("PRAGMA quick_check").fetchone()[0]
            ic = None
            if critical:
                ic = conn_check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn_check.close()
    except (sqlite3.DatabaseError, sqlite3.OperationalError) as e:
        return {"status": "failed", "error": f"backup verification failed: {e}"}

    sha = _sha256_file(dst)
    size = dst.stat().st_size

    return {
        "status": "ok",
        "size_bytes": size,
        "sha256": sha,
        "quick_check": qc,
        "integrity_check": ic,
    }


def _backup_file(src: Path, dst: Path, *, required: bool = True) -> dict:
    """Copy a single file with sha256 verification."""
    if not src.exists():
        if not required:
            return {"status": "skipped", "reason": "source not found"}
        return {"status": "failed", "error": f"required source not found: {src}"}

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)

    sha_src = _sha256_file(src)
    sha_dst = _sha256_file(dst)

    if sha_src != sha_dst:
        return {"status": "best_effort", "error": "sha256 mismatch after copy",
                "sha256": sha_dst, "size_bytes": dst.stat().st_size}

    result: dict = {"status": "ok", "sha256": sha_dst,
                    "size_bytes": dst.stat().st_size}

    if dst.suffix == ".json":
        try:
            json.loads(dst.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            result["status"] = "best_effort"
            result["json_parse_error"] = str(e)

    return result


def _backup_directory(src: Path, dst: Path, *, required: bool = True) -> dict:
    """Copy a directory tree with basic verification."""
    if not src.exists():
        if not required:
            return {"status": "skipped", "reason": "source not found"}
        return {"status": "failed", "error": f"required source not found: {src}"}

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)

    file_count = sum(1 for f in dst.rglob("*") if f.is_file())
    size_bytes = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())

    json_errors = []
    for jf in dst.rglob("*.json"):
        try:
            json.loads(jf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            json_errors.append(f"{jf.name}: {e}")

    status = "best_effort" if json_errors else "ok"
    result: dict = {"status": status, "file_count": file_count,
                    "size_bytes": size_bytes}
    if json_errors:
        result["json_errors"] = json_errors[:5]
    return result


def _get_git_commit_or_unknown(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True, text=True, timeout=2,
        )
        return result.stdout.strip()[:12] if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


class BackupLockedError(Exception):
    pass


class BackupService:
    def __init__(self, storage_dir: Path, repo_root: Path | None = None):
        self._storage_dir = Path(storage_dir).resolve()
        self._repo_root = (repo_root or Path.cwd()).resolve()
        self._backup_root = self._storage_dir / "backups"
        self._backup_root.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._backup_root / ".lock"

    @contextmanager
    def _acquire_lock(self):
        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise BackupLockedError("another backup is in progress") from e
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _check_same_filesystem(p1: Path, p2: Path) -> bool:
        return os.statvfs(p1).f_fsid == os.statvfs(p2).f_fsid

    @staticmethod
    def _free_disk_bytes(path: Path) -> int:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize

    @staticmethod
    def _estimate_size_bytes(items: list[BackupItem], repo_root: Path) -> int:
        total = 0
        for item in items:
            src = repo_root / item.path
            if not src.exists():
                continue
            if src.is_file():
                total += src.stat().st_size
            else:
                total += sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
        return total

    def create(self, profile: str = "daily", host_mode: bool = False) -> dict:
        """Create a backup. Returns manifest dict."""
        with self._acquire_lock():
            items = [
                item for item in BACKUP_REGISTRY
                if profile in item.profiles
                and (host_mode or not item.host_only)
            ]
            skipped_host_only = [
                item.id for item in BACKUP_REGISTRY
                if profile in item.profiles and item.host_only and not host_mode
            ]

            estimated = self._estimate_size_bytes(items, self._repo_root)
            free = self._free_disk_bytes(self._backup_root)
            if free < estimated * 1.5:
                return {
                    "status": "no_space",
                    "estimated_bytes": estimated,
                    "free_bytes": free,
                }

            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            tmp_dir = self._backup_root / f".tmp-{profile}-{ts}"
            tmp_dir.mkdir(parents=True)

            profile_dir = self._backup_root / profile
            profile_dir.mkdir(parents=True, exist_ok=True)
            if not self._check_same_filesystem(tmp_dir, profile_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)
                raise RuntimeError(
                    "tmp_dir and target dir are on different filesystems; "
                    "rename will not be atomic"
                )

            manifest_items = []
            for item in items:
                if item.item_type == "sqlite":
                    src = self._repo_root / item.path
                    if src.exists():
                        try:
                            conn = sqlite3.connect(str(src))
                            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                            conn.close()
                        except Exception:
                            pass
                    result = _backup_sqlite(
                        src, tmp_dir / "sqlite" / src.name, critical=item.critical,
                    )
                elif item.item_type == "file":
                    src = self._repo_root / item.path
                    result = _backup_file(src, tmp_dir / "files" / item.path,
                                          required=item.required)
                else:
                    src = self._repo_root / item.path
                    result = _backup_directory(src, tmp_dir / "files" / item.path,
                                              required=item.required)

                result["id"] = item.id
                result["type"] = item.item_type
                result["source_path"] = item.path
                result["required"] = item.required
                result["critical"] = item.critical
                result["sensitive"] = item.sensitive
                manifest_items.append(result)

            any_required_failed = any(
                i["required"] and i["status"] == "failed"
                for i in manifest_items
            )
            trusted = not any_required_failed

            manifest = {
                "schema_version": 1,
                "backup_id": f"{profile}-{ts}",
                "created_at": datetime.now(UTC).astimezone().isoformat(),
                "profile": profile,
                "host_mode": host_mode,
                "complete": len(skipped_host_only) == 0,
                "skipped_host_only": skipped_host_only,
                "contains_sensitive": any(
                    i["sensitive"] for i in manifest_items
                ),
                "source": {
                    "hostname": socket.gethostname(),
                    "repo_root_basename": self._repo_root.name,
                    "git_commit": _get_git_commit_or_unknown(self._repo_root),
                    "storage_dir": str(
                        self._storage_dir.relative_to(self._repo_root)
                    ),
                },
                "items": manifest_items,
                "summary": {
                    "ok": sum(1 for i in manifest_items if i["status"] == "ok"),
                    "best_effort": sum(
                        1 for i in manifest_items if i["status"] == "best_effort"
                    ),
                    "skipped": sum(
                        1 for i in manifest_items if i["status"] == "skipped"
                    ),
                    "failed": sum(
                        1 for i in manifest_items if i["status"] == "failed"
                    ),
                    "trusted": trusted,
                },
            }

            (tmp_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2)
            )

            if trusted:
                date_str = datetime.now().strftime("%Y-%m-%d")
                target = profile_dir / date_str
                if target.exists():
                    target = profile_dir / f"{date_str}-{ts.split('-')[1]}"
                os.rename(tmp_dir, target)
                try:
                    manifest["backup_path"] = str(
                        target.relative_to(self._repo_root)
                    )
                except ValueError:
                    manifest["backup_path"] = str(target)
            else:
                failed_dir = self._backup_root / "failed" / f"{profile}-{ts}"
                failed_dir.parent.mkdir(parents=True, exist_ok=True)
                os.rename(tmp_dir, failed_dir)
                try:
                    manifest["backup_path"] = str(
                        failed_dir.relative_to(self._repo_root)
                    )
                except ValueError:
                    manifest["backup_path"] = str(failed_dir)

            return manifest

    def latest_status(self) -> dict | None:
        """Return the most recent manifest, or None."""
        daily_dir = self._backup_root / "daily"
        if not daily_dir.exists():
            return None
        manifests = sorted(daily_dir.glob("*/manifest.json"), reverse=True)
        if not manifests:
            return None
        try:
            return json.loads(manifests[0].read_text(encoding="utf-8"))
        except Exception:
            return None

    def list_backups(self, profile: str = "daily") -> list[dict]:
        """List all backups for a profile, newest first."""
        profile_dir = self._backup_root / profile
        if not profile_dir.exists():
            return []
        results = []
        for mf in sorted(profile_dir.glob("*/manifest.json"), reverse=True):
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:
                continue
            try:
                path_str = str(mf.parent.relative_to(self._repo_root))
            except ValueError:
                path_str = str(mf.parent.relative_to(self._backup_root.parent))
            results.append({
                "backup_id": data.get("backup_id"),
                "created_at": data.get("created_at"),
                "trusted": data.get("summary", {}).get("trusted"),
                "complete": data.get("complete", True),
                "skipped_host_only": data.get("skipped_host_only", []),
                "profile": data.get("profile"),
                "path": path_str,
            })
        return results

    def prune(self, keep_days: int = 7) -> list[str]:
        """Remove backups older than keep_days. Returns deleted dir names."""
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=keep_days)
        deleted = []
        for profile_dir in self._backup_root.iterdir():
            if not profile_dir.is_dir() or profile_dir.name.startswith("."):
                continue
            if profile_dir.name == "failed":
                continue
            for backup_dir in profile_dir.iterdir():
                if not backup_dir.is_dir():
                    continue
                mf = backup_dir / "manifest.json"
                if not mf.exists():
                    continue
                try:
                    data = json.loads(mf.read_text(encoding="utf-8"))
                    created = datetime.fromisoformat(data["created_at"])
                    if created.replace(tzinfo=None) < cutoff:
                        shutil.rmtree(backup_dir)
                        deleted.append(backup_dir.name)
                except Exception:
                    continue
        return deleted


# ---------------------------------------------------------------------------
# Restore helpers
# ---------------------------------------------------------------------------


def _cli_inspect(backup_dir: Path) -> None:
    import sys
    mf = backup_dir / "manifest.json"
    if not mf.exists():
        print(f"ERROR: no manifest.json in {backup_dir}")
        sys.exit(1)
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    print(f"Backup ID:  {manifest.get('backup_id')}")
    print(f"Created:    {manifest.get('created_at')}")
    print(f"Profile:    {manifest.get('profile')}")
    print(f"Host mode:  {manifest.get('host_mode')}")
    print(f"Complete:   {manifest.get('complete')}")
    print(f"Trusted:    {manifest.get('summary', {}).get('trusted')}")
    print(f"Git commit: {manifest.get('source', {}).get('git_commit')}")
    print()
    summary = manifest.get("summary", {})
    print(f"Items: ok={summary.get('ok')} best_effort={summary.get('best_effort')} "
          f"skipped={summary.get('skipped')} failed={summary.get('failed')}")
    print()
    for item in manifest.get("items", []):
        status_mark = "✓" if item["status"] == "ok" else "!" if item["status"] == "best_effort" else "✗"
        size = item.get("size_bytes", 0)
        size_str = f"{size // 1024}KB" if size else ""
        print(f"  {status_mark} {item['id']:25s} {item['type']:10s} {item['status']:12s} {size_str}")


def _cli_restore_plan(backup_dir: Path, *, item_id: str | None = None) -> None:
    import sys
    mf = backup_dir / "manifest.json"
    if not mf.exists():
        print(f"ERROR: no manifest.json in {backup_dir}")
        sys.exit(1)
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    items = manifest.get("items", [])
    if item_id:
        items = [i for i in items if i["id"] == item_id]
        if not items:
            print(f"ERROR: item '{item_id}' not found in manifest")
            sys.exit(1)

    print("=== RESTORE PLAN (read-only, no changes will be made) ===")
    print()
    print("Prerequisites:")
    print("  1. Stop the bot: docker stop qq-bot")
    print()
    print("Actions that will be performed:")
    for item in items:
        if item["status"] in ("failed", "skipped"):
            continue
        src_in_backup = item.get("source_path", "")
        if item["type"] == "sqlite":
            db_name = Path(src_in_backup).name
            print(f"  - Replace {src_in_backup}")
            print(f"    from: {backup_dir}/sqlite/{db_name}")
            print("    pre-restore backup of live file")
            print(f"    delete: {src_in_backup}-wal, {src_in_backup}-shm")
            print("    verify: PRAGMA quick_check on restored DB")
        else:
            print(f"  - Replace {src_in_backup}")
            print(f"    from: {backup_dir}/files/{src_in_backup}")
    print()
    print("Post-restore:")
    print("  - Start the bot: docker start qq-bot")
    print()
    print("To execute: add --apply flag")


def _cli_restore(backup_dir: Path, *, item_id: str | None = None, force: bool = False) -> None:
    import sys
    mf = backup_dir / "manifest.json"
    if not mf.exists():
        print(f"ERROR: no manifest.json in {backup_dir}")
        sys.exit(1)

    if not force:
        lock_path = Path("storage/backups/.bot-running")
        pid_file = Path("bot.pid")
        if pid_file.exists() or lock_path.exists():
            print("ERROR: bot appears to be running. Stop it first or use --force")
            sys.exit(1)

    manifest = json.loads(mf.read_text(encoding="utf-8"))
    items = manifest.get("items", [])
    if item_id:
        items = [i for i in items if i["id"] == item_id]
        if not items:
            print(f"ERROR: item '{item_id}' not found in manifest")
            sys.exit(1)

    repo_root = Path.cwd()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    pre_restore_dir = Path("storage/backups/pre-restore") / ts
    pre_restore_dir.mkdir(parents=True, exist_ok=True)

    restored = 0
    for item in items:
        if item["status"] in ("failed", "skipped"):
            continue
        src_path = item.get("source_path", "")
        live_path = repo_root / src_path

        if item["type"] == "sqlite":
            db_name = Path(src_path).name
            backup_file = backup_dir / "sqlite" / db_name

            if not backup_file.exists():
                print(f"  SKIP {item['id']}: backup file not found")
                continue

            # WAL checkpoint on live DB
            if live_path.exists():
                try:
                    conn = sqlite3.connect(str(live_path))
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    conn.close()
                except Exception:
                    pass

                # Pre-restore backup
                shutil.copy2(live_path, pre_restore_dir / db_name)

            # Replace
            shutil.copy2(backup_file, live_path)

            # Clean WAL/SHM
            for ext in ("-wal", "-shm"):
                wal = Path(str(live_path) + ext)
                if wal.exists():
                    wal.unlink()

            # Verify
            try:
                conn = sqlite3.connect(str(live_path))
                check = conn.execute("PRAGMA quick_check").fetchone()[0]
                conn.close()
                status = "ok" if check == "ok" else "WARN"
            except Exception as e:
                status = f"ERROR: {e}"

            print(f"  ✓ {item['id']}: restored ({status})")
            restored += 1

        else:
            backup_file = backup_dir / "files" / src_path
            if not backup_file.exists():
                print(f"  SKIP {item['id']}: backup not found")
                continue

            # Pre-restore backup
            if live_path.exists():
                pre_dst = pre_restore_dir / src_path
                pre_dst.parent.mkdir(parents=True, exist_ok=True)
                if live_path.is_dir():
                    shutil.copytree(live_path, pre_dst)
                else:
                    shutil.copy2(live_path, pre_dst)

            # Replace
            if backup_file.is_dir():
                if live_path.exists():
                    shutil.rmtree(live_path)
                shutil.copytree(backup_file, live_path)
            else:
                live_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_file, live_path)

            print(f"  ✓ {item['id']}: restored")
            restored += 1

    print(f"\nRestored {restored} item(s). Pre-restore backup at: {pre_restore_dir}")
    print("Start the bot: docker start qq-bot")


# ---------------------------------------------------------------------------
# CLI entry point: python -m services.storage.backup
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Omubot backup service CLI"
    )
    sub = parser.add_subparsers(dest="command")

    p_create = sub.add_parser("create", help="Create a backup")
    p_create.add_argument("--profile", default="daily")
    p_create.add_argument("--host-mode", action="store_true")

    sub.add_parser("status", help="Show latest backup status")

    p_list = sub.add_parser("list", help="List backups")
    p_list.add_argument("--profile", default="daily")

    p_prune = sub.add_parser("prune", help="Prune old backups")
    p_prune.add_argument("--keep-days", type=int, default=7)

    p_inspect = sub.add_parser("inspect", help="Inspect a backup directory")
    p_inspect.add_argument("backup_dir", help="Path to backup directory")

    p_plan = sub.add_parser("restore-plan", help="Show restore plan (read-only)")
    p_plan.add_argument("backup_dir", help="Path to backup directory")
    p_plan.add_argument("--item", help="Restore only this item ID")

    p_restore = sub.add_parser("restore", help="Restore from backup")
    p_restore.add_argument("backup_dir", help="Path to backup directory")
    p_restore.add_argument("--item", help="Restore only this item ID")
    p_restore.add_argument("--apply", action="store_true", required=True)
    p_restore.add_argument("--force", action="store_true",
                           help="Allow restore while bot may be running")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    svc = BackupService(storage_dir=Path("storage"), repo_root=Path.cwd())

    if args.command == "create":
        manifest = svc.create(profile=args.profile, host_mode=args.host_mode)
        status = manifest.get("status")
        if status == "no_space":
            print(f"ERROR: insufficient disk space "
                  f"(need {manifest['estimated_bytes']} bytes, "
                  f"have {manifest['free_bytes']})")
            sys.exit(1)
        summary = manifest.get("summary", {})
        print(f"Backup {manifest['backup_id']}: "
              f"trusted={summary.get('trusted')} "
              f"ok={summary.get('ok')} "
              f"failed={summary.get('failed')} "
              f"skipped={summary.get('skipped')}")
        if not summary.get("trusted"):
            sys.exit(2)
        pruned = svc.prune()
        if pruned:
            print(f"Pruned {len(pruned)} old backup(s)")

    elif args.command == "status":
        s = svc.latest_status()
        if s is None:
            print("No backups found")
            sys.exit(1)
        print(f"Latest: {s['backup_id']} "
              f"created={s['created_at']} "
              f"trusted={s['summary']['trusted']}")

    elif args.command == "list":
        backups = svc.list_backups(profile=args.profile)
        if not backups:
            print("No backups found")
            sys.exit(0)
        for b in backups:
            print(f"  {b['backup_id']}  {b['created_at']}  "
                  f"trusted={b['trusted']}")

    elif args.command == "prune":
        deleted = svc.prune(keep_days=args.keep_days)
        print(f"Pruned {len(deleted)} backup(s)")
        for d in deleted:
            print(f"  - {d}")

    elif args.command == "inspect":
        _cli_inspect(Path(args.backup_dir))

    elif args.command == "restore-plan":
        _cli_restore_plan(Path(args.backup_dir), item_id=args.item)

    elif args.command == "restore":
        _cli_restore(Path(args.backup_dir), item_id=args.item, force=args.force)


if __name__ == "__main__":
    _cli_main()
