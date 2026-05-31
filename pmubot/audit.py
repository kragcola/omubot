from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Lock

# Structured audit trail for pmubot write operations. Persisted as JSON lines to
# a writable named volume (the repo mount is read-only), so the trail survives
# container restarts and stays machine-readable for the /api/audit UI panel.
# Every write endpoint also keeps emitting to stdout via LOGGER, so `docker logs
# pmubot` remains a second, independent record.

_LOCK = Lock()


def audit_path() -> Path:
    return Path(os.getenv("PMUBOT_AUDIT_FILE", "/var/lib/pmubot/audit.jsonl"))


def record(action: str, *, target: str, result: str, **extra: object) -> dict[str, object]:
    """Append one audit entry and return it. Best-effort: a write failure never
    breaks the originating operation (it is already done by the time we log)."""
    entry: dict[str, object] = {
        "ts": time.time(),
        "action": action,
        "target": target,
        "result": result,
    }
    for key, value in extra.items():
        entry[key] = value
    line = json.dumps(entry, ensure_ascii=False)
    path = audit_path()
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError:
        # Volume not mounted / not writable — stdout LOGGER still has the record.
        pass
    return entry


def tail(limit: int = 100) -> list[dict[str, object]]:
    """Return the most recent `limit` audit entries, newest first."""
    path = audit_path()
    try:
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    out: list[dict[str, object]] = []
    for raw in lines[-max(1, limit):]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    out.reverse()
    return out
