"""Behavior contracts for dark Agent Runtime capability and policy decisions."""

import inspect
import math
from datetime import UTC, datetime
from typing import Any

import pytest

import services.agent_runtime.policy as policy_module
from kernel.types import Tool, ToolApproval, ToolContext, ToolEffect, ToolSpec


class _SpecTool(Tool):
    def __init__(self, name: str, *, effect: ToolEffect) -> None:
        self._name = name
        self._spec = ToolSpec(
            name=name,
            description=name,
            input_schema={"type": "object"},
            owner="test",
            effect=effect,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object"}

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        return "ok"


def test_capability_planner_applies_allowlist_then_blocklist_deterministically() -> None:
    planner_type = getattr(policy_module, "CapabilityPlanner", None)
    constraints_type = getattr(policy_module, "CapabilityConstraints", None)
    assert planner_type is not None
    assert constraints_type is not None

    read_tool = _SpecTool("read_a", effect=ToolEffect.READ)
    write_tool = _SpecTool("write_b", effect=ToolEffect.WRITE_LOCAL)
    omitted_tool = _SpecTool("omitted_c", effect=ToolEffect.PURE)
    plan = planner_type().plan(
        tools=(write_tool, omitted_tool, read_tool),
        constraints=constraints_type(
            tools_enabled=True,
            allowed_tools=("write_b", "read_a"),
            blocked_tools=("write_b",),
        ),
        registry_generation=7,
    )

    assert plan.registry_generation == 7
    assert tuple(tool.name for tool in plan.tools) == ("read_a",)
    assert plan.excluded == {
        "omitted_c": "not_allowed",
        "write_b": "blocked",
    }


def test_policy_gate_denies_legacy_and_missing_scope_before_allowing_read() -> None:
    gate_type = getattr(policy_module, "PolicyGate", None)
    principal_type = getattr(policy_module, "RuntimePrincipal", None)
    request_type = getattr(policy_module, "ToolPolicyRequest", None)
    assert gate_type is not None
    assert principal_type is not None
    assert request_type is not None

    principal = principal_type(
        kind="user",
        principal_id="user-1",
        granted_scopes=("memory:read",),
        allowed_target_refs=("memory:user-1",),
    )
    legacy = ToolSpec(
        name="legacy_tool",
        description="legacy",
        input_schema={"type": "object"},
    )
    missing_scope = ToolSpec(
        name="write_tool",
        description="write",
        input_schema={"type": "object"},
        owner="test",
        effect=ToolEffect.WRITE_LOCAL,
        required_scopes=("memory:write",),
    )
    allowed_read = ToolSpec(
        name="read_tool",
        description="read",
        input_schema={"type": "object"},
        owner="test",
        effect=ToolEffect.READ,
        required_scopes=("memory:read",),
    )
    gate = gate_type()

    legacy_decision = gate.evaluate(
        request_type(
            spec=legacy,
            principal=principal,
            args_digest="sha256:legacy",
            target_ref="memory:user-1",
        )
    )
    scope_decision = gate.evaluate(
        request_type(
            spec=missing_scope,
            principal=principal,
            args_digest="sha256:write",
            target_ref="memory:user-1",
        )
    )
    allowed_decision = gate.evaluate(
        request_type(
            spec=allowed_read,
            principal=principal,
            args_digest="sha256:read",
            target_ref="memory:user-1",
        )
    )

    assert (legacy_decision.outcome, legacy_decision.reason) == (
        "deny",
        "legacy_unclassified",
    )
    assert (scope_decision.outcome, scope_decision.reason) == (
        "deny",
        "missing_scope",
    )
    assert scope_decision.missing_scopes == ("memory:write",)
    assert (allowed_decision.outcome, allowed_decision.reason) == (
        "allow",
        "allowed",
    )


def test_external_effect_approval_is_expiring_and_bound_to_exact_intent() -> None:
    grant_type = getattr(policy_module, "ApprovalGrant", None)
    assert grant_type is not None
    principal = policy_module.RuntimePrincipal(
        kind="user",
        principal_id="user-2",
        granted_scopes=("qzone:publish",),
        allowed_target_refs=("qzone:user-2",),
    )
    spec = ToolSpec(
        name="qzone_publish",
        version="2",
        owner="qzone_journal",
        description="Publish a QZone entry",
        input_schema={"type": "object"},
        effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
        required_scopes=("qzone:publish",),
        approval=ToolApproval.NEVER,
    )
    now = datetime(2026, 7, 21, 2, 0, tzinfo=UTC)
    gate = policy_module.PolicyGate(clock=lambda: now)

    def request(approval=None):
        return policy_module.ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest="sha256:args",
            target_ref="qzone:user-2",
            approval=approval,
        )

    missing = gate.evaluate(request())
    mismatched = gate.evaluate(
        request(
            grant_type(
                approval_ref_digest="sha256:approval",
                principal_kind="user",
                principal_id="user-2",
                tool_name="qzone_publish",
                tool_version="2",
                effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
                args_digest="sha256:different-args",
                target_ref="qzone:user-2",
                expires_at="2026-07-21T03:00:00+00:00",
            )
        )
    )
    expired = gate.evaluate(
        request(
            grant_type(
                approval_ref_digest="sha256:approval",
                principal_kind="user",
                principal_id="user-2",
                tool_name="qzone_publish",
                tool_version="2",
                effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
                args_digest="sha256:args",
                target_ref="qzone:user-2",
                expires_at="2026-07-21T01:59:59+00:00",
            )
        )
    )
    valid = gate.evaluate(
        request(
            grant_type(
                approval_ref_digest="sha256:approval",
                principal_kind="user",
                principal_id="user-2",
                tool_name="qzone_publish",
                tool_version="2",
                effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
                args_digest="sha256:args",
                target_ref="qzone:user-2",
                expires_at="2026-07-21T03:00:00+00:00",
            )
        )
    )

    assert (missing.outcome, missing.reason) == (
        "require_approval",
        "approval_required",
    )
    assert (mismatched.outcome, mismatched.reason) == (
        "deny",
        "approval_invalid",
    )
    assert (expired.outcome, expired.reason) == (
        "deny",
        "approval_expired",
    )
    assert (valid.outcome, valid.reason) == ("allow", "allowed")


