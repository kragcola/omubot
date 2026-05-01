"""Affection data models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AffectionProfile:
    """Per-user affection state."""

    user_id: str  # QQ号 as string
    score: float = 0.0  # 0.0 ~ 100.0
    custom_nickname: str = ""  # user-requested nickname (private chat), e.g. "司君"
    group_nickname: str = ""  # user-requested nickname (group chat), separate from private
    last_interaction: str = ""  # ISO timestamp
    total_interactions: int = 0
    first_interaction: str = ""  # ISO timestamp
    daily_count: int = 0  # interactions today (for cap)
    daily_date: str = ""  # date string for daily reset
    preferred_suffix: str = ""  # "君", "酱", "同学", "さん"

    @property
    def tier(self) -> str:
        if self.score < 20:
            return "陌生人"
        if self.score < 40:
            return "熟人"
        if self.score < 60:
            return "朋友"
        if self.score < 80:
            return "好朋友"
        return "重要的人"

    @property
    def mood_bonus_valence(self) -> float:
        """How much warmth the bot adds toward this user (0.0-0.25)."""
        if self.score < 20:
            return 0.0
        if self.score < 40:
            return 0.05
        if self.score < 60:
            return 0.10
        if self.score < 80:
            return 0.18
        return 0.25

    @property
    def default_suffix(self) -> str:
        """Recommended address suffix based on score tier."""
        if self.score < 20:
            return ""
        if self.score < 40:
            return ""
        if self.score < 60:
            return "君"
        return "君"
