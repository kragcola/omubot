"""Owner-driven retention contracts for governed SQLite stores."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from services.storage.catalog import (
    DEFAULT_DATABASE_CATALOG,
    DatabaseCatalog,
    RetentionProfile,
)


@dataclass(frozen=True, slots=True)
class RetentionRequest:
    enabled: bool = False
    dry_run: bool = True
    keep_days: int = 7
    batch_size: int = 100


@dataclass(frozen=True, slots=True)
class OwnerRetentionOutcome:
    candidate_count: int
    deleted_count: int
    details: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetentionResult:
    db_id: str
    owner: str
    status: str
    dry_run: bool
    candidate_count: int = 0
    deleted_count: int = 0
    details: dict[str, int] = field(default_factory=dict)


class RetentionHandler(Protocol):
    async def apply(
        self,
        *,
        keep_days: int,
        batch_size: int,
        dry_run: bool,
    ) -> OwnerRetentionOutcome: ...


class BlockTraceRetentionStore(Protocol):
    async def apply_retention(
        self,
        *,
        keep_days: int,
        batch_size: int,
        dry_run: bool,
    ) -> dict[str, Any]: ...


class RetentionHandlerNotRegistered(RuntimeError):
    """Raised when an owner-managed database has no explicit handler."""


class BlockTraceRetentionHandler:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def apply(
        self,
        *,
        keep_days: int,
        batch_size: int,
        dry_run: bool,
    ) -> OwnerRetentionOutcome:
        from services.block_trace.store import BlockTraceStore

        store = BlockTraceStore(self._db_path)
        try:
            await store.init()
            result = await store.apply_retention(
                keep_days=keep_days,
                batch_size=batch_size,
                dry_run=dry_run,
            )
        finally:
            await store.close()
        return OwnerRetentionOutcome(
            candidate_count=int(result["candidate_count"]),
            deleted_count=int(result["deleted_count"]),
            details={
                str(table): int(count)
                for table, count in result["details"].items()
            },
        )


class BoundBlockTraceRetentionHandler:
    """Retention adapter for the already-owned runtime BlockTraceStore."""

    def __init__(self, store: BlockTraceRetentionStore) -> None:
        self._store = store

    async def apply(
        self,
        *,
        keep_days: int,
        batch_size: int,
        dry_run: bool,
    ) -> OwnerRetentionOutcome:
        result = await self._store.apply_retention(
            keep_days=keep_days,
            batch_size=batch_size,
            dry_run=dry_run,
        )
        return OwnerRetentionOutcome(
            candidate_count=int(result["candidate_count"]),
            deleted_count=int(result["deleted_count"]),
            details={
                str(table): int(count)
                for table, count in result["details"].items()
            },
        )


class RetentionService:
    def __init__(
        self,
        *,
        catalog: DatabaseCatalog = DEFAULT_DATABASE_CATALOG,
        handlers: dict[str, RetentionHandler] | None = None,
    ) -> None:
        self._catalog = catalog
        self._handlers = dict(handlers or {})

    async def run(
        self,
        db_id: str,
        request: RetentionRequest | None = None,
    ) -> RetentionResult:
        spec = self._catalog.get(db_id)
        effective = request or RetentionRequest()
        if not effective.enabled:
            return RetentionResult(
                db_id=db_id,
                owner=spec.owner,
                status="disabled",
                dry_run=effective.dry_run,
            )
        if spec.retention_profile is not RetentionProfile.OWNER_MANAGED:
            raise ValueError(
                f"database {db_id} does not allow owner-managed retention"
            )
        if effective.keep_days < 1:
            raise ValueError("keep_days must be at least 1")
        if not 1 <= effective.batch_size <= 10_000:
            raise ValueError("batch_size must be between 1 and 10000")
        handler = self._handlers.get(db_id)
        if handler is None:
            raise RetentionHandlerNotRegistered(
                f"database {db_id} has no retention handler"
            )
        outcome = await handler.apply(
            keep_days=effective.keep_days,
            batch_size=effective.batch_size,
            dry_run=effective.dry_run,
        )
        if (
            outcome.candidate_count < 0
            or outcome.deleted_count < 0
            or outcome.candidate_count > effective.batch_size
            or outcome.deleted_count > effective.batch_size
            or outcome.deleted_count > outcome.candidate_count
        ):
            raise RuntimeError(
                f"retention handler for {db_id} violated batch bounds"
            )
        if effective.dry_run and outcome.deleted_count:
            raise RuntimeError(
                f"retention handler for {db_id} deleted rows during dry-run"
            )
        return RetentionResult(
            db_id=db_id,
            owner=spec.owner,
            status="dry_run" if effective.dry_run else "applied",
            dry_run=effective.dry_run,
            candidate_count=outcome.candidate_count,
            deleted_count=outcome.deleted_count,
            details=dict(outcome.details),
        )


def create_default_retention_service(repo_root: str | Path) -> RetentionService:
    block_trace_path = DEFAULT_DATABASE_CATALOG.resolve(repo_root, "block_trace")
    return RetentionService(
        handlers={
            "block_trace": BlockTraceRetentionHandler(block_trace_path),
        },
    )


def create_bound_retention_service(
    block_trace_store: BlockTraceRetentionStore,
) -> RetentionService:
    return RetentionService(
        handlers={
            "block_trace": BoundBlockTraceRetentionHandler(block_trace_store),
        },
    )
