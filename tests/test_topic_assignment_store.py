from __future__ import annotations

import asyncio
import json
import sqlite3
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest


def _store_type() -> type[Any]:
    try:
        from services.group.topic_assignment_store import TopicAssignmentStore
    except ImportError:
        pytest.fail(
            "missing expected Phase 2 TopicAssignmentStore derived database API",
            pytrace=False,
        )
    return TopicAssignmentStore


def _projection_api() -> tuple[type[Any], type[Any], type[Any], type[Any]]:
    try:
        from services.group.topic_assignment_store import (
            AssignmentRun,
            TopicAssignment,
            TopicBlockIdentity,
            UtteranceMembership,
        )
    except ImportError:
        pytest.fail(
            "missing expected Phase 2 projection record APIs",
            pytrace=False,
        )
    return AssignmentRun, TopicBlockIdentity, TopicAssignment, UtteranceMembership


async def test_store_init_creates_independent_governed_schema_v1(tmp_path: Path) -> None:
    db_path = tmp_path / "research-topic-assignments.db"
    store = _store_type()(db_path)

    await store.init()
    await store.close()

    with sqlite3.connect(db_path) as connection:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert user_version == 1
    assert quick_check == "ok"
    assert {
        "assignment_run",
        "topic_block_identity",
        "topic_assignment",
        "utterance_membership",
    }.issubset(tables)
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


async def test_commit_projection_round_trips_assignment_evidence_and_multi_event_utterance(
    tmp_path: Path,
) -> None:
    run_type, block_type, assignment_type, membership_type = _projection_api()
    store = _store_type()(tmp_path / "research-topic-assignments.db")
    await store.init()

    run = run_type(
        run_uuid="run-1",
        algorithm_version="topic-block-v1-abc123",
        algorithm_config_hash="abc123",
        input_digest="sha256:input-1",
        input_cutoff="2026-07-15T08:00:00+00:00",
        started_at="2026-07-15T08:01:00+00:00",
        completed_at="2026-07-15T08:01:01+00:00",
        status="completed",
        event_count=2,
        assignment_count=2,
    )
    block = block_type(
        block_uuid="65c0922b-f1f8-57dc-a7d4-c3dc2eeb8b2e",
        algorithm_version=run.algorithm_version,
        group_id="group-pseudo-a",
        seed_event_uid="event-1",
        created_at=run.completed_at,
    )
    assignments = (
        assignment_type(
            event_uid="event-1",
            algorithm_version=run.algorithm_version,
            run_uuid=run.run_uuid,
            block_uuid=block.block_uuid,
            assigned_at=run.completed_at,
            reason="new_block",
            score=None,
            runner_up_margin=None,
            reply_predecessor_event_uid=None,
            evidence={"candidate_count": 0},
        ),
        assignment_type(
            event_uid="event-2",
            algorithm_version=run.algorithm_version,
            run_uuid=run.run_uuid,
            block_uuid=block.block_uuid,
            assigned_at=run.completed_at,
            reason="reply_message_active",
            score=1.0,
            runner_up_margin=1.0,
            reply_predecessor_event_uid="event-1",
            evidence={"reply_to_message_id": 1001},
        ),
    )
    memberships = (
        membership_type(
            event_uid="event-1",
            algorithm_version=run.algorithm_version,
            run_uuid=run.run_uuid,
            utterance_uuid="8c351aeb-f46e-59b9-8a3c-d6401e540515",
            ordinal=0,
            assigned_at=run.completed_at,
            reason="same_actor_gap",
        ),
        membership_type(
            event_uid="event-2",
            algorithm_version=run.algorithm_version,
            run_uuid=run.run_uuid,
            utterance_uuid="8c351aeb-f46e-59b9-8a3c-d6401e540515",
            ordinal=1,
            assigned_at=run.completed_at,
            reason="same_actor_gap",
        ),
    )

    try:
        result = await store.commit_projection(
            run=run,
            blocks=(block,),
            assignments=assignments,
            memberships=memberships,
        )
        saved_assignments = await store.list_assignments(run.algorithm_version)
        saved_memberships = await store.list_utterance_members(
            run.algorithm_version,
            memberships[0].utterance_uuid,
        )
    finally:
        await store.close()

    assert result.status == "committed"
    assert result.inserted_assignments == 2
    assert [row.event_uid for row in saved_assignments] == ["event-1", "event-2"]
    assert saved_assignments[1].reply_predecessor_event_uid == "event-1"
    assert saved_assignments[1].evidence == {"reply_to_message_id": 1001}
    assert [row.event_uid for row in saved_memberships] == ["event-1", "event-2"]
    assert [row.ordinal for row in saved_memberships] == [0, 1]


