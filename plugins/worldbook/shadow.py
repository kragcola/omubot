"""Offline Stage-1 Worldbook shadow evaluator.

Caller-supplied fixture / content / output roots only. Never registers
PromptProvider, never writes production config or storage, and never
touches Docker / NapCat / QZone.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from plugins.schedule.story_arc import StoryArc, StoryArcStore
from services.worldbook.config import WorldbookConfig, worldbook_gates_all_false
from services.worldbook.domain import SocialEvidenceRef
from services.worldbook.projection import PromptProjection
from services.worldbook.runtime import WorldbookRuntime

_KNOWN_CONDITION_KEYS = frozenset({
    "min_step",
    "after_step",
    "var_gte",
    "var_lte",
    "var_eq",
})
_SETBACK_SEVERITIES = frozenset({"setback", "major", "crisis"})


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _tree_fingerprint(root: Path) -> str:
    """Deterministic fingerprint of a directory tree (paths + content hashes)."""
    if not root.exists():
        return ""
    pieces: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        pieces.append(f"{rel}:{digest}")
    return hashlib.sha256("\n".join(pieces).encode("utf-8")).hexdigest()


class _FixtureSocialStore:
    """Minimal async social store for shadow only (no production SN store)."""

    def __init__(self, items: Sequence[Mapping[str, Any]]) -> None:
        self._items = [dict(item) for item in items]

    async def recall(
        self,
        *,
        group_id: str,
        user_id: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in self._items:
            if str(item.get("group_id") or "") != str(group_id):
                continue
            if str(item.get("user_id") or "") != str(user_id):
                continue
            if not str(item.get("evidence_message_id") or "").strip():
                continue
            if not str(item.get("summary") or "").strip():
                continue
            if str(item.get("privacy") or "group") == "private":
                # Recall may still return; projection rejects privacy=private.
                pass
            out.append(dict(item))
            if len(out) >= max(0, int(limit)):
                break
        return out


@dataclass
class ShadowReport:
    """Structured offline shadow report (JSON-serializable)."""

    overall_verdict: str
    registry: dict[str, Any]
    steps: list[dict[str, Any]]
    continuity: dict[str, Any]
    social: dict[str, Any]
    storylet_observations: dict[str, Any]
    setback_recovery: dict[str, Any]
    partner_state: dict[str, Any] = field(default_factory=dict)
    life_state: dict[str, Any] = field(default_factory=dict)
    invariants: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_verdict": self.overall_verdict,
            "registry": dict(self.registry),
            "steps": list(self.steps),
            "continuity": dict(self.continuity),
            "social": dict(self.social),
            "storylet_observations": dict(self.storylet_observations),
            "setback_recovery": dict(self.setback_recovery),
            "partner_state": dict(self.partner_state),
            "life_state": dict(self.life_state),
            "invariants": dict(self.invariants),
            "metadata": dict(self.metadata),
        }

    @property
    def ok(self) -> bool:
        return self.overall_verdict == "pass"


def _assemble_work_root(
    *,
    work_root: Path,
    fixture_root: Path,
    content_root: Path,
) -> dict[str, Path]:
    """Copy content + mutable fixture seeds into an isolated work root."""
    canon_src = content_root / "canon"
    storylet_src = content_root / "storylets"
    if not canon_src.is_dir():
        raise FileNotFoundError(f"content canon dir missing: {canon_src}")
    if not storylet_src.is_dir():
        raise FileNotFoundError(f"content storylet dir missing: {storylet_src}")

    paths = {
        "canon_dir": work_root / "config" / "worldbook" / "canon",
        "storylet_dir": work_root / "config" / "worldbook" / "storylets",
        "state_dir": work_root / "storage" / "worldbook",
        "story_arc_dir": work_root / "storage" / "living_persona" / "story_arcs",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)

    # Content registries are copied once into the work root (read-only use).
    for src in sorted(canon_src.glob("*.json")):
        shutil.copy2(src, paths["canon_dir"] / src.name)
    for src in sorted(storylet_src.glob("*.json")):
        shutil.copy2(src, paths["storylet_dir"] / src.name)

    life_src = fixture_root / "life_state.json"
    if not life_src.is_file():
        raise FileNotFoundError(f"life_state fixture missing: {life_src}")
    shutil.copy2(life_src, paths["state_dir"] / "life_state.json")

    arcs_src = fixture_root / "arcs"
    if not arcs_src.is_dir():
        raise FileNotFoundError(f"arcs fixture dir missing: {arcs_src}")
    for src in sorted(arcs_src.glob("*.json")):
        shutil.copy2(src, paths["story_arc_dir"] / src.name)

    return paths


def _load_scenario(fixture_root: Path) -> dict[str, Any]:
    path = fixture_root / "scenario_7day.json"
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise TypeError("scenario_7day.json root must be object")
    steps = raw.get("steps")
    if not isinstance(steps, list) or len(steps) != 7:
        raise ValueError("scenario_7day.json must contain exactly 7 steps")
    return raw


def _load_social_corpus(fixture_root: Path) -> dict[str, Any]:
    path = fixture_root / "social_evidence.json"
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise TypeError("social_evidence.json root must be object")
    return raw


def _load_expected(fixture_root: Path) -> dict[str, Any]:
    path = fixture_root / "expected_shadow_report.json"
    if not path.is_file():
        return {}
    raw = _read_json(path)
    return raw if isinstance(raw, dict) else {}


def _build_runtime(
    *,
    work_root: Path,
    story_arc_store: StoryArcStore,
    social_store: _FixtureSocialStore | None,
) -> WorldbookRuntime:
    cfg = WorldbookConfig(
        enabled=True,
        chat_projection_enabled=True,
        schedule_projection_enabled=True,
        storylet_enabled=True,
        dream_proposal_enabled=False,
        social_evidence_enabled=True,
        canon_dir="config/worldbook/canon",
        storylet_dir="config/worldbook/storylets",
        state_dir="storage/worldbook",
        story_arc_dir="storage/living_persona/story_arcs",
    )
    return WorldbookRuntime(
        cfg,
        root=work_root,
        story_arc_store=story_arc_store,
        social_narrative_store=social_store,
        persona_id="fengxiaomeng-v2-shadow",
        persona_identity_text="",
    )


def _advance_main_clock(store: StoryArcStore, *, step: int) -> StoryArc:
    """Set main arc now_step / last_event_step / generated_days for drama clock."""

    def mutator(arc: StoryArc) -> None:
        if str(arc.arc_role) != "main":
            return
        budget = dict(arc.event_budget or {})
        budget["now_step"] = int(step)
        budget["last_event_step"] = int(step)
        budget["generated_days"] = int(step)
        budget["events_this_tick"] = 0
        arc.event_budget = budget

    # Prefer explicit main by role; update each main-role arc id found.
    main_id: str | None = None
    for arc_id in store.list_arc_ids():
        loaded = store.load(arc_id)
        if loaded is not None and str(loaded.arc_role) == "main":
            main_id = loaded.arc_id
            break
    if main_id is None:
        raise RuntimeError("shadow requires an explicit main StoryArc seed")
    return store.update(main_id, mutator)


def _apply_variable_nudges(
    store: StoryArcStore,
    *,
    nudges: Mapping[str, Any],
) -> None:
    if not nudges:
        return

    def mutator(arc: StoryArc) -> None:
        variables = dict(arc.variables or {})
        for key, value in nudges.items():
            key_s = str(key)
            try:
                target = float(value)
            except (TypeError, ValueError):
                variables[key_s] = value
                continue
            current = variables.get(key_s, 0)
            try:
                base = float(current) if current is not None else 0.0
            except (TypeError, ValueError):
                base = 0.0
            # Scenario nudges are absolute floors for required thresholds.
            variables[key_s] = max(base, target)
        arc.variables = variables

    for arc_id in store.list_arc_ids():
        loaded = store.load(arc_id)
        if loaded is not None and str(loaded.arc_role) == "main":
            store.update(arc_id, mutator)
            return


def _projection_summary(result: Any) -> dict[str, Any]:
    blocks = [
        {
            "block_id": b.block_id,
            "label": b.label,
            "source": b.meta.source,
            "char_count": b.char_count,
        }
        for b in result.blocks
    ]
    traces = [
        {
            "source": t.source,
            "budget_decision": t.budget_decision,
            "hit_reason": t.hit_reason,
            "label": t.label,
            "evidence_refs": list(t.evidence_refs),
        }
        for t in result.traces
    ]
    social_blocks = [b for b in blocks if str(b.get("source")) == "social_evidence"]
    storylet_blocks = [b for b in blocks if str(b.get("source")) == "storylet"]
    return {
        "mode": result.mode,
        "block_count": len(blocks),
        "blocks": blocks,
        "traces": traces,
        "social_block_count": len(social_blocks),
        "storylet_block_ids": [b["block_id"] for b in storylet_blocks],
    }


def _evaluate_social_corpus(
    *,
    items: Sequence[Mapping[str, Any]],
    group_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Project the full fixture corpus and collect accept/reject reasons."""
    cfg = WorldbookConfig(
        enabled=True,
        chat_projection_enabled=True,
        schedule_projection_enabled=True,
        social_evidence_enabled=True,
        social_budget_chars=2000,
        total_budget_chars=4000,
    )
    projection = PromptProjection(cfg)
    refs: list[SocialEvidenceRef] = []
    construction_rejects: list[dict[str, str]] = []
    for item in items:
        try:
            refs.append(SocialEvidenceRef.from_dict(item))
        except (TypeError, ValueError) as exc:
            construction_rejects.append(
                {
                    "experience_id": str(item.get("experience_id") or ""),
                    "budget_decision": "rejected:missing_evidence",
                    "detail": str(exc),
                }
            )

    chat = projection.project(
        mode="chat",
        social=refs,
        group_id=group_id,
        user_id=user_id,
    )
    schedule = projection.project(
        mode="schedule",
        social=refs,
        group_id=None,
        user_id=None,
    )

    accept_reasons: list[str] = []
    reject_reasons: list[str] = []
    per_item: list[dict[str, str]] = list(construction_rejects)
    for cr in construction_rejects:
        decision = str(cr.get("budget_decision") or "")
        if decision.startswith("rejected"):
            reject_reasons.append(decision)
    for trace in chat.traces:
        if str(trace.source) != "social_evidence":
            continue
        decision = str(trace.budget_decision)
        entry = {
            "label": str(trace.label or ""),
            "budget_decision": decision,
            "hit_reason": str(trace.hit_reason or ""),
        }
        per_item.append(entry)
        if decision == "accepted":
            accept_reasons.append(decision)
        elif decision.startswith("rejected"):
            reject_reasons.append(decision)

    schedule_social = [
        str(t.budget_decision)
        for t in schedule.traces
        if str(t.source) == "social_evidence"
    ]

    return {
        "accept_reasons": sorted(set(accept_reasons)),
        "reject_reasons": sorted(set(reject_reasons)),
        "per_item": per_item,
        "schedule_decisions": sorted(set(schedule_social)),
        "chat_social_block_count": sum(
            1 for b in chat.blocks if b.meta.source == "social_evidence"
        ),
        "schedule_social_block_count": sum(
            1 for b in schedule.blocks if b.meta.source == "social_evidence"
        ),
    }


