"""Data contracts for Persona v2 SystemModule dry-run validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ModuleGroup = Literal[
    "core",
    "runtime",
    "state",
    "memory",
    "learning",
    "context",
    "output",
    "eval",
    "observer",
    "self",
]
SlotTTL = Literal["per_turn", "per_session", "per_user", "persistent"]
SlotPrivacy = Literal["public", "group", "user_only", "admin_only"]
DisabledBehaviorValue = Literal["fail", "degrade", "skip"]
IssueLevel = Literal["error", "warn", "info"]


@dataclass(frozen=True)
class Scope:
    """RuntimeStateBus scope key.

    S1' keeps this intentionally small: enough to distinguish turn/session,
    group, and user state without binding to any existing runtime store.
    """

    session_id: str = ""
    group_id: str | None = None
    user_id: str = ""
    turn_id: str = ""

    def key(self, ttl: SlotTTL) -> tuple[str, str, str, str]:
        if ttl == "per_turn":
            return (self.session_id, self.group_id or "", self.user_id, self.turn_id)
        if ttl == "per_session":
            return (self.session_id, "", "", "")
        if ttl == "per_user":
            return ("", "", self.user_id, "")
        return ("persistent", "", "", "")


@dataclass(frozen=True)
class SourceRef:
    module_id: str
    evidence_path: str

    def to_dict(self) -> dict[str, str]:
        return {"module_id": self.module_id, "evidence_path": self.evidence_path}


@dataclass(frozen=True)
class StateSlotDefinition:
    id: str
    schema: str
    ttl: SlotTTL = "per_turn"
    privacy: SlotPrivacy = "public"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StateSlotDefinition:
        return cls(
            id=str(payload.get("id", "")).strip(),
            schema=str(payload.get("schema", "")).strip(),
            ttl=_literal(payload.get("ttl"), {"per_turn", "per_session", "per_user", "persistent"}, "per_turn"),
            privacy=_literal(payload.get("privacy"), {"public", "group", "user_only", "admin_only"}, "public"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema": self.schema,
            "ttl": self.ttl,
            "privacy": self.privacy,
        }


@dataclass(frozen=True)
class DisabledBehavior:
    behavior: DisabledBehaviorValue = "degrade"
    default_decision: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> DisabledBehavior:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            behavior=_literal(payload.get("behavior"), {"fail", "degrade", "skip"}, "degrade"),
            default_decision=dict(payload.get("default_decision") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "behavior": self.behavior,
            "default_decision": dict(self.default_decision),
        }


@dataclass(frozen=True)
class SwitchSurface:
    persona_level: bool = True
    group_level: bool = True
    turn_level: bool = False
    on_disabled: DisabledBehavior = field(default_factory=DisabledBehavior)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> SwitchSurface:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            persona_level=bool(payload.get("persona_level", True)),
            group_level=bool(payload.get("group_level", True)),
            turn_level=bool(payload.get("turn_level", False)),
            on_disabled=DisabledBehavior.from_dict(payload.get("on_disabled")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona_level": self.persona_level,
            "group_level": self.group_level,
            "turn_level": self.turn_level,
            "on_disabled": self.on_disabled.to_dict(),
        }


@dataclass(frozen=True)
class ModuleContract:
    id: str
    group: ModuleGroup
    version: str = "2.1.0-proposal"
    enabled: bool = True
    required: bool = False
    reserved: bool = False
    status: str = "active"
    persona_reads: tuple[str, ...] = ()
    persona_writes: tuple[str, ...] = ()
    state_owns: tuple[StateSlotDefinition, ...] = ()
    state_consumes: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    provides_for: tuple[str, ...] = ()
    switch_surface: SwitchSurface = field(default_factory=SwitchSurface)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ModuleContract:
        module_id = str(payload.get("id", "")).strip()
        group = _group_from_module_id(module_id, str(payload.get("group", "")).strip())
        persona_bindings = payload.get("persona_bindings") if isinstance(payload.get("persona_bindings"), dict) else {}
        state_owns_raw = payload.get("state_owns") if isinstance(payload.get("state_owns"), list) else []
        return cls(
            id=module_id,
            group=group,
            version=str(payload.get("version", "2.1.0-proposal")).strip() or "2.1.0-proposal",
            enabled=bool(payload.get("enabled", payload.get("status", "active") != "disabled")),
            required=bool(payload.get("required", False)),
            reserved=bool(payload.get("reserved", False)),
            status=str(payload.get("status", "active")).strip() or "active",
            persona_reads=_string_tuple(persona_bindings.get("reads")),
            persona_writes=_string_tuple(persona_bindings.get("writes")),
            state_owns=tuple(
                StateSlotDefinition.from_dict(item)
                for item in state_owns_raw
                if isinstance(item, dict)
            ),
            state_consumes=_string_tuple(payload.get("state_consumes")),
            depends_on=_string_tuple(payload.get("depends_on")),
            provides_for=_string_tuple(payload.get("provides_for")),
            switch_surface=SwitchSurface.from_dict(payload.get("switch_surface")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "group": self.group,
            "version": self.version,
            "enabled": self.enabled,
            "required": self.required,
            "reserved": self.reserved,
            "status": self.status,
            "persona_bindings": {
                "reads": list(self.persona_reads),
                "writes": list(self.persona_writes),
            },
            "state_owns": [slot.to_dict() for slot in self.state_owns],
            "state_consumes": list(self.state_consumes),
            "depends_on": list(self.depends_on),
            "provides_for": list(self.provides_for),
            "switch_surface": self.switch_surface.to_dict(),
        }


@dataclass(frozen=True)
class ModuleIssue:
    level: IssueLevel
    code: str
    message: str
    module_id: str = ""
    slot_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "level": self.level,
            "code": self.code,
            "message": self.message,
        }
        if self.module_id:
            payload["module_id"] = self.module_id
        if self.slot_id:
            payload["slot_id"] = self.slot_id
        return payload


@dataclass(frozen=True)
class ModuleGraphValidationResult:
    ok: bool
    issues: tuple[ModuleIssue, ...]
    module_order: tuple[str, ...] = ()

    @property
    def errors(self) -> tuple[ModuleIssue, ...]:
        return tuple(issue for issue in self.issues if issue.level == "error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [issue.to_dict() for issue in self.issues],
            "module_order": list(self.module_order),
        }


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, list | tuple | set):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _literal(value: Any, allowed: set[str], fallback: str) -> Any:
    text = str(value or "").strip()
    return text if text in allowed else fallback


def _group_from_module_id(module_id: str, declared_group: str) -> ModuleGroup:
    group = declared_group or module_id.split(".", 1)[0]
    allowed = {
        "core",
        "runtime",
        "state",
        "memory",
        "learning",
        "context",
        "output",
        "eval",
        "observer",
        "self",
    }
    if group not in allowed:
        return "core"
    return group  # type: ignore[return-value]
