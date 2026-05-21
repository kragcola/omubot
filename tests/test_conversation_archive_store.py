"""Tests for ConversationArchive storage primitives."""

from __future__ import annotations

import time

import aiosqlite
import pytest

from services.conversation_archive import ConversationArchive, sync_business_message_refs


@pytest.fixture
async def archive(tmp_path):
    store = ConversationArchive(db_path=str(tmp_path / "messages.db"))
    await store.init()
    yield store
    await store.close()


async def test_archive_compat_message_log_methods(archive: ConversationArchive) -> None:
    """Existing MessageLog-shaped calls should keep working."""
    first_pk = await archive.record(
        group_id="100",
        role="user",
        speaker="Alice(1)",
        content_text="hello",
        content_json=None,
        message_id=101,
        created_at=1000.0,
    )
    second_pk = await archive.record(
        group_id="100",
        role="assistant",
        speaker=None,
        content_text="hi",
        content_json=None,
        message_id=102,
        created_at=1001.0,
    )
    assert first_pk and second_pk and second_pk > first_pk

    rows = await archive.query_recent("100", limit=10)
    assert [row["content_text"] for row in rows] == ["hello", "hi"]
    assert rows[0]["message_id"] == 101

    compact_rows = await archive.query_for_compact("100", before=1000.5)
    assert [row["content_text"] for row in compact_rows] == ["hello"]
    assert await archive.list_group_ids() == ["100"]

    await archive.record_session_msg("private-1", "user", "secret")
    session_rows = await archive.query_recent("session:private-1", limit=10)
    assert [row["content_text"] for row in session_rows] == ["secret"]
    assert await archive.list_group_ids() == ["100"]


async def test_group_activity_summary_excludes_sessions(archive: ConversationArchive) -> None:
    """Activity summary aggregates per group_id and skips private sessions."""
    base = 10_000.0
    await archive.record(
        group_id="200",
        role="user",
        speaker="Alice(1)",
        content_text="hi",
        content_json=None,
        created_at=base - 1000.0,
    )
    await archive.record(
        group_id="200",
        role="user",
        speaker="Alice(1)",
        content_text="hello",
        content_json=None,
        created_at=base - 100.0,
    )
    await archive.record(
        group_id="200",
        role="assistant",
        speaker=None,
        content_text="hey",
        content_json=None,
        created_at=base - 50.0,
    )
    await archive.record_session_msg("private-2", "user", "ignored")

    summary = await archive.group_activity_summary(since=base - 500.0)
    assert "200" in summary
    assert all(not key.startswith("session:") for key in summary)
    bucket = summary["200"]
    # latest record was 50s before "now"
    assert bucket["last_at"] == pytest.approx(base - 50.0)
    # only 2 messages newer than (base - 500): "hello" + "hey"
    assert bucket["count_window"] == 2
    # one of those two is from a user
    assert bucket["user_count_window"] == 1

    full = await archive.group_activity_summary(since=0)
    assert full["200"]["count_window"] == 3


