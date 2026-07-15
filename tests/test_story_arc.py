from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

import pytest

from plugins.schedule import story_arc as story_arc_module
from plugins.schedule.plugin import ScheduleConfig
from plugins.schedule.story_arc import (
    FictionPartnerProfile,
    FictionPartnerState,
    FictionPartnerStateStore,
    StoryArc,
    StoryArcEventCandidate,
    StoryArcStore,
    can_trigger_event,
    choose_best_least_recently_viewed,
    record_event_trigger,
)


def _arc() -> StoryArc:
    return StoryArc(
        arc_id="stage_play_competition_week",
        title="舞台剧比赛准备周",
        scope="fiction",
        starts_on="2026-06-08",
        ends_on="2026-06-14",
        stage="preparation",
        goals=["完成舞台剧比赛准备", "兼顾期末复习"],
        active_conflicts=["排练时间不足", "考试压力升高"],
        variables={
            "deadline_days_left": 6,
            "exam_pressure": 0.7,
            "rehearsal_progress": 0.35,
            "team_morale": 0.6,
        },
        partner_states={
            "天马司": {"mood": "亢奋但压力大", "availability": "normal"},
            "草薙宁宁": {"mood": "担心动作完成度", "availability": "normal"},
        },
        open_threads=["是否降低动作难度", "是否周六追加排练"],
        last_events=[{"date": "2026-06-08", "summary": "第一次整排进度慢"}],
        next_day_seed="在复习和排练之间做取舍",
    )


@pytest.mark.asyncio
async def test_story_arc_store_empty_active_returns_none(tmp_path) -> None:
    store = StoryArcStore(tmp_path / "storage" / "living_persona" / "story_arcs")

    await store.startup()

    assert store.load_active() is None
    assert store.list_arc_ids() == []


@pytest.mark.asyncio
async def test_story_arc_store_round_trip_preserves_fields(tmp_path) -> None:
    store = StoryArcStore(tmp_path / "storage" / "living_persona" / "story_arcs")
    arc = _arc()

    await store.startup()
    store.save(arc)
    loaded = store.load("stage_play_competition_week")

    assert loaded == arc
    assert loaded is not None
    assert loaded.to_dict()["arc_id"] == "stage_play_competition_week"
    assert loaded.to_dict()["partner_states"]["天马司"]["availability"] == "normal"
    assert loaded.to_dict()["last_events"][0]["summary"] == "第一次整排进度慢"
    assert store.load_active(on_date="2026-06-10") == arc
    assert store.list_arc_ids() == ["stage_play_competition_week"]


def test_story_arc_from_dict_allows_partner_states_placeholder() -> None:
    arc = StoryArc.from_dict({
        "arc_id": "stage_play_competition_week",
        "title": "舞台剧比赛准备周",
    })

    assert arc.partner_states == {}
    assert set(arc.to_dict()) == {
        "arc_id",
        "revision",
        "title",
        "scope",
        "starts_on",
        "ends_on",
        "stage",
        "goals",
        "active_conflicts",
        "variables",
        "partner_states",
        "open_threads",
        "last_events",
        "next_day_seed",
        "event_budget",
    }


@pytest.mark.asyncio
async def test_story_arc_store_rejects_malformed_json(tmp_path) -> None:
    root = tmp_path / "storage" / "living_persona" / "story_arcs"
    root.mkdir(parents=True)
    (root / "bad.json").write_text("{not json", encoding="utf-8")
    store = StoryArcStore(root)

    assert store.load("bad") is None
    assert store.load("../bad") is None


@pytest.mark.asyncio
async def test_story_arc_store_rejects_stale_snapshot_overwrite(tmp_path) -> None:
    store = StoryArcStore(tmp_path / "story_arcs")
    await store.startup()
    store.save(StoryArc(arc_id="shared_arc", variables={"counter": 0}))
    first = store.load("shared_arc")
    stale = store.load("shared_arc")
    assert first is not None and stale is not None

    first.variables["first_writer"] = True
    store.save(first)
    stale.variables["stale_writer"] = True

    with pytest.raises(RuntimeError, match="revision"):
        store.save(stale)

    loaded = store.load("shared_arc")
    assert loaded is not None
    assert loaded.variables == {"counter": 0, "first_writer": True}


