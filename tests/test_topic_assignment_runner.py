from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

_RAW_SCHEMA = """
CREATE TABLE research_message_event (
    event_uid TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_time TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    direction TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    source TEXT NOT NULL,
    group_id TEXT NOT NULL,
    message_id INTEGER,
    reply_to_message_id INTEGER,
    at_user_ids TEXT NOT NULL,
    text TEXT,
    content_type TEXT NOT NULL
)
"""


def _runner_type() -> type[Any]:
    try:
        from services.group.topic_assignment_runner import TopicAssignmentRunner
    except ImportError:
        pytest.fail(
            "missing expected Phase 2 offline TopicAssignmentRunner",
            pytrace=False,
        )
    return TopicAssignmentRunner


def _write_raw_db(path: Path, insertion_order: tuple[int, ...]) -> None:
    events = (
        (
            "event-1", "run-a", "2026-07-15T08:00:00+00:00",
            "2026-07-15T08:00:00.100000+00:00", "inbound", "human",
            "actor-u1", "live", "group-a", 101, None, "[]", "alpha", "text",
        ),
        (
            "event-2", "run-a", "2026-07-15T08:00:01+00:00",
            "2026-07-15T08:00:01.100000+00:00", "inbound", "human",
            "actor-u1", "live", "group-a", 102, None, "[]", "alpha more", "text",
        ),
        (
            "event-3", "run-a", "2026-07-15T08:00:02+00:00",
            "2026-07-15T08:00:02.100000+00:00", "inbound", "human",
            "actor-u2", "live", "group-a", 103, 101, "[]", "reply", "text",
        ),
    )
    with sqlite3.connect(path) as connection:
        connection.execute(_RAW_SCHEMA)
        for index in insertion_order:
            connection.execute(
                """
                INSERT INTO research_message_event VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                events[index],
            )
        connection.execute("PRAGMA user_version=1")
        connection.commit()


def _derived_snapshot(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        assignments = list(
            connection.execute(
                """
                SELECT event_uid, algorithm_version, block_uuid, reason, score,
                       runner_up_margin, reply_predecessor_event_uid, evidence_json
                FROM topic_assignment
                ORDER BY event_uid
                """
            )
        )
        memberships = list(
            connection.execute(
                """
                SELECT event_uid, algorithm_version, utterance_uuid, ordinal, reason
                FROM utterance_membership
                ORDER BY event_uid
                """
            )
        )
    return {"assignments": assignments, "memberships": memberships}


async def test_runner_is_deterministic_across_raw_physical_order_and_never_mutates_raw(
    tmp_path: Path,
) -> None:
    raw_a = tmp_path / "raw-a.db"
    raw_b = tmp_path / "raw-b.db"
    derived_a = tmp_path / "derived-a.db"
    derived_b = tmp_path / "derived-b.db"
    _write_raw_db(raw_a, (0, 1, 2))
    _write_raw_db(raw_b, (2, 0, 1))
    runner_type = _runner_type()

    result_a = await runner_type(
        raw_db_path=raw_a,
        derived_db_path=derived_a,
        utterance_gap_seconds=2.5,
    ).run()
    result_b = await runner_type(
        raw_db_path=raw_b,
        derived_db_path=derived_b,
        utterance_gap_seconds=2.5,
    ).run()

    snapshot_a = _derived_snapshot(derived_a)
    snapshot_b = _derived_snapshot(derived_b)
    with sqlite3.connect(f"file:{raw_a}?mode=ro", uri=True) as connection:
        raw_state = (
            int(connection.execute("PRAGMA user_version").fetchone()[0]),
            int(connection.execute("SELECT COUNT(*) FROM research_message_event").fetchone()[0]),
        )

    assert result_a.algorithm_version == result_b.algorithm_version
    assert result_a.input_digest == result_b.input_digest
    assert snapshot_a == snapshot_b
    assert raw_state == (1, 3)
    assert snapshot_a["assignments"][2][3] == "reply_message_active"
    assert snapshot_a["assignments"][2][6] == "event-1"
    assert json.loads(snapshot_a["assignments"][2][7])["reply_to_message_id"] == 101
    assert snapshot_a["memberships"][0][2] == snapshot_a["memberships"][1][2]
    assert snapshot_a["memberships"][2][2] != snapshot_a["memberships"][0][2]


async def test_runner_rerun_is_idempotent_and_parameter_change_creates_new_version(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw.db"
    derived = tmp_path / "derived.db"
    _write_raw_db(raw, (0, 1, 2))
    runner_type = _runner_type()

    first = await runner_type(
        raw_db_path=raw,
        derived_db_path=derived,
        utterance_gap_seconds=2.5,
    ).run()
    duplicate = await runner_type(
        raw_db_path=raw,
        derived_db_path=derived,
        utterance_gap_seconds=2.5,
    ).run()
    changed = await runner_type(
        raw_db_path=raw,
        derived_db_path=derived,
        utterance_gap_seconds=0.5,
    ).run()

    with sqlite3.connect(f"file:{derived}?mode=ro", uri=True) as connection:
        versions = list(
            connection.execute(
                """
                SELECT algorithm_version, COUNT(*)
                FROM topic_assignment
                GROUP BY algorithm_version
                ORDER BY algorithm_version
                """
            )
        )

    assert first.status == "committed"
    assert duplicate.status == "duplicate"
    assert duplicate.inserted_assignments == 0
    assert changed.status == "committed"
    assert changed.algorithm_version != first.algorithm_version
    assert [count for _, count in versions] == [3, 3]


async def test_runner_reply_lookup_never_uses_future_reused_message_id(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw.db"
    derived = tmp_path / "derived.db"
    events = (
        (
            "event-1", "run-a", "2026-07-15T08:00:00+00:00",
            "2026-07-15T08:00:00.100000+00:00", "inbound", "human",
            "actor-u1", "live", "group-a", 500, None, "[]", "alpha", "text",
        ),
        (
            "event-2", "run-a", "2026-07-15T08:00:01+00:00",
            "2026-07-15T08:00:01.100000+00:00", "inbound", "human",
            "actor-u2", "live", "group-a", 501, 500, "[]", "reply", "text",
        ),
        (
            "event-3", "run-a", "2026-07-15T08:00:02+00:00",
            "2026-07-15T08:00:02.100000+00:00", "inbound", "human",
            "actor-u3", "live", "group-a", 500, None, "[]", "future reuse", "text",
        ),
    )
    with sqlite3.connect(raw) as connection:
        connection.execute(_RAW_SCHEMA)
        connection.executemany(
            "INSERT INTO research_message_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            events,
        )
        connection.execute("PRAGMA user_version=1")
        connection.commit()

    result = await _runner_type()(
        raw_db_path=raw,
        derived_db_path=derived,
    ).run()

    with sqlite3.connect(f"file:{derived}?mode=ro", uri=True) as connection:
        reply = connection.execute(
            """
            SELECT reason, reply_predecessor_event_uid
            FROM topic_assignment
            WHERE event_uid = ? AND algorithm_version = ?
            """,
            ("event-2", result.algorithm_version),
        ).fetchone()

    assert reply == ("reply_message_active", "event-1")


async def test_runner_cutoff_compares_iso_timestamps_as_instants(tmp_path: Path) -> None:
    raw = tmp_path / "raw.db"
    derived = tmp_path / "derived.db"
    events = (
        (
            "included", "run-a", "2026-07-15T15:59:59+08:00",
            "2026-07-15T15:59:59+08:00", "inbound", "human",
            "actor-u1", "live", "group-a", 601, None, "[]", "before", "text",
        ),
        (
            "excluded", "run-a", "2026-07-15T08:30:00+00:00",
            "2026-07-15T08:30:00+00:00", "inbound", "human",
            "actor-u2", "live", "group-a", 602, None, "[]", "after", "text",
        ),
    )
    with sqlite3.connect(raw) as connection:
        connection.execute(_RAW_SCHEMA)
        connection.executemany(
            "INSERT INTO research_message_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            events,
        )
        connection.execute("PRAGMA user_version=1")
        connection.commit()

    result = await _runner_type()(
        raw_db_path=raw,
        derived_db_path=derived,
        input_cutoff="2026-07-15T16:00:00+08:00",
    ).run()

    with sqlite3.connect(f"file:{derived}?mode=ro", uri=True) as connection:
        event_uids = list(
            row[0]
            for row in connection.execute(
                "SELECT event_uid FROM topic_assignment ORDER BY event_uid"
            )
        )

    assert result.event_count == 1
    assert event_uids == ["included"]


async def test_runner_rejects_raw_and_derived_alias_before_touching_raw(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw.db"
    _write_raw_db(raw, (0, 1, 2))
    with sqlite3.connect(raw) as connection:
        before = (
            connection.execute("PRAGMA journal_mode").fetchone()[0],
            connection.execute("PRAGMA user_version").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM research_message_event").fetchone()[0],
        )

    with pytest.raises(ValueError, match="different SQLite files"):
        await _runner_type()(
            raw_db_path=raw,
            derived_db_path=raw,
        ).run()

    with sqlite3.connect(raw) as connection:
        after = (
            connection.execute("PRAGMA journal_mode").fetchone()[0],
            connection.execute("PRAGMA user_version").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM research_message_event").fetchone()[0],
        )

    assert after == before


async def test_runner_treats_equivalent_snapshot_from_later_cutoff_as_duplicate(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw.db"
    derived = tmp_path / "derived.db"
    _write_raw_db(raw, (0,))
    runner_type = _runner_type()

    first = await runner_type(raw_db_path=raw, derived_db_path=derived).run()
    duplicate = await runner_type(
        raw_db_path=raw,
        derived_db_path=derived,
        input_cutoff="2026-07-15T09:00:00+00:00",
    ).run()

    assert duplicate.input_digest == first.input_digest
    assert duplicate.run_uuid == first.run_uuid
    assert duplicate.status == "duplicate"


async def test_runner_records_completion_after_projection_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.group import topic_assignment_runner as runner_module

    raw = tmp_path / "raw.db"
    derived = tmp_path / "derived.db"
    _write_raw_db(raw, (0,))
    original_project = runner_module._project_events
    project_finished_at: datetime | None = None

    def slow_project(*args: Any, **kwargs: Any) -> Any:
        nonlocal project_finished_at
        result = original_project(*args, **kwargs)
        time.sleep(0.02)
        project_finished_at = datetime.now(UTC)
        return result

    monkeypatch.setattr(runner_module, "_project_events", slow_project)

    result = await _runner_type()(raw_db_path=raw, derived_db_path=derived).run()

    with sqlite3.connect(f"file:{derived}?mode=ro", uri=True) as connection:
        completed_at = datetime.fromisoformat(
            connection.execute(
                "SELECT completed_at FROM assignment_run WHERE run_uuid = ?",
                (result.run_uuid,),
            ).fetchone()[0]
        )

    assert project_finished_at is not None
    assert completed_at >= project_finished_at
