"""Shared reader for storage/learning_settings.json.

Used by both admin API (read/write) and plugins (read-only on_tick gating).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FILENAME = "learning_settings.json"

_DEFAULTS: dict[str, Any] = {
    "autopilot": {"enabled": False, "aggressiveness": "standard"},
    "style": {"extract_enabled": True, "extract_interval_minutes": 120},
    "consolidator": {"auto_enabled": False, "interval_minutes": 360},
    "affection": {"scoring_enabled": True},
}


def load(storage_dir: str | Path = "storage") -> dict[str, Any]:
    path = Path(storage_dir) / _FILENAME
    if not path.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            merged: dict[str, Any] = {}
            for key, defaults in _DEFAULTS.items():
                if key in data and isinstance(data[key], dict):
                    merged[key] = {**defaults, **data[key]}
                else:
                    merged[key] = dict(defaults)
            return merged
    except (json.JSONDecodeError, OSError):
        pass
    return dict(_DEFAULTS)