@pytest.mark.asyncio
async def test_story_arc_store_update_serializes_concurrent_writers(tmp_path) -> None:
    root = tmp_path / "story_arcs"
    store = StoryArcStore(root)
    await store.startup()
    store.save(StoryArc(arc_id="shared_arc", variables={"counter": 0}))

    def increment() -> None:
        writer = StoryArcStore(root)

        def mutate(arc: StoryArc) -> None:
            arc.variables["counter"] = int(arc.variables.get("counter", 0)) + 1

        writer.update("shared_arc", mutate)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(increment) for _ in range(24)]
        for future in futures:
            future.result()

    loaded = store.load("shared_arc")
    assert loaded is not None
    assert loaded.variables["counter"] == 24
    assert loaded.revision == 25


@pytest.mark.asyncio
async def test_story_arc_store_clean_seed_requires_explicit_fiction_factory(tmp_path) -> None:
    calls: list[str] = []

    def seed_factory(on_date: str) -> StoryArc:
        calls.append(on_date)
        return StoryArc(
            arc_id="first_fiction_arc",
            title="显式测试虚构弧",
            scope="fiction",
            starts_on=on_date,
            stage="planning",
        )

    store = StoryArcStore(tmp_path / "story_arcs", seed_factory=seed_factory)
    await store.startup()

    first = store.ensure_seeded("2026-06-10")
    reused = store.ensure_seeded("2026-06-10")

    assert first is not None and reused is not None
    assert first.arc_id == "first_fiction_arc"
    assert reused.arc_id == "first_fiction_arc"
    assert calls == ["2026-06-10"]
    assert store.list_arc_ids() == ["first_fiction_arc"]


@pytest.mark.asyncio
async def test_default_runtime_store_seeds_a_bounded_fiction_arc(tmp_path) -> None:
    factory = getattr(story_arc_module, "create_default_story_arc_store", None)
    assert callable(factory), "runtime must wire an explicit default fiction seed factory"

    store = cast(Any, factory)(tmp_path / "story_arcs")
    await store.startup()
    seeded = store.ensure_seeded("2026-07-15")

    assert seeded is not None
    assert seeded.scope == "fiction"
    assert seeded.starts_on == "2026-07-15"
    assert seeded.ends_on == "2026-07-21"
    assert seeded.stage == "planning"
    assert seeded.goals
    assert seeded.active_conflicts
    assert store.load_active(on_date="2026-07-15") is not None


@pytest.mark.asyncio
async def test_story_arc_store_rejects_non_fiction_seed_factory(tmp_path) -> None:
    store = StoryArcStore(
        tmp_path / "story_arcs",
        seed_factory=lambda on_date: StoryArc(
            arc_id="real_person_arc",
            scope="factual",
            starts_on=on_date,
        ),
    )
    await store.startup()

    with pytest.raises(ValueError, match="fiction"):
        store.ensure_seeded("2026-06-10")

    assert store.list_arc_ids() == []


@pytest.mark.asyncio
async def test_story_arc_store_filters_future_and_archives_expired_or_terminal(tmp_path) -> None:
    store = StoryArcStore(tmp_path / "storage" / "living_persona" / "story_arcs")
    await store.startup()
    store.save(StoryArc(
        arc_id="expired_arc",
        starts_on="2026-06-01",
        ends_on="2026-06-09",
        stage="active",
    ))
    store.save(StoryArc(
        arc_id="terminal_arc",
        starts_on="2026-06-01",
        ends_on="2026-06-20",
        stage="completed",
    ))
    store.save(StoryArc(
        arc_id="future_arc",
        starts_on="2026-06-11",
        ends_on="2026-06-20",
        stage="planning",
    ))
    store.save(StoryArc(
        arc_id="active_arc",
        starts_on="2026-06-01",
        ends_on="2026-06-20",
        stage="active",
    ))

    active = store.load_active(on_date="2026-06-10")

    assert active is not None
    assert active.arc_id == "active_arc"
    assert store.list_arc_ids() == ["active_arc", "future_arc"]
    assert store.list_archived_arc_ids() == ["expired_arc", "terminal_arc"]


@pytest.mark.asyncio
async def test_story_arc_store_future_only_returns_none_without_archiving(tmp_path) -> None:
    store = StoryArcStore(tmp_path / "story_arcs")
    await store.startup()
    store.save(StoryArc(
        arc_id="future_arc",
        starts_on="2026-06-11",
        ends_on="2026-06-20",
        stage="planning",
    ))

    assert store.load_active(on_date="2026-06-10") is None
    assert store.list_arc_ids() == ["future_arc"]
    assert store.list_archived_arc_ids() == []


