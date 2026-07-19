"""Production Worldbook StoryArc seed pack — validation + idempotent importer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kernel.types import PluginContext
from plugins.schedule.story_arc import StoryArc, StoryArcStore
from plugins.worldbook.arc_seed import (
    ArcSeedConflictError,
    ArcSeedError,
    ArcSeedResult,
    load_seed_pack,
    seed_missing_arcs,
    validate_seed_pack,
)
from plugins.worldbook.plugin import WorldbookPlugin, WorldbookPluginConfig
from services.worldbook.config import WorldbookConfig

_REPO = Path(__file__).resolve().parents[1]
_PROD_SEED_DIR = _REPO / "config" / "worldbook" / "arcs"
_REQUIRED_IDS = (
    "living_story_v1.main",
    "living_story_v1.side_study",
    "living_story_v1.ambient_park",
)


def _write_arc(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _base_arc(
    *,
    arc_id: str,
    arc_role: str,
    stack_order: int,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "arc_id": arc_id,
        "revision": 0,
        "title": f"title-{arc_id}",
        "scope": "fiction",
        "stage": "active",
        "status": "active",
        "starts_on": "",
        "ends_on": "",
        "arc_role": arc_role,
        "stack_order": stack_order,
        "goals": ["推进虚构日常"],
        "active_conflicts": [],
        "variables": {},
        "partner_states": {},
        "open_threads": [],
        "last_events": [],
        "event_history": [],
        "causal_links": [],
        "deadlines": [],
        "next_day_seed": "",
        "event_budget": {
            "now_step": 0,
            "last_event_step": 0,
            "generated_days": 0,
            "setback_count": 0,
            "events_this_tick": 0,
            "triggered_once": [],
            "cooldowns": {},
            "storylet_available_at": {},
            "last_viewed": {},
            "committed_event_ids": [],
        },
    }
    payload.update(overrides)
    return payload


def _minimal_valid_pack(seed_dir: Path) -> None:
    _write_arc(
        seed_dir / "living_story_v1.main.json",
        _base_arc(
            arc_id="living_story_v1.main",
            arc_role="main",
            stack_order=0,
            partner_states={"tsukasa": {"mood": "steady"}},
        ),
    )
    _write_arc(
        seed_dir / "living_story_v1.side_study.json",
        _base_arc(
            arc_id="living_story_v1.side_study",
            arc_role="side",
            stack_order=10,
        ),
    )
    _write_arc(
        seed_dir / "living_story_v1.ambient_park.json",
        _base_arc(
            arc_id="living_story_v1.ambient_park",
            arc_role="ambient",
            stack_order=20,
            partner_states={"nene": {"mood": "calm"}},
        ),
    )


def test_production_seed_pack_structure_and_contract() -> None:
    arcs = load_seed_pack(_PROD_SEED_DIR)
    validate_seed_pack(arcs)

    by_id = {a.arc_id: a for a in arcs}
    assert set(by_id) == set(_REQUIRED_IDS)

    mains = [a for a in arcs if a.arc_role == "main"]
    sides = [a for a in arcs if a.arc_role == "side"]
    ambients = [a for a in arcs if a.arc_role == "ambient"]
    assert len(mains) == 1
    assert len(sides) >= 1
    assert len(ambients) >= 1

    for arc in arcs:
        assert arc.scope == "fiction"
        assert arc.status == "active"
        assert arc.stage == "active"
        assert int(arc.revision) == 0
        assert arc.last_events == []
        assert arc.event_history == []
        assert arc.causal_links == []
        budget = dict(arc.event_budget or {})
        assert list(budget.get("committed_event_ids") or []) == []
        assert list(budget.get("triggered_once") or []) == []
        for entity_id, state in (arc.partner_states or {}).items():
            assert entity_id in {"tsukasa", "nene"}
            if isinstance(state, dict):
                assert list(state.get("applied_event_ids") or []) == []

    assert by_id["living_story_v1.main"].stack_order == 0
    assert by_id["living_story_v1.side_study"].stack_order == 10
    assert by_id["living_story_v1.ambient_park"].stack_order == 20


@pytest.mark.asyncio
async def test_seeds_all_missing_then_restart_idempotent(tmp_path: Path) -> None:
    seed_dir = tmp_path / "seeds"
    _minimal_valid_pack(seed_dir)
    store = StoryArcStore(tmp_path / "store")
    await store.startup()

    first = seed_missing_arcs(store, seed_dir)
    assert isinstance(first, ArcSeedResult)
    assert sorted(first.seeded_ids) == sorted(_REQUIRED_IDS)
    assert first.existing_ids == []
    assert sorted(store.list_arc_ids()) == sorted(_REQUIRED_IDS)

    second = seed_missing_arcs(store, seed_dir)
    assert second.seeded_ids == []
    assert sorted(second.existing_ids) == sorted(_REQUIRED_IDS)
    assert sorted(store.list_arc_ids()) == sorted(_REQUIRED_IDS)


@pytest.mark.asyncio
async def test_preexisting_matching_arc_preserved_only_missing_seeded(
    tmp_path: Path,
) -> None:
    seed_dir = tmp_path / "seeds"
    _minimal_valid_pack(seed_dir)
    store = StoryArcStore(tmp_path / "store")
    await store.startup()

    # Pre-seed main with unique marker; must survive importer.
    marker_title = "pre-existing-main-must-survive"
    pre = StoryArc.from_dict(
        _base_arc(
            arc_id="living_story_v1.main",
            arc_role="main",
            stack_order=0,
            title=marker_title,
            goals=["already-running"],
        )
    )
    store.save(pre)
    before = store.load("living_story_v1.main")
    assert before is not None
    before_snapshot = before.to_dict()

    result = seed_missing_arcs(store, seed_dir)
    assert "living_story_v1.main" in result.existing_ids
    assert "living_story_v1.main" not in result.seeded_ids
    assert sorted(result.seeded_ids) == [
        "living_story_v1.ambient_park",
        "living_story_v1.side_study",
    ]

    after = store.load("living_story_v1.main")
    assert after is not None
    assert after.to_dict() == before_snapshot
    assert after.title == marker_title
    assert after.goals == ["already-running"]


@pytest.mark.asyncio
async def test_invalid_pack_fails_before_any_write(tmp_path: Path) -> None:
    store = StoryArcStore(tmp_path / "store")
    await store.startup()
    # Pre-existing unrelated arc must remain untouched on pack failure.
    legacy = StoryArc.from_dict(
        _base_arc(
            arc_id="weekly_life_legacy",
            arc_role="side",
            stack_order=99,
            title="legacy-side",
        )
    )
    store.save(legacy)
    before_ids = store.list_arc_ids()
    before_legacy = store.load("weekly_life_legacy")
    assert before_legacy is not None
    before_snap = before_legacy.to_dict()

    cases: list[tuple[str, Any]] = []

    # invalid JSON
    bad_json = tmp_path / "bad_json"
    bad_json.mkdir()
    (bad_json / "broken.json").write_text("{not-json", encoding="utf-8")
    cases.append(("invalid_json", bad_json))

    # factual scope
    factual = tmp_path / "factual"
    _minimal_valid_pack(factual)
    main_path = factual / "living_story_v1.main.json"
    main = json.loads(main_path.read_text(encoding="utf-8"))
    main["scope"] = "factual"
    _write_arc(main_path, main)
    cases.append(("factual_scope", factual))

    # duplicate IDs
    dup = tmp_path / "dup"
    _minimal_valid_pack(dup)
    _write_arc(
        dup / "copy.main.json",
        _base_arc(arc_id="living_story_v1.main", arc_role="main", stack_order=0),
    )
    cases.append(("duplicate_ids", dup))

    # zero main
    zero_main = tmp_path / "zero_main"
    _minimal_valid_pack(zero_main)
    main = json.loads((zero_main / "living_story_v1.main.json").read_text(encoding="utf-8"))
    main["arc_role"] = "side"
    _write_arc(zero_main / "living_story_v1.main.json", main)
    cases.append(("zero_main", zero_main))

    # two main
    two_main = tmp_path / "two_main"
    _minimal_valid_pack(two_main)
    side = json.loads(
        (two_main / "living_story_v1.side_study.json").read_text(encoding="utf-8")
    )
    side["arc_role"] = "main"
    _write_arc(two_main / "living_story_v1.side_study.json", side)
    cases.append(("two_main", two_main))

    # missing side
    no_side = tmp_path / "no_side"
    _minimal_valid_pack(no_side)
    (no_side / "living_story_v1.side_study.json").unlink()
    cases.append(("missing_side", no_side))

    # missing ambient
    no_ambient = tmp_path / "no_ambient"
    _minimal_valid_pack(no_ambient)
    (no_ambient / "living_story_v1.ambient_park.json").unlink()
    cases.append(("missing_ambient", no_ambient))

    # terminal stage
    terminal = tmp_path / "terminal"
    _minimal_valid_pack(terminal)
    main = json.loads((terminal / "living_story_v1.main.json").read_text(encoding="utf-8"))
    main["stage"] = "completed"
    _write_arc(terminal / "living_story_v1.main.json", main)
    cases.append(("terminal_stage", terminal))

    # inactive status
    inactive = tmp_path / "inactive"
    _minimal_valid_pack(inactive)
    main = json.loads((inactive / "living_story_v1.main.json").read_text(encoding="utf-8"))
    main["status"] = "archived"
    _write_arc(inactive / "living_story_v1.main.json", main)
    cases.append(("inactive_status", inactive))

    # nonzero revision
    rev = tmp_path / "rev"
    _minimal_valid_pack(rev)
    main = json.loads((rev / "living_story_v1.main.json").read_text(encoding="utf-8"))
    main["revision"] = 3
    _write_arc(rev / "living_story_v1.main.json", main)
    cases.append(("nonzero_revision", rev))

    # preloaded event history
    history = tmp_path / "history"
    _minimal_valid_pack(history)
    main = json.loads((history / "living_story_v1.main.json").read_text(encoding="utf-8"))
    main["event_history"] = [{"event_id": "preloaded"}]
    _write_arc(history / "living_story_v1.main.json", main)
    cases.append(("event_history", history))

    # preloaded applied / committed history
    applied = tmp_path / "applied"
    _minimal_valid_pack(applied)
    main = json.loads((applied / "living_story_v1.main.json").read_text(encoding="utf-8"))
    main["event_budget"]["committed_event_ids"] = ["evt-1"]
    main["last_events"] = [{"event_id": "evt-1"}]
    _write_arc(applied / "living_story_v1.main.json", main)
    cases.append(("committed_history", applied))

    for label, seed_dir in cases:
        with pytest.raises((ArcSeedError, json.JSONDecodeError, ValueError, TypeError)):
            seed_missing_arcs(store, seed_dir)
        assert store.list_arc_ids() == before_ids, label
        loaded = store.load("weekly_life_legacy")
        assert loaded is not None, label
        assert loaded.to_dict() == before_snap, label
        for rid in _REQUIRED_IDS:
            assert store.load(rid) is None, label


@pytest.mark.asyncio
async def test_existing_role_scope_mismatch_and_foreign_main_fail_closed(
    tmp_path: Path,
) -> None:
    seed_dir = tmp_path / "seeds"
    _minimal_valid_pack(seed_dir)

    # same-id role mismatch
    store_role = StoryArcStore(tmp_path / "store_role")
    await store_role.startup()
    store_role.save(
        StoryArc.from_dict(
            _base_arc(
                arc_id="living_story_v1.main",
                arc_role="side",
                stack_order=0,
                title="role-mismatch",
            )
        )
    )
    before = store_role.load("living_story_v1.main")
    assert before is not None
    snap = before.to_dict()
    with pytest.raises(ArcSeedConflictError):
        seed_missing_arcs(store_role, seed_dir)
    after = store_role.load("living_story_v1.main")
    assert after is not None
    assert after.to_dict() == snap
    assert store_role.load("living_story_v1.side_study") is None

    # same-id scope mismatch
    store_scope = StoryArcStore(tmp_path / "store_scope")
    await store_scope.startup()
    store_scope.save(
        StoryArc.from_dict(
            _base_arc(
                arc_id="living_story_v1.side_study",
                arc_role="side",
                stack_order=10,
                scope="self",
                title="scope-mismatch",
            )
        )
    )
    before_scope = store_scope.load("living_story_v1.side_study")
    assert before_scope is not None
    snap_scope = before_scope.to_dict()
    with pytest.raises(ArcSeedConflictError):
        seed_missing_arcs(store_scope, seed_dir)
    assert store_scope.load("living_story_v1.side_study") is not None
    assert store_scope.load("living_story_v1.side_study").to_dict() == snap_scope  # type: ignore[union-attr]
    assert store_scope.load("living_story_v1.main") is None

    # different explicit main already present
    store_main = StoryArcStore(tmp_path / "store_main")
    await store_main.startup()
    store_main.save(
        StoryArc.from_dict(
            _base_arc(
                arc_id="other.main.line",
                arc_role="main",
                stack_order=0,
                title="foreign-main",
            )
        )
    )
    before_ids = store_main.list_arc_ids()
    with pytest.raises(ArcSeedConflictError):
        seed_missing_arcs(store_main, seed_dir)
    assert store_main.list_arc_ids() == before_ids
    for rid in _REQUIRED_IDS:
        assert store_main.load(rid) is None


@pytest.mark.asyncio
async def test_worldbook_plugin_disabled_is_no_io(tmp_path: Path) -> None:
    seed_dir = tmp_path / "seeds"
    _minimal_valid_pack(seed_dir)
    store_dir = tmp_path / "story_arcs"
    # Intentionally do not create store_dir or seed paths beyond packing.

    plugin = WorldbookPlugin(
        config=WorldbookPluginConfig(
            enabled=False,
            storylet_enabled=True,
            arc_seed_dir=str(seed_dir),
            story_arc_dir=str(store_dir),
        )
    )
    ctx = PluginContext()
    await plugin.on_startup(ctx)
    assert getattr(ctx, "worldbook_runtime", None) is None
    assert getattr(ctx, "story_arc_store", None) is None
    assert not store_dir.exists()


@pytest.mark.asyncio
async def test_enabled_storylet_startup_seeds_before_runtime_and_is_idempotent(
    tmp_path: Path,
) -> None:
    seed_dir = tmp_path / "seeds"
    _minimal_valid_pack(seed_dir)
    store_dir = tmp_path / "story_arcs"
    # Partial crash state: only main present.
    store_pre = StoryArcStore(store_dir)
    await store_pre.startup()
    store_pre.save(
        StoryArc.from_dict(
            _base_arc(
                arc_id="living_story_v1.main",
                arc_role="main",
                stack_order=0,
                title="partial-crash-main",
            )
        )
    )

    cfg = WorldbookPluginConfig(
        enabled=True,
        storylet_enabled=True,
        arc_seed_dir=str(seed_dir),
        story_arc_dir=str(store_dir),
        canon_dir=str(tmp_path / "empty_canon"),
        storylet_dir=str(tmp_path / "empty_storylets"),
        state_dir=str(tmp_path / "state"),
    )
    (tmp_path / "empty_canon").mkdir()
    (tmp_path / "empty_storylets").mkdir()

    plugin = WorldbookPlugin(config=cfg)
    ctx = PluginContext()
    await plugin.on_startup(ctx)

    runtime = getattr(ctx, "worldbook_runtime", None)
    assert runtime is not None
    store = getattr(ctx, "story_arc_store", None)
    assert store is not None
    assert sorted(store.list_arc_ids()) == sorted(_REQUIRED_IDS)
    main = store.load("living_story_v1.main")
    assert main is not None
    assert main.title == "partial-crash-main"

    # Restart: no duplicates, same IDs.
    plugin2 = WorldbookPlugin(config=cfg)
    ctx2 = PluginContext()
    await plugin2.on_startup(ctx2)
    store2 = getattr(ctx2, "story_arc_store", None)
    assert store2 is not None
    assert sorted(store2.list_arc_ids()) == sorted(_REQUIRED_IDS)
    assert len(list(store_dir.glob("*.json"))) == 3


@pytest.mark.asyncio
async def test_needs_store_for_causal_gates_without_storylet_seed(
    tmp_path: Path,
) -> None:
    """Dream/social/schedule need store provision; social path itself never seeds."""
    seed_dir = tmp_path / "seeds"
    _minimal_valid_pack(seed_dir)

    gate_cfgs = [
        WorldbookPluginConfig(
            enabled=True,
            storylet_enabled=False,
            schedule_projection_enabled=True,
            arc_seed_dir=str(seed_dir),
            story_arc_dir=str(tmp_path / "store_schedule"),
            canon_dir=str(tmp_path / "c"),
            storylet_dir=str(tmp_path / "s"),
            state_dir=str(tmp_path / "st"),
        ),
        WorldbookPluginConfig(
            enabled=True,
            storylet_enabled=False,
            dream_proposal_enabled=True,
            arc_seed_dir=str(seed_dir),
            story_arc_dir=str(tmp_path / "store_dream"),
            canon_dir=str(tmp_path / "c"),
            storylet_dir=str(tmp_path / "s"),
            state_dir=str(tmp_path / "st"),
        ),
        WorldbookPluginConfig(
            enabled=True,
            storylet_enabled=False,
            social_evidence_enabled=True,
            arc_seed_dir=str(seed_dir),
            story_arc_dir=str(tmp_path / "store_social"),
            canon_dir=str(tmp_path / "c"),
            storylet_dir=str(tmp_path / "s"),
            state_dir=str(tmp_path / "st"),
        ),
    ]
    (tmp_path / "c").mkdir(exist_ok=True)
    (tmp_path / "s").mkdir(exist_ok=True)
    for cfg in gate_cfgs:
        plugin = WorldbookPlugin(config=cfg)
        ctx = PluginContext()
        await plugin.on_startup(ctx)
        store = getattr(ctx, "story_arc_store", None)
        assert store is not None
        # No storylet gate => no production seed writes.
        assert store.list_arc_ids() == []


def test_worldbook_config_arc_seed_dir_default_and_resolve_paths() -> None:
    cfg = WorldbookConfig()
    assert cfg.arc_seed_dir == "config/worldbook/arcs"
    paths = cfg.resolve_paths("/repo")
    assert paths["arc_seed_dir"] == Path("/repo") / "config/worldbook/arcs"
    assert "story_arc_dir" in paths



# ---------------------------------------------------------------------------
# Codex acceptance-review corrections: raw contract, filename, dirty budget,
# terminal/inactive existing conflicts (fail closed, no write).
# ---------------------------------------------------------------------------


def test_raw_missing_or_invalid_arc_role_scope_status_rejected(tmp_path: Path) -> None:
    """StoryArc.from_dict silently normalizes; seed loader must reject raw first."""
    cases: list[tuple[str, dict[str, Any]]] = [
        ("missing_arc_role", {"arc_role": "__DELETE__"}),
        ("invalid_arc_role", {"arc_role": "bogus"}),
        ("missing_scope", {"scope": "__DELETE__"}),
        ("missing_status", {"status": "__DELETE__"}),
        ("invalid_status", {"status": "paused"}),
        ("invalid_scope", {"scope": "factual"}),
    ]
    for label, patch in cases:
        seed_dir = tmp_path / label
        _minimal_valid_pack(seed_dir)
        main_path = seed_dir / "living_story_v1.main.json"
        raw = json.loads(main_path.read_text(encoding="utf-8"))
        for key, value in patch.items():
            if value == "__DELETE__":
                raw.pop(key, None)
            else:
                raw[key] = value
        _write_arc(main_path, raw)
        with pytest.raises(ArcSeedError):
            load_seed_pack(seed_dir)


def test_filename_stem_must_match_arc_id(tmp_path: Path) -> None:
    seed_dir = tmp_path / "stem"
    _minimal_valid_pack(seed_dir)
    main_path = seed_dir / "living_story_v1.main.json"
    # Rename file so stem != arc_id.
    main_path.rename(seed_dir / "living_story_v1.main.v2.json")
    with pytest.raises(ArcSeedError, match="filename stem"):
        load_seed_pack(seed_dir)

    # content arc_id differs from stem
    seed_dir2 = tmp_path / "stem2"
    _minimal_valid_pack(seed_dir2)
    main_path2 = seed_dir2 / "living_story_v1.main.json"
    payload2 = json.loads(main_path2.read_text(encoding="utf-8"))
    payload2["arc_id"] = "living_story_v1.main.other"
    _write_arc(main_path2, payload2)
    with pytest.raises(ArcSeedError, match="filename stem"):
        load_seed_pack(seed_dir2)


def test_duplicate_json_object_keys_rejected(tmp_path: Path) -> None:
    seed_dir = tmp_path / "dupkeys"
    _minimal_valid_pack(seed_dir)
    main_path = seed_dir / "living_story_v1.main.json"
    body = (
        '{\n'
        '  "arc_id": "living_story_v1.main",\n'
        '  "revision": 0,\n'
        '  "title": "dup",\n'
        '  "scope": "fiction",\n'
        '  "stage": "active",\n'
        '  "status": "active",\n'
        '  "arc_role": "main",\n'
        '  "arc_role": "side",\n'
        '  "stack_order": 0,\n'
        '  "goals": [],\n'
        '  "active_conflicts": [],\n'
        '  "variables": {},\n'
        '  "partner_states": {},\n'
        '  "open_threads": [],\n'
        '  "last_events": [],\n'
        '  "event_history": [],\n'
        '  "causal_links": [],\n'
        '  "deadlines": [],\n'
        '  "next_day_seed": "",\n'
        '  "starts_on": "",\n'
        '  "ends_on": "",\n'
        '  "event_budget": {\n'
        '    "now_step": 0,\n'
        '    "last_event_step": 0,\n'
        '    "generated_days": 0,\n'
        '    "setback_count": 0,\n'
        '    "events_this_tick": 0,\n'
        '    "triggered_once": [],\n'
        '    "cooldowns": {},\n'
        '    "storylet_available_at": {},\n'
        '    "last_viewed": {},\n'
        '    "committed_event_ids": []\n'
        '  }\n'
        '}\n'
    )
    main_path.write_text(body, encoding="utf-8")
    with pytest.raises(ArcSeedError):
        load_seed_pack(seed_dir)


@pytest.mark.asyncio
async def test_dirty_budget_counters_and_maps_rejected_before_write(
    tmp_path: Path,
) -> None:
    store = StoryArcStore(tmp_path / "store")
    await store.startup()
    before_ids = store.list_arc_ids()

    counter_keys = (
        "now_step",
        "last_event_step",
        "generated_days",
        "setback_count",
        "events_this_tick",
    )
    map_keys = (
        ("cooldowns", {"x": 1}),
        ("storylet_available_at", {"s": 3}),
        ("last_viewed", {"e": 2}),
    )
    list_keys = (
        ("triggered_once", ["once-1"]),
        ("committed_event_ids", ["c1"]),
    )

    dirty_cases: list[tuple[str, dict[str, Any]]] = []
    for key in counter_keys:
        dirty_cases.append((f"counter_{key}", {key: 1}))
    for key, value in map_keys:
        dirty_cases.append((f"map_{key}", {key: value}))
    for key, value in list_keys:
        dirty_cases.append((f"list_{key}", {key: value}))

    for label, budget_patch in dirty_cases:
        seed_dir = tmp_path / label
        _minimal_valid_pack(seed_dir)
        main_path = seed_dir / "living_story_v1.main.json"
        raw = json.loads(main_path.read_text(encoding="utf-8"))
        raw["event_budget"].update(budget_patch)
        _write_arc(main_path, raw)
        with pytest.raises(ArcSeedError):
            seed_missing_arcs(store, seed_dir)
        assert store.list_arc_ids() == before_ids, label
        for rid in _REQUIRED_IDS:
            assert store.load(rid) is None, label


@pytest.mark.asyncio
async def test_existing_terminal_or_inactive_same_id_conflicts_preserve_bytes(
    tmp_path: Path,
) -> None:
    seed_dir = tmp_path / "seeds"
    _minimal_valid_pack(seed_dir)

    conflict_cases: list[tuple[str, dict[str, Any]]] = [
        ("terminal_completed", {"stage": "completed", "status": "active"}),
        ("terminal_archived_stage", {"stage": "archived", "status": "active"}),
        ("inactive_status", {"stage": "active", "status": "inactive"}),
        ("paused_status", {"stage": "active", "status": "paused"}),
        ("archived_status", {"stage": "active", "status": "archived"}),
    ]
    for label, overrides in conflict_cases:
        store = StoryArcStore(tmp_path / f"store_{label}")
        await store.startup()
        pre = StoryArc.from_dict(
            _base_arc(
                arc_id="living_story_v1.main",
                arc_role="main",
                stack_order=0,
                title=f"must-survive-{label}",
                goals=["do-not-touch"],
                **overrides,
            )
        )
        store.save(pre)
        before = store.load("living_story_v1.main")
        assert before is not None
        snap = before.to_dict()
        arc_files = list((tmp_path / f"store_{label}").rglob("*.json"))
        bytes_before = {p: p.read_bytes() for p in arc_files}

        with pytest.raises(ArcSeedConflictError):
            seed_missing_arcs(store, seed_dir)

        after = store.load("living_story_v1.main")
        assert after is not None, label
        assert after.to_dict() == snap, label
        assert after.title == f"must-survive-{label}", label
        assert store.load("living_story_v1.side_study") is None, label
        assert store.load("living_story_v1.ambient_park") is None, label
        for p, data in bytes_before.items():
            assert p.read_bytes() == data, label


@pytest.mark.asyncio
async def test_existing_nonterminal_evolved_stages_preserved(
    tmp_path: Path,
) -> None:
    """setback/recovery/planning on matching role/scope must be preserved."""
    seed_dir = tmp_path / "seeds"
    _minimal_valid_pack(seed_dir)
    for stage in ("setback", "recovery", "planning", "active"):
        store = StoryArcStore(tmp_path / f"store_ok_{stage}")
        await store.startup()
        pre = StoryArc.from_dict(
            _base_arc(
                arc_id="living_story_v1.main",
                arc_role="main",
                stack_order=0,
                title=f"evolved-{stage}",
                stage=stage,
                status="active",
            )
        )
        store.save(pre)
        before = store.load("living_story_v1.main")
        assert before is not None
        snap = before.to_dict()
        result = seed_missing_arcs(store, seed_dir)
        assert "living_story_v1.main" in result.existing_ids
        assert "living_story_v1.main" not in result.seeded_ids
        after = store.load("living_story_v1.main")
        assert after is not None
        assert after.to_dict() == snap
        assert sorted(result.seeded_ids) == [
            "living_story_v1.ambient_park",
            "living_story_v1.side_study",
        ]


@pytest.mark.asyncio
async def test_second_load_race_rechecks_terminal_conflict(tmp_path: Path) -> None:
    """If an ID appears between preflight and write, re-check conflict."""
    seed_dir = tmp_path / "seeds"
    _minimal_valid_pack(seed_dir)
    store = StoryArcStore(tmp_path / "store_race")
    await store.startup()

    real_load = store.load
    inject = StoryArc.from_dict(
        _base_arc(
            arc_id="living_story_v1.side_study",
            arc_role="side",
            stack_order=10,
            title="raced-terminal",
            stage="completed",
            status="active",
        )
    )
    # Count loads of side_study only.
    side_loads = {"n": 0}

    def flaky_load(arc_id: str):  # type: ignore[no-untyped-def]
        if arc_id != "living_story_v1.side_study":
            return real_load(arc_id)
        side_loads["n"] += 1
        # Preflight (first load): missing. Write-loop (second+): terminal raced-in.
        if side_loads["n"] == 1:
            return None
        return inject

    store.load = flaky_load  # type: ignore[method-assign]
    with pytest.raises(ArcSeedConflictError):
        seed_missing_arcs(store, seed_dir)
    store.load = real_load  # type: ignore[method-assign]
    # The raced terminal snapshot was never written through save path from seed.
    assert store.load("living_story_v1.side_study") is None
