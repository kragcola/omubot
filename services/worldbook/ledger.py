"""Story ledger adapter over existing StoryArcStore (no second truth source)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from services.worldbook.domain import ArcRole, StoryLedgerView

_CST = ZoneInfo("Asia/Shanghai")
_TERMINAL = frozenset({
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


def _coerce_date(value: str | date | datetime | None) -> date:
    if value is None:
        return datetime.now(_CST).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def _arc_role(arc: Mapping[str, Any] | Any) -> ArcRole:
    if isinstance(arc, Mapping):
        role = str(arc.get("arc_role") or arc.get("role") or "side").strip().lower()
    else:
        role = str(getattr(arc, "arc_role", "side") or "side").strip().lower()
    if role in {"main", "side", "ambient"}:
        return cast(ArcRole, role)
    return "side"


def _arc_dict(arc: Any) -> dict[str, Any]:
    if isinstance(arc, Mapping):
        return dict(arc)
    to_dict = getattr(arc, "to_dict", None)
    if callable(to_dict):
        serialized = to_dict()
        if isinstance(serialized, Mapping):
            return {str(key): value for key, value in serialized.items()}
    return {
        "arc_id": str(getattr(arc, "arc_id", "") or ""),
        "title": str(getattr(arc, "title", "") or ""),
        "stage": str(getattr(arc, "stage", "") or ""),
        "arc_role": _arc_role(arc),
        "stack_order": int(getattr(arc, "stack_order", 0) or 0),
        "goals": list(getattr(arc, "goals", []) or []),
        "open_threads": list(getattr(arc, "open_threads", []) or []),
        "status": str(getattr(arc, "status", "") or ""),
    }


def _is_active(arc: Mapping[str, Any], current: date) -> bool:
    stage = str(arc.get("stage") or "").strip().lower()
    status = str(arc.get("status") or "").strip().lower()
    if stage in _TERMINAL or status in _TERMINAL:
        return False
    ends = str(arc.get("ends_on") or "").strip()
    if ends:
        try:
            if current > date.fromisoformat(ends):
                return False
        except ValueError:
            pass
    starts = str(arc.get("starts_on") or "").strip()
    if starts:
        try:
            if current < date.fromisoformat(starts):
                return False
        except ValueError:
            pass
    return True


def _sort_key(arc: Mapping[str, Any]) -> tuple[int, str]:
    order = int(arc.get("stack_order") or 0)
    return (order, str(arc.get("arc_id") or ""))


class StoryLedgerAdapter:
    """Stable main+side+ambient stack over StoryArcStore.

    When worldbook gate is active, main is selected by explicit ``arc_role`` /
    ``stack_order`` — never by mtime.
    """

    def __init__(self, story_arc_store: Any | None = None) -> None:
        self._store = story_arc_store

    def set_store(self, store: Any | None) -> None:
        self._store = store

    def load_stack(
        self,
        *,
        on_date: str | date | datetime | None = None,
    ) -> StoryLedgerView:
        current = _coerce_date(on_date)
        arcs = self._load_all_active(current)
        mains = [a for a in arcs if _arc_role(a) == "main"]
        sides = [a for a in arcs if _arc_role(a) == "side"]
        ambient = [a for a in arcs if _arc_role(a) == "ambient"]
        # Unspecified role treated as side already; if no explicit main but arcs
        # exist with legacy data, pick lowest stack_order then arc_id (stable).
        main: dict[str, Any] | None = None
        if mains:
            mains_sorted = sorted(mains, key=_sort_key)
            main = mains_sorted[0]
            # extras with role=main become sides to keep single main.
            sides.extend(mains_sorted[1:])
        elif arcs:
            # Legacy arcs without arc_role: do NOT use mtime. Prefer stack_order
            # then arc_id; only one becomes main when stack is non-empty and no
            # explicit main — callers may set arc_role="main" to pin.
            legacy = sorted(arcs, key=_sort_key)
            # Keep legacy as sides only unless a single arc exists (compat).
            if len(legacy) == 1:
                main = legacy[0]
                sides = []
            else:
                sides = legacy
        sides_t = tuple(
            dict(a) for a in sorted(sides, key=_sort_key) if a is not main
        )
        ambient_t = tuple(dict(a) for a in sorted(ambient, key=_sort_key))
        ordering: list[str] = []
        if main is not None:
            ordering.append(str(main.get("arc_id") or ""))
        ordering.extend(str(a.get("arc_id") or "") for a in sides_t)
        ordering.extend(str(a.get("arc_id") or "") for a in ambient_t)
        return StoryLedgerView(
            main=dict(main) if main is not None else None,
            sides=sides_t,
            ambient=ambient_t,
            ordering=tuple(x for x in ordering if x),
        )

    def _load_all_active(self, current: date) -> list[dict[str, Any]]:
        store = self._store
        if store is None:
            return []
        # Prefer list_arc_ids + load (deterministic) over load_active (mtime).
        list_ids = getattr(store, "list_arc_ids", None)
        load = getattr(store, "load", None)
        if callable(list_ids) and callable(load):
            out: list[dict[str, Any]] = []
            raw_ids = list_ids()
            if not isinstance(raw_ids, Iterable) or isinstance(raw_ids, str | bytes):
                return []
            for arc_id in raw_ids:
                loaded = load(arc_id)
                if loaded is None:
                    continue
                data = _arc_dict(loaded)
                if _is_active(data, current):
                    out.append(data)
            return out
        # Fallback: load_active only (single arc) — still not multi-mtime race
        # for multi-arc stack when list API missing.
        load_active = getattr(store, "load_active", None)
        if callable(load_active):
            try:
                active = load_active(on_date=current)
            except TypeError:
                active = load_active()
            if active is None:
                return []
            data = _arc_dict(active)
            return [data] if _is_active(data, current) else []
        return []

    def load_from_arc_dicts(
        self,
        arcs: Sequence[Mapping[str, Any] | Any],
        *,
        on_date: str | date | datetime | None = None,
    ) -> StoryLedgerView:
        """Test / offline helper: build stack from plain arc dicts."""
        current = _coerce_date(on_date)
        active = [_arc_dict(a) for a in arcs if _is_active(_arc_dict(a), current)]
        # Temporarily use in-memory store shim.
        class _Mem:
            def list_arc_ids(self) -> list[str]:
                return [str(a.get("arc_id") or "") for a in active]

            def load(self, arc_id: str) -> dict[str, Any] | None:
                for a in active:
                    if str(a.get("arc_id") or "") == arc_id:
                        return a
                return None

        previous = self._store
        self._store = _Mem()
        try:
            return self.load_stack(on_date=current)
        finally:
            self._store = previous
