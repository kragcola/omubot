"""Behavior contracts for owner-driven, bounded database retention."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from services.block_trace.store import BlockTraceStore
from services.block_trace.types import PromptBlockTrace
from services.storage.retention import (
    OwnerRetentionOutcome,
    RetentionHandlerNotRegistered,
    RetentionRequest,
    RetentionService,
    create_default_retention_service,
)


class _RecordingHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, bool]] = []

    async def apply(
        self,
        *,
        keep_days: int,
        batch_size: int,
        dry_run: bool,
    ) -> OwnerRetentionOutcome:
        self.calls.append((keep_days, batch_size, dry_run))
        return OwnerRetentionOutcome(
            candidate_count=batch_size,
            deleted_count=0 if dry_run else batch_size,
            details={"prompt_block_traces": batch_size},
        )


async def test_retention_is_disabled_by_default_without_calling_owner() -> None:
    handler = _RecordingHandler()
    service = RetentionService(handlers={"block_trace": handler})

    result = await service.run("block_trace")

    assert result.status == "disabled"
    assert result.dry_run is True
    assert result.deleted_count == 0
    assert handler.calls == []


async def test_enabled_dry_run_delegates_without_deleting() -> None:
    handler = _RecordingHandler()
    service = RetentionService(handlers={"block_trace": handler})

    result = await service.run(
        "block_trace",
        RetentionRequest(enabled=True, dry_run=True, keep_days=30, batch_size=25),
    )

    assert result.status == "dry_run"
    assert result.candidate_count == 25
    assert result.deleted_count == 0
    assert handler.calls == [(30, 25, True)]


async def test_enabled_apply_reports_owner_result() -> None:
    handler = _RecordingHandler()
    service = RetentionService(handlers={"block_trace": handler})

    result = await service.run(
        "block_trace",
        RetentionRequest(enabled=True, dry_run=False, keep_days=14, batch_size=3),
    )

    assert result.status == "applied"
    assert result.candidate_count == 3
    assert result.deleted_count == 3
    assert handler.calls == [(14, 3, False)]


async def test_owner_managed_database_without_handler_fails_closed() -> None:
    service = RetentionService()

    with pytest.raises(RetentionHandlerNotRegistered):
        await service.run("messages", RetentionRequest(enabled=True))


async def test_non_owner_managed_database_cannot_enable_retention() -> None:
    service = RetentionService()

    with pytest.raises(ValueError, match="does not allow"):
        await service.run("usage", RetentionRequest(enabled=True))


@pytest.mark.parametrize(
    "retention_request",
    (
        RetentionRequest(enabled=True, keep_days=0),
        RetentionRequest(enabled=True, batch_size=0),
        RetentionRequest(enabled=True, batch_size=10_001),
    ),
)
async def test_invalid_retention_bounds_are_rejected(
    retention_request: RetentionRequest,
) -> None:
    service = RetentionService(handlers={"block_trace": _RecordingHandler()})

    with pytest.raises(ValueError):
        await service.run("block_trace", retention_request)


class _OversizedHandler:
    async def apply(
        self,
        *,
        keep_days: int,
        batch_size: int,
        dry_run: bool,
    ) -> OwnerRetentionOutcome:
        del keep_days, dry_run
        return OwnerRetentionOutcome(
            candidate_count=batch_size + 1,
            deleted_count=batch_size + 1,
        )


async def test_owner_handler_cannot_report_more_than_bounded_batch() -> None:
    service = RetentionService(handlers={"block_trace": _OversizedHandler()})

    with pytest.raises(RuntimeError, match="batch"):
        await service.run(
            "block_trace",
            RetentionRequest(enabled=True, dry_run=False, batch_size=2),
        )


def _old_trace(trace_id: str) -> PromptBlockTrace:
    return PromptBlockTrace(
        trace_id=trace_id,
        request_id=f"request-{trace_id}",
        task="main",
        source="system",
        provider="tests",
        candidate_id=f"candidate-{trace_id}",
        decision="accepted",
        hit_reason="test",
        evidence_refs=(),
        token_estimate=1,
        char_count=1,
        position="dynamic",
        label="test",
        created_at="2020-01-01T00:00:00+08:00",
    )


async def _seed_old_block_trace_rows(store: BlockTraceStore) -> None:
    await store.record(_old_trace("old-trace"))
    await store.record_humanization_metrics(
        request_id="old-humanization",
        score={"score": 0.5, "axes": {}, "issues": []},
        metric_id="old-humanization",
        created_at="2020-01-01T00:00:00+08:00",
    )
    await store.record_runtime_metric(
        metric_key="test-retention",
        metric_id="old-runtime",
        created_at="2020-01-01T00:00:00+08:00",
    )


async def _old_row_count(store: BlockTraceStore) -> int:
    traces = await store.recent(limit=20)
    humanization = await store.list_humanization_metrics(limit=20)
    runtime = await store.list_runtime_metrics(limit=20)
    return len(traces) + len(humanization) + len(runtime)


async def test_block_trace_retention_dry_run_is_bounded_and_read_only(tmp_path) -> None:
    store = BlockTraceStore(tmp_path / "block-trace.db")
    await store.init()
    try:
        await _seed_old_block_trace_rows(store)

        result = await store.apply_retention(
            keep_days=1,
            batch_size=2,
            dry_run=True,
        )

        assert result["candidate_count"] == 2
        assert result["deleted_count"] == 0
        assert sum(result["details"].values()) == 2
        assert await _old_row_count(store) == 3
    finally:
        await store.close()


async def test_block_trace_retention_apply_respects_total_batch_limit(tmp_path) -> None:
    store = BlockTraceStore(tmp_path / "block-trace.db")
    await store.init()
    try:
        await _seed_old_block_trace_rows(store)

        result = await store.apply_retention(
            keep_days=1,
            batch_size=2,
            dry_run=False,
        )

        assert result["candidate_count"] == 2
        assert result["deleted_count"] == 2
        assert await _old_row_count(store) == 1
    finally:
        await store.close()


async def test_block_trace_retention_cancellation_rolls_back_batch(tmp_path) -> None:
    store = BlockTraceStore(tmp_path / "block-trace.db")
    await store.init()
    try:
        await _seed_old_block_trace_rows(store)
        connection = store._conn()
        original_execute = connection.execute
        delete_calls = 0

        async def cancel_second_delete(sql, *args, **kwargs):
            nonlocal delete_calls
            if str(sql).lstrip().upper().startswith("DELETE"):
                delete_calls += 1
                if delete_calls == 2:
                    raise asyncio.CancelledError
            return await original_execute(sql, *args, **kwargs)

        with (
            patch.object(connection, "execute", side_effect=cancel_second_delete),
            pytest.raises(asyncio.CancelledError),
        ):
            await store.apply_retention(
                keep_days=1,
                batch_size=3,
                dry_run=False,
            )

        assert await _old_row_count(store) == 3
    finally:
        await store.close()


async def test_default_registry_uses_block_trace_owner_handler(tmp_path) -> None:
    db_path = tmp_path / "storage" / "block_trace.db"
    db_path.parent.mkdir(parents=True)
    store = BlockTraceStore(db_path)
    await store.init()
    try:
        await _seed_old_block_trace_rows(store)
    finally:
        await store.close()

    service = create_default_retention_service(tmp_path)
    result = await service.run(
        "block_trace",
        RetentionRequest(enabled=True, dry_run=True, keep_days=1, batch_size=2),
    )

    assert result.status == "dry_run"
    assert result.candidate_count == 2
    assert result.deleted_count == 0
