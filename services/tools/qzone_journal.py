"""Governed Agent Runtime adapter for QZone journal publication."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from kernel.types import (
    ExternalEffectPreDispatchError,
    ToolApproval,
    ToolConcurrency,
    ToolEffect,
    ToolExecutionError,
    ToolIdempotency,
    ToolInvocationBinding,
    ToolRetryPolicy,
    ToolSpec,
)
from services.agent_runtime.ledger import ToolCallRecord
from services.agent_runtime.reconciliation import (
    ReconciliationCommand,
    ReconciliationDecision,
    ReconciliationReceipt,
)
from services.tools.base import Tool
from services.tools.context import ToolContext

_DRAFT_ID_RE = re.compile(r"^qzd_[0-9a-f]{24}$")
_QZONE_TARGET_RE = re.compile(
    r"^qzone:draft:(qzd_[0-9a-f]{24}):publish$"
)


class QZonePublishDraftTool(Tool):
    """Publish one trusted, separately approved QZone journal draft."""

    def __init__(self, delivery: Any) -> None:
        self._delivery = delivery

    @property
    def name(self) -> str:
        return "qzone_publish_draft"

    @property
    def description(self) -> str:
        return "发布一条已由空间日志审核流程批准的日志草稿。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "draft_id": {
                    "type": "string",
                    "pattern": r"^qzd_[0-9a-f]{24}$",
                    "description": "已批准且由运行时提供的空间日志草稿 ID",
                },
            },
            "required": ["draft_id"],
            "additionalProperties": False,
        }

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=dict(self.parameters),
            owner="qzone_journal",
            effect=ToolEffect.EXTERNAL_IRREVERSIBLE,
            required_scopes=("qzone:journal:publish",),
            credential_mode="ephemeral_broker",
            approval=ToolApproval.ALWAYS,
            idempotency=ToolIdempotency.RECONCILE_ONLY,
            retry_policy=ToolRetryPolicy.NEVER,
            concurrency=ToolConcurrency.KEYED_SERIAL,
            binding_required=True,
            data_classification=("qzone_journal_public_post",),
        )

    def bind_invocation(
        self,
        ctx: ToolContext,
        arguments: Mapping[str, Any] | dict[str, Any],
    ) -> ToolInvocationBinding:
        draft_id = self._trusted_draft_id(ctx, arguments.get("draft_id"))
        target = f"qzone:draft:{draft_id}:publish"
        return ToolInvocationBinding(target_ref=target, concurrency_key=target)

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        if not all(
            (
                str(ctx.run_id or "").strip(),
                str(ctx.call_id or "").strip(),
                str(ctx.principal_kind or "").strip(),
                str(ctx.principal_id or "").strip(),
            )
        ):
            raise ToolExecutionError(
                code="governed_runtime_required",
                safe_message="A governed Agent Runtime context is required",
                external_effect_started=False,
            )
        draft_id = self._trusted_draft_id(ctx, kwargs.get("draft_id"))
        try:
            result = await self._delivery.deliver(draft_id)
        except ExternalEffectPreDispatchError as exc:
            raise ToolExecutionError(
                code="qzone_publish_precondition_failed",
                safe_message="QZone publish was rejected before dispatch",
                external_effect_started=False,
            ) from exc
        if isinstance(result, dict):
            raise ToolExecutionError(
                code="qzone_live_publish_not_configured",
                safe_message="QZone live publish is not configured",
                external_effect_started=False,
            )
        status = str(getattr(result, "status", "") or "")
        result_draft_id = str(getattr(result, "draft_id", "") or "")
        if status != "published" or result_draft_id != draft_id:
            raise RuntimeError("QZone delivery returned an unverified final state")
        return json.dumps(
            {"draft_id": draft_id, "status": "published"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _trusted_draft_id(ctx: ToolContext, value: Any) -> str:
        draft_id = str(value or "").strip()
        if _DRAFT_ID_RE.fullmatch(draft_id) is None:
            raise ValueError("QZone draft_id is invalid")
        trusted_raw = ctx.extra.get("qzone_draft_ids")
        if not isinstance(trusted_raw, (list, tuple, set, frozenset)):
            raise ValueError("trusted QZone draft allowlist is missing")
        trusted_ids: set[str] = set()
        for item in trusted_raw:
            trusted_id = str(item or "").strip()
            if _DRAFT_ID_RE.fullmatch(trusted_id) is None:
                raise ValueError("trusted QZone draft allowlist is invalid")
            trusted_ids.add(trusted_id)
        if draft_id not in trusted_ids:
            raise ValueError("QZone draft_id is not trusted for this run")
        return draft_id


class QZoneReconciliationAdapter:
    """Apply an idempotent QZone domain resolution before Runtime audit."""

    adapter_id = "qzone-journal-store-v1"
    idempotent = True
    offline_only = True

    def __init__(self, store: Any) -> None:
        self._store = store

    def supports(self, call: ToolCallRecord) -> bool:
        return (
            call.owner == "qzone_journal"
            and call.tool_name == "qzone_publish_draft"
            and call.effect == "external_irreversible"
            and _QZONE_TARGET_RE.fullmatch(call.target_ref) is not None
        )

    async def reconcile(
        self,
        call: ToolCallRecord,
        command: ReconciliationCommand,
    ) -> ReconciliationReceipt:
        if not self.supports(call):
            raise ValueError("QZone adapter does not support tool call")
        target_match = _QZONE_TARGET_RE.fullmatch(call.target_ref)
        if target_match is None:  # pragma: no cover - guarded by supports
            raise ValueError("QZone reconciliation target is invalid")
        draft_id = target_match.group(1)
        if command.decision is ReconciliationDecision.CONFIRMED_SUCCEEDED:
            if not command.external_id:
                raise ValueError(
                    "confirmed QZone publication requires external_id"
                )
            draft = await self._store.confirm_published(
                draft_id,
                note=command.operator_note,
                external_post_id=command.external_id,
            )
            expected_status = "published"
        else:
            draft = await self._store.confirm_not_published(
                draft_id,
                note=command.operator_note,
            )
            expected_status = "approved"
        if (
            str(getattr(draft, "draft_id", "") or "") != draft_id
            or str(getattr(draft, "status", "") or "") != expected_status
        ):
            raise RuntimeError(
                "QZone domain reconciliation returned an invalid state"
            )
        return ReconciliationReceipt(
            decision=command.decision,
            target_ref=call.target_ref,
            evidence_ref=command.evidence_ref,
            external_id=command.external_id,
        )


__all__ = ["QZonePublishDraftTool", "QZoneReconciliationAdapter"]
