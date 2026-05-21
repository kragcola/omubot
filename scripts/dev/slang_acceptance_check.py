#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PYTEST_TARGETS = [
    "tests/test_slang_store.py",
    "tests/test_slang_plugin.py",
    "tests/test_admin_api.py",
    "tests/test_slang_semantic_reviewer.py",
    "tests/test_slang_db_integrity.py",
]

RUFF_TARGETS = [
    "services/storage/sqlite.py",
    "services/slang/store.py",
    "services/slang/semantic_reviewer.py",
    "services/slang/review_utils.py",
    "plugins/slang/plugin.py",
    "services/health.py",
    "admin/routes/api/slang.py",
    "scripts/dev/slang_db_repair.py",
    "scripts/dev/slang_semantic_smoke.py",
    "scripts/dev/slang_acceptance_check.py",
    "tests/test_slang_store.py",
    "tests/test_slang_plugin.py",
    "tests/test_admin_api.py",
    "tests/test_slang_semantic_reviewer.py",
    "tests/test_slang_db_integrity.py",
]


@dataclass(slots=True)
class Check:
    name: str
    command: list[str] | None
    required: bool = True


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Omubot slang module acceptance checks.")
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Skip Admin API / docker-log smoke checks that require the bot container to be running.",
    )
    parser.add_argument(
        "--strict-logs",
        action="store_true",
        help="Make the semantic smoke check fail if expected docker log lines are missing.",
    )
    parser.add_argument(
        "--group-id",
        default="",
        help="Optional group id passed to the semantic smoke check.",
    )
    parser.add_argument(
        "--db-path",
        default=str(ROOT / "storage" / "slang.db"),
        help="Path to the live slang SQLite database.",
    )
    return parser.parse_args()


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("UV_CACHE_DIR", str(ROOT / ".cache" / "uv"))
    env.setdefault("PIP_CACHE_DIR", str(ROOT / ".cache" / "pip"))
    return env


def _sqlite_check(db_path: str, pragma: str) -> Check | None:
    if shutil.which("sqlite3") is None:
        return None
    return Check(
        name=f"sqlite {pragma}",
        command=["sqlite3", db_path, f"PRAGMA {pragma};"],
    )


def _run_check(check: Check) -> bool:
    if check.command is None:
        print(f"[skip] {check.name} (unavailable)")
        return True
    print(f"\n[run] {check.name}")
    print("$ " + " ".join(check.command))
    proc = subprocess.run(
        check.command,
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.stderr.strip():
        print(proc.stderr.rstrip(), file=sys.stderr)
    if proc.returncode == 0:
        print(f"[ok]  {check.name}")
        return True
    level = "fail" if check.required else "warn"
    print(f"[{level}] {check.name} exited with {proc.returncode}")
    return not check.required


def main() -> int:
    args = _parse_args()
    checks: list[Check] = []
    for pragma in ("integrity_check", "quick_check"):
        check = _sqlite_check(args.db_path, pragma)
        if check is not None:
            checks.append(check)
        else:
            checks.append(Check(name=f"sqlite {pragma}", command=None, required=False))

    checks.extend([
        Check(
            name="slang pytest suite",
            command=["uv", "run", "pytest", *PYTEST_TARGETS, "-q"],
        ),
        Check(
            name="slang ruff suite",
            command=["uv", "run", "ruff", "check", *RUFF_TARGETS],
        ),
    ])

    if not args.skip_live:
        smoke_command = ["uv", "run", "python", "scripts/dev/slang_semantic_smoke.py"]
        if args.strict_logs:
            smoke_command.append("--strict-logs")
        if args.group_id:
            smoke_command.extend(["--group-id", args.group_id])
        checks.append(Check(name="live semantic smoke", command=smoke_command))

    passed = 0
    failed = 0
    for check in checks:
        ok = _run_check(check)
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\n[acceptance] Summary: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
