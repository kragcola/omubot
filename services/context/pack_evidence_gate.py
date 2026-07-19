"""Pack-Time Confidence/Evidence Gate v1 (peg_v1).

Evidence-aware tiering applied after ContextService.search() (RRF + type caps
+ top_k) and before pack_context_hits(). Pure / sync / secret-free metrics.

Never uses post-RRF ContextHit.score as confidence (fusion rank only).
Does not mutate input hits, stores, schema, RRF weights, or TemporalTrace.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal

from services.context.types import ContextHit

GATE_VERSION: Final[str] = "peg_v1"

GateAction = Literal["keep", "demote", "omit"]

# Trusted Card sources that remain legitimate without message / evidence refs.
TRUSTED_EMPTY_EVIDENCE_SOURCES: Final[frozenset[str]] = frozenset(
    {
        "manual",
        "user_config",
        "migration",
        "food_plugin",
    }
)

_DEFAULT_MEMORY_SOFT: Final[float] = 0.45
_DEFAULT_GRAPH_SOFT: Final[float] = 0.60

# Closed reason codes for secret-free metrics (no query/content/ids).
REASON_DISABLED: Final[str] = "disabled"
REASON_IDENTITY: Final[str] = "identity"
REASON_KEEP_HINT: Final[str] = "gate_keep_hint"
REASON_KEEP_TRUSTED_EMPTY: Final[str] = "gate_keep_trusted_empty_evidence"
REASON_KEEP_EVIDENCE: Final[str] = "gate_keep_evidence"
REASON_KEEP_DEFAULT: Final[str] = "gate_keep_default"
REASON_KEEP_DOC: Final[str] = "gate_keep_doc"
REASON_DEMOTE_MEMORY_WEAK: Final[str] = "gate_demote_memory_weak_empty"
REASON_DEMOTE_GRAPH_WEAK: Final[str] = "gate_demote_graph_weak_empty"
REASON_OMIT_NON_ACTIVE: Final[str] = "gate_omit_non_active"
REASON_OMIT_NON_FINITE: Final[str] = "gate_omit_non_finite_confidence"
REASON_OMIT_BLANK_DOC: Final[str] = "gate_omit_blank_doc"
REASON_OMIT_GRAPH_WEAK_HOP: Final[str] = "gate_omit_graph_weak_uncorroborated"
REASON_OMIT_UNKNOWN_TYPE: Final[str] = "gate_omit_unknown_type"

_ALLOWED_REASONS: Final[frozenset[str]] = frozenset(
    {
        REASON_DISABLED,
        REASON_IDENTITY,
        REASON_KEEP_HINT,
        REASON_KEEP_TRUSTED_EMPTY,
        REASON_KEEP_EVIDENCE,
        REASON_KEEP_DEFAULT,
        REASON_KEEP_DOC,
        REASON_DEMOTE_MEMORY_WEAK,
        REASON_DEMOTE_GRAPH_WEAK,
        REASON_OMIT_NON_ACTIVE,
        REASON_OMIT_NON_FINITE,
        REASON_OMIT_BLANK_DOC,
        REASON_OMIT_GRAPH_WEAK_HOP,
        REASON_OMIT_UNKNOWN_TYPE,
    }
)

_ALLOWED_ACTIONS: Final[frozenset[str]] = frozenset({"keep", "demote", "omit"})


def _clamp_unit(value: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    return max(0.0, min(1.0, v))


@dataclass(frozen=True, slots=True)
class PackEvidenceGatePolicy:
    """Immutable pack-time evidence gate policy."""

    enabled: bool = True
    memory_soft_confidence: float = _DEFAULT_MEMORY_SOFT
    graph_soft_confidence: float = _DEFAULT_GRAPH_SOFT

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "memory_soft_confidence",
            _clamp_unit(self.memory_soft_confidence, _DEFAULT_MEMORY_SOFT),
        )
        object.__setattr__(
            self,
            "graph_soft_confidence",
            _clamp_unit(self.graph_soft_confidence, _DEFAULT_GRAPH_SOFT),
        )


DEFAULT_PACK_EVIDENCE_GATE_POLICY = PackEvidenceGatePolicy()


@dataclass(frozen=True, slots=True)
class PackEvidenceGateResult:
    """Gated hit list plus closed metrics. Hits: kept (orig order) then demoted."""

    hits: tuple[ContextHit, ...]
    omitted_count: int
    metrics: Mapping[str, Any]


def is_memory_hint_hit(hit: ContextHit) -> bool:
    """Synthetic RetrievalGate minimal-hint card — always keep, never a trace seed."""
    if str(getattr(hit, "retriever", "") or "") == "card_store_hint":
        return True
    hid = str(getattr(hit, "id", "") or "")
    return hid.startswith("memory_hint:")


def extract_trace_seed_ids(hits: Sequence[ContextHit]) -> tuple[str, ...]:
    """Pre-gate real memory_card ids for TemporalTrace (exclude synthetic hints)."""
    out: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        if getattr(hit, "type", "") != "memory_card":
            continue
        if is_memory_hint_hit(hit):
            continue
        hid = str(getattr(hit, "id", "") or "").strip()
        if not hid or hid in seen:
            continue
        seen.add(hid)
        out.append(hid)
    return tuple(out)


def apply_pack_evidence_gate(
    hits: Sequence[ContextHit],
    *,
    policy: PackEvidenceGatePolicy | None = None,
) -> PackEvidenceGateResult:
    """Tier hits for packing. Disabled path is strict identity (same objects/order)."""
    pol = policy if policy is not None else DEFAULT_PACK_EVIDENCE_GATE_POLICY
    if not pol.enabled:
        metrics = _metrics(
            enabled=False,
            identity=True,
            actions={"keep": len(hits), "demote": 0, "omit": 0},
            reasons={REASON_DISABLED: len(hits)} if hits else {},
        )
        # Return the same sequence order without copying objects; list→tuple is fine.
        return PackEvidenceGateResult(
            hits=tuple(hits),
            omitted_count=0,
            metrics=metrics,
        )

    kept: list[ContextHit] = []
    demoted: list[ContextHit] = []
    omitted = 0
    actions: dict[str, int] = {"keep": 0, "demote": 0, "omit": 0}
    reasons: dict[str, int] = {}

    for hit in hits:
        action, reason = _classify(hit, pol)
        actions[action] = actions.get(action, 0) + 1
        reasons[reason] = reasons.get(reason, 0) + 1
        if action == "keep":
            kept.append(hit)
        elif action == "demote":
            demoted.append(hit)
        else:
            omitted += 1

    ordered = tuple(kept + demoted)
    return PackEvidenceGateResult(
        hits=ordered,
        omitted_count=omitted,
        metrics=_metrics(
            enabled=True,
            identity=False,
            actions=actions,
            reasons=reasons,
        ),
    )


def sanitize_pack_evidence_gate_metrics(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Closed secret-free metrics dict for recent-row attachment."""
    if not raw:
        return {
            "version": GATE_VERSION,
            "enabled": False,
            "identity": True,
            "actions": {"keep": 0, "demote": 0, "omit": 0},
            "reasons": {},
        }
    enabled = bool(raw.get("enabled", False))
    identity = bool(raw.get("identity", not enabled))
    actions_raw = raw.get("actions") if isinstance(raw.get("actions"), Mapping) else {}
    reasons_raw = raw.get("reasons") if isinstance(raw.get("reasons"), Mapping) else {}
    actions: dict[str, int] = {"keep": 0, "demote": 0, "omit": 0}
    for key in _ALLOWED_ACTIONS:
        try:
            actions[key] = max(0, int(actions_raw.get(key, 0) or 0))  # type: ignore[union-attr]
        except (TypeError, ValueError):
            actions[key] = 0
    reasons: dict[str, int] = {}
    for key, val in reasons_raw.items():  # type: ignore[union-attr]
        sk = str(key)
        if sk not in _ALLOWED_REASONS:
            continue
        try:
            reasons[sk] = max(0, int(val or 0))
        except (TypeError, ValueError):
            continue
    version = str(raw.get("version") or GATE_VERSION)
    if version != GATE_VERSION:
        version = GATE_VERSION
    return {
        "version": version,
        "enabled": enabled,
        "identity": identity,
        "actions": actions,
        "reasons": reasons,
    }