async def test_identical_snapshot_rerun_is_idempotent_and_preserves_first_assignment_time(
    tmp_path: Path,
) -> None:
    run_type, block_type, assignment_type, membership_type = _projection_api()
    store = _store_type()(tmp_path / "research-topic-assignments.db")
    await store.init()
    algorithm_version = "topic-block-v1-abc123"
    block_uuid = "65c0922b-f1f8-57dc-a7d4-c3dc2eeb8b2e"
    utterance_uuid = "8c351aeb-f46e-59b9-8a3c-d6401e540515"

    def projection(run_uuid: str, assigned_at: str) -> tuple[Any, Any, Any, Any]:
        run = run_type(
            run_uuid=run_uuid,
            algorithm_version=algorithm_version,
            algorithm_config_hash="abc123",
            input_digest="sha256:input-1",
            input_cutoff="2026-07-15T08:00:00+00:00",
            started_at=assigned_at,
            completed_at=assigned_at,
            status="completed",
            event_count=1,
            assignment_count=1,
        )
        block = block_type(
            block_uuid=block_uuid,
            algorithm_version=algorithm_version,
            group_id="group-pseudo-a",
            seed_event_uid="event-1",
            created_at=assigned_at,
        )
        assignment = assignment_type(
            event_uid="event-1",
            algorithm_version=algorithm_version,
            run_uuid=run_uuid,
            block_uuid=block_uuid,
            assigned_at=assigned_at,
            reason="new_block",
            score=None,
            runner_up_margin=None,
            reply_predecessor_event_uid=None,
            evidence={"candidate_count": 0},
        )
        membership = membership_type(
            event_uid="event-1",
            algorithm_version=algorithm_version,
            run_uuid=run_uuid,
            utterance_uuid=utterance_uuid,
            ordinal=0,
            assigned_at=assigned_at,
            reason="singleton",
        )
        return run, block, assignment, membership

    first = projection("run-1", "2026-07-15T08:01:00+00:00")
    rerun = projection("run-2", "2026-07-15T09:01:00+00:00")

    try:
        first_result = await store.commit_projection(
            run=first[0], blocks=(first[1],), assignments=(first[2],), memberships=(first[3],)
        )
        rerun_result = await store.commit_projection(
            run=rerun[0], blocks=(rerun[1],), assignments=(rerun[2],), memberships=(rerun[3],)
        )
        saved = await store.list_assignments(algorithm_version)
    finally:
        await store.close()

    assert first_result.status == "committed"
    assert rerun_result.status == "duplicate"
    assert rerun_result.inserted_assignments == 0
    assert len(saved) == 1
    assert saved[0].run_uuid == "run-1"
    assert saved[0].assigned_at == "2026-07-15T08:01:00+00:00"


async def test_expanded_snapshot_reuses_verified_history_and_inserts_only_new_event(
    tmp_path: Path,
) -> None:
    run_type, block_type, assignment_type, membership_type = _projection_api()
    store = _store_type()(tmp_path / "research-topic-assignments.db")
    await store.init()
    version = "topic-block-v1-abc123"
    block_uuid = "65c0922b-f1f8-57dc-a7d4-c3dc2eeb8b2e"
    utterance_uuid = "8c351aeb-f46e-59b9-8a3c-d6401e540515"

    first_run = run_type(
        run_uuid="run-1",
        algorithm_version=version,
        algorithm_config_hash="abc123",
        input_digest="sha256:input-1",
        input_cutoff="2026-07-15T08:00:00+00:00",
        started_at="2026-07-15T08:01:00+00:00",
        completed_at="2026-07-15T08:01:00+00:00",
        status="completed",
        event_count=1,
        assignment_count=1,
    )
    expanded_run = run_type(
        run_uuid="run-2",
        algorithm_version=version,
        algorithm_config_hash="abc123",
        input_digest="sha256:input-2",
        input_cutoff="2026-07-15T08:02:00+00:00",
        started_at="2026-07-15T08:03:00+00:00",
        completed_at="2026-07-15T08:03:00+00:00",
        status="completed",
        event_count=2,
        assignment_count=2,
    )
    first_block = block_type(
        block_uuid=block_uuid,
        algorithm_version=version,
        group_id="group-pseudo-a",
        seed_event_uid="event-1",
        created_at=first_run.completed_at,
    )

    def assignment(event_uid: str, run_uuid: str, assigned_at: str, reason: str) -> Any:
        return assignment_type(
            event_uid=event_uid,
            algorithm_version=version,
            run_uuid=run_uuid,
            block_uuid=block_uuid,
            assigned_at=assigned_at,
            reason=reason,
            score=None if event_uid == "event-1" else 1.0,
            runner_up_margin=None if event_uid == "event-1" else 1.0,
            reply_predecessor_event_uid=None if event_uid == "event-1" else "event-1",
            evidence={"candidate_count": 0} if event_uid == "event-1" else {"reply": True},
        )

    def membership(event_uid: str, run_uuid: str, assigned_at: str, ordinal: int) -> Any:
        return membership_type(
            event_uid=event_uid,
            algorithm_version=version,
            run_uuid=run_uuid,
            utterance_uuid=utterance_uuid,
            ordinal=ordinal,
            assigned_at=assigned_at,
            reason="same_actor_gap",
        )

    try:
        await store.commit_projection(
            run=first_run,
            blocks=(first_block,),
            assignments=(assignment("event-1", "run-1", first_run.completed_at, "new_block"),),
            memberships=(membership("event-1", "run-1", first_run.completed_at, 0),),
        )
        result = await store.commit_projection(
            run=expanded_run,
            blocks=(
                block_type(
                    block_uuid=block_uuid,
                    algorithm_version=version,
                    group_id="group-pseudo-a",
                    seed_event_uid="event-1",
                    created_at=expanded_run.completed_at,
                ),
            ),
            assignments=(
                assignment("event-1", "run-2", expanded_run.completed_at, "new_block"),
                assignment("event-2", "run-2", expanded_run.completed_at, "reply_message_active"),
            ),
            memberships=(
                membership("event-1", "run-2", expanded_run.completed_at, 0),
                membership("event-2", "run-2", expanded_run.completed_at, 1),
            ),
        )
        saved = await store.list_assignments(version)
    finally:
        await store.close()

    assert result.status == "committed"
    assert result.inserted_assignments == 1
    assert result.inserted_memberships == 1
    assert [(row.event_uid, row.run_uuid) for row in saved] == [
        ("event-1", "run-1"),
        ("event-2", "run-2"),
    ]


