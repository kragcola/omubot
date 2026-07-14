"""Compatibility exports for the core history-backfill stage."""

from plugins.history_loader.plugin import _extract_content, load_group_history, run_history_backfill

__all__ = ["_extract_content", "load_group_history", "run_history_backfill"]
