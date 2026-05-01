"""AffectionStore — JSON file persistence for per-user affection data."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from plugins.affection.models import AffectionProfile

_L = logger.bind(channel="affection")

CST = ZoneInfo("Asia/Shanghai")


class AffectionStore:
    """Read/write per-user AffectionProfile JSON files. Cache in memory."""

    def __init__(self, storage_dir: str = "storage/affection") -> None:
        self._dir = Path(storage_dir)
        self._profiles: dict[str, AffectionProfile] = {}

    async def startup(self) -> None:
        """Create storage dir, load all existing profiles into cache."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self._profiles.clear()
        for f in self._dir.glob("*.json"):
            user_id = f.stem
            profile = self._load_from_disk(user_id)
            if profile is not None:
                self._profiles[user_id] = profile
        _L.info("AffectionStore loaded {} profiles", len(self._profiles))

    def get(self, user_id: str) -> AffectionProfile:
        """Get profile for a user. Returns a fresh default if not tracked yet."""
        if user_id in self._profiles:
            return self._profiles[user_id]
        today = datetime.now(CST).strftime("%Y-%m-%d")
        return AffectionProfile(user_id=user_id, daily_date=today)

    def save(self, profile: AffectionProfile) -> None:
        """Atomic save to disk and update in-memory cache."""
        self._profiles[profile.user_id] = profile
        path = self._dir / f"{profile.user_id}.json"
        data = {
            "user_id": profile.user_id,
            "score": profile.score,
            "custom_nickname": profile.custom_nickname,
            "last_interaction": profile.last_interaction,
            "total_interactions": profile.total_interactions,
            "first_interaction": profile.first_interaction,
            "daily_count": profile.daily_count,
            "daily_date": profile.daily_date,
            "preferred_suffix": profile.preferred_suffix,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _load_from_disk(self, user_id: str) -> AffectionProfile | None:
        """Read a single JSON file into an AffectionProfile. Returns None on failure."""
        path = self._dir / f"{user_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return AffectionProfile(
                user_id=data["user_id"],
                score=float(data.get("score", 0.0)),
                custom_nickname=data.get("custom_nickname", ""),
                last_interaction=data.get("last_interaction", ""),
                total_interactions=int(data.get("total_interactions", 0)),
                first_interaction=data.get("first_interaction", ""),
                daily_count=int(data.get("daily_count", 0)),
                daily_date=data.get("daily_date", ""),
                preferred_suffix=data.get("preferred_suffix", ""),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            _L.warning("Failed to load affection profile {}: {}", path, e)
            return None