def _ledger_snapshot(runtime: WorldbookRuntime) -> dict[str, Any]:
    ledger = runtime.ledger.load_stack()
    return {
        "main_arc_id": (
            str(ledger.main.get("arc_id") or "") if ledger.main is not None else ""
        ),
        "side_arc_ids": [
            str(a.get("arc_id") or "") for a in ledger.sides if a.get("arc_id")
        ],
        "ambient_arc_ids": [
            str(a.get("arc_id") or "") for a in ledger.ambient if a.get("arc_id")
        ],
        "ordering": list(ledger.ordering),
    }


def _main_budget_snapshot(store: StoryArcStore) -> dict[str, Any]:
    for arc_id in store.list_arc_ids():
        arc = store.load(arc_id)
        if arc is not None and str(arc.arc_role) == "main":
            budget = dict(arc.event_budget or {})
            return {
                "arc_id": arc.arc_id,
                "triggered_once": list(budget.get("triggered_once") or []),
                "cooldowns": dict(budget.get("cooldowns") or {}),
                "setback_count": int(budget.get("setback_count") or 0),
                "now_step": int(
                    budget.get("now_step")
                    or budget.get("last_event_step")
                    or 0
                ),
                "variables": dict(arc.variables or {}),
            }
    return {}


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _coerce_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ttl_is_parseable(raw: Any) -> bool:
    text = str(raw or "").strip()
    if not text:
        return False
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _current_run_committed_event_ids(
    steps: Sequence[Mapping[str, Any] | Any],
) -> set[str]:
    """Stable set of event IDs formally committed and observed in this run.

    Only ``formally_committed is True`` step items contribute. Empty / blank
    event_ids are discarded. This set is the sole provenance window for
    partner/life semantic invariants — historical fixture state outside it
    cannot satisfy thresholds.
    """
    out: set[str] = set()
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        for item in step.get("committed") or []:
            if not isinstance(item, Mapping):
                continue
            if item.get("formally_committed") is not True:
                continue
            eid = str(item.get("event_id") or "").strip()
            if eid:
                out.add(eid)
    return out