async def test_conflicting_same_version_history_fails_closed_and_rolls_back_new_run(
    tmp_path: Path,
) -> None:
    from services.group.topic_assignment_store import ProjectionConflictError

    run_type, block_type, assignment_type, membership_type = _projection_api()
    db_path = tmp_path / "research-topic-assignments.db"
    store = _store_type()(db_path)
    await store.init()
    version = "topic-block-v1-abc123"
    first_run = run_type(
        run_uuid="run-1",
        algorithm_version=version,
        algorithm_config_hash="abc123",
        input_digest="sha256:input-1",
        input_cutoff="cutoff-1",
        started_at="time-1",
        completed_at="time-1",
        status="completed",
        event_count=1,
        assignment_count=1,
    )
    block = block_type(
        block_uuid="65c0922b-f1f8-57dc-a7d4-c3dc2eeb8b2e",
        algorithm_version=version,
        group_id="group-a",
        seed_event_uid="event-1",
        created_at="time-1",
    )
    assignment = assignment_type(
        event_uid="event-1",
        algorithm_version=version,
        run_uuid="run-1",
        block_uuid=block.block_uuid,
        assigned_at="time-1",
        reason="new_block",
        score=None,
        runner_up_margin=None,
        reply_predecessor_event_uid=None,
        evidence={"candidate_count": 0},
    )
    membership = membership_type(
        event_uid="event-1",
        algorithm_version=version,
        run_uuid="run-1",
        utterance_uuid="8c351aeb-f46e-59b9-8a3c-d6401e540515",
        ordinal=0,
        assigned_at="time-1",
        reason="utterance_seed",
    )

    try:
        await store.commit_projection(
            run=first_run,
            blocks=(block,),
            assignments=(assignment,),
            memberships=(membership,),
        )
        conflicting_run = replace(
            first_run,
            run_uuid="run-2",
            input_digest="sha256:input-2",
            input_cutoff="cutoff-2",
            started_at="time-2",
            completed_at="time-2",
        )
        with pytest.raises(ProjectionConflictError):
            await store.commit_projection(
                run=conflicting_run,
                blocks=(replace(block, created_at="time-2"),),
                assignments=(
                    replace(
                        assignment,
                        run_uuid="run-2",
                        assigned_at="time-2",
                        evidence={"candidate_count": 99},
                    ),
                ),
                memberships=(replace(membership, run_uuid="run-2", assigned_at="time-2"),),
            )
    finally:
        await store.close()

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM assignment_run").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM topic_assignment").fetchone()[0] == 1
        evidence_json = connection.execute(
            "SELECT evidence_json FROM topic_assignment WHERE event_uid='event-1'"
        ).fetchone()[0]
    assert json.loads(evidence_json) == {"candidate_count": 0}


async def test_store_rejects_future_schema_without_downgrade_or_table_loss(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "research-topic-assignments.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE future_marker (value TEXT)")
        connection.execute("INSERT INTO future_marker VALUES ('preserve')")
        connection.execute("PRAGMA user_version=2")
        connection.commit()

    store = _store_type()(db_path)
    with pytest.raises((ValueError, RuntimeError)):
        await store.init()

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT value FROM future_marker").fetchone()[0] == "preserve"


