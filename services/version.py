"""Omubot version info and GitHub release check."""

from __future__ import annotations

import tomllib
from pathlib import Path


def _read_version() -> str:
    """Read bot version from pyproject.toml, the single source of truth."""
    # Try installed package metadata first (most reliable when pip-installed)
    try:
        from importlib.metadata import version as _pkg_version
        return _pkg_version("qq-bot")
    except Exception:
        pass
    # Fallback: parse pyproject.toml from repo root
    try:
        candidates = [
            Path(__file__).parent.parent / "pyproject.toml",
            Path.cwd() / "pyproject.toml",
        ]
        for candidate in candidates:
            if candidate.is_file():
                data = tomllib.loads(candidate.read_text())
                v: str = data["project"]["version"]
                return v
    except Exception:
        pass
    return "0.0.0"


VERSION = _read_version()
GITHUB_REPO = "kragcola/omubot"


async def fetch_latest_release() -> dict | None:
    """Fetch the latest GitHub release. Returns None on failure."""
    import httpx

    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
            if resp.status_code == 200:
                return resp.json()
            return None
    except Exception:
        return None


def parse_semver(version: str) -> tuple[int, int, int]:
    """Parse '1.2.3' into (1, 2, 3). Strips leading 'v' if present."""
    v = version.lstrip("v")
    parts = v.split(".")
    major = int(parts[0]) if len(parts) > 0 else 0
    minor = int(parts[1]) if len(parts) > 1 else 0
    patch = int(parts[2]) if len(parts) > 2 else 0
    return (major, minor, patch)


def version_summary() -> str:
    """Single-line version string."""
    return f"Omubot v{VERSION}"
