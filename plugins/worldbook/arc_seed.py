"""Plugin-owned production StoryArc seed pack loader / validator / importer.

StoryArcStore remains the only mutable truth. Seeds live under a versioned
config pack and are applied idempotently: validate entire pack first, then
write only missing arc IDs. Never overwrite existing matching arcs; fail closed
on invalid packs or conflicting existing arcs.

Raw seed JSON is validated *before* ``StoryArc.from_dict`` so silent defaults
(missing ``arc_role`` → side, missing scope/status, invalid role → side) cannot
mask a broken pack contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plugins.schedule.story_arc import StoryArc, StoryArcStore

_ALLOWED_ROLES = frozenset({"main", "side", "ambient"})
_REQUIRED_RAW_FIELDS = ("arc_id", "arc_role", "scope", "status")
_TERMINAL_STAGES = frozenset({
    "abandoned",
    "archived",
    "cancelled",
    "canceled",
    "complete",
    "completed",
    "expired",
    "finished",
    "terminal",
})
_INACTIVE_STATUS = frozenset({
    "inactive",
    "archived",
    "abandoned",
    "cancelled",
    "canceled",
    "complete",
    "completed",
    "expired",
    "finished",
    "terminal",
    "paused",
})
# Non-terminal evolved stages (setback/recovery/planning) may exist on matching
# arcs and are preserved; only terminal stages conflict.
_BUDGET_ZERO_COUNTERS = (
    "now_step",
    "last_event_step",
    "generated_days",
    "setback_count",
    "events_this_tick",
)
_BUDGET_EMPTY_LISTS = (
    "triggered_once",
    "committed_event_ids",
)
_BUDGET_EMPTY_MAPS = (
    "cooldowns",
    "storylet_available_at",
    "last_viewed",
)


class ArcSeedError(ValueError):
    """Raised when the seed pack is invalid (fail closed before any write)."""


class ArcSeedConflictError(ArcSeedError):
    """Raised when an existing arc conflicts with the seed pack contract."""


@dataclass(frozen=True, slots=True)
class ArcSeedResult:
    """Outcome of an idempotent seed pass (IDs only — no free text)."""

    seeded_ids: list[str] = field(default_factory=list)
    existing_ids: list[str] = field(default_factory=list)


def _reject_duplicate_keys(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` that rejects duplicate JSON object keys."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        out[key] = value
    return out


def _load_raw_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArcSeedError(f"cannot read seed file: {path.name}") from exc
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ArcSeedError(f"invalid seed JSON: {path.name}") from exc
    except ValueError as exc:
        # duplicate keys from object_pairs_hook
        raise ArcSeedError(f"invalid seed JSON: {path.name}: {exc}") from exc


def _require_raw_str(raw: dict[str, Any], key: str, path_name: str) -> str:
    if key not in raw:
        raise ArcSeedError(f"seed missing required field {key}: {path_name}")
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ArcSeedError(f"seed field {key} must be non-empty string: {path_name}")
    return value.strip()


def _validate_raw_seed(raw: dict[str, Any], path: Path) -> None:
    """Enforce explicit raw contract before StoryArc.from_dict normalization."""
    path_name = path.name
    for key in _REQUIRED_RAW_FIELDS:
        if key not in raw:
            raise ArcSeedError(f"seed missing required field {key}: {path_name}")

    arc_id = _require_raw_str(raw, "arc_id", path_name)
    if path.stem != arc_id:
        raise ArcSeedError(
            f"seed filename stem must match arc_id: "
            f"file={path.stem!r} arc_id={arc_id!r}"
        )

    role = raw["arc_role"]
    if not isinstance(role, str) or role.strip().lower() not in _ALLOWED_ROLES:
        raise ArcSeedError(
            f"invalid arc_role for {arc_id}: {role!r} (raw; no silent default)"
        )

    scope = raw["scope"]
    if not isinstance(scope, str) or scope.strip().lower() != "fiction":
        raise ArcSeedError(
            f"seed scope must be fiction (raw): {arc_id} got {scope!r}"
        )

    status = raw["status"]
    if not isinstance(status, str) or status.strip().lower() != "active":
        raise ArcSeedError(
            f"seed status must be active (raw): {arc_id} got {status!r}"
        )


def load_seed_pack(seed_dir: str | Path) -> list[StoryArc]:
    """Load every ``*.json`` arc seed under ``seed_dir`` (non-recursive).

    Does not write. Invalid JSON / non-object roots / missing required raw
    fields / filename mismatches raise immediately. Raw contract is enforced
    before ``StoryArc.from_dict`` so defaults cannot mask pack errors.
    """
    root = Path(seed_dir)
    if not root.is_dir():
        raise ArcSeedError(f"arc seed dir missing: {root}")
    paths = sorted(root.glob("*.json"))
    if not paths:
        raise ArcSeedError(f"arc seed pack empty: {root}")
    arcs: list[StoryArc] = []
    for path in paths:
        raw = _load_raw_json(path)
        if not isinstance(raw, dict):
            raise ArcSeedError(f"seed root must be object: {path.name}")
        _validate_raw_seed(raw, path)
        try:
            arcs.append(StoryArc.from_dict(raw))
        except (TypeError, ValueError) as exc:
            raise ArcSeedError(f"invalid seed arc: {path.name}") from exc
    return arcs


def _assert_clean_budget(arc: StoryArc) -> None:
    """Reject any pre-run event/budget state (counters, maps, lists)."""
    budget = dict(arc.event_budget or {})
    arc_id = arc.arc_id
    for key in _BUDGET_ZERO_COUNTERS:
        if key not in budget:
            continue
        try:
            value = int(budget.get(key) or 0)
        except (TypeError, ValueError) as exc:
            raise ArcSeedError(
                f"seed event_budget.{key} must be int 0: {arc_id}"
            ) from exc
        if value != 0:
            raise ArcSeedError(
                f"seed event_budget.{key} must be 0: {arc_id} got {value}"
            )
    for key in _BUDGET_EMPTY_LISTS:
        items = list(budget.get(key) or [])
        if items:
            raise ArcSeedError(f"seed event_budget.{key} must be empty: {arc_id}")
    for key in _BUDGET_EMPTY_MAPS:
        mapping = budget.get(key) or {}
        if not isinstance(mapping, dict):
            raise ArcSeedError(
                f"seed event_budget.{key} must be empty object: {arc_id}"
            )
        if mapping:
            raise ArcSeedError(f"seed event_budget.{key} must be empty: {arc_id}")


def validate_seed_pack(arcs: list[StoryArc]) -> None:
    """Validate full pack structure before any store write.

    Contract:
    - exactly one ``main``, >=1 ``side``, >=1 ``ambient``
    - all fiction scope, active status/stage, revision 0
    - clean event / applied history and empty committed IDs
    - zero budget counters; empty cooldown/view maps
    - unique arc_ids; roles in {main, side, ambient}
    """
    if not arcs:
        raise ArcSeedError("seed pack empty")

    seen_ids: set[str] = set()
    mains = 0
    sides = 0
    ambients = 0

    for arc in arcs:
        arc_id = str(arc.arc_id or "").strip()
        if not arc_id:
            raise ArcSeedError("seed arc missing arc_id")
        if arc_id in seen_ids:
            raise ArcSeedError(f"duplicate seed arc_id: {arc_id}")
        seen_ids.add(arc_id)

        role = str(arc.arc_role or "").strip().lower()
        if role not in _ALLOWED_ROLES:
            raise ArcSeedError(f"invalid arc_role for {arc_id}: {role!r}")
        if role == "main":
            mains += 1
        elif role == "side":
            sides += 1
        else:
            ambients += 1

        scope = str(arc.scope or "").strip().lower()
        if scope != "fiction":
            raise ArcSeedError(f"seed scope must be fiction: {arc_id}")

        status = str(arc.status or "").strip().lower()
        if status != "active" or status in _INACTIVE_STATUS:
            raise ArcSeedError(f"seed status must be active: {arc_id}")

        stage = str(arc.stage or "").strip().lower()
        if stage != "active" or stage in _TERMINAL_STAGES:
            raise ArcSeedError(f"seed stage must be active: {arc_id}")

        if int(arc.revision or 0) != 0:
            raise ArcSeedError(f"seed revision must be 0: {arc_id}")

        if list(arc.last_events or []):
            raise ArcSeedError(f"seed last_events must be empty: {arc_id}")
        if list(arc.event_history or []):
            raise ArcSeedError(f"seed event_history must be empty: {arc_id}")
        if list(arc.causal_links or []):
            raise ArcSeedError(f"seed causal_links must be empty: {arc_id}")

        _assert_clean_budget(arc)

        for entity_id, state in (arc.partner_states or {}).items():
            if not str(entity_id or "").strip():
                raise ArcSeedError(f"empty partner entity on {arc_id}")
            if isinstance(state, dict) and list(state.get("applied_event_ids") or []):
                raise ArcSeedError(
                    f"seed partner applied_event_ids must be empty: {arc_id}/{entity_id}"
                )

    if mains != 1:
        raise ArcSeedError(f"seed pack requires exactly one main, got {mains}")
    if sides < 1:
        raise ArcSeedError("seed pack requires at least one side arc")
    if ambients < 1:
        raise ArcSeedError("seed pack requires at least one ambient arc")


def _existing_conflicts_with_seed(
    existing: StoryArc,
    seed: StoryArc,
) -> str | None:
    """Return conflict reason or None when existing may be preserved as-is.

    Matching role+scope alone is not enough: terminal stages and inactive
    statuses conflict so startup cannot claim seed success while the ledger
    has no active main. Non-terminal evolved stages (setback / recovery /
    planning / active) are allowed.
    """
    existing_role = str(existing.arc_role or "side").strip().lower()
    seed_role = str(seed.arc_role or "side").strip().lower()
    if existing_role != seed_role:
        return f"role mismatch for {seed.arc_id}: existing={existing_role} seed={seed_role}"
    existing_scope = str(existing.scope or "").strip().lower()
    seed_scope = str(seed.scope or "").strip().lower()
    if existing_scope != seed_scope:
        return (
            f"scope mismatch for {seed.arc_id}: "
            f"existing={existing_scope} seed={seed_scope}"
        )
    existing_stage = str(existing.stage or "").strip().lower()
    if existing_stage in _TERMINAL_STAGES:
        return (
            f"existing terminal stage for {seed.arc_id}: "
            f"stage={existing_stage}"
        )
    existing_status = str(existing.status or "").strip().lower()
    if existing_status in _INACTIVE_STATUS or existing_status != "active":
        return (
            f"existing inactive status for {seed.arc_id}: "
            f"status={existing_status}"
        )
    return None


def seed_missing_arcs(
    store: StoryArcStore,
    seed_dir: str | Path,
) -> ArcSeedResult:
    """Validate the full pack, then save only missing IDs.

    Fail closed: any pack or conflict error leaves the store unchanged for the
    intended seed IDs (no partial pack apply after validation failure; conflict
    checks run before any new write). Second-load races re-check conflict
    before treating a newly appeared ID as a preserved existing arc.
    """
    arcs = load_seed_pack(seed_dir)
    validate_seed_pack(arcs)

    by_id = {arc.arc_id: arc for arc in arcs}
    seed_main_ids = {a.arc_id for a in arcs if str(a.arc_role) == "main"}

    # Preflight existing state — no writes yet.
    existing_ids: list[str] = []
    missing: list[StoryArc] = []
    for arc_id, seed in by_id.items():
        current = store.load(arc_id)
        if current is None:
            missing.append(seed)
            continue
        reason = _existing_conflicts_with_seed(current, seed)
        if reason is not None:
            raise ArcSeedConflictError(reason)
        existing_ids.append(arc_id)

    # Any explicit main outside the pack's single main conflicts (fail closed).
    pack_main_id = next(iter(seed_main_ids))
    for other_id in store.list_arc_ids():
        if other_id == pack_main_id:
            continue
        other = store.load(other_id)
        if other is None:
            continue
        if str(other.arc_role or "").strip().lower() == "main":
            raise ArcSeedConflictError(
                f"existing foreign main {other_id} conflicts with seed main"
            )

    seeded_ids: list[str] = []
    for seed in missing:
        # Race path: another writer may have created the ID after preflight.
        raced = store.load(seed.arc_id)
        if raced is not None:
            reason = _existing_conflicts_with_seed(raced, seed)
            if reason is not None:
                raise ArcSeedConflictError(reason)
            existing_ids.append(seed.arc_id)
            continue
        # Ensure seed snapshot starts at revision 0 for StoryArcStore.save.
        payload = seed.to_dict()
        payload["revision"] = 0
        store.save(StoryArc.from_dict(payload))
        seeded_ids.append(seed.arc_id)

    return ArcSeedResult(
        seeded_ids=sorted(seeded_ids),
        existing_ids=sorted(set(existing_ids)),
    )


def seed_pack_arc_ids(seed_dir: str | Path) -> list[str]:
    """Return sorted arc_ids from a validated pack (read-only helper)."""
    arcs = load_seed_pack(seed_dir)
    validate_seed_pack(arcs)
    return sorted(a.arc_id for a in arcs)


__all__ = [
    "ArcSeedConflictError",
    "ArcSeedError",
    "ArcSeedResult",
    "load_seed_pack",
    "seed_missing_arcs",
    "seed_pack_arc_ids",
    "validate_seed_pack",
]
