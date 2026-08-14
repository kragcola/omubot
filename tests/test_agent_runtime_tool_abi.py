"""Behavior contracts for the additive Agent Runtime v2 Tool ABI."""

from typing import Any, cast

import pytest

import kernel.types as kernel_types
from kernel.types import Tool, ToolContext


class _LegacyTool(Tool):
    @property
    def name(self) -> str:
        return "legacy_lookup"

    @property
    def description(self) -> str:
        return "Legacy lookup tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        return str(kwargs.get("query") or "")


def test_legacy_tool_gets_fail_closed_v2_spec_without_changing_model_schema() -> None:
    tool = _LegacyTool()
    schema_before = {
        "type": "function",
        "function": {
            "name": "legacy_lookup",
            "description": "Legacy lookup tool",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }

    spec = getattr(tool, "spec", None)

    assert spec is not None
    assert spec.abi_version == 2
    assert spec.name == tool.name
    assert spec.owner == "legacy"
    assert spec.effect == "legacy_unclassified"
    assert spec.binding_required is False
    assert spec.input_schema == tool.parameters
    assert tool.to_openai_tool() == schema_before
    binding = tool.bind_invocation(ToolContext(user_id="u1"), {"query": "x"})
    assert binding.target_ref == ""
    assert binding.concurrency_key == ""


def test_explicit_v2_spec_carries_policy_and_execution_metadata() -> None:
    approval_type = getattr(kernel_types, "ToolApproval", None)
    idempotency_type = getattr(kernel_types, "ToolIdempotency", None)
    retry_type = getattr(kernel_types, "ToolRetryPolicy", None)
    concurrency_type = getattr(kernel_types, "ToolConcurrency", None)

    assert approval_type is not None
    assert idempotency_type is not None
    assert retry_type is not None
    assert concurrency_type is not None

    spec = kernel_types.ToolSpec(
        name="publish_entry",
        version="3",
        owner="qzone_journal",
        description="Publish a governed journal entry",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object"},
        effect=kernel_types.ToolEffect.EXTERNAL_IRREVERSIBLE,
        required_scopes=("qzone:publish",),
        credential_mode="ephemeral_broker",
        approval=approval_type.ALWAYS,
        idempotency=idempotency_type.RECONCILE_ONLY,
        retry_policy=retry_type.NEVER,
        concurrency=concurrency_type.KEYED_SERIAL,
        concurrency_key_template="qzone:{target_uin}",
        timeout_ms=15_000,
        data_classification=("public",),
        result_visibility="model_safe",
    )

    assert spec.owner == "qzone_journal"
    assert spec.approval == "always"
    assert spec.idempotency == "reconcile_only"
    assert spec.retry_policy == "never"
    assert spec.concurrency == "keyed_serial"
    assert spec.required_scopes == ("qzone:publish",)
    assert spec.concurrency_key_template == "qzone:{target_uin}"


def test_tool_context_and_result_separate_runtime_secrets_from_model_output() -> None:
    result_status_type = getattr(kernel_types, "ToolResultStatus", None)
    result_type = getattr(kernel_types, "ToolResult", None)
    error_type = getattr(kernel_types, "ToolError", None)
    receipt_type = getattr(kernel_types, "ToolEffectReceipt", None)

    assert result_status_type is not None
    assert result_type is not None
    assert error_type is not None
    assert receipt_type is not None

    ctx = ToolContext(
        user_id="user-1",
        group_id="group-1",
        session_id="session-1",
        principal_kind="user",
        principal_id="user-1",
        run_id="run-1",
        step_id="step-1",
        call_id="call-1",
        target_ref="sticker:stk_01234567:send",
        auth_context_ref="auth-secret-ref",
        approval_ref="approval-secret-ref",
        idempotency_key="idempotency-secret-key",
        trace_id="trace-1",
        registry_generation=7,
    )
    result = result_type(
        status=result_status_type.UNKNOWN,
        structured_output={"message": "delivery needs reconciliation"},
        error=error_type(
            code="unverified_delivery",
            retryable=False,
            safe_message="Delivery result is unknown",
        ),
        effect_receipt=receipt_type(
            provider="qzone",
            external_id="",
            request_digest="digest-secret",
            committed_at="",
        ),
        started_at="2026-07-21T00:00:00+00:00",
        ended_at="2026-07-21T00:00:01+00:00",
    )

    assert ctx.run_id == "run-1"
    assert ctx.target_ref == "sticker:stk_01234567:send"
    assert ctx.registry_generation == 7
    assert result.to_model_payload() == {
        "status": "unknown",
        "output": {"message": "delivery needs reconciliation"},
        "error": {
            "code": "unverified_delivery",
            "message": "Delivery result is unknown",
            "retryable": False,
        },
    }
    assert "digest-secret" not in str(result.to_model_payload())
    assert "approval-secret-ref" not in str(result.to_model_payload())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("effect", "not-a-real-effect"),
        ("approval", "not-a-real-approval"),
        ("idempotency", "unsafe"),
        ("retry_policy", "retry-everything"),
        ("concurrency", "unbounded"),
    ],
)
def test_tool_spec_rejects_invalid_runtime_policy_enums(
    field: str,
    value: str,
) -> None:
    kwargs: dict[str, Any] = {
        "name": "governed_tool",
        "description": "Governed tool",
        "input_schema": {"type": "object"},
    }
    kwargs[field] = cast(Any, value)

    with pytest.raises(ValueError, match=f"invalid ToolSpec {field}"):
        kernel_types.ToolSpec(**kwargs)


def test_tool_result_rejects_invalid_status_at_construction() -> None:
    with pytest.raises(ValueError, match="invalid ToolResult status"):
        kernel_types.ToolResult(status=cast(Any, "not-a-result-status"))


def test_tool_invocation_binding_is_additive_and_strips_fields() -> None:
    binding_type = getattr(kernel_types, "ToolInvocationBinding", None)
    assert binding_type is not None

    binding = binding_type(target_ref="  memory:user:1  ", concurrency_key="  memory:user:1  ")
    empty = binding_type()

    assert binding.target_ref == "memory:user:1"
    assert binding.concurrency_key == "memory:user:1"
    assert empty.target_ref == ""
    assert empty.concurrency_key == ""


def test_keyed_serial_allows_empty_template_when_binding_required() -> None:
    spec = kernel_types.ToolSpec(
        name="bound_write",
        description="bound write",
        input_schema={"type": "object"},
        owner="test",
        effect=kernel_types.ToolEffect.WRITE_LOCAL,
        concurrency=kernel_types.ToolConcurrency.KEYED_SERIAL,
        concurrency_key_template="",
        binding_required=True,
    )
    assert spec.binding_required is True
    assert spec.concurrency_key_template == ""


def test_tool_execution_error_is_fail_safe_about_external_dispatch() -> None:
    error_type = getattr(kernel_types, "ToolExecutionError", None)

    assert error_type is not None
    default = error_type(
        code="provider_failed",
        safe_message="External operation failed",
    )
    proven_pre_dispatch = error_type(
        code="local_gate_rejected",
        safe_message="The operation was rejected before dispatch",
        external_effect_started=False,
    )

    assert default.external_effect_started is True
    assert default.transient is False
    assert proven_pre_dispatch.external_effect_started is False
    with pytest.raises(ValueError, match="code"):
        error_type(code="Provider Secret!", safe_message="safe")
    with pytest.raises(ValueError, match="safe_message"):
        error_type(code="provider_failed", safe_message="")
