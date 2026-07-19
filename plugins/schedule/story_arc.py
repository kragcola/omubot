"""StoryArc and fiction-partner ledgers for Living Persona.

Wave 2 defined the external StoryArc JSON ledger and event-budget primitives.
Wave 3 adds C-MVP fiction partner state cards and schedule-generation wiring.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal
from zoneinfo import ZoneInfo

from loguru import logger

_L = logger.bind(channel="story_arc")
_ARC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_CST = ZoneInfo("Asia/Shanghai")
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
_STORE_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: dict[str, threading.RLock] = {}

_JOURNAL_SOURCES = frozenset({
    "event_replan",
    "dream_reflection",
    "schedule_generator",
})
# Additive: factual is producer-carriable when public_projection is present.
# Deep template/hash/alias validation stays in QZone (no reverse import here).
_JOURNAL_SUBJECT_KINDS = frozenset({"self", "fiction", "factual"})
_JOURNAL_PRIVACY = frozenset({"public", "private", "unknown"})
_JOURNAL_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,179}$")


def _deep_freeze_public_projection_value(value: Any) -> Any:
    """Deep-freeze nested projection payload (maps + sequences).

    A frozen dataclass alone is not enough: a shallow ``dict(...)`` still aliases
    nested lists/dicts, so external mutation could silently rewrite carrier
    content after construction. Primitives are left as-is.
    """
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _deep_freeze_public_projection_value(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze_public_projection_value(item) for item in value)
    return value


def _deep_thaw_public_projection_value(value: Any) -> Any:
    """Return a plain JSON-ready deep copy of a frozen projection payload."""
    if isinstance(value, Mapping):
        return {
            str(key): _deep_thaw_public_projection_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_deep_thaw_public_projection_value(item) for item in value]
    if isinstance(value, list):
        return [_deep_thaw_public_projection_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class JournalEventRecord:
    """Producer-owned metadata for a StoryArc journal-eligible event.

    QZone must not invent subject_kind / privacy / salience; producers emit them
    at creation (Generative Agents / A-MEM style ingestion-time metadata).

    Factual events are strictly additive carriers: they require ``privacy=public``
    and a mapping ``public_projection`` payload. Template/hash/alias deep checks
    remain QZone-owned. self/fiction must not carry ``public_projection``.
    Schedule/Dream do not generate factual events in this cut.

    ``public_projection`` is deep-frozen at construction (MappingProxy + tuples)
    so nested mutation cannot rewrite a frozen record after the fact.
    """

    date: str
    source: Literal["event_replan", "dream_reflection", "schedule_generator"]
    summary: str
    subject_kind: Literal["self", "fiction", "factual"]
    privacy: Literal["public", "private", "unknown"]
    salience: float
    event_id: str | None = None
    public_projection: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        try:
            date.fromisoformat(str(self.date).strip())
        except ValueError as exc:
            raise ValueError(f"invalid date: {self.date!r}") from exc
        object.__setattr__(self, "date", str(self.date).strip())

        source = str(self.source or "").strip()
        if source not in _JOURNAL_SOURCES:
            raise ValueError(f"invalid source: {self.source!r}")
        object.__setattr__(self, "source", source)

        summary = str(self.summary or "").strip()
        if not summary:
            raise ValueError("summary must be non-empty")
        object.__setattr__(self, "summary", summary)

        subject_kind = str(self.subject_kind or "").strip()
        if subject_kind not in _JOURNAL_SUBJECT_KINDS:
            raise ValueError(f"invalid subject_kind: {self.subject_kind!r}")
        object.__setattr__(self, "subject_kind", subject_kind)

        privacy = str(self.privacy or "").strip()
        if privacy not in _JOURNAL_PRIVACY:
            raise ValueError(f"invalid privacy: {self.privacy!r}")
        object.__setattr__(self, "privacy", privacy)

        if isinstance(self.salience, bool) or not isinstance(self.salience, (int, float)):
            raise ValueError(f"invalid salience: {self.salience!r}")
        salience = float(self.salience)
        if not math.isfinite(salience) or not 0.0 <= salience <= 1.0:
            raise ValueError(f"invalid salience: {self.salience!r}")
        object.__setattr__(self, "salience", salience)

        if self.event_id is not None:
            event_id = str(self.event_id).strip()
            if _JOURNAL_EVENT_ID_RE.fullmatch(event_id) is None:
                raise ValueError(f"invalid event_id: {self.event_id!r}")
            object.__setattr__(self, "event_id", event_id)

        # Strict additive factual carrier (shallow structure only at this boundary).
        if subject_kind == "factual":
            if privacy != "public":
                raise ValueError("factual events require public privacy")
            if self.public_projection is None:
                raise ValueError(
                    "factual events require public_projection mapping payload"
                )
            if not isinstance(self.public_projection, Mapping):
                raise ValueError("public_projection must be a mapping")
            # Deep freeze nested maps/lists; do not import qzone validators here.
            object.__setattr__(
                self,
                "public_projection",
                _deep_freeze_public_projection_value(self.public_projection),
            )
        elif self.public_projection is not None:
            raise ValueError(
                "public_projection is only allowed for subject_kind=factual"
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "date": self.date,
            "source": self.source,
            "summary": self.summary,
            "subject_kind": self.subject_kind,
            "privacy": self.privacy,
            "salience": self.salience,
        }
        if self.event_id is not None:
            payload["event_id"] = self.event_id
        # Include projection only for factual carriers (never for self/fiction).
        # Deep thaw so callers receive plain dict/list without shared mutables.
        if self.subject_kind == "factual" and self.public_projection is not None:
            payload["public_projection"] = _deep_thaw_public_projection_value(
                self.public_projection
            )
        return payload


def _list_str(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _dict_value(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list_dict(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _safe_arc_id(arc_id: str) -> str:
    arc_id = str(arc_id or "").strip()
    if not _ARC_ID_RE.fullmatch(arc_id):
        raise ValueError(f"invalid story arc id: {arc_id!r}")
    return arc_id


def _coerce_date(value: str | date | datetime | None) -> date:
    if value is None:
        return datetime.now(_CST).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def _arc_boundary(value: str) -> date | None:
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_terminal_stage(stage: str) -> bool:
    return str(stage or "").strip().lower() in _TERMINAL_STAGES


def _store_thread_lock(path: Path) -> threading.RLock:
    key = str(path.expanduser().resolve())
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STORE_LOCKS[key] = lock
        return lock


class StoryArcConflictError(RuntimeError):
    """Raised when a stale StoryArc snapshot attempts to overwrite a newer revision."""


@dataclass(slots=True)
class StoryArc:
    arc_id: str
    revision: int = 0
    title: str = ""
    scope: str = "fiction"
    stage: str = "planning"
    goals: list[str] = field(default_factory=list)
    active_conflicts: list[str] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)
    partner_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    open_threads: list[str] = field(default_factory=list)
    last_events: list[dict[str, Any]] = field(default_factory=list)
    next_day_seed: str = ""
    starts_on: str = ""
    ends_on: str = ""
    event_budget: dict[str, Any] = field(default_factory=dict)
    # Worldbook Living Story Runtime v1 extensions (optional, default-safe).
    # arc_role/stack_order pin main+side+ambient without mtime selection.
    arc_role: str = "side"
    stack_order: int = 0
    status: str = "active"
    deadlines: list[dict[str, Any]] = field(default_factory=list)
    causal_links: list[dict[str, Any]] = field(default_factory=list)
    event_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "arc_id": _safe_arc_id(self.arc_id),
            "revision": max(0, int(self.revision)),
            "title": self.title,
            "scope": self.scope,
            "starts_on": self.starts_on,
            "ends_on": self.ends_on,
            "stage": self.stage,
            "goals": list(self.goals),
            "active_conflicts": list(self.active_conflicts),
            "variables": dict(self.variables),
            "partner_states": {
                str(entity_id): dict(state)
                for entity_id, state in self.partner_states.items()
            },
            "open_threads": list(self.open_threads),
            "last_events": [dict(event) for event in self.last_events],
            "next_day_seed": self.next_day_seed,
            "event_budget": dict(self.event_budget),
            "arc_role": str(self.arc_role or "side"),
            "stack_order": int(self.stack_order or 0),
            "status": str(self.status or "active"),
            "deadlines": [dict(item) for item in self.deadlines],
            "causal_links": [dict(item) for item in self.causal_links],
            "event_history": [dict(item) for item in self.event_history],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StoryArc:
        arc_id = _safe_arc_id(str(data.get("arc_id", "") or ""))
        raw_partner_states = _dict_value(data.get("partner_states"))
        partner_states: dict[str, dict[str, Any]] = {
            str(entity_id): dict(state)
            for entity_id, state in raw_partner_states.items()
            if isinstance(state, dict)
        }
        role = str(data.get("arc_role") or data.get("role") or "side").strip().lower()
        if role not in {"main", "side", "ambient"}:
            role = "side"
        return cls(
            arc_id=arc_id,
            revision=max(0, int(data.get("revision", 0) or 0)),
            title=str(data.get("title", "") or ""),
            scope=str(data.get("scope", "fiction") or "fiction"),
            starts_on=str(data.get("starts_on", "") or ""),
            ends_on=str(data.get("ends_on", "") or ""),
            stage=str(data.get("stage", "planning") or "planning"),
            goals=_list_str(data.get("goals")),
            active_conflicts=_list_str(data.get("active_conflicts")),
            variables=_dict_value(data.get("variables")),
            partner_states=partner_states,
            open_threads=_list_str(data.get("open_threads")),
            last_events=_list_dict(data.get("last_events")),
            next_day_seed=str(data.get("next_day_seed", "") or ""),
            event_budget=_dict_value(data.get("event_budget")),
            arc_role=role,
            stack_order=int(data.get("stack_order") or 0),
            status=str(data.get("status") or "active") or "active",
            deadlines=_list_dict(data.get("deadlines")),
            causal_links=_list_dict(data.get("causal_links")),
            event_history=_list_dict(data.get("event_history")),
        )


@dataclass(frozen=True, slots=True)
class StoryArcEventCandidate:
    event_id: str
    event_type: str = "daily"
    salience: float = 1.0
    severity: str = "daily"
    once_only: bool = False
    cooldown_key: str = ""
    cooldown_steps: int = 0

    @property
    def budget_key(self) -> str:
        return self.cooldown_key or self.event_type or self.event_id

    @property
    def is_setback(self) -> bool:
        return self.severity == "setback"


@dataclass(frozen=True, slots=True)
class FictionPartnerProfile:
    entity_id: str
    display_name: str
    pinned_profile: str
    constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _safe_arc_id(self.entity_id)
        if not self.display_name.strip():
            raise ValueError("display_name is required")


@dataclass(slots=True)
class FictionPartnerState:
    entity_id: str
    kind: str = "fiction"
    display_name: str = ""
    pinned_profile: str = ""
    current_state: str = "日常状态稳定，等待主线事件推进。"
    mood: str = "平稳"
    availability: str = "normal"
    recent_events: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    # Exact, non-expiring side-effect ledger. recent_events is bounded history only.
    applied_event_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        if self.kind != "fiction":
            raise ValueError("fiction partner state must use kind='fiction'")
        return {
            "entity_id": _safe_arc_id(self.entity_id),
            "kind": "fiction",
            "display_name": self.display_name,
            "pinned_profile": self.pinned_profile,
            "current_state": self.current_state,
            "mood": self.mood,
            "availability": self.availability,
            "recent_events": list(self.recent_events),
            "constraints": list(self.constraints),
            "applied_event_ids": list(self.applied_event_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FictionPartnerState:
        kind = str(data.get("kind", "fiction") or "fiction")
        if kind != "fiction":
            raise ValueError("only fiction partner states are supported in C-MVP")
        raw_applied = data.get("applied_event_ids")
        applied: list[str] = []
        if isinstance(raw_applied, list):
            seen: set[str] = set()
            for item in raw_applied:
                text = str(item or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    applied.append(text)
        return cls(
            entity_id=_safe_arc_id(str(data.get("entity_id", "") or "")),
            kind="fiction",
            display_name=str(data.get("display_name", "") or ""),
            pinned_profile=str(data.get("pinned_profile", "") or ""),
            current_state=str(data.get("current_state", "") or "日常状态稳定，等待主线事件推进。"),
            mood=str(data.get("mood", "") or "平稳"),
            availability=str(data.get("availability", "") or "normal"),
            recent_events=_list_str(data.get("recent_events")),
            constraints=_list_str(data.get("constraints")),
            applied_event_ids=applied,
        )

    @classmethod
    def from_profile(cls, profile: FictionPartnerProfile) -> FictionPartnerState:
        return cls(
            entity_id=profile.entity_id,
            display_name=profile.display_name,
            pinned_profile=profile.pinned_profile,
            constraints=list(profile.constraints),
        )

    def to_arc_state(self) -> dict[str, Any]:
        return {
            "kind": "fiction",
            "display_name": self.display_name,
            "pinned_profile": self.pinned_profile,
            "current_state": self.current_state,
            "mood": self.mood,
            "availability": self.availability,
            "recent_events": list(self.recent_events[:3]),
            "constraints": list(self.constraints[:3]),
            "applied_event_ids": list(self.applied_event_ids),
        }


class StoryArcStore:
    """Read/write StoryArc JSON files under storage/living_persona/story_arcs."""

    def __init__(
        self,
        storage_dir: str | Path = "storage/living_persona/story_arcs",
        *,
        seed_factory: Callable[[str], StoryArc | None] | None = None,
    ) -> None:
        self._dir = Path(storage_dir)
        self._archive_dir = self._dir / "archive"
        self._lock_path = self._dir / ".story_arc.lock"
        self._thread_lock = _store_thread_lock(self._dir)
        self._seed_factory = seed_factory

    async def startup(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def load(self, arc_id: str) -> StoryArc | None:
        try:
            path = self._path(arc_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("story arc root must be an object")
            return StoryArc.from_dict(raw)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            _L.error("story arc load failed | path={} error={}", path, exc)
            return None

    def load_active(self, *, on_date: str | date | datetime | None = None) -> StoryArc | None:
        """Return the newest currently active arc and archive finished ledgers."""
        with self._locked():
            return self._load_active_unlocked(_coerce_date(on_date))

    def _load_active_unlocked(self, current_date: date) -> StoryArc | None:
        candidates = sorted(self._dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        active: StoryArc | None = None
        for path in candidates:
            loaded = self.load(path.stem)
            if loaded is None:
                continue
            ends_on = _arc_boundary(loaded.ends_on)
            if _is_terminal_stage(loaded.stage) or (ends_on is not None and current_date > ends_on):
                self._archive_unlocked(loaded.arc_id)
                continue
            starts_on = _arc_boundary(loaded.starts_on)
            if starts_on is not None and current_date < starts_on:
                continue
            if active is None:
                active = loaded
        return active

    def archive(self, arc_id: str) -> bool:
        """Move an inactive arc out of the active ledger directory."""
        with self._locked():
            return self._archive_unlocked(arc_id)

    def _archive_unlocked(self, arc_id: str) -> bool:
        source = self._path(arc_id)
        if not source.exists():
            return False
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        destination = self._archive_dir / source.name
        os.replace(source, destination)
        _L.info("story arc archived | arc_id={}", arc_id)
        return True

    def save(self, arc: StoryArc) -> None:
        """Persist one optimistic snapshot, rejecting stale revisions."""
        with self._locked():
            self._save_unlocked(arc)

    def _save_unlocked(self, arc: StoryArc) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(arc.arc_id)
        current = self.load(arc.arc_id)
        expected_revision = max(0, int(arc.revision))
        actual_revision = current.revision if current is not None else 0
        if (current is None and expected_revision != 0) or (
            current is not None and expected_revision != actual_revision
        ):
            raise StoryArcConflictError(
                f"story arc revision conflict: arc_id={arc.arc_id} "
                f"expected={expected_revision} actual={actual_revision}"
            )
        previous_revision = arc.revision
        arc.revision = actual_revision + 1
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(arc.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except BaseException:
            arc.revision = previous_revision
            raise
        _L.info(
            "story arc saved | arc_id={} stage={} revision={}",
            arc.arc_id,
            arc.stage,
            arc.revision,
        )

    def update(
        self,
        arc_id: str,
        mutator: Callable[[StoryArc], None],
    ) -> StoryArc:
        """Reload and mutate an arc while holding the shared writer lock."""
        with self._locked():
            current = self.load(arc_id)
            if current is None:
                raise KeyError(f"story arc not found: {arc_id}")
            before = current.to_dict()
            mutator(current)
            if current.to_dict() == before:
                return current
            self._save_unlocked(current)
            return current

    def ensure_seeded(self, on_date: str | date | datetime | None = None) -> StoryArc | None:
        """Seed a clean ledger only through an explicitly supplied fiction factory."""
        current_date = _coerce_date(on_date)
        with self._locked():
            active = self._load_active_unlocked(current_date)
            if active is not None:
                return active
            if any(self._dir.glob("*.json")) or self._seed_factory is None:
                return None
            date_str = current_date.isoformat()
            seeded = self._seed_factory(date_str)
            if seeded is None:
                return None
            if not isinstance(seeded, StoryArc):
                raise TypeError("story arc seed_factory must return StoryArc or None")
            if seeded.scope.strip().lower() != "fiction":
                raise ValueError("story arc seed_factory may only create fiction arcs")
            starts_on = _arc_boundary(seeded.starts_on)
            ends_on = _arc_boundary(seeded.ends_on)
            if _is_terminal_stage(seeded.stage):
                raise ValueError("story arc seed must not use a terminal stage")
            if starts_on is not None and current_date < starts_on:
                raise ValueError("story arc seed must be active on the requested date")
            if ends_on is not None and current_date > ends_on:
                raise ValueError("story arc seed must be active on the requested date")
            self._save_unlocked(seeded)
            return seeded

    def list_arc_ids(self) -> list[str]:
        return sorted(path.stem for path in self._dir.glob("*.json"))

    def list_archived_arc_ids(self) -> list[str]:
        return sorted(path.stem for path in self._archive_dir.glob("*.json"))

    def _path(self, arc_id: str) -> Path:
        return self._dir / f"{_safe_arc_id(arc_id)}.json"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._thread_lock.acquire()
        handle = None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            handle = self._lock_path.open("a+", encoding="utf-8")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if handle is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            self._thread_lock.release()


def _default_fiction_story_arc(on_date: str) -> StoryArc:
    start = date.fromisoformat(on_date)
    end = start + timedelta(days=6)
    return StoryArc(
        arc_id=f"weekly_life_{start.strftime('%Y%m%d')}",
        title="学习与舞台的一周",
        scope="fiction",
        starts_on=start.isoformat(),
        ends_on=end.isoformat(),
        stage="planning",
        goals=[
            "让学习、舞台练习与休息形成可延续的节奏",
            "和 W×S 伙伴一起完成这一周的日常推进",
        ],
        active_conflicts=["想做的事情很多，但每天的时间和精力有限"],
        variables={
            "deadline_days_left": 6,
            "exam_pressure": 0.35,
            "rehearsal_progress": 0.1,
            "team_morale": 0.7,
        },
        open_threads=["根据今天的日程选择本周第一个具体推进点"],
        next_day_seed="从今天真实生成的日程出发，建立这一周的第一段连续因果。",
    )


def create_default_story_arc_store(
    storage_dir: str | Path = "storage/living_persona/story_arcs",
) -> StoryArcStore:
    """Build the production ledger with an explicit fiction-only seed factory."""
    return StoryArcStore(storage_dir, seed_factory=_default_fiction_story_arc)


class FictionPartnerStateStore:
    """Read/write C-MVP fiction partner cards under storage/living_persona."""

    def __init__(self, storage_dir: str | Path = "storage/living_persona/partner_states") -> None:
        self._dir = Path(storage_dir)

    async def startup(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def load(self, entity_id: str) -> FictionPartnerState | None:
        try:
            path = self._path(entity_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("fiction partner state root must be an object")
            return FictionPartnerState.from_dict(raw)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            _L.error("fiction partner state load failed | path={} error={}", path, exc)
            return None

    def save(self, state: FictionPartnerState) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(state.entity_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        _L.info("fiction partner state saved | entity_id={}", state.entity_id)

    def ensure_cards(self, profiles: Sequence[FictionPartnerProfile]) -> list[FictionPartnerState]:
        states: list[FictionPartnerState] = []
        for profile in profiles:
            existing = self.load(profile.entity_id)
            if existing is None:
                existing = FictionPartnerState.from_profile(profile)
                self.save(existing)
            states.append(existing)
        return states

    def list_entity_ids(self) -> list[str]:
        return sorted(path.stem for path in self._dir.glob("*.json"))

    def _path(self, entity_id: str) -> Path:
        return self._dir / f"{_safe_arc_id(entity_id)}.json"


def _budget_list(arc: StoryArc, key: str) -> list[str]:
    value = arc.event_budget.get(key)
    if not isinstance(value, list):
        value = []
        arc.event_budget[key] = value
    return [str(item) for item in value]


def can_trigger_event(
    arc: StoryArc,
    candidate: StoryArcEventCandidate,
    *,
    now_step: int = 0,
) -> bool:
    """Check once-only, per-arc setback, and cooldown constraints."""
    triggered_once = set(_budget_list(arc, "triggered_once"))
    if candidate.once_only and candidate.event_id in triggered_once:
        return False
    if candidate.is_setback and int(arc.event_budget.get("setback_count", 0) or 0) >= 1:
        return False
    cooldowns = _dict_value(arc.event_budget.get("cooldowns"))
    arc.event_budget["cooldowns"] = cooldowns
    cooldown_until = int(cooldowns.get(candidate.budget_key, -1) or -1)
    return int(now_step) >= cooldown_until


def record_event_trigger(
    arc: StoryArc,
    candidate: StoryArcEventCandidate,
    *,
    now_step: int = 0,
) -> bool:
    """Record a selected event if it passes the event-budget guards."""
    if not can_trigger_event(arc, candidate, now_step=now_step):
        return False
    if candidate.once_only:
        triggered_once = set(_budget_list(arc, "triggered_once"))
        triggered_once.add(candidate.event_id)
        arc.event_budget["triggered_once"] = sorted(triggered_once)
    if candidate.is_setback:
        arc.event_budget["setback_count"] = int(arc.event_budget.get("setback_count", 0) or 0) + 1
    if candidate.cooldown_steps > 0:
        cooldowns = _dict_value(arc.event_budget.get("cooldowns"))
        cooldowns[candidate.budget_key] = int(now_step) + int(candidate.cooldown_steps)
        arc.event_budget["cooldowns"] = cooldowns
    last_viewed = _dict_value(arc.event_budget.get("last_viewed"))
    last_viewed[candidate.event_id] = int(now_step)
    arc.event_budget["last_viewed"] = last_viewed
    return True


def choose_best_least_recently_viewed(
    arc: StoryArc,
    candidates: list[StoryArcEventCandidate],
    *,
    now_step: int = 0,
) -> StoryArcEventCandidate | None:
    """Choose the highest-salience eligible candidate, then the least recently viewed."""
    eligible = [candidate for candidate in candidates if can_trigger_event(arc, candidate, now_step=now_step)]
    if not eligible:
        return None
    best_salience = max(float(candidate.salience) for candidate in eligible)
    best = [candidate for candidate in eligible if float(candidate.salience) == best_salience]
    last_viewed = _dict_value(arc.event_budget.get("last_viewed"))

    def sort_key(candidate: StoryArcEventCandidate) -> tuple[int, str]:
        viewed = int(last_viewed.get(candidate.event_id, -1) or -1)
        return viewed, candidate.event_id

    return sorted(best, key=sort_key)[0]
