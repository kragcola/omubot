"""Graph Provenance Gate v1 (gpg_v1) — pure evidence normalizer.

Normalizes write-side evidence for knowledge-graph facts/candidates.
Does not invent message/card/chunk IDs. ``graph_fact`` is never a valid
primary support type (it is derived/audit-only).

Primary contract (v1): a non-empty evidence type is **primary** unless it
is in the derived deny-set. v1 deny-set is ``graph_fact`` only. Legacy bare
non-empty id (no type) canonicalizes to ``type=evidence`` and is primary.
Alias types (memory_card/doc_chunk/message) and fixture remain valid;
future explicit types (observation, episode, …) need no allowlist update.

When the gate is disabled, callers must not use this module for enforcement;
legacy truthy acceptance remains in the store path and is intentionally unsafe.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

# Derived / audit-only types — never primary support for pack refs or supersede copy.
DERIVED_EVIDENCE_TYPES: frozenset[str] = frozenset({"graph_fact"})

# Canonical alias shapes (not an allowlist for primary).
_ALIAS_TYPE: dict[str, str] = {
    "card_id": "memory_card",
    "chunk_id": "doc_chunk",
    "message_id": "message",
}

_AUX_FIELDS = ("source", "scope", "scope_id")

# Conservative token for explicit types: lowercase start, then [a-z0-9_]* .
# Does not reject existing canonical types (memory_card, doc_chunk, graph_fact, fixture).
_TYPE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class GraphProvenanceError(ValueError):
    """Typed provenance rejection.

    ``code`` is a closed machine token suitable for review_note
    (``provenance_gate:<code>``) and structured logs. Never put raw
    evidence / query / content into the message for automated reject paths.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code or "invalid").strip() or "invalid"
        text = f"graph_provenance:{self.code}"
        if detail:
            text = f"{text}:{detail}"
        super().__init__(text)


def is_derived_evidence_type(evidence_type: str | None) -> bool:
    """True when type is audit/derived-only (v1: graph_fact only)."""
    return str(evidence_type or "").strip() in DERIVED_EVIDENCE_TYPES


def is_primary_evidence_type(evidence_type: str | None) -> bool:
    """True for any non-empty type that is not derived.

    Unified contract for supersede copy, ContextProvenance.evidence_refs,
    and graph_provenance_kind classification. Not an allowlist.
    """
    etype = str(evidence_type or "").strip()
    if not etype:
        return False
    return not is_derived_evidence_type(etype)


def evidence_id_from_mapping(evidence: Mapping[str, Any]) -> str:
    """Strip first non-empty id from id / card_id / chunk_id / message_id."""
    for key in ("id", "card_id", "chunk_id", "message_id"):
        val = _strip_id(evidence.get(key))
        if val:
            return val
    return ""


def evidence_type_from_mapping(evidence: Mapping[str, Any]) -> str:
    """Return explicit type, or implied alias type, or ``evidence`` for bare id.

    Matches write-side normalize: bare non-empty id (no type/alias) is the
    legacy unknown-type anchor ``type=evidence``.
    """
    explicit = _strip_str(evidence.get("type"))
    if explicit:
        return explicit
    for field, implied in _ALIAS_TYPE.items():
        if _strip_id(evidence.get(field)):
            return implied
    if evidence_id_from_mapping(evidence):
        return "evidence"
    return ""


def is_primary_evidence_mapping(evidence: Mapping[str, Any] | None) -> bool:
    """True when mapping has a primary type and non-empty id (any alias).

    Bare non-empty id (no type) is primary via type=evidence compatibility.
    """
    if not isinstance(evidence, Mapping):
        return False
    etype = evidence_type_from_mapping(evidence)
    eid = evidence_id_from_mapping(evidence)
    return bool(eid) and is_primary_evidence_type(etype)


