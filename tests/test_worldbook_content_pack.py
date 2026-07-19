"""Stage-1 Worldbook content pack schema and authoring checks."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from services.worldbook.domain import Storylet
from services.worldbook.store import CanonRegistry, StoryletRegistry

_REPO = Path(__file__).resolve().parents[1]
_CANON_PATH = _REPO / "config" / "worldbook" / "canon" / "fengxiaomeng_native_v1.json"
_STORYLET_PATH = _REPO / "config" / "worldbook" / "storylets" / "living_story_v1.json"
_FIXTURE_ROOT = _REPO / "tests" / "fixtures" / "worldbook" / "living_story_v1"
_CANON_SCHEMA = _REPO / "schemas" / "worldbook-canon-v1.schema.json"
_STORYLET_SCHEMA = _REPO / "schemas" / "worldbook-storylet-v1.schema.json"
_PLUGIN_DEFAULT = _REPO / "plugins" / "worldbook" / "config.default.json"

_KNOWN_CONDITIONS = frozenset({
    "min_step",
    "after_step",
    "var_gte",
    "var_lte",
    "var_eq",
})
# Real production QQ / group identifiers must never appear in Stage-1 packs.
_REAL_ID_RE = re.compile(r"\b(?:963737802|984198159|384801062)\b")
_QQ_LIKE_RE = re.compile(r"\b[1-9]\d{5,11}\b")


def _load(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def test_content_pack_files_nonempty_and_present() -> None:
    assert _CANON_PATH.is_file()
    assert _STORYLET_PATH.is_file()
    canon = _load(_CANON_PATH)
    storylets = _load(_STORYLET_PATH)
    assert canon.get("schema_version") == 1
    assert storylets.get("schema_version") == 1
    assert len(canon.get("entries") or []) >= 8
    assert len(storylets.get("storylets") or []) >= 6


def test_canon_and_storylet_validate_against_schemas() -> None:
    canon_schema = _load(_CANON_SCHEMA)
    storylet_schema = _load(_STORYLET_SCHEMA)
    Draft202012Validator(canon_schema).validate(_load(_CANON_PATH))
    Draft202012Validator(storylet_schema).validate(_load(_STORYLET_PATH))


def test_canon_local_source_metadata_and_no_always_active() -> None:
    canon = _load(_CANON_PATH)
    for entry in canon["entries"]:
        assert entry.get("always_active") is False
        meta = entry.get("metadata") or {}
        source = str(meta.get("source") or "")
        assert source.startswith("config/persona/"), entry.get("entry_id")
        assert not source.startswith("http")
        # At least one activation surface.
        surfaces = (
            entry.get("keywords")
            or entry.get("aliases")
            or entry.get("regexes")
            or entry.get("entity_ids")
        )
        assert surfaces, entry.get("entry_id")


def test_storylets_all_fiction_supported_conditions_and_loadable() -> None:
    pack = _load(_STORYLET_PATH)
    for raw in pack["storylets"]:
        assert raw.get("scope") == "fiction"
        conditions = raw.get("conditions") or {}
        assert isinstance(conditions, dict)
        unknown = sorted(k for k in conditions if k not in _KNOWN_CONDITIONS)
        assert unknown == [], f"{raw.get('storylet_id')}: {unknown}"
        # Domain model must accept every storylet.
        Storylet.from_dict(raw)

    registry = StoryletRegistry(_STORYLET_PATH.parent)
    loaded = registry.load()
    assert len(loaded) == len(pack["storylets"])


def test_canon_registry_loads_native_pack() -> None:
    registry = CanonRegistry(_CANON_PATH.parent)
    entries = registry.load()
    assert len(entries) >= 8
    assert any(e.entry_id == "identity.fengxiaomeng" for e in entries)


def test_no_real_group_members_or_qq_ids_in_pack_or_fixtures() -> None:
    targets = [
        _CANON_PATH,
        _STORYLET_PATH,
        *_FIXTURE_ROOT.rglob("*.json"),
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8")
        assert _REAL_ID_RE.search(text) is None, path
        # Allow ISO dates / versions; flag long pure digit tokens that look like QQ.
        for match in _QQ_LIKE_RE.finditer(text):
            token = match.group(0)
            # 8+ digit pure IDs are forbidden (QQ-like); shorter may appear in times.
            if len(token) >= 8:
                pytest.fail(f"QQ-like id {token!r} in {path}")


def test_fixture_seeds_main_side_ambient_life_social_scenario() -> None:
    arcs_dir = _FIXTURE_ROOT / "arcs"
    main = _load(arcs_dir / "living_story_v1.main.json")
    side = _load(arcs_dir / "living_story_v1.side_study.json")
    ambient = _load(arcs_dir / "living_story_v1.ambient_park.json")
    assert main["arc_role"] == "main"
    assert side["arc_role"] == "side"
    assert ambient["arc_role"] == "ambient"

    life = _load(_FIXTURE_ROOT / "life_state.json")
    assert life.get("items")
    for key, item in life["items"].items():
        meta = item["meta"]
        assert meta.get("source")
        assert meta.get("scope")
        assert meta.get("privacy")
        assert meta.get("updated_at")
        assert meta.get("decay_at"), f"TTL missing for {key}"

    social = _load(_FIXTURE_ROOT / "social_evidence.json")
    expects = {str(i.get("expect") or "") for i in social["items"]}
    assert "accepted" in expects
    assert "rejected:cross_group" in expects
    assert "rejected:cross_user" in expects
    assert "rejected:privacy" in expects
    assert "rejected:missing_evidence" in expects

    scenario = _load(_FIXTURE_ROOT / "scenario_7day.json")
    assert len(scenario["steps"]) == 7
    expected = _load(_FIXTURE_ROOT / "expected_shadow_report.json")
    assert expected["invariants"]["overall_verdict"] == "pass"


def test_storylet_required_evidence_matches_fixture_arcs() -> None:
    arc_ids = {
        p.stem for p in (_FIXTURE_ROOT / "arcs").glob("*.json")
    }
    pack = _load(_STORYLET_PATH)
    for raw in pack["storylets"]:
        for token in raw.get("required_evidence") or []:
            assert str(token).startswith("arc:")
            aid = str(token)[4:]
            assert aid in arc_ids, f"{raw['storylet_id']} -> {aid}"


def test_storylets_target_real_arcs_and_conflicts_keep_explicit_costs() -> None:
    arc_ids = {p.stem for p in (_FIXTURE_ROOT / "arcs").glob("*.json")}
    pack = _load(_STORYLET_PATH)
    by_id = {str(raw["storylet_id"]): raw for raw in pack["storylets"]}

    for raw in pack["storylets"]:
        assert raw.get("target_arc_id") in arc_ids, raw.get("storylet_id")

    setback = by_id["setback.prop_failure"]
    setback_cost = (setback.get("cost") or {}).get("variable_deltas") or {}
    setback_consequence = (
        (setback.get("consequence") or {}).get("variable_deltas") or {}
    )
    assert float(setback_cost.get("energy") or 0) < 0
    assert "energy" not in setback_consequence

    tension = by_id["tension.exam_vs_stage"]
    tension_cost = (tension.get("cost") or {}).get("variable_deltas") or {}
    assert float(tension_cost.get("energy") or 0) < 0
    assert float(tension_cost.get("focus_split") or 0) > 0

    recovery = by_id["recovery.after_setback"]
    recovery_effect = recovery.get("consequence") or {}
    recovery_deltas = recovery_effect.get("variable_deltas") or {}
    assert float(recovery_deltas.get("setback_flag") or 0) < 0
    assert recovery_effect.get("stage") == "recovery"

    closure = by_id["closure.week_aftermath"]
    assert (closure.get("consequence") or {}).get("stage") == "active"


def test_default_plugin_gates_all_false() -> None:
    from services.worldbook.config import (
        worldbook_config_from_mapping,
        worldbook_gates_all_false,
    )

    payload = _load(_PLUGIN_DEFAULT)
    cfg = worldbook_config_from_mapping(payload)
    assert worldbook_gates_all_false(cfg)

_PROD_ARCS = _REPO / "config" / "worldbook" / "arcs"
_FIXTURE_ARCS = _FIXTURE_ROOT / "arcs"


def test_production_arc_seed_pack_present_and_distinct_from_fixtures() -> None:
    """Production seeds live under config/; offline fixtures stay under tests/."""
    prod_files = sorted(_PROD_ARCS.glob("*.json"))
    fixture_files = sorted(_FIXTURE_ARCS.glob("*.json"))
    assert _PROD_ARCS.is_dir()
    assert len(prod_files) >= 3
    assert len(fixture_files) >= 3

    prod_ids = {p.stem for p in prod_files}
    fixture_ids = {p.stem for p in fixture_files}
    assert "living_story_v1.main" in prod_ids
    assert "living_story_v1.side_study" in prod_ids
    assert "living_story_v1.ambient_park" in prod_ids
    # Same stable IDs as fixtures (Storylet targets), but separate trees.
    assert prod_ids == fixture_ids
    assert _PROD_ARCS.resolve() != _FIXTURE_ARCS.resolve()

    # Content must not be a byte-copy of offline fixtures (production wording).
    for name in ("living_story_v1.main.json", "living_story_v1.side_study.json", "living_story_v1.ambient_park.json"):
        prod_text = (_PROD_ARCS / name).read_text(encoding="utf-8")
        fix_text = (_FIXTURE_ARCS / name).read_text(encoding="utf-8")
        assert "fixture" not in prod_text.lower()
        assert prod_text != fix_text


def test_production_arc_seeds_validate_and_match_storylet_targets() -> None:
    from plugins.worldbook.arc_seed import load_seed_pack, validate_seed_pack

    arcs = load_seed_pack(_PROD_ARCS)
    validate_seed_pack(arcs)
    by_role = {}
    for arc in arcs:
        by_role.setdefault(arc.arc_role, []).append(arc.arc_id)
        assert arc.scope == "fiction"
        assert arc.status == "active"
        assert arc.stage == "active"
        assert int(arc.revision) == 0
    assert len(by_role.get("main") or []) == 1
    assert len(by_role.get("side") or []) >= 1
    assert len(by_role.get("ambient") or []) >= 1

    arc_ids = {a.arc_id for a in arcs}
    pack = _load(_STORYLET_PATH)
    for raw in pack["storylets"]:
        assert raw.get("target_arc_id") in arc_ids, raw.get("storylet_id")
        for token in raw.get("required_evidence") or []:
            assert str(token).startswith("arc:")
            assert str(token)[4:] in arc_ids


def test_production_arc_seeds_forbid_real_ids_and_limit_partners() -> None:
    for path in sorted(_PROD_ARCS.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        assert _REAL_ID_RE.search(text) is None, path
        for match in _QQ_LIKE_RE.finditer(text):
            token = match.group(0)
            if len(token) >= 8:
                pytest.fail(f"QQ-like id {token!r} in {path}")
        data = _load(path)
        for entity_id in (data.get("partner_states") or {}):
            assert entity_id in {"tsukasa", "nene"}, entity_id


def test_arc_seed_dir_in_plugin_defaults() -> None:
    payload = _load(_PLUGIN_DEFAULT)
    values = payload.get("values") or {}
    assert values.get("arc_seed_dir") == "config/worldbook/arcs"
