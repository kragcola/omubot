"""RED contracts for QZone Journal composition, runtime, and review routes.

QZone modules are loaded inside test bodies so an unfinished public surface
fails as a focused assertion rather than interrupting pytest collection.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI

from kernel.types import AdminRoute, PluginContext
from services.llm.llm_request import LLMRequest

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
    assert module is not None, f"{module_name} must exist for the runtime contract"
    symbol = getattr(module, symbol_name, None)
    assert symbol is not None, f"{module_name} must expose {symbol_name}"
    return symbol


def _candidate(*, summary: str = "今天把卡住的排练段落理顺了") -> Any:
    CandidateEvent = _required_symbol(
        "plugins.qzone_journal.selector",
        "CandidateEvent",
    )
    return CandidateEvent(
        source="event_replan",
        event_date=datetime.now(_CST).date(),
        stable_id="arc-main:event-1",
        subject_kind="fiction",
        privacy="public",
        salience=0.9,
        summary=summary,
    )


def _event_payload() -> dict[str, Any]:
    return {
        "source": "event_replan",
        "date": datetime.now(_CST).date().isoformat(),
        "event_id": "arc-main:event-1",
        "summary": "今天把卡住的排练段落理顺了",
        "salience": 0.9,
        "subject_kind": "fiction",
        "privacy": "public",
    }


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


class _LLMProbe:
    def __init__(self, result: Any = None, *, error: Exception | None = None) -> None:
        self.result = result if result is not None else {"text": "成稿"}
        self.error = error
        self.calls: list[Any] = []

    async def _invoke(self, request: Any) -> Any:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.result

    async def __call__(self, request: Any) -> Any:
        return await self._invoke(request)

    async def _call(self, request: Any) -> Any:
        return await self._invoke(request)


class _StoryArcStore:
    def __init__(self, events: list[dict[str, Any]] | None) -> None:
        self._arc = (
            SimpleNamespace(last_events=[dict(event) for event in events])
            if events is not None
            else None
        )

    def load_active(self) -> Any:
        return self._arc


def _plugin_context(
    tmp_path: Path,
    llm: _LLMProbe,
    *,
    events: list[dict[str, Any]] | None = None,
    day_narrative: str = "和平常一样的一天",
) -> PluginContext:
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
        story_arc_store=_StoryArcStore(events),
    )


def _enabled_plugin() -> Any:
    PluginConfig = _required_symbol("plugins.qzone_journal.plugin", "PluginConfig")
    Plugin = _required_symbol("plugins.qzone_journal.plugin", "QZoneJournalPlugin")
    return Plugin(
        config=PluginConfig(
            enabled=True,
            dry_run=True,
            allow_live_publish=False,
            manual_review=True,
        )
    )


def _single_admin_route(plugin: Any) -> AdminRoute:
    register = getattr(plugin, "register_admin_routes", None)
    assert callable(register), "QZoneJournalPlugin must expose register_admin_routes()"
    routes = register()
    assert isinstance(routes, list), "register_admin_routes() must return a list"
    assert len(routes) == 1, "enabled QZone Journal must register one review router"
    route = routes[0]
    assert isinstance(route, AdminRoute)
    assert route.path.startswith("/")
    assert route.router is not None
    return route


async def test_composer_uses_typed_request_with_first_person_safety_prompt() -> None:
    JournalComposer = _required_symbol(
        "plugins.qzone_journal.composer",
        "JournalComposer",
    )
    candidate = _candidate()
    llm = _LLMProbe({"text": candidate.summary})

    text = await JournalComposer(llm, max_chars=120).compose(candidate)

    assert text == f"虚构故事里，{candidate.summary}"
    assert len(llm.calls) == 1
    request = llm.calls[0]
    assert isinstance(request, LLMRequest)
    assert request.task == "qzone_journal_compose"
    prompt = _request_text(request)
    assert "第一人称" in prompt
    for forbidden_subject in ("真人事实", "私聊内容", "线下行为"):
        assert forbidden_subject in prompt
    assert any(word in prompt for word in ("禁止", "不得", "不要"))


@pytest.mark.parametrize(
    "raw_text",
    [
        pytest.param("  “  今天   把排练段落  理顺了  ”  ", id="chinese-quotes"),
        pytest.param('  "  今天   把排练段落  理顺了  "  ', id="ascii-quotes"),
    ],
)
async def test_composer_strips_wrapping_quotes_collapses_whitespace_and_truncates(
    raw_text: str,
) -> None:
    JournalComposer = _required_symbol(
        "plugins.qzone_journal.composer",
        "JournalComposer",
    )
    llm = _LLMProbe({"text": raw_text})

    text = await JournalComposer(llm, max_chars=8).compose(_candidate())

    assert text == "虚构故事里，今天"
    assert len(text) == 8


@pytest.mark.parametrize(
    ("result", "error"),
    [
        pytest.param({"text": "   "}, None, id="empty-output"),
        pytest.param(None, RuntimeError("provider unavailable"), id="llm-error"),
    ],
)
async def test_composer_empty_or_failed_llm_uses_summary_only_fallback(
    result: Any,
    error: Exception | None,
) -> None:
    JournalComposer = _required_symbol(
        "plugins.qzone_journal.composer",
        "JournalComposer",
    )
    candidate = _candidate(summary="把排练里卡住的八拍重新理顺了")
    llm = _LLMProbe(result, error=error)

    text = await JournalComposer(llm, max_chars=80).compose(candidate)

    assert text == f"虚构故事里，{candidate.summary}"
    assert len(llm.calls) == 1


async def test_enabled_startup_initializes_storage_dir_journal_database(
    tmp_path: Path,
) -> None:
    plugin = _enabled_plugin()
    ctx = _plugin_context(tmp_path, _LLMProbe())
    db_path = tmp_path / "qzone_journal.db"

    await plugin.on_startup(ctx)
    try:
        assert db_path.is_file(), "enabled startup must create qzone_journal.db"
    finally:
        await plugin.on_shutdown(ctx)

    with sqlite3.connect(db_path) as connection:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'qzone_journal_drafts'"
        ).fetchone()
        audit = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'qzone_journal_manual_resolutions'"
        ).fetchone()

    assert user_version == 6
    assert table == ("qzone_journal_drafts",)
    assert audit == ("qzone_journal_manual_resolutions",)


async def test_significant_tick_creates_one_review_draft_and_dedupes_before_llm(
    tmp_path: Path,
) -> None:
    llm = _LLMProbe({"text": "今天终于把卡住的排练段落理顺了。"})
    plugin = _enabled_plugin()
    ctx = _plugin_context(tmp_path, llm, events=[_event_payload()])
    db_path = tmp_path / "qzone_journal.db"

    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    assert db_path.is_file(), "enabled tick requires the initialized journal store"
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT source, content, status FROM qzone_journal_drafts"
        ).fetchall()

    assert rows == [
        (
            "event_replan",
                "虚构故事里，今天终于把卡住的排练段落理顺了。",
            "pending_review",
        )
    ]
    assert len(llm.calls) == 1, "duplicate ticks must dedupe before composing again"
    assert isinstance(llm.calls[0], LLMRequest)


async def test_flat_schedule_only_tick_creates_no_draft_and_makes_no_llm_call(
    tmp_path: Path,
) -> None:
    llm = _LLMProbe()
    plugin = _enabled_plugin()
    ctx = _plugin_context(
        tmp_path,
        llm,
        events=None,
        day_narrative="和平常一样的一天，没有值得单独记录的变化。",
    )
    db_path = tmp_path / "qzone_journal.db"

    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    assert db_path.is_file(), "enabled startup must initialize an empty outbox"
    with sqlite3.connect(db_path) as connection:
        count = int(
            connection.execute("SELECT COUNT(*) FROM qzone_journal_drafts").fetchone()[0]
        )

    assert count == 0
    assert llm.calls == []


async def test_dream_reflection_without_explicit_public_subject_is_rejected(
    tmp_path: Path,
) -> None:
    llm = _LLMProbe()
    plugin = _enabled_plugin()
    event = {
        "source": "dream_reflection",
        "date": datetime.now(_CST).date().isoformat(),
        "summary": "今天和群友一起经历了很多事",
        "salience": 0.95,
    }
    ctx = _plugin_context(tmp_path, llm, events=[event])

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


async def test_admin_review_routes_list_approve_and_reject_without_publishing(
    tmp_path: Path,
) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    plugin = _enabled_plugin()
    ctx = _plugin_context(tmp_path, _LLMProbe())
    db_path = tmp_path / "qzone_journal.db"

    await plugin.on_startup(ctx)
    seed_store = JournalStore(db_path)
    await seed_store.init()
    try:
        first = await seed_store.enqueue(
            dedupe_key="admin-review-a",
            event_date=datetime.now(_CST).date(),
            source="event_replan",
            content="第一条待审草稿",
        )
        second = await seed_store.enqueue(
            dedupe_key="admin-review-b",
            event_date=datetime.now(_CST).date(),
            source="dream_reflection",
            content="第二条待审草稿",
        )
    finally:
        await seed_store.close()

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
            approved = await client.post(f"{base_path}/{first.draft_id}/approve")
            rejected = await client.post(
                f"{base_path}/{second.draft_id}/reject",
                json={"note": "题材不合适"},
            )
            illegal = await client.post(f"{base_path}/{second.draft_id}/approve")
            missing = await client.post(f"{base_path}/missing-draft/approve")

        assert listed.status_code == 200
        payload = listed.json()
        if isinstance(payload, dict):
            payload = payload.get("drafts")
        assert isinstance(payload, list), "GET /drafts must return a draft list"
        listed_ids = {str(row.get("draft_id")) for row in payload if isinstance(row, dict)}
        assert {first.draft_id, second.draft_id} <= listed_ids
        assert approved.status_code == 200
        assert rejected.status_code == 200
        assert illegal.status_code == 409
        assert missing.status_code == 404
    finally:
        await plugin.on_shutdown(ctx)

    with sqlite3.connect(db_path) as connection:
        statuses = dict(
            connection.execute(
                "SELECT draft_id, status FROM qzone_journal_drafts ORDER BY draft_id"
            ).fetchall()
        )

    assert statuses[first.draft_id] == "approved"
    assert statuses[second.draft_id] == "rejected"
    assert "published" not in statuses.values()


async def test_admin_approve_defaults_to_dry_run_even_when_live_flags_are_open(
    tmp_path: Path,
) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    PluginConfig = _required_symbol("plugins.qzone_journal.plugin", "PluginConfig")
    Plugin = _required_symbol("plugins.qzone_journal.plugin", "QZoneJournalPlugin")
    plugin = Plugin(
        config=PluginConfig(
            enabled=True,
            dry_run=False,
            allow_live_publish=True,
            allowed_live_uins=["123456789"],
            manual_review=True,
        )
    )
    ctx = _plugin_context(tmp_path, _LLMProbe())
    await plugin.on_startup(ctx)
    seed = JournalStore(tmp_path / "qzone_journal.db")
    await seed.init()
    try:
        draft = await seed.enqueue(
            dedupe_key="admin-approve-explicit-scope",
            event_date=datetime.now(_CST).date(),
            source="event_replan",
            content="虚构故事里，完成了一段排练。",
            stable_id="admin-approve-explicit-scope",
            subject_kind="fiction",
            privacy="public",
            salience=0.9,
            source_summary="完成了一段排练",
        )
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
            response = await client.post(f"{base_path}/{draft.draft_id}/approve")
    finally:
        await plugin.on_shutdown(ctx)

    assert response.status_code == 200
    assert response.json()["approval_scope"] == "dry_run"


async def test_admin_live_approval_requires_ready_live_gate(tmp_path: Path) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    PluginConfig = _required_symbol("plugins.qzone_journal.plugin", "PluginConfig")
    Plugin = _required_symbol("plugins.qzone_journal.plugin", "QZoneJournalPlugin")
    plugin = Plugin(
        config=PluginConfig(
            enabled=True,
            dry_run=False,
            allow_live_publish=True,
            allowed_live_uins=["123456789"],
            manual_review=True,
        )
    )
    ctx = _plugin_context(tmp_path, _LLMProbe())
    await plugin.on_startup(ctx)
    seed = JournalStore(tmp_path / "qzone_journal.db")
    await seed.init()
    try:
        draft = await seed.enqueue(
            dedupe_key="admin-live-approval-gate",
            event_date=datetime.now(_CST).date(),
            source="event_replan",
            content="虚构故事里，完成了一段排练。",
            stable_id="admin-live-approval-gate",
            subject_kind="fiction",
            privacy="public",
            salience=0.9,
            source_summary="完成了一段排练",
        )
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
            response = await client.post(
                f"{base_path}/{draft.draft_id}/approve",
                json={"approval_scope": "live"},
            )
        current = await plugin._require_store().get(draft.draft_id)
    finally:
        await plugin.on_shutdown(ctx)

    assert response.status_code == 409
    assert "live approval gate" in str(response.json()).lower()
    assert current is not None and current.status == "pending_review"


async def test_admin_manual_resolution_routes_for_unknown_drafts(
    tmp_path: Path,
) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    plugin = _enabled_plugin()
    ctx = _plugin_context(tmp_path, _LLMProbe())
    db_path = tmp_path / "qzone_journal.db"
    now = datetime(2026, 7, 15, 12, 0, tzinfo=_CST)

    await plugin.on_startup(ctx)
    # Higher daily limit only for fixture setup so two unknown drafts can coexist.
    seed = JournalStore(db_path, max_posts_per_day=3)
    await seed.init()
    try:
        pub = await seed.approve(
            (
                await seed.enqueue(
                    dedupe_key="manual-route-pub",
                    event_date=now.date(),
                    source="event_replan",
                    content="人工确认已发",
                )
            ).draft_id
        )
        rel = await seed.approve(
            (
                await seed.enqueue(
                    dedupe_key="manual-route-not",
                    event_date=now.date(),
                    source="event_replan",
                    content="人工确认未发",
                )
            ).draft_id
        )
        approved_only = await seed.approve(
            (
                await seed.enqueue(
                    dedupe_key="manual-route-approved",
                    event_date=now.date(),
                    source="event_replan",
                    content="仍在已批准",
                )
            ).draft_id
        )
        claimed_pub = await seed.claim_for_publish(pub.draft_id, now=now)
        claimed_rel = await seed.claim_for_publish(rel.draft_id, now=now)
        assert claimed_pub is not None and claimed_rel is not None
        await seed.mark_unknown(pub.draft_id, reason="restart_during_dispatch")
        await seed.mark_unknown(rel.draft_id, reason="ambiguous_timeout")
        pub_id = pub.draft_id
        rel_id = rel.draft_id
        approved_id = approved_only.draft_id
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
            confirmed = await client.post(
                f"{base_path}/{pub_id}/confirm-published",
                json={"note": "saw on phone", "external_post_id": "tid-9"},
            )
            released = await client.post(
                f"{base_path}/{rel_id}/confirm-not-published",
                json={"note": "not on feed"},
            )
            missing_body = await client.post(
                f"{base_path}/{approved_id}/confirm-published",
                json={},
            )
            illegal = await client.post(
                f"{base_path}/{approved_id}/confirm-published",
                json={"note": "wrong state"},
            )
            missing = await client.post(
                f"{base_path}/missing-draft/confirm-not-published",
                json={"note": "gone"},
            )
            repeated = await client.post(
                f"{base_path}/{pub_id}/confirm-published",
                json={"note": "saw on phone", "external_post_id": "tid-9"},
            )

        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "published"
        assert confirmed.json()["external_post_id"] == "tid-9"
        assert confirmed.json()["publish_date"] == now.date().isoformat()
        assert released.status_code == 200
        assert released.json()["status"] == "approved"
        assert released.json()["publish_date"] is None
        assert released.json()["external_post_id"] is None
        assert missing_body.status_code == 422
        assert illegal.status_code == 409
        assert missing.status_code == 404
        assert repeated.status_code == 200
        assert repeated.json()["status"] == "published"
        body = str(confirmed.json())
        assert "cookie" not in body.lower()
        assert "p_skey" not in body.lower()
    finally:
        await plugin.on_shutdown(ctx)


async def test_admin_publish_respects_plugin_dry_run_before_delivery(
    tmp_path: Path,
) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    plugin = _enabled_plugin()
    ctx = _plugin_context(tmp_path, _LLMProbe())
    db_path = tmp_path / "qzone_journal.db"

    await plugin.on_startup(ctx)
    seed = JournalStore(db_path)
    await seed.init()
    try:
        draft = await seed.approve(
            (
                await seed.enqueue(
                    dedupe_key="admin-publish-dry-run-gate",
                    event_date=datetime.now(_CST).date(),
                    source="event_replan",
                    content="只允许预演的草稿",
                )
            ).draft_id
        )
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
            response = await client.post(f"{base_path}/{draft.draft_id}/publish")

        assert response.status_code == 409
        assert "dry-run" in str(response.json()).lower()
    finally:
        await plugin.on_shutdown(ctx)

    with sqlite3.connect(db_path) as connection:
        status = connection.execute(
            "SELECT status FROM qzone_journal_drafts WHERE draft_id = ?",
            (draft.draft_id,),
        ).fetchone()[0]
    assert status == "approved"


async def test_admin_publish_reports_post_dispatch_runtime_failure_as_unknown(
    tmp_path: Path,
) -> None:
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    PluginConfig = _required_symbol("plugins.qzone_journal.plugin", "PluginConfig")
    Plugin = _required_symbol("plugins.qzone_journal.plugin", "QZoneJournalPlugin")
    plugin = Plugin(
        config=PluginConfig(
            enabled=True,
            dry_run=False,
            allow_live_publish=True,
            allowed_live_uins=["384801062"],
            manual_review=True,
        )
    )
    ctx = _plugin_context(tmp_path, _LLMProbe())
    db_path = tmp_path / "qzone_journal.db"
    now = datetime(2026, 7, 15, 12, 0, tzinfo=_CST)

    await plugin.on_startup(ctx)
    seed = JournalStore(db_path)
    await seed.init()
    try:
        draft = await seed.approve(
            (
                await seed.enqueue(
                    dedupe_key="admin-publish-post-dispatch-failure",
                    event_date=now.date(),
                    source="event_replan",
                    content="发布结果不确定的草稿",
                )
            ).draft_id
        )
        claimed = await seed.claim_for_publish(draft.draft_id, now=now)
        assert claimed is not None
        await seed.mark_unknown(draft.draft_id, reason="unverified_publish_response")
    finally:
        await seed.close()

    class _PostDispatchFailure:
        async def deliver(self, _draft_id: str) -> None:
            raise RuntimeError("QZone publish response was not explicitly successful")

    plugin._delivery = lambda *, dry_run: _PostDispatchFailure()
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
            response = await client.post(f"{base_path}/{draft.draft_id}/publish")

        assert response.status_code == 502
        assert response.json()["detail"] == "QZone publish failed: RuntimeError"
    finally:
        await plugin.on_shutdown(ctx)


async def test_admin_health_reports_fail_closed_config_and_complete_counts(
    tmp_path: Path,
) -> None:
    plugin = _enabled_plugin()
    ctx = _plugin_context(tmp_path, _LLMProbe())

    await plugin.on_startup(ctx)
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
    assert body["configured_enabled"] is True
    assert body["dry_run"] is True
    assert body["live_allowed"] is False
    assert body["advanced_enabled"] is False
    assert body["manual_review"] is True
    assert body["wire_profile_id"] == "qzone-text-v1-unverified"
    assert body["profile_validated"] is False
    assert body["salience_threshold"] == 0.7
    assert body["allowed_sources"] == [
        "event_replan",
        "dream_reflection",
        "schedule_generator",
    ]
    assert body["max_drafts_per_tick"] == 1
    assert body["max_drafts_per_day"] == 1
    assert body["counts"] == {
        "pending_review": 0,
        "approved": 0,
        "dispatching": 0,
        "published": 0,
        "rejected": 0,
        "failed": 0,
        "unknown": 0,
    }
    SELECTION_REASON_CODES = _required_symbol(
        "plugins.qzone_journal.selector",
        "SELECTION_REASON_CODES",
    )
    decisions = body["selection_decisions"]
    assert isinstance(decisions, dict)
    assert set(decisions) == set(SELECTION_REASON_CODES)
    assert all(isinstance(v, int) and v >= 0 for v in decisions.values())
    # Secret-free: no summary/stable_id/QQ-like values in health payload.
    dumped = json.dumps(body, ensure_ascii=False)
    assert "stable_id" not in dumped
    assert "source_summary" not in dumped
    assert "p_skey" not in dumped
    assert "cookie" not in dumped.lower()
    gate = body["live_publish_gate"]
    assert gate["ready"] is False
    assert isinstance(gate["reasons"], list)
    assert {item["code"] for item in gate["reasons"]} >= {
        "dry_run_enabled",
        "live_publish_disallowed",
        "profile_not_validated",
        "allowed_live_uins_empty",
        "bot_disconnected",
    }


async def test_admin_health_summarizes_process_lifetime_selection_decisions(
    tmp_path: Path,
) -> None:
    plugin = _enabled_plugin()
    plugin._selection_decisions.update(
        {
            "accept": 2,
            "reject_below_threshold": 3,
            "reject_out_ranked": 1,
        }
    )
    ctx = _plugin_context(tmp_path, _LLMProbe())

    await plugin.on_startup(ctx)
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
    assert "selection_summary" in body
    assert body["selection_summary"] == {
        "scope": "process_lifetime",
        "total": 6,
        "accepted": 2,
        "rejected": 4,
        "acceptance_rate": pytest.approx(1 / 3),
    }


async def test_tick_ranks_two_accepted_candidates_and_emits_out_ranked(
    tmp_path: Path,
) -> None:
    """R2: one tick with two accepted candidates → one draft, one LLM, loser out_ranked."""
    llm = _LLMProbe({"text": "胜出草稿"})
    PluginConfig = _required_symbol("plugins.qzone_journal.plugin", "PluginConfig")
    Plugin = _required_symbol("plugins.qzone_journal.plugin", "QZoneJournalPlugin")
    plugin = Plugin(
        config=PluginConfig(
            enabled=True,
            dry_run=True,
            allow_live_publish=False,
            manual_review=True,
            max_drafts_per_tick=1,
            max_drafts_per_day=1,
        )
    )
    today = datetime.now(_CST).date().isoformat()
    low = {
        "source": "event_replan",
        "date": today,
        "event_id": "rank-low",
        "summary": "低显著度的排练小调整",
        "salience": 0.72,
        "subject_kind": "fiction",
        "privacy": "public",
    }
    high = {
        "source": "event_replan",
        "date": today,
        "event_id": "rank-high",
        "summary": "高显著度的重大突破段落",
        "salience": 0.98,
        "subject_kind": "fiction",
        "privacy": "public",
    }
    ctx = _plugin_context(tmp_path, llm, events=[low, high])

    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
        route = _single_admin_route(plugin)
        app = FastAPI()
        base_path = route.path.rstrip("/")
        app.include_router(route.router, prefix=base_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            health = await client.get(f"{base_path}/health")
    finally:
        await plugin.on_shutdown(ctx)

    assert len(llm.calls) == 1
    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        rows = connection.execute(
            "SELECT stable_id, status FROM qzone_journal_drafts"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "rank-high"
    assert rows[0][1] == "pending_review"
    body = health.json()
    assert body["selection_decisions"]["accept"] == 1
    assert body["selection_decisions"]["reject_out_ranked"] == 1


async def test_day_draft_budget_blocks_extra_llm_and_rejected_does_not_occupy(
    tmp_path: Path,
) -> None:
    """R4: occupied same-day draft → reject_day_draft_budget; rejected/failed free."""
    JournalStore = _required_symbol("plugins.qzone_journal.store", "JournalStore")
    llm = _LLMProbe({"text": "不应再调用"})
    plugin = _enabled_plugin()
    today = datetime.now(_CST).date()
    ctx = _plugin_context(tmp_path, llm, events=[_event_payload()])
    db_path = tmp_path / "qzone_journal.db"

    await plugin.on_startup(ctx)
    seed = JournalStore(db_path, max_posts_per_day=3)
    await seed.init()
    try:
        pending = await seed.enqueue(
            dedupe_key="budget-occupied",
            event_date=today,
            source="event_replan",
            content="已占用预算的待审草稿",
            source_summary="已占用",
        )
        assert pending.status == "pending_review"
    finally:
        await seed.close()

    try:
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    assert llm.calls == []
    with sqlite3.connect(db_path) as connection:
        count = int(
            connection.execute("SELECT COUNT(*) FROM qzone_journal_drafts").fetchone()[0]
        )
    assert count == 1
    assert plugin._selection_decisions.get("reject_day_draft_budget", 0) >= 1

    # rejected does not occupy budget — next tick may compose a different event.
    llm2 = _LLMProbe({"text": "预算释放后成稿"})
    plugin2 = _enabled_plugin()
    freed_event = {
        "source": "event_replan",
        "date": today.isoformat(),
        "event_id": "budget-after-reject",
        "summary": "拒绝后预算释放的新事件",
        "salience": 0.9,
        "subject_kind": "fiction",
        "privacy": "public",
    }
    ctx2 = _plugin_context(tmp_path, llm2, events=[freed_event])
    await plugin2.on_startup(ctx2)
    seed2 = JournalStore(db_path, max_posts_per_day=3)
    await seed2.init()
    try:
        await seed2.reject(
            pending.draft_id,
            reason="admin_review_rejected",
            note="不合适",
        )
    finally:
        await seed2.close()
    try:
        await plugin2.on_tick(ctx2)
    finally:
        await plugin2.on_shutdown(ctx2)
    assert len(llm2.calls) == 1


async def test_duplicate_dedupe_emits_closed_reason_before_llm(tmp_path: Path) -> None:
    """R5: existing dedupe is checked before LLM and emits reject_duplicate_dedupe."""
    llm = _LLMProbe({"text": "第一次成稿"})
    plugin = _enabled_plugin()
    ctx = _plugin_context(tmp_path, llm, events=[_event_payload()])

    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    assert len(llm.calls) == 1
    assert plugin._selection_decisions.get("reject_duplicate_dedupe", 0) >= 1
    assert plugin._selection_decisions.get("accept", 0) >= 1


async def test_same_tick_duplicate_dedupe_key_counts_one_accept_one_reject(
    tmp_path: Path,
) -> None:
    """Contract 1: same-tick two raw events sharing a dedupe key → one draft/LLM/accept.

    With max_drafts_per_tick=2 and max_drafts_per_day=2, in-tick duplicates must not
    each consume a draft slot; the second yields reject_duplicate_dedupe.
    """
    llm = _LLMProbe({"text": "同 tick 去重后的唯一成稿"})
    PluginConfig = _required_symbol("plugins.qzone_journal.plugin", "PluginConfig")
    Plugin = _required_symbol("plugins.qzone_journal.plugin", "QZoneJournalPlugin")
    plugin = Plugin(
        config=PluginConfig(
            enabled=True,
            dry_run=True,
            allow_live_publish=False,
            manual_review=True,
            max_drafts_per_tick=2,
            max_drafts_per_day=2,
        )
    )
    today = datetime.now(_CST).date().isoformat()
    # Identical source + date + stable_id ⇒ identical CandidateEvent.dedupe_key.
    shared = {
        "source": "event_replan",
        "date": today,
        "event_id": "same-tick-dedupe",
        "summary": "第一次原始事件文案",
        "salience": 0.92,
        "subject_kind": "fiction",
        "privacy": "public",
    }
    twin = {
        **shared,
        "summary": "同 dedupe key 的第二次原始事件文案",
        "salience": 0.95,
    }
    ctx = _plugin_context(tmp_path, llm, events=[shared, twin])

    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    assert len(llm.calls) == 1, "duplicate same-tick events must share one LLM compose"
    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        rows = connection.execute(
            "SELECT stable_id, status FROM qzone_journal_drafts"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "same-tick-dedupe"
    assert rows[0][1] == "pending_review"
    assert plugin._selection_decisions.get("accept", 0) == 1
    assert plugin._selection_decisions.get("reject_duplicate_dedupe", 0) == 1


async def test_same_tick_hard_gate_reject_does_not_poison_dedupe_key_for_valid_twin(
    tmp_path: Path,
) -> None:
    """Ordering: hard-gate reject must not occupy same-tick seen_dedupe_keys.

    Same tick: first raw event adapts but fails hard gate (factual subject);
    second shares source/date/stable_id but is fiction/public. Frozen pipeline
    is adapter → hard gate → dedupe → day budget → rank → compose. The invalid
    first event must emit the exact hard-gate reason and must not suppress the
    valid twin via reject_duplicate_dedupe.
    """
    llm = _LLMProbe({"text": "硬门禁后同 key 有效孪生成稿"})
    PluginConfig = _required_symbol("plugins.qzone_journal.plugin", "PluginConfig")
    Plugin = _required_symbol("plugins.qzone_journal.plugin", "QZoneJournalPlugin")
    plugin = Plugin(
        config=PluginConfig(
            enabled=True,
            dry_run=True,
            allow_live_publish=False,
            manual_review=True,
            max_drafts_per_tick=2,
            max_drafts_per_day=2,
        )
    )
    today = datetime.now(_CST).date().isoformat()
    # Identical source + date + stable_id ⇒ identical CandidateEvent.dedupe_key.
    invalid_first = {
        "source": "event_replan",
        "date": today,
        "event_id": "hard-gate-then-valid-twin",
        "summary": "事实主体不应成稿的原始事件",
        "salience": 0.94,
        "subject_kind": "factual",
        "privacy": "public",
    }
    valid_twin = {
        "source": "event_replan",
        "date": today,
        "event_id": "hard-gate-then-valid-twin",
        "summary": "同 dedupe key 的公开虚构孪生事件",
        "salience": 0.96,
        "subject_kind": "fiction",
        "privacy": "public",
    }
    ctx = _plugin_context(tmp_path, llm, events=[invalid_first, valid_twin])

    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    # v0.7: bare factual without closed public projection is reject_public_projection
    # (not reject_subject_not_allowed). Must not poison same-tick dedupe for valid twin.
    assert plugin._selection_decisions.get("reject_public_projection", 0) == 1
    assert plugin._selection_decisions.get("reject_duplicate_dedupe", 0) == 0
    assert plugin._selection_decisions.get("accept", 0) == 1
    assert len(llm.calls) == 1, "valid twin after hard-gate reject must still compose"
    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        rows = connection.execute(
            "SELECT stable_id, status FROM qzone_journal_drafts"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "hard-gate-then-valid-twin"
    assert rows[0][1] == "pending_review"