def _normalize_event_id_set(raw: Any) -> set[str]:
    """Coerce a sequence/set of event ids into a stripped non-empty set."""
    if raw is None:
        return set()
    if isinstance(raw, (str, bytes)):
        text = str(raw).strip()
        return {text} if text else set()
    out: set[str] = set()
    try:
        iterable = list(raw)  # type: ignore[arg-type]
    except TypeError:
        return set()
    for item in iterable:
        text = str(item or "").strip()
        if text:
            out.add(text)
    return out


def _snapshot_partner_state(
    store: StoryArcStore,
    *,
    current_run_event_ids: set[str] | Sequence[str] | None = None,
) -> dict[str, Any]:
    """Sanitized partner facts from final persisted arc.partner_states.

    Counts only applied_event_ids that intersect the current-run committed
    event-id set. Historical preseeded IDs outside that set cannot satisfy
    partner semantic invariants. Never embeds note / current_state /
    pinned_profile free text.
    """
    run_ids = _normalize_event_id_set(current_run_event_ids)
    entity_ids: list[str] = []
    entities: list[dict[str, Any]] = []
    change_count = 0
    for arc_id in sorted(store.list_arc_ids()):
        arc = store.load(arc_id)
        if arc is None:
            continue
        partners = getattr(arc, "partner_states", None) or {}
        if not isinstance(partners, Mapping):
            continue
        for eid_raw, state in sorted(partners.items(), key=lambda kv: str(kv[0])):
            if not isinstance(state, Mapping):
                continue
            applied_raw = state.get("applied_event_ids") or []
            applied: list[str] = []
            if isinstance(applied_raw, list):
                seen: set[str] = set()
                for item in applied_raw:
                    text = str(item or "").strip()
                    if not text or text in seen:
                        continue
                    if text not in run_ids:
                        continue
                    seen.add(text)
                    applied.append(text)
            if not applied:
                continue
            eid = str(eid_raw).strip()
            if not eid:
                continue
            entity_ids.append(eid)
            change_count += len(applied)
            entities.append(
                {
                    "entity_id": eid,
                    "arc_id": str(arc.arc_id),
                    "applied_event_count": len(applied),
                    "applied_event_ids": applied,
                }
            )
    # Distinct entity ids across arcs (stable sorted).
    distinct = sorted(set(entity_ids))
    return {
        "distinct_entity_count": len(distinct),
        "change_count": change_count,
        "entity_ids": distinct,
        "entities": entities,
        "provenance_scope": "current_run_committed",
        "current_run_event_count": len(run_ids),
    }


def _snapshot_life_state(
    state_path: Path,
    *,
    current_run_event_ids: set[str] | Sequence[str] | None = None,
) -> dict[str, Any]:
    """Story-ledger keys/count/missing TTL from final persisted LifeState.

    Only story_ledger items whose evidence_refs intersect the current-run
    committed event-id set may satisfy semantic thresholds. Historical items
    with old/no current evidence are excluded (and cannot pad TTL completeness).
    Never embeds item values — only structural TTL evidence. Malformed/non-ISO
    decay_at remains fail-closed missing TTL for counted items.
    """
    run_ids = _normalize_event_id_set(current_run_event_ids)
    empty = {
        "item_count": 0,
        "story_ledger_item_count": 0,
        "story_ledger_keys": [],
        "story_ledger_missing_ttl": [],
        "story_ledger_ttl_parseable_count": 0,
        "provenance_scope": "current_run_committed",
        "current_run_event_count": len(run_ids),
    }
    if not state_path.is_file():
        return empty
    raw = _read_json(state_path)
    if not isinstance(raw, Mapping):
        return empty
    items_raw = raw.get("items")
    items: Mapping[str, Any] = items_raw if isinstance(items_raw, Mapping) else {}
    story_keys: list[str] = []
    missing_ttl: list[str] = []
    parseable = 0
    for key, item in sorted(items.items(), key=lambda kv: str(kv[0])):
        if not isinstance(item, Mapping):
            continue
        meta_raw = item.get("meta")
        meta: Mapping[str, Any] = (
            meta_raw if isinstance(meta_raw, Mapping) else {}
        )
        if str(meta.get("source") or "") != "story_ledger":
            continue
        refs_raw = meta.get("evidence_refs") or []
        refs: set[str] = set()
        if isinstance(refs_raw, (list, tuple, set)):
            for ref in refs_raw:
                text = str(ref or "").strip()
                if text:
                    refs.add(text)
        if not refs.intersection(run_ids):
            continue
        key_s = str(key)
        story_keys.append(key_s)
        decay = meta.get("decay_at")
        if _ttl_is_parseable(decay):
            parseable += 1
        else:
            missing_ttl.append(key_s)
    return {
        "item_count": len(items),
        "story_ledger_item_count": len(story_keys),
        "story_ledger_keys": story_keys,
        "story_ledger_missing_ttl": missing_ttl,
        "story_ledger_ttl_parseable_count": parseable,
        "provenance_scope": "current_run_committed",
        "current_run_event_count": len(run_ids),
    }


