"""In-memory RuntimeStateBus skeleton for Persona v2 Part B S1'."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .models import ModuleContract, Scope, SourceRef, StateSlotDefinition


class StateSlotOwnershipError(RuntimeError):
    """Raised when a module writes a state slot it does not own."""


@dataclass(frozen=True)
class SlotSnapshot:
    slot_id: str
    value: Any
    scope: Scope
    source: SourceRef
    confidence: float
    updated_at: datetime
    decay_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "value": self.value,
            "scope": {
                "session_id": self.scope.session_id,
                "group_id": self.scope.group_id,
                "user_id": self.scope.user_id,
                "turn_id": self.scope.turn_id,
            },
            "source": self.source.to_dict(),
            "confidence": self.confidence,
            "updated_at": self.updated_at.isoformat(timespec="seconds"),
            "decay_at": self.decay_at.isoformat(timespec="seconds") if self.decay_at else None,
        }


class RuntimeStateBus:
    """A minimal typed state-slot bus.

    S1' is intentionally in-memory and dry-run friendly. It proves ownership,
    scope keys, TTL cleanup and trace snapshots without migrating storage.
    """

    def __init__(self, contracts: list[ModuleContract] | tuple[ModuleContract, ...]) -> None:
        self._slot_defs: dict[str, StateSlotDefinition] = {}
        self._owners: dict[str, str] = {}
        self._values: dict[tuple[str, tuple[str, str, str, str]], SlotSnapshot] = {}

        for contract in contracts:
            if not contract.enabled:
                continue
            for slot in contract.state_owns:
                if slot.id in self._owners:
                    raise StateSlotOwnershipError(
                        f"state slot {slot.id!r} has multiple owners: {self._owners[slot.id]}, {contract.id}"
                    )
                self._owners[slot.id] = contract.id
                self._slot_defs[slot.id] = slot

    @property
    def owners(self) -> dict[str, str]:
        return dict(self._owners)

    def get(self, slot_id: str, *, scope: Scope) -> SlotSnapshot | None:
        slot = self._slot_defs.get(slot_id)
        if slot is None:
            return None
        key = (slot_id, scope.key(slot.ttl))
        snapshot = self._values.get(key)
        if snapshot is not None and self._is_expired(snapshot):
            self._values.pop(key, None)
            return None
        return snapshot

    def set(
        self,
        slot_id: str,
        value: Any,
        *,
        scope: Scope,
        source: SourceRef,
        confidence: float,
        decay_at: datetime | None = None,
    ) -> None:
        slot = self._slot_defs.get(slot_id)
        owner = self._owners.get(slot_id)
        if slot is None or owner is None:
            raise StateSlotOwnershipError(f"state slot {slot_id!r} is not registered")
        if source.module_id != owner:
            raise StateSlotOwnershipError(
                f"module {source.module_id!r} cannot write slot {slot_id!r}; owner is {owner!r}"
            )
        if not source.evidence_path:
            raise ValueError("source.evidence_path is required")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        snapshot = SlotSnapshot(
            slot_id=slot_id,
            value=value,
            scope=scope,
            source=source,
            confidence=confidence,
            updated_at=datetime.now(),
            decay_at=decay_at,
        )
        self._values[(slot_id, scope.key(slot.ttl))] = snapshot

    def clear_per_turn(self, *, scope: Scope) -> None:
        keys_to_delete = []
        for key in self._values:
            slot_id, scope_key = key
            slot = self._slot_defs[slot_id]
            if slot.ttl == "per_turn" and scope_key == scope.key("per_turn"):
                keys_to_delete.append(key)
        for key in keys_to_delete:
            self._values.pop(key, None)

    def clear_expired(self) -> int:
        keys_to_delete = [key for key, snapshot in self._values.items() if self._is_expired(snapshot)]
        for key in keys_to_delete:
            self._values.pop(key, None)
        return len(keys_to_delete)

    def clear_stale_per_session(self, *, max_age: timedelta, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now()) - max_age
        keys_to_delete = []
        for key, snapshot in self._values.items():
            slot_id, _scope_key = key
            slot = self._slot_defs[slot_id]
            if slot.ttl == "per_session" and snapshot.updated_at <= cutoff:
                keys_to_delete.append(key)
        for key in keys_to_delete:
            self._values.pop(key, None)
        return len(keys_to_delete)

    def snapshot_all_for_trace(self) -> dict[str, dict[str, Any]]:
        self.clear_expired()
        return {
            f"{slot_id}:{index}": snapshot.to_dict()
            for index, ((slot_id, _scope_key), snapshot) in enumerate(sorted(self._values.items()))
        }

    @staticmethod
    def _is_expired(snapshot: SlotSnapshot) -> bool:
        return snapshot.decay_at is not None and snapshot.decay_at <= datetime.now()
