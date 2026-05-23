"""Tests for EpisodeStore: schema, CRUD, state machine, revision tracking."""

from __future__ import annotations

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
