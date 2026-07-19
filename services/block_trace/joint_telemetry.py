"""Joint Dual-Path Memory Telemetry v1 (jdt_v1).

Pure classification/aggregation over existing prompt_block_traces rows.
Secret-free closed payload; no ranking/search/prompt behavior.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

JDT_VERSION: Final[str] = "jdt_v1"

# Closed source -> role mapping. Unknown sources are ignored.
SOURCE_TO_ROLE: Final[dict[str, str]] = {
    "context": "context_main",
    "context_temporal_trace": "context_temporal_trace",
    "context_evidence_use": "context_constrained",
    "episode": "episode",
}

JOINT_ROLES: Final[tuple[str, ...]] = (
    "context_main",
    "context_temporal_trace",
    "context_constrained",
    "episode",
)

CONTEXT_ROLES: Final[frozenset[str]] = frozenset(
    ("context_main", "context_temporal_trace", "context_constrained")
)

CLOSED_DECISIONS: Final[tuple[str, ...]] = ("accepted", "trimmed", "rejected")
SURVIVED_DECISIONS: Final[frozenset[str]] = frozenset(("accepted", "trimmed"))

OUTCOME_FLAGS: Final[tuple[str, ...]] = (
    "both_present",
    "both_survived",
    "context_only_survived",
    "episode_only_survived",
    "neither_survived",
    "context_main_dropped_episode_survived",
    "context_main_absent_episode_survived",
    "constrained_and_episode_survived",
)

RELEVANT_SOURCES: Final[frozenset[str]] = frozenset(SOURCE_TO_ROLE.keys())

# Store/admin limit clamp (closed safe range).
LIMIT_MIN: Final[int] = 1
LIMIT_MAX: Final[int] = 200
LIMIT_DEFAULT: Final[int] = 50

# Recent item closed top-level keys (plus nested closed maps).
RECENT_ITEM_KEYS: Final[frozenset[str]] = frozenset(
    ("decision_totals", "outcomes")
)


def empty_decision_totals() -> dict[str, dict[str, int]]:
    return {
        role: {d: 0 for d in CLOSED_DECISIONS}
        for role in JOINT_ROLES
    }


def empty_outcome_counts() -> dict[str, int]:
    return {flag: 0 for flag in OUTCOME_FLAGS}


def disabled_snapshot() -> dict[str, Any]:
    """Exact disabled zero shape (kill-switch / no SELECT)."""
    return {
        "version": JDT_VERSION,
        "enabled": False,
        "sample_size": 0,
        "decision_totals": empty_decision_totals(),
        "outcome_counts": empty_outcome_counts(),
        "recent": [],
    }


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return LIMIT_DEFAULT
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return LIMIT_DEFAULT
    if value < LIMIT_MIN:
        return LIMIT_MIN
    if value > LIMIT_MAX:
        return LIMIT_MAX
    return value


def role_for_source(source: object) -> str | None:
    if not isinstance(source, str):
        return None
    return SOURCE_TO_ROLE.get(source)


def _is_survived(decision: object) -> bool:
    return isinstance(decision, str) and decision in SURVIVED_DECISIONS


def _is_closed_decision(decision: object) -> bool:
    return isinstance(decision, str) and decision in CLOSED_DECISIONS


def classify_request_traces(
    traces: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate one request's relevant traces into closed counters + outcomes.

    Unknown sources and unknown decisions are ignored (fail-closed).
    Malformed rows (non-mapping) are skipped.
    """
    decision_totals = empty_decision_totals()
    role_present = {role: False for role in JOINT_ROLES}
    role_survived = {role: False for role in JOINT_ROLES}

    for raw in traces:
        if not isinstance(raw, Mapping):
            continue
        role = role_for_source(raw.get("source"))
        if role is None:
            continue
        decision = raw.get("decision")
        if not _is_closed_decision(decision):
            continue
        role_present[role] = True
        # decision is closed str after check
        decision_s = str(decision)
        decision_totals[role][decision_s] += 1
        if _is_survived(decision_s):
            role_survived[role] = True

    context_present = any(role_present[r] for r in CONTEXT_ROLES)
    context_survived = any(role_survived[r] for r in CONTEXT_ROLES)
    episode_present = role_present["episode"]
    episode_survived = role_survived["episode"]
    main_present = role_present["context_main"]
    main_survived = role_survived["context_main"]
    constrained_survived = role_survived["context_constrained"]

    outcomes = {
        "both_present": bool(context_present and episode_present),
        "both_survived": bool(context_survived and episode_survived),
        "context_only_survived": bool(context_survived and not episode_survived),
        "episode_only_survived": bool(episode_survived and not context_survived),
        "neither_survived": bool(not context_survived and not episode_survived),
        "context_main_dropped_episode_survived": bool(
            main_present and not main_survived and episode_survived
        ),
        "context_main_absent_episode_survived": bool(
            (not main_present) and episode_survived
        ),
        "constrained_and_episode_survived": bool(
            constrained_survived and episode_survived
        ),
    }

    return {
        "decision_totals": decision_totals,
        "outcomes": outcomes,
    }


def aggregate_joint_snapshot(
    *,
    request_groups: Sequence[tuple[str, Sequence[Mapping[str, Any]]]],
    enabled: bool = True,
) -> dict[str, Any]:
    """Build the closed public jdt_v1 payload from ordered request groups.

    ``request_groups`` is newest-first: ``(request_id, traces)``. The request
    id is used only for internal grouping and ordering; it is never exposed
    because runtime ids embed group/private session identifiers.
    """
    if not enabled:
        return disabled_snapshot()

    decision_totals = empty_decision_totals()
    outcome_counts = empty_outcome_counts()
    recent: list[dict[str, Any]] = []

    for _request_id, traces in request_groups:
        classified = classify_request_traces(traces)
        item_totals = classified["decision_totals"]
        item_outcomes = classified["outcomes"]
        if not any(
            item_totals[role][decision]
            for role in JOINT_ROLES
            for decision in CLOSED_DECISIONS
        ):
            continue

        for role in JOINT_ROLES:
            for decision in CLOSED_DECISIONS:
                decision_totals[role][decision] += item_totals[role][decision]
        for flag in OUTCOME_FLAGS:
            if item_outcomes.get(flag):
                outcome_counts[flag] += 1

        recent.append(
            {
                "decision_totals": {
                    role: dict(item_totals[role]) for role in JOINT_ROLES
                },
                "outcomes": {flag: bool(item_outcomes[flag]) for flag in OUTCOME_FLAGS},
            }
        )

    return {
        "version": JDT_VERSION,
        "enabled": True,
        "sample_size": len(recent),
        "decision_totals": decision_totals,
        "outcome_counts": outcome_counts,
        "recent": recent,
    }


def group_rows_by_request(
    rows: Sequence[Mapping[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group SQL rows (newest-request-first) into (request_id, traces).

    Preserves first-seen request order (SQL must already order newest first).
    Only closed role sources are kept; extra keys on row dicts are dropped
    before classification to reduce accidental leakage in pure aggregation.
    """
    order: list[str] = []
    buckets: dict[str, list[dict[str, Any]]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        rid = raw.get("request_id")
        if not isinstance(rid, str) or not rid:
            continue
        source = raw.get("source")
        if role_for_source(source) is None:
            continue
        decision = raw.get("decision")
        if rid not in buckets:
            order.append(rid)
            buckets[rid] = []
        # Closed projection only — never pass through open columns.
        buckets[rid].append(
            {
                "source": source if isinstance(source, str) else "",
                "decision": decision if isinstance(decision, str) else "",
            }
        )
    return [(rid, buckets[rid]) for rid in order]