def test_canonical_args_digest_is_order_stable_and_rejects_non_json_numbers() -> None:
    digest = getattr(policy_module, "canonical_args_digest", None)
    assert callable(digest)

    first = digest({"text": "你好", "nested": {"b": 2, "a": 1}})
    second = digest({"nested": {"a": 1, "b": 2}, "text": "你好"})

    assert isinstance(first, str)
    assert isinstance(second, str)
    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64
    with pytest.raises(ValueError, match="canonical JSON"):
        digest({"value": math.nan})


def test_policy_gate_denies_nonempty_target_without_host_target_grant() -> None:
    principal = policy_module.RuntimePrincipal(
        kind="user",
        principal_id="user-target",
        granted_scopes=("memory:read",),
    )
    spec = ToolSpec(
        name="read_tool",
        description="read",
        input_schema={"type": "object"},
        owner="test",
        effect=ToolEffect.READ,
        required_scopes=("memory:read",),
    )

    decision = policy_module.PolicyGate().evaluate(
        policy_module.ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest="sha256:target",
            target_ref="memory:another-user",
        )
    )

    assert (decision.outcome, decision.reason) == (
        "deny",
        "target_out_of_scope",
    )


def test_policy_gate_requires_target_for_external_effect() -> None:
    principal = policy_module.RuntimePrincipal(
        kind="user",
        principal_id="user-external-target",
    )
    spec = ToolSpec(
        name="external_without_target",
        owner="test",
        description="external without target",
        input_schema={"type": "object"},
        effect=ToolEffect.EXTERNAL_REVERSIBLE,
    )

    decision = policy_module.PolicyGate().evaluate(
        policy_module.ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest="sha256:args",
            registry_generation=4,
        ),
        current_registry_generation=4,
    )

    assert (decision.outcome, decision.reason) == ("deny", "target_required")


def test_policy_gate_requires_target_for_write_local_effect() -> None:
    principal = policy_module.RuntimePrincipal(
        kind="user",
        principal_id="user-write-local",
        granted_scopes=("memory:write",),
        allowed_target_refs=("memory:user:user-write-local",),
    )
    spec = ToolSpec(
        name="local_write_without_target",
        owner="test",
        description="local write without target",
        input_schema={"type": "object"},
        effect=ToolEffect.WRITE_LOCAL,
        required_scopes=("memory:write",),
        binding_required=True,
    )
    gate = policy_module.PolicyGate()

    missing = gate.evaluate(
        policy_module.ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest="sha256:args",
            registry_generation=6,
        ),
        current_registry_generation=6,
    )
    allowed = gate.evaluate(
        policy_module.ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest="sha256:args",
            target_ref="memory:user:user-write-local",
            registry_generation=6,
        ),
        current_registry_generation=6,
    )

    assert (missing.outcome, missing.reason) == ("deny", "target_required")
    assert (allowed.outcome, allowed.reason) == ("allow", "allowed")


