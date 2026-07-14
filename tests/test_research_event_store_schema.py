"""Schema and file-hardening contract for the raw research event store."""

from __future__ import annotations

import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


def _load_api() -> tuple[type[Any], type[Any]]:
    try:
        from services.group.research_event_store import ResearchEvent, ResearchEventStore
    except ImportError as exc:
        pytest.fail(
            "missing expected ResearchEvent and ResearchEventStore schema API",
            pytrace=False,
        )
        raise AssertionError("unreachable") from exc
    return ResearchEvent, ResearchEventStore


async def _init_store(db_path: Path) -> Any:
    _, store_type = _load_api()
    store = store_type(str(db_path))
    await store.init()
    return store


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _create_compatible_future_version_database(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
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
        )
        connection.execute("PRAGMA user_version=2")
        connection.commit()
    finally:
        connection.close()


def _journal_mode(db_path: Path) -> str:
    connection = _connect_read_only(db_path)
    try:
        row = connection.execute("PRAGMA journal_mode").fetchone()
    finally:
        connection.close()
    assert row is not None
    return str(row[0])


async def test_init_creates_owner_read_write_only_database(tmp_path: Path) -> None:
    db_path = tmp_path / "research-events.db"
    store = await _init_store(db_path)
    await store.close()

    mode = stat.S_IMODE(db_path.stat().st_mode)
    assert mode == 0o600, f"expected database mode 0600, got {mode:04o}"


async def test_init_sets_schema_metadata_version_one(tmp_path: Path) -> None:
    db_path = tmp_path / "research-events.db"
    store = await _init_store(db_path)
    await store.close()

    connection = _connect_read_only(db_path)
    try:
        (schema_version,) = connection.execute("PRAGMA user_version").fetchone()
    finally:
        connection.close()
    assert schema_version == 1


async def test_init_reopens_existing_version_one_database(tmp_path: Path) -> None:
    db_path = tmp_path / "research-events.db"
    store = await _init_store(db_path)
    await store.close()

    reopened = await _init_store(db_path)
    await reopened.close()

    connection = _connect_read_only(db_path)
    try:
        (schema_version,) = connection.execute("PRAGMA user_version").fetchone()
        table_names = {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        connection.close()

    assert schema_version == 1
    assert "research_message_event" in table_names


async def test_init_rejects_future_schema_without_downgrade_or_table_loss(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "research-events.db"
    _create_compatible_future_version_database(db_path)
    assert _journal_mode(db_path) == "delete"
    _, store_type = _load_api()
    store = store_type(str(db_path))
    init_error: Exception | None = None

    try:
        await store.init()
    except Exception as exc:
        init_error = exc
    finally:
        await store.close()

    connection = _connect_read_only(db_path)
    try:
        (schema_version,) = connection.execute("PRAGMA user_version").fetchone()
        table_names = {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        connection.close()

    table_exists = "research_message_event" in table_names
    assert (
        isinstance(init_error, (ValueError, RuntimeError))
        and schema_version == 2
        and table_exists
    ), (
        "future schema must fail closed without mutation; "
        f"error={init_error!r}, user_version={schema_version}, "
        f"table_exists={table_exists}"
    )
    error_message = str(init_error).lower()
    assert "version" in error_message
    assert "newer" in error_message or "future" in error_message
    assert _journal_mode(db_path) == "delete"


async def test_init_creates_fixed_raw_event_table_name(tmp_path: Path) -> None:
    db_path = tmp_path / "research-events.db"
    store = await _init_store(db_path)
    await store.close()

    connection = _connect_read_only(db_path)
    try:
        table_names = {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        connection.close()
    assert "research_message_event" in table_names


async def test_closed_database_passes_sqlite_quick_check(tmp_path: Path) -> None:
    db_path = tmp_path / "research-events.db"
    store = await _init_store(db_path)
    await store.close()

    connection = _connect_read_only(db_path)
    try:
        results = [row[0] for row in connection.execute("PRAGMA quick_check")]
    finally:
        connection.close()
    assert results == ["ok"]


async def test_message_id_none_round_trips_for_degraded_platform_event(tmp_path: Path) -> None:
    event_type, _ = _load_api()
    event = event_type(
        event_uid="onebot:group:g-42:degraded:no-message-id",
        run_id="run-20260712-schema",
        event_time=datetime(2026, 7, 12, 11, 30, 15, tzinfo=UTC),
        direction="outbound",
        actor_type="ai",
        actor_id="actor-bot-self",
        source="live",
        group_id="g-42",
        message_id=None,
        reply_to_message_id=1001,
        at_user_ids=(),
        text="平台发送成功但没有返回 message_id",
        content_type="text",
    )
    store = await _init_store(tmp_path / "research-events.db")

    try:
        result = await store.append(event)
        saved = await store.get_event(event.event_uid)
        assert result.status == "persisted", "message_id=None must remain persistable"
        assert saved is not None
        assert saved.message_id is None
    finally:
        await store.close()
