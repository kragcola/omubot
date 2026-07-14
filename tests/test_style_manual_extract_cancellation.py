from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import pytest

import services.style.manual_extract as manual_extract_module
from services.conversation_archive import ConversationArchive
from services.learning_extract_coordinator import (
    ExtractRunParams,
    LearningExtractCoordinator,
)
from services.style import StyleStore


@dataclass
class _CancellationEnvironment:
    archive: ConversationArchive
    coordinator: LearningExtractCoordinator
    runner: Callable[[], Coroutine[Any, Any, dict[str, Any]]]
    extractor_started: asyncio.Event


@pytest.fixture
async def cancellation_environment(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[_CancellationEnvironment]:
    archive = ConversationArchive(db_path=str(tmp_path / "messages.db"))
    style_store = StyleStore(tmp_path / "style.db")
    await archive.init()
    await style_store.init()
    await archive.record(
        group_id="100",
        role="user",
        speaker="user-1",
        content_text="这是一条可扫描的表达学习样本",
        content_json=None,
        message_id=1,
        created_at=1.0,
    )
    await archive.upsert_cursor(
        scanner_name="style_manual_extract",
        chat_type="group",
        chat_id="100",
        scope_key="chat",
        required=True,
        last_message_pk=0,
        last_created_at=0.0,
        scanner_version="v1",
        params_hash="",
        status="active",
    )

    extractor_started = asyncio.Event()

    class _HangingStyleExtractor:
        def __init__(self, llm_client: Any) -> None:
            del llm_client

        async def extract(self, messages: list[dict[str, Any]]) -> list[Any]:
            assert messages
            extractor_started.set()
            await asyncio.Event().wait()
            raise AssertionError("hanging extractor should only finish by cancellation")

    monkeypatch.setattr(
        manual_extract_module,
        "StyleExtractor",
        _HangingStyleExtractor,
    )
    coordinator = LearningExtractCoordinator(nouns=("style",))

    async def runner() -> dict[str, Any]:
        return await manual_extract_module.run_style_manual_extract(
            style_store=style_store,
            message_log=archive,
            llm_client=object(),
            group_id="100",
            limit=10,
            max_batches=1,
        )

    try:
        yield _CancellationEnvironment(
            archive=archive,
            coordinator=coordinator,
            runner=runner,
            extractor_started=extractor_started,
        )
    finally:
        await coordinator.stop()
        await style_store.close()
        await archive.close()


async def _assert_scan_closed_without_cursor_advance(
    archive: ConversationArchive,
) -> None:
    rows = await _scan_run_rows(archive)
    assert rows, "expected the real archive scanner to create a run row"
    assert all(row["status"] != "running" for row in rows), rows
    assert all(row["finished_at"] is not None for row in rows), rows

    cursor = await archive.get_cursor(
        scanner_name="style_manual_extract",
        chat_type="group",
        chat_id="100",
        scope_key="chat",
    )
    assert cursor is not None
    assert int(cursor["last_message_pk"] or 0) == 0


async def _scan_run_rows(
    archive: ConversationArchive,
) -> list[dict[str, Any]]:
    assert archive._db is not None
    result = await archive._db.execute(
        "SELECT status, error, finished_at FROM conversation_scan_runs "
        "WHERE scanner_name = 'style_manual_extract' ORDER BY started_at",
    )
    return [dict(row) for row in await result.fetchall()]


@pytest.mark.asyncio
async def test_style_extract_timeout_closes_scan_without_advancing_cursor(
    cancellation_environment: _CancellationEnvironment,
) -> None:
    env = cancellation_environment

    payload = await env.coordinator.run_noun(
        noun="style",
        group_id="100",
        params=ExtractRunParams(timeout_seconds=0.01),
        runner=env.runner,
    )

    assert payload["status"] == "failed"
    assert payload["nouns"]["style"]["status"] == "timeout"
    assert payload["results"]["style"]["error"] == "timeout"
    await _assert_scan_closed_without_cursor_advance(env.archive)


@pytest.mark.asyncio
async def test_style_extract_external_cancel_closes_scan_and_propagates(
    cancellation_environment: _CancellationEnvironment,
) -> None:
    env = cancellation_environment
    task = asyncio.create_task(
        env.coordinator.run_noun(
            noun="style",
            group_id="100",
            params=ExtractRunParams(timeout_seconds=30.0),
            runner=env.runner,
        )
    )
    await env.extractor_started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await _assert_scan_closed_without_cursor_advance(env.archive)


@pytest.mark.asyncio
async def test_style_extract_shutdown_closes_scan_and_cancels_waiter(
    cancellation_environment: _CancellationEnvironment,
) -> None:
    env = cancellation_environment
    task = asyncio.create_task(
        env.coordinator.run_noun(
            noun="style",
            group_id="100",
            params=ExtractRunParams(timeout_seconds=30.0),
            runner=env.runner,
        )
    )
    await env.extractor_started.wait()

    await env.coordinator.stop()

    with pytest.raises(asyncio.CancelledError):
        await task
    await _assert_scan_closed_without_cursor_advance(env.archive)


@pytest.mark.asyncio
async def test_style_extract_repeated_cancel_waits_for_scan_cleanup(
    cancellation_environment: _CancellationEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = cancellation_environment
    real_finish_scan_batch = manual_extract_module.finish_scan_batch
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def controlled_finish_scan_batch(*args: Any, **kwargs: Any) -> None:
        if kwargs.get("status") == "abandoned":
            cleanup_started.set()
            await cleanup_release.wait()
        try:
            await real_finish_scan_batch(*args, **kwargs)
        finally:
            if kwargs.get("status") == "abandoned":
                cleanup_finished.set()

    monkeypatch.setattr(
        manual_extract_module,
        "finish_scan_batch",
        controlled_finish_scan_batch,
    )
    task = asyncio.create_task(env.runner())
    await asyncio.wait_for(env.extractor_started.wait(), timeout=1.0)

    task.cancel()
    await asyncio.wait_for(cleanup_started.wait(), timeout=1.0)
    task.cancel()
    await asyncio.sleep(0)
    returned_before_cleanup = task.done()
    rows_before_release = await _scan_run_rows(env.archive)

    cleanup_release.set()
    await asyncio.wait_for(cleanup_finished.wait(), timeout=1.0)
    with pytest.raises(asyncio.CancelledError):
        await task

    assert returned_before_cleanup is False, rows_before_release
    await _assert_scan_closed_without_cursor_advance(env.archive)