def policy_from_mapping(raw: Mapping[str, Any] | PackEvidenceGatePolicy | None) -> PackEvidenceGatePolicy:
    if isinstance(raw, PackEvidenceGatePolicy):
        return raw
    if not raw:
        return DEFAULT_PACK_EVIDENCE_GATE_POLICY
    raw_enabled = raw.get("enabled", True)
    # Raw mappings are not the production Pydantic ingress. Keep malformed
    # values fail-safe instead of silently treating 0/"false" as a disable.
    enabled = raw_enabled if isinstance(raw_enabled, bool) else True
    mem = raw.get("memory_soft_confidence", _DEFAULT_MEMORY_SOFT)
    graph = raw.get("graph_soft_confidence", _DEFAULT_GRAPH_SOFT)
    return PackEvidenceGatePolicy(
        enabled=enabled,
        memory_soft_confidence=_clamp_unit(mem, _DEFAULT_MEMORY_SOFT),  # type: ignore[arg-type]
        graph_soft_confidence=_clamp_unit(graph, _DEFAULT_GRAPH_SOFT),  # type: ignore[arg-type]
    )


def _metrics(
    *,
    enabled: bool,
    identity: bool,
    actions: Mapping[str, int],
    reasons: Mapping[str, int],
) -> Mapping[str, Any]:
    return MappingProxyType(
        sanitize_pack_evidence_gate_metrics(
            {
                "version": GATE_VERSION,
                "enabled": enabled,
                "identity": identity,
                "actions": dict(actions),
                "reasons": dict(reasons),
            }
        )
    )


