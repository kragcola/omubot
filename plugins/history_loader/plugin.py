"""Compatibility exports for the core history-backfill stage."""

from services.history_backfill import (
    _contains_debug_command,
    _extract_content,
    load_group_history,
    run_history_backfill,
)

__all__ = [
    "_contains_debug_command",
    "_extract_content",
    "load_group_history",
    "run_history_backfill",
]
