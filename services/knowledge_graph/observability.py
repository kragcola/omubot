"""Graph Population & Evidence-Quality Observability v1 (gpo_v1).

Pure read-side classification for graph health snapshots. Reuses provenance
semantics; never invents IDs, never emits raw evidence/quote/JSON/content.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from services.knowledge_graph.provenance import (
    GraphProvenanceError,
    is_derived_evidence_type,
    is_primary_evidence_mapping,
    normalize_graph_evidence,
)

GPO_VERSION = "gpo_v1"

EVIDENCE_TYPE_BUCKETS: tuple[str, ...] = (
    "memory_card",
    "doc_chunk",
    "message",
    "evidence",
    "fixture",
    "observation",
    "episode",
    "graph_fact",
    "other",
    "empty",
)

_KNOWN_TYPE_SET: frozenset[str] = frozenset(
    b for b in EVIDENCE_TYPE_BUCKETS if b not in {"other", "empty"}
)

ROW_QUALITY_BUCKETS: tuple[str, ...] = (
    "primary",
    "derived",
    "invalid",
    "missing",
)

FACT_SUPPORT_BUCKETS: tuple[str, ...] = (
    "primary",
    "derived_only",
    "invalid_only",
    "none",
)

PENDING_QUALITY_BUCKETS: tuple[str, ...] = (
    "primary",
    "derived",
    "invalid",
    "missing",
)

# Closed gpg_v1 codes + legacy missing_type. Unknown suffixes → "other".
KNOWN_PROVENANCE_GATE_CODES: frozenset[str] = frozenset(
    {
        "empty_evidence",
        "invalid_shape",
        "whitespace_id",
        "conflicting_ids",
        "conflicting_types",
        "missing_id",
        "invalid_type",
        "graph_fact_primary",
        "invalid_id_scalar",
        "missing_type",
    }
)

_GATE_PREFIX = "provenance_gate:"


def classify_evidence_mapping_quality(evidence: Any) -> str:
    """Classify one evidence mapping/row: primary | derived | invalid | missing."""
    if evidence is None:
        return "missing"
    if not isinstance(evidence, Mapping):
        return "invalid"
    if not evidence:
        return "missing"
    try:
        normalized = normalize_graph_evidence(evidence, allow_graph_fact=True)
    except GraphProvenanceError:
        return "invalid"
    etype = str(normalized.get("type") or "").strip()
    if is_derived_evidence_type(etype):
        return "derived"
    if is_primary_evidence_mapping(normalized):
        return "primary"
    return "invalid"


def classify_fact_support(rows: Sequence[Mapping[str, Any]] | None) -> str:
    """Aggregate evidence rows for one active fact.

    Priority: primary > derived > invalid > none.
    Labels: primary | derived_only | invalid_only | none.
    """
    if not rows:
        return "none"
    saw_derived = False
    saw_invalid = False
    for row in rows:
        if not isinstance(row, Mapping):
            saw_invalid = True
            continue
        quality = classify_evidence_mapping_quality(row)
        if quality == "primary":
            return "primary"
        if quality == "derived":
            saw_derived = True
        elif quality == "invalid":
            saw_invalid = True
        # missing row among multi-evidence does not upgrade support
    if saw_derived:
        return "derived_only"
    if saw_invalid:
        return "invalid_only"
    return "none"


def classify_evidence_type_bucket(evidence_type: str | None) -> str:
    """Map evidence type to a secret-free closed histogram bucket."""
    token = str(evidence_type or "").strip()
    if not token:
        return "empty"
    if token in _KNOWN_TYPE_SET:
        return token
    return "other"


def classify_pending_evidence_quality(evidence: Any) -> str:
    """Classify pending candidate evidence_json payload or mapping."""
    if evidence is None:
        return "missing"
    if isinstance(evidence, str):
        text = evidence.strip()
        if not text:
            return "missing"
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return "invalid"
        return classify_pending_evidence_quality(parsed)
    if isinstance(evidence, Mapping):
        if not evidence:
            return "missing"
        return classify_evidence_mapping_quality(evidence)
    return "invalid"


def classify_pending_gate_code(review_note: str | None) -> str | None:
    """Return closed gate code, ``other``, or None when not a gate note.

    Never returns arbitrary suffixes — unknown codes collapse to ``other``.
    """
    note = str(review_note or "").strip()
    if not note.startswith(_GATE_PREFIX):
        return None
    suffix = note[len(_GATE_PREFIX) :].strip()
    if not suffix:
        return "other"
    # Only the first path segment is the machine code (no detail leak).
    code = suffix.split(":", 1)[0].strip()
    if code in KNOWN_PROVENANCE_GATE_CODES:
        return code
    return "other"


def empty_zero_counts(keys: Sequence[str]) -> dict[str, int]:
    return {str(k): 0 for k in keys}


def empty_observability_payload(*, provenance_gate_enabled: bool) -> dict[str, Any]:
    """Secret-free zeroed nested observability block."""
    return {
        "version": GPO_VERSION,
        "enabled": True,
        "provenance_gate_enabled": bool(provenance_gate_enabled),
        "active_fact_support": empty_zero_counts(FACT_SUPPORT_BUCKETS),
        "active_evidence_rows": empty_zero_counts(("primary", "derived", "invalid")),
        "active_evidence_type_histogram": empty_zero_counts(EVIDENCE_TYPE_BUCKETS),
        "pending_evidence_quality": empty_zero_counts(PENDING_QUALITY_BUCKETS),
        "pending_gate_codes": empty_zero_counts(
            (*sorted(KNOWN_PROVENANCE_GATE_CODES), "other")
        ),
    }


def accumulate_active_evidence(
    payload: MutableMapping[str, Any],
    rows: Sequence[Mapping[str, Any]] | None,
) -> None:
    """Mutate observability counters for one fact's evidence rows."""
    support = classify_fact_support(rows)
    payload["active_fact_support"][support] = (
        int(payload["active_fact_support"].get(support, 0)) + 1
    )
    if not rows:
        return
    for row in rows:
        if not isinstance(row, Mapping):
            payload["active_evidence_rows"]["invalid"] = (
                int(payload["active_evidence_rows"].get("invalid", 0)) + 1
            )
            bucket = classify_evidence_type_bucket(None)
            payload["active_evidence_type_histogram"][bucket] = (
                int(payload["active_evidence_type_histogram"].get(bucket, 0)) + 1
            )
            continue
        quality = classify_evidence_mapping_quality(row)
        if quality == "missing":
            # empty mapping among multi-row is not a countable evidence row quality
            # for active_evidence_rows (only primary|derived|invalid).
            continue
        if quality in ("primary", "derived", "invalid"):
            payload["active_evidence_rows"][quality] = (
                int(payload["active_evidence_rows"].get(quality, 0)) + 1
            )
        etype = str(row.get("type") or "").strip()
        if not etype:
            # alias-only rows: derive bucket from normalize when possible
            try:
                norm = normalize_graph_evidence(row, allow_graph_fact=True)
                etype = str(norm.get("type") or "").strip()
            except GraphProvenanceError:
                etype = ""
        bucket = classify_evidence_type_bucket(etype)
        payload["active_evidence_type_histogram"][bucket] = (
            int(payload["active_evidence_type_histogram"].get(bucket, 0)) + 1
        )


def accumulate_pending_candidate(
    payload: MutableMapping[str, Any],
    *,
    evidence: Any,
    review_note: str | None,
) -> None:
    quality = classify_pending_evidence_quality(evidence)
    payload["pending_evidence_quality"][quality] = (
        int(payload["pending_evidence_quality"].get(quality, 0)) + 1
    )
    code = classify_pending_gate_code(review_note)
    if code is not None:
        payload["pending_gate_codes"][code] = (
            int(payload["pending_gate_codes"].get(code, 0)) + 1
        )
