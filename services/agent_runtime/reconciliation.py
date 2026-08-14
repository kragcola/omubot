"""Authorized offline reconciliation for ambiguous external tool effects."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from services.agent_runtime.ledger import (
    AgentRuntimeLedger,
    ToolCallRecord,
    ToolReconciliationRecord,
)
from services.agent_runtime.policy import RuntimePrincipal

_RECONCILE_SCOPE = "runtime:tool:reconcile"


class ReconciliationDecision(StrEnum):
    CONFIRMED_SUCCEEDED = "confirmed_succeeded"
    CONFIRMED_NOT_APPLIED = "confirmed_not_applied"


@dataclass(frozen=True, slots=True)
class ReconciliationCommand:
    decision: ReconciliationDecision
    evidence_ref: str
    operator_note: str
    external_id: str = ""


@dataclass(frozen=True, slots=True)
class ReconciliationReceipt:
    decision: ReconciliationDecision
    target_ref: str
    evidence_ref: str
    external_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "decision",
            ReconciliationDecision(self.decision),
        )
        object.__setattr__(self, "target_ref", str(self.target_ref or "").strip())
        object.__setattr__(
            self,
            "evidence_ref",
            str(self.evidence_ref or "").strip(),
        )
        object.__setattr__(
            self,
            "external_id",
            str(self.external_id or "").strip(),
        )


class ReconciliationAdapter(Protocol):
    adapter_id: str
    idempotent: bool
    offline_only: bool

    def supports(self, call: ToolCallRecord) -> bool: ...

    async def reconcile(
        self,
        call: ToolCallRecord,
        command: ReconciliationCommand,
    ) -> ReconciliationReceipt: ...


class ReconciliationCoordinator:
    """Apply an idempotent domain resolution before appending Runtime truth."""

    def __init__(self, *, ledger: AgentRuntimeLedger) -> None:
        self._ledger = ledger

    async def resolve_unknown(
        self,
        *,
        call_id: str,
        decision: ReconciliationDecision | str,
        principal: RuntimePrincipal,
        evidence_ref: str,
        operator_note: str,
        adapter: ReconciliationAdapter,
        external_id: str = "",
    ) -> ToolReconciliationRecord:
        call = await self._ledger.get_tool_call(str(call_id or "").strip())
        if call is None:
            raise KeyError(str(call_id or "").strip())
        run = await self._ledger.get_run(call.run_id)
        if run is None:
            raise RuntimeError("reconciliation call run is missing")
        self._authorize(principal, target_ref=call.target_ref)
        if call.status != "unknown":
            raise ValueError("only an unknown tool call can be reconciled")
        if call.idempotency_mode != "reconcile_only":
            raise ValueError("tool call does not permit reconciliation")
        existing = await self._ledger.get_tool_reconciliation(call.call_id)
        if existing is None and run.status != "waiting_external":
            raise ValueError("tool call run is not waiting for reconciliation")

        normalized_decision = ReconciliationDecision(decision)
        clean_evidence = self._safe_ascii_ref(
            evidence_ref,
            field="evidence_ref",
            max_length=240,
        )
        clean_note = self._clean_operator_note(operator_note)
        clean_external = ""
        if str(external_id or "").strip():
            clean_external = self._safe_ascii_ref(
                external_id,
                field="external_id",
                max_length=240,
            )
        if (
            normalized_decision
            is ReconciliationDecision.CONFIRMED_NOT_APPLIED
            and clean_external
        ):
            raise ValueError("confirmed_not_applied cannot have external_id")

        adapter_id = self._safe_ascii_ref(
            getattr(adapter, "adapter_id", ""),
            field="adapter_id",
            max_length=128,
        )
        actor = self._safe_ascii_ref(
            f"{principal.kind}:{principal.principal_id}",
            field="actor",
            max_length=128,
        )
        note_digest = "sha256:" + hashlib.sha256(
            clean_note.encode("utf-8")
        ).hexdigest()
        if existing is not None:
            exact = (
                existing.decision == normalized_decision.value
                and existing.actor == actor
                and existing.adapter_id == adapter_id
                and existing.evidence_ref == clean_evidence
                and existing.note_digest == note_digest
                and (
                    not clean_external
                    or existing.external_id == clean_external
                )
            )
            if not exact:
                raise ValueError(
                    "tool call already has a different reconciliation"
                )
            return existing
        if getattr(adapter, "idempotent", False) is not True:
            raise ValueError("reconciliation adapter must be idempotent")
        if adapter.supports(call) is not True:
            raise ValueError("reconciliation adapter does not support tool call")

        command = ReconciliationCommand(
            decision=normalized_decision,
            evidence_ref=clean_evidence,
            operator_note=clean_note,
            external_id=clean_external,
        )
        receipt = await adapter.reconcile(call, command)
        self._verify_receipt(call=call, command=command, receipt=receipt)
        return await self._ledger.record_tool_reconciliation(
            call.call_id,
            decision=normalized_decision.value,
            actor=actor,
            adapter_id=adapter_id,
            evidence_ref=clean_evidence,
            note_digest=note_digest,
            external_id=receipt.external_id or None,
        )

    @staticmethod
    def _authorize(principal: RuntimePrincipal, *, target_ref: str) -> None:
        if _RECONCILE_SCOPE not in principal.granted_scopes:
            raise PermissionError("runtime principal lacks reconciliation scope")
        if target_ref not in principal.allowed_target_refs:
            raise PermissionError("runtime principal cannot reconcile target")

    @staticmethod
    def _verify_receipt(
        *,
        call: ToolCallRecord,
        command: ReconciliationCommand,
        receipt: ReconciliationReceipt,
    ) -> None:
        if not isinstance(receipt, ReconciliationReceipt):
            raise TypeError("reconciliation adapter returned an invalid receipt")
        if receipt.decision is not command.decision:
            raise ValueError("reconciliation receipt decision mismatch")
        if receipt.target_ref != call.target_ref:
            raise ValueError("reconciliation receipt target mismatch")
        if receipt.evidence_ref != command.evidence_ref:
            raise ValueError("reconciliation receipt evidence mismatch")
        if command.external_id and receipt.external_id != command.external_id:
            raise ValueError("reconciliation receipt external_id mismatch")
        if (
            command.decision is ReconciliationDecision.CONFIRMED_NOT_APPLIED
            and receipt.external_id
        ):
            raise ValueError("not-applied receipt cannot have external_id")

    @staticmethod
    def _safe_ascii_ref(value: object, *, field: str, max_length: int) -> str:
        clean = str(value or "").strip()
        allowed = (
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789:._/@+-"
        )
        if (
            not clean
            or len(clean) > max_length
            or not clean.isascii()
            or any(character not in allowed for character in clean)
        ):
            raise ValueError(f"{field} is invalid")
        return clean

    @staticmethod
    def _clean_operator_note(value: object) -> str:
        raw = str(value or "")
        if any(
            (ord(character) < 32 and character not in "\t\r\n")
            or ord(character) == 127
            for character in raw
        ):
            raise ValueError("operator_note contains control characters")
        clean = " ".join(raw.split())
        if not clean or len(clean) > 500:
            raise ValueError("operator_note must be 1..500 characters")
        return clean


class OneBotManualAttestationAdapter:
    """Record an operator-attested OneBot outcome without replaying it."""

    adapter_id = "onebot-manual-attestation-v1"
    idempotent = True
    offline_only = True
    _OWNERS = frozenset({"group_admin", "qq_interaction", "sticker"})

    def supports(self, call: ToolCallRecord) -> bool:
        return (
            call.owner in self._OWNERS
            and call.effect
            in {"external_reversible", "external_irreversible"}
            and call.target_ref.startswith("onebot:")
        )

    async def reconcile(
        self,
        call: ToolCallRecord,
        command: ReconciliationCommand,
    ) -> ReconciliationReceipt:
        if not self.supports(call):
            raise ValueError("OneBot attestation does not support tool call")
        return ReconciliationReceipt(
            decision=command.decision,
            target_ref=call.target_ref,
            evidence_ref=command.evidence_ref,
            external_id=command.external_id,
        )

__all__ = [
    "OneBotManualAttestationAdapter",
    "ReconciliationAdapter",
    "ReconciliationCommand",
    "ReconciliationCoordinator",
    "ReconciliationDecision",
    "ReconciliationReceipt",
]
