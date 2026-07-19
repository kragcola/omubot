"""TDD contracts for QZone Journal v0.3 review-console backend surface."""

from __future__ import annotations

import importlib
import sqlite3
from datetime import date, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI

from kernel.types import AdminRoute, PluginContext
from services.storage.migrations import Migration, MigrationRunner

_CST = ZoneInfo("Asia/Shanghai")
_ALL_STATUSES = (
    "pending_review",
    "approved",
    "dispatching",
    "published",
    "rejected",
    "failed",
    "unknown",
)


def _optional_qzone_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        missing = str(exc.name or "")
        if missing and (missing == name or name.startswith(f"{missing}.")):
            return None
        raise


def _required_symbol(module_name: str, symbol_name: str) -> Any:
    module = _optional_qzone_module(module_name)
    assert module is not None, f"{module_name} must exist"
    symbol = getattr(module, symbol_name, None)
    assert symbol is not None, f"{module_name} must expose {symbol_name}"
    return symbol


def _plugin_context(tmp_path: Path) -> PluginContext:
    schedule = SimpleNamespace(
        date=datetime.now(_CST).date().isoformat(),
        day_narrative="和平常一样的一天",
        slots=[],
    )
    return PluginContext(
        storage_dir=tmp_path,
        plugin_data_dir=tmp_path / "plugins",
        llm_client=SimpleNamespace(_call=lambda _req: {"text": ""}),
        schedule_store=SimpleNamespace(current=schedule),
        story_arc_store=SimpleNamespace(load_active=lambda: None),
    )


def _enabled_plugin(**overrides: Any) -> Any:
    PluginConfig = _required_symbol("plugins.qzone_journal.plugin", "PluginConfig")
    Plugin = _required_symbol("plugins.qzone_journal.plugin", "QZoneJournalPlugin")
    base = {
        "enabled": True,
        "dry_run": True,
        "allow_live_publish": False,
        "manual_review": True,
    }
    base.update(overrides)
    return Plugin(config=PluginConfig(**base))


def _single_admin_route(plugin: Any) -> AdminRoute:
    routes = plugin.register_admin_routes()
    assert len(routes) == 1
    route = routes[0]
    assert isinstance(route, AdminRoute)
    return route


async def _enqueue(store: Any, key: str, content: str = "待审草稿") -> Any:
    return await store.enqueue(
        dedupe_key=key,
        event_date=date(2026, 7, 16),
        source="event_replan",
        content=content,
    )


