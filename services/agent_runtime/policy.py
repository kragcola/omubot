"""Dark capability planning and execution-time policy contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from kernel.types import Tool, ToolApproval, ToolEffect, ToolIdempotency, ToolSpec

_POLICY_VERSION = "agent-runtime-policy-v1"


def canonical_args_digest(arguments: Mapping[str, Any]) -> str:
    if not isinstance(arguments, Mapping):
        raise ValueError("tool arguments must be a canonical JSON object")
    try:
        payload = json.dumps(
            dict(arguments),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("tool arguments must be canonical JSON") from exc
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class CapabilityConstraints:
    tools_enabled: bool = True
    allowed_tools: tuple[str, ...] = ()
    blocked_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_tools",
            tuple(
                sorted({str(name).strip() for name in self.allowed_tools if str(name).strip()})
            ),
        )
        object.__setattr__(
            self,
            "blocked_tools",
            tuple(
                sorted({str(name).strip() for name in self.blocked_tools if str(name).strip()})
            ),
        )


@dataclass(frozen=True, slots=True)
class CapabilityPlan:
    tools: tuple[Tool, ...]
    registry_generation: int
    excluded: dict[str, str]


class CapabilityPlanner:
    """Build the model-visible tool set without granting execution authority."""

    def plan(
        self,
        *,
        tools: Iterable[Tool],
        constraints: CapabilityConstraints,
        registry_generation: int,
    ) -> CapabilityPlan:
        if registry_generation < 0:
            raise ValueError("registry_generation must be non-negative")
        candidates: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in candidates:
                raise ValueError(f"duplicate tool in capability catalog: {tool.name}")
            candidates[tool.name] = tool

        allowed = set(constraints.allowed_tools)
        blocked = set(constraints.blocked_tools)
        visible: list[Tool] = []
        excluded: dict[str, str] = {}
        for name in sorted(candidates):
            tool = candidates[name]
            if not constraints.tools_enabled:
                excluded[name] = "tools_disabled"
            elif name in blocked:
                excluded[name] = "blocked"
            elif allowed and name not in allowed:
                excluded[name] = "not_allowed"
            else:
                visible.append(tool)
        return CapabilityPlan(
            tools=tuple(visible),
            registry_generation=registry_generation,
            excluded=excluded,
        )


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class PolicyReason(StrEnum):
    ALLOWED = "allowed"
    CALL_SNAPSHOT_MISMATCH = "call_snapshot_mismatch"
    INPUT_SCHEMA_INVALID = "input_schema_invalid"
    TOOL_SCHEMA_INVALID = "tool_schema_invalid"
    LEGACY_UNCLASSIFIED = "legacy_unclassified"
    REGISTRY_GENERATION_MISMATCH = "registry_generation_mismatch"
    MISSING_SCOPE = "missing_scope"
    TARGET_REQUIRED = "target_required"
    TARGET_OUT_OF_SCOPE = "target_out_of_scope"
    IDEMPOTENCY_KEY_REQUIRED = "idempotency_key_required"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_INVALID = "approval_invalid"
    APPROVAL_EXPIRED = "approval_expired"


@dataclass(frozen=True, slots=True)
class RuntimePrincipal:
    kind: str
    principal_id: str
    granted_scopes: tuple[str, ...] = ()
    allowed_target_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.principal_id.strip():
            raise ValueError("runtime principal kind and id are required")
        object.__setattr__(self, "kind", self.kind.strip())
        object.__setattr__(self, "principal_id", self.principal_id.strip())
        object.__setattr__(
            self,
            "granted_scopes",
            tuple(
                sorted(
                    {
                        str(scope).strip()
                        for scope in self.granted_scopes
                        if str(scope).strip()
                    }
                )
            ),
        )
        object.__setattr__(
            self,
            "allowed_target_refs",
            tuple(
                sorted(
                    {
                        str(target).strip()
                        for target in self.allowed_target_refs
                        if str(target).strip()
                    }
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    approval_ref_digest: str
    principal_kind: str
    principal_id: str
    tool_name: str
    tool_version: str
    effect: ToolEffect
    args_digest: str
    target_ref: str
    expires_at: str

    def __post_init__(self) -> None:
        required = (
            "approval_ref_digest",
            "principal_kind",
            "principal_id",
            "tool_name",
            "tool_version",
            "args_digest",
            "expires_at",
        )
        for field_name in required:
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"ApprovalGrant {field_name} is required")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "target_ref", self.target_ref.strip())
        try:
            object.__setattr__(self, "effect", ToolEffect(self.effect))
        except (TypeError, ValueError) as exc:
            raise ValueError("ApprovalGrant effect is invalid") from exc
        expires_at = self.expires_datetime()
        if expires_at.utcoffset() is None:
            raise ValueError("ApprovalGrant expires_at must include timezone")

    def expires_datetime(self) -> datetime:
        try:
            return datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("ApprovalGrant expires_at must be ISO-8601") from exc


@dataclass(frozen=True, slots=True)
class ToolPolicyRequest:
    spec: ToolSpec
    principal: RuntimePrincipal
    args_digest: str
    target_ref: str = ""
    idempotency_key_digest: str = ""
    approval: ApprovalGrant | None = None
    registry_generation: int = 0

    def __post_init__(self) -> None:
        if not self.args_digest.strip():
            raise ValueError("tool policy request args_digest is required")
        if self.registry_generation < 0:
            raise ValueError("registry_generation must be non-negative")
        object.__setattr__(self, "args_digest", self.args_digest.strip())
        object.__setattr__(self, "target_ref", self.target_ref.strip())
        object.__setattr__(
            self,
            "idempotency_key_digest",
            self.idempotency_key_digest.strip(),
        )


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    outcome: PolicyOutcome
    reason: PolicyReason
    policy_version: str = _POLICY_VERSION
    missing_scopes: tuple[str, ...] = ()

    def to_event_metadata(self, request: ToolPolicyRequest) -> dict[str, Any]:
        approval_ref_digest = (
            request.approval.approval_ref_digest
            if request.approval is not None
            else ""
        )
        return {
            "policy_version": self.policy_version,
            "outcome": self.outcome.value,
            "reason": self.reason.value,
            "tool_name": request.spec.name,
            "tool_version": request.spec.version,
            "owner": request.spec.owner,
            "effect": request.spec.effect.value,
            "principal_kind": request.principal.kind,
            "principal_id": request.principal.principal_id,
            "target_ref": request.target_ref,
            "args_digest": request.args_digest,
            "idempotency_key_digest": request.idempotency_key_digest,
            "approval_ref_digest": approval_ref_digest,
            "registry_generation": request.registry_generation,
            "missing_scopes": list(self.missing_scopes),
        }


class PolicyGate:
    """Complete mediation for one trusted ToolPolicyRequest."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def evaluate(
        self,
        request: ToolPolicyRequest,
        *,
        current_registry_generation: int | None = None,
    ) -> PolicyDecision:
        if current_registry_generation is not None:
            if current_registry_generation < 0:
                raise ValueError("current_registry_generation must be non-negative")
            if request.registry_generation != current_registry_generation:
                return PolicyDecision(
                    outcome=PolicyOutcome.DENY,
                    reason=PolicyReason.REGISTRY_GENERATION_MISMATCH,
                )
        spec = request.spec
        if spec.effect is ToolEffect.LEGACY_UNCLASSIFIED:
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason=PolicyReason.LEGACY_UNCLASSIFIED,
            )

        missing_scopes = tuple(
            sorted(set(spec.required_scopes) - set(request.principal.granted_scopes))
        )
        if missing_scopes:
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason=PolicyReason.MISSING_SCOPE,
                missing_scopes=missing_scopes,
            )

        if spec.effect in {
            ToolEffect.WRITE_LOCAL,
            ToolEffect.EXTERNAL_READ,
            ToolEffect.EXTERNAL_REVERSIBLE,
            ToolEffect.EXTERNAL_IRREVERSIBLE,
        } and not request.target_ref:
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason=PolicyReason.TARGET_REQUIRED,
            )

        if (
            request.target_ref
            and request.target_ref not in request.principal.allowed_target_refs
        ):
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason=PolicyReason.TARGET_OUT_OF_SCOPE,
            )

        if (
            spec.idempotency
            in {ToolIdempotency.REQUIRED, ToolIdempotency.PROVIDER_SUPPORTED}
            and not request.idempotency_key_digest
        ):
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason=PolicyReason.IDEMPOTENCY_KEY_REQUIRED,
            )

        requires_approval = spec.approval is ToolApproval.ALWAYS or spec.effect in {
            ToolEffect.EXTERNAL_REVERSIBLE,
            ToolEffect.EXTERNAL_IRREVERSIBLE,
        }
        if requires_approval:
            approval = request.approval
            if approval is None:
                return PolicyDecision(
                    outcome=PolicyOutcome.REQUIRE_APPROVAL,
                    reason=PolicyReason.APPROVAL_REQUIRED,
                )
            if not self._approval_matches(request, approval):
                return PolicyDecision(
                    outcome=PolicyOutcome.DENY,
                    reason=PolicyReason.APPROVAL_INVALID,
                )
            if self._approval_expired(approval):
                return PolicyDecision(
                    outcome=PolicyOutcome.DENY,
                    reason=PolicyReason.APPROVAL_EXPIRED,
                )
        return PolicyDecision(
            outcome=PolicyOutcome.ALLOW,
            reason=PolicyReason.ALLOWED,
        )

    @staticmethod
    def _approval_matches(
        request: ToolPolicyRequest,
        approval: ApprovalGrant,
    ) -> bool:
        return (
            approval.principal_kind == request.principal.kind
            and approval.principal_id == request.principal.principal_id
            and approval.tool_name == request.spec.name
            and approval.tool_version == request.spec.version
            and approval.effect is request.spec.effect
            and approval.args_digest == request.args_digest
            and approval.target_ref == request.target_ref
        )

    def _approval_expired(self, approval: ApprovalGrant) -> bool:
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("PolicyGate clock must return timezone-aware datetime")
        return now >= approval.expires_datetime()
