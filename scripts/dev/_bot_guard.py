"""Shared bot-running guard for host-side dev scripts.

Several scripts under scripts/dev/ open SQLite files in storage/ directly
from the host (sqlite3.connect / SlangStore.init from outside the bot
container). Doing that while the bot container is running creates a
cross-process locking domain split — host sqlite3 holds POSIX locks on the
host inode while the container holds WAL/shm locks via the Docker bind
mount, and the two do not see each other's lock state. This is one of the
contributing factors to slang.db corruption.

After Phase 3 (TASK-20260521-01) the storage volume becomes a Docker
named volume (`omubot-storage`); the host has no direct path to the live
db files at all. Any host-side `sqlite3.connect("storage/...")` would
either touch a stale leftover file or fail outright. So the guard also
refuses to proceed when a named volume is detected, regardless of whether
the bot container is running.

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
NAMED_VOLUME_NAME = "omubot-storage"


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


def storage_is_named_volume() -> bool:
    """Detect whether the bot service mounts storage as the named volume.

    Two signals must agree, and both are best-effort:
      - `docker compose config --format json` lists service `bot` with a
        named-volume entry whose source/name is `omubot-storage`
      - `docker volume inspect omubot-storage` exits 0

    Returns False on any tooling failure — callers must treat unknown as
    "still bind mount" so we don't accidentally lock out the legacy path.
    """
    if shutil.which("docker") is None:
        return False
    config_signal = False
    try:
        proc = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout or "{}")
            services = data.get("services") if isinstance(data, dict) else None
            bot_svc = (services or {}).get(BOT_SERVICE_NAME) or {}
            for vol in bot_svc.get("volumes") or []:
                if not isinstance(vol, dict):
                    continue
                if vol.get("type") != "volume":
                    continue
                if vol.get("target") != "/app/storage":
                    continue
                source = str(vol.get("source") or "")
                if source == NAMED_VOLUME_NAME:
                    config_signal = True
                    break
    except Exception:
        return False
    if not config_signal:
        return False
    try:
        proc = subprocess.run(
            ["docker", "volume", "inspect", NAMED_VOLUME_NAME],
            capture_output=True,
            text=True,
        )
    except Exception:
        return False
    return proc.returncode == 0


def assert_bot_stopped(*, action: str, force: bool = False) -> None:
    """Refuse to proceed when the bot container is still running, or when
    storage has migrated to a named volume.

    Exits the process with status 2 unless `force=True`. Prints a loud
    warning when forced — running write paths against a live SQLite file
    is the recurring slang.db corruption pattern, so do not silence this.
    """
    compose_available = bool(shutil.which("docker") or shutil.which("docker-compose"))
    if not compose_available:
        # We cannot tell either way; let the script continue and rely on
        # the operator knowing what they're doing.
        return
    named_volume = storage_is_named_volume()
    if named_volume:
        if force:
            print(
                f"[guard] WARNING: storage is now a docker named volume "
                f"({NAMED_VOLUME_NAME}); host-side {action!r} cannot reach "
                f"the live db files. --force will proceed but writes will "
                f"land on a stale or empty path. Use "
                f"`docker exec qq-bot ...` instead.",
                file=sys.stderr,
            )
            return
        print(
            f"[guard] storage is a docker named volume ({NAMED_VOLUME_NAME}); "
            f"refusing to {action} from the host. The live db lives inside "
            f"the bot container. Use `docker exec qq-bot uv run python -m ...` "
            f"or `scripts/dev/storage_export.sh` to export a working copy.",
            file=sys.stderr,
        )
        raise SystemExit(2)
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
