"""RED/GREEN contracts for advanced fiction context and draft provenance.

Offline-only: no live HTTP, credential acquisition, or NapCat operations.
"""

from __future__ import annotations

import importlib
import json
import re
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
from services.llm.llm_request import LLMRequest
from services.storage.migrations import Migration, MigrationRunner

_CST = ZoneInfo("Asia/Shanghai")


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
    assert module is not None, f"{module_name} must exist for advanced context contract"
    symbol = getattr(module, symbol_name, None)
    assert symbol is not None, f"{module_name} must expose {symbol_name}"
    return symbol


def _candidate(
    *,
    subject_kind: str = "fiction",
    summary: str = "今天把卡住的排练段落理顺了",
    stable_id: str = "arc-main:event-1",
    salience: float = 0.9,
) -> Any:
    CandidateEvent = _required_symbol(
        "plugins.qzone_journal.selector",
        "CandidateEvent",
    )
    return CandidateEvent(
        source="event_replan",
        event_date=date(2026, 7, 16),
        stable_id=stable_id,
        subject_kind=subject_kind,
        privacy="public",
        salience=salience,
        summary=summary,
    )


def _fiction_arc(
    *,
    scope: str = "fiction",
    title: str = "舞台周推进",
    stage: str = "rehearsal",
    open_threads: list[str] | None = None,
    partner_states: dict[Any, Any] | None = None,
    arc_id: str = "arc-main",
    revision: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        arc_id=arc_id,
        revision=revision,
        title=title,
        scope=scope,
        stage=stage,
        open_threads=list(
            open_threads
            if open_threads is not None
            else ["把第二幕衔接理顺", "确认假日彩排时间"]
        ),
        partner_states=dict(
            partner_states
            if partner_states is not None
            else {
                "tenma_tsukasa": {
                    "kind": "fiction",
                    "current_state": "为比赛兴奋但压力大",
                    "availability": "normal",
                    "recent_events": ["主动提出加练"],
                },
                "friend_real": {
                    "kind": "factual",
                    "current_state": "真人状态不得入上下文",
                },
            }
        ),
        last_events=[],
    )


def _event_payload(
    *,
    subject_kind: str = "fiction",
    summary: str = "今天把卡住的排练段落理顺了",
    event_id: str = "arc-main:event-1",
) -> dict[str, Any]:
    return {
        "source": "event_replan",
        "date": datetime.now(_CST).date().isoformat(),
        "event_id": event_id,
        "summary": summary,
        "salience": 0.9,
        "subject_kind": subject_kind,
        "privacy": "public",
    }


class _LLMProbe:
    def __init__(self, result: Any = None) -> None:
        self.result = result if result is not None else {"text": "成稿"}
        self.calls: list[Any] = []
        self.credential_reads: list[str] = []

    async def _call(self, request: Any) -> Any:
        self.calls.append(request)
        return self.result

    async def __call__(self, request: Any) -> Any:
        return await self._call(request)


class _StoryArcStore:
    def __init__(self, arc: Any | None) -> None:
        self._arc = arc

    def load_active(self) -> Any:
        return self._arc


def _plugin_context(
    tmp_path: Path,
    llm: _LLMProbe,
    *,
    arc: Any | None = None,
    events: list[dict[str, Any]] | None = None,
    day_narrative: str = "和平常一样的一天",
) -> PluginContext:
    if events is not None:
        base = arc if arc is not None else _fiction_arc()
        arc = SimpleNamespace(
            arc_id=getattr(base, "arc_id", "arc-main"),
            revision=getattr(base, "revision", 1),
            title=getattr(base, "title", "舞台周推进"),
            scope=getattr(base, "scope", "fiction"),
            stage=getattr(base, "stage", "rehearsal"),
            open_threads=list(getattr(base, "open_threads", []) or []),
            partner_states=dict(getattr(base, "partner_states", {}) or {}),
            last_events=[dict(e) for e in events],
        )
    schedule = SimpleNamespace(
        date=datetime.now(_CST).date().isoformat(),
        day_narrative=day_narrative,
        slots=[],
    )
    return PluginContext(
        storage_dir=tmp_path,
        plugin_data_dir=tmp_path / "plugins",
        llm_client=llm,
        schedule_store=SimpleNamespace(current=schedule),
        story_arc_store=_StoryArcStore(arc),
    )


