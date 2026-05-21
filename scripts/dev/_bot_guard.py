"""Shared bot-running guard for host-side dev scripts.

Several scripts under scripts/dev/ open SQLite files in storage/ directly
from the host (sqlite3.connect / SlangStore.init from outside the bot
container). Doing that while the bot container is running creates a
cross-process locking domain split — host sqlite3 holds POSIX locks on the
host inode while the container holds WAL/shm locks via the Docker bind
mount, and the two do not see each other's lock state. This is one of the
contributing factors to slang.db corruption.

Usage:
    from scripts.dev._bot_guard import assert_bot_stopped

    assert_bot_stopped(action="merge slang terms", force=args.force)

`force=True` (typically wired to a `--force` flag) prints a loud warning
but proceeds. Read-only scripts should not call this.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
from typing import Any

BOT_SERVICE_NAME = "bot"


def _docker_compose_json() -> list[dict[str, Any]]:
    commands = [
        ["docker", "compose", "ps", "--format", "json"],
        ["docker-compose", "ps", "--format", "json"],
    ]
    last_error = ""
    for cmd in commands:
        if shutil.which(cmd[0]) is None:
            continue
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            payload = proc.stdout.strip() or "[]"
            try:
                data = json.loads(payload)
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
                if isinstance(data, dict):
                    return [data]
            except Exception:
                rows: list[dict[str, Any]] = []
                for line in payload.splitlines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    with contextlib.suppress(Exception):
                        item = json.loads(stripped)
                        if isinstance(item, dict):
                            rows.append(item)
                return rows
        last_error = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
    if last_error:
        raise RuntimeError(last_error)
    return []


def is_bot_running() -> bool:
    """Best-effort detection: True iff `docker compose ps` reports the bot
    service in the `running` state. Returns False on any error (no docker,
    different compose dir, etc.) — callers should treat unknown as "safe"
    and rely on the explicit `--force` flag instead.
    """
    try:
        for item in _docker_compose_json():
            if not isinstance(item, dict):
                continue
            service = str(item.get("Service") or item.get("service") or "")
            state = str(item.get("State") or item.get("state") or "").lower()
            if service == BOT_SERVICE_NAME and state == "running":
                return True
    except Exception:
        return False
    return False


def assert_bot_stopped(*, action: str, force: bool = False) -> None:
    """Refuse to proceed when the bot container is still running.

    Exits the process with status 2 unless `force=True`. Prints a loud
    warning when forced — running write paths against a live SQLite file
    is the recurring slang.db corruption pattern, so do not silence this.
    """
    compose_available = bool(shutil.which("docker") or shutil.which("docker-compose"))
    if not compose_available:
        # We cannot tell either way; let the script continue and rely on
        # the operator knowing what they're doing.
        return
    if not is_bot_running():
        return
    if force:
        print(
            f"[guard] WARNING: bot container is running but --force was passed; "
            f"continuing with {action!r}. This can corrupt SQLite files. "
            f"Stop the bot first unless you know exactly what you're doing.",
            file=sys.stderr,
        )
        return
    print(
        f"[guard] bot container is still running; refusing to {action}. "
        f"Stop it first (`docker compose stop bot`) or rerun with --force.",
        file=sys.stderr,
    )
    raise SystemExit(2)
