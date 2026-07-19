"""Typed references connecting episodes to other memory records."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from services.memory.entity_identity import parse_entity_key

LinkedRefKind = Literal[
    "entity",
    "card",
    "fact",
    "message_pk",
    "message",
    "episode",
    "legacy",
]

_LINKED_REF_KINDS: frozenset[str] = frozenset(
    {"entity", "card", "fact", "message_pk", "message", "episode", "legacy"}
)


@dataclass(frozen=True, slots=True)
class LinkedMemoryRef:
    kind: LinkedRefKind
    target_id: str
    canonical: str


def make_linked_ref(*, kind: str, target_id: Any) -> LinkedMemoryRef:
    clean_kind = str(kind or "").strip().lower()
    if clean_kind not in _LINKED_REF_KINDS:
        raise ValueError(f"unsupported linked ref kind: {kind!r}")

    if clean_kind == "message_pk":
        if isinstance(target_id, bool):
            raise TypeError("message_pk target must be a positive integer")
        raw_target = str(target_id).strip()
        if not raw_target.isdigit() or int(raw_target) <= 0:
            raise ValueError("message_pk target must be a positive integer")
        clean_target = str(int(raw_target))
    else:
        clean_target = str(target_id or "").strip()
        if not clean_target:
            raise ValueError(f"{clean_kind} target must not be empty")

    if clean_kind == "entity":
        parsed_entity = parse_entity_key(clean_target)
        if parsed_entity is None:
            raise ValueError(f"invalid entity key: {clean_target!r}")
        clean_target = parsed_entity.entity_key

    return LinkedMemoryRef(
        kind=clean_kind,  # type: ignore[arg-type]
        target_id=clean_target,
        canonical=f"{clean_kind}:{clean_target}",
    )


def parse_linked_ref(value: object) -> LinkedMemoryRef | None:
    kind = ""
    target: Any = ""
    if isinstance(value, LinkedMemoryRef):
        kind = value.kind
        target = value.target_id
    elif isinstance(value, dict):
        kind = str(value.get("type") or "").strip().lower()
        target = value.get("id")
    elif isinstance(value, str):
        clean_value = value.strip()
        if not clean_value:
            return None
        if ":" not in clean_value:
            kind = "legacy"
            target = clean_value
        else:
            kind, target = clean_value.split(":", 1)
            kind = kind.strip().lower()
    else:
        return None

    try:
        return make_linked_ref(kind=kind, target_id=target)
    except (TypeError, ValueError):
        return None


def normalize_linked_refs(
    values: object,
    *,
    preserve_legacy: bool = True,
) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in _iter_values(values):
        ref = parse_linked_ref(value)
        if ref is None or ref.canonical in seen:
            continue
        seen.add(ref.canonical)
        storage_value = ref.canonical
        if (
            preserve_legacy
            and ref.kind == "legacy"
            and isinstance(value, str)
            and ":" not in value.strip()
        ):
            storage_value = ref.target_id
        result.append(storage_value)
    return tuple(result)


def linked_ref_evidence(values: object) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in _iter_values(values):
        ref = parse_linked_ref(value)
        if ref is None or ref.canonical in seen:
            continue
        seen.add(ref.canonical)
        result.append(ref.canonical)
    return tuple(result)


def _iter_values(values: object) -> Iterable[object]:
    if isinstance(values, str | dict):
        return (values,)
    if isinstance(values, Iterable):
        return values
    return ()