def _enabled_plugin(*, advanced_enabled: bool = False) -> Any:
    PluginConfig = _required_symbol("plugins.qzone_journal.plugin", "PluginConfig")
    Plugin = _required_symbol("plugins.qzone_journal.plugin", "QZoneJournalPlugin")
    return Plugin(
        config=PluginConfig(
            enabled=True,
            dry_run=True,
            allow_live_publish=False,
            manual_review=True,
            advanced_enabled=advanced_enabled,
        )
    )


def _request_text(request: LLMRequest) -> str:
    parts = [
        str(block.get("text", ""))
        for block in request.system_blocks()
        if isinstance(block, dict)
    ]
    for message in request.user_messages:
        if isinstance(message, dict):
            parts.append(str(message.get("content", "")))
    return "\n".join(parts)


def _single_admin_route(plugin: Any) -> AdminRoute:
    routes = plugin.register_admin_routes()
    assert len(routes) == 1
    return routes[0]


def test_advanced_context_empty_when_advanced_disabled() -> None:
    build = _required_symbol(
        "plugins.qzone_journal.advanced_fiction_context",
        "build_advanced_fiction_context",
    )
    text = build(
        advanced_enabled=False,
        arc=_fiction_arc(),
        candidate=_candidate(subject_kind="fiction"),
    )
    assert text == ""


def test_advanced_context_empty_for_non_fiction_arc_or_subject() -> None:
    build = _required_symbol(
        "plugins.qzone_journal.advanced_fiction_context",
        "build_advanced_fiction_context",
    )
    assert (
        build(
            advanced_enabled=True,
            arc=_fiction_arc(scope="life"),
            candidate=_candidate(subject_kind="fiction"),
        )
        == ""
    )
    assert (
        build(
            advanced_enabled=True,
            arc=_fiction_arc(scope="fiction"),
            candidate=_candidate(subject_kind="self"),
        )
        == ""
    )
    assert (
        build(
            advanced_enabled=True,
            arc=None,
            candidate=_candidate(subject_kind="fiction"),
        )
        == ""
    )


def test_advanced_context_excludes_factual_and_malformed_partner_states() -> None:
    build = _required_symbol(
        "plugins.qzone_journal.advanced_fiction_context",
        "build_advanced_fiction_context",
    )
    arc = _fiction_arc(
        partner_states={
            "tenma_tsukasa": {
                "kind": "fiction",
                "current_state": "在彩排",
                "availability": "normal",
            },
            "real_friend": {
                "kind": "factual",
                "current_state": "真人不得入稿",
                "user_id": "384801062",
            },
            "broken": "not-a-dict",
            "missing_kind": {"current_state": "无 kind"},
            "empty_kind": {"kind": "", "current_state": "空 kind"},
            "wrong_case": {"kind": "Fiction", "current_state": "大小写不匹配"},
        }
    )
    text = build(
        advanced_enabled=True,
        arc=arc,
        candidate=_candidate(),
    )
    assert "tenma_tsukasa" in text
    assert "在彩排" in text
    assert "real_friend" not in text
    assert "真人不得入稿" not in text
    assert "broken" not in text
    assert "missing_kind" not in text
    assert "empty_kind" not in text
    assert "wrong_case" not in text
    assert "384801062" not in text


def test_advanced_context_is_deterministic_and_hard_bounded() -> None:
    build = _required_symbol(
        "plugins.qzone_journal.advanced_fiction_context",
        "build_advanced_fiction_context",
    )
    long_thread = "thread-" + ("x" * 400)
    partners = {
        f"partner_{i:02d}": {
            "kind": "fiction",
            "current_state": f"state-{i}-" + ("y" * 200),
            "availability": "busy",
            "recent_events": [f"event-{i}-" + ("z" * 200) for _ in range(8)],
        }
        for i in range(20, 0, -1)
    }
    arc = _fiction_arc(
        title="T" * 300,
        stage="S" * 200,
        open_threads=[long_thread for _ in range(30)],
        partner_states=partners,
    )
    first = build(advanced_enabled=True, arc=arc, candidate=_candidate())
    second = build(advanced_enabled=True, arc=arc, candidate=_candidate())
    assert first == second
    assert first
    assert len(first) <= 1600
    assert "\n" in first
    assert "开放线索：\n- " in first
    assert "虚构伙伴状态：\n- " in first
    # Deterministic partner ordering by entity_id ascending.
    assert first.index("partner_01") < first.index("partner_02")
    assert "partner_20" not in first or first.count("partner_") <= 12