async def test_commit_rejects_inconsistent_completed_batch_without_partial_rows(
    tmp_path: Path,
) -> None:
    run_type, block_type, assignment_type, membership_type = _projection_api()
    db_path = tmp_path / "research-topic-assignments.db"
    store = _store_type()(db_path)
    await store.init()
    run = run_type(
        run_uuid="run-bad",
        algorithm_version="topic-block-v1-bad",
        algorithm_config_hash="bad",
        input_digest="sha256:bad",
        input_cutoff="cutoff",
        started_at="time",
        completed_at="time",
        status="completed",
        event_count=2,
        assignment_count=1,
    )
    block = block_type(
        block_uuid="65c0922b-f1f8-57dc-a7d4-c3dc2eeb8b2e",
        algorithm_version=run.algorithm_version,
        group_id="group-a",
        seed_event_uid="event-1",
        created_at="time",
    )
    assignment = assignment_type(
        event_uid="event-1",
        algorithm_version=run.algorithm_version,
        run_uuid=run.run_uuid,
        block_uuid=block.block_uuid,
        assigned_at="time",
        reason="new_block",
        score=None,
        runner_up_margin=None,
        reply_predecessor_event_uid=None,
        evidence={},
    )
    membership = membership_type(
        event_uid="event-1",
        algorithm_version=run.algorithm_version,
        run_uuid=run.run_uuid,
        utterance_uuid="8c351aeb-f46e-59b9-8a3c-d6401e540515",
        ordinal=0,
        assigned_at="time",
        reason="utterance_seed",
    )

    try:
        with pytest.raises(ValueError, match="event_count"):
            await store.commit_projection(
                run=run,
                blocks=(block,),
                assignments=(assignment,),
                memberships=(membership,),
            )
    finally:
        await store.close()

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM assignment_run").fetchone()[0] == 0


async def test_double_cancel_cannot_leave_projection_transaction_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiosqlite

    from services.group import topic_assignment_store as store_module

    run_type, block_type, assignment_type, membership_type = _projection_api()
    store = _store_type()(tmp_path / "research-topic-assignments.db")
    await store.init()
    run = run_type(
        run_uuid="run-cancel",
        algorithm_version="topic-block-v1-cancel",
        algorithm_config_hash="cancel",
        input_digest="sha256:cancel",
        input_cutoff="cutoff",
        started_at="time",
        completed_at="time",
        status="completed",
        event_count=1,
        assignment_count=1,
    )
    block = block_type(
        block_uuid="65c0922b-f1f8-57dc-a7d4-c3dc2eeb8b2e",
        algorithm_version=run.algorithm_version,
        group_id="group-a",
        seed_event_uid="event-1",
        created_at="time",
    )
    assignment = assignment_type(
        event_uid="event-1",
        algorithm_version=run.algorithm_version,
        run_uuid=run.run_uuid,
        block_uuid=block.block_uuid,
        assigned_at="time",
        reason="new_block",
        score=None,
        runner_up_margin=None,
        reply_predecessor_event_uid=None,
        evidence={},
    )
    membership = membership_type(
        event_uid="event-1",
        algorithm_version=run.algorithm_version,
        run_uuid=run.run_uuid,
        utterance_uuid="8c351aeb-f46e-59b9-8a3c-d6401e540515",
        ordinal=0,
        assigned_at="time",
        reason="utterance_seed",
    )
    inserted = asyncio.Event()
    hold = asyncio.Event()
    rollback_started = asyncio.Event()
    original_ensure = store_module._ensure_assignment
    original_rollback = aiosqlite.Connection.rollback

    async def slow_ensure(db: Any, candidate: Any) -> bool:
        result = await original_ensure(db, candidate)
        inserted.set()
        await hold.wait()
        return result

    async def slow_rollback(db: Any) -> None:
        rollback_started.set()
        await hold.wait()
        await original_rollback(db)

    monkeypatch.setattr(store_module, "_ensure_assignment", slow_ensure)
    monkeypatch.setattr(aiosqlite.Connection, "rollback", slow_rollback)

    try:
        task = asyncio.create_task(
            store.commit_projection(
                run=run,
                blocks=(block,),
                assignments=(assignment,),
                memberships=(membership,),
            )
        )
        await inserted.wait()
        task.cancel()
        await rollback_started.wait()
        task.cancel()
        hold.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        result = await store.commit_projection(
            run=run,
            blocks=(block,),
            assignments=(assignment,),
            memberships=(membership,),
        )
    finally:
        hold.set()
        await store.close()

    assert result.status == "committed"