def _recovery_steps_after_setback(steps: Sequence[Mapping[str, Any]]) -> int | None:
    """Gap (recovery_step - setback_step) from formally committed events."""
    setback_step: int | None = None
    recovery_step: int | None = None
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        step_n = _coerce_int(step.get("step"))
        for item in step.get("committed") or []:
            if not isinstance(item, Mapping):
                continue
            if item.get("formally_committed") is not True:
                continue
            sev = str(item.get("severity") or "").strip().lower()
            if sev in _SETBACK_SEVERITIES and setback_step is None:
                setback_step = step_n
            if sev == "recovery" and recovery_step is None:
                recovery_step = step_n
    if setback_step is None or recovery_step is None:
        return None
    return int(recovery_step) - int(setback_step)


def _final_main_stage_and_setback_flag(
    store: StoryArcStore,
) -> tuple[str | None, float | None]:
    for arc_id in store.list_arc_ids():
        arc = store.load(arc_id)
        if arc is None or str(arc.arc_role) != "main":
            continue
        stage = str(getattr(arc, "stage", "") or "") or None
        variables = dict(getattr(arc, "variables", None) or {})
        flag = _coerce_float(variables.get("setback_flag"), default=None)
        return stage, flag
    return None, None


def _check_invariants(
    *,
    report_body: Mapping[str, Any],
    expected: Mapping[str, Any],
    source_fp_before: str,
    source_fp_after: str,
    fixture_fp_before: str,
    fixture_fp_after: str,
    default_gates_all_false: bool,
) -> dict[str, Any]:
    inv_exp: dict[str, Any] = _as_dict(expected.get("invariants"))
    registry: dict[str, Any] = _as_dict(report_body.get("registry"))
    steps: list[Any] = _as_list(report_body.get("steps"))
    continuity: dict[str, Any] = _as_dict(report_body.get("continuity"))
    social: dict[str, Any] = _as_dict(report_body.get("social"))
    setback: dict[str, Any] = _as_dict(report_body.get("setback_recovery"))
    story_obs: dict[str, Any] = _as_dict(report_body.get("storylet_observations"))
    partner: dict[str, Any] = _as_dict(report_body.get("partner_state"))
    life: dict[str, Any] = _as_dict(report_body.get("life_state"))

    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}

    canon_count = _coerce_int(registry.get("canon_count"))
    storylet_count = _coerce_int(registry.get("storylet_count"))
    checks["registry_nonempty"] = canon_count > 0 and storylet_count > 0
    checks["registry_canon_min"] = canon_count >= _coerce_int(
        inv_exp.get("registry_canon_min"), 1
    )
    checks["registry_storylet_min"] = storylet_count >= _coerce_int(
        inv_exp.get("registry_storylet_min"), 1
    )
    if inv_exp.get("registry_storylet_exact") is not None:
        checks["registry_storylet_exact"] = storylet_count == _coerce_int(
            inv_exp.get("registry_storylet_exact")
        )

    checks["step_count"] = len(steps) == _coerce_int(inv_exp.get("step_count"), 7)

    main_id = str(continuity.get("main_arc_id") or "")
    checks["main_present"] = main_id == str(
        inv_exp.get("main_arc_id") or "living_story_v1.main"
    )
    checks["side_present"] = list(continuity.get("side_arc_ids") or []) == list(
        inv_exp.get("side_arc_ids")
        or ["living_story_v1.side_study"]
    )
    checks["ambient_present"] = list(continuity.get("ambient_arc_ids") or []) == list(
        inv_exp.get("ambient_arc_ids")
        or ["living_story_v1.ambient_park"]
    )
    checks["reload_continuity"] = bool(continuity.get("reload_ok"))
    checks["once_cooldown_survive_reload"] = bool(
        continuity.get("once_cooldown_survived")
    )

    schedule_social_max = _coerce_int(inv_exp.get("schedule_social_blocks_max"), 0)
    max_schedule_social = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        for mode_key in ("schedule", "chat"):
            block = step.get(mode_key)
            if not isinstance(block, dict):
                continue
            if mode_key == "schedule":
                max_schedule_social = max(
                    max_schedule_social,
                    _coerce_int(block.get("social_block_count")),
                )
    checks["schedule_zero_social"] = max_schedule_social <= schedule_social_max

    reject_reasons = {str(x) for x in (social.get("reject_reasons") or [])}
    accept_reasons = {str(x) for x in (social.get("accept_reasons") or [])}
    required_rejects = {
        str(x)
        for x in (
            inv_exp.get("required_social_reject_reasons")
            or [
                "rejected:cross_group",
                "rejected:cross_user",
                "rejected:privacy",
                "rejected:missing_evidence",
            ]
        )
    }
    checks["social_rejects"] = required_rejects.issubset(reject_reasons)
    checks["social_accept_current"] = "accepted" in accept_reasons
    checks["chat_social_accept_only_current"] = bool(
        social.get("chat_accept_only_current_scope")
    )

    major_count = _coerce_int(setback.get("major_setback_count"))
    budget_setback_count = _coerce_int(setback.get("budget_setback_count"))
    checks["setback_at_most_one"] = major_count <= _coerce_int(
        inv_exp.get("major_setback_max"), 1
    )
    checks["recovery_observed"] = bool(setback.get("recovery_observed"))
    # Mandatory: Drama selection budget must not double-count setbacks vs EventReducer.
    checks["budget_setback_matches_committed"] = budget_setback_count == major_count

    # Mandatory: every formally committed Storylet event observed during the
    # run must equal the final union of arc.event_budget.committed_event_ids.
    observed_event_ids = _current_run_committed_event_ids(steps)
    final_event_ids = {
        str(x)
        for x in (story_obs.get("committed_event_ids") or [])
        if str(x).strip()
    }
    checks["formal_commit_observation_complete"] = observed_event_ids == final_event_ids

    must_include: list[str] = []
    path_exp = expected.get("expected_storylet_path")
    if isinstance(path_exp, Mapping):
        must_include = [str(x) for x in (path_exp.get("must_include") or [])]
    selected = {str(x) for x in (story_obs.get("selected_storylet_ids") or [])}
    checks["must_include_storylets"] = all(s in selected for s in must_include)

    checks["source_tree_unchanged"] = source_fp_before == source_fp_after
    checks["fixture_tree_unchanged"] = fixture_fp_before == fixture_fp_after
    checks["default_plugin_gates_all_false"] = bool(default_gates_all_false)

    # --- Semantic persisted-truth invariants (fail-closed) ---
    partner_entity_min = _coerce_int(inv_exp.get("partner_entity_min"), 2)
    partner_change_min = _coerce_int(inv_exp.get("partner_change_min"), 2)
    partner_entities = _coerce_int(partner.get("distinct_entity_count"))
    partner_changes = _coerce_int(partner.get("change_count"))
    checks["partner_entities_changed"] = partner_entities >= partner_entity_min
    checks["partner_change_count"] = partner_changes >= partner_change_min

    story_ledger_min = _coerce_int(inv_exp.get("story_ledger_item_min"), 3)
    story_ledger_count = _coerce_int(life.get("story_ledger_item_count"))
    missing_ttl = [
        str(x) for x in (life.get("story_ledger_missing_ttl") or []) if str(x).strip()
    ]
    ttl_required = inv_exp.get("story_ledger_ttl_required")
    if ttl_required is None:
        ttl_required = True
    # Fail-closed: require min story-ledger items and, when TTL is required,
    # an empty missing-TTL list (every key has a parseable decay_at).
    if bool(ttl_required):
        checks["story_ledger_ttl_complete"] = (
            story_ledger_count >= story_ledger_min and not missing_ttl
        )
    else:
        checks["story_ledger_ttl_complete"] = story_ledger_count >= story_ledger_min

    recovery_gap = setback.get("recovery_steps_after_setback")
    recovery_min = _coerce_int(inv_exp.get("recovery_steps_min"), 2)
    recovery_max = _coerce_int(inv_exp.get("recovery_steps_max"), 3)
    if recovery_gap is None or recovery_gap == "":
        checks["recovery_steps_in_window"] = False
    else:
        gap_n = _coerce_int(recovery_gap, default=-1)
        checks["recovery_steps_in_window"] = recovery_min <= gap_n <= recovery_max

    expected_final_stage = str(
        inv_exp.get("final_main_stage")
        or _as_dict(expected.get("expected_continuity")).get("final_main_stage")
        or "active"
    )
    final_stage = str(continuity.get("final_main_stage") or "")
    checks["final_main_stage_active"] = final_stage == expected_final_stage

    final_flag = continuity.get("final_setback_flag")
    flag_cleared_required = inv_exp.get("final_setback_flag_cleared")
    if flag_cleared_required is None:
        flag_cleared_required = True
    if final_flag is None or final_flag == "":
        checks["final_setback_flag_cleared"] = False
    else:
        flag_f = _coerce_float(final_flag, default=None)
        if flag_f is None:
            checks["final_setback_flag_cleared"] = False
        elif bool(flag_cleared_required):
            checks["final_setback_flag_cleared"] = flag_f == 0.0
        else:
            checks["final_setback_flag_cleared"] = True

    details["max_schedule_social"] = max_schedule_social
    details["selected_storylets"] = sorted(selected)
    details["major_setback_count"] = major_count
    details["budget_setback_count"] = budget_setback_count
    details["observed_event_ids"] = sorted(observed_event_ids)
    details["final_committed_event_ids"] = sorted(final_event_ids)
    details["reject_reasons"] = sorted(reject_reasons)
    details["accept_reasons"] = sorted(accept_reasons)
    details["partner_entities"] = partner_entities
    details["partner_changes"] = partner_changes
    details["story_ledger_item_count"] = story_ledger_count
    details["story_ledger_missing_ttl"] = missing_ttl
    details["recovery_steps_after_setback"] = recovery_gap
    details["final_main_stage"] = final_stage
    details["final_setback_flag"] = final_flag

    all_ok = all(checks.values()) if checks else False
    return {
        "checks": checks,
        "details": details,
        "verdict": "pass" if all_ok else "fail",
    }