def test_advanced_context_scrubs_qq_like_identifiers_and_id_fields() -> None:
    build = _required_symbol(
        "plugins.qzone_journal.advanced_fiction_context",
        "build_advanced_fiction_context",
    )
    arc = _fiction_arc(
        title="和 384801062 一起排练",
        stage="prep",
        open_threads=[
            "群 963737802 里的彩排提醒不要写进空间",
            "qq_384801062 与 user123456 不得出现",
            "user_id=alice group_id:dev-room p_skey=abc token=abc",
        ],
        partner_states={
            "kusanagi_nene": {
                "kind": "fiction",
                "current_state": "用户 123456789 的状态串；qq_384801062 user123456",
                "availability": "normal",
                "user_id": "384801062",
                "group_id": 963737802,
                "recent_events": [
                    "联系 10001 后继续练",
                    "user_id=alice 与 p_skey=abc token=abc",
                    "group_id:dev-room",
                ],
            },
            # Non-string keys must still resolve after str conversion.
            42: {
                "kind": "fiction",
                "current_state": "整数键伙伴仍应可见",
                "availability": "normal",
            },
        },
    )
    text = build(advanced_enabled=True, arc=arc, candidate=_candidate())
    assert text
    for token in (
        "384801062",
        "963737802",
        "123456789",
        "10001",
        "qq_384801062",
        "user123456",
        "user_id=alice",
        "group_id:dev-room",
        "p_skey=abc",
        "token=abc",
    ):
        assert token not in text, f"prompt must scrub {token!r}"
    assert "user_id" not in text
    assert "group_id" not in text
    assert "p_skey" not in text
    assert "token=" not in text
    assert "kusanagi_nene" in text
    assert "整数键伙伴仍应可见" in text
    # Ordinary alphanumeric fiction ids stay intact.
    assert "kusanagi_nene" in text


def test_public_safety_normalizes_unicode_and_redacts_whole_secret_values() -> None:
    scrub = _required_symbol(
        "plugins.qzone_journal.public_safety",
        "scrub_public_text",
    )
    zwsp_qq = "\u200b".join("384801062")
    value = (
        "token＝supersecret，user_id＝alice；token\u200b=hidden，"
        "ｔｏｋｅｎ＝wide-secret；"
        f"编号 qq_{zwsp_qq}；authorization: Bearer eyJhbGciOiJIUzI1NiJ9.signature，"
        "然后继续排练。"
    )

    cleaned = scrub(value)

    lowered = cleaned.lower()
    for forbidden in (
        "supersecret",
        "alice",
        "hidden",
        "wide-secret",
        "384801062",
        "eyjhbgcioijiuzi1nij9.signature",
        "authorization",
    ):
        assert forbidden not in lowered
    assert "\u200b" not in cleaned
    assert re.search(r"(?<!\d)\d{5,16}(?!\d)", cleaned) is None
    assert "然后继续排练" in cleaned


async def test_composer_includes_advanced_context_only_when_non_empty() -> None:
    JournalComposer = _required_symbol(
        "plugins.qzone_journal.composer",
        "JournalComposer",
    )
    candidate = _candidate()
    llm = _LLMProbe({"text": candidate.summary})
    composer = JournalComposer(llm, max_chars=120)

    await composer.compose(candidate, day_narrative="今日叙事")
    bare = _request_text(llm.calls[0])
    assert "虚构世界书" not in bare and "fiction-worldbook" not in bare.lower()

    llm.calls.clear()
    await composer.compose(
        candidate,
        day_narrative="今日叙事",
        advanced_fiction_context="标题：舞台周\n阶段：rehearsal\n伙伴 tenma_tsukasa：加练中",
    )
    enriched = _request_text(llm.calls[0])
    assert "舞台周" in enriched
    assert "tenma_tsukasa" in enriched
    assert any(
        marker in enriched
        for marker in ("虚构世界书", "fiction-worldbook", "引用材料", "非指令")
    )
    for red_line in ("真人事实", "私聊内容", "线下行为"):
        assert red_line in enriched


