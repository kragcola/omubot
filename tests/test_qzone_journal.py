"""First RED contract slice for the QZone Journal plugin.

The production package does not exist yet.  Imports are intentionally delayed
until each test body so RED is reported as a precise contract assertion instead
of a collection error.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from admin.routes.api import create_api_router
from kernel.types import AdminRoute, PluginContext

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
    assert module is not None, f"{module_name} must exist for the QZone Journal contract"
    symbol = getattr(module, symbol_name, None)
    assert symbol is not None, f"{module_name} must expose {symbol_name}"
    return symbol


def _selector_types() -> tuple[type[Any], type[Any]]:
    return (
        _required_symbol("plugins.qzone_journal.selector", "CandidateEvent"),
        _required_symbol("plugins.qzone_journal.selector", "JournalSelector"),
    )


def _store_type() -> type[Any]:
    return _required_symbol("plugins.qzone_journal.store", "JournalStore")


def _event(
    *,
    source: str = "event_replan",
    subject_kind: str = "self",
    privacy: str = "public",
    salience: float = 0.8,
    stable_id: str = "arc-main:stage-2",
    summary: str = "今天把卡住很久的排练段落理顺了",
) -> Any:
    CandidateEvent, _ = _selector_types()
    return CandidateEvent(
        source=source,
        event_date=date(2026, 7, 15),
        stable_id=stable_id,
        subject_kind=subject_kind,
        privacy=privacy,
        salience=salience,
        summary=summary,
    )


def _selector() -> Any:
    _, JournalSelector = _selector_types()
    return JournalSelector(
        allowed_sources={"event_replan", "dream_reflection", "schedule_generator"},
        salience_threshold=0.7,
        advanced_enabled=False,
    )


def test_selector_accepts_only_allowlisted_public_self_or_fiction() -> None:
    cases = {
        "allowlisted-self": (_event(source="event_replan", subject_kind="self"), True),
        "allowlisted-fiction": (
            _event(source="dream_reflection", subject_kind="fiction"),
            True,
        ),
        "private-source": (_event(source="private_message", subject_kind="self"), False),
        "factual-person": (_event(subject_kind="factual"), False),
        "unknown-subject": (_event(subject_kind="unknown"), False),
        "private-scope": (_event(privacy="private"), False),
        "unknown-source": (_event(source="unknown_source"), False),
    }
    selector = _selector()

    actual = {
        name: selector.select(event) is not None
        for name, (event, _expected) in cases.items()
    }
    expected = {name: accepted for name, (_event_value, accepted) in cases.items()}

    assert actual == expected


def test_selector_evaluate_emits_closed_reason_codes_for_hard_gates() -> None:
    """R1: every hard reject yields an exact closed reason; accept yields accept."""
    SELECTION_REASON_CODES = _required_symbol(
        "plugins.qzone_journal.selector",
        "SELECTION_REASON_CODES",
    )
    selector = _selector()
    cases = {
        "accept": (
            _event(source="event_replan", subject_kind="self", privacy="public"),
            "accept",
        ),
        "source": (
            _event(source="private_message", subject_kind="self"),
            "reject_source_not_allowed",
        ),
        "privacy": (
            _event(privacy="private"),
            "reject_privacy_not_public",
        ),
        "subject-factual": (
            # Bare factual is constructible but fails closed public projection (v0.7).
            _event(subject_kind="factual"),
            "reject_public_projection",
        ),
        "subject-unknown": (
            _event(subject_kind="unknown"),
            "reject_subject_not_allowed",
        ),
        "empty-identity": (
            _event(stable_id="  ", summary="有摘要"),
            "reject_empty_identity",
        ),
        "empty-summary": (
            _event(summary="   "),
            "reject_empty_identity",
        ),
        "salience-high": (
            _event(salience=1.5),
            "reject_salience_out_of_range",
        ),
        "salience-neg": (
            _event(salience=-0.1),
            "reject_salience_out_of_range",
        ),
        "threshold": (
            _event(
                source="schedule_generator",
                subject_kind="fiction",
                salience=0.2,
                stable_id="schedule:2026-07-15",
                summary="和平常一样的一天",
            ),
            "reject_below_threshold",
        ),
    }
    for name, (event, expected_reason) in cases.items():
        decision = selector.evaluate(event, today=date(2026, 7, 15))
        assert decision.reason == expected_reason, name
        assert decision.reason in SELECTION_REASON_CODES
        if expected_reason == "accept":
            assert decision.accepted is True
            assert decision.candidate is not None
            assert decision.score is not None
        else:
            assert decision.accepted is False


def test_selector_threshold_accepts_significant_sources_but_rejects_flat_schedule() -> None:
    selector = _selector()

    assert selector.select(_event(source="event_replan", salience=0.7)) is not None
    assert selector.select(
        _event(source="dream_reflection", subject_kind="fiction", salience=0.91)
    ) is not None
    assert selector.select(
        _event(
            source="schedule_generator",
            salience=0.2,
            stable_id="schedule:2026-07-15",
            summary="和平常一样的一天",
        )
    ) is None


def test_publish_worth_score_formula_and_stable_id_tie_break() -> None:
    """R3: exact score ties resolve by lexicographically smaller stable_id."""
    compute_publish_worth = _required_symbol(
        "plugins.qzone_journal.selector",
        "compute_publish_worth",
    )
    rank_accepted = _required_symbol(
        "plugins.qzone_journal.selector",
        "rank_accepted",
    )
    SelectionDecision = _required_symbol(
        "plugins.qzone_journal.selector",
        "SelectionDecision",
    )
    today = date(2026, 7, 15)
    left = _event(stable_id="aaa-win", salience=0.8, summary="完全不同的甲文案")
    right = _event(stable_id="zzz-lose", salience=0.8, summary="完全不同的乙文案")
    score_left = compute_publish_worth(
        left,
        today=today,
        recent_summaries=[],
        day_narrative="",
    )
    score_right = compute_publish_worth(
        right,
        today=today,
        recent_summaries=[],
        day_narrative="",
    )
    assert score_left.importance == pytest.approx(0.8)
    assert score_left.recency == pytest.approx(1.0)
    assert score_left.novelty == pytest.approx(1.0)
    assert score_left.relevance == pytest.approx(0.5)
    assert score_left.total == pytest.approx(0.50 * 0.8 + 0.20 + 0.20 + 0.10 * 0.5)
    assert score_left.total == pytest.approx(score_right.total)

    ranked = rank_accepted(
        [
            SelectionDecision(
                reason="accept",
                candidate=right,
                score=score_right,
                source=right.source,
            ),
            SelectionDecision(
                reason="accept",
                candidate=left,
                score=score_left,
                source=left.source,
            ),
        ]
    )
    assert [item.candidate.stable_id for item in ranked] == ["aaa-win", "zzz-lose"]


def test_ranking_key_sorted_accepted_matches_rank_accepted_order() -> None:
    """Contract 5: sorted(..., key=ranking_key) must match rank_accepted order.

    Ordering contract: higher total, then higher salience, then lexicographically
    smaller stable_id. ranking_key alone is not a reverse-sort key for stable_id.
    """
    PublishWorthScore = _required_symbol(
        "plugins.qzone_journal.selector",
        "PublishWorthScore",
    )
    rank_accepted = _required_symbol(
        "plugins.qzone_journal.selector",
        "rank_accepted",
    )
    ranking_key = _required_symbol(
        "plugins.qzone_journal.selector",
        "ranking_key",
    )
    SelectionDecision = _required_symbol(
        "plugins.qzone_journal.selector",
        "SelectionDecision",
    )

    def _decision(
        stable_id: str,
        *,
        salience: float,
        total: float,
    ) -> Any:
        event = _event(stable_id=stable_id, salience=salience, summary=f"rank:{stable_id}")
        score = PublishWorthScore(
            importance=salience,
            recency=1.0,
            novelty=1.0,
            relevance=0.5,
            total=total,
        )
        return SelectionDecision(
            reason="accept",
            candidate=event,
            score=score,
            source=event.source,
        )

    decisions = [
        _decision("bbb-tie", salience=0.8, total=0.85),
        _decision("aaa-tie", salience=0.8, total=0.85),
        _decision("zzz-high-total", salience=0.7, total=0.95),
        _decision("mmm-high-sal", salience=0.9, total=0.85),
    ]

    ranked = rank_accepted(decisions)
    by_ranking_key = sorted(decisions, key=ranking_key, reverse=True)
    ranked_ids = [item.candidate.stable_id for item in ranked]
    key_ids = [item.candidate.stable_id for item in by_ranking_key]
    assert ranked_ids == [
        "zzz-high-total",
        "mmm-high-sal",
        "aaa-tie",
        "bbb-tie",
    ]
    assert ranked_ids == key_ids


def test_evaluate_without_today_does_not_grant_historical_recency() -> None:
    """Contract 6: omitting today must not treat a historical event as recency=1."""
    selector = _selector()
    historical = _event(
        stable_id="historical-recency",
        summary="去年排练节点回看",
        salience=0.85,
    )
    # CandidateEvent.event_date is fixed to 2026-07-15 by _event(); today is omitted.
    decision = selector.evaluate(historical)
    assert decision.accepted is True
    assert decision.score is not None
    assert decision.score.recency == pytest.approx(0.0), (
        "without an explicit today reference, historical events must score recency=0 "
        f"(got {decision.score.recency})"
    )


def test_candidate_dedupe_key_depends_only_on_source_date_and_stable_id() -> None:
    first = _event(summary="第一次文案", salience=0.75)
    second = _event(summary="重写后的文案", salience=0.99)

    assert first.dedupe_key
    assert first.dedupe_key == second.dedupe_key
    assert first.dedupe_key != _event(stable_id="arc-main:stage-3").dedupe_key


async def _open_store(tmp_path: Path, *, max_posts_per_day: int = 1) -> Any:
    JournalStore = _store_type()
    store = JournalStore(
        tmp_path / "qzone_journal.db",
        max_posts_per_day=max_posts_per_day,
    )
    await store.init()
    return store


async def _enqueue(
    store: Any,
    dedupe_key: str,
    *,
    content: str = "今天终于把那段排练理顺啦。",
) -> Any:
    return await store.enqueue(
        dedupe_key=dedupe_key,
        event_date=date(2026, 7, 15),
        source="event_replan",
        content=content,
    )


async def test_store_init_creates_v1_user_version_and_migration_ledger(tmp_path: Path) -> None:
    store = await _open_store(tmp_path)
    await store.close()

    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        rows = connection.execute(
            "SELECT version, name, checksum FROM _omubot_schema_migrations ORDER BY version"
        ).fetchall()
        audit_table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'qzone_journal_manual_resolutions'"
        ).fetchone()

    assert user_version == 6
    assert len(rows) == 6
    assert rows[0][0] == 1
    assert rows[1][0] == 2
    assert rows[2][0] == 3
    assert rows[3][0] == 4
    assert rows[4][0] == 5
    assert rows[5][0] == 6
    assert str(rows[0][1]).strip()
    assert str(rows[0][2]).strip()
    assert str(rows[2][1]).strip()
    assert str(rows[2][2]).strip()
    assert str(rows[3][1]).strip()
    assert str(rows[3][2]).strip()
    assert audit_table == ("qzone_journal_manual_resolutions",)


async def test_store_enqueue_is_idempotent_for_same_dedupe_key(tmp_path: Path) -> None:
    store = await _open_store(tmp_path)
    try:
        first = await _enqueue(store, "same-key", content="第一版")
        second = await _enqueue(store, "same-key", content="不应覆盖的第二版")
        stats = await store.stats()
        persisted = await store.get(first.draft_id)
    finally:
        await store.close()

    assert second.draft_id == first.draft_id
    assert stats["total"] == 1
    assert persisted.content == "第一版"
    assert persisted.status == "pending_review"


async def test_store_max_drafts_per_day_atomic_across_two_store_instances(
    tmp_path: Path,
) -> None:
    """Contract 3: store-level max_drafts_per_day is atomic across JournalStore instances.

    With budget=1 and two distinct same-day drafts racing through separate store
    handles on one SQLite file: exactly one insert succeeds, the other yields a
    typed budget refusal, final occupied count is 1, and dedupe remains idempotent.
    """
    JournalStore = _store_type()
    db_path = tmp_path / "qzone_journal.db"
    day = date(2026, 7, 15)

    def _build_store() -> Any:
        # Public constructor contract: store must accept max_drafts_per_day so the
        # daily draft budget is enforced at enqueue (not only in the plugin tick).
        try:
            store = JournalStore(db_path, max_drafts_per_day=1, max_posts_per_day=3)
        except TypeError as exc:
            raise AssertionError(
                "JournalStore must accept max_drafts_per_day on its public constructor "
                "so the atomic day-draft budget is store-level, not plugin-only"
            ) from exc
        return store

    store_a = _build_store()
    store_b = _build_store()
    await store_a.init()
    await store_b.init()

    BudgetRefusal: type[BaseException] | None = None
    for module_name in (
        "plugins.qzone_journal.store",
        "plugins.qzone_journal",
    ):
        module = _optional_qzone_module(module_name)
        if module is None:
            continue
        for symbol in (
            "DayDraftBudgetExceededError",
            "DraftDayBudgetExceededError",
            "MaxDraftsPerDayExceededError",
            "DayDraftBudgetRefusal",
        ):
            candidate = getattr(module, symbol, None)
            if isinstance(candidate, type) and issubclass(candidate, BaseException):
                BudgetRefusal = candidate
                break
        if BudgetRefusal is not None:
            break
    assert BudgetRefusal is not None, (
        "store module must expose a typed budget-refusal exception for max_drafts_per_day"
    )

    async def _insert(store: Any, key: str, body: str) -> Any:
        return await store.enqueue(
            dedupe_key=key,
            event_date=day,
            source="event_replan",
            content=body,
            stable_id=f"budget-{key}",
            subject_kind="fiction",
            privacy="public",
            salience=0.9,
            source_summary=body,
        )

    try:
        results = await asyncio.gather(
            _insert(store_a, "day-budget-race-a", "同日竞态草稿甲"),
            _insert(store_b, "day-budget-race-b", "同日竞态草稿乙"),
            return_exceptions=True,
        )
        winners = [item for item in results if not isinstance(item, BaseException)]
        losers = [item for item in results if isinstance(item, BaseException)]
        assert len(winners) == 1, f"exactly one insert must succeed, got {results!r}"
        assert len(losers) == 1, f"exactly one insert must refuse, got {results!r}"
        assert isinstance(losers[0], BudgetRefusal), (
            f"loser must be typed budget refusal, got {type(losers[0]).__name__}: {losers[0]!r}"
        )
        occupied = await store_a.count_occupied_drafts_for_event_date(day)
        assert occupied == 1

        # Dedupe idempotency preserved: re-enqueue of the winner returns same row.
        winner = winners[0]
        again = await store_b.enqueue(
            dedupe_key=winner.dedupe_key,
            event_date=day,
            source="event_replan",
            content="不应覆盖的重复入队",
        )
        assert again.draft_id == winner.draft_id
        occupied_after = await store_b.count_occupied_drafts_for_event_date(day)
        assert occupied_after == 1
    finally:
        await store_a.close()
        await store_b.close()


async def test_store_requires_approval_before_publish_claim(tmp_path: Path) -> None:
    store = await _open_store(tmp_path)
    now = datetime(2026, 7, 15, 20, 0, tzinfo=_CST)
    try:
        draft = await _enqueue(store, "approval-gate")
        assert await store.claim_for_publish(draft.draft_id, now=now) is None
        assert (await store.get(draft.draft_id)).status == "pending_review"

        approved = await store.approve(draft.draft_id)
        claimed = await store.claim_for_publish(draft.draft_id, now=now)
    finally:
        await store.close()

    assert approved.status == "approved"
    assert claimed is not None
    assert claimed.status == "dispatching"


async def test_store_daily_limit_is_atomic_for_concurrent_and_sequential_claims(
    tmp_path: Path,
) -> None:
    store = await _open_store(tmp_path, max_posts_per_day=1)
    now = datetime(2026, 7, 15, 23, 59, tzinfo=_CST)
    try:
        first = await store.approve((await _enqueue(store, "quota-a")).draft_id)
        second = await store.approve((await _enqueue(store, "quota-b")).draft_id)

        claims = await asyncio.gather(
            store.claim_for_publish(first.draft_id, now=now),
            store.claim_for_publish(second.draft_id, now=now),
        )
        winners = [claim for claim in claims if claim is not None]
        assert len(winners) == 1

        winner = winners[0]
        loser_id = second.draft_id if winner.draft_id == first.draft_id else first.draft_id
        await store.mark_published(winner.draft_id, external_post_id="qzone-1")
        assert await store.claim_for_publish(loser_id, now=now) is None
        assert (await store.get(loser_id)).status == "approved"
    finally:
        await store.close()


async def test_store_published_draft_cannot_be_claimed_again(tmp_path: Path) -> None:
    store = await _open_store(tmp_path)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=_CST)
    try:
        draft = await store.approve((await _enqueue(store, "published-once")).draft_id)
        claimed = await store.claim_for_publish(draft.draft_id, now=now)
        assert claimed is not None
        await store.mark_published(draft.draft_id, external_post_id="qzone-1")
        before = await store.get(draft.draft_id)

        assert await store.claim_for_publish(draft.draft_id, now=now) is None
        after = await store.get(draft.draft_id)
    finally:
        await store.close()

    assert after == before
    assert after.status == "published"




async def test_store_mark_published_sanitizes_external_post_id_before_write(
    tmp_path: Path,
) -> None:
    """mark_published must share confirm_published external_post_id contract."""
    store = await _open_store(tmp_path, max_posts_per_day=20)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=_CST)
    try:
        draft = await store.approve(
            (await _enqueue(store, "mark-published-sanitize")).draft_id
        )
        claimed = await store.claim_for_publish(draft.draft_id, now=now)
        assert claimed is not None and claimed.status == "dispatching"

        published = await store.mark_published(
            draft.draft_id,
            external_post_id="  tid-parser-42  ",
        )
        assert published.status == "published"
        assert published.external_post_id == "tid-parser-42"

        rejected_cases = [
            "",
            "   ",
            "bad id with spaces",
            "p_skey=leaked-secret-value",
            "x" * 121,
            "emoji-😊-id",
        ]
        for index, bad in enumerate(rejected_cases):
            other = await store.approve(
                (await _enqueue(store, f"mark-published-bad-{index}")).draft_id
            )
            claimed_other = await store.claim_for_publish(other.draft_id, now=now)
            assert claimed_other is not None
            with pytest.raises(ValueError):
                await store.mark_published(other.draft_id, external_post_id=bad)
            after = await store.get(other.draft_id)
            assert after is not None
            assert after.status == "dispatching"
            assert after.external_post_id is None
    finally:
        await store.close()


async def test_store_late_publish_confirmation_cannot_overwrite_unknown(
    tmp_path: Path,
) -> None:
    InvalidDraftTransitionError = _required_symbol(
        "plugins.qzone_journal.store",
        "InvalidDraftTransitionError",
    )
    store = await _open_store(tmp_path)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=_CST)
    try:
        draft = await store.approve(
            (await _enqueue(store, "late-publish-confirmation")).draft_id
        )
        claimed = await store.claim_for_publish(draft.draft_id, now=now)
        assert claimed is not None
        await store.mark_unknown(draft.draft_id, reason="restart_during_dispatch")

        with pytest.raises(InvalidDraftTransitionError):
            await store.mark_published(
                draft.draft_id,
                external_post_id="late-qzone-id",
            )

        after = await store.get(draft.draft_id)
    finally:
        await store.close()

    assert after is not None
    assert after.status == "unknown"
    assert after.external_post_id is None


async def test_store_startup_recovers_dispatching_as_unknown(tmp_path: Path) -> None:
    store = await _open_store(tmp_path)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=_CST)
    draft = await store.approve((await _enqueue(store, "restart-recovery")).draft_id)
    claimed = await store.claim_for_publish(draft.draft_id, now=now)
    assert claimed is not None and claimed.status == "dispatching"
    await store.close()

    recovered_store = await _open_store(tmp_path)
    try:
        recovered = await recovered_store.get(draft.draft_id)
        assert recovered.status == "unknown"
        assert await recovered_store.claim_for_publish(draft.draft_id, now=now) is None
    finally:
        await recovered_store.close()


async def test_store_confirm_published_keeps_quota_and_writes_audit(
    tmp_path: Path,
) -> None:
    InvalidDraftTransitionError = _required_symbol(
        "plugins.qzone_journal.store",
        "InvalidDraftTransitionError",
    )
    store = await _open_store(tmp_path)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=_CST)
    try:
        draft = await store.approve((await _enqueue(store, "manual-confirm-pub")).draft_id)
        claimed = await store.claim_for_publish(draft.draft_id, now=now)
        assert claimed is not None
        await store.mark_unknown(draft.draft_id, reason="restart_during_dispatch")
        unknown = await store.get(draft.draft_id)
        assert unknown is not None
        assert unknown.status == "unknown"
        assert unknown.publish_date == date(2026, 7, 15)

        resolved = await store.confirm_published(
            draft.draft_id,
            note="  operator saw feed on phone  ",
            external_post_id="  qzone-tid-42  ",
        )
        again = await store.confirm_published(
            draft.draft_id,
            note="operator saw feed on phone",
            external_post_id="qzone-tid-42",
        )
        competitor = await store.approve((await _enqueue(store, "manual-confirm-quota")).draft_id)
        blocked = await store.claim_for_publish(competitor.draft_id, now=now)
        audits = await store.list_manual_resolutions(draft_id=draft.draft_id)

        pending = await store.approve((await _enqueue(store, "manual-confirm-illegal")).draft_id)
        with pytest.raises(InvalidDraftTransitionError):
            await store.confirm_published(pending.draft_id, note="wrong state")
        with pytest.raises(KeyError):
            await store.confirm_published("missing-draft", note="gone")
    finally:
        await store.close()

    assert resolved.status == "published"
    assert resolved.publish_date == date(2026, 7, 15)
    assert resolved.external_post_id == "qzone-tid-42"
    assert again.status == "published"
    assert again.external_post_id == "qzone-tid-42"
    assert blocked is None
    assert len(audits) == 1
    assert audits[0]["decision"] == "confirm_published"
    assert audits[0]["note"] == "operator saw feed on phone"
    assert audits[0]["external_post_id"] == "qzone-tid-42"
    assert "cookie" not in str(audits[0]).lower()
    assert "p_skey" not in str(audits[0]).lower()


async def test_store_confirm_not_published_releases_quota_and_writes_audit(
    tmp_path: Path,
) -> None:
    store = await _open_store(tmp_path)
    now = datetime(2026, 7, 15, 18, 0, tzinfo=_CST)
    try:
        draft = await store.approve((await _enqueue(store, "manual-confirm-not")).draft_id)
        claimed = await store.claim_for_publish(draft.draft_id, now=now)
        assert claimed is not None
        await store.mark_unknown(draft.draft_id, reason="ambiguous_http_timeout")

        released = await store.confirm_not_published(
            draft.draft_id,
            note="feed empty after refresh",
        )
        again = await store.confirm_not_published(
            draft.draft_id,
            note="feed empty after refresh",
        )
        reclaimed = await store.claim_for_publish(draft.draft_id, now=now)
        audits = await store.list_manual_resolutions(draft_id=draft.draft_id)
    finally:
        await store.close()

    assert released.status == "approved"
    assert released.publish_date is None
    assert released.external_post_id is None
    assert again.status == "approved"
    assert reclaimed is not None
    assert reclaimed.status == "dispatching"
    assert reclaimed.publish_date == date(2026, 7, 15)
    assert len(audits) == 1
    assert audits[0]["decision"] == "confirm_not_published"
    assert audits[0]["note"] == "feed empty after refresh"
    assert audits[0]["previous_status"] == "unknown"
    assert audits[0]["new_status"] == "approved"


async def test_store_manual_resolution_idempotency_requires_matching_audit(
    tmp_path: Path,
) -> None:
    InvalidDraftTransitionError = _required_symbol(
        "plugins.qzone_journal.store",
        "InvalidDraftTransitionError",
    )
    store = await _open_store(tmp_path, max_posts_per_day=2)
    now = datetime(2026, 7, 15, 19, 0, tzinfo=_CST)
    try:
        published = await store.approve(
            (await _enqueue(store, "ordinary-published")).draft_id
        )
        claimed = await store.claim_for_publish(published.draft_id, now=now)
        assert claimed is not None
        await store.mark_published(
            published.draft_id,
            external_post_id="qzone-normal-1",
        )
        approved = await store.approve(
            (await _enqueue(store, "ordinary-approved")).draft_id
        )

        with pytest.raises(InvalidDraftTransitionError):
            await store.confirm_published(
                published.draft_id,
                note="this was not an unknown recovery",
                external_post_id="qzone-normal-1",
            )
        with pytest.raises(InvalidDraftTransitionError):
            await store.confirm_not_published(
                approved.draft_id,
                note="this was never dispatched",
            )

        assert await store.list_manual_resolutions(draft_id=published.draft_id) == []
        assert await store.list_manual_resolutions(draft_id=approved.draft_id) == []
    finally:
        await store.close()


async def test_store_cancelled_claim_leaves_no_half_transition(tmp_path: Path) -> None:
    store = await _open_store(tmp_path)
    draft = await store.approve((await _enqueue(store, "cancel-safe")).draft_id)
    blocker = sqlite3.connect(tmp_path / "qzone_journal.db", timeout=1.0)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        task = asyncio.create_task(
            store.claim_for_publish(
                draft.draft_id,
                now=datetime(2026, 7, 15, 12, 0, tzinfo=_CST),
            )
        )
        await asyncio.sleep(0.05)
        assert not task.done(), "claim should be waiting on the competing write transaction"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        blocker.rollback()
        blocker.close()

    try:
        assert (await store.get(draft.draft_id)).status == "approved"
    finally:
        await store.close()


def test_plugin_config_defaults_and_manifest_are_fail_closed() -> None:
    PluginConfig = _required_symbol("plugins.qzone_journal.plugin", "PluginConfig")
    Plugin = _required_symbol("plugins.qzone_journal.plugin", "QZoneJournalPlugin")
    config = PluginConfig()

    assert config.enabled is False
    assert config.dry_run is True
    assert config.allow_live_publish is False
    assert config.allowed_live_uins == []
    assert config.advanced_enabled is False
    assert config.max_posts_per_day == 1
    assert config.max_drafts_per_tick == 1
    assert config.max_drafts_per_day == 1
    assert config.manual_review is True

    normalized = PluginConfig(allowed_live_uins=["  384801062  ", "384801062", "123456"])
    assert normalized.allowed_live_uins == ["384801062", "123456"]
    with pytest.raises(ValueError):
        PluginConfig(allowed_live_uins=["not-a-uin"])
    with pytest.raises(ValueError):
        PluginConfig(manual_review=False)
    with pytest.raises(ValueError):
        PluginConfig(max_drafts_per_tick=0)
    with pytest.raises(ValueError):
        PluginConfig(max_drafts_per_day=4)
    with pytest.raises(ValueError):
        PluginConfig(
            allowed_sources=[
                "event_replan",
                "cookie=p_skey=must-not-be-allowlisted",
            ]
        )

    manifest_path = Path("plugins/qzone_journal/plugin.json")
    assert manifest_path.is_file(), "qzone_journal requires a canonical manifest v3"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    permissions = set(manifest.get("permissions", ()))
    assert manifest.get("manifest_version") == 3
    assert manifest.get("version") == "0.8.2"
    assert Plugin.version == "0.8.2"
    assert {"lifecycle", "tick", "storage", "network", "admin"} <= permissions
    assert permissions.isdisjoint({"message", "reply"})
    restart_fields = set(manifest.get("config", {}).get("restart_required_fields", ()))
    assert "allowed_live_uins" in restart_fields
    assert "max_drafts_per_tick" in restart_fields
    assert "max_drafts_per_day" in restart_fields

    defaults = json.loads(
        Path("plugins/qzone_journal/config.default.json").read_text(encoding="utf-8")
    )
    values = defaults.get("values", defaults)
    assert values.get("max_drafts_per_tick") == 1
    assert values.get("max_drafts_per_day") == 1
    assert values.get("dry_run") is True
    assert values.get("allow_live_publish") is False
    assert values.get("manual_review") is True


class _LLMProbe:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def _call(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        return {"text": "unexpected"}


async def test_default_disabled_plugin_tick_has_zero_storage_and_llm_side_effects(
    tmp_path: Path,
) -> None:
    PluginConfig = _required_symbol("plugins.qzone_journal.plugin", "PluginConfig")
    Plugin = _required_symbol("plugins.qzone_journal.plugin", "QZoneJournalPlugin")
    llm = _LLMProbe()
    ctx = PluginContext(
        storage_dir=tmp_path,
        plugin_data_dir=tmp_path / "plugins",
        llm_client=llm,
    )
    plugin = Plugin(config=PluginConfig())

    await plugin.on_startup(ctx)
    await plugin.on_tick(ctx)
    await plugin.on_shutdown(ctx)

    assert llm.calls == []
    assert list(tmp_path.rglob("*")) == []


class _AdminRouteBus:
    plugins: ClassVar[list[Any]] = []

    def __init__(self, route: AdminRoute) -> None:
        self._route = route

    def collect_admin_routes(self) -> list[AdminRoute]:
        return [self._route]

    def plugin_health(self) -> list[dict[str, Any]]:
        return []


def test_admin_api_aggregator_mounts_routes_collected_from_plugin_bus() -> None:
    plugin_router = APIRouter()

    @plugin_router.get("/status")
    async def qzone_status() -> dict[str, bool]:
        return {"ok": True}

    bus = _AdminRouteBus(AdminRoute(path="/qzone-journal", router=plugin_router))
    app = FastAPI()
    app.include_router(
        create_api_router(
            ctx=SimpleNamespace(bus=bus),
            bus=bus,
            repo_root=Path.cwd(),
        )
    )

    response = TestClient(app).get("/api/admin/qzone-journal/status")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
