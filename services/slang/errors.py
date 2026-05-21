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


class SlangCollisionError(ValueError):
    """Raised when a term/alias update would collide with an existing entry."""

    def __init__(self, term_id: str, collides_with_id: str, collision_key: str) -> None:
        self.term_id = term_id
        self.collides_with_id = collides_with_id
        self.collision_key = collision_key
        super().__init__(
            f"alias collision: term {term_id} would collide with "
            f"{collides_with_id} on key '{collision_key}'"
        )


class SlangCrossScopeMergeError(ValueError):
    """Raised when ``merge_terms`` is asked to fold a source term whose
    ``(scope, group_id)`` does not match the target's.

    Cross-scope merges would silently grant cross-group visibility to lexical
    state that admins never explicitly enabled, violating decision 7.5 of the
    multilayer-memory plan. Reject at the merge boundary.
    """

    def __init__(
        self,
        target_id: str,
        source_id: str,
        target_scope: str,
        target_group_id: str,
        source_scope: str,
        source_group_id: str,
    ) -> None:
        self.target_id = target_id
        self.source_id = source_id
        self.target_scope = target_scope
        self.target_group_id = target_group_id
        self.source_scope = source_scope
        self.source_group_id = source_group_id
        super().__init__(
            f"cross-scope merge rejected: target {target_id} "
            f"(scope={target_scope}, group_id={target_group_id!r}) cannot absorb "
            f"source {source_id} (scope={source_scope}, group_id={source_group_id!r})"
        )
