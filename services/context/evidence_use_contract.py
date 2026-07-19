"""Evidence-use / pack-state contract v1 (euc_v1).

Observability and a soft model-facing constraint around post-gate packs.
Not proof that an answer used evidence. Metrics are secret-free and closed-set.
Pure / sync / deterministic; no I/O.

Classification is final post-pack first: demote_present requires at least one
demoted non-hint survivor in pack_hits. Empty final packs with prior demote
signals classify as empty (inject). omit_only remains a distinct diagnostic
for pure-omit empty packs but injects like empty/hint_only when enabled.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal

from services.context.pack_evidence_gate import (
    is_memory_hint_hit,
    sanitize_pack_evidence_gate_metrics,
)
from services.context.types import ContextHit

CONTRACT_VERSION: Final[str] = "euc_v1"

PackState = Literal[
    "empty",
    "hint_only",
    "nonempty",
    "demote_present",
    "omit_only",
    "skip",
]

ContractAction = Literal["none", "constrained_instruction", "identity"]

_CLOSED_PACK_STATES: Final[frozenset[str]] = frozenset(
    {
        "empty",
        "hint_only",
        "nonempty",
        "demote_present",
        "omit_only",
        "skip",
    }
)

_CLOSED_ACTIONS: Final[frozenset[str]] = frozenset(
    {"none", "constrained_instruction", "identity"}
)

# States that may receive the soft constrained instruction when inject is on.
_INJECTABLE_PACK_STATES: Final[frozenset[str]] = frozenset(
    {"empty", "hint_only", "omit_only"}
)

# Concise Chinese soft constraint for empty / hint_only / omit_only packs.
# Never forces pass_turn, hard abstention, or silence.
CONSTRAINED_MODE_INSTRUCTION_ZH: Final[str] = (
    "【资料约束】当前检索包为空或仅有最小提示，没有可引用的具体资料。"
    "请不要编造记忆卡片或文档中的具体事实；可正常对话与回应，"
    "不必沉默或硬性拒答。"
)

# Low-priority dynamic block (main context uses 50; temporal trace 45).
INSTRUCTION_BLOCK_PRIORITY: Final[int] = 40
INSTRUCTION_BLOCK_LABEL: Final[str] = "资料使用约束"
INSTRUCTION_BLOCK_SOURCE: Final[str] = "context_evidence_use"


@dataclass(frozen=True, slots=True)
class EvidenceUseContractPolicy:
    """Kill-switch + soft instruction policy for euc_v1."""

    enabled: bool = True
    inject_constrained_instruction: bool = True


DEFAULT_EVIDENCE_USE_CONTRACT_POLICY = EvidenceUseContractPolicy()


@dataclass(frozen=True, slots=True)
class EvidenceUseContract:
    """Closed pack-state contract attached to a ContextPack (internal)."""

    version: str
    enabled: bool
    identity: bool
    pack_state: PackState
    action: ContractAction
    inject_instruction: bool
    actions: Mapping[str, int]
    reasons: Mapping[str, int]

    def to_metrics(self) -> dict[str, Any]:
        return sanitize_evidence_use_contract_metrics(
            {
                "version": self.version,
                "enabled": self.enabled,
                "identity": self.identity,
                "pack_state": self.pack_state,
                "action": self.action,
                "inject_instruction": self.inject_instruction,
                "actions": dict(self.actions),
                "reasons": dict(self.reasons),
            }
        )


def derive_evidence_use_contract(
    *,
    pack_hits: Sequence[ContextHit],
    omitted_count: int = 0,
    peg_metrics: Mapping[str, Any] | None = None,
    retrieve_mode: str = "hybrid",
    enabled: bool = True,
    inject_constrained_instruction: bool = True,
) -> EvidenceUseContract:
    """Derive closed pack_state + optional soft instruction flag.

    Deterministic and side-effect free. Malformed peg metrics fail closed
    (zero demote/omit signals after sanitize).

    pack_state describes the final post-pack hit list first. demote_present
    requires at least one demoted non-hint survivor in pack_hits.
    """
    del omitted_count  # reserved for future pack-budget signals; not trusted raw
    peg = sanitize_pack_evidence_gate_metrics(peg_metrics)
    actions = dict(peg.get("actions") or {"keep": 0, "demote": 0, "omit": 0})
    reasons = dict(peg.get("reasons") or {})
    keep_n = int(actions.get("keep", 0) or 0)
    demote_n = int(actions.get("demote", 0) or 0)
    omit_n = int(actions.get("omit", 0) or 0)

    mode = str(retrieve_mode or "hybrid").strip().lower() or "hybrid"
    pack_state = _classify_pack_state(
        pack_hits=pack_hits,
        retrieve_mode=mode,
        keep_n=keep_n,
        demote_n=demote_n,
        omit_n=omit_n,
    )

    if not enabled:
        return EvidenceUseContract(
            version=CONTRACT_VERSION,
            enabled=False,
            identity=True,
            pack_state=pack_state,
            action="identity",
            inject_instruction=False,
            actions=MappingProxyType(actions),
            reasons=MappingProxyType(reasons),
        )

    inject = bool(
        inject_constrained_instruction
        and pack_state in _INJECTABLE_PACK_STATES
        and mode != "skip"
    )
    action: ContractAction = "constrained_instruction" if inject else "none"
    return EvidenceUseContract(
        version=CONTRACT_VERSION,
        enabled=True,
        identity=False,
        pack_state=pack_state,
        action=action,
        inject_instruction=inject,
        actions=MappingProxyType(actions),
        reasons=MappingProxyType(reasons),
    )


def sanitize_evidence_use_contract_metrics(raw: Mapping[str, Any] | None | Any) -> dict[str, Any]:
    """Closed secret-free metrics for recent rows and aggregates.

    Invariants (fail closed):
    - action=identity only when disabled or identity=true
    - constrained_instruction only when inject_instruction=true and
      pack_state in {empty, hint_only, omit_only}
    - otherwise action=none; inject forced false when inconsistent
    """
    base: dict[str, Any] = {
        "version": CONTRACT_VERSION,
        "enabled": False,
        "identity": True,
        "pack_state": "empty",
        "action": "none",
        "inject_instruction": False,
        "actions": {"keep": 0, "demote": 0, "omit": 0},
        "reasons": {},
    }
    if not isinstance(raw, Mapping):
        return base

    enabled_raw = raw.get("enabled", False)
    enabled_b = enabled_raw if isinstance(enabled_raw, bool) else False

    # Disabled always forces identity=true (kill-switch).
    if not enabled_b:
        identity_b = True
    else:
        identity_raw = raw.get("identity", False)
        identity_b = identity_raw if isinstance(identity_raw, bool) else False

    pack_state_raw = str(raw.get("pack_state") or "empty")
    pack_state = pack_state_raw if pack_state_raw in _CLOSED_PACK_STATES else "empty"

    inject_raw = raw.get("inject_instruction", False)
    inject = inject_raw if isinstance(inject_raw, bool) else False

    action_raw = str(raw.get("action") or "none")
    action = action_raw if action_raw in _CLOSED_ACTIONS else "none"

    # Kill-switch / identity path: no inject; action must be identity.
    if not enabled_b or identity_b:
        inject = False
        action = "identity"
        identity_b = True
    else:
        # Enabled + non-identity: identity action is malformed → none.
        if action == "identity":
            action = "none"
        # Inject only for injectable empty-like states.
        if pack_state not in _INJECTABLE_PACK_STATES:
            inject = False
        # constrained_instruction only with inject + injectable state.
        if inject and pack_state in _INJECTABLE_PACK_STATES:
            action = "constrained_instruction"
        else:
            inject = False
            # Malformed constrained_instruction / identity already handled → none.
            if action != "none":
                action = "none"

    # Reuse peg sanitizer for closed actions/reasons (no private allowlist import).
    peg_slice = sanitize_pack_evidence_gate_metrics(
        {
            "actions": raw.get("actions") if isinstance(raw.get("actions"), Mapping) else {},
            "reasons": raw.get("reasons") if isinstance(raw.get("reasons"), Mapping) else {},
        }
    )
    actions = dict(peg_slice.get("actions") or {"keep": 0, "demote": 0, "omit": 0})
    reasons = dict(peg_slice.get("reasons") or {})

    version = str(raw.get("version") or CONTRACT_VERSION)
    if version != CONTRACT_VERSION:
        version = CONTRACT_VERSION

    return {
        "version": version,
        "enabled": enabled_b,
        "identity": identity_b,
        "pack_state": pack_state,
        "action": action,
        "inject_instruction": inject,
        "actions": actions,
        "reasons": reasons,
    }


def policy_from_mapping(
    raw: Mapping[str, Any] | EvidenceUseContractPolicy | None,
) -> EvidenceUseContractPolicy:
    if isinstance(raw, EvidenceUseContractPolicy):
        return raw
    if not raw:
        return DEFAULT_EVIDENCE_USE_CONTRACT_POLICY
    raw_enabled = raw.get("enabled", True)
    enabled = raw_enabled if isinstance(raw_enabled, bool) else True
    raw_inject = raw.get("inject_constrained_instruction", True)
    inject = raw_inject if isinstance(raw_inject, bool) else True
    return EvidenceUseContractPolicy(
        enabled=enabled,
        inject_constrained_instruction=inject,
    )


def aggregate_evidence_use_metrics(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate closed counters from recent rows (secret-free)."""
    state_counts: dict[str, int] = {s: 0 for s in sorted(_CLOSED_PACK_STATES)}
    action_counts: dict[str, int] = {a: 0 for a in sorted(_CLOSED_ACTIONS)}
    inject_count = 0
    present = 0
    for row in rows:
        raw = row.get("evidence_use_contract") if isinstance(row, Mapping) else None
        if not isinstance(raw, Mapping):
            continue
        clean = sanitize_evidence_use_contract_metrics(raw)
        present += 1
        st = str(clean.get("pack_state") or "empty")
        if st in state_counts:
            state_counts[st] += 1
        else:
            state_counts["empty"] += 1
        act = str(clean.get("action") or "none")
        if act in action_counts:
            action_counts[act] += 1
        else:
            action_counts["none"] += 1
        if clean.get("inject_instruction"):
            inject_count += 1
    total = present
    return {
        "version": CONTRACT_VERSION,
        "present": present,
        "state_counts": state_counts,
        "action_counts": action_counts,
        "inject_count": inject_count,
        "inject_rate": (inject_count / total) if total else 0.0,
        "skip_rate": (state_counts.get("skip", 0) / total) if total else 0.0,
        "empty_rate": (state_counts.get("empty", 0) / total) if total else 0.0,
        "hint_only_rate": (state_counts.get("hint_only", 0) / total) if total else 0.0,
    }


def _classify_pack_state(
    *,
    pack_hits: Sequence[ContextHit],
    retrieve_mode: str,
    keep_n: int,
    demote_n: int,
    omit_n: int,
) -> PackState:
    """Final post-pack state first; peg action counts are secondary signals."""
    if retrieve_mode == "skip":
        return "skip"

    hits = list(pack_hits)
    if hits:
        non_hints = [h for h in hits if not is_memory_hint_hit(h)]
        if not non_hints:
            return "hint_only"
        # demote_present only when ≥1 demoted non-hint actually survived pack.
        if demote_n > 0 and keep_n == 0:
            return "demote_present"
        return "nonempty"

    # Empty final pack: never demote_present (no survivors).
    # Pure omit gate (no keep/demote) → omit_only diagnostic; else empty.
    if omit_n > 0 and keep_n == 0 and demote_n == 0:
        return "omit_only"
    return "empty"