async def run_worldbook_shadow(
    *,
    fixture_root: str | Path,
    content_root: str | Path,
    output_root: str | Path,
    work_root: str | Path | None = None,
    keep_work: bool = False,
) -> ShadowReport:
    """Run the 7-day offline Living Story shadow.

    Parameters
    ----------
    fixture_root:
        Directory containing arcs/, life_state.json, social_evidence.json,
        scenario_7day.json, expected_shadow_report.json.
    content_root:
        Directory containing canon/ and storylets/ JSON packs (typically
        ``config/worldbook``). Copied into the work root; never mutated.
    output_root:
        Caller-supplied directory for the JSON report (and optional work copy).
    work_root:
        Optional explicit temp work root. When omitted a temporary directory
        under ``output_root`` is created.
    """
    fixture_root = Path(fixture_root).resolve()
    content_root = Path(content_root).resolve()
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    source_fp_before = _tree_fingerprint(content_root)
    fixture_fp_before = _tree_fingerprint(fixture_root)

    cleanup_work = False
    if work_root is None:
        work_path = Path(
            tempfile.mkdtemp(prefix="worldbook_shadow_", dir=str(output_root))
        )
        cleanup_work = not keep_work
    else:
        work_path = Path(work_root).resolve()
        work_path.mkdir(parents=True, exist_ok=True)

    try:
        _assemble_work_root(
            work_root=work_path,
            fixture_root=fixture_root,
            content_root=content_root,
        )
        scenario = _load_scenario(fixture_root)
        social_corpus = _load_social_corpus(fixture_root)
        expected = _load_expected(fixture_root)

        current_scope = scenario.get("current_scope") or social_corpus.get(
            "current_scope"
        ) or {}
        group_id = str(current_scope.get("group_id") or "fixture-group-living-story")
        user_id = str(current_scope.get("user_id") or "fixture-user-living-story")
        reload_after = int(scenario.get("reload_after_step") or 3)

        social_items = social_corpus.get("items")
        if not isinstance(social_items, list):
            social_items = []
        social_store = _FixtureSocialStore(social_items)
        story_store = StoryArcStore(
            storage_dir=str(work_path / "storage" / "living_persona" / "story_arcs")
        )
        runtime = _build_runtime(
            work_root=work_path,
            story_arc_store=story_store,
            social_store=social_store,
        )
        runtime.ensure_loaded()

        registry = {
            "canon_count": len(runtime.canon_registry.list_entries()),
            "storylet_count": len(runtime.storylet_registry.list_storylets()),
            "canon_ids": [e.entry_id for e in runtime.canon_registry.list_entries()],
            "storylet_ids": [
                s.storylet_id for s in runtime.storylet_registry.list_storylets()
            ],
        }

        social_eval = _evaluate_social_corpus(
            items=social_items,
            group_id=group_id,
            user_id=user_id,
        )
        # Mark whether any accepted social is outside current scope (must be false).
        social_eval["chat_accept_only_current_scope"] = True
        for item in social_items:
            if str(item.get("expect") or "") != "accepted":
                continue
            if (
                str(item.get("group_id") or "") != group_id
                or str(item.get("user_id") or "") != user_id
            ):
                social_eval["chat_accept_only_current_scope"] = False

        steps_out: list[dict[str, Any]] = []
        selected_ids: list[str] = []
        selected_severities: list[str] = []
        pre_reload_budget: dict[str, Any] | None = None
        post_reload_budget: dict[str, Any] | None = None
        reload_ok = False
        once_survived = False

        main_snapshot = _ledger_snapshot(runtime)
        main_arc_id = str(main_snapshot.get("main_arc_id") or "living_story_v1.main")

        for raw_step in scenario["steps"]:
            if not isinstance(raw_step, dict):
                continue
            step_raw = raw_step.get("step")
            if step_raw is None:
                step_raw = raw_step.get("day")
            step = _coerce_int(step_raw, 0)
            mode = str(raw_step.get("mode") or "chat").strip().lower()
            conversation = str(raw_step.get("conversation_text") or "")
            nudges = raw_step.get("variable_nudges")
            if not isinstance(nudges, Mapping):
                nudges = {}

            _advance_main_clock(story_store, step=step)
            _apply_variable_nudges(story_store, nudges=nudges)

            day_raw = raw_step.get("day")
            step_record: dict[str, Any] = {
                "day": _coerce_int(day_raw, step),
                "step": step,
                "mode": mode,
                "conversation_text": conversation,
                "notes": str(raw_step.get("notes") or ""),
                "committed": [],
            }

            # Re-bind ledger store in case of reload.
            runtime.ledger.set_store(story_store)

            if mode in {"chat", "both"}:
                chat_result = await runtime.project_chat(
                    conversation_text=conversation,
                    group_id=group_id,
                    user_id=user_id,
                    session_id=f"shadow-day-{step}",
                )
                step_record["chat"] = _projection_summary(chat_result)
                # Collect storylets from projection blocks.
                chat_storylets = [
                    b.block_id.replace("storylet:", "", 1)
                    for b in chat_result.blocks
                    if b.meta.source == "storylet"
                ]
                step_record["chat_selected_storylets"] = chat_storylets
            if mode in {"schedule", "both"}:
                # Pass social deliberately to prove schedule rejects them.
                schedule_refs: list[SocialEvidenceRef] = []
                for item in social_items:
                    try:
                        schedule_refs.append(SocialEvidenceRef.from_dict(item))
                    except (TypeError, ValueError):
                        continue
                sched_result = runtime.project_schedule(
                    conversation_text=conversation,
                    social=schedule_refs,
                )
                step_record["schedule"] = _projection_summary(sched_result)
                step_record["schedule_selected_storylets"] = [
                    b.block_id.replace("storylet:", "", 1)
                    for b in sched_result.blocks
                    if b.meta.source == "storylet"
                ]

            # Formal path: project_chat / project_schedule already call
            # commit_storylet atomically. Shadow must not re-synthesize events.
            # In mode=both, union chat and schedule Storylet commits so neither
            # path is silently dropped when both project on the same step.
            chat_ids = [
                str(x)
                for x in (step_record.get("chat_selected_storylets") or [])
                if str(x).strip()
            ]
            sched_ids = [
                str(x)
                for x in (step_record.get("schedule_selected_storylets") or [])
                if str(x).strip()
            ]
            if mode == "schedule":
                commit_ids = list(dict.fromkeys(sched_ids))
            elif mode == "chat":
                commit_ids = list(dict.fromkeys(chat_ids))
            else:
                # mode == "both": stable union, chat order first then schedule-only.
                commit_ids = list(dict.fromkeys([*chat_ids, *sched_ids]))

            committed_obs: list[dict[str, Any]] = []
            budget_snap = _main_budget_snapshot(story_store)
            # Collect committed_event_ids from every active arc so formal commits
            # that land on side/ambient targets are still observed correctly.
            committed_by_arc: dict[str, set[str]] = {}
            for arc_id in story_store.list_arc_ids():
                arc_obj = story_store.load(arc_id)
                if arc_obj is None:
                    continue
                raw_ids = (arc_obj.event_budget or {}).get("committed_event_ids")
                if isinstance(raw_ids, list):
                    committed_by_arc[str(arc_id)] = {
                        str(x) for x in raw_ids if str(x).strip()
                    }
            for sid in commit_ids:
                found = runtime.storylet_registry.get(sid)
                if found is None:
                    continue
                selected_ids.append(sid)
                selected_severities.append(found.severity)
                target_arc = str(getattr(found, "target_arc_id", "") or "").strip()
                if not target_arc:
                    target_arc = main_arc_id
                # Prefer deterministic event id for this step on the target Arc.
                from services.worldbook.domain import deterministic_storylet_event_id

                event_id = deterministic_storylet_event_id(sid, step, target_arc)
                deltas: dict[str, Any] = {}
                # Merge cost + consequence deltas for observation (match commit).
                for section in (found.cost, found.consequence):
                    raw_deltas = dict(section or {}).get("variable_deltas")
                    if isinstance(raw_deltas, Mapping):
                        for k, v in raw_deltas.items():
                            key_s = str(k)
                            if (
                                key_s in deltas
                                and isinstance(deltas[key_s], (int, float))
                                and not isinstance(deltas[key_s], bool)
                                and isinstance(v, (int, float))
                                and not isinstance(v, bool)
                            ):
                                deltas[key_s] = float(deltas[key_s]) + float(v)
                            else:
                                deltas[key_s] = v
                arc_committed = committed_by_arc.get(target_arc) or set()
                formally = (
                    event_id in arc_committed
                    or any(
                        str(event_id) == str(x)
                        or str(x).endswith(f".{sid}.s{step}")
                        for x in arc_committed
                    )
                    or any(sid in str(x) for x in arc_committed)
                )
                committed_obs.append(
                    {
                        "storylet_id": sid,
                        "severity": found.severity,
                        "arc_id": target_arc,
                        "event_id": event_id,
                        "variable_deltas": deltas,
                        "formally_committed": formally,
                    }
                )
            step_record["committed"] = committed_obs
            step_record["budget_after"] = budget_snap
            steps_out.append(step_record)

            if step == reload_after:
                pre_reload_budget = _main_budget_snapshot(story_store)
                # Explicit reload checkpoint: new runtime + store handles, same work root.
                story_store = StoryArcStore(
                    storage_dir=str(
                        work_path / "storage" / "living_persona" / "story_arcs"
                    )
                )
                runtime = _build_runtime(
                    work_root=work_path,
                    story_arc_store=story_store,
                    social_store=social_store,
                )
                runtime.ensure_loaded()
                post_reload_budget = _main_budget_snapshot(story_store)
                reload_ok = (
                    pre_reload_budget.get("arc_id") == post_reload_budget.get("arc_id")
                    and list(pre_reload_budget.get("triggered_once") or [])
                    == list(post_reload_budget.get("triggered_once") or [])
                )
                once_survived = reload_ok and bool(
                    pre_reload_budget.get("triggered_once")
                )
                # Continuity of main/side/ambient ids
                post_ledger = _ledger_snapshot(runtime)
                reload_ok = reload_ok and post_ledger.get("main_arc_id") == main_arc_id

        final_ledger = _ledger_snapshot(runtime)
        final_budget = _main_budget_snapshot(story_store)

        major_setbacks = [
            sid
            for sid, sev in zip(selected_ids, selected_severities, strict=False)
            if sev in _SETBACK_SEVERITIES
        ]
        recovery_ids = [
            sid
            for sid, sev in zip(selected_ids, selected_severities, strict=False)
            if sev == "recovery"
        ]

        final_main_stage, final_setback_flag = _final_main_stage_and_setback_flag(
            story_store
        )
        continuity = {
            **final_ledger,
            "reload_after_step": reload_after,
            "reload_ok": reload_ok,
            "once_cooldown_survived": once_survived,
            "pre_reload_triggered_once": list(
                (pre_reload_budget or {}).get("triggered_once") or []
            ),
            "post_reload_triggered_once": list(
                (post_reload_budget or {}).get("triggered_once") or []
            ),
            "final_budget": final_budget,
            "final_main_stage": final_main_stage,
            "final_setback_flag": final_setback_flag,
        }

        recovery_gap = _recovery_steps_after_setback(steps_out)
        setback_recovery = {
            "major_setback_count": len(set(major_setbacks)),
            "major_setback_ids": sorted(set(major_setbacks)),
            "recovery_observed": len(recovery_ids) > 0,
            "recovery_ids": sorted(set(recovery_ids)),
            "budget_setback_count": int(final_budget.get("setback_count") or 0),
            "recovery_steps_after_setback": recovery_gap,
        }

        # Current-run formal commits observed during steps_out (provenance window).
        current_run_event_ids = _current_run_committed_event_ids(steps_out)

        partner_state = _snapshot_partner_state(
            story_store,
            current_run_event_ids=current_run_event_ids,
        )
        life_state_path = work_path / "storage" / "worldbook" / "life_state.json"
        life_state = _snapshot_life_state(
            life_state_path,
            current_run_event_ids=current_run_event_ids,
        )

        # Final formal commit set: union committed_event_ids from every Arc.
        # selection_count counts formal events (not unique Storylet ids).
        # Semantic partner/life snapshots use current_run_event_ids (steps_out);
        # formal observation completeness still requires equality with this
        # final arc-budget union.
        final_committed_ids: list[str] = []
        seen_event_ids: set[str] = set()
        for arc_id in story_store.list_arc_ids():
            arc_obj = story_store.load(arc_id)
            if arc_obj is None:
                continue
            raw_ids = (arc_obj.event_budget or {}).get("committed_event_ids")
            if not isinstance(raw_ids, list):
                continue
            for raw in raw_ids:
                eid = str(raw).strip()
                if not eid or eid in seen_event_ids:
                    continue
                seen_event_ids.add(eid)
                final_committed_ids.append(eid)
        final_committed_ids = sorted(final_committed_ids)

        storylet_observations = {
            "selected_storylet_ids": list(dict.fromkeys(selected_ids)),
            "selected_severities": list(selected_severities),
            "committed_event_ids": final_committed_ids,
            "selection_count": len(final_committed_ids),
        }

        # Default production plugin gates remain all false (shadow never flips them).
        from services.worldbook.config import worldbook_config_from_mapping

        default_plugin_path = (
            Path(__file__).resolve().parent / "config.default.json"
        )
        default_gates_all_false = True
        if default_plugin_path.is_file():
            default_payload = _read_json(default_plugin_path)
            default_cfg = worldbook_config_from_mapping(default_payload)
            default_gates_all_false = worldbook_gates_all_false(default_cfg)

        source_fp_after = _tree_fingerprint(content_root)
        fixture_fp_after = _tree_fingerprint(fixture_root)

        draft = {
            "registry": registry,
            "steps": steps_out,
            "continuity": continuity,
            "social": social_eval,
            "storylet_observations": storylet_observations,
            "setback_recovery": setback_recovery,
            "partner_state": partner_state,
            "life_state": life_state,
        }
        invariant_result = _check_invariants(
            report_body=draft,
            expected=expected,
            source_fp_before=source_fp_before,
            source_fp_after=source_fp_after,
            fixture_fp_before=fixture_fp_before,
            fixture_fp_after=fixture_fp_after,
            default_gates_all_false=default_gates_all_false,
        )
        overall = str(invariant_result.get("verdict") or "fail")

        report = ShadowReport(
            overall_verdict=overall,
            registry=registry,
            steps=steps_out,
            continuity=continuity,
            social=social_eval,
            storylet_observations=storylet_observations,
            setback_recovery=setback_recovery,
            partner_state=partner_state,
            life_state=life_state,
            invariants=invariant_result,
            metadata={
                "fixture_root": str(fixture_root),
                "content_root": str(content_root),
                "output_root": str(output_root),
                "work_root": str(work_path),
                "pack_id": str(scenario.get("pack_id") or "living_story_v1"),
                "source_fingerprint": source_fp_after,
                "fixture_fingerprint": fixture_fp_after,
                "report_essential_hash": "",
            },
        )
        essential = {
            "overall_verdict": report.overall_verdict,
            "registry_counts": {
                "canon": registry.get("canon_count"),
                "storylet": registry.get("storylet_count"),
            },
            "selected_storylet_ids": storylet_observations.get("selected_storylet_ids"),
            "setback_recovery": {
                "major_setback_count": setback_recovery.get("major_setback_count"),
                "recovery_observed": setback_recovery.get("recovery_observed"),
                "recovery_steps_after_setback": setback_recovery.get(
                    "recovery_steps_after_setback"
                ),
            },
            "continuity_roles": {
                "main": continuity.get("main_arc_id"),
                "sides": continuity.get("side_arc_ids"),
                "ambient": continuity.get("ambient_arc_ids"),
            },
            "continuity_final": {
                "final_main_stage": continuity.get("final_main_stage"),
                "final_setback_flag": continuity.get("final_setback_flag"),
            },
            "partner_state": {
                "distinct_entity_count": partner_state.get("distinct_entity_count"),
                "change_count": partner_state.get("change_count"),
                "entity_ids": partner_state.get("entity_ids"),
                "provenance_scope": partner_state.get("provenance_scope"),
                "current_run_event_count": partner_state.get("current_run_event_count"),
            },
            "life_state": {
                "item_count": life_state.get("item_count"),
                "story_ledger_item_count": life_state.get("story_ledger_item_count"),
                "story_ledger_keys": life_state.get("story_ledger_keys"),
                "story_ledger_missing_ttl": life_state.get("story_ledger_missing_ttl"),
                "provenance_scope": life_state.get("provenance_scope"),
                "current_run_event_count": life_state.get("current_run_event_count"),
            },
            "social_accept": social_eval.get("accept_reasons"),
            "social_reject": social_eval.get("reject_reasons"),
            "invariant_checks": invariant_result.get("checks"),
        }
        report.metadata["report_essential_hash"] = _stable_hash(essential)

        report_path = output_root / "shadow_report.json"
        _write_json(report_path, report.to_dict())
        return report
    finally:
        if cleanup_work and work_path.exists():
            shutil.rmtree(work_path, ignore_errors=True)


def run_worldbook_shadow_sync(**kwargs: Any) -> ShadowReport:
    """Sync wrapper for CLI / non-async callers."""
    import asyncio

    return asyncio.run(run_worldbook_shadow(**kwargs))
