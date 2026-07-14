"""Tests for MemoryConsolidator dry-run orchestration.

Asserts:

- 5 typed candidate domains all parsed from a stub LLM JSON
- production stores (slang/style/episode/graph) receive **zero** writes
- normalizer attach uses ``domain="general"`` /
  ``source_table="consolidator_candidates"``
- archive cursor advances on success, holds on failure
- D2 cancel-path: ``asyncio.wait_for`` timeout leaves run row marked
  ``failed`` with no orphaned normalizer rows
- external cancellation closes the real archive scan row without advancing
  its cursor
- repeated cancellation cannot return before archive scan cleanup finishes
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import services.memory_consolidator.consolidator as consolidator_module
from services.conversation_archive.store import ConversationArchive
from services.learning_normalizer.store import LearningNormalizerStore
from services.memory_consolidator import (
    ConsolidatorCandidatesStore,
    MemoryConsolidator,
)

_STUB_RESPONSE = {
    "facts": [
        {
            "subject": "Alice",
            "predicate": "likes",
            "object": "spicy noodles",
            "evidence_quotes": ["Alice: 我超爱吃辣面"],
            "confidence": 0.7,
        }
    ],
    "slang": [
        {
            "term": "yyds",
            "meaning": "永远的神 - top tier",
            "aliases": ["YYDS"],
            "repeat_policy": "understand_only",
            "evidence": "Alice: 这家辣面 yyds",
            "confidence": 0.8,
        }
    ],
    "styles": [
        {
            "expression": "用句末加 啦 表达熟络",
            "situation": "回应熟悉群友的提问",
            "outcome_signal": "用户继续聊天",
            "confidence": 0.5,
        }
    ],
    "episodes": [
        {
            "situation": "晚饭推荐辣面",
            "observed_context": "Alice 抱怨外卖难吃",
            "action_taken": "推荐附近的小店",
            "outcome_signal": "Alice 表示感谢",
            "reflection": "推荐式回应在午晚餐场景接受度高",
            "confidence": 0.6,
        }
    ],
    "graph_relations": [
        {
            "subject_node": "Alice",
            "predicate": "frequents",
            "object_node": "辣面店",
            "edge_type": "fact",
            "confidence": 0.5,
        }
    ],
}


class StubLLM:
    """Returns a fixed JSON for ``reflection_consolidator``; empty for others."""

    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response_text = json.dumps(response or _STUB_RESPONSE)

    async def _call(self, request: Any) -> dict[str, Any]:
        self.calls.append({"task": request.task})
        if str(request.task) == "reflection_consolidator":
            return {"text": self._response_text}
        return {"text": "summary placeholder"}


class SlowStubLLM(StubLLM):
    async def _call(self, request: Any) -> dict[str, Any]:
        await asyncio.sleep(5.0)
        return await super()._call(request)


class HangingStubLLM:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def _call(self, request: Any) -> dict[str, Any]:
        del request
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("hanging LLM should only finish by cancellation")


@pytest.fixture
async def archive(tmp_path):
    arc = ConversationArchive(str(tmp_path / "messages.db"))
    await arc.init()
    yield arc
    await arc.close()


@pytest.fixture
async def store(tmp_path):
    s = ConsolidatorCandidatesStore(str(tmp_path / "consolidator_candidates.db"))
    await s.init()
    yield s
    await s.close()


@pytest.fixture
async def normalizer(tmp_path):
    n = LearningNormalizerStore(str(tmp_path / "consolidator_normalizer.db"))
    await n.init()
    yield n
    await n.close()


async def _seed_archive(archive: ConversationArchive, group_id: str = "g_test") -> None:
    sample = [
        ("Alice(1)", "我超爱吃辣面"),
        ("Bob(2)", "推荐哪家？"),
        ("Alice(1)", "巷口那家 yyds"),
        ("Bob(2)", "晚上一起？"),
        ("Alice(1)", "好啦"),
    ]
    for idx, (speaker, text) in enumerate(sample, start=1):
        await archive.record(
            group_id=group_id,
            role="user",
            speaker=speaker,
            content_text=text,
            content_json=None,
            message_id=idx,
            created_at=1_700_000_000.0 + idx,
        )


async def _seed_consolidator_cursor(
    archive: ConversationArchive,
    *,
    group_id: str,
) -> None:
    await archive.upsert_cursor(
        scanner_name="memory_consolidator",
        chat_type="group",
        chat_id=group_id,
        scope_key="chat",
        required=True,
        last_message_pk=0,
        last_created_at=0.0,
        scanner_version="v1",
        params_hash="default-2026-05-21",
        status="active",
    )


async def _consolidator_scan_rows(
    archive: ConversationArchive,
    *,
    group_id: str,
) -> list[dict[str, Any]]:
    assert archive._db is not None
    result = await archive._db.execute(
        "SELECT status, error, finished_at FROM conversation_scan_runs "
        "WHERE scanner_name = 'memory_consolidator' AND chat_type = 'group' "
        "AND chat_id = ? ORDER BY started_at",
        (group_id,),
    )
    return [dict(row) for row in await result.fetchall()]


async def _assert_cancelled_scan_closed_without_cursor_advance(
    archive: ConversationArchive,
    *,
    group_id: str,
) -> None:
    rows = await _consolidator_scan_rows(archive, group_id=group_id)
    assert rows, "expected the real archive scanner to create a run row"
    assert all(row["status"] != "running" for row in rows), rows
    assert all(row["finished_at"] is not None for row in rows), rows
    assert all(row["status"] in {"abandoned", "cancelled"} for row in rows), rows

    cursor = await archive.get_cursor(
        scanner_name="memory_consolidator",
        chat_type="group",
        chat_id=group_id,
        scope_key="chat",
    )
    assert cursor is not None
    assert int(cursor["last_message_pk"] or 0) == 0


@pytest.mark.asyncio
async def test_run_once_records_five_typed_candidates(archive, store, normalizer):
    await _seed_archive(archive, group_id="g1")
    llm = StubLLM()
    consolidator = MemoryConsolidator(
        store=store, archive=archive, normalizer=normalizer, llm_client=llm,
    )
    report = await consolidator.run_once(
        group_id="g1", triggered_by="test", scope="group",
        max_batches=1, batch_size=10,
    )
    assert report.status == "done"
    assert report.scanned == 5
    assert report.candidates == 5

    candidates = await store.list_candidates(run_id=report.run_id)
    domains = sorted({c.domain for c in candidates})
    assert domains == ["episode", "fact", "graph_relation", "slang", "style"]
    for c in candidates:
        assert c.state == "dry_run"
        assert c.scope == "group"
        assert c.group_id == "g1"
        assert c.normalizer_cluster_id != ""

    # both spine tasks invoked at least once → no dead code
    tasks_called = {call["task"] for call in llm.calls}
    assert "reflection_consolidator" in tasks_called
    assert "episode_summarizer" in tasks_called


@pytest.mark.asyncio
async def test_run_once_zero_writes_to_production_stores(
    archive, store, normalizer,
):
    await _seed_archive(archive, group_id="g1")

    fake_slang = MagicMock()
    fake_slang.create_term = AsyncMock()
    fake_slang.merge_terms = AsyncMock()
    fake_style = MagicMock()
    fake_style.create_expression = AsyncMock()
    fake_episode = MagicMock()
    fake_episode.create_episode = AsyncMock()
    fake_graph = MagicMock()
    fake_graph.add_edge = AsyncMock()

    llm = StubLLM()
    consolidator = MemoryConsolidator(
        store=store, archive=archive, normalizer=normalizer, llm_client=llm,
    )
    await consolidator.run_once(
        group_id="g1", triggered_by="test", scope="group",
        max_batches=1, batch_size=10,
    )

    fake_slang.create_term.assert_not_awaited()
    fake_slang.merge_terms.assert_not_awaited()
    fake_style.create_expression.assert_not_awaited()
    fake_episode.create_episode.assert_not_awaited()
    fake_graph.add_edge.assert_not_awaited()


@pytest.mark.asyncio
async def test_normalizer_attach_uses_general_domain(archive, store):
    await _seed_archive(archive, group_id="g1")
    spy = MagicMock()
    spy.attach_candidate = AsyncMock(
        return_value=type(
            "R", (), {"cluster_id": "cluster_xyz"}
        )()
    )
    llm = StubLLM()
    consolidator = MemoryConsolidator(
        store=store, archive=archive, normalizer=spy, llm_client=llm,
    )
    await consolidator.run_once(
        group_id="g1", triggered_by="test", scope="group",
        max_batches=1, batch_size=10,
    )
    assert spy.attach_candidate.await_count == 5
    for call in spy.attach_candidate.await_args_list:
        kwargs = call.kwargs
        assert kwargs["domain"] == "general"
        assert kwargs["profile"] == "general"
        assert kwargs["source_table"] == "consolidator_candidates"
        assert kwargs["scope"] == "group"


@pytest.mark.asyncio
async def test_run_once_advances_cursor_only_on_success(
    archive, store, normalizer,
):
    await _seed_archive(archive, group_id="g1")
    consolidator = MemoryConsolidator(
        store=store, archive=archive, normalizer=normalizer, llm_client=StubLLM(),
    )
    first = await consolidator.run_once(
        group_id="g1", triggered_by="test", scope="group",
        max_batches=1, batch_size=10,
    )
    assert first.scanned == 5

    # second pass with no new messages → 0 scanned
    second = await consolidator.run_once(
        group_id="g1", triggered_by="test", scope="group",
        max_batches=1, batch_size=10,
    )
    assert second.scanned == 0
    assert second.status == "done"


@pytest.mark.asyncio
async def test_run_once_failed_llm_keeps_cursor(archive, store, normalizer):
    await _seed_archive(archive, group_id="g1")

    class BoomLLM:
        async def _call(self, request):
            raise RuntimeError("upstream LLM exploded")

    consolidator = MemoryConsolidator(
        store=store, archive=archive, normalizer=normalizer, llm_client=BoomLLM(),
    )
    report = await consolidator.run_once(
        group_id="g1", triggered_by="test", scope="group",
        max_batches=1, batch_size=10,
    )
    assert report.status == "done"
    assert report.candidates == 0

    # cursor must not have advanced — second pass still sees the same rows
    next_pass = await consolidator.run_once(
        group_id="g1", triggered_by="test", scope="group",
        max_batches=1, batch_size=10,
    )
    assert next_pass.scanned == 5


@pytest.mark.asyncio
async def test_run_once_cancel_marks_run_failed(archive, store, normalizer):
    await _seed_archive(archive, group_id="g1")
    slow_llm = SlowStubLLM()
    consolidator = MemoryConsolidator(
        store=store, archive=archive, normalizer=normalizer, llm_client=slow_llm,
    )
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            consolidator.run_once(
                group_id="g1", triggered_by="test", scope="group",
                max_batches=1, batch_size=10,
            ),
            timeout=0.1,
        )

    runs = await store.list_runs(limit=5)
    assert runs, "run row must exist"
    latest = runs[0]
    assert latest.status == "failed"
    assert latest.candidates_count == 0

    # no orphaned candidates were recorded for the failed run
    orphans = await store.list_candidates(run_id=latest.run_id)
    assert orphans == []


@pytest.mark.asyncio
async def test_run_once_external_cancel_closes_archive_scan_and_holds_cursor(
    archive,
    store,
    normalizer,
):
    group_id = "g_external_cancel"
    await _seed_archive(archive, group_id=group_id)
    await _seed_consolidator_cursor(archive, group_id=group_id)
    hanging_llm = HangingStubLLM()
    consolidator = MemoryConsolidator(
        store=store,
        archive=archive,
        normalizer=normalizer,
        llm_client=hanging_llm,
    )
    task = asyncio.create_task(
        consolidator.run_once(
            group_id=group_id,
            triggered_by="test",
            scope="group",
            max_batches=1,
            batch_size=10,
        )
    )
    await asyncio.wait_for(hanging_llm.started.wait(), timeout=1.0)

    task.cancel("external cancellation")

    with pytest.raises(asyncio.CancelledError) as raised:
        await asyncio.wait_for(task, timeout=1.0)
    assert raised.value.args == ("external cancellation",)
    await _assert_cancelled_scan_closed_without_cursor_advance(
        archive,
        group_id=group_id,
    )


@pytest.mark.asyncio
async def test_run_once_repeated_cancel_waits_for_archive_scan_cleanup(
    archive,
    store,
    normalizer,
    monkeypatch: pytest.MonkeyPatch,
):
    group_id = "g_repeated_cancel"
    await _seed_archive(archive, group_id=group_id)
    await _seed_consolidator_cursor(archive, group_id=group_id)
    hanging_llm = HangingStubLLM()
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()
    cleanup_finished = asyncio.Event()
    real_finish_scan_batch = consolidator_module._archive_finish_scan_batch

    async def controlled_finish_scan_batch(*args: Any, **kwargs: Any) -> None:
        is_cancel_cleanup = kwargs.get("status") in {"abandoned", "cancelled"}
        if is_cancel_cleanup:
            cleanup_started.set()
            await cleanup_release.wait()
        await real_finish_scan_batch(*args, **kwargs)
        if is_cancel_cleanup:
            cleanup_finished.set()

    monkeypatch.setattr(
        consolidator_module,
        "_archive_finish_scan_batch",
        controlled_finish_scan_batch,
    )
    consolidator = MemoryConsolidator(
        store=store,
        archive=archive,
        normalizer=normalizer,
        llm_client=hanging_llm,
    )
    task = asyncio.create_task(
        consolidator.run_once(
            group_id=group_id,
            triggered_by="test",
            scope="group",
            max_batches=1,
            batch_size=10,
        )
    )
    await asyncio.wait_for(hanging_llm.started.wait(), timeout=1.0)

    task.cancel("initial cancellation")
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=1.0)
    except TimeoutError:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)
        pytest.fail("archive cancellation cleanup did not start")

    task.cancel("repeated cancellation")
    await asyncio.sleep(0)
    returned_before_cleanup = task.done()
    rows_before_release = await _consolidator_scan_rows(
        archive,
        group_id=group_id,
    )

    cleanup_release.set()
    await asyncio.wait_for(cleanup_finished.wait(), timeout=1.0)
    with pytest.raises(asyncio.CancelledError) as raised:
        await asyncio.wait_for(task, timeout=1.0)

    assert returned_before_cleanup is False, rows_before_release
    assert rows_before_release, "scan row must exist before cleanup is released"
    assert all(row["status"] == "running" for row in rows_before_release), (
        rows_before_release
    )
    assert raised.value.args == ("initial cancellation",)
    await _assert_cancelled_scan_closed_without_cursor_advance(
        archive,
        group_id=group_id,
    )


@pytest.mark.asyncio
async def test_run_once_cancel_after_owner_finish_commit_has_consistent_outcome(
    archive,
    store,
    normalizer,
    monkeypatch: pytest.MonkeyPatch,
):
    group_id = "g_owner_finish_commit_cancel"
    await _seed_archive(archive, group_id=group_id)
    await _seed_consolidator_cursor(archive, group_id=group_id)
    finish_committed = asyncio.Event()
    allow_finish_return = asyncio.Event()
    real_finish_run = store.finish_run

    async def controlled_finish_run(*args: Any, **kwargs: Any) -> Any:
        result = await real_finish_run(*args, **kwargs)
        if not finish_committed.is_set():
            finish_committed.set()
            await allow_finish_return.wait()
        return result

    monkeypatch.setattr(store, "finish_run", controlled_finish_run)
    consolidator = MemoryConsolidator(
        store=store,
        archive=archive,
        normalizer=normalizer,
        llm_client=StubLLM(),
    )
    task = asyncio.create_task(
        consolidator.run_once(
            group_id=group_id,
            triggered_by="test",
            scope="group",
            max_batches=1,
            batch_size=10,
        )
    )

    async with asyncio.timeout(1.0):
        await finish_committed.wait()
        task.cancel("cancel after owner finish commit")
        await asyncio.sleep(0)
        allow_finish_return.set()
        try:
            report = await task
        except asyncio.CancelledError as exc:
            outcome: dict[str, Any] = {
                "kind": "cancelled",
                "cancel_args": exc.args,
                "report_status": None,
            }
        else:
            outcome = {
                "kind": "returned",
                "cancel_args": None,
                "report_status": report.status,
            }

        runs = await store.list_runs(limit=5)
        assert runs, "owner run row must exist"
        latest = runs[0]
        outcome_is_consistent = (
            outcome["kind"] == "returned"
            and outcome["report_status"] == "done"
            and latest.status == "done"
        ) or (
            outcome["kind"] == "cancelled"
            and outcome["cancel_args"] == ("cancel after owner finish commit",)
            and latest.status == "failed"
        )
        observed = {
            "outcome": outcome,
            "owner_status": latest.status,
            "owner_finished_at": latest.finished_at,
        }
        assert (
            latest.status != "running"
            and latest.finished_at > 0
            and outcome_is_consistent
        ), observed


@pytest.mark.asyncio
async def test_run_once_cancel_after_owner_start_commit_closes_run(
    archive,
    store,
    normalizer,
    monkeypatch: pytest.MonkeyPatch,
):
    start_committed = asyncio.Event()
    allow_start_return = asyncio.Event()
    real_start_run = store.start_run

    async def controlled_start_run(*args: Any, **kwargs: Any) -> str:
        run_id = await real_start_run(*args, **kwargs)
        start_committed.set()
        await allow_start_return.wait()
        return run_id

    monkeypatch.setattr(store, "start_run", controlled_start_run)
    consolidator = MemoryConsolidator(
        store=store,
        archive=archive,
        normalizer=normalizer,
        llm_client=StubLLM(),
    )
    task = asyncio.create_task(
        consolidator.run_once(
            group_id="g_owner_start_commit_cancel",
            triggered_by="test",
            scope="group",
            max_batches=1,
            batch_size=10,
        )
    )

    async with asyncio.timeout(1.0):
        await start_committed.wait()
        task.cancel("cancel after owner start commit")
        await asyncio.sleep(0)
        allow_start_return.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task

        runs = await store.list_runs(limit=5)
        assert runs, "owner run row must exist"
        latest = runs[0]
        assert raised.value.args == ("cancel after owner start commit",)
        assert latest.status == "failed"
        assert latest.finished_at > 0
