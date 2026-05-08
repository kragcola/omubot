"""Affection & Nickname system — per-user relationship tracking."""

from __future__ import annotations

from plugins.affection.engine import AffectionEngine
from plugins.affection.models import AffectionProfile
from plugins.affection.plugin import AffectionConfig, AffectionPlugin
from plugins.affection.store import AffectionStore

__all__ = ["AffectionConfig", "AffectionEngine", "AffectionPlugin", "AffectionProfile", "AffectionStore"]
