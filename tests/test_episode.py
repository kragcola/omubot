"""Tests for EpisodeStore: schema, CRUD, state machine, revision tracking."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from services.episodic import (
    PER_GROUP_MAX_ACTIVE,
    Episode,
    EpisodeStore,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_episodic.db")


@pytest.fixture
async def store(db_path):
    s = EpisodeStore(db_path)
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_init_creates_tables(store: EpisodeStore):
    db = store._require_db()
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) as cur:
        tables = [row["name"] for row in await cur.fetchall()]
    assert "episodes" in tables
    assert "episode_revisions" in tables
    assert "episode_observations" in tables


@pytest.mark.asyncio
async def test_create_episode_defaults(store: EpisodeStore):
    ep = await store.create_episode(
        situation="user asked about weather",
        group_id="g1",
    )
    assert isinstance(ep, Episode)
    assert ep.episode_state == "dry_run"
    assert ep.scope == "group"
    assert ep.source == "consolidator"
    assert ep.confidence == 0.5
    assert ep.group_id == "g1"
    assert ep.situation == "user asked about weather"


@pytest.mark.asyncio
async def test_create_episode_custom_fields(store: EpisodeStore):
    ep = await store.create_episode(
        situation="bot replied with joke",
        observed_context="user was sad",
        action_taken="told a joke",
        outcome_signal="user laughed",
        reflection="humor works for sad users",
        group_id="g2",
        scope="global",
        source="manual",
        confidence=0.9,
        linked_memory_ids=["mem_1", "mem_2"],
        meta={"tag": "humor"},
    )
    assert ep.observed_context == "user was sad"
    assert ep.action_taken == "told a joke"
    assert ep.outcome_signal == "user laughed"
    assert ep.reflection == "humor works for sad users"
    assert ep.scope == "global"
    assert ep.source == "manual"
    assert ep.confidence == 0.9
    assert ep.linked_memory_ids == ["mem_1", "mem_2"]
    assert ep.meta == {"tag": "humor"}


@pytest.mark.asyncio
async def test_get_episode(store: EpisodeStore):
    ep = await store.create_episode(situation="test get", group_id="g1")
    fetched = await store.get_episode(ep.episode_id)
    assert fetched is not None
    assert fetched.episode_id == ep.episode_id
    assert fetched.situation == "test get"


@pytest.mark.asyncio
async def test_get_episode_not_found(store: EpisodeStore):
    result = await store.get_episode("nonexistent_id")
    assert result is None


@pytest.mark.asyncio
async def test_list_episodes_filter_by_state(store: EpisodeStore):
    await store.create_episode(situation="a", group_id="g1", confidence=0.3)
    ep2 = await store.create_episode(situation="b", group_id="g1", confidence=0.8)
    await store.auto_promote_dry_runs(group_id="g1")

    dry_runs = await store.list_episodes(state_filter="dry_run", group_id="g1")
    candidates = await store.list_episodes(state_filter="candidate", group_id="g1")
    assert len(dry_runs) == 1
    assert dry_runs[0].situation == "a"
    assert len(candidates) == 1
    assert candidates[0].episode_id == ep2.episode_id


@pytest.mark.asyncio
async def test_list_episodes_filter_by_group(store: EpisodeStore):
    await store.create_episode(situation="g1 ep", group_id="g1")
    await store.create_episode(situation="g2 ep", group_id="g2")
    g1_eps = await store.list_episodes(group_id="g1")
    assert len(g1_eps) == 1
    assert g1_eps[0].group_id == "g1"


@pytest.mark.asyncio
async def test_transition_state_valid(store: EpisodeStore):
    ep = await store.create_episode(situation="t", group_id="g1", confidence=0.8)
    await store.transition_state(ep.episode_id, new_state="candidate", actor="system")
    updated = await store.get_episode(ep.episode_id)
    assert updated.episode_state == "candidate"

    await store.transition_state(ep.episode_id, new_state="approved", actor="admin")
    updated = await store.get_episode(ep.episode_id)
    assert updated.episode_state == "approved"


@pytest.mark.asyncio
async def test_transition_state_invalid(store: EpisodeStore):
    ep = await store.create_episode(situation="t", group_id="g1")
    with pytest.raises(ValueError, match="Invalid transition"):
        await store.transition_state(ep.episode_id, new_state="approved", actor="admin")


@pytest.mark.asyncio
async def test_transition_enabled_for_prompt_allowed(store: EpisodeStore):
    ep = await store.create_episode(situation="t", group_id="g1", confidence=0.8)
    await store.transition_state(ep.episode_id, new_state="candidate", actor="system")
    await store.transition_state(ep.episode_id, new_state="approved", actor="admin")
    await store.transition_state(ep.episode_id, new_state="enabled_for_prompt", actor="admin")
    updated = await store.get_episode(ep.episode_id)
    assert updated.episode_state == "enabled_for_prompt"


@pytest.mark.asyncio
async def test_transition_disabled_to_approved(store: EpisodeStore):
    ep = await store.create_episode(situation="t", group_id="g1", confidence=0.8)
    await store.transition_state(ep.episode_id, new_state="candidate", actor="system")
    await store.transition_state(ep.episode_id, new_state="disabled", actor="admin")
    await store.transition_state(ep.episode_id, new_state="approved", actor="admin")
    updated = await store.get_episode(ep.episode_id)
    assert updated.episode_state == "approved"


@pytest.mark.asyncio
async def test_auto_promote_dry_runs(store: EpisodeStore):
    await store.create_episode(situation="low", group_id="g1", confidence=0.3)
    await store.create_episode(situation="high", group_id="g1", confidence=0.8)
    promoted = await store.auto_promote_dry_runs(group_id="g1")
    assert promoted == 1
    candidates = await store.list_episodes(state_filter="candidate", group_id="g1")
    assert len(candidates) == 1
    assert candidates[0].situation == "high"


@pytest.mark.asyncio
async def test_per_group_max_active_limit(store: EpisodeStore):
    for i in range(PER_GROUP_MAX_ACTIVE):
        ep = await store.create_episode(situation=f"ep{i}", group_id="gfull", confidence=0.8)
        await store.transition_state(ep.episode_id, new_state="candidate", actor="system")
        await store.transition_state(ep.episode_id, new_state="approved", actor="admin")

    extra = await store.create_episode(situation="overflow", group_id="gfull", confidence=0.8)
    await store.transition_state(extra.episode_id, new_state="candidate", actor="system")
    with pytest.raises(ValueError, match="max active"):
        await store.transition_state(extra.episode_id, new_state="approved", actor="admin")


@pytest.mark.asyncio
async def test_cross_group_visibility(store: EpisodeStore):
    ep = await store.create_episode(situation="shared", group_id="g1")
    assert ep.cross_group_visible is False

    ok = await store.set_cross_group_visibility(ep.episode_id, visible=True, actor="admin")
    assert ok is True
    updated = await store.get_episode(ep.episode_id)
    assert updated.cross_group_visible is True
    assert updated.cross_group_enabled_by == "admin"
    assert updated.cross_group_enabled_at != ""

    ok = await store.set_cross_group_visibility(ep.episode_id, visible=False, actor="admin")
    assert ok is True
    updated = await store.get_episode(ep.episode_id)
    assert updated.cross_group_visible is False


@pytest.mark.asyncio
async def test_revisions_recorded(store: EpisodeStore):
    ep = await store.create_episode(situation="rev test", group_id="g1", confidence=0.8)
    await store.transition_state(ep.episode_id, new_state="candidate", actor="system")
    await store.transition_state(ep.episode_id, new_state="approved", actor="admin", reason="looks good")

    revs = await store.list_revisions(ep.episode_id)
    assert len(revs) == 2
    assert revs[0].action == "state_candidate_to_approved"
    assert revs[0].prev_state == "candidate"
    assert revs[0].new_state == "approved"
    assert revs[0].reason == "looks good"
    assert revs[1].action == "state_dry_run_to_candidate"


@pytest.mark.asyncio
async def test_count_by_state(store: EpisodeStore):
    await store.create_episode(situation="a", group_id="g1", confidence=0.3)
    await store.create_episode(situation="b", group_id="g1", confidence=0.8)
    await store.auto_promote_dry_runs(group_id="g1")

    stats = await store.count_by_state(group_id="g1")
    assert stats["dry_run"] == 1
    assert stats["candidate"] == 1
    assert stats["approved"] == 0


@pytest.mark.asyncio
async def test_count_by_state_all_groups(store: EpisodeStore):
    await store.create_episode(situation="a", group_id="g1")
    await store.create_episode(situation="b", group_id="g2")
    stats = await store.count_by_state(group_id="")
    assert stats["dry_run"] == 2


# ----------------------------------------------------------------------
# D.4 recall path
# ----------------------------------------------------------------------


async def _seed_enabled(
    store: EpisodeStore,
    *,
    group_id: str = "g1",
    situation: str = "scene",
    confidence: float = 0.6,
) -> str:
    ep = await store.create_episode(
        situation=situation, group_id=group_id, confidence=confidence,
    )
    await store.transition_state(ep.episode_id, new_state="candidate")
    await store.transition_state(ep.episode_id, new_state="approved")
    await store.transition_state(ep.episode_id, new_state="enabled_for_prompt")
    return ep.episode_id


@pytest.mark.asyncio
async def test_list_for_recall_only_enabled_for_prompt(store: EpisodeStore):
    visible = await _seed_enabled(store, situation="visible")
    # plain dry_run — must NOT surface
    await store.create_episode(situation="hidden_dryrun", group_id="g1")
    # approved-but-not-enabled — must NOT surface
    ep_approved = await store.create_episode(
        situation="hidden_approved", group_id="g1", confidence=0.7,
    )
    await store.transition_state(ep_approved.episode_id, new_state="candidate")
    await store.transition_state(ep_approved.episode_id, new_state="approved")

    out = await store.list_for_recall(group_id="g1", limit=5)
    assert [e.episode_id for e in out] == [visible]


@pytest.mark.asyncio
async def test_list_for_recall_orders_by_confidence(store: EpisodeStore):
    low = await _seed_enabled(store, situation="low", confidence=0.55)
    high = await _seed_enabled(store, situation="high", confidence=0.85)

    out = await store.list_for_recall(group_id="g1", limit=5)
    assert [e.episode_id for e in out] == [high, low]


@pytest.mark.asyncio
async def test_list_for_recall_filters_by_group(store: EpisodeStore):
    await _seed_enabled(store, group_id="g1", situation="g1ep")
    await _seed_enabled(store, group_id="g2", situation="g2ep")

    g1_only = await store.list_for_recall(group_id="g1", limit=5)
    assert len(g1_only) == 1
    assert g1_only[0].situation == "g1ep"


@pytest.mark.asyncio
async def test_list_for_recall_empty_group_returns_empty(store: EpisodeStore):
    await _seed_enabled(store, group_id="g1")
    # passing empty group must NOT leak to "all groups" — recall is
    # always group-scoped, audit § D.4
    out = await store.list_for_recall(group_id="", limit=5)
    assert out == []


@pytest.mark.asyncio
async def test_list_for_recall_respects_limit(store: EpisodeStore):
    for i in range(4):
        await _seed_enabled(store, situation=f"s{i}", confidence=0.6 + i * 0.05)
    out = await store.list_for_recall(group_id="g1", limit=2)
    assert len(out) == 2


@pytest.mark.asyncio
async def test_list_for_recall_include_decayed_surfaces_disabled(store: EpisodeStore):
    ep = await store.create_episode(
        situation="老场景",
        group_id="g1",
        confidence=0.82,
        outcome_signal="用户后来还是接话了",
    )
    await store.transition_state(ep.episode_id, new_state="candidate")
    await store.transition_state(ep.episode_id, new_state="approved")
    await store.transition_state(ep.episode_id, new_state="enabled_for_prompt")
    await store.transition_state(ep.episode_id, new_state="disabled")

    assert await store.list_for_recall(group_id="g1", limit=5) == []
    recalled = await store.list_for_recall(group_id="g1", limit=5, include_decayed=True)
    assert [item.episode_id for item in recalled] == [ep.episode_id]


@pytest.mark.asyncio
async def test_update_last_used_stamps_episode(store: EpisodeStore):
    ep_id = await _seed_enabled(store)
    before = await store.get_episode(ep_id)
    assert before is not None
    assert before.last_used_at == ""

    ok = await store.update_last_used(ep_id)
    assert ok is True

    after = await store.get_episode(ep_id)
    assert after is not None
    assert after.last_used_at != ""


@pytest.mark.asyncio
async def test_record_observation_dedupes_by_message_and_trigger(store: EpisodeStore):
    ep = await store.create_episode(situation="bot remembered a pattern", group_id="g1")

    first = await store.record_observation(
        ep.episode_id,
        message_id="req_1",
        trigger_type="episode_inject",
        group_id="g1",
        scope="group",
        meta={"candidate_id": "pbc_ep"},
    )
    duplicate = await store.record_observation(
        ep.episode_id,
        message_id="req_1",
        trigger_type="episode_inject",
        group_id="g1",
        scope="group",
    )
    other_trigger = await store.record_observation(
        ep.episode_id,
        message_id="req_1",
        trigger_type="reflection_cite",
        group_id="g1",
        scope="group",
    )

    assert first is True
    assert duplicate is False
    assert other_trigger is True
    db = store._require_db()
    async with db.execute(
        "SELECT COUNT(*) AS cnt FROM episode_observations WHERE episode_id = ?",
        (ep.episode_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row["cnt"] == 2


@pytest.mark.asyncio
async def test_update_last_used_returns_false_for_unknown(store: EpisodeStore):
    ok = await store.update_last_used("ep_does_not_exist")
    assert ok is False


@pytest.mark.asyncio
async def test_update_last_used_handles_empty_id(store: EpisodeStore):
    ok = await store.update_last_used("")
    assert ok is False


# ---------------------------------------------------------------------------
# Memory Episode v2 — decay eligibility / validation / audited setter
# ---------------------------------------------------------------------------

_TZ_SH = ZoneInfo("Asia/Shanghai")


def _future_iso(*, hours: int = 24) -> str:
    return (datetime.now(_TZ_SH) + timedelta(hours=hours)).isoformat(timespec="seconds")


def _past_iso(*, hours: int = 24) -> str:
    return (datetime.now(_TZ_SH) - timedelta(hours=hours)).isoformat(timespec="seconds")


@pytest.mark.asyncio
async def test_create_episode_decay_at_empty_means_no_expiry(store: EpisodeStore):
    ep = await store.create_episode(
        situation="no expiry",
        group_id="g1",
        decay_at="",
    )
    assert ep.decay_at == ""
    fetched = await store.get_episode(ep.episode_id)
    assert fetched is not None
    assert fetched.decay_at == ""


@pytest.mark.asyncio
async def test_create_episode_decay_at_normalizes_aware_iso_to_shanghai(store: EpisodeStore):
    # UTC noon → Asia/Shanghai 20:00 same day
    ep = await store.create_episode(
        situation="normalize tz",
        group_id="g1",
        decay_at="2026-08-01T12:00:00+00:00",
    )
    assert ep.decay_at == "2026-08-01T20:00:00+08:00"
    fetched = await store.get_episode(ep.episode_id)
    assert fetched is not None
    assert fetched.decay_at == "2026-08-01T20:00:00+08:00"


@pytest.mark.asyncio
async def test_create_episode_decay_at_accepts_zulu_suffix(store: EpisodeStore):
    ep = await store.create_episode(
        situation="zulu",
        group_id="g1",
        decay_at="2026-08-01T12:00:00Z",
    )
    assert ep.decay_at == "2026-08-01T20:00:00+08:00"


@pytest.mark.asyncio
async def test_create_episode_decay_at_rejects_naive_iso(store: EpisodeStore):
    with pytest.raises(ValueError):
        await store.create_episode(
            situation="naive",
            group_id="g1",
            decay_at="2026-08-01T12:00:00",
        )


@pytest.mark.asyncio
async def test_create_episode_decay_at_rejects_malformed(store: EpisodeStore):
    with pytest.raises(ValueError):
        await store.create_episode(
            situation="bad",
            group_id="g1",
            decay_at="not-a-timestamp",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [True, False, 123, 1.5, ["2026-08-01T12:00:00+08:00"], {"t": "x"}],
)
async def test_create_episode_decay_at_rejects_non_string(store: EpisodeStore, bad):
    with pytest.raises((TypeError, ValueError)):
        await store.create_episode(
            situation="typed",
            group_id="g1",
            decay_at=bad,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_list_for_recall_excludes_past_decay_without_sweeper(store: EpisodeStore):
    """Default recall must hide expired enabled rows even if expire_decayed has not run."""
    live = await _seed_enabled(store, situation="still live", confidence=0.7)
    expired_id = await _seed_enabled(store, situation="should be hidden", confidence=0.95)
    past = _past_iso(hours=2)
    # Direct DB write of a past decay_at (simulates wall-clock passage without sweeper).
    db = store._require_db()
    await db.execute(
        "UPDATE episodes SET decay_at = ? WHERE episode_id = ?",
        (past, expired_id),
    )
    await db.commit()

    out = await store.list_for_recall(group_id="g1", limit=10)
    assert [e.episode_id for e in out] == [live]
    assert all(e.episode_id != expired_id for e in out)


@pytest.mark.asyncio
async def test_list_for_recall_offset_safe_keeps_future_legacy_utc(
    store: EpisodeStore,
    monkeypatch: pytest.MonkeyPatch,
):
    """Lexicographic ISO compare is wrong across offsets; absolute time must win.

    Probe: '2026-07-16T12:32:15+00:00' is one hour *later* than
    '2026-07-16T19:32:15+08:00', but string compare treats the UTC form as
    smaller (T12… < T19…). Default recall must still keep the future episode;
    expire_decayed must not treat it as past.
    """
    fixed_now = "2026-07-16T19:32:15+08:00"  # 11:32 UTC
    legacy_future_utc = "2026-07-16T12:32:15+00:00"  # 12:32 UTC (still future)
    # Guard the trap still holds under plain string ordering.
    assert not (legacy_future_utc > fixed_now)

    monkeypatch.setattr("services.episodic.store._now_iso", lambda: fixed_now)

    future_id = await _seed_enabled(
        store, situation="legacy utc offset still future", confidence=0.9
    )
    db = store._require_db()
    await db.execute(
        "UPDATE episodes SET decay_at = ? WHERE episode_id = ?",
        (legacy_future_utc, future_id),
    )
    await db.commit()

    out = await store.list_for_recall(group_id="g1", limit=10)
    assert [e.episode_id for e in out] == [future_id]

    n = await store.expire_decayed()
    assert n == 0
    still = await store.get_episode(future_id)
    assert still is not None
    assert still.episode_state == "enabled_for_prompt"
    assert still.decay_at == legacy_future_utc


@pytest.mark.asyncio
async def test_list_for_recall_includes_future_and_empty_decay(store: EpisodeStore):
    empty_id = await _seed_enabled(store, situation="never expires", confidence=0.6)
    future_id = await _seed_enabled(store, situation="expires later", confidence=0.8)
    future = _future_iso(hours=48)
    db = store._require_db()
    await db.execute(
        "UPDATE episodes SET decay_at = ? WHERE episode_id = ?",
        (future, future_id),
    )
    await db.commit()

    out = await store.list_for_recall(group_id="g1", limit=10)
    ids = [e.episode_id for e in out]
    assert future_id in ids
    assert empty_id in ids
    # confidence DESC: future (0.8) before empty (0.6)
    assert ids.index(future_id) < ids.index(empty_id)


@pytest.mark.asyncio
async def test_list_for_recall_include_decayed_is_wide_historical_reader(store: EpisodeStore):
    """include_decayed=True = enabled_for_prompt + disabled; no default expiry exclusion."""
    live = await _seed_enabled(store, situation="live", confidence=0.5)
    expired_enabled = await _seed_enabled(store, situation="expired still enabled", confidence=0.9)
    disabled_id = await _seed_enabled(store, situation="admin disabled", confidence=0.7)
    await store.transition_state(disabled_id, new_state="disabled", actor="admin", reason="test")

    past = _past_iso(hours=1)
    db = store._require_db()
    await db.execute(
        "UPDATE episodes SET decay_at = ? WHERE episode_id = ?",
        (past, expired_enabled),
    )
    await db.commit()

    default = await store.list_for_recall(group_id="g1", limit=10)
    assert [e.episode_id for e in default] == [live]

    wide = await store.list_for_recall(group_id="g1", limit=10, include_decayed=True)
    wide_ids = {e.episode_id for e in wide}
    assert live in wide_ids
    assert expired_enabled in wide_ids  # still enabled_for_prompt but past decay — wide reader keeps it
    assert disabled_id in wide_ids


@pytest.mark.asyncio
async def test_set_decay_at_updates_and_records_revision(store: EpisodeStore):
    ep_id = await _seed_enabled(store, situation="set decay")
    target = "2026-09-01T10:00:00+00:00"
    ok = await store.set_decay_at(
        ep_id,
        decay_at=target,
        actor="admin",
        reason="schedule sunset",
    )
    assert ok is True
    fetched = await store.get_episode(ep_id)
    assert fetched is not None
    assert fetched.decay_at == "2026-09-01T18:00:00+08:00"
    assert fetched.updated_at != ""

    revs = await store.list_revisions(ep_id)
    assert any(r.action == "set_decay_at" for r in revs)
    rev = next(r for r in revs if r.action == "set_decay_at")
    assert rev.actor == "admin"
    assert rev.reason == "schedule sunset"
    assert rev.before.get("decay_at") == ""
    assert rev.after.get("decay_at") == "2026-09-01T18:00:00+08:00"


@pytest.mark.asyncio
async def test_set_decay_at_clear_with_empty_string(store: EpisodeStore):
    ep_id = await _seed_enabled(store, situation="clear decay")
    await store.set_decay_at(
        ep_id,
        decay_at=_future_iso(hours=12),
        actor="admin",
        reason="temp",
    )
    ok = await store.set_decay_at(
        ep_id,
        decay_at="",
        actor="admin",
        reason="clear expiry",
    )
    assert ok is True
    fetched = await store.get_episode(ep_id)
    assert fetched is not None
    assert fetched.decay_at == ""
    revs = await store.list_revisions(ep_id)
    clear_rev = next(r for r in revs if r.action == "set_decay_at" and r.after.get("decay_at") == "")
    assert clear_rev.reason == "clear expiry"


@pytest.mark.asyncio
async def test_set_decay_at_missing_returns_false(store: EpisodeStore):
    ok = await store.set_decay_at(
        "ep_missing_xyz",
        decay_at=_future_iso(),
        actor="admin",
        reason="noop",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_set_decay_at_rejects_invalid(store: EpisodeStore):
    ep_id = await _seed_enabled(store)
    with pytest.raises(ValueError):
        await store.set_decay_at(ep_id, decay_at="2026-01-01T00:00:00", actor="admin")
    with pytest.raises((TypeError, ValueError)):
        await store.set_decay_at(ep_id, decay_at=123, actor="admin")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_expire_decayed_still_disables_past_enabled(store: EpisodeStore):
    """Sweeper remains compatible: past decay_at enabled → disabled."""
    ep_id = await _seed_enabled(store, situation="to expire")
    past = _past_iso(hours=3)
    db = store._require_db()
    await db.execute(
        "UPDATE episodes SET decay_at = ? WHERE episode_id = ?",
        (past, ep_id),
    )
    await db.commit()

    n = await store.expire_decayed()
    assert n == 1
    fetched = await store.get_episode(ep_id)
    assert fetched is not None
    assert fetched.episode_state == "disabled"
    # After sweeper, default recall empty; wide reader still sees disabled
    assert await store.list_for_recall(group_id="g1", limit=5) == []
    wide = await store.list_for_recall(group_id="g1", limit=5, include_decayed=True)
    assert [e.episode_id for e in wide] == [ep_id]