async def test_store_migration_v3_to_v4_creates_review_decisions(tmp_path: Path) -> None:
    """Bootstrap a pure v3 DB, then upgrade via JournalStore.init to v4."""
    db_path = tmp_path / "legacy_v3.db"

    async def apply_v1(db: Any) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS qzone_journal_drafts (
                draft_id          TEXT PRIMARY KEY,
                dedupe_key        TEXT NOT NULL UNIQUE,
                event_date        TEXT NOT NULL,
                source            TEXT NOT NULL,
                content           TEXT NOT NULL,
                status            TEXT NOT NULL,
                publish_date      TEXT,
                external_post_id  TEXT,
                last_error_code   TEXT,
                created_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_qzone_drafts_status "
            "ON qzone_journal_drafts(status, created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_qzone_drafts_publish_date "
            "ON qzone_journal_drafts(publish_date, status)"
        )

    async def verify_v1(db: Any) -> bool:
        cursor = await db.execute("PRAGMA table_info(qzone_journal_drafts)")
        try:
            columns = {str(row[1]) for row in await cursor.fetchall()}
        finally:
            await cursor.close()
        return "draft_id" in columns

    async def apply_v2(db: Any) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS qzone_journal_manual_resolutions (
                resolution_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id          TEXT NOT NULL,
                decision          TEXT NOT NULL,
                note              TEXT NOT NULL,
                previous_status   TEXT NOT NULL,
                new_status        TEXT NOT NULL,
                external_post_id  TEXT,
                created_at        TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_qzone_manual_resolutions_draft "
            "ON qzone_journal_manual_resolutions(draft_id, created_at)"
        )

    async def verify_v2(db: Any) -> bool:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='qzone_journal_manual_resolutions'"
        )
        try:
            return (await cursor.fetchone()) is not None
        finally:
            await cursor.close()

    async def apply_v3(db: Any) -> None:
        for name, definition in (
            ("stable_id", "TEXT"),
            ("subject_kind", "TEXT"),
            ("privacy", "TEXT"),
            ("salience", "REAL"),
            ("source_summary", "TEXT"),
            ("provenance_json", "TEXT"),
        ):
            await db.execute(
                f"ALTER TABLE qzone_journal_drafts ADD COLUMN {name} {definition}"
            )

    async def verify_v3(db: Any) -> bool:
        cursor = await db.execute("PRAGMA table_info(qzone_journal_drafts)")
        try:
            columns = {str(row[1]) for row in await cursor.fetchall()}
        finally:
            await cursor.close()
        return "provenance_json" in columns

    runner = MigrationRunner(db_path=db_path, db_id="qzone_journal")
    await runner.ensure(
        (
            Migration(
                version=1,
                name="create_qzone_journal_outbox",
                checksum="qzone-journal-v1-drafts-outbox-20260715",
                apply=apply_v1,
                verify=verify_v1,
            ),
            Migration(
                version=2,
                name="create_qzone_journal_manual_resolutions",
                checksum="qzone-journal-v2-manual-resolutions-20260715",
                apply=apply_v2,
                verify=verify_v2,
            ),
            Migration(
                version=3,
                name="add_qzone_journal_review_provenance",
                checksum="qzone-journal-v3-review-provenance-20260716",
                apply=apply_v3,
                verify=verify_v3,
            ),
        )
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO qzone_journal_drafts (
                draft_id, dedupe_key, event_date, source, content,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending_review', ?, ?)
            """,
            (
                "qzd_legacy_v3",
                "legacy-v3-key",
                "2026-07-16",
                "event_replan",
                "旧 v3 草稿",
                "2026-07-16T00:00:00+00:00",
                "2026-07-16T00:00:00+00:00",
            ),
        )
        connection.commit()
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == 3

    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    store = JournalStore(db_path)
    await store.init()
    try:
        legacy = await store.get("qzd_legacy_v3")
        assert legacy is not None
        assert legacy.content == "旧 v3 草稿"
    finally:
        await store.close()

    with sqlite3.connect(db_path) as connection:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        versions = [
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM _omubot_schema_migrations ORDER BY version"
            )
        ]
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(qzone_journal_review_decisions)"
            )
        }
        indexes = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA index_list(qzone_journal_review_decisions)"
            )
        }
        # Manual resolutions table must remain independent.
        manual = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='qzone_journal_manual_resolutions'"
        ).fetchone()

    assert user_version == 6
    assert versions == [1, 2, 3, 4, 5, 6]
    assert manual == ("qzone_journal_manual_resolutions",)
    for col in (
        "decision_id",
        "draft_id",
        "decision",
        "note",
        "previous_status",
        "new_status",
        "created_at",
    ):
        assert col in columns
    assert "idx_qzone_review_decisions_draft" in indexes


async def test_fresh_store_init_is_user_version_6_with_review_table(
    tmp_path: Path,
) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    await store.close()

    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        versions = [
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM _omubot_schema_migrations ORDER BY version"
            )
        ]
        review = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='qzone_journal_review_decisions'"
        ).fetchone()

    assert user_version == 6
    assert versions == [1, 2, 3, 4, 5, 6]
    assert review == ("qzone_journal_review_decisions",)


async def test_approve_reject_audit_atomic_and_idempotent(tmp_path: Path) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        pending = await _enqueue(store, "audit-approve")
        approved = await store.approve(pending.draft_id, note="  看起来可以  ")
        again = await store.approve(approved.draft_id, note="重复批准")
        assert again.status == "approved"

        rejectable = await _enqueue(store, "audit-reject")
        rejected = await store.reject(
            rejectable.draft_id,
            reason="admin_review_rejected",
            note="题材不合适",
        )
        again_reject = await store.reject(
            rejected.draft_id,
            reason="admin_review_rejected",
            note="再次拒绝也不应重复审计",
        )
        assert again_reject.status == "rejected"

        approve_audits = await store.list_review_decisions(draft_id=approved.draft_id)
        reject_audits = await store.list_review_decisions(draft_id=rejected.draft_id)
    finally:
        await store.close()

    assert len(approve_audits) == 1
    assert approve_audits[0]["decision"] == "approve"
    assert approve_audits[0]["note"] == "看起来可以"
    assert approve_audits[0]["previous_status"] == "pending_review"
    assert approve_audits[0]["new_status"] == "approved"
    assert "operator" not in approve_audits[0]
    assert "user_id" not in approve_audits[0]

    assert len(reject_audits) == 1
    assert reject_audits[0]["decision"] == "reject"
    assert reject_audits[0]["note"] == "题材不合适"
    assert reject_audits[0]["previous_status"] == "pending_review"
    assert reject_audits[0]["new_status"] == "rejected"

    # Reason code still lands on the draft for reject.
    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        reason = connection.execute(
            "SELECT last_error_code FROM qzone_journal_drafts WHERE draft_id = ?",
            (rejected.draft_id,),
        ).fetchone()[0]
    assert reason == "admin_review_rejected"


async def test_approve_reject_roll_back_when_review_audit_insert_fails(
    tmp_path: Path,
) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        approve_target = await _enqueue(store, "audit-approve-rollback")
        db = store._require_db()
        await db.execute(
            """
            CREATE TRIGGER qzone_test_fail_approve_audit
            BEFORE INSERT ON qzone_journal_review_decisions
            WHEN NEW.decision = 'approve'
            BEGIN
                SELECT RAISE(ABORT, 'forced approve audit failure');
            END
            """
        )
        await db.commit()

        with pytest.raises(sqlite3.IntegrityError, match="forced approve audit failure"):
            await store.approve(approve_target.draft_id, note="应整体回滚")

        approve_after = await store.get(approve_target.draft_id)
        approve_audits = await store.list_review_decisions(
            draft_id=approve_target.draft_id
        )
        assert approve_after is not None
        assert approve_after.status == "pending_review"
        assert approve_audits == []

        await db.execute("DROP TRIGGER qzone_test_fail_approve_audit")
        await db.commit()

        reject_target = await _enqueue(store, "audit-reject-rollback")
        await db.execute(
            """
            CREATE TRIGGER qzone_test_fail_reject_audit
            BEFORE INSERT ON qzone_journal_review_decisions
            WHEN NEW.decision = 'reject'
            BEGIN
                SELECT RAISE(ABORT, 'forced reject audit failure');
            END
            """
        )
        await db.commit()

        with pytest.raises(sqlite3.IntegrityError, match="forced reject audit failure"):
            await store.reject(
                reject_target.draft_id,
                reason="admin_review_rejected",
                note="也应整体回滚",
            )

        reject_after = await store.get(reject_target.draft_id)
        reject_audits = await store.list_review_decisions(
            draft_id=reject_target.draft_id
        )
        assert reject_after is not None
        assert reject_after.status == "pending_review"
        assert reject_after.last_error_code is None
        assert reject_audits == []
    finally:
        await store.close()


async def test_operator_note_scrub_never_retains_secrets_or_qq_ids(
    tmp_path: Path,
) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        draft = await _enqueue(store, "note-scrub-review")
        await store.approve(
            draft.draft_id,
            note="看过 qq_384801062，token=note-secret，可以发。",
        )
        audits = await store.list_review_decisions(draft_id=draft.draft_id)

        # Empty optional approve note is allowed (no body / blank).
        blank = await _enqueue(store, "note-blank-approve")
        blank_ok = await store.approve(blank.draft_id, note=None)
        assert blank_ok.status == "approved"
        blank_audits = await store.list_review_decisions(draft_id=blank.draft_id)
    finally:
        await store.close()

    assert len(audits) == 1
    note = str(audits[0]["note"])
    assert "384801062" not in note
    assert "note-secret" not in note
    assert "token=" not in note.lower() or "[redacted]" in note

    assert len(blank_audits) == 1
    assert blank_audits[0]["note"] == ""


async def test_list_pagination_offset_and_truthful_total(tmp_path: Path) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        for index in range(5):
            await _enqueue(store, f"page-{index}", content=f"草稿 {index}")
        await store.approve((await store.list(limit=1))[0].draft_id)

        page0 = await store.list(status="pending_review", limit=2, offset=0)
        page1 = await store.list(status="pending_review", limit=2, offset=2)
        page2 = await store.list(status="pending_review", limit=2, offset=4)
        total = await store.count(status="pending_review")
        all_pending = await store.list(status="pending_review", limit=100, offset=0)
    finally:
        await store.close()

    assert total == 4
    assert len(page0) == 2
    assert len(page1) == 2
    assert len(page2) == 0
    page0_ids = {row.draft_id for row in page0}
    page1_ids = {row.draft_id for row in page1}
    assert page0_ids.isdisjoint(page1_ids)
    assert {row.draft_id for row in all_pending} == page0_ids | page1_ids


async def test_admin_list_detail_audit_and_reject_note_requirements(
    tmp_path: Path,
) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    plugin = _enabled_plugin()
    ctx = _plugin_context(tmp_path)
    db_path = tmp_path / "qzone_journal.db"

    await plugin.on_startup(ctx)
    seed = JournalStore(db_path)
    await seed.init()
    try:
        for index in range(3):
            await seed.enqueue(
                dedupe_key=f"admin-page-{index}",
                event_date=date(2026, 7, 16),
                source="event_replan",
                content=f"后台草稿 {index}",
            )
        rows = await seed.list(limit=10)
        first_id = rows[0].draft_id
        second_id = rows[1].draft_id
        third_id = rows[2].draft_id
    finally:
        await seed.close()

    route = _single_admin_route(plugin)
    app = FastAPI()
    base_path = route.path.rstrip("/")
    app.include_router(route.router, prefix=base_path)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            listed = await client.get(
                f"{base_path}/drafts",
                params={"status": "pending_review", "limit": 2, "offset": 0},
            )
            page2 = await client.get(
                f"{base_path}/drafts",
                params={"status": "pending_review", "limit": 2, "offset": 2},
            )
            bad_limit = await client.get(
                f"{base_path}/drafts",
                params={"limit": 101},
            )
            bad_status = await client.get(
                f"{base_path}/drafts",
                params={"status": "not-a-real-status"},
            )
            detail = await client.get(f"{base_path}/drafts/{first_id}")
            missing_detail = await client.get(f"{base_path}/drafts/missing-draft")
            empty_approve = await client.post(f"{base_path}/{first_id}/approve")
            note_approve = await client.post(
                f"{base_path}/{second_id}/approve",
                json={"note": "可选备注"},
            )
            reject_approved = await client.post(
                f"{base_path}/{second_id}/reject",
                json={"note": "已通过后不允许再走普通拒绝"},
            )
            bare_reject = await client.post(f"{base_path}/{third_id}/reject")
            empty_reject = await client.post(
                f"{base_path}/{third_id}/reject",
                json={"note": "   "},
            )
            ok_reject = await client.post(
                f"{base_path}/{third_id}/reject",
                json={"note": "题材不合适"},
            )
            audit = await client.get(f"{base_path}/drafts/{third_id}/audit")
            missing_audit = await client.get(f"{base_path}/drafts/missing-draft/audit")
            # publish endpoint must still exist
            publish_probe = await client.post(f"{base_path}/{first_id}/publish")

        assert listed.status_code == 200
        body = listed.json()
        assert set(body) >= {"drafts", "total", "limit", "offset", "has_more"}
        assert body["total"] == 3
        assert body["limit"] == 2
        assert body["offset"] == 0
        assert body["has_more"] is True
        assert len(body["drafts"]) == 2

        assert page2.status_code == 200
        body2 = page2.json()
        assert body2["total"] == 3
        assert body2["offset"] == 2
        assert body2["has_more"] is False
        assert len(body2["drafts"]) == 1

        assert bad_limit.status_code == 422
        assert bad_status.status_code == 422

        assert detail.status_code == 200
        assert detail.json()["draft_id"] == first_id
        assert missing_detail.status_code == 404

        assert empty_approve.status_code == 200
        assert empty_approve.json()["status"] == "approved"
        assert note_approve.status_code == 200
        assert note_approve.json()["status"] == "approved"
        assert reject_approved.status_code == 409

        assert bare_reject.status_code == 422
        assert empty_reject.status_code == 422
        assert ok_reject.status_code == 200
        assert ok_reject.json()["status"] == "rejected"

        assert audit.status_code == 200
        audit_body = audit.json()
        assert "review_decisions" in audit_body
        assert "manual_resolutions" in audit_body
        assert isinstance(audit_body["review_decisions"], list)
        assert isinstance(audit_body["manual_resolutions"], list)
        assert len(audit_body["review_decisions"]) == 1
        assert audit_body["review_decisions"][0]["decision"] == "reject"
        assert audit_body["review_decisions"][0]["note"] == "题材不合适"
        assert audit_body["manual_resolutions"] == []
        assert missing_audit.status_code == 404

        # Endpoint retained; dry-run config still blocks live publish.
        assert publish_probe.status_code == 409
    finally:
        await plugin.on_shutdown(ctx)


async def test_admin_health_full_counts_and_live_publish_gate(tmp_path: Path) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    plugin = _enabled_plugin()
    ctx = _plugin_context(tmp_path)
    db_path = tmp_path / "qzone_journal.db"

    await plugin.on_startup(ctx)
    seed = JournalStore(db_path, max_posts_per_day=3)
    await seed.init()
    try:
        pending = await seed.enqueue(
            dedupe_key="health-pending",
            event_date=date(2026, 7, 16),
            source="event_replan",
            content="待审",
        )
        del pending
        approved = await seed.approve(
            (
                await seed.enqueue(
                    dedupe_key="health-approved",
                    event_date=date(2026, 7, 16),
                    source="event_replan",
                    content="已批",
                )
            ).draft_id
        )
        rejected = await seed.reject(
            (
                await seed.enqueue(
                    dedupe_key="health-rejected",
                    event_date=date(2026, 7, 16),
                    source="event_replan",
                    content="已拒",
                )
            ).draft_id,
            reason="admin_review_rejected",
            note="不要",
        )
        del rejected
        unknown_src = await seed.approve(
            (
                await seed.enqueue(
                    dedupe_key="health-unknown",
                    event_date=date(2026, 7, 16),
                    source="event_replan",
                    content="未知",
                )
            ).draft_id
        )
        claimed = await seed.claim_for_publish(
            unknown_src.draft_id,
            now=datetime(2026, 7, 16, 12, 0, tzinfo=_CST),
        )
        assert claimed is not None
        await seed.mark_unknown(unknown_src.draft_id, reason="ambiguous")
        # keep approved row; leave dispatching/published/failed at 0
        assert approved.status == "approved"
    finally:
        await seed.close()

    route = _single_admin_route(plugin)
    app = FastAPI()
    base_path = route.path.rstrip("/")
    app.include_router(route.router, prefix=base_path)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(f"{base_path}/health")
    finally:
        await plugin.on_shutdown(ctx)

    assert response.status_code == 200
    body = response.json()
    counts = body["counts"]
    for status in _ALL_STATUSES:
        assert status in counts
        assert isinstance(counts[status], int)
    assert counts["pending_review"] == 1
    assert counts["approved"] == 1
    assert counts["rejected"] == 1
    assert counts["unknown"] == 1
    assert counts["dispatching"] == 0
    assert counts["published"] == 0
    assert counts["failed"] == 0

    gate = body["live_publish_gate"]
    assert isinstance(gate, dict)
    assert gate["ready"] is False
    assert isinstance(gate["reasons"], list)
    codes = {str(item["code"]) for item in gate["reasons"]}
    messages = {str(item["message"]) for item in gate["reasons"]}
    assert "dry_run_enabled" in codes
    assert "live_publish_disallowed" in codes
    assert "profile_not_validated" in codes
    assert "allowed_live_uins_empty" in codes
    assert "bot_disconnected" in codes
    # Chinese operator-facing copy present.
    assert any(any("\u4e00" <= ch <= "\u9fff" for ch in msg) for msg in messages)
    assert body["profile_validated"] is False


async def test_live_publish_gate_remains_false_when_profile_unvalidated(
    tmp_path: Path,
) -> None:
    PluginConfig = _required_symbol("plugins.qzone_journal.plugin", "PluginConfig")
    Plugin = _required_symbol("plugins.qzone_journal.plugin", "QZoneJournalPlugin")
    # Even with live flags open, built-in profile stays unvalidated so ready
    # must remain false — credentials are never inspected.
    plugin = Plugin(
        config=PluginConfig(
            enabled=True,
            dry_run=False,
            allow_live_publish=True,
            allowed_live_uins=["384801062"],
            manual_review=True,
        )
    )
    ctx = _plugin_context(tmp_path)
    # Simulate connected bot + transport without reading credentials.
    plugin._bot = SimpleNamespace(self_id="384801062")
    from plugins.qzone_journal.transport import QZoneTransport

    plugin._transport = QZoneTransport()

    await plugin.on_startup(ctx)
    # on_startup may reset bot/transport when enabled — re-apply after startup.
    plugin._bot = SimpleNamespace(self_id="384801062")
    plugin._transport = QZoneTransport()

    route = _single_admin_route(plugin)
    app = FastAPI()
    base_path = route.path.rstrip("/")
    app.include_router(route.router, prefix=base_path)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(f"{base_path}/health")
    finally:
        await plugin.on_shutdown(ctx)

    assert response.status_code == 200
    gate = response.json()["live_publish_gate"]
    codes = {str(item["code"]) for item in gate["reasons"]}
    assert "profile_not_validated" in codes
    assert "dry_run_enabled" not in codes
    assert "live_publish_disallowed" not in codes
    assert "allowed_live_uins_empty" not in codes
    assert "bot_disconnected" not in codes
    assert "transport_unavailable" not in codes
    assert gate["ready"] is False


async def test_configured_disabled_gate_reason(tmp_path: Path) -> None:
    PluginConfig = _required_symbol("plugins.qzone_journal.plugin", "PluginConfig")
    Plugin = _required_symbol("plugins.qzone_journal.plugin", "QZoneJournalPlugin")
    plugin = Plugin(
        config=PluginConfig(
            enabled=False,
            dry_run=True,
            allow_live_publish=False,
            manual_review=True,
        )
    )
    # Disabled plugin still exposes admin routes for operators.
    route = _single_admin_route(plugin)
    app = FastAPI()
    base_path = route.path.rstrip("/")
    app.include_router(route.router, prefix=base_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get(f"{base_path}/health")

    assert response.status_code == 200
    gate = response.json()["live_publish_gate"]
    codes = {str(item["code"]) for item in gate["reasons"]}
    assert "configured_disabled" in codes
    assert gate["ready"] is False


def test_admin_frontend_renders_selection_observability_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    types_source = (
        root / "admin/frontend/src/views/qzone-journal/types.ts"
    ).read_text(encoding="utf-8")
    view_source = (
        root / "admin/frontend/src/views/qzone-journal/QzoneJournalView.vue"
    ).read_text(encoding="utf-8")

    assert "QzoneSelectionReason" in types_source
    assert "QZONE_SELECTION_REASON_LABELS" in types_source
    assert "selection_decisions" in types_source
    assert "selection_summary" in types_source
    assert "max_drafts_per_tick" in types_source
    assert "max_drafts_per_day" in types_source
    assert "选材诊断" in view_source
    assert "选材诊断暂不可用" in view_source
    assert "selectionRows" in view_source
    assert "selectionSummary" in view_source
    assert 'class="qzone-page"' in view_source
    assert 'class="qzone-selection"' in view_source
    assert ".qzone-page :deep(.om-page__surface)" in view_source
    assert ".qzone-selection {" in view_source
    assert "min-width: 0" in view_source
    gate_code_css = view_source.split(".qzone-gate__code {", maxsplit=1)[1].split(
        "}", maxsplit=1
    )[0]
    assert "flex-shrink: 0" not in gate_code_css
    assert "overflow-wrap: anywhere" in gate_code_css


def test_admin_frontend_draft_drawer_guards_stale_async_actions() -> None:
    """Drawer mutations must not paint/toast after draft switch or close."""
    root = Path(__file__).resolve().parents[1]
    drawer_source = (
        root / "admin/frontend/src/views/qzone-journal/DraftDetailDrawer.vue"
    ).read_text(encoding="utf-8")

    assert "let actionGeneration = 0" in drawer_source
    assert "actionGeneration += 1" in drawer_source or "++actionGeneration" in drawer_source

    for handler in (
        "async function onApprove()",
        "async function onReject()",
        "async function onDryRun()",
        "async function onConfirmPublished()",
        "async function onConfirmNotPublished()",
    ):
        assert handler in drawer_source

    # Each mutation captures draft id + generation, then re-checks after await.
    assert "const actionDraftId = props.draftId" in drawer_source
    assert "const actionGen = " in drawer_source
    assert "function isCurrentAction" in drawer_source
    assert "actionGen === actionGeneration" in drawer_source
    assert "props.draftId === actionDraftId" in drawer_source
    assert "isCurrentAction(actionDraftId, actionGen)" in drawer_source

    watch_block = drawer_source.split("watch(", maxsplit=1)[1].split(
        "</script>", maxsplit=1
    )[0]
    assert "actionGeneration" in watch_block
    assert "actionBusy.value = false" in watch_block

    # Closing or switching draft invalidates in-flight UI writes.
    assert "if (!visible)" in watch_block or "if (!visible)" in drawer_source


def test_admin_frontend_gate_distinguishes_loading_error_and_ready() -> None:
    """Cold load must say checking; health failure must not show stale gate."""
    root = Path(__file__).resolve().parents[1]
    view_source = (
        root / "admin/frontend/src/views/qzone-journal/QzoneJournalView.vue"
    ).read_text(encoding="utf-8")

    assert "healthLoading" in view_source
    assert "正在检查门禁" in view_source
    assert "gatePhase" in view_source
    assert "gatePhase === 'loading'" in view_source or 'gatePhase === "loading"' in view_source
    assert "qzone-gate--loading" in view_source
    assert "qzone-gate--error" in view_source
    assert "qzone-gate--blocked" in view_source
    assert "qzone-gate--ready" in view_source

    # Class binding must not collapse loading/error into blocked.
    assert "gateReady ? 'qzone-gate--ready' : 'qzone-gate--blocked'" not in view_source
    assert "gateClass" in view_source

    load_health = view_source.split("async function loadHealth", maxsplit=1)[1].split(
        "async function loadDrafts", maxsplit=1
    )[0]
    assert "health.value = null" in load_health

    # Reasons only when we have a definitive blocked health snapshot.
    assert "gateReasons.length" in view_source
    reasons_line = [
        line for line in view_source.splitlines()
        if "gateReasons" in line and ("v-if" in line or "gatePhase" in line)
    ]
    assert reasons_line, "gate reasons list must be conditionally rendered"
    joined = "\n".join(reasons_line)
    assert "blocked" in joined

    # Meta suppressed without healthy payload (cleared on error).
    assert 'v-if="health"' in view_source or 'v-if="health &&' in view_source

    # Keep no-live-publish UI boundary.
    assert "真实发布按钮" in view_source or "live publish" in view_source.lower()
    assert "publishQzoneDraft" not in view_source
