"""Event-driven reducer for committed worldbook events.

In worldbook-enabled mode, variables mutate only through this reducer —
never via fixed daily increments.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from services.worldbook.domain import EventRecord, utc_now_iso


def _arc_id_of(arc: Any) -> str:
    if isinstance(arc, Mapping):
        return str(arc.get("arc_id") or "").strip()
    return str(getattr(arc, "arc_id", "") or "").strip()


def _committed_event_ids(arc: Any) -> set[str]:
    """Stable idempotency set that survives human-readable history eviction."""
    raw = getattr(arc, "event_budget", None)
    if not isinstance(raw, dict):
        return set()
    ids = raw.get("committed_event_ids")
    if not isinstance(ids, list):
        return set()
    return {str(x).strip() for x in ids if str(x).strip()}


def _record_committed_event_id(arc: Any, event_id: str) -> None:
    """Record event_id for exact replay idempotency (Stage-0: never expires).

    Human-readable ``event_history`` / ``last_events`` may be bounded and
    evicted. ``committed_event_ids`` is the durable exact set and must not
    semantically expire IDs — replaying any past committed id must remain a
    no-op even after thousands of later commits.
    """
    budget = getattr(arc, "event_budget", None)
    if not isinstance(budget, dict):
        # Best-effort: attach if arc supports dict assignment.
        try:
            arc.event_budget = {}
            budget = arc.event_budget
        except Exception:
            return
    if not isinstance(budget, dict):
        return
    existing = budget.get("committed_event_ids")
    ids = (
        [str(x) for x in existing if str(x).strip()]
        if isinstance(existing, list)
        else []
    )
    if event_id not in ids:
        ids.append(event_id)
    # No cap / semantic expiry: bounded history eviction must not drop
    # idempotency keys. Storage growth is acceptable for Stage-0 offline arcs.
    budget["committed_event_ids"] = ids


class EventReducer:
    """Apply committed events to a StoryArc-like mutable mapping/object."""

    def apply(
        self,
        arc: Any,
        event: EventRecord,
        *,
        now_step: int | None = None,
    ) -> EventRecord:
        if event.status != "committed":
            raise ValueError(
                f"reducer only accepts committed events, got status={event.status!r}"
            )
        target_arc_id = _arc_id_of(arc)
        event_arc_id = str(event.arc_id or "").strip()
        if event_arc_id and target_arc_id and event_arc_id != target_arc_id:
            raise ValueError(
                f"event.arc_id {event_arc_id!r} does not match arc.id {target_arc_id!r}"
            )
        step = int(now_step if now_step is not None else event.step)

        # Stable idempotency set (survives event_history / last_events eviction).
        if event.event_id in _committed_event_ids(arc):
            return replace(
                event,
                step=step if event.step == 0 else int(event.step),
                committed_at=event.committed_at or utc_now_iso(),
                status="committed",
            )

        for collection_name in ("event_history", "last_events"):
            collection = getattr(arc, collection_name, None)
            if not isinstance(collection, list):
                continue
            for existing in collection:
                if not isinstance(existing, dict):
                    continue
                if str(existing.get("event_id") or "") != event.event_id:
                    continue
                _record_committed_event_id(arc, event.event_id)
                return replace(
                    event,
                    step=int(existing.get("step") or step),
                    committed_at=str(
                        existing.get("committed_at")
                        or event.committed_at
                        or utc_now_iso()
                    ),
                    status="committed",
                )

        deltas = dict(event.variable_deltas)
        variables = getattr(arc, "variables", None)
        if not isinstance(variables, dict):
            raise TypeError("arc.variables must be a dict")
        for key, delta in deltas.items():
            key_s = str(key)
            current = variables.get(key_s, 0)
            if isinstance(delta, (int, float)) and not isinstance(delta, bool):
                try:
                    base = float(current) if current is not None else 0.0
                except (TypeError, ValueError):
                    base = 0.0
                new_val = base + float(delta)
                # Preserve int when both were integral-looking.
                if isinstance(delta, int) and (
                    isinstance(current, int)
                    or current is None
                    or float(current).is_integer()
                ):
                    variables[key_s] = round(new_val)
                else:
                    variables[key_s] = round(new_val, 6)
            else:
                variables[key_s] = delta

        # Append to event history / last_events without inventing factual social data.
        record = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "summary": event.summary,
            "severity": event.severity,
            "step": step,
            "source": event.source,
            "arc_id": event_arc_id or target_arc_id,
            "evidence_refs": list(event.evidence_refs),
            "committed_at": event.committed_at or utc_now_iso(),
            "variable_deltas": dict(deltas),
            "consequences": list(event.consequences),
        }
        last_events = getattr(arc, "last_events", None)
        if isinstance(last_events, list):
            last_events.append(record)
            # keep bounded
            if len(last_events) > 64:
                del last_events[:-64]
        event_history = getattr(arc, "event_history", None)
        if isinstance(event_history, list):
            event_history.append(record)
            if len(event_history) > 256:
                del event_history[:-256]

        # Open threads from consequences.
        open_threads = getattr(arc, "open_threads", None)
        if isinstance(open_threads, list):
            for consequence in event.consequences:
                text = str(consequence).strip()
                if text and text not in open_threads:
                    open_threads.append(text)

        # Causal links optional list on arc.
        causal_links = getattr(arc, "causal_links", None)
        if isinstance(causal_links, list) and event.event_id:
            causal_links.append(
                {
                    "from_event": event.event_id,
                    "to": list(event.consequences),
                    "step": step,
                }
            )

        # Budget bookkeeping for setbacks / recovery + stable idempotency ids.
        event_budget = getattr(arc, "event_budget", None)
        if not isinstance(event_budget, dict):
            try:
                arc.event_budget = {}
                event_budget = arc.event_budget
            except Exception:
                event_budget = None
        if isinstance(event_budget, dict):
            event_budget["last_event_step"] = step
            if str(event.severity).lower() in {"setback", "major", "crisis"}:
                event_budget["setback_count"] = (
                    int(event_budget.get("setback_count") or 0) + 1
                )
                recovery = int(event.recovery_steps or 0)
                if recovery > 0:
                    event_budget["recovery_until_step"] = step + recovery
            _record_committed_event_id(arc, event.event_id)

        return replace(
            event,
            step=step,
            committed_at=event.committed_at or utc_now_iso(),
            status="committed",
        )

    def reject_fixed_daily_increment(self, reason: str = "fixed_daily") -> None:
        raise RuntimeError(
            f"worldbook-enabled mode forbids fixed daily variable increments ({reason})"
        )


def apply_variable_deltas(
    variables: dict[str, Any],
    deltas: Mapping[str, Any],
) -> dict[str, Any]:
    """Pure helper used by tests and callers without a full arc object."""
    out = dict(variables)
    for key, delta in deltas.items():
        key_s = str(key)
        current = out.get(key_s, 0)
        if isinstance(delta, (int, float)) and not isinstance(delta, bool):
            try:
                base = float(current) if current is not None else 0.0
            except (TypeError, ValueError):
                base = 0.0
            out[key_s] = round(base + float(delta), 6)
        else:
            out[key_s] = delta
    return out
