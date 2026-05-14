"""Slang service exception types."""

from __future__ import annotations


class SlangDatabaseCorruptError(RuntimeError):
    """Raised when the slang SQLite database cannot be opened or is malformed.

    Carries the failing database path so callers (plugin, admin API) can surface
    actionable diagnostics without re-deriving the path from context.
    """

    def __init__(self, db_path: str, *, original: BaseException | None = None) -> None:
        self.db_path = db_path
        self.original = original
        message = f"slang database corrupt | path={db_path}"
        if original is not None:
            message = f"{message} | reason={original}"
        super().__init__(message)
