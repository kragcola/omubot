"""Stable entity identity contract shared by memory surfaces."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from services.similarity import normalize_text_key

EntityKind = Literal["user", "group", "concept"]

_PLATFORM_USER_RE = re.compile(r"^用户\s*(?P<platform_id>\d+)$")
_PLATFORM_GROUP_RE = re.compile(r"^群(?:聊)?\s*(?P<platform_id>\d+)$")


@dataclass(frozen=True, slots=True)
class EntityRef:
    kind: EntityKind
    scope: str
    scope_id: str
    entity_key: str
    display: str = ""
    aliases: tuple[str, ...] = ()
    storage_refs: tuple[str, ...] = ()


def make_entity_ref(
    *,
    kind: EntityKind,
    scope: str,
    scope_id: str,
    display: str,
    platform_id: str | None = None,
    aliases: tuple[str, ...] | list[str] = (),
    storage_refs: tuple[str, ...] | list[str] = (),
) -> EntityRef:
    clean_scope = str(scope or "").strip().lower()
    clean_scope_id = str(scope_id or "").strip()
    clean_display = str(display or "").strip()
    clean_platform_id = str(platform_id or "").strip()

    if kind in {"user", "group"}:
        if not clean_platform_id or not clean_platform_id.isdigit():
            raise ValueError(f"{kind} entity requires a numeric platform_id")
        canonical_platform_id = str(int(clean_platform_id))
        entity_key = f"{kind}:qq:{canonical_platform_id}"
    elif kind == "concept":
        normalized = normalize_text_key(clean_display)
        if not clean_scope or not clean_scope_id or not clean_display:
            raise ValueError("concept entity requires scope, scope_id and display")
        if not normalized:
            raw_display = unicodedata.normalize("NFKC", clean_display).strip().lower()
            digest = hashlib.sha256(raw_display.encode("utf-8")).hexdigest()[:16]
            normalized = f"raw-{digest}"
        entity_key = f"concept:{clean_scope}:{clean_scope_id}:{normalized}"
    else:
        raise ValueError(f"unsupported entity kind: {kind}")

    return EntityRef(
        kind=kind,
        scope=clean_scope,
        scope_id=clean_scope_id,
        entity_key=entity_key,
        display=clean_display,
        aliases=_dedupe(aliases),
        storage_refs=_dedupe(storage_refs),
    )


def entity_ref_from_surface(*, subject: str, scope: str, scope_id: str) -> EntityRef:
    clean_subject = str(subject or "").strip()
    platform_match = _PLATFORM_USER_RE.fullmatch(clean_subject)
    if platform_match is not None:
        return make_entity_ref(
            kind="user",
            scope=scope,
            scope_id=scope_id,
            display=clean_subject,
            platform_id=platform_match.group("platform_id"),
        )
    group_match = _PLATFORM_GROUP_RE.fullmatch(clean_subject)
    if group_match is not None:
        return make_entity_ref(
            kind="group",
            scope=scope,
            scope_id=scope_id,
            display=clean_subject,
            platform_id=group_match.group("platform_id"),
        )
    return make_entity_ref(
        kind="concept",
        scope=scope,
        scope_id=scope_id,
        display=clean_subject,
    )


def parse_entity_key(value: str) -> EntityRef | None:
    clean_value = str(value or "").strip()
    platform_parts = clean_value.split(":")
    if (
        len(platform_parts) == 3
        and platform_parts[0] in {"user", "group"}
        and platform_parts[1] == "qq"
        and platform_parts[2].isdigit()
    ):
        kind: EntityKind = "user" if platform_parts[0] == "user" else "group"
        return make_entity_ref(
            kind=kind,
            scope=kind,
            scope_id=platform_parts[2],
            display="",
            platform_id=platform_parts[2],
        )

    concept_parts = clean_value.split(":", 3)
    if (
        len(concept_parts) == 4
        and concept_parts[0] == "concept"
        and concept_parts[1] in {"user", "group", "global"}
        and concept_parts[2]
        and concept_parts[3]
        and normalize_text_key(concept_parts[3]) == concept_parts[3]
    ):
        return make_entity_ref(
            kind="concept",
            scope=concept_parts[1],
            scope_id=concept_parts[2],
            display=concept_parts[3],
        )
    return None


def _dedupe(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)
