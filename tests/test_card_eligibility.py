"""Card Category Time Eligibility v1 — RED→GREEN contract tests.

Covers pure policy helpers plus RetrievalGate / TemporalTrace wiring:
status/event expiry, non-decaying categories, timestamp anchors, kill-switch,
full/keyword/semantic/count paths, full-cache time crossing, TemporalTrace
active-head exclusion with superseded parent preservation, expired status,
cross-scope isolation, and cancellation propagation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from services.memory.card_eligibility import (
    DEFAULT_CARD_ELIGIBILITY_POLICY,
    CardEligibilityPolicy,
    card_time_anchor,
    filter_cards_for_recall,
    is_card_eligible_for_recall,
    parse_card_anchor_timestamp,
    policy_from_config,
)
from services.memory.card_store import CardStore, NewCard
from services.memory.retrieval import RetrievalGate
from services.memory.temporal_trace import TemporalTraceAssembler, TemporalTraceConfig

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

# Fixed "now" for deterministic TTL math.
_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path) -> AsyncIterator[CardStore]:
    path = str(tmp_path / "card_eligibility.db")
    s = CardStore(db_path=path)
    await s.init()
    try:
        yield s
    finally:
        await s.close()


def _card(
    *,
    category: str = "status",
    status: str = "active",
    updated_at: str = "",
    created_at: str = "",
    content: str = "test card",
    card_id: str = "c1",
    scope: str = "user",
    scope_id: str = "123",
) -> SimpleNamespace:
    return SimpleNamespace(
        card_id=card_id,
        category=category,
        status=status,
        updated_at=updated_at,
        created_at=created_at,
        content=content,
        scope=scope,
        scope_id=scope_id,
        priority=5,
        confidence=0.7,
    )


async def _set_card_times(
    store: CardStore,
    card_id: str,
    *,
    updated_at: str | None = None,
    created_at: str | None = None,
) -> None:
    db = store._require_db()
    if updated_at is not None and created_at is not None:
        await db.execute(
            "UPDATE memory_cards SET updated_at = ?, created_at = ? WHERE card_id = ?",
            (updated_at, created_at, card_id),
        )
    elif updated_at is not None:
        await db.execute(
            "UPDATE memory_cards SET updated_at = ? WHERE card_id = ?",
            (updated_at, card_id),
        )
    elif created_at is not None:
        await db.execute(
            "UPDATE memory_cards SET created_at = ? WHERE card_id = ?",
            (created_at, card_id),
        )
    await db.commit()


def _gate(
    store: CardStore,
    *,
    policy: CardEligibilityPolicy | None = None,
    now: datetime | None = None,
    semantic: bool = False,
    refresh_interval: int = 5,
) -> RetrievalGate:
    clock = now or _NOW
    return RetrievalGate(
        card_store=store,
        refresh_interval=refresh_interval,
        semantic_enabled=semantic,
        semantic_backend="ngram",
        now_provider=lambda: clock,
        card_eligibility=policy if policy is not None else DEFAULT_CARD_ELIGIBILITY_POLICY,
    )


# ---------------------------------------------------------------------------
# Pure policy helpers
# ---------------------------------------------------------------------------


def test_default_policy_status_event_ttls() -> None:
    pol = DEFAULT_CARD_ELIGIBILITY_POLICY
    assert pol.enabled is True
    assert pol.ttl_days_for("status") == 30
    assert pol.ttl_days_for("event") == 180
    for cat in ("preference", "boundary", "relationship", "promise", "fact"):
        assert pol.ttl_days_for(cat) is None


def test_status_within_ttl_eligible() -> None:
    card = _card(
        category="status",
        updated_at="2026-07-01T12:00:00+00:00",  # 16 days before _NOW
    )
    assert is_card_eligible_for_recall(card, now=_NOW) is True


def test_status_past_ttl_ineligible() -> None:
    card = _card(
        category="status",
        updated_at="2026-06-01T12:00:00+00:00",  # 46 days
    )
    assert is_card_eligible_for_recall(card, now=_NOW) is False


def test_event_within_and_past_ttl() -> None:
    young = _card(category="event", updated_at="2026-02-01T00:00:00+00:00")  # ~166d
    old = _card(category="event", updated_at="2025-12-01T00:00:00+00:00")  # ~228d
    assert is_card_eligible_for_recall(young, now=_NOW) is True
    assert is_card_eligible_for_recall(old, now=_NOW) is False


@pytest.mark.parametrize(
    "category",
    ["preference", "boundary", "relationship", "promise", "fact"],
)
def test_non_decaying_categories_never_expire(category: str) -> None:
    card = _card(
        category=category,
        updated_at="2020-01-01T00:00:00+00:00",
    )
    assert is_card_eligible_for_recall(card, now=_NOW) is True


def test_malformed_updated_at_fail_closed_for_status() -> None:
    card = _card(category="status", updated_at="not-a-timestamp")
    assert is_card_eligible_for_recall(card, now=_NOW) is False


def test_empty_updated_at_falls_back_to_created_at() -> None:
    fresh = _card(
        category="status",
        updated_at="",
        created_at="2026-07-10T12:00:00+00:00",
    )
    stale = _card(
        category="status",
        updated_at="   ",
        created_at="2026-05-01T12:00:00+00:00",
    )
    assert card_time_anchor(fresh) == "2026-07-10T12:00:00+00:00"
    assert is_card_eligible_for_recall(fresh, now=_NOW) is True
    assert is_card_eligible_for_recall(stale, now=_NOW) is False


def test_both_anchors_empty_fail_closed_for_decaying() -> None:
    card = _card(category="status", updated_at="", created_at="")
    assert is_card_eligible_for_recall(card, now=_NOW) is False
    # Non-decaying still eligible without anchors.
    fact = _card(category="fact", updated_at="", created_at="")
    assert is_card_eligible_for_recall(fact, now=_NOW) is True


def test_offset_less_timestamp_uses_asia_shanghai() -> None:
    # 2026-07-01 00:00 Asia/Shanghai == 2026-06-30 16:00 UTC
    parsed = parse_card_anchor_timestamp("2026-07-01T00:00:00")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(hours=8)
    # Explicit Z preserved as UTC.
    z = parse_card_anchor_timestamp("2026-07-01T00:00:00Z")
    assert z is not None
    assert z.utcoffset() == timedelta(0)
    # Explicit +00:00 preserved.
    exp = parse_card_anchor_timestamp("2026-07-01T00:00:00+00:00")
    assert exp is not None
    assert exp.utcoffset() == timedelta(0)


def test_explicit_offset_eligibility_math() -> None:
    # Anchor 31 days before now with +08:00 still past 30d status TTL.
    card = _card(
        category="status",
        updated_at="2026-06-16T20:00:00+08:00",  # == 2026-06-16T12:00 UTC → 31d
    )
    assert is_card_eligible_for_recall(card, now=_NOW) is False
    card2 = _card(
        category="status",
        updated_at="2026-06-17T20:00:00+08:00",  # 30d exactly at _NOW noon UTC
    )
    assert is_card_eligible_for_recall(card2, now=_NOW) is True


def test_kill_switch_disabled_is_identity() -> None:
    pol = CardEligibilityPolicy(enabled=False, category_ttl_days={"status": 1})
    expired = _card(
        category="status",
        updated_at="2020-01-01T00:00:00+00:00",
        status="active",
    )
    assert is_card_eligible_for_recall(expired, policy=pol, now=_NOW) is True
    # Disabled policy does not re-check status either (identity for filter helpers).
    superseded = _card(
        category="status",
        status="superseded",
        updated_at="2026-07-16T00:00:00+00:00",
    )
    assert is_card_eligible_for_recall(superseded, policy=pol, now=_NOW) is True
    filtered = filter_cards_for_recall([expired, superseded], policy=pol, now=_NOW)
    assert len(filtered) == 2


def test_explicit_expired_status_ineligible_when_enabled() -> None:
    card = _card(
        category="status",
        status="expired",
        updated_at="2026-07-16T00:00:00+00:00",
    )
    assert is_card_eligible_for_recall(card, now=_NOW) is False


def test_policy_clamps_ttl_bounds() -> None:
    pol = CardEligibilityPolicy(
        enabled=True,
        category_ttl_days={"status": 0, "event": 99999, "fact": -3},
    )
    assert pol.ttl_days_for("status") == 1
    assert pol.ttl_days_for("event") == 3650
    assert pol.ttl_days_for("fact") == 1


def test_policy_from_config_status_event_fields() -> None:
    pol = policy_from_config(
        {"enabled": True, "status_ttl_days": 7, "event_ttl_days": 14},
    )
    assert pol.ttl_days_for("status") == 7
    assert pol.ttl_days_for("event") == 14
    disabled = policy_from_config({"enabled": False})
    assert disabled.enabled is False


def test_ttl_turns_not_interpreted() -> None:
    """Schema field ttl_turns must remain unused by eligibility v1."""
    card = _card(
        category="status",
        updated_at="2020-01-01T00:00:00+00:00",
    )
    card.ttl_turns = 1  # would still be expired by calendar TTL
    assert is_card_eligible_for_recall(card, now=_NOW) is False
    # Fresh status with ttl_turns=0 is still eligible by calendar age.
    fresh = _card(category="status", updated_at="2026-07-16T00:00:00+00:00")
    fresh.ttl_turns = 0
    assert is_card_eligible_for_recall(fresh, now=_NOW) is True


# ---------------------------------------------------------------------------
# RetrievalGate paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_full_excludes_expired_status_keeps_fact(store: CardStore) -> None:
    fact_id = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="123", content="长期事实偏好音游"),
    )
    status_id = await store.add_card(
        NewCard(category="status", scope="user", scope_id="123", content="临时心情低落"),
    )
    await _set_card_times(
        store,
        status_id,
        updated_at="2026-05-01T12:00:00+00:00",
    )
    # Fact remains old but non-decaying.
    await _set_card_times(
        store,
        fact_id,
        updated_at="2020-01-01T00:00:00+00:00",
    )

    gate = _gate(store)
    result = await gate.retrieve_cards(session_id="elig_full", user_id="123")
    assert result.decision.startswith("full_")
    ids = {h.card.card_id for h in result.hits}
    assert fact_id in ids
    assert status_id not in ids
    assert result.total_active == 1
    assert result.matched_active == 1


@pytest.mark.asyncio
async def test_gate_keyword_excludes_expired_event(store: CardStore) -> None:
    event_id = await store.add_card(
        NewCard(category="event", scope="user", scope_id="123", content="去年音游比赛"),
    )
    await _set_card_times(
        store,
        event_id,
        updated_at="2025-01-01T00:00:00+00:00",
    )
    # Fresh non-matching fact keeps total_active > 0 for count truthfulness.
    await store.add_card(
        NewCard(category="fact", scope="user", scope_id="123", content="会弹钢琴"),
    )

    gate = _gate(store, refresh_interval=100)
    # Turn 1 full
    await gate.retrieve_cards(session_id="elig_kw", user_id="123")
    # Turn 2 keyword
    result = await gate.retrieve_cards(
        session_id="elig_kw",
        user_id="123",
        conversation_text="音游比赛",
    )
    assert result.decision in {"keyword", "minimal_hint", "miss", "semantic_ngram"}
    # Expired event must not appear even if keyword would match store search.
    for hit in result.hits:
        assert hit.card.card_id != event_id
    if result.decision == "keyword":
        assert result.matched_active == 0 or all(
            h.card.category != "event" or h.card.card_id != event_id for h in result.hits
        )


@pytest.mark.asyncio
async def test_gate_keyword_does_not_let_expired_rows_starve_fresh_match(
    store: CardStore,
) -> None:
    """Eligibility filtering must happen before the keyword candidate cap."""
    for index in range(5):
        expired_id = await store.add_card(
            NewCard(
                category="event",
                scope="user",
                scope_id="123",
                content=f"音游比赛旧记录{index}",
                priority=10,
            ),
        )
        await _set_card_times(
            store,
            expired_id,
            updated_at="2025-01-01T00:00:00+00:00",
        )

    fresh_id = await store.add_card(
        NewCard(
            category="event",
            scope="user",
            scope_id="123",
            content="音游比赛新记录",
            priority=1,
        ),
    )
    await _set_card_times(
        store,
        fresh_id,
        updated_at="2026-07-10T00:00:00+00:00",
    )

    gate = _gate(store, refresh_interval=100)
    await gate.retrieve_cards(session_id="kw_starvation", user_id="123")
    result = await gate.retrieve_cards(
        session_id="kw_starvation",
        user_id="123",
        conversation_text="音游比赛",
    )

    assert result.decision == "keyword"
    assert result.matched_active == 1
    assert [hit.card.card_id for hit in result.hits] == [fresh_id]


@pytest.mark.asyncio
async def test_gate_keyword_fresh_event_matches(store: CardStore) -> None:
    event_id = await store.add_card(
        NewCard(category="event", scope="user", scope_id="123", content="本周音游比赛"),
    )
    await _set_card_times(
        store,
        event_id,
        updated_at="2026-07-10T00:00:00+00:00",
    )
    gate = _gate(store, refresh_interval=100)
    await gate.retrieve_cards(session_id="elig_kw2", user_id="123")
    result = await gate.retrieve_cards(
        session_id="elig_kw2",
        user_id="123",
        conversation_text="音游比赛",
    )
    assert result.decision == "keyword"
    assert any(h.card.card_id == event_id for h in result.hits)
    assert result.matched_active >= 1


@pytest.mark.asyncio
async def test_gate_semantic_excludes_expired_status(store: CardStore) -> None:
    status_id = await store.add_card(
        NewCard(category="status", scope="user", scope_id="123", content="对猫毛过敏发作中"),
    )
    await _set_card_times(
        store,
        status_id,
        updated_at="2026-05-01T00:00:00+00:00",
    )
    # Keep a fresh fact so total_active is truthful and non-zero if needed.
    await store.add_card(
        NewCard(category="fact", scope="user", scope_id="123", content="会弹钢琴"),
    )
    gate = _gate(store, semantic=True, refresh_interval=100)
    await gate.retrieve_cards(session_id="elig_sem", user_id="123")
    result = await gate.retrieve_cards(
        session_id="elig_sem",
        user_id="123",
        conversation_text="对猫会过敏",
    )
    for hit in result.hits:
        assert hit.card.card_id != status_id
    # If decision is semantic, matched must not include the expired status.
    if result.decision.startswith("semantic_"):
        assert all(h.card.card_id != status_id for h in result.hits)


@pytest.mark.asyncio
async def test_gate_full_cache_time_crossing(store: CardStore) -> None:
    """Cache stores raw cards; re-filter on read when clock advances past TTL."""
    status_id = await store.add_card(
        NewCard(category="status", scope="user", scope_id="123", content="短期状态焦虑"),
    )
    # Anchor: 29 days before first clock → eligible; 31 days before second → not.
    await _set_card_times(
        store,
        status_id,
        updated_at="2026-06-18T12:00:00+00:00",
    )
    clock = {"t": datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)}  # age = 29d

    gate = RetrievalGate(
        card_store=store,
        refresh_interval=5,
        now_provider=lambda: clock["t"],
        card_eligibility=DEFAULT_CARD_ELIGIBILITY_POLICY,
    )
    first = await gate.retrieve_cards(session_id="cache_cross", user_id="123")
    assert any(h.card.card_id == status_id for h in first.hits)
    assert first.total_active == 1

    # Advance clock without invalidating cache key contents.
    clock["t"] = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)  # age = 32d
    # Force full path again via new session.
    second = await gate.retrieve_cards(session_id="cache_cross_2", user_id="123")
    assert all(h.card.card_id != status_id for h in second.hits)
    assert second.total_active == 0
    assert second.matched_active == 0


@pytest.mark.asyncio
async def test_gate_count_truthful_total_active(store: CardStore) -> None:
    await store.add_card(
        NewCard(category="fact", scope="user", scope_id="123", content="会弹钢琴"),
    )
    expired = await store.add_card(
        NewCard(category="status", scope="user", scope_id="123", content="旧状态"),
    )
    await _set_card_times(
        store,
        expired,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    other = await store.add_card(
        NewCard(category="status", scope="user", scope_id="999", content="别人状态"),
    )
    await _set_card_times(
        store,
        other,
        updated_at="2026-07-16T00:00:00+00:00",
    )

    gate = _gate(store, refresh_interval=100)
    full = await gate.retrieve_cards(session_id="count_e", user_id="123")
    assert full.total_active == 1
    assert full.matched_active == 1

    await gate.retrieve_cards(session_id="count_e", user_id="123")  # turn 2 setup
    # Minimal/miss path: total_active still only eligible cards.
    # Use high refresh so we stay off full path; nonsense query for miss/minimal.
    for _ in range(3):
        await gate.retrieve_cards(
            session_id="count_e",
            user_id="123",
            conversation_text="海底两万里科幻小说无关内容",
        )
    minimal = await gate.retrieve_cards(
        session_id="count_e",
        user_id="123",
        conversation_text="海底两万里科幻小说无关内容",
    )
    assert minimal.decision in {"minimal_hint", "miss"}
    if minimal.decision == "minimal_hint":
        assert minimal.total_active == 1
        assert minimal.matched_active == 0


@pytest.mark.asyncio
async def test_gate_kill_switch_serves_expired_status(store: CardStore) -> None:
    status_id = await store.add_card(
        NewCard(category="status", scope="user", scope_id="123", content="旧状态仍可见"),
    )
    await _set_card_times(
        store,
        status_id,
        updated_at="2020-01-01T00:00:00+00:00",
    )
    pol = CardEligibilityPolicy(enabled=False)
    gate = _gate(store, policy=pol)
    result = await gate.retrieve_cards(session_id="kill", user_id="123")
    assert any(h.card.card_id == status_id for h in result.hits)
    assert result.total_active == 1


@pytest.mark.asyncio
async def test_gate_cross_scope_isolation(store: CardStore) -> None:
    a = await store.add_card(
        NewCard(category="status", scope="user", scope_id="aaa", content="用户A状态"),
    )
    b = await store.add_card(
        NewCard(category="status", scope="user", scope_id="bbb", content="用户B状态"),
    )
    await _set_card_times(store, a, updated_at="2026-07-16T00:00:00+00:00")
    await _set_card_times(store, b, updated_at="2026-07-16T00:00:00+00:00")
    gate = _gate(store)
    res_a = await gate.retrieve_cards(session_id="scope_a", user_id="aaa")
    res_b = await gate.retrieve_cards(session_id="scope_b", user_id="bbb")
    assert {h.card.card_id for h in res_a.hits} == {a}
    assert {h.card.card_id for h in res_b.hits} == {b}


# ---------------------------------------------------------------------------
# TemporalTrace active heads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_temporal_trace_excludes_expired_status_head_preserves_parent(
    store: CardStore,
) -> None:
    """Expired active status head must not open a KP; walk still returns parents."""
    old = await store.add_card(
        NewCard(
            category="status",
            scope="user",
            scope_id="123",
            content="用户状态：在杭州出差",
        ),
    )
    new = await store.supersede_card(
        old,
        NewCard(
            category="status",
            scope="user",
            scope_id="123",
            content="用户状态：在上海出差",
        ),
        source_msg_id="m_sh",
        evidence_text="改到上海了",
    )
    # Make head calendar-expired while parent remains in DB as superseded.
    await _set_card_times(store, new, updated_at="2026-05-01T00:00:00+00:00")
    await _set_card_times(store, old, updated_at="2026-04-01T00:00:00+00:00")

    chain = await store.walk_supersedes_chain(new, max_depth=4)
    assert len(chain) >= 2
    assert chain[0].card_id == new
    assert any(c.card_id == old for c in chain)

    assembler = TemporalTraceAssembler(
        store,
        config=TemporalTraceConfig(enabled=True),
        card_eligibility=DEFAULT_CARD_ELIGIBILITY_POLICY,
        now_provider=lambda: _NOW,
    )
    result = await assembler.assemble(
        current_message="你之前不是说在杭州吗",
        session_id="user_123",
        user_id="123",
        active_memory_hits=[new],
    )
    # Expired head excluded → no trace (or no KP using that head).
    if result is not None:
        for kp in result.knowledge_points:
            head_id = getattr(getattr(kp, "current", None), "card_id", None)
            assert head_id != new


@pytest.mark.asyncio
async def test_temporal_trace_fresh_status_head_emits_with_parent(
    store: CardStore,
) -> None:
    old = await store.add_card(
        NewCard(
            category="status",
            scope="user",
            scope_id="123",
            content="用户状态：在杭州出差",
        ),
    )
    new = await store.supersede_card(
        old,
        NewCard(
            category="status",
            scope="user",
            scope_id="123",
            content="用户状态：在上海出差",
        ),
        source_msg_id="m_sh2",
        evidence_text="改到上海了",
    )
    await _set_card_times(store, new, updated_at="2026-07-10T00:00:00+00:00")
    # Parent can be "old" by calendar; trajectory must still include it.
    await _set_card_times(store, old, updated_at="2025-01-01T00:00:00+00:00")

    assembler = TemporalTraceAssembler(
        store,
        config=TemporalTraceConfig(enabled=True),
        card_eligibility=DEFAULT_CARD_ELIGIBILITY_POLICY,
        now_provider=lambda: _NOW,
    )
    result = await assembler.assemble(
        current_message="你之前不是说在杭州吗",
        session_id="user_123",
        user_id="123",
        active_memory_hits=[new],
    )
    assert result is not None
    assert result.knowledge_points
    kp = result.knowledge_points[0]
    current = getattr(kp, "current", None)
    assert current is not None
    assert getattr(current, "card_id", None) == new
    # Parent preserved in earlier/trajectory despite age.
    earlier = getattr(kp, "earlier", None) or []
    trajectory = getattr(kp, "trajectory", None) or []
    parent_ids = set()
    for node in list(earlier) + list(trajectory):
        cid = getattr(node, "card_id", None) if not isinstance(node, dict) else node.get("card_id")
        if cid:
            parent_ids.add(cid)
    assert old in parent_ids


@pytest.mark.asyncio
async def test_temporal_trace_kill_switch_allows_expired_head(store: CardStore) -> None:
    old = await store.add_card(
        NewCard(
            category="status",
            scope="user",
            scope_id="123",
            content="用户状态：在杭州",
        ),
    )
    new = await store.supersede_card(
        old,
        NewCard(
            category="status",
            scope="user",
            scope_id="123",
            content="用户状态：在上海",
        ),
        source_msg_id="m_kill",
        evidence_text="上海",
    )
    await _set_card_times(store, new, updated_at="2020-01-01T00:00:00+00:00")

    assembler = TemporalTraceAssembler(
        store,
        config=TemporalTraceConfig(enabled=True),
        card_eligibility=CardEligibilityPolicy(enabled=False),
        now_provider=lambda: _NOW,
    )
    result = await assembler.assemble(
        current_message="你之前不是说在杭州吗",
        session_id="user_123",
        user_id="123",
        active_memory_hits=[new],
    )
    assert result is not None
    assert any(
        getattr(getattr(kp, "current", None), "card_id", None) == new
        for kp in result.knowledge_points
    )


@pytest.mark.asyncio
async def test_temporal_trace_cancel_propagates(store: CardStore) -> None:
    class _CancelStore:
        async def get_card(self, *_a, **_k):
            raise asyncio.CancelledError()

        async def list_active_chain_heads(self, *_a, **_k):
            raise asyncio.CancelledError()

        async def walk_supersedes_chain(self, *_a, **_k):
            raise asyncio.CancelledError()

    assembler = TemporalTraceAssembler(
        _CancelStore(),
        config=TemporalTraceConfig(enabled=True),
        card_eligibility=DEFAULT_CARD_ELIGIBILITY_POLICY,
        now_provider=lambda: _NOW,
    )
    with pytest.raises(asyncio.CancelledError):
        await assembler.assemble(
            current_message="你之前不是说在杭州吗",
            session_id="user_1",
            user_id="1",
            active_memory_hits=["x"],
        )


# ---------------------------------------------------------------------------
# Admin / direct store path unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_store_list_still_returns_expired_status(store: CardStore) -> None:
    """CardStore direct APIs remain unfiltered (admin/maintenance)."""
    cid = await store.add_card(
        NewCard(category="status", scope="user", scope_id="123", content="旧状态"),
    )
    await _set_card_times(store, cid, updated_at="2020-01-01T00:00:00+00:00")
    cards = await store.get_entity_cards("user", "123")
    assert any(c.card_id == cid for c in cards)