async def test_composer_scrubs_sensitive_material_from_prompt_and_final_draft() -> None:
    JournalComposer = _required_symbol(
        "plugins.qzone_journal.composer",
        "JournalComposer",
    )
    candidate = _candidate(
        summary="和 qq_384801062 联系后继续排练，token=source-secret",
    )
    llm = _LLMProbe({
        "text": "我联系了 user123456，p_skey=output-secret，然后继续排练。",
    })

    text = await JournalComposer(llm, max_chars=160).compose(
        candidate,
        day_narrative="group_id:dev-room 的今日叙事",
        advanced_fiction_context="伙伴 user_id=alice 在 qq_963737802 彩排",
    )

    prompt = _request_text(llm.calls[0])
    for blob in (prompt, text):
        lowered = blob.lower()
        for forbidden in (
            "384801062",
            "963737802",
            "user123456",
            "token=source-secret",
            "p_skey=output-secret",
            "group_id:dev-room",
            "user_id=alice",
        ):
            assert forbidden not in lowered
        assert re.search(r"(?<!\d)\d{5,16}(?!\d)", blob) is None
    assert "继续排练" in text


async def test_store_enqueue_persists_review_provenance_fields(tmp_path: Path) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        provenance = {
            "schema_version": 1,
            "arc_id": "arc-main",
            "arc_revision": 3,
            "arc_stage": "rehearsal",
            "advanced_context_included": True,
            "fiction_partner_entity_ids": ["tenma_tsukasa"],
        }
        draft = await store.enqueue(
            dedupe_key="prov-1",
            event_date=date(2026, 7, 16),
            source="event_replan",
            content="带出处的草稿",
            stable_id="arc-main:event-1",
            subject_kind="fiction",
            privacy="public",
            salience=0.91,
            source_summary="今天把卡住的排练段落理顺了",
            provenance=provenance,
        )
        loaded = await store.get(draft.draft_id)
    finally:
        await store.close()

    assert loaded is not None
    assert loaded.stable_id == "arc-main:event-1"
    assert loaded.subject_kind == "fiction"
    assert loaded.privacy == "public"
    assert loaded.salience == pytest.approx(0.91)
    assert loaded.source_summary == "今天把卡住的排练段落理顺了"
    assert loaded.provenance_json is not None
    parsed = json.loads(loaded.provenance_json)
    assert parsed["schema_version"] == 1
    assert parsed["arc_id"] == "arc-main"
    assert parsed["advanced_context_included"] is True
    assert parsed["fiction_partner_entity_ids"] == ["tenma_tsukasa"]
    body = json.dumps(parsed)
    assert "p_skey" not in body
    assert "cookie" not in body.lower()


