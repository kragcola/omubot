"""Typed candidate dataclasses + literals for the MemoryConsolidator dry-run.

5 candidate domains map 1:1 to the multilayer-memory plan § 5 Phase C
output schema. Promotion to production stores is intentionally out of
scope here — see ``.claude/handoff/TASK-20260521-03-memory-consolidator-dryrun.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CandidateDomain = Literal["fact", "slang", "style", "episode", "graph_relation"]
CandidateState = Literal["dry_run", "queued", "approved", "rejected"]
CandidateScope = Literal["group", "user", "global"]
RunStatus = Literal["running", "done", "failed"]

CANDIDATE_DOMAINS: tuple[str, ...] = (
    "fact",
    "slang",
    "style",
    "episode",
    "graph_relation",
)
CANDIDATE_STATES: tuple[str, ...] = (
    "dry_run",
    "queued",
    "approved",
    "rejected",
)
CANDIDATE_SCOPES: tuple[str, ...] = ("group", "user", "global")
VALID_DECISION_TRANSITIONS: dict[str, set[str]] = {
    "dry_run": {"queued", "approved", "rejected"},
}


@dataclass(slots=True)
class Candidate:
    """A typed candidate produced by the consolidator dry-run."""

    candidate_id: str
    run_id: str
    domain: CandidateDomain
    scope: CandidateScope
    group_id: str
    source_message_pks: list[int]
    payload: dict[str, Any]
    confidence: float
    state: CandidateState
    decision_reason: str
    decided_by: str
    decided_at: float
    normalizer_cluster_id: str
    created_at: float


@dataclass(slots=True)
class ScanRun:
    """A single dry-run pass over the conversation archive."""

    run_id: str
    triggered_by: str
    group_id: str
    scope: CandidateScope
    started_at: float
    finished_at: float
    status: RunStatus
    scanned_count: int
    candidates_count: int
    error_text: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunReport:
    """Summary returned by ``MemoryConsolidator.run_once``."""

    run_id: str
    scanned: int
    candidates: int
    status: RunStatus
    error_text: str = ""


@dataclass(slots=True)
class FactPayload:
    subject: str
    predicate: str
    object: str
    evidence_quotes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SlangPayload:
    term: str
    meaning: str
    aliases: list[str] = field(default_factory=list)
    repeat_policy: str = "understand_only"


@dataclass(slots=True)
class StylePayload:
    expression: str
    situation: str
    outcome_signal: str = ""


@dataclass(slots=True)
class EpisodePayload:
    situation: str
    observed_context: str = ""
    action_taken: str = ""
    outcome_signal: str = ""
    reflection: str = ""


@dataclass(slots=True)
class GraphRelationPayload:
    subject_node: str
    predicate: str
    object_node: str
    edge_type: str = "fact"


_PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    "fact": ("subject", "predicate", "object", "evidence_quotes"),
    "slang": ("term", "meaning", "aliases", "repeat_policy"),
    "style": ("expression", "situation", "outcome_signal"),
    "episode": (
        "situation",
        "observed_context",
        "action_taken",
        "outcome_signal",
        "reflection",
    ),
    "graph_relation": (
        "subject_node",
        "predicate",
        "object_node",
        "edge_type",
    ),
}


def normalize_payload(domain: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and project a raw payload onto the typed schema for ``domain``.

    Unknown fields are dropped silently; required fields default to empty
    strings / empty lists. Returns a brand-new dict — caller-owned dicts
    are never mutated.
    """
    fields = _PAYLOAD_FIELDS.get(domain)
    if fields is None:
        raise ValueError(f"unknown candidate domain: {domain!r}")
    projected: dict[str, Any] = {}
    for key in fields:
        value = payload.get(key)
        if key in {"evidence_quotes", "aliases"}:
            if isinstance(value, list):
                projected[key] = [str(item) for item in value if str(item).strip()]
            elif isinstance(value, str) and value.strip():
                projected[key] = [value.strip()]
            else:
                projected[key] = []
        elif value is None:
            projected[key] = ""
        else:
            projected[key] = str(value)
    return projected


def derive_raw_text(domain: str, payload: dict[str, Any]) -> str:
    """Pick the human-facing handle field used by normalizer attach.

    Each domain has a distinct anchor field: slang uses ``term``, style
    uses ``expression``, episode/graph_relation use the most specific
    one. Falls back to a stable join if the anchor field is empty so
    ``LearningNormalizerStore.normalize_key`` sees a non-empty value.
    """
    if domain == "slang":
        anchor = str(payload.get("term", "") or "").strip()
    elif domain == "style":
        anchor = str(payload.get("expression", "") or "").strip()
    elif domain == "fact":
        anchor = " ".join(
            str(payload.get(k, "") or "").strip()
            for k in ("subject", "predicate", "object")
        ).strip()
    elif domain == "graph_relation":
        anchor = " ".join(
            str(payload.get(k, "") or "").strip()
            for k in ("subject_node", "predicate", "object_node")
        ).strip()
    elif domain == "episode":
        anchor = str(payload.get("situation", "") or "").strip()
    else:
        anchor = ""
    if not anchor:
        anchor = " ".join(
            str(v).strip() for v in payload.values() if str(v).strip()
        )
    return anchor or domain
