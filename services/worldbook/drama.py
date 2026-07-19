"""Deterministic Drama Manager over Storylet candidates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.worldbook.domain import Storylet


@dataclass(frozen=True, slots=True)
class DramaSelection:
    storylet: Storylet
    rank_score: float
    reasons: tuple[str, ...]


def _get_budget(arc_budget: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(arc_budget, Mapping):
        return {}
    return dict(arc_budget)


def _events_this_tick_for_step(budget: Mapping[str, Any], *, now_step: int) -> int:
    """Return the per-Arc event count for the logical step ``now_step``.

    Budgets without ``events_tick_step`` (legacy) fail open as a fresh step so
    a stale ``events_this_tick`` cannot permanently block selection. When the
    stored tick step matches ``now_step``, the persisted count is preserved so
    chat then schedule on the same Arc share one cap.
    """
    if "events_tick_step" not in budget:
        return 0
    raw_tick_step = budget.get("events_tick_step")
    if raw_tick_step is None:
        return 0
    try:
        tick_step = int(raw_tick_step)
    except (TypeError, ValueError):
        return 0
    if tick_step != int(now_step):
        return 0
    raw_count = budget.get("events_this_tick")
    if raw_count is None:
        return 0
    try:
        return max(0, int(raw_count))
    except (TypeError, ValueError):
        return 0


def _triggered_once(budget: Mapping[str, Any]) -> set[str]:
    raw = budget.get("triggered_once")
    if not isinstance(raw, list):
        return set()
    return {str(x) for x in raw if str(x).strip()}


def _cooldowns(budget: Mapping[str, Any]) -> dict[str, int]:
    raw = budget.get("cooldowns")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def _delays(budget: Mapping[str, Any]) -> dict[str, int]:
    raw = budget.get("storylet_available_at")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


# Known condition keys only — unknown/misspelled keys fail closed.
_KNOWN_CONDITION_KEYS = frozenset({
    "min_step",
    "after_step",
    "var_gte",
    "var_lte",
    "var_eq",
})


def _conditions_met(
    storylet: Storylet,
    *,
    variables: Mapping[str, Any],
    available_evidence: set[str],
    now_step: int,
) -> tuple[bool, str]:
    conditions = dict(storylet.conditions)
    unknown = sorted(str(k) for k in conditions if str(k) not in _KNOWN_CONDITION_KEYS)
    if unknown:
        return False, f"unknown_condition:{','.join(unknown)}"
    # min_step / after_step
    min_step = int(conditions.get("min_step") or conditions.get("after_step") or 0)
    if now_step < min_step:
        return False, f"min_step:{min_step}"
    # variable comparisons: {"var_gte": {"exam_pressure": 0.5}}
    var_gte = conditions.get("var_gte")
    if isinstance(var_gte, dict):
        for key, threshold in var_gte.items():
            try:
                current = float(variables.get(str(key), 0) or 0)
                if current < float(threshold):
                    return False, f"var_gte:{key}"
            except (TypeError, ValueError):
                return False, f"var_gte_invalid:{key}"
    elif var_gte is not None:
        return False, "var_gte_invalid"
    var_lte = conditions.get("var_lte")
    if isinstance(var_lte, dict):
        for key, threshold in var_lte.items():
            try:
                current = float(variables.get(str(key), 0) or 0)
                if current > float(threshold):
                    return False, f"var_lte:{key}"
            except (TypeError, ValueError):
                return False, f"var_lte_invalid:{key}"
    elif var_lte is not None:
        return False, "var_lte_invalid"
    var_eq = conditions.get("var_eq")
    if isinstance(var_eq, dict):
        for key, expected in var_eq.items():
            if variables.get(str(key)) != expected:
                return False, f"var_eq:{key}"
    elif var_eq is not None:
        return False, "var_eq_invalid"
    required = set(storylet.required_evidence)
    if required and not required.issubset(available_evidence):
        return False, "required_evidence_missing"
    return True, "ok"


class DramaManager:
    """Sort and gate storylets with once/cooldown/delay/severity/recovery budgets."""

    def __init__(
        self,
        *,
        max_setbacks_per_arc: int = 1,
        max_events_per_tick: int = 1,
        recovery_window_steps: int = 3,
    ) -> None:
        self.max_setbacks_per_arc = max(0, int(max_setbacks_per_arc))
        self.max_events_per_tick = max(0, int(max_events_per_tick))
        self.recovery_window_steps = max(0, int(recovery_window_steps))

    def select(
        self,
        storylets: Sequence[Storylet],
        *,
        arc_budget: Mapping[str, Any] | None = None,
        variables: Mapping[str, Any] | None = None,
        available_evidence: Sequence[str] | None = None,
        now_step: int = 0,
        limit: int | None = None,
    ) -> list[DramaSelection]:
        if not storylets:
            return []
        budget = _get_budget(arc_budget)
        variables = dict(variables or {})
        evidence = {str(e) for e in (available_evidence or []) if str(e).strip()}
        once = _triggered_once(budget)
        cooldowns = _cooldowns(budget)
        delays = _delays(budget)
        setback_count = int(budget.get("setback_count") or 0)
        recovery_until = int(budget.get("recovery_until_step") or -1)
        in_recovery = int(now_step) < recovery_until
        events_this_tick = _events_this_tick_for_step(budget, now_step=int(now_step))
        tick_cap = self.max_events_per_tick if limit is None else max(0, int(limit))

        eligible: list[DramaSelection] = []
        for storylet in storylets:
            if storylet.once and storylet.storylet_id in once:
                continue
            cd_until = cooldowns.get(storylet.storylet_id, -1)
            if int(now_step) < int(cd_until):
                continue
            available_at = delays.get(storylet.storylet_id)
            if available_at is None and storylet.delay_steps > 0:
                # first availability gate: not ready until delay elapses from step 0
                if int(now_step) < int(storylet.delay_steps):
                    continue
            elif available_at is not None and int(now_step) < int(available_at):
                continue
            is_setback = str(storylet.severity).lower() in {
                "setback",
                "major",
                "crisis",
            }
            if is_setback and setback_count >= self.max_setbacks_per_arc:
                continue
            if is_setback and in_recovery:
                continue
            ok, reason = _conditions_met(
                storylet,
                variables=variables,
                available_evidence=evidence,
                now_step=int(now_step),
            )
            if not ok:
                continue
            rank = float(storylet.saliency) * 10.0 + float(storylet.priority) / 100.0
            eligible.append(
                DramaSelection(
                    storylet=storylet,
                    rank_score=rank,
                    reasons=(reason, f"saliency:{storylet.saliency}"),
                )
            )

        eligible.sort(
            key=lambda s: (
                -s.rank_score,
                -s.storylet.priority,
                s.storylet.storylet_id,
            )
        )
        remaining_cap = max(0, tick_cap - events_this_tick)
        return eligible[:remaining_cap]

    def apply_selection_budget(
        self,
        arc_budget: dict[str, Any],
        selection: DramaSelection,
        *,
        now_step: int,
    ) -> dict[str, Any]:
        """Return updated arc event_budget after selecting a storylet (pure)."""
        budget = dict(arc_budget)
        storylet = selection.storylet
        if storylet.once:
            once = sorted(_triggered_once(budget) | {storylet.storylet_id})
            budget["triggered_once"] = once
        if storylet.cooldown_steps > 0:
            cds = _cooldowns(budget)
            cds[storylet.storylet_id] = int(now_step) + int(storylet.cooldown_steps)
            budget["cooldowns"] = cds
        if storylet.delay_steps > 0:
            delays = _delays(budget)
            # after fire, re-arm delay for potential non-once reuse
            delays[storylet.storylet_id] = int(now_step) + int(storylet.delay_steps)
            budget["storylet_available_at"] = delays
        # Setback / recovery accounting is owned exclusively by EventReducer on
        # committed events. Selection budget only tracks once/cooldown/delay/
        # tick caps so projection and commit never double-count setbacks.
        step = int(now_step)
        prior = _events_this_tick_for_step(budget, now_step=step)
        budget["events_this_tick"] = prior + 1
        budget["events_tick_step"] = step
        last = dict(budget.get("last_viewed") or {}) if isinstance(
            budget.get("last_viewed"), dict
        ) else {}
        last[storylet.storylet_id] = step
        budget["last_viewed"] = last
        return budget