async def test_archive_backfills_legacy_rows_idempotently(tmp_path) -> None:
    """Existing group_messages rows should be copied once into the archive table."""
    db_path = tmp_path / "messages.db"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE group_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                role TEXT NOT NULL,
                speaker TEXT,
                content_text TEXT,
                content_json TEXT,
                message_id INTEGER,
                created_at REAL NOT NULL
            )"""
        )
        await db.execute(
            """INSERT INTO group_messages
                   (group_id, role, speaker, content_text, content_json, message_id, created_at)
               VALUES ('200', 'user', 'Bob(2)', 'legacy', NULL, 201, 2000.0)"""
        )
        await db.commit()

    archive = ConversationArchive(db_path=str(db_path))
    await archive.init()
    try:
        assert await archive.query_recent("200", limit=10) == [{
            "role": "user",
            "speaker": "Bob(2)",
            "content_text": "legacy",
            "message_id": 201,
            "created_at": 2000.0,
        }]
        assert await archive.backfill_legacy_messages() == 0
        cursor = await archive._db.execute("SELECT COUNT(*) AS n FROM conversation_messages")
        row = await cursor.fetchone()
        assert row["n"] == 1
    finally:
        await archive.close()


async def test_archive_compat_queries_follow_legacy_deletes(
    archive: ConversationArchive,
) -> None:
    """Compatibility reads must honor legacy group_messages cleanup paths."""
    await archive.record(
        group_id="250",
        role="user",
        speaker="Temp(1)",
        content_text="delete me",
        content_json=None,
        message_id=250,
        created_at=2500.0,
    )
    assert len(await archive.query_recent("250", limit=10)) == 1

    await archive._db.execute(
        "DELETE FROM group_messages WHERE group_id = ? AND message_id = ?",
        ("250", 250),
    )
    await archive._db.commit()
    assert await archive.query_recent("250", limit=10) == []

    archived = await archive.list_messages_by_pk_range(
        chat_type="group",
        chat_id="250",
        from_message_pk=0,
        to_message_pk=await archive.max_message_pk(chat_type="group", chat_id="250"),
    )
    assert [row["content_text"] for row in archived] == ["delete me"]


async def test_archive_cursor_window_and_needs_rescan(archive: ConversationArchive) -> None:
    for idx in range(5):
        await archive.record(
            group_id="300",
            role="user",
            speaker=f"U({idx})",
            content_text=f"m{idx}",
            content_json=None,
            message_id=300 + idx,
            created_at=3000.0 + idx,
        )

    await archive.upsert_cursor(
        scanner_name="style_extract",
        chat_type="group",
        chat_id="300",
        last_message_pk=3,
        last_created_at=3002.0,
        scanner_version="v1",
        params_hash="p1",
    )
    window = await archive.prepare_scan_window(
        scanner_name="style_extract",
        chat_type="group",
        chat_id="300",
        scanner_version="v1",
        params_hash="p1",
        backtrack_window=2,
    )
    assert window["from_message_pk"] == 3
    assert window["backtrack_from_message_pk"] == 1
    assert window["to_message_pk"] == 5
    rows = await archive.list_messages_by_pk_range(
        chat_type="group",
        chat_id="300",
        from_message_pk=window["backtrack_from_message_pk"],
        to_message_pk=window["to_message_pk"],
    )
    assert [row["content_text"] for row in rows] == ["m1", "m2", "m3", "m4"]

    changed = await archive.prepare_scan_window(
        scanner_name="style_extract",
        chat_type="group",
        chat_id="300",
        scanner_version="v2",
        params_hash="p1",
    )
    assert changed["needs_rescan"] is True
    assert changed["cursor_status"] == "needs_rescan"


async def test_archive_scan_batch_bootstraps_recent_then_advances(
    archive: ConversationArchive,
) -> None:
    for idx in range(5):
        await archive.record(
            group_id="350",
            role="user",
            speaker=f"U({idx})",
            content_text=f"m{idx}",
            content_json=None,
            message_id=350 + idx,
            created_at=3500.0 + idx,
        )

    first = await archive.read_scan_batch(
        scanner_name="style_manual_extract",
        group_id="350",
        limit=2,
        scanner_version="v1",
    )
    assert first["source"] == "archive"
    assert [row["content_text"] for row in first["rows"]] == ["m3", "m4"]
    await archive.finish_scan_batch(
        first,
        status="success",
        scanned_count=2,
        extracted_count=1,
        saved_count=1,
    )
    cursor = await archive.get_cursor(
        scanner_name="style_manual_extract",
        chat_type="group",
        chat_id="350",
    )
    assert cursor is not None
    assert cursor["last_message_pk"] == first["to_message_pk"]

    await archive.record(
        group_id="350",
        role="user",
        speaker="U(5)",
        content_text="m5",
        content_json=None,
        message_id=355,
        created_at=3505.0,
    )
    second = await archive.read_scan_batch(
        scanner_name="style_manual_extract",
        group_id="350",
        limit=2,
        scanner_version="v1",
    )
    assert [row["content_text"] for row in second["rows"]] == ["m5"]


async def test_archive_scan_batch_handles_sparse_global_message_pk(
    archive: ConversationArchive,
) -> None:
    first_pk = await archive.record(
        group_id="360",
        role="user",
        speaker="A(1)",
        content_text="first",
        content_json=None,
        message_id=360,
        created_at=3600.0,
    )
    assert first_pk is not None
    await archive.upsert_cursor(
        scanner_name="style_manual_extract",
        chat_type="group",
        chat_id="360",
        last_message_pk=first_pk,
        last_created_at=3600.0,
        scanner_version="v1",
    )
    for idx in range(20):
        await archive.record(
            group_id="361",
            role="user",
            speaker=f"B({idx})",
            content_text=f"other-{idx}",
            content_json=None,
            message_id=3610 + idx,
            created_at=3610.0 + idx,
        )
    next_pk = await archive.record(
        group_id="360",
        role="user",
        speaker="A(2)",
        content_text="next",
        content_json=None,
        message_id=362,
        created_at=3620.0,
    )
    assert next_pk is not None
    assert next_pk > first_pk + 20

    batch = await archive.read_scan_batch(
        scanner_name="style_manual_extract",
        group_id="360",
        limit=2,
        scanner_version="v1",
    )
    assert batch["from_message_pk"] == first_pk
    assert batch["to_message_pk"] == next_pk
    assert [row["content_text"] for row in batch["rows"]] == ["next"]


async def test_archive_needs_rescan_legacy_fallback_is_audited(
    archive: ConversationArchive,
) -> None:
    first_pk = await archive.record(
        group_id="370",
        role="user",
        speaker="A(1)",
        content_text="before",
        content_json=None,
        message_id=370,
        created_at=3700.0,
    )
    await archive.record(
        group_id="370",
        role="user",
        speaker="A(2)",
        content_text="after",
        content_json=None,
        message_id=371,
        created_at=3701.0,
    )
    assert first_pk is not None
    await archive.upsert_cursor(
        scanner_name="style_manual_extract",
        chat_type="group",
        chat_id="370",
        last_message_pk=first_pk,
        scanner_version="v1",
        params_hash="p1",
    )

    batch = await archive.read_scan_batch(
        scanner_name="style_manual_extract",
        group_id="370",
        limit=10,
        scanner_version="v2",
        params_hash="p1",
    )

    assert batch["source"] == "legacy_fallback"
    assert batch["can_advance"] is False
    assert batch["run_id"]
    cursor = await archive.get_cursor(
        scanner_name="style_manual_extract",
        chat_type="group",
        chat_id="370",
    )
    assert cursor is not None
    assert cursor["status"] == "needs_rescan"
    assert cursor["last_message_pk"] == first_pk
    run_cursor = await archive._db.execute(
        """SELECT status, finished_at, scanned_count, meta_json
           FROM conversation_scan_runs
           WHERE run_id = ?""",
        (batch["run_id"],),
    )
    run = await run_cursor.fetchone()
    assert run["status"] == "legacy_fallback"
    assert run["finished_at"] is not None
    assert run["scanned_count"] == 2
    assert "cursor_needs_rescan" in run["meta_json"]

    await archive.finish_scan_batch(batch, status="success", scanned_count=2)
    unchanged = await archive.get_cursor(
        scanner_name="style_manual_extract",
        chat_type="group",
        chat_id="370",
    )
    assert unchanged is not None
    assert unchanged["status"] == "needs_rescan"
    assert unchanged["last_message_pk"] == first_pk


async def test_archive_abandoned_scan_batch_does_not_advance_cursor(
    archive: ConversationArchive,
) -> None:
    await archive.record(
        group_id="380",
        role="user",
        speaker="A(1)",
        content_text="hello",
        content_json=None,
        message_id=380,
        created_at=3800.0,
    )
    batch = await archive.read_scan_batch(
        scanner_name="slang_manual_extract",
        group_id="380",
        limit=10,
        scanner_version="v1",
    )
    await archive.finish_scan_batch(
        batch,
        status="abandoned",
        scanned_count=1,
        error="cancelled",
        advance_cursor=False,
    )
    cursor = await archive.get_cursor(
        scanner_name="slang_manual_extract",
        chat_type="group",
        chat_id="380",
    )
    assert cursor is None
    run_cursor = await archive._db.execute(
        "SELECT status, error FROM conversation_scan_runs WHERE run_id = ?",
        (batch["run_id"],),
    )
    run = await run_cursor.fetchone()
    assert dict(run) == {"status": "abandoned", "error": "cancelled"}


async def test_archive_scan_run_lifecycle(archive: ConversationArchive) -> None:
    run_id = await archive.start_scan_run(
        scanner_name="slang_extract",
        chat_type="group",
        chat_id="400",
        from_message_pk=0,
        to_message_pk=10,
        backtrack_from_message_pk=0,
        meta={"manual": True},
    )
    await archive.finish_scan_run(
        run_id,
        status="success",
        scanned_count=10,
        extracted_count=2,
        filtered_count=1,
        saved_count=1,
    )
    cursor = await archive._db.execute(
        """SELECT status, scanned_count, extracted_count, filtered_count, saved_count
           FROM conversation_scan_runs
           WHERE run_id = ?""",
        (run_id,),
    )
    row = await cursor.fetchone()
    assert dict(row) == {
        "status": "success",
        "scanned_count": 10,
        "extracted_count": 2,
        "filtered_count": 1,
        "saved_count": 1,
    }


async def test_archive_dry_run_blocks_without_policy_or_cursor(
    archive: ConversationArchive,
) -> None:
    missing_policy = await archive.dry_run_cleanup(
        chat_type="group",
        chat_id="500",
        required_scanners=["slang_extract"],
    )
    assert missing_policy["blocked"] is True
    assert "retention_policy_missing" in missing_policy["blockers"]

    await archive.set_retention_policy(
        chat_type="group",
        chat_id="500",
        cleanup_enabled=True,
        keep_raw_forever=False,
        raw_retention_days=1,
    )
    missing_cursor = await archive.dry_run_cleanup(
        chat_type="group",
        chat_id="500",
        required_scanners=["slang_extract"],
    )
    assert missing_cursor["blocked"] is True
    assert "missing_required_cursor:slang_extract:chat" in missing_cursor["blockers"]


async def test_archive_dry_run_respects_refs_and_never_deletes(
    archive: ConversationArchive,
) -> None:
    now = time.time()
    old_pk = await archive.record(
        group_id="600",
        role="user",
        speaker="Old(1)",
        content_text="old evidence",
        content_json=None,
        message_id=601,
        created_at=now - 10 * 86400,
    )
    free_pk = await archive.record(
        group_id="600",
        role="user",
        speaker="Free(2)",
        content_text="old free",
        content_json=None,
        message_id=602,
        created_at=now - 9 * 86400,
    )
    assert old_pk is not None
    assert free_pk is not None
    await archive.add_message_ref(
        message_pk=old_pk,
        ref_owner="slang",
        ref_type="evidence",
        external_table="slang_observations",
        external_id="obs-1",
    )
    await archive.set_retention_policy(
        chat_type="group",
        chat_id="600",
        cleanup_enabled=True,
        keep_raw_forever=False,
        raw_retention_days=7,
    )
    await archive.upsert_cursor(
        scanner_name="slang_extract",
        chat_type="group",
        chat_id="600",
        last_message_pk=free_pk,
    )
    result = await archive.dry_run_cleanup(
        chat_type="group",
        chat_id="600",
        required_scanners=["slang_extract"],
        now=now,
    )
    assert result["blocked"] is False
    assert result["candidate_message_pks"] == [free_pk]
    assert result["protected_message_pks"] == [old_pk]
    assert result["candidate_count"] == 1
    assert result["protected_count"] == 1

    rows = await archive.query_recent("600", limit=10)
    assert [row["content_text"] for row in rows] == ["old evidence", "old free"]


async def test_archive_platform_message_ref_protects_cleanup(
    archive: ConversationArchive,
) -> None:
    now = time.time()
    protected_pk = await archive.record(
        group_id="700",
        role="user",
        speaker="A(1)",
        content_text="business evidence",
        content_json=None,
        message_id=701,
        created_at=now - 10 * 86400,
    )
    free_pk = await archive.record(
        group_id="700",
        role="user",
        speaker="A(2)",
        content_text="cleanup candidate",
        content_json=None,
        message_id=702,
        created_at=now - 10 * 86400,
    )
    assert protected_pk is not None
    assert free_pk is not None
    ref_id = await archive.add_message_ref_for_platform_message(
        chat_type="group",
        chat_id="700",
        platform_message_id=701,
        ref_owner="style",
        ref_type="evidence",
        external_table="style_expressions",
        external_id="sty_1",
    )
    missing = await archive.add_message_ref_for_platform_message(
        chat_type="group",
        chat_id="700",
        platform_message_id=999,
        ref_owner="style",
        ref_type="evidence",
        external_table="style_expressions",
        external_id="sty_missing",
    )
    assert ref_id
    assert missing is None

    await archive.set_retention_policy(
        chat_type="group",
        chat_id="700",
        cleanup_enabled=True,
        keep_raw_forever=False,
        raw_retention_days=7,
    )
    await archive.upsert_cursor(
        scanner_name="style_manual_extract",
        chat_type="group",
        chat_id="700",
        last_message_pk=free_pk,
    )
    result = await archive.dry_run_cleanup(
        chat_type="group",
        chat_id="700",
        required_scanners=["style_manual_extract"],
        now=now,
    )
    assert result["protected_message_pks"] == [protected_pk]
    assert result["candidate_message_pks"] == [free_pk]


async def test_archive_business_ref_sync_is_idempotent(
    archive: ConversationArchive,
    tmp_path,
) -> None:
    await archive.record(
        group_id="800",
        role="user",
        speaker="A(1)",
        content_text="slang evidence",
        content_json=None,
        message_id=801,
        created_at=8001.0,
    )
    await archive.record(
        group_id="800",
        role="user",
        speaker="A(2)",
        content_text="style evidence",
        content_json=None,
        message_id=802,
        created_at=8002.0,
    )
    slang_db = tmp_path / "slang.db"
    style_db = tmp_path / "style.db"
    async with aiosqlite.connect(slang_db) as db:
        await db.execute(
            """CREATE TABLE slang_observations (
                observation_id TEXT PRIMARY KEY,
                term_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                message_id INTEGER,
                raw_text TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL
            )"""
        )
        await db.execute(
            """INSERT INTO slang_observations
               (observation_id, term_id, group_id, message_id, raw_text, observed_at)
               VALUES ('obs_1', 'slang_1', '800', 801, 'slang evidence', '2026-05-13')"""
        )
        await db.execute(
            """INSERT INTO slang_observations
               (observation_id, term_id, group_id, message_id, raw_text, observed_at)
               VALUES ('obs_missing', 'slang_2', '800', 899, 'missing', '2026-05-13')"""
        )
        await db.commit()
    async with aiosqlite.connect(style_db) as db:
        await db.execute(
            """CREATE TABLE style_evidence (
                evidence_id TEXT PRIMARY KEY,
                expression_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                message_id INTEGER,
                raw_text TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL
            )"""
        )
        await db.execute(
            """INSERT INTO style_evidence
               (evidence_id, expression_id, group_id, message_id, raw_text, observed_at)
               VALUES ('sev_1', 'sty_1', '800', 802, 'style evidence', '2026-05-13')"""
        )
        await db.commit()

    first = await sync_business_message_refs(
        archive,
        slang_db_path=slang_db,
        style_db_path=style_db,
    )
    second = await sync_business_message_refs(
        archive,
        slang_db_path=slang_db,
        style_db_path=style_db,
    )
    assert first["slang"]["linked"] == 1
    assert first["slang"]["missing_message"] == 1
    assert first["style"]["linked"] == 1
    assert second["slang"]["linked"] == 1
    assert second["style"]["linked"] == 1
    cursor = await archive._db.execute("SELECT COUNT(*) AS n FROM conversation_message_refs")
    row = await cursor.fetchone()
    assert row["n"] == 2
