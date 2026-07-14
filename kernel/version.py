"""Project version sourced from the repository package metadata."""

from __future__ import annotations

import tomllib
from pathlib import Path


def _read_version() -> str:
    try:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except Exception:
        return "0.0.0"


VERSION = _read_version()
