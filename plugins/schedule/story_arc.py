"""StoryArc and fiction-partner ledgers for Living Persona.

Wave 2 defined the external StoryArc JSON ledger and event-budget primitives.
Wave 3 adds C-MVP fiction partner state cards and schedule-generation wiring.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

_L = logger.bind(channel="story_arc")
_ARC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


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


@dataclass(slots=True)
class StoryArc:
    arc_id: str
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "arc_id": _safe_arc_id(self.arc_id),
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
        return cls(
            arc_id=arc_id,
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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FictionPartnerState:
        kind = str(data.get("kind", "fiction") or "fiction")
        if kind != "fiction":
            raise ValueError("only fiction partner states are supported in C-MVP")
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
        }


class StoryArcStore:
    """Read/write StoryArc JSON files under storage/living_persona/story_arcs."""

    def __init__(self, storage_dir: str | Path = "storage/living_persona/story_arcs") -> None:
        self._dir = Path(storage_dir)

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

    def load_active(self) -> StoryArc | None:
        """Return the newest arc in the ledger, or None for an empty directory."""
        candidates = sorted(self._dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in candidates:
            loaded = self.load(path.stem)
            if loaded is not None:
                return loaded
        return None

    def save(self, arc: StoryArc) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(arc.arc_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(arc.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        _L.info("story arc saved | arc_id={} stage={}", arc.arc_id, arc.stage)

    def list_arc_ids(self) -> list[str]:
        return sorted(path.stem for path in self._dir.glob("*.json"))

    def _path(self, arc_id: str) -> Path:
        return self._dir / f"{_safe_arc_id(arc_id)}.json"


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
