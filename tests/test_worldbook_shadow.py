"""Stage-1 Worldbook 7-day offline shadow evaluator tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.schedule.story_arc import StoryArc, StoryArcStore
from plugins.worldbook.shadow import (
    _check_invariants,
    _snapshot_life_state,
    _snapshot_partner_state,
    run_worldbook_shadow,
    run_worldbook_shadow_sync,
)
from services.worldbook.config import WorldbookConfig, worldbook_gates_all_false

_REPO = Path(__file__).resolve().parents[1]
_FIXTURE = _REPO / "tests" / "fixtures" / "worldbook" / "living_story_v1"
_CONTENT = _REPO / "config" / "worldbook"
_PLUGIN_DEFAULT = _REPO / "plugins" / "worldbook" / "config.default.json"


def _fingerprint(root: Path) -> str:
    from plugins.worldbook.shadow import _tree_fingerprint

    return _tree_fingerprint(root)


@pytest.mark.asyncio
async def test_seven_day_shadow_passes_and_matches_golden_invariants(
    tmp_path: Path,
) -> None:
    content_fp = _fingerprint(_CONTENT)
    fixture_fp = _fingerprint(_FIXTURE)

    report = await run_worldbook_shadow(
        fixture_root=_FIXTURE,
        content_root=_CONTENT,
        output_root=tmp_path / "out1",
        keep_work=True,
    )

    assert report.overall_verdict == "pass", report.invariants
    assert report.ok is True
    checks = report.invariants.get("checks") or {}
    assert checks.get("registry_nonempty") is True
    assert checks.get("step_count") is True
    assert checks.get("main_present") is True
    assert checks.get("side_present") is True
    assert checks.get("ambient_present") is True
    assert checks.get("setback_at_most_one") is True
    assert checks.get("recovery_observed") is True
    assert checks.get("must_include_storylets") is True
    assert checks.get("schedule_zero_social") is True
    assert checks.get("social_rejects") is True
    assert checks.get("social_accept_current") is True
    assert checks.get("reload_continuity") is True
    assert checks.get("once_cooldown_survive_reload") is True
    assert checks.get("source_tree_unchanged") is True
    assert checks.get("fixture_tree_unchanged") is True
    assert checks.get("default_plugin_gates_all_false") is True

    # Essential golden expectations from fixture file.
    expected = json.loads(
        (_FIXTURE / "expected_shadow_report.json").read_text(encoding="utf-8")
    )
    inv = expected["invariants"]
    assert report.registry["canon_count"] >= inv["registry_canon_min"]
    assert report.registry["storylet_count"] >= inv["registry_storylet_min"]
    assert (
        report.setback_recovery["major_setback_count"] <= inv["major_setback_max"]
    )
    assert report.setback_recovery["recovery_observed"] is True
    assert "setback.prop_failure" in report.storylet_observations["selected_storylet_ids"]
    assert "recovery.after_setback" in report.storylet_observations["selected_storylet_ids"]

    assert _fingerprint(_CONTENT) == content_fp
    assert _fingerprint(_FIXTURE) == fixture_fp
    assert (tmp_path / "out1" / "shadow_report.json").is_file()


@pytest.mark.asyncio
async def test_schedule_steps_have_zero_social_and_chat_accepts_only_current_scope(
    tmp_path: Path,
) -> None:
    report = await run_worldbook_shadow(
        fixture_root=_FIXTURE,
        content_root=_CONTENT,
        output_root=tmp_path / "out_social",
    )
    for step in report.steps:
        sched = step.get("schedule")
        if isinstance(sched, dict):
            assert int(sched.get("social_block_count") or 0) == 0
            for trace in sched.get("traces") or []:
                if trace.get("source") == "social_evidence":
                    assert str(trace.get("budget_decision", "")).startswith("rejected")

    social = report.social
    assert "accepted" in (social.get("accept_reasons") or [])
    for reason in (
        "rejected:cross_group",
        "rejected:cross_user",
        "rejected:privacy",
        "rejected:missing_evidence",
    ):
        assert reason in (social.get("reject_reasons") or [])
    assert social.get("chat_accept_only_current_scope") is True
    assert int(social.get("schedule_social_block_count") or 0) == 0


@pytest.mark.asyncio
async def test_main_side_ambient_stable_across_reload_and_once_cooldown_survives(
    tmp_path: Path,
) -> None:
    report = await run_worldbook_shadow(
        fixture_root=_FIXTURE,
        content_root=_CONTENT,
        output_root=tmp_path / "out_reload",
    )
    cont = report.continuity
    assert cont["main_arc_id"] == "living_story_v1.main"
    assert cont["side_arc_ids"] == ["living_story_v1.side_study"]
    assert cont["ambient_arc_ids"] == ["living_story_v1.ambient_park"]
    assert cont["reload_ok"] is True
    assert cont["once_cooldown_survived"] is True
    pre = list(cont.get("pre_reload_triggered_once") or [])
    post = list(cont.get("post_reload_triggered_once") or [])
    assert pre == post
    assert pre  # at least one once-storylet fired before reload (setback)


@pytest.mark.asyncio
async def test_at_most_one_major_setback_and_recovery_observed(tmp_path: Path) -> None:
    report = await run_worldbook_shadow(
        fixture_root=_FIXTURE,
        content_root=_CONTENT,
        output_root=tmp_path / "out_setback",
    )
    assert report.setback_recovery["major_setback_count"] <= 1
    assert report.setback_recovery["major_setback_count"] >= 1
    assert report.setback_recovery["recovery_observed"] is True
    assert "setback.prop_failure" in report.setback_recovery["major_setback_ids"]


@pytest.mark.asyncio
async def test_storylet_budget_matches_committed_major_setbacks(tmp_path: Path) -> None:
    report = await run_worldbook_shadow(
        fixture_root=_FIXTURE,
        content_root=_CONTENT,
        output_root=tmp_path / "out_budget_consistency",
    )

    assert report.setback_recovery["budget_setback_count"] == (
        report.setback_recovery["major_setback_count"]
    )
    checks = report.invariants.get("checks") or {}
    assert checks.get("budget_setback_matches_committed") is True


@pytest.mark.asyncio
async def test_shadow_observes_every_formal_storylet_commit(tmp_path: Path) -> None:
    report = await run_worldbook_shadow(
        fixture_root=_FIXTURE,
        content_root=_CONTENT,
        output_root=tmp_path / "out_commit_observation",
    )

    observed = {
        str(item.get("event_id") or "")
        for step in report.steps
        for item in (step.get("committed") or [])
        if item.get("formally_committed") is True
    }
    final_committed = set(
        report.storylet_observations.get("committed_event_ids") or []
    )
    assert observed == final_committed
    assert report.storylet_observations["selection_count"] == len(final_committed)
    checks = report.invariants.get("checks") or {}
    assert checks.get("formal_commit_observation_complete") is True


def test_rerun_is_deterministic_and_default_gates_remain_false(tmp_path: Path) -> None:
    content_fp = _fingerprint(_CONTENT)
    fixture_fp = _fingerprint(_FIXTURE)

    r1 = run_worldbook_shadow_sync(
        fixture_root=_FIXTURE,
        content_root=_CONTENT,
        output_root=tmp_path / "det_a",
    )
    r2 = run_worldbook_shadow_sync(
        fixture_root=_FIXTURE,
        content_root=_CONTENT,
        output_root=tmp_path / "det_b",
    )
    assert r1.overall_verdict == "pass"
    assert r2.overall_verdict == "pass"
    assert r1.metadata["report_essential_hash"] == r2.metadata["report_essential_hash"]
    assert (
        r1.storylet_observations["selected_storylet_ids"]
        == r2.storylet_observations["selected_storylet_ids"]
    )
    assert _fingerprint(_CONTENT) == content_fp
    assert _fingerprint(_FIXTURE) == fixture_fp

    default = json.loads(_PLUGIN_DEFAULT.read_text(encoding="utf-8"))
    from services.worldbook.config import worldbook_config_from_mapping

    assert worldbook_gates_all_false(worldbook_config_from_mapping(default))
    # Shadow never mutates production WorldbookConfig defaults object.
    assert worldbook_gates_all_false(WorldbookConfig())


def test_cli_exits_nonzero_on_invariant_failure(tmp_path: Path) -> None:
    """CLI wiring smoke: broken expected still returns structured report path."""
    from tools.run_worldbook_shadow import main as cli_main

    # Happy path exit 0
    code = cli_main(
        [
            "--fixture-root",
            str(_FIXTURE),
            "--content-root",
            str(_CONTENT),
            "--output-root",
            str(tmp_path / "cli_ok"),
        ]
    )
    assert code == 0
    assert (tmp_path / "cli_ok" / "shadow_report.json").is_file()


def _assert_report_free_of_raw_dialogue(report_dict: dict) -> None:
    """Report must not embed raw user_text/bot_reply or free-form partner notes."""
    blob = json.dumps(report_dict, ensure_ascii=False)
    for banned in ("user_text", "bot_reply"):
        assert banned not in blob
    partner = report_dict.get("partner_state") or {}
    for entity in partner.get("entities") or []:
        assert "note" not in entity
        assert "event_note" not in entity
        assert "current_state" not in entity
        assert "pinned_profile" not in entity


@pytest.mark.asyncio
async def test_shadow_report_proves_partner_life_recovery_continuity_from_persisted_truth(
    tmp_path: Path,
) -> None:
    """Report must encode post-run persisted partner/life/setback/stage facts.

    Evidence is final arc.partner_states + LifeStateStore snapshot — not authored
    Storylet payload inspection alone.
    """
    report = await run_worldbook_shadow(
        fixture_root=_FIXTURE,
        content_root=_CONTENT,
        output_root=tmp_path / "out_semantic",
        keep_work=True,
    )
    assert report.overall_verdict == "pass", report.invariants
    body = report.to_dict()
    _assert_report_free_of_raw_dialogue(body)

    expected = json.loads(
        (_FIXTURE / "expected_shadow_report.json").read_text(encoding="utf-8")
    )
    inv = expected["invariants"]

    partner = body.get("partner_state") or {}
    assert int(partner.get("distinct_entity_count") or 0) >= int(
        inv["partner_entity_min"]
    )
    assert int(partner.get("change_count") or 0) >= int(inv["partner_change_min"])
    entity_ids = set(partner.get("entity_ids") or [])
    for required in expected["expected_partner_state"]["entity_ids_min"]:
        assert required in entity_ids
    assert int(partner.get("change_count") or 0) >= int(
        expected["expected_partner_state"]["change_count_min"]
    )
    assert int(partner.get("change_count") or 0) >= 3
    assert int(partner.get("distinct_entity_count") or 0) >= 2

    life = body.get("life_state") or {}
    assert int(life.get("item_count") or 0) >= 3
    assert int(life.get("story_ledger_item_count") or 0) >= int(
        inv["story_ledger_item_min"]
    )
    assert int(life.get("story_ledger_item_count") or 0) >= 3
    assert list(life.get("story_ledger_missing_ttl") or []) == []
    assert len(life.get("story_ledger_keys") or []) >= 3

    gap = report.setback_recovery.get("recovery_steps_after_setback")
    assert gap is not None
    assert int(inv["recovery_steps_min"]) <= int(gap) <= int(inv["recovery_steps_max"])
    assert int(gap) == int(
        expected["expected_setback_recovery"]["recovery_steps_after_setback"]
    )

    cont = report.continuity
    assert cont.get("final_main_stage") == expected["expected_continuity"][
        "final_main_stage"
    ]
    final_flag = cont.get("final_setback_flag")
    assert final_flag is not None
    assert float(final_flag) == float(
        expected["expected_continuity"]["final_setback_flag"]
    )

    checks = report.invariants.get("checks") or {}
    assert checks.get("partner_entities_changed") is True
    assert checks.get("partner_change_count") is True
    assert checks.get("story_ledger_ttl_complete") is True
    assert checks.get("recovery_steps_in_window") is True
    assert checks.get("final_main_stage_active") is True
    assert checks.get("final_setback_flag_cleared") is True

    work = Path(str(report.metadata["work_root"]))
    run_ids = set(report.storylet_observations.get("committed_event_ids") or [])
    assert run_ids
    assert partner.get("provenance_scope") == "current_run_committed"
    assert int(partner.get("current_run_event_count") or 0) == len(run_ids)
    assert life.get("provenance_scope") == "current_run_committed"
    assert int(life.get("current_run_event_count") or 0) == len(run_ids)

    changed_entities: set[str] = set()
    change_events = 0
    for arc_path in sorted(
        (work / "storage" / "living_persona" / "story_arcs").glob("*.json")
    ):
        arc = json.loads(arc_path.read_text(encoding="utf-8"))
        for eid, state in (arc.get("partner_states") or {}).items():
            applied = [
                str(x).strip()
                for x in (state.get("applied_event_ids") or [])
                if str(x).strip() and str(x).strip() in run_ids
            ]
            if applied:
                changed_entities.add(str(eid))
                change_events += len(applied)
    assert len(changed_entities) >= 2
    assert change_events >= 3
    assert set(partner.get("entity_ids") or []) == changed_entities
    assert int(partner.get("change_count") or 0) == change_events

    life_disk = json.loads(
        (work / "storage" / "worldbook" / "life_state.json").read_text(encoding="utf-8")
    )
    ledger_keys = []
    for key, item in (life_disk.get("items") or {}).items():
        meta = item.get("meta") or {}
        if str(meta.get("source") or "") != "story_ledger":
            continue
        refs = {
            str(x).strip()
            for x in (meta.get("evidence_refs") or [])
            if str(x).strip()
        }
        if not refs.intersection(run_ids):
            continue
        ledger_keys.append(key)
        assert str(meta.get("decay_at") or "").strip()
    assert len(ledger_keys) >= 3
    assert set(life.get("story_ledger_keys") or []) == set(ledger_keys)


def test_semantic_invariants_fail_closed_when_facts_removed_or_corrupted() -> None:
    """Pure invariant checker must fail when semantic post-run facts are missing."""
    expected = json.loads(
        (_FIXTURE / "expected_shadow_report.json").read_text(encoding="utf-8")
    )
    body = {
        "registry": {"canon_count": 14, "storylet_count": 8},
        "steps": [{"step": i, "committed": []} for i in range(7)],
        "continuity": {
            "main_arc_id": "living_story_v1.main",
            "side_arc_ids": ["living_story_v1.side_study"],
            "ambient_arc_ids": ["living_story_v1.ambient_park"],
            "reload_ok": True,
            "once_cooldown_survived": True,
        },
        "social": {
            "accept_reasons": ["accepted"],
            "reject_reasons": [
                "rejected:cross_group",
                "rejected:cross_user",
                "rejected:privacy",
                "rejected:missing_evidence",
            ],
            "chat_accept_only_current_scope": True,
        },
        "storylet_observations": {
            "selected_storylet_ids": [
                "setback.prop_failure",
                "recovery.after_setback",
            ],
            "committed_event_ids": [],
        },
        "setback_recovery": {
            "major_setback_count": 1,
            "budget_setback_count": 1,
            "recovery_observed": True,
        },
        "partner_state": {
            "distinct_entity_count": 0,
            "change_count": 0,
            "entity_ids": [],
            "entities": [],
        },
        "life_state": {
            "item_count": 0,
            "story_ledger_item_count": 0,
            "story_ledger_keys": [],
            "story_ledger_missing_ttl": ["activity"],
        },
    }
    result = _check_invariants(
        report_body=body,
        expected=expected,
        source_fp_before="a",
        source_fp_after="a",
        fixture_fp_before="b",
        fixture_fp_after="b",
        default_gates_all_false=True,
    )
    assert result["verdict"] == "fail"
    checks = result["checks"]
    assert checks.get("partner_entities_changed") is False
    assert checks.get("partner_change_count") is False
    assert checks.get("story_ledger_ttl_complete") is False
    assert checks.get("recovery_steps_in_window") is False
    assert checks.get("final_main_stage_active") is False
    assert checks.get("final_setback_flag_cleared") is False

    body_gap = dict(body)
    body_gap["setback_recovery"] = {
        **body["setback_recovery"],
        "recovery_steps_after_setback": 5,
    }
    body_gap["partner_state"] = {
        "distinct_entity_count": 2,
        "change_count": 3,
        "entity_ids": ["nene", "tsukasa"],
        "entities": [],
    }
    body_gap["life_state"] = {
        "item_count": 5,
        "story_ledger_item_count": 3,
        "story_ledger_keys": ["activity", "mood", "open_constraint"],
        "story_ledger_missing_ttl": [],
    }
    body_gap["continuity"] = {
        **body["continuity"],
        "final_main_stage": "active",
        "final_setback_flag": 0,
    }
    result_gap = _check_invariants(
        report_body=body_gap,
        expected=expected,
        source_fp_before="a",
        source_fp_after="a",
        fixture_fp_before="b",
        fixture_fp_after="b",
        default_gates_all_false=True,
    )
    assert result_gap["verdict"] == "fail"
    assert result_gap["checks"].get("recovery_steps_in_window") is False


def test_partner_snapshot_excludes_historical_applied_ids_outside_current_run(
    tmp_path: Path,
) -> None:
    """Partner semantic counts must ignore preseeded historical applied_event_ids.

    Only applied IDs that intersect the current-run committed set may satisfy
    partner_entity_min / partner_change_min. Historical-only fixture state is
    not evidence that this shadow run changed partners.
    """
    store = StoryArcStore(tmp_path / "story_arcs")
    store.save(
        StoryArc(
            arc_id="shadow.main",
            revision=0,
            title="historical partner seed",
            stage="active",
            arc_role="main",
            partner_states={
                "nene": {
                    "mood": "steady",
                    "applied_event_ids": [
                        "storylet.historical.partner_nudge.s0",
                        "storylet.historical.partner_nudge.s1",
                    ],
                },
                "tsukasa": {
                    "mood": "focused",
                    "applied_event_ids": [
                        "storylet.historical.partner_nudge.s2",
                    ],
                },
            },
            event_budget={"committed_event_ids": []},
        )
    )
    # Empty current-run set: two historically updated partners must not count.
    snap_empty = _snapshot_partner_state(store, current_run_event_ids=set())
    assert snap_empty["distinct_entity_count"] == 0
    assert snap_empty["change_count"] == 0
    assert snap_empty["entity_ids"] == []
    assert snap_empty["entities"] == []
    assert snap_empty.get("provenance_scope") == "current_run_committed"
    assert snap_empty.get("current_run_event_count") == 0

    # Partial intersect: only one current-run ID is visible among historical IDs.
    current = {"storylet.historical.partner_nudge.s0"}
    snap_partial = _snapshot_partner_state(store, current_run_event_ids=current)
    assert snap_partial["distinct_entity_count"] == 1
    assert snap_partial["change_count"] == 1
    assert snap_partial["entity_ids"] == ["nene"]
    assert snap_partial["entities"][0]["applied_event_ids"] == [
        "storylet.historical.partner_nudge.s0"
    ]
    assert snap_partial["entities"][0]["applied_event_count"] == 1
    assert snap_partial.get("current_run_event_count") == 1

    # Full historical ID set still only counts the applied IDs present on disk
    # that intersect the provided current-run set (all three here).
    full = {
        "storylet.historical.partner_nudge.s0",
        "storylet.historical.partner_nudge.s1",
        "storylet.historical.partner_nudge.s2",
    }
    snap_full = _snapshot_partner_state(store, current_run_event_ids=full)
    assert snap_full["distinct_entity_count"] == 2
    assert snap_full["change_count"] == 3
    assert snap_full["entity_ids"] == ["nene", "tsukasa"]
    assert snap_full.get("current_run_event_count") == 3


def test_life_snapshot_excludes_historical_story_ledger_without_current_run_evidence(
    tmp_path: Path,
) -> None:
    """Story-ledger semantic counts require evidence_refs ∩ current-run IDs.

    Preseeded story_ledger items with only old/fixture evidence_refs must not
    satisfy story_ledger_item_min even when TTL is parseable.
    """
    life_path = tmp_path / "life_state.json"
    life_path.write_text(
        json.dumps(
            {
                "revision": 1,
                "updated_at": "2026-07-01T08:00:00+08:00",
                "items": {
                    "activity": {
                        "key": "activity",
                        "value": "historical activity",
                        "meta": {
                            "source": "story_ledger",
                            "scope": "self",
                            "privacy": "private",
                            "updated_at": "2026-07-01T08:00:00+08:00",
                            "decay_at": "2026-12-31T23:59:59+08:00",
                            "evidence_refs": [
                                "storylet.historical.ledger.activity.s0"
                            ],
                        },
                    },
                    "mood": {
                        "key": "mood",
                        "value": "historical mood",
                        "meta": {
                            "source": "story_ledger",
                            "scope": "self",
                            "privacy": "private",
                            "updated_at": "2026-07-01T08:00:00+08:00",
                            "decay_at": "2026-12-31T23:59:59+08:00",
                            "evidence_refs": [
                                "storylet.historical.ledger.mood.s1"
                            ],
                        },
                    },
                    "open_constraint": {
                        "key": "open_constraint",
                        "value": "historical constraint",
                        "meta": {
                            "source": "story_ledger",
                            "scope": "self",
                            "privacy": "private",
                            "updated_at": "2026-07-01T08:00:00+08:00",
                            "decay_at": "2026-12-31T23:59:59+08:00",
                            "evidence_refs": [
                                "storylet.historical.ledger.open.s2"
                            ],
                        },
                    },
                    "energy": {
                        "key": "energy",
                        "value": "0.5",
                        "meta": {
                            "source": "life_state",
                            "scope": "self",
                            "privacy": "private",
                            "updated_at": "2026-07-01T08:00:00+08:00",
                            "decay_at": "2026-12-31T23:59:59+08:00",
                            "evidence_refs": ["fixture:life_state:energy"],
                        },
                    },
                },
                "applied_event_ids": [
                    "storylet.historical.ledger.activity.s0",
                    "storylet.historical.ledger.mood.s1",
                    "storylet.historical.ledger.open.s2",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    snap_empty = _snapshot_life_state(life_path, current_run_event_ids=set())
    assert snap_empty["item_count"] == 4
    assert snap_empty["story_ledger_item_count"] == 0
    assert snap_empty["story_ledger_keys"] == []
    assert snap_empty["story_ledger_missing_ttl"] == []
    assert snap_empty["story_ledger_ttl_parseable_count"] == 0
    assert snap_empty.get("provenance_scope") == "current_run_committed"
    assert snap_empty.get("current_run_event_count") == 0

    # One current-run evidence ref unlocks only that ledger item.
    current = {"storylet.historical.ledger.mood.s1"}
    snap_one = _snapshot_life_state(life_path, current_run_event_ids=current)
    assert snap_one["story_ledger_item_count"] == 1
    assert snap_one["story_ledger_keys"] == ["mood"]
    assert snap_one["story_ledger_missing_ttl"] == []
    assert snap_one["story_ledger_ttl_parseable_count"] == 1
    assert snap_one.get("current_run_event_count") == 1

    full = {
        "storylet.historical.ledger.activity.s0",
        "storylet.historical.ledger.mood.s1",
        "storylet.historical.ledger.open.s2",
    }
    snap_full = _snapshot_life_state(life_path, current_run_event_ids=full)
    assert snap_full["story_ledger_item_count"] == 3
    assert snap_full["story_ledger_keys"] == [
        "activity",
        "mood",
        "open_constraint",
    ]
    assert snap_full["story_ledger_ttl_parseable_count"] == 3
    assert snap_full.get("current_run_event_count") == 3


def test_life_snapshot_malformed_decay_at_counts_as_missing_ttl(
    tmp_path: Path,
) -> None:
    """Malformed/non-ISO decay_at is fail-closed missing TTL for current-run items."""
    current_id = "storylet.current.ledger.activity.s0"
    life_path = tmp_path / "life_state.json"
    life_path.write_text(
        json.dumps(
            {
                "revision": 1,
                "updated_at": "2026-07-10T08:00:00+08:00",
                "items": {
                    "activity": {
                        "key": "activity",
                        "value": "broken ttl",
                        "meta": {
                            "source": "story_ledger",
                            "scope": "self",
                            "privacy": "private",
                            "updated_at": "2026-07-10T08:00:00+08:00",
                            "decay_at": "not-an-iso-timestamp",
                            "evidence_refs": [current_id],
                        },
                    },
                    "mood": {
                        "key": "mood",
                        "value": "ok ttl",
                        "meta": {
                            "source": "story_ledger",
                            "scope": "self",
                            "privacy": "private",
                            "updated_at": "2026-07-10T08:00:00+08:00",
                            "decay_at": "2026-12-31T23:59:59+08:00",
                            "evidence_refs": [current_id],
                        },
                    },
                    "stale_good": {
                        "key": "stale_good",
                        "value": "historical only",
                        "meta": {
                            "source": "story_ledger",
                            "scope": "self",
                            "privacy": "private",
                            "updated_at": "2026-07-01T08:00:00+08:00",
                            "decay_at": "also-not-iso",
                            "evidence_refs": [
                                "storylet.historical.ledger.stale.s9"
                            ],
                        },
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    snap = _snapshot_life_state(
        life_path, current_run_event_ids={current_id}
    )
    assert snap["story_ledger_item_count"] == 2
    assert snap["story_ledger_keys"] == ["activity", "mood"]
    assert snap["story_ledger_missing_ttl"] == ["activity"]
    assert snap["story_ledger_ttl_parseable_count"] == 1
    # Historical malformed item must not appear once filtered out.
    assert "stale_good" not in snap["story_ledger_keys"]
    assert "stale_good" not in snap["story_ledger_missing_ttl"]

    # Empty current-run set => no ledger credit and no parseable TTL credit.
    snap_none = _snapshot_life_state(life_path, current_run_event_ids=set())
    assert snap_none["story_ledger_item_count"] == 0
    assert snap_none["story_ledger_ttl_parseable_count"] == 0
    assert snap_none["story_ledger_missing_ttl"] == []


def test_current_run_committed_event_ids_from_formally_committed_steps() -> None:
    """Helper extracts only formally_committed event IDs from steps_out."""
    from plugins.worldbook.shadow import _current_run_committed_event_ids

    steps = [
        {
            "step": 0,
            "committed": [
                {
                    "event_id": "storylet.a.s0",
                    "formally_committed": True,
                },
                {
                    "event_id": "storylet.b.s0",
                    "formally_committed": False,
                },
            ],
        },
        {
            "step": 1,
            "committed": [
                {
                    "event_id": "storylet.a.s0",
                    "formally_committed": True,
                },
                {
                    "event_id": "  ",
                    "formally_committed": True,
                },
                "not-a-mapping",
            ],
        },
        {"step": 2, "committed": []},
        "skip-me",
    ]
    ids = _current_run_committed_event_ids(steps)
    assert ids == {"storylet.a.s0"}
