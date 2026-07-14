"""Migration governance contracts for the first managed SQLite stores."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from services.block_trace.store import BlockTraceStore
from services.episodic.store import EpisodeStore
from services.llm.usage import UsageTracker


def _user_version(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("PRAGMA user_version").fetchone()
    assert row is not None
    return int(row[0])


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row[0]) for row in rows}


def _ledger_entries(db_path: Path) -> list[tuple[str, int, int]]:
    if "_omubot_schema_migrations" not in _tables(db_path):
        return []
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT db_id, version, adopted
            FROM _omubot_schema_migrations
            ORDER BY version
            """
        ).fetchall()
    return [(str(db_id), int(version), int(adopted)) for db_id, version, adopted in rows]


def _demote_to_legacy(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE IF EXISTS _omubot_schema_migrations")
        connection.execute("PRAGMA user_version=0")


async def _record_usage(tracker: UsageTracker, *, user_id: str = "governed-user") -> None:
    await tracker.record(
        call_type="chat",
        user_id=user_id,
        group_id="governed-group",
        model="test-model",
        provider_kind="test-provider",
        input_tokens=11,
        cache_read_tokens=2,
        cache_create_tokens=3,
        output_tokens=5,
        prompt_cache_hit_tokens=2,
        prompt_cache_miss_tokens=9,
        reasoning_replay_tokens=0,
        tool_rounds=0,
        elapsed_s=0.1,
    )


@pytest.mark.parametrize(
    ("store_type", "db_id"),
    (
        (BlockTraceStore, "block_trace"),
        (UsageTracker, "usage"),
        (EpisodeStore, "episodic"),
    ),
)
@pytest.mark.asyncio
async def test_fresh_store_init_records_versioned_migration(
    tmp_path: Path,
    store_type: type[Any],
    db_id: str,
) -> None:
    db_path = tmp_path / f"{db_id}.db"
    store = store_type(str(db_path))
    try:
        await store.init()
    finally:
        await store.close()

    assert _user_version(db_path) == 1
    assert _ledger_entries(db_path) == [(db_id, 1, 0)]


@pytest.mark.asyncio
async def test_block_trace_reopen_preserves_one_ledger_row_and_data(tmp_path: Path) -> None:
    db_path = tmp_path / "block_trace.db"
    store = BlockTraceStore(str(db_path))
    await store.init()
    metric_id = await store.record_humanization_metrics(
        request_id="governed-request",
        group_id="governed-group",
        score={"score": 0.75, "axes": {"content": 0.8}, "issues": []},
    )
    await store.close()

    reopened = BlockTraceStore(str(db_path))
    await reopened.init()
    try:
        rows = await reopened.list_humanization_metrics(request_id="governed-request")
    finally:
        await reopened.close()

    assert [row["metric_id"] for row in rows] == [metric_id]
    assert _ledger_entries(db_path) == [("block_trace", 1, 0)]


@pytest.mark.asyncio
async def test_block_trace_rejects_legacy_schema_missing_required_column(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "block_trace_missing_column.db"
    store = BlockTraceStore(str(db_path))
    await store.init()
    await store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("ALTER TABLE prompt_block_traces DROP COLUMN budget_reason")
    _demote_to_legacy(db_path)

    malformed = BlockTraceStore(str(db_path))
    try:
        with pytest.raises(ValueError, match="legacy schema fingerprint mismatch"):
            await malformed.init()
    finally:
        await malformed.close()

    assert _user_version(db_path) == 0
    assert _ledger_entries(db_path) == []


@pytest.mark.asyncio
async def test_block_trace_rejects_legacy_schema_missing_required_index(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "block_trace_missing_index.db"
    store = BlockTraceStore(str(db_path))
    await store.init()
    await store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP INDEX idx_rme_group_key")
    _demote_to_legacy(db_path)

    malformed = BlockTraceStore(str(db_path))
    try:
        with pytest.raises(ValueError, match="legacy schema fingerprint mismatch"):
            await malformed.init()
    finally:
        await malformed.close()

    assert _user_version(db_path) == 0
    assert _ledger_entries(db_path) == []


@pytest.mark.asyncio
async def test_block_trace_rejects_legacy_schema_without_column_constraints(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "block_trace_missing_constraints.db"
    store = BlockTraceStore(str(db_path))
    await store.init()
    await store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "ALTER TABLE prompt_block_traces RENAME TO malformed_prompt_block_traces"
        )
        connection.execute(
            "CREATE TABLE prompt_block_traces AS "
            "SELECT * FROM malformed_prompt_block_traces WHERE 0"
        )
        connection.execute("DROP TABLE malformed_prompt_block_traces")
        connection.execute(
            "CREATE INDEX idx_bt_request ON prompt_block_traces(request_id)"
        )
        connection.execute(
            "CREATE INDEX idx_bt_source "
            "ON prompt_block_traces(source, candidate_id)"
        )
        connection.execute(
            "CREATE INDEX idx_bt_created ON prompt_block_traces(created_at)"
        )
    _demote_to_legacy(db_path)

    malformed = BlockTraceStore(str(db_path))
    try:
        with pytest.raises(ValueError, match="legacy schema fingerprint mismatch"):
            await malformed.init()
    finally:
        await malformed.close()

    assert _user_version(db_path) == 0
    assert _ledger_entries(db_path) == []


@pytest.mark.asyncio
async def test_usage_reopen_preserves_one_ledger_row_and_data(tmp_path: Path) -> None:
    db_path = tmp_path / "usage.db"
    tracker = UsageTracker(str(db_path))
    await tracker.init()
    await _record_usage(tracker)
    await tracker.close()

    reopened = UsageTracker(str(db_path))
    await reopened.init()
    try:
        rows = await reopened.query_raw(
            "SELECT user_id, input_tokens, output_tokens FROM llm_calls"
        )
    finally:
        await reopened.close()

    assert [(row["user_id"], row["input_tokens"], row["output_tokens"]) for row in rows] == [
        ("governed-user", 11, 5)
    ]
    assert _ledger_entries(db_path) == [("usage", 1, 0)]


@pytest.mark.asyncio
async def test_episode_reopen_preserves_one_ledger_row_and_data(tmp_path: Path) -> None:
    db_path = tmp_path / "episodic.db"
    store = EpisodeStore(str(db_path))
    await store.init()
    episode = await store.create_episode(
        situation="governed migration",
        group_id="governed-group",
    )
    await store.close()

    reopened = EpisodeStore(str(db_path))
    await reopened.init()
    try:
        restored = await reopened.get_episode(episode.episode_id)
    finally:
        await reopened.close()

    assert restored is not None
    assert restored.situation == "governed migration"
    assert _ledger_entries(db_path) == [("episodic", 1, 0)]


@pytest.mark.parametrize(
    ("table", "column"),
    (
        ("episodes", "reflection"),
        ("episode_revisions", "reason"),
        ("episode_observations", "meta"),
    ),
)
@pytest.mark.asyncio
async def test_episodic_rejects_legacy_schema_missing_required_column(
    tmp_path: Path,
    table: str,
    column: str,
) -> None:
    db_path = tmp_path / f"episodic_missing_{table}_{column}.db"
    store = EpisodeStore(str(db_path))
    await store.init()
    await store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    _demote_to_legacy(db_path)

    malformed = EpisodeStore(str(db_path))
    try:
        with pytest.raises(ValueError, match="legacy schema fingerprint mismatch"):
            await malformed.init()
    finally:
        await malformed.close()

    assert _user_version(db_path) == 0
    assert _ledger_entries(db_path) == []


@pytest.mark.asyncio
async def test_episodic_rejects_legacy_schema_missing_required_index(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "episodic_missing_index.db"
    store = EpisodeStore(str(db_path))
    await store.init()
    await store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP INDEX idx_episode_obs_scope")
    _demote_to_legacy(db_path)

    malformed = EpisodeStore(str(db_path))
    try:
        with pytest.raises(ValueError, match="legacy schema fingerprint mismatch"):
            await malformed.init()
    finally:
        await malformed.close()

    assert _user_version(db_path) == 0
    assert _ledger_entries(db_path) == []


@pytest.mark.asyncio
async def test_usage_adopts_verified_legacy_schema_and_preserves_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_usage.db"
    legacy = UsageTracker(str(db_path))
    await legacy.init()
    await _record_usage(legacy, user_id="legacy-user")
    await legacy.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE IF EXISTS _omubot_schema_migrations")
        connection.execute("PRAGMA user_version=0")

    governed = UsageTracker(str(db_path))
    await governed.init()
    try:
        rows = await governed.query_raw("SELECT user_id FROM llm_calls")
    finally:
        await governed.close()

    assert [row["user_id"] for row in rows] == ["legacy-user"]
    assert _user_version(db_path) == 1
    assert _ledger_entries(db_path) == [("usage", 1, 1)]


@pytest.mark.asyncio
async def test_usage_rejects_legacy_schema_missing_required_index(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "usage_missing_index.db"
    tracker = UsageTracker(str(db_path))
    await tracker.init()
    await tracker.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP INDEX idx_llm_calls_group")
    _demote_to_legacy(db_path)

    malformed = UsageTracker(str(db_path))
    try:
        with pytest.raises(ValueError, match="legacy schema fingerprint mismatch"):
            await malformed.init()
    finally:
        await malformed.close()

    assert _user_version(db_path) == 0
    assert _ledger_entries(db_path) == []


@pytest.mark.asyncio
async def test_usage_rejects_partial_legacy_schema_without_writing(tmp_path: Path) -> None:
    db_path = tmp_path / "partial_usage.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE wrong_usage_schema (id INTEGER PRIMARY KEY)")

    tracker = UsageTracker(str(db_path))
    try:
        with pytest.raises(ValueError):
            await tracker.init()
    finally:
        await tracker.close()

    assert "wrong_usage_schema" in _tables(db_path)
    assert "llm_calls" not in _tables(db_path)
    assert _user_version(db_path) == 0
    assert _ledger_entries(db_path) == []
