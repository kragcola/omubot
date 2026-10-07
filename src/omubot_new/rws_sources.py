"""Body-free rhythm owner and pure projections of currently authorized sources.

The caller obtains Memory/Climate/schedule snapshots from their existing owners
under current policy, and revalidates source bindings before consuming a score.
This module grants no access and never supplies a strong-addressing decision.
Hawkes here retains the legacy interval proxy, not a fitted Hawkes process.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from .climate import ClimateSnapshot
from .memory import MemoryFact, MemoryFactSearchResult
from .rws import RwsSignal, gray_zone_signals
from .schedule_life import ScheduleDayRecord
from .types import Event, Scope
from .willingness import WillingnessRecommendation, willingness_phase

SOURCE_VERSION = "rws-actual-sources-v1"
HAWKES_VERSION = "legacy-interval-proxy-1800s-v1"
MEMORY_FAMILIARITY_CAP = 50  # Legacy active-card density normalization.


@dataclass(frozen=True, slots=True)
class RhythmEvent:
    event_id: str
    subject_id: str
    observed_at: float


@dataclass(frozen=True, slots=True)
class HawkesSnapshot:
    scope: Scope
    status: Literal["available", "missing", "disabled"]
    reason: str
    rho: float | None
    events: tuple[RhythmEvent, ...]
    as_of: float
    window_s: float


class HawkesRhythm:
    """Single synchronous owner; feed only policy-authorized ingress events.

    No daemon or persistent second store. Revocation must call the deletion
    methods before another snapshot; disabling clears retained identities.
    Resource caps evict oldest observations and are not tuning coefficients.
    """

    def __init__(
        self, *, enabled: bool = False, window_s: float = 1800.0,
        max_scopes: int = 128, max_events: int = 256,
    ) -> None:
        if not math.isfinite(window_s) or window_s <= 0 or max_scopes < 1 or max_events < 2:
            raise ValueError("invalid rhythm bounds")
        self.enabled = enabled
        self.window_s = window_s
        self.max_scopes, self.max_events = max_scopes, max_events
        self._scopes: OrderedDict[tuple[str, str], OrderedDict[str, RhythmEvent]] = OrderedDict()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:
            self.clear()

    def clear(self) -> None:
        self._scopes.clear()

    def clear_scope(self, scope: Scope) -> None:
        self._scopes.pop((scope.bot_id, scope.group_id), None)

    def revoke_event(self, scope: Scope, event_id: str) -> None:
        events = self._scopes.get((scope.bot_id, scope.group_id))
        if events is not None:
            events.pop(event_id, None)

    def revoke_subject(self, scope: Scope, subject_id: str) -> None:
        events = self._scopes.get((scope.bot_id, scope.group_id))
        if events is not None:
            for event_id, event in tuple(events.items()):
                if event.subject_id == subject_id:
                    del events[event_id]

    def purge_unreadable(self, has_read_permission: Callable[[str, Scope], bool]) -> None:
        for key, events in tuple(self._scopes.items()):
            scope = Scope(bot_id=key[0], group_id=key[1])
            for event_id, event in tuple(events.items()):
                if not has_read_permission(event.subject_id, scope):
                    del events[event_id]
            if not events:
                del self._scopes[key]

    def prune(self, now: float) -> None:
        if not math.isfinite(now):
            raise ValueError("invalid rhythm clock")
        cutoff = now - self.window_s
        for key, events in tuple(self._scopes.items()):
            for event_id, event in tuple(events.items()):
                if event.observed_at <= cutoff:
                    del events[event_id]
            if not events:
                del self._scopes[key]

    def observe(self, event: Event, *, now: float) -> bool:
        self.prune(now)
        if not self.enabled or event.event_time is None:
            return False
        observed_at = float(event.event_time)
        if not now - self.window_s < observed_at <= now:
            return False
        key = (event.scope.bot_id, event.scope.group_id)
        events = self._scopes.setdefault(key, OrderedDict())
        observation = RhythmEvent(event.event_id, event.user_id, observed_at)
        previous = events.get(event.event_id)
        if previous is not None:
            if previous != observation:
                raise ValueError("rhythm event identity conflict")
            return False
        events[event.event_id] = observation
        while len(events) > self.max_events:
            events.popitem(last=False)
        self._scopes.move_to_end(key)
        while len(self._scopes) > self.max_scopes:
            self._scopes.popitem(last=False)
        return True

    def snapshot(self, scope: Scope, *, now: float) -> HawkesSnapshot:
        self.prune(now)
        retained = self._scopes.get((scope.bot_id, scope.group_id))
        events = () if retained is None else tuple(retained.values())
        if not self.enabled:
            return HawkesSnapshot(scope, "disabled", "disabled_by_current_n3_policy",
                                  None, (), now, self.window_s)
        if len(events) < 2:
            return HawkesSnapshot(scope, "missing", "insufficient_authorized_events",
                                  None, events, now, self.window_s)
        times = sorted(event.observed_at for event in events)
        gaps = [max(0.001, b - a) for a, b in pairwise(times)]
        rate_per_min = 60.0 / (sum(gaps) / len(gaps))
        burst = sum(gap <= 20.0 for gap in gaps) / len(gaps)
        density = 1.0 - math.exp(-rate_per_min / 6.0)
        rho = min(0.99, 0.55 * density + 0.45 * burst)
        return HawkesSnapshot(scope, "available", "authorized_event_interval_proxy",
                              rho, events, now, self.window_s)


@dataclass(frozen=True, slots=True)
class RwsSourceSnapshot:
    scope: Scope
    subject_id: str
    as_of: float
    signals: tuple[RwsSignal, ...]
    # Keep exact owner objects for the caller's current source/revision gate;
    # these are not serialized into RWS diagnostic traces.
    memory_facts: tuple[MemoryFact, ...]
    climate: ClimateSnapshot | None
    schedule: ScheduleDayRecord | None
    rhythm: HawkesSnapshot | None
    willingness: WillingnessRecommendation | None = None


def build_gray_zone_snapshot(
    *, scope: Scope, subject_id: str, now: float,
    memory: MemoryFactSearchResult | None = None,
    rhythm: HawkesSnapshot | None = None,
    climate: ClimateSnapshot | None = None,
    schedule: ScheduleDayRecord | None = None,
    strong_addressed: bool = False,
    memory_missing_reason: str | None = None,
    willingness: WillingnessRecommendation | None = None,
    outcome_ratio: float | None = None,
) -> RwsSourceSnapshot:
    """Project actual typed sources; absence is never a fabricated neutral value.

    Memory density uses visible active facts rather than old entity cards. It
    keeps the old cap/weight but names that source change explicitly. Caller
    should request all own-subject facts (limit 50), not the 12-fact hot subset.
    Climate and schedule have no approved RWS numeric mapping; retain actual
    snapshots for provenance while leaving their numeric terms missing.
    """
    if strong_addressed:
        raise ValueError("trusted strong addressing bypasses RWS")
    if not subject_id or not math.isfinite(now):
        raise ValueError("invalid RWS snapshot identity or clock")
    replacements: dict[str, RwsSignal] = {}
    facts = () if memory is None else memory.facts
    if memory is not None:
        for fact in facts:
            if fact.scope != scope or fact.subject_id != subject_id or fact.status != "active":
                raise ValueError("RWS memory scope/subject/status mismatch")
            if ((fact.valid_from is not None and fact.valid_from > now)
                    or (fact.valid_to is not None and fact.valid_to <= now)):
                raise ValueError("RWS memory fact is not current")
        replacements["memory_familiarity"] = RwsSignal(
            "memory_familiarity", "available", "memory.authorized_active_fact_density_v1",
            "truncated_visible_lower_bound" if memory.truncated else "current_authorized_fact_density",
            min(1.0, len(facts) / MEMORY_FAMILIARITY_CAP),
        )
    elif memory_missing_reason is not None:
        replacements["memory_familiarity"] = RwsSignal(
            "memory_familiarity", "missing", "memory.search_facts", memory_missing_reason,
        )
    if rhythm is not None:
        if rhythm.scope != scope or rhythm.as_of != now:
            raise ValueError("RWS rhythm scope/time mismatch")
        replacements["hawkes_rho"] = RwsSignal(
            "hawkes_rho", rhythm.status, HAWKES_VERSION, rhythm.reason, rhythm.rho,
        )
    if climate is not None:
        if climate.key != (scope.bot_id, scope.group_id, subject_id):
            raise ValueError("RWS climate scope/subject mismatch")
        replacements["mood_residual"] = RwsSignal(
            "mood_residual", "missing", "climate.current_snapshot",
            "expression_only_no_approved_rws_mapping",
        )
    if schedule is not None:
        if (schedule.bot_id, schedule.group_id) != (scope.bot_id, scope.group_id):
            raise ValueError("RWS schedule scope mismatch")
        replacements["schedule_residual"] = RwsSignal(
            "schedule_residual", "missing", "schedule.committed_day",
            "committed_schedule_has_no_rws_activity_multiplier",
        )
    if willingness is not None:
        phase = willingness_phase(willingness)
        replacements["willingness_phase"] = RwsSignal(
            "willingness_phase", willingness.status, "conversation.authorized_group_window_v1",
            willingness.reason, phase,
        )
    replacements["outcome_ratio"] = (RwsSignal(
        "outcome_ratio", "available", "domain_learning.reviewed_episode_recall",
        "current_similar_structured_outcomes", outcome_ratio,
    ) if outcome_ratio is not None else RwsSignal(
        "outcome_ratio", "missing", "domain_learning.reviewed_episode_recall",
        "no_current_similar_labeled_outcome",
    ))
    return RwsSourceSnapshot(
        scope, subject_id, now,
        tuple(replacements.get(signal.name, signal) for signal in gray_zone_signals()),
        facts, climate, schedule, rhythm, willingness,
    )