async def test_store_enqueue_rejects_secret_laden_provenance(tmp_path: Path) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        with pytest.raises(ValueError):
            await store.enqueue(
                dedupe_key="bad-prov",
                event_date=date(2026, 7, 16),
                source="event_replan",
                content="不应入库",
                provenance={
                    "schema_version": 1,
                    "arc_id": "arc-main",
                    "p_skey": "secret",
                },
            )
        with pytest.raises(ValueError):
            await store.enqueue(
                dedupe_key="bad-social",
                event_date=date(2026, 7, 16),
                source="event_replan",
                content="不应入库",
                provenance={
                    "schema_version": 1,
                    "arc_id": "arc-main",
                    "social_narrative": "raw group dump",
                },
            )
        # Secret/id values hidden under *allowed* keys must not persist.
        with pytest.raises(ValueError):
            await store.enqueue(
                dedupe_key="bad-arc-secret",
                event_date=date(2026, 7, 16),
                source="event_replan",
                content="不应入库",
                provenance={
                    "schema_version": 1,
                    "arc_id": "token=abc",
                    "arc_stage": "rehearsal",
                    "advanced_context_included": True,
                },
            )
        with pytest.raises(ValueError):
            await store.enqueue(
                dedupe_key="bad-stage-assign",
                event_date=date(2026, 7, 16),
                source="event_replan",
                content="不应入库",
                provenance={
                    "schema_version": 1,
                    "arc_id": "arc-main",
                    "arc_stage": "p_skey=abc",
                    "advanced_context_included": True,
                },
            )
        with pytest.raises(ValueError):
            await store.enqueue(
                dedupe_key="bad-partner-secret",
                event_date=date(2026, 7, 16),
                source="event_replan",
                content="不应入库",
                provenance={
                    "schema_version": 1,
                    "arc_id": "arc-main",
                    "advanced_context_included": True,
                    "fiction_partner_entity_ids": ["user_id=alice"],
                },
            )
        # Truthy/falsey strings are not real bools.
        for fake_bool in ("true", "false", "1", "0", 1, 0):
            with pytest.raises(ValueError):
                await store.enqueue(
                    dedupe_key=f"bad-bool-{fake_bool!r}",
                    event_date=date(2026, 7, 16),
                    source="event_replan",
                    content="不应入库",
                    provenance={
                        "schema_version": 1,
                        "arc_id": "arc-main",
                        "advanced_context_included": fake_bool,
                    },
                )
        with pytest.raises(ValueError):
            await store.enqueue(
                dedupe_key="bad-stable-qq",
                event_date=date(2026, 7, 16),
                source="event_replan",
                content="不应入库",
                stable_id="384801062",
            )
        with pytest.raises(ValueError):
            await store.enqueue(
                dedupe_key="bad-summary-secret",
                event_date=date(2026, 7, 16),
                source="event_replan",
                content="不应入库",
                source_summary="token=abc 的摘要",
            )
        # Embedded QQ digits are scrubbed; no raw digit run remains.
        scrubbed = await store.enqueue(
            dedupe_key="scrub-summary",
            event_date=date(2026, 7, 16),
            source="event_replan",
            content="可入库",
            stable_id="arc-main:event-384801062",
            source_summary="今天和 qq_384801062 以及 user123456 排练",
            provenance={
                "schema_version": 1,
                "arc_id": "arc-main-384801062",
                "arc_revision": 1,
                "arc_stage": "prep-963737802",
                "advanced_context_included": True,
                "fiction_partner_entity_ids": [
                    "tenma_tsukasa",
                    "partner_384801062",
                    "user123456",
                ],
            },
        )
        assert scrubbed.stable_id is not None
        assert scrubbed.source_summary is not None
        assert scrubbed.provenance_json is not None
        for field in (
            scrubbed.stable_id,
            scrubbed.source_summary,
            scrubbed.provenance_json,
        ):
            for token in (
                "384801062",
                "963737802",
                "user123456",
                "qq_384801062",
            ):
                assert token not in field, f"{field!r} still holds {token!r}"
            assert re.search(r"(?<!\d)\d{5,16}(?!\d)", field) is None
        parsed = json.loads(scrubbed.provenance_json)
        assert parsed["advanced_context_included"] is True
        assert "tenma_tsukasa" in parsed["fiction_partner_entity_ids"]
        # Ordinary alphanumeric fiction IDs intact.
        assert "tenma_tsukasa" in parsed["fiction_partner_entity_ids"]
        safe_body = await store.enqueue(
            dedupe_key="scrub-body",
            event_date=date(2026, 7, 16),
            source="event_replan",
            content="联系 qq_384801062，token=body-secret，然后继续排练。",
        )
        for forbidden in ("384801062", "token=body-secret"):
            assert forbidden not in safe_body.content.lower()
        assert "继续排练" in safe_body.content
        # Compatibility: callers without provenance still work.
        draft = await store.enqueue(
            dedupe_key="legacy-ok",
            event_date=date(2026, 7, 16),
            source="event_replan",
            content="兼容旧调用",
        )
        assert draft.stable_id is None
        assert draft.provenance_json is None
    finally:
        await store.close()