def normalize_graph_evidence(
    evidence: Mapping[str, Any] | None,
    *,
    allow_graph_fact: bool = False,
) -> dict[str, Any]:
    """Return a stripped canonical evidence dict or raise GraphProvenanceError.

    Canonical shapes:
    - card_id → type=memory_card, id=card_id, card_id=…
    - chunk_id → type=doc_chunk, id=chunk_id, chunk_id=…
    - message_id → type=message, id=message_id, message_id=…
    - bare non-empty generic id (no type) → type=evidence, id=… (legacy
      unknown-type provenance; primary after normalization)
    - explicit type + generic id → type/id only (no synthesized alias fields)

    Rejects missing IDs, whitespace-only IDs, boolean/non-finite ID scalars,
    conflicting alias IDs/types, invalid type tokens, and graph_fact as sole
    primary support (unless ``allow_graph_fact=True``, reserved for
    non-primary audit paths).

    Alias fields (``card_id`` / ``chunk_id`` / ``message_id``) are emitted
    only when the input actually used that alias. Generic ``type``+``id``
    (e.g. ``doc_chunk`` + ``fallback_id``) stays type/id-only so listeners
    that require an explicit alias (FactGraphBridge) remain no-ops.
    """
    if evidence is None:
        raise GraphProvenanceError("empty_evidence")
    if not isinstance(evidence, Mapping):
        raise GraphProvenanceError("invalid_shape")
    raw = dict(evidence)
    if not raw:
        raise GraphProvenanceError("empty_evidence")

    explicit_type = _strip_str(raw.get("type"))
    id_raw = raw.get("id")
    _reject_invalid_id_scalar(id_raw, field="id")
    id_present = id_raw is not None and str(id_raw) != ""
    id_value = _strip_id(id_raw) if id_present else ""

    alias_hits: list[tuple[str, str, str]] = []
    for field, implied in _ALIAS_TYPE.items():
        if field not in raw or raw[field] is None:
            continue
        _reject_invalid_id_scalar(raw[field], field=field)
        val = _strip_id(raw.get(field))
        if not val:
            # Present but empty/whitespace — treat as invalid id for that alias
            raise GraphProvenanceError("whitespace_id", field)
        alias_hits.append((field, implied, val))

    if len(alias_hits) > 1:
        raise GraphProvenanceError("conflicting_ids", "multiple_aliases")

    used_alias_field: str | None = None
    if alias_hits:
        alias_field, implied_type, alias_id = alias_hits[0]
        if explicit_type and explicit_type != implied_type:
            raise GraphProvenanceError("conflicting_types")
        if id_present:
            if not id_value:
                raise GraphProvenanceError("whitespace_id", "id")
            if id_value != alias_id:
                raise GraphProvenanceError("conflicting_ids")
        evidence_type = explicit_type or implied_type
        evidence_id = alias_id
        used_alias_field = alias_field
    else:
        if id_present and not id_value:
            raise GraphProvenanceError("whitespace_id", "id")
        if not id_value:
            raise GraphProvenanceError("missing_id")
        # Legacy bare id: honest unknown-type provenance → type=evidence.
        # Pre-gpg reverse lookup stored evidence_id only; type empty read as
        # evidence:<id>. Whitespace-only ids already rejected above.
        if not explicit_type:
            evidence_type = "evidence"
            evidence_id = id_value
        else:
            evidence_type = explicit_type
            evidence_id = id_value

    if not _TYPE_TOKEN_RE.match(evidence_type):
        raise GraphProvenanceError("invalid_type", evidence_type)

    if is_derived_evidence_type(evidence_type) and not allow_graph_fact:
        raise GraphProvenanceError("graph_fact_primary")

    out: dict[str, Any] = {
        "type": evidence_type,
        "id": evidence_id,
    }
    # Only re-emit alias fields that the caller actually supplied. Do not
    # synthesize chunk_id/card_id/message_id from type alone.
    if used_alias_field:
        out[used_alias_field] = evidence_id

    quote = _strip_str(raw.get("quote"))
    if quote:
        out["quote"] = quote

    for key in _AUX_FIELDS:
        if key in raw and raw[key] is not None:
            val = _strip_str(raw.get(key))
            if val:
                out[key] = val

    # Preserve supersedes_fact_id when present (admin supersede chain metadata).
    if "supersedes_fact_id" in raw and raw["supersedes_fact_id"] is not None:
        sid = _strip_str(raw.get("supersedes_fact_id"))
        if sid:
            out["supersedes_fact_id"] = sid

    return out


def primary_evidence_from_rows(
    rows: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any] | None:
    """Pick the first primary-typed evidence row and normalize it.

    Returns None when no primary support exists (empty, whitespace, or
    graph_fact-derived only). Does not fabricate graph_fact:self.
    """
    if not rows:
        return None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if not is_primary_evidence_mapping(row):
            continue
        try:
            # Re-normalize full row so bare id / alias shapes are canonical.
            payload = dict(row)
            quote = _strip_str(row.get("quote"))
            if quote:
                payload["quote"] = quote
            return normalize_graph_evidence(payload)
        except GraphProvenanceError:
            continue
    return None


def _strip_str(value: Any) -> str:
    return str(value or "").strip()


def _strip_id(value: Any) -> str:
    """Return a safe ID string; malformed scalars are non-anchors on reads."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return _strip_str(value)


def _reject_invalid_id_scalar(value: Any, *, field: str) -> None:
    """Fail writes before bool/non-finite scalars become string evidence IDs."""
    if isinstance(value, bool) or (
        isinstance(value, float) and not math.isfinite(value)
    ):
        raise GraphProvenanceError("invalid_id_scalar", field)