def _classify(hit: ContextHit, policy: PackEvidenceGatePolicy) -> tuple[GateAction, str]:
    hit_type = str(getattr(hit, "type", "") or "")
    if hit_type == "memory_card":
        return _classify_memory(hit, policy)
    if hit_type == "doc_chunk":
        return _classify_doc(hit)
    if hit_type == "graph_fact":
        return _classify_graph(hit, policy)
    return "omit", REASON_OMIT_UNKNOWN_TYPE


def _classify_memory(
    hit: ContextHit, policy: PackEvidenceGatePolicy
) -> tuple[GateAction, str]:
    if is_memory_hint_hit(hit):
        return "keep", REASON_KEEP_HINT

    status = str(getattr(hit, "status", "") or "active").strip() or "active"
    if status != "active":
        return "omit", REASON_OMIT_NON_ACTIVE

    conf = _read_confidence(hit)
    # Fail-closed only for present non-finite values (NaN/Inf). Absent confidence
    # is not a numeric non-finite; soft threshold uses 0.0 (untrusted empty → demote).
    if conf is not None and not math.isfinite(conf):
        return "omit", REASON_OMIT_NON_FINITE
    conf_for_soft = 0.0 if conf is None else conf

    if _has_evidence(hit):
        return "keep", REASON_KEEP_EVIDENCE

    source = str(getattr(hit, "source", "") or "").strip()
    if source in TRUSTED_EMPTY_EVIDENCE_SOURCES:
        return "keep", REASON_KEEP_TRUSTED_EMPTY

    if conf_for_soft < policy.memory_soft_confidence:
        return "demote", REASON_DEMOTE_MEMORY_WEAK

    return "keep", REASON_KEEP_DEFAULT


def _classify_doc(hit: ContextHit) -> tuple[GateAction, str]:
    content = str(getattr(hit, "content", "") or "")
    if not content.strip():
        return "omit", REASON_OMIT_BLANK_DOC
    return "keep", REASON_KEEP_DOC


def _classify_graph(
    hit: ContextHit, policy: PackEvidenceGatePolicy
) -> tuple[GateAction, str]:
    status = str(getattr(hit, "status", "") or "active").strip() or "active"
    if status != "active":
        return "omit", REASON_OMIT_NON_ACTIVE

    conf = _read_confidence(hit)
    if conf is not None and not math.isfinite(conf):
        return "omit", REASON_OMIT_NON_FINITE
    conf_for_soft = 0.0 if conf is None else conf

    has_ev = _has_evidence(hit)
    if has_ev:
        return "keep", REASON_KEEP_EVIDENCE

    hop = _graph_hop(hit)
    weak = conf_for_soft < policy.graph_soft_confidence
    # Weak + empty evidence + multi-hop: hard omit (uncorroborated hop).
    if weak and hop >= 1:
        return "omit", REASON_OMIT_GRAPH_WEAK_HOP

    # Direct (hop 0) empty-evidence or other low-confidence without hop>=1: demote.
    if weak or not has_ev:
        return "demote", REASON_DEMOTE_GRAPH_WEAK

    return "keep", REASON_KEEP_DEFAULT


def _read_confidence(hit: ContextHit) -> float | None:
    """Read source confidence — never hit.score (RRF fusion rank).

    Returns None when no confidence field is present. Callers must not treat
    None as a fusion score; NaN/Inf must be returned as floats for fail-closed omit.
    """
    breakdown = getattr(hit, "score_breakdown", None)
    if breakdown is not None:
        conf = getattr(breakdown, "confidence", None)
        if conf is not None:
            try:
                return float(conf)
            except (TypeError, ValueError):
                return float("nan")
    meta = getattr(hit, "metadata", None) or {}
    if isinstance(meta, Mapping) and "confidence" in meta:
        try:
            return float(meta["confidence"])
        except (TypeError, ValueError):
            return float("nan")
    return None


def _has_evidence(hit: ContextHit) -> bool:
    prov = getattr(hit, "provenance", None)
    if prov is None:
        return False
    msg = str(getattr(prov, "source_message_id", "") or "").strip()
    if msg:
        return True
    refs = getattr(prov, "evidence_refs", None) or ()
    return any(str(ref or "").strip() for ref in refs)


def _graph_hop(hit: ContextHit) -> int:
    meta = getattr(hit, "metadata", None) or {}
    if not isinstance(meta, Mapping):
        return 0
    raw = meta.get("graph_hop", 0)
    try:
        hop = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, hop)