async def test_store_migration_v2_to_v3_adds_nullable_provenance_columns(
    tmp_path: Path,
) -> None:
    """Bootstrap a pure v2 DB in temp storage, then upgrade via JournalStore.init."""
    db_path = tmp_path / "legacy_v2.db"

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
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return row is not None

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
                "qzd_legacy",
                "legacy-key",
                "2026-07-15",
                "event_replan",
                "旧行无出处",
                "2026-07-15T00:00:00+00:00",
                "2026-07-15T00:00:00+00:00",
            ),
        )
        connection.commit()
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == 2

    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    store = JournalStore(db_path)
    await store.init()
    try:
        legacy = await store.get("qzd_legacy")
        assert legacy is not None
        assert legacy.content == "旧行无出处"
        assert legacy.stable_id is None
        assert legacy.provenance_json is None
    finally:
        await store.close()

    with sqlite3.connect(db_path) as connection:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(qzone_journal_drafts)")
        }
        versions = [
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM _omubot_schema_migrations ORDER BY version"
            )
        ]

    assert user_version == 6
    assert versions == [1, 2, 3, 4, 5, 6]
    for col in (
        "stable_id",
        "subject_kind",
        "privacy",
        "salience",
        "source_summary",
        "provenance_json",
    ):
        assert col in columns
    review_table = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='qzone_journal_review_decisions'"
        )
    ]
    assert review_table == ["qzone_journal_review_decisions"]


async def test_manual_resolution_note_uses_public_safety_scrub(tmp_path: Path) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        draft = await store.enqueue(
            dedupe_key="note-scrub",
            event_date=date(2026, 7, 16),
            source="event_replan",
            content="待确认草稿",
        )
        await store.approve(draft.draft_id)
        claimed = await store.claim_for_publish(
            draft.draft_id,
            now=datetime(2026, 7, 16, 12, 0, tzinfo=_CST),
        )
        assert claimed is not None
        await store.mark_unknown(draft.draft_id, reason="ambiguous_response")
        await store.confirm_not_published(
            draft.draft_id,
            note="查看 qq_384801062，token=note-secret，然后确认未发布。",
        )
        audits = await store.list_manual_resolutions(draft_id=draft.draft_id)
    finally:
        await store.close()

    assert len(audits) == 1
    note = str(audits[0]["note"])
    assert "384801062" not in note
    assert "token=note-secret" not in note.lower()
    assert "然后确认未发布" in note


async def test_admin_serialization_exposes_review_provenance_bundle(
    tmp_path: Path,
) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    plugin = _enabled_plugin(advanced_enabled=True)
    ctx = _plugin_context(tmp_path, _LLMProbe(), events=[_event_payload()])
    await plugin.on_startup(ctx)
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        draft = await store.enqueue(
            dedupe_key="admin-prov",
            event_date=datetime.now(_CST).date(),
            source="event_replan",
            content="待审带出处",
            stable_id="arc-main:event-9",
            subject_kind="fiction",
            privacy="public",
            salience=0.88,
            source_summary="摘要",
            provenance={
                "schema_version": 1,
                "arc_id": "arc-main",
                "arc_revision": 2,
                "arc_stage": "rehearsal",
                "advanced_context_included": True,
                "fiction_partner_entity_ids": ["tenma_tsukasa"],
            },
        )
    finally:
        await store.close()

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
            listed = await client.get(f"{base_path}/drafts")
            health = await client.get(f"{base_path}/health")
        assert listed.status_code == 200
        payload = listed.json()
        rows = payload.get("drafts") if isinstance(payload, dict) else payload
        assert isinstance(rows, list)
        row = next(r for r in rows if r.get("draft_id") == draft.draft_id)
        # Nested review bundle is mandatory; top-level compat fields may remain.
        assert "review" in row
        review = row["review"]
        assert isinstance(review, dict)
        assert review["stable_id"] == "arc-main:event-9"
        assert review["subject_kind"] == "fiction"
        assert review["privacy"] == "public"
        assert review["salience"] == pytest.approx(0.88)
        assert review["source_summary"] == "摘要"
        prov = review.get("provenance")
        assert isinstance(prov, dict)
        assert prov["schema_version"] == 1
        assert prov["arc_id"] == "arc-main"
        assert prov["arc_revision"] == 2
        assert prov["arc_stage"] == "rehearsal"
        assert prov["advanced_context_included"] is True
        assert prov["fiction_partner_entity_ids"] == ["tenma_tsukasa"]
        # Existing top-level compatibility fields when present.
        if "stable_id" in row:
            assert row["stable_id"] == "arc-main:event-9"
        if "subject_kind" in row:
            assert row["subject_kind"] == "fiction"

        assert health.status_code == 200
        body = health.json()
        assert body["advanced_enabled"] is True
        assert body["manual_review"] is True
        assert body["wire_profile_id"] == "qzone-text-v1-unverified"
        assert body["profile_validated"] is False
        assert body["salience_threshold"] == pytest.approx(0.7)
        assert "event_replan" in body["allowed_sources"]
        assert "dream_reflection" in body["allowed_sources"]
    finally:
        await plugin.on_shutdown(ctx)