def test_story_arc_config_default_off_and_override() -> None:
    cfg = ScheduleConfig()
    assert cfg.story_arc_enabled is False

    enabled = ScheduleConfig.model_validate({"story_arc_enabled": True})
    assert enabled.story_arc_enabled is True


def test_story_arc_event_once_only_blocks_second_trigger() -> None:
    arc = _arc()
    candidate = StoryArcEventCandidate(
        event_id="sprained_ankle",
        event_type="partner_setback",
        severity="setback",
        once_only=True,
    )

    assert can_trigger_event(arc, candidate, now_step=1) is True
    assert record_event_trigger(arc, candidate, now_step=1) is True
    assert can_trigger_event(arc, candidate, now_step=2) is False
    assert record_event_trigger(arc, candidate, now_step=2) is False
    assert arc.event_budget["setback_count"] == 1
    assert arc.event_budget["triggered_once"] == ["sprained_ankle"]


def test_story_arc_event_setback_budget_is_per_arc_once() -> None:
    arc = _arc()
    first = StoryArcEventCandidate(
        event_id="sprained_ankle",
        event_type="partner_setback",
        severity="setback",
    )
    second = StoryArcEventCandidate(
        event_id="missed_train",
        event_type="partner_setback",
        severity="setback",
    )

    assert record_event_trigger(arc, first, now_step=1) is True
    assert can_trigger_event(arc, second, now_step=2) is False


def test_story_arc_event_cooldown_blocks_same_key_until_expiry() -> None:
    arc = _arc()
    candidate = StoryArcEventCandidate(
        event_id="extra_rehearsal",
        event_type="daily_practice",
        cooldown_key="practice",
        cooldown_steps=3,
    )
    same_key = StoryArcEventCandidate(
        event_id="costume_adjustment",
        event_type="daily_practice",
        cooldown_key="practice",
    )

    assert record_event_trigger(arc, candidate, now_step=10) is True
    assert can_trigger_event(arc, same_key, now_step=12) is False
    assert can_trigger_event(arc, same_key, now_step=13) is True


def test_story_arc_best_least_recently_viewed_rotates_equal_salience() -> None:
    arc = _arc()
    candidates = [
        StoryArcEventCandidate(event_id="daily_practice", salience=0.7),
        StoryArcEventCandidate(event_id="exam_review", salience=0.7),
        StoryArcEventCandidate(event_id="low_priority_chat", salience=0.2),
    ]

    selected = choose_best_least_recently_viewed(arc, candidates, now_step=1)
    assert selected is not None
    assert selected.event_id == "daily_practice"
    assert record_event_trigger(arc, selected, now_step=1) is True

    selected_again = choose_best_least_recently_viewed(arc, candidates, now_step=2)
    assert selected_again is not None
    assert selected_again.event_id == "exam_review"


def test_story_arc_json_shape_is_plain_external_ledger() -> None:
    data = _arc().to_dict()

    encoded = json.dumps(data, ensure_ascii=False)

    assert "stage_play_competition_week" in encoded
    assert "storage/living_persona" not in encoded


@pytest.mark.asyncio
async def test_fiction_partner_state_store_lazy_generates_and_reuses_cards(tmp_path) -> None:
    store = FictionPartnerStateStore(tmp_path / "storage" / "living_persona" / "partner_states")
    profiles = [
        FictionPartnerProfile(
            entity_id="tenma_tsukasa",
            display_name="天马司",
            pinned_profile="W×S 成员，舞台中心感强。",
            constraints=("kind=fiction",),
        ),
    ]

    await store.startup()
    first = store.ensure_cards(profiles)
    first[0].mood = "亢奋但压力大"
    first[0].recent_events.append("第一次整排进度慢")
    store.save(first[0])
    second = store.ensure_cards(profiles)

    assert len(first) == 1
    assert second[0].mood == "亢奋但压力大"
    assert second[0].kind == "fiction"
    assert second[0].to_arc_state()["kind"] == "fiction"
    assert store.list_entity_ids() == ["tenma_tsukasa"]


def test_fiction_partner_state_rejects_factual_kind() -> None:
    with pytest.raises(ValueError, match="only fiction"):
        FictionPartnerState.from_dict({
            "entity_id": "real_user_123",
            "kind": "factual",
            "display_name": "真人用户",
        })

    state = FictionPartnerState(entity_id="tenma_tsukasa", kind="factual")
    with pytest.raises(ValueError, match="kind='fiction'"):
        state.to_dict()
