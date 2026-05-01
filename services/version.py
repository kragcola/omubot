"""Omubot version info and GitHub release check."""

from __future__ import annotations

VERSION = "1.0.4"
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