async def test_tick_advanced_path_enriches_composer_and_persists_provenance(
    tmp_path: Path,
) -> None:
    llm = _LLMProbe({"text": "今天终于把卡住的排练段落理顺了。"})
    plugin = _enabled_plugin(advanced_enabled=True)
    arc = _fiction_arc(
        partner_states={
            "tenma_tsukasa": {
                "kind": "fiction",
                "current_state": "加练中",
                "availability": "normal",
            },
            "real_person": {
                "kind": "factual",
                "current_state": "不得进入",
                "user_id": "384801062",
            },
        }
    )
    ctx = _plugin_context(
        tmp_path,
        llm,
        arc=arc,
        events=[_event_payload()],
    )
    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    assert len(llm.calls) == 1, "duplicate ticks must still dedupe before LLM"
    prompt = _request_text(llm.calls[0])
    assert "tenma_tsukasa" in prompt
    assert "real_person" not in prompt
    assert "384801062" not in prompt
    assert any(
        marker in prompt
        for marker in ("虚构世界书", "fiction-worldbook", "引用材料", "非指令")
    )

    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        row = connection.execute(
            """
            SELECT stable_id, subject_kind, privacy, salience,
                   source_summary, provenance_json, content, status
            FROM qzone_journal_drafts
            """
        ).fetchone()

    assert row is not None
    stable_id, subject_kind, privacy, salience, source_summary, provenance_json, content, status = (
        row
    )
    assert stable_id == "arc-main:event-1"
    assert subject_kind == "fiction"
    assert privacy == "public"
    assert float(salience) == pytest.approx(0.9)
    assert source_summary
    assert status == "pending_review"
    assert content
    prov = json.loads(str(provenance_json))
    assert prov["schema_version"] == 1
    assert prov["arc_id"] == "arc-main"
    assert prov["advanced_context_included"] is True
    assert "tenma_tsukasa" in prov["fiction_partner_entity_ids"]
    assert "real_person" not in prov["fiction_partner_entity_ids"]
    blob = json.dumps(prov, ensure_ascii=False)
    for forbidden in ("p_skey", "cookie", "token", "credential", "social_narrative"):
        assert forbidden not in blob.lower()


async def test_tick_advanced_off_matches_legacy_empty_context_path(
    tmp_path: Path,
) -> None:
    llm = _LLMProbe({"text": "今天终于把卡住的排练段落理顺了。"})
    plugin = _enabled_plugin(advanced_enabled=False)
    ctx = _plugin_context(tmp_path, llm, events=[_event_payload()])
    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    assert len(llm.calls) == 1
    prompt = _request_text(llm.calls[0])
    assert "tenma_tsukasa" not in prompt
    assert "虚构世界书" not in prompt
    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        row = connection.execute(
            "SELECT subject_kind, provenance_json FROM qzone_journal_drafts"
        ).fetchone()
    assert row is not None
    # Provenance may still be recorded for operator review when advanced is off.
    if row[1]:
        prov = json.loads(row[1])
        assert prov.get("advanced_context_included") is False


async def test_tick_rejects_factual_subject_even_when_advanced_enabled(
    tmp_path: Path,
) -> None:
    llm = _LLMProbe()
    plugin = _enabled_plugin(advanced_enabled=True)
    ctx = _plugin_context(
        tmp_path,
        llm,
        events=[_event_payload(subject_kind="factual", summary="真人线下活动")],
    )
    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        count = int(
            connection.execute("SELECT COUNT(*) FROM qzone_journal_drafts").fetchone()[0]
        )
    assert count == 0
    assert llm.calls == []