def test_external_read_requires_scoped_target_but_not_approval() -> None:
    external_read = getattr(ToolEffect, "EXTERNAL_READ", None)
    assert external_read is not None
    principal = policy_module.RuntimePrincipal(
        kind="user",
        principal_id="user-external-read",
        allowed_target_refs=("https:example.com",),
    )
    spec = ToolSpec(
        name="fetch_public_page",
        owner="web_fetch",
        description="fetch public page",
        input_schema={"type": "object"},
        effect=external_read,
    )
    gate = policy_module.PolicyGate()

    missing_target = gate.evaluate(
        policy_module.ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest="sha256:args",
            registry_generation=5,
        ),
        current_registry_generation=5,
    )
    allowed = gate.evaluate(
        policy_module.ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest="sha256:args",
            target_ref="https:example.com",
            registry_generation=5,
        ),
        current_registry_generation=5,
    )

    assert (missing_target.outcome, missing_target.reason) == (
        "deny",
        "target_required",
    )
    assert (allowed.outcome, allowed.reason) == ("allow", "allowed")


def test_policy_gate_rejects_stale_registry_generation() -> None:
    gate = policy_module.PolicyGate()
    assert "current_registry_generation" in inspect.signature(
        gate.evaluate
    ).parameters
    principal = policy_module.RuntimePrincipal(
        kind="service",
        principal_id="runtime",
    )
    spec = ToolSpec(
        name="pure_tool",
        description="pure",
        input_schema={"type": "object"},
        owner="test",
        effect=ToolEffect.PURE,
    )
    request = policy_module.ToolPolicyRequest(
        spec=spec,
        principal=principal,
        args_digest="sha256:stale",
        registry_generation=4,
    )

    decision = gate.evaluate(request, current_registry_generation=5)

    assert (decision.outcome, decision.reason) == (
        "deny",
        "registry_generation_mismatch",
    )


def test_approval_grant_is_bound_to_tool_effect() -> None:
    assert "effect" in inspect.signature(policy_module.ApprovalGrant).parameters
    principal = policy_module.RuntimePrincipal(
        kind="user",
        principal_id="user-effect",
        granted_scopes=("external:write",),
        allowed_target_refs=("external:target",),
    )
    spec = ToolSpec(
        name="external_write",
        version="1",
        owner="test",
        description="external write",
        input_schema={"type": "object"},
        effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
        required_scopes=("external:write",),
    )
    approval = policy_module.ApprovalGrant(
        approval_ref_digest="sha256:approval",
        principal_kind="user",
        principal_id="user-effect",
        tool_name="external_write",
        tool_version="1",
        effect=ToolEffect.READ,
        args_digest="sha256:effect",
        target_ref="external:target",
        expires_at="2026-07-21T04:00:00+00:00",
    )

    decision = policy_module.PolicyGate(
        clock=lambda: datetime(2026, 7, 21, 3, 0, tzinfo=UTC)
    ).evaluate(
        policy_module.ToolPolicyRequest(
            spec=spec,
            principal=principal,
            args_digest="sha256:effect",
            target_ref="external:target",
            approval=approval,
        )
    )

    assert (decision.outcome, decision.reason) == (
        "deny",
        "approval_invalid",
    )


def test_policy_decision_event_metadata_contains_digests_not_raw_authority() -> None:
    principal = policy_module.RuntimePrincipal(
        kind="service",
        principal_id="runtime",
        granted_scopes=("memory:read",),
        allowed_target_refs=("memory:user-1",),
    )
    spec = ToolSpec(
        name="read_tool",
        version="3",
        owner="memory",
        description="read",
        input_schema={"type": "object"},
        effect=ToolEffect.READ,
        required_scopes=("memory:read",),
    )
    request = policy_module.ToolPolicyRequest(
        spec=spec,
        principal=principal,
        args_digest="sha256:args",
        target_ref="memory:user-1",
        idempotency_key_digest="sha256:idempotency",
        registry_generation=9,
    )
    decision = policy_module.PolicyGate().evaluate(
        request,
        current_registry_generation=9,
    )
    to_event_metadata = getattr(decision, "to_event_metadata", None)
    assert callable(to_event_metadata)

    metadata = to_event_metadata(request)

    assert isinstance(metadata, dict)
    assert metadata == {
        "policy_version": "agent-runtime-policy-v1",
        "outcome": "allow",
        "reason": "allowed",
        "tool_name": "read_tool",
        "tool_version": "3",
        "owner": "memory",
        "effect": "read",
        "principal_kind": "service",
        "principal_id": "runtime",
        "target_ref": "memory:user-1",
        "args_digest": "sha256:args",
        "idempotency_key_digest": "sha256:idempotency",
        "approval_ref_digest": "",
        "registry_generation": 9,
        "missing_scopes": [],
    }
    assert "expires_at" not in metadata
    assert "raw_args" not in metadata