async def test_tick_skips_poison_review_fields_and_continues_later_candidates(
    tmp_path: Path,
) -> None:
    """Contract 4: poison review fields reject before LLM; only the clean candidate drafts.

    A ranked poison candidate (unsafe stable_id) must not call the LLM and must not
    consume the draft slot; the subsequent clean candidate becomes the sole draft.
    """
    llm = _LLMProbe({"text": "安全草稿"})
    plugin = _enabled_plugin(advanced_enabled=True)
    # Higher salience poison first so ranking would prefer it if validation were late.
    ctx = _plugin_context(
        tmp_path,
        llm,
        events=[
            {
                **_event_payload(
                    event_id="384801062",
                    summary="带纯编号身份的高显著毒事件",
                ),
                "salience": 0.99,
            },
            {
                **_event_payload(
                    event_id="arc-main:event-clean",
                    summary="后续健康事件",
                ),
                "salience": 0.8,
            },
        ],
    )

    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    assert len(llm.calls) == 1, (
        "poison review-field candidates must be rejected before LLM; "
        f"only the clean candidate may compose (got {len(llm.calls)} calls)"
    )
    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        rows = connection.execute(
            "SELECT stable_id, source_summary, content FROM qzone_journal_drafts"
        ).fetchall()
    assert rows == [
        ("arc-main:event-clean", "后续健康事件", "虚构故事里，安全草稿")
    ]
    assert plugin._selection_decisions.get("reject_review_field", 0) >= 1
    assert plugin._selection_decisions.get("accept", 0) == 1


async def test_advanced_path_never_acquires_credentials_or_posts_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_calls: list[str] = []
    http_calls: list[str] = []

    class _ForbiddenCreds:
        async def acquire(self, *args: Any, **kwargs: Any) -> Any:
            credential_calls.append("acquire")
            raise AssertionError("advanced path must not acquire credentials")

    class _ForbiddenTransport:
        async def publish(self, *args: Any, **kwargs: Any) -> Any:
            http_calls.append("publish")
            raise AssertionError("advanced path must not post HTTP")

        async def aclose(self) -> None:
            return None

    NapCatCredentialSource = _required_symbol(
        "plugins.qzone_journal.transport",
        "NapCatCredentialSource",
    )
    QZoneTransport = _required_symbol(
        "plugins.qzone_journal.transport",
        "QZoneTransport",
    )
    monkeypatch.setattr(
        "plugins.qzone_journal.plugin.NapCatCredentialSource",
        lambda *a, **k: _ForbiddenCreds(),
    )
    monkeypatch.setattr(
        "plugins.qzone_journal.plugin.QZoneTransport",
        lambda *a, **k: _ForbiddenTransport(),
    )
    # Also guard the real classes if plugin already imported differently.
    del NapCatCredentialSource, QZoneTransport

    llm = _LLMProbe({"text": "今天终于把卡住的排练段落理顺了。"})
    plugin = _enabled_plugin(advanced_enabled=True)
    ctx = _plugin_context(tmp_path, llm, events=[_event_payload()])
    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
        route = _single_admin_route(plugin)
        app = FastAPI()
        base_path = route.path.rstrip("/")
        app.include_router(route.router, prefix=base_path)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            health = await client.get(f"{base_path}/health")
            drafts = await client.get(f"{base_path}/drafts")
        assert health.status_code == 200
        assert drafts.status_code == 200
        assert health.json()["profile_validated"] is False
        assert health.json()["advanced_enabled"] is True
    finally:
        await plugin.on_shutdown(ctx)

    assert credential_calls == []
    assert http_calls == []
    assert len(llm.calls) == 1


def test_builtin_wire_profile_remains_unvalidated() -> None:
    profile = _required_symbol(
        "plugins.qzone_journal.delivery",
        "BUILTIN_WIRE_PROFILE",
    )
    assert profile.validated is False
    assert profile.profile_id == "qzone-text-v1-unverified"
