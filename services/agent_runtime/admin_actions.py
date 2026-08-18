"""Explicitly injected offline operator decisions for Agent Runtime v2."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from services.agent_runtime.ledger import (
    AgentRuntimeLedger,
    ToolCallRecord,
    ToolReconciliationRecord,
)
from services.agent_runtime.policy import RuntimePrincipal
from services.agent_runtime.reconciliation import (
    ReconciliationAdapter,
    ReconciliationCoordinator,
    ReconciliationDecision,
)
from services.memory.governance_contracts import (
    CandidateEnvelopeV1,
    PromotionEventV1,
    PromotionKind,
)
from services.memory.governance_store import MemoryGovernanceStore
from services.worldbook.governance_contracts import (
    LEGACY_SINGLETON_WORLD_ID,
    WorldbookEventProposalV1,
    WorldbookEventSource,
    WorldbookOperatorDecisionV1,
)
from services.worldbook.governance_store import WorldbookGovernanceStore

_CONTRACT_VERSION = "offline_admin_actions.v1"
_SCHEMA_VERSION = 1
_MODE = "offline_dark"
_APPROVE_SCOPE = "runtime:tool:approve"
_RECONCILE_SCOPE = "runtime:tool:reconcile"
_MEMORY_SCOPE = "memory:candidate:decide"
_WORLDBOOK_SCOPE = "worldbook:proposal:decide"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROCESS_TOKEN_KEY = secrets.token_bytes(32)
_MAX_PREVIEW_BYTES = 2_048
_MAX_PREVIEW_TEXT = 240
ResourceAuthorizerV1 = Callable[[str, str], Awaitable[bool]]


class WorldbookProposalCommitterV1(Protocol):
    """Narrow reducer port exposed only to named Worldbook decisions."""

    async def commit_approved(self, proposal_id: str) -> Any:
        ...


@dataclass(frozen=True, slots=True)
class OperatorIdentityV1:
    """Server-owned operator identity; never reconstructed from an Admin body."""

    operator_id: str
    granted_scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        operator_id = _safe_ascii(
            self.operator_id,
            field="operator_id",
            maximum=96,
        )
        scopes = tuple(
            sorted(
                {
                    _safe_ascii(value, field="operator_scope", maximum=120)
                    for value in self.granted_scopes
                }
            )
        )
        object.__setattr__(self, "operator_id", operator_id)
        object.__setattr__(self, "granted_scopes", scopes)

    @property
    def actor_ref(self) -> str:
        return f"operator:{self.operator_id}"


def _safe_ascii(value: object, *, field: str, maximum: int) -> str:
    clean = str(value or "").strip()
    if (
        not clean
        or len(clean) > maximum
        or not clean.isascii()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in clean)
    ):
        raise ValueError(f"{field} is invalid")
    return clean


def _receipt(
    *,
    decision_id: str,
    decision_type: str,
    status: str,
    exact_retry: bool,
    started_field: str,
    started: bool = False,
) -> dict[str, Any]:
    return {
        "contract_version": _CONTRACT_VERSION,
        "schema_version": _SCHEMA_VERSION,
        "mode": _MODE,
        "decision_id": decision_id,
        "decision_type": decision_type,
        "status": status,
        "exact_retry": exact_retry,
        started_field: started,
    }


def _preview_digest(preview: dict[str, Any]) -> str:
    raw = json.dumps(
        preview,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(raw) > _MAX_PREVIEW_BYTES:
        raise ValueError("operator preview exceeds its bounded contract")
    if any(
        isinstance(value, str) and len(value) > _MAX_PREVIEW_TEXT
        for value in preview.values()
    ):
        raise ValueError("operator preview text exceeds its bounded contract")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _iso_utc(value: datetime | str) -> str:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("operator context timestamp must be timezone-aware")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


class OfflineAdminActionsV1:
    """Append decisions to caller-owned dark stores without executing effects."""

    def __init__(
        self,
        runtime_source: AgentRuntimeLedger,
        memory_source: MemoryGovernanceStore,
        operator: OperatorIdentityV1,
        worldbook_source: WorldbookGovernanceStore | None = None,
        worldbook_committer: WorldbookProposalCommitterV1 | None = None,
        reconciliation_adapters: Sequence[ReconciliationAdapter] = (),
        resource_authorizer: ResourceAuthorizerV1 | None = None,
    ) -> None:
        if runtime_source is None:
            raise ValueError("runtime source is required")
        if memory_source is None:
            raise ValueError("memory source is required")
        if not isinstance(operator, OperatorIdentityV1):
            raise TypeError("operator identity is required")
        self._runtime_source = runtime_source
        self._memory_source = memory_source
        self._worldbook_source = worldbook_source
        self._worldbook_committer = worldbook_committer
        self._operator = operator
        self._reconciliation_adapters = tuple(reconciliation_adapters)
        self._resource_authorizer = resource_authorizer

    async def tool_approval_token(self, call_id: str) -> str:
        context = await self.tool_approval_context(call_id)
        return str(context["expected_token"])

    async def tool_approval_context(self, call_id: str) -> dict[str, Any]:
        self._require_runtime()
        self._require_scope(_APPROVE_SCOPE)
        call = await self._runtime_call(call_id)
        await self._require_resource(_APPROVE_SCOPE, call.target_ref)
        if call.status != "approval_pending":
            raise ValueError("tool approval context requires approval_pending state")
        preview = self._runtime_preview(call)
        digest = _preview_digest(preview)
        return self._decision_context(
            resource_kind="tool_call",
            resource_id=call.call_id,
            state=call.status,
            updated_at=call.updated_at,
            preview=preview,
            token=self._runtime_token(
                "tool_approval",
                call,
                preview_digest=digest,
            ),
        )

    async def approve_tool_call(
        self,
        call_id: str,
        *,
        expected_token: str,
        approval_ref: str,
    ) -> dict[str, Any]:
        self._require_runtime()
        self._require_scope(_APPROVE_SCOPE)
        clean_ref = _safe_ascii(approval_ref, field="approval_ref", maximum=240)
        call = await self._runtime_call(call_id)
        await self._require_resource(_APPROVE_SCOPE, call.target_ref)
        preview_digest = _preview_digest(self._runtime_preview(call))
        digest = "sha256:" + hashlib.sha256(clean_ref.encode("utf-8")).hexdigest()
        decision_id = self._decision_id("approval", call.call_id, digest)
        if call.approval_ref_digest:
            if call.approval_ref_digest != digest:
                raise ValueError("tool approval already has a different decision")
            self._verify_token(
                expected_token,
                self._runtime_token(
                    "tool_approval",
                    call,
                    preview_digest=preview_digest,
                ),
            )
            return _receipt(
                decision_id=decision_id,
                decision_type="tool_approval",
                status="recorded",
                exact_retry=True,
                started_field="execution_started",
            )
        self._verify_token(
            expected_token,
            self._runtime_token(
                "tool_approval",
                call,
                preview_digest=preview_digest,
            ),
        )
        if call.status != "approval_pending":
            raise ValueError("tool approval state is stale or not pending")
        await self._runtime_source.record_tool_call_approval(
            call.call_id,
            approval_ref_digest=digest,
            actor=self._operator.actor_ref,
        )
        return _receipt(
            decision_id=decision_id,
            decision_type="tool_approval",
            status="recorded",
            exact_retry=False,
            started_field="execution_started",
        )

    async def reconciliation_token(self, call_id: str) -> str:
        context = await self.reconciliation_context(call_id)
        return str(context["expected_token"])

    async def reconciliation_context(self, call_id: str) -> dict[str, Any]:
        self._require_runtime()
        self._require_scope(_RECONCILE_SCOPE)
        call = await self._runtime_call(call_id)
        await self._require_resource(_RECONCILE_SCOPE, call.target_ref)
        run = await self._runtime_source.get_run(call.run_id)
        if run is None:
            raise RuntimeError("reconciliation run is missing")
        if call.status != "unknown" or run.status != "waiting_external":
            raise ValueError("reconciliation context requires waiting external state")
        preview = self._runtime_preview(call)
        digest = _preview_digest(preview)
        return self._decision_context(
            resource_kind="tool_call",
            resource_id=call.call_id,
            state=call.status,
            updated_at=call.updated_at,
            preview=preview,
            token=self._runtime_token(
                "tool_reconciliation",
                call,
                run_status=run.status,
                preview_digest=digest,
            ),
        )

    async def reconcile_tool_call(
        self,
        call_id: str,
        *,
        expected_token: str,
        decision: str,
        evidence_ref: str,
        operator_note: str,
        external_id: str = "",
    ) -> dict[str, Any]:
        self._require_runtime()
        self._require_scope(_RECONCILE_SCOPE)
        call = await self._runtime_call(call_id)
        await self._require_resource(_RECONCILE_SCOPE, call.target_ref)
        run = await self._runtime_source.get_run(call.run_id)
        if run is None:
            raise RuntimeError("reconciliation run is missing")
        preview_digest = _preview_digest(self._runtime_preview(call))
        normalized = ReconciliationDecision(decision)
        clean_evidence = _safe_ascii(
            evidence_ref,
            field="evidence_ref",
            maximum=240,
        )
        clean_note = " ".join(str(operator_note or "").split())
        if not clean_note or len(clean_note) > 500:
            raise ValueError("operator_note must be 1..500 characters")
        clean_external = ""
        if str(external_id or "").strip():
            clean_external = _safe_ascii(
                external_id,
                field="external_id",
                maximum=240,
            )
        note_digest = "sha256:" + hashlib.sha256(clean_note.encode()).hexdigest()
        existing = await self._runtime_source.get_tool_reconciliation(call.call_id)
        if existing is not None:
            adapter = self._adapter_for_existing(existing, call)
            self._verify_reconciliation_retry(
                existing,
                decision=normalized,
                evidence_ref=clean_evidence,
                note_digest=note_digest,
                external_id=clean_external,
                adapter=adapter,
            )
            prior_token = self._runtime_token(
                "tool_reconciliation",
                call,
                run_status="waiting_external",
                preview_digest=preview_digest,
            )
            self._verify_token(expected_token, prior_token)
            return self._reconciliation_receipt(existing, exact_retry=True)

        current_token = self._runtime_token(
            "tool_reconciliation",
            call,
            run_status=run.status,
            preview_digest=preview_digest,
        )
        self._verify_token(expected_token, current_token)
        if call.status != "unknown" or run.status != "waiting_external":
            raise ValueError("reconciliation state is stale or not waiting")
        adapter = self._select_offline_adapter(call)
        principal = RuntimePrincipal(
            kind="operator",
            principal_id=self._operator.operator_id,
            granted_scopes=self._operator.granted_scopes,
            allowed_target_refs=(call.target_ref,),
        )
        record = await ReconciliationCoordinator(
            ledger=self._runtime_source
        ).resolve_unknown(
            call_id=call.call_id,
            decision=normalized,
            principal=principal,
            evidence_ref=clean_evidence,
            operator_note=clean_note,
            adapter=adapter,
            external_id=clean_external,
        )
        return self._reconciliation_receipt(record, exact_retry=False)

    async def memory_candidate_token(self, candidate_id: str) -> str:
        context = await self.memory_candidate_context(candidate_id)
        return str(context["expected_token"])

    async def memory_candidate_context(self, candidate_id: str) -> dict[str, Any]:
        self._require_memory()
        self._require_scope(_MEMORY_SCOPE)
        candidate = await self._memory_candidate(candidate_id)
        await self._require_resource(
            _MEMORY_SCOPE,
            f"memory_candidate:{candidate.candidate_id}",
        )
        event_ids, conflict_ids = await self._memory_state(candidate.candidate_id)
        state = "pending" if not event_ids else "decided"
        preview = self._memory_preview(candidate, event_ids, conflict_ids)
        digest = _preview_digest(preview)
        return self._decision_context(
            resource_kind="memory_candidate",
            resource_id=candidate.candidate_id,
            state=state,
            updated_at=candidate.produced_at,
            preview=preview,
            token=self._memory_token(
                candidate,
                event_ids,
                conflict_ids,
                preview_digest=digest,
            ),
        )

    async def decide_memory_candidate(
        self,
        candidate_id: str,
        *,
        expected_token: str,
        decision: str,
        reason_code: str,
        operator_note: str = "",
        occurred_at: datetime,
    ) -> dict[str, Any]:
        self._require_memory()
        self._require_scope(_MEMORY_SCOPE)
        candidate = await self._memory_candidate(candidate_id)
        await self._require_resource(
            _MEMORY_SCOPE,
            f"memory_candidate:{candidate.candidate_id}",
        )
        event_kind, status = self._memory_decision(decision)
        current_event_ids, current_conflicts = await self._memory_state(
            candidate.candidate_id
        )
        current_preview_digest = _preview_digest(
            self._memory_preview(candidate, current_event_ids, current_conflicts)
        )
        event = PromotionEventV1.create(
            candidate_id=candidate.candidate_id,
            candidate_sha256=candidate.candidate_sha256,
            event_kind=event_kind,
            actor_kind="operator",
            actor_ref=self._operator.actor_ref,
            occurred_at=occurred_at,
            reason_code=reason_code,
            operator_note=operator_note,
            conflict_ids=current_conflicts,
            projection_kind=candidate.proposal.projection_kind,
            operation=candidate.proposal.operation,
            projection_ref=None,
            receipt_ref=None,
        )
        existing_events = await self._memory_source.list_promotion_events(
            candidate.candidate_id
        )
        for existing in existing_events:
            if existing.event_id == event.event_id:
                prior_ids = tuple(
                    value for value in current_event_ids if value != event.event_id
                )
                self._verify_token(
                    expected_token,
                    self._memory_token(
                        candidate,
                        prior_ids,
                        current_conflicts,
                        preview_digest=_preview_digest(
                            self._memory_preview(
                                candidate,
                                prior_ids,
                                current_conflicts,
                            )
                        ),
                    ),
                )
                return self._memory_receipt(event, status=status, exact_retry=True)
        if any(
            existing.event_kind
            in {
                PromotionKind.PROMOTION_APPROVED,
                PromotionKind.PROMOTION_REJECTED,
            }
            for existing in existing_events
        ):
            raise ValueError("candidate already has a different promotion decision")
        self._verify_token(
            expected_token,
            self._memory_token(
                candidate,
                current_event_ids,
                current_conflicts,
                preview_digest=current_preview_digest,
            ),
        )
        persisted, inserted = (
            await self._memory_source.append_promotion_event_with_outcome(event)
        )
        return self._memory_receipt(
            persisted,
            status=status,
            exact_retry=not inserted,
        )

    async def worldbook_proposal_context(self, proposal_id: str) -> dict[str, Any]:
        """Return one redacted, exact-proposal decision context."""
        self._require_worldbook()
        self._require_scope(_WORLDBOOK_SCOPE)
        proposal = await self._worldbook_schedule_proposal(proposal_id)
        await self._require_resource(
            _WORLDBOOK_SCOPE,
            self._worldbook_resource(proposal.proposal_id),
        )
        decision, receipt = await self._worldbook_state(proposal.proposal_id)
        preview = self._worldbook_preview(proposal, decision, receipt)
        return self._decision_context(
            resource_kind="worldbook_proposal",
            resource_id=proposal.proposal_id,
            state=self._worldbook_status(decision, receipt),
            updated_at=(
                decision.decided_at if decision is not None else proposal.proposed_at
            ),
            preview=preview,
            token=self._worldbook_token(
                proposal,
                decision,
                receipt,
                preview_digest=_preview_digest(preview),
            ),
        )

    async def decide_worldbook_proposal(
        self,
        proposal_id: str,
        *,
        expected_token: str,
        decision: str,
        reason_code: str,
    ) -> dict[str, Any]:
        """Persist one named Schedule decision and resume an approved commit."""
        self._require_worldbook()
        self._require_scope(_WORLDBOOK_SCOPE)
        proposal = await self._worldbook_schedule_proposal(proposal_id)
        await self._require_resource(
            _WORLDBOOK_SCOPE,
            self._worldbook_resource(proposal.proposal_id),
        )
        normalized_decision = self._worldbook_decision(decision)
        clean_reason = _safe_ascii(
            reason_code,
            field="reason_code",
            maximum=120,
        )
        existing, receipt = await self._worldbook_state(proposal.proposal_id)
        exact_retry = False
        if existing is not None:
            preview = self._worldbook_preview(proposal, existing, receipt)
            self._verify_token(
                expected_token,
                self._worldbook_token(
                    proposal,
                    existing,
                    receipt,
                    preview_digest=_preview_digest(preview),
                ),
            )
            if not self._same_worldbook_decision(
                existing,
                decision=normalized_decision,
                reason_code=clean_reason,
            ):
                raise ValueError("Worldbook proposal already has another decision")
            persisted = existing
            exact_retry = True
        else:
            preview = self._worldbook_preview(proposal, None, None)
            self._verify_token(
                expected_token,
                self._worldbook_token(
                    proposal,
                    None,
                    None,
                    preview_digest=_preview_digest(preview),
                ),
            )
            if normalized_decision == "approve" and self._worldbook_committer is None:
                raise RuntimeError("Worldbook schedule committer is unavailable")
            candidate = WorldbookOperatorDecisionV1.create(
                proposal_id=proposal.proposal_id,
                decision=normalized_decision,
                reason_code=clean_reason,
                operator_ref=self._operator.actor_ref,
                decided_at=datetime.now(UTC),
            )
            try:
                persisted = await self._require_worldbook().append_operator_decision(candidate)
            except ValueError:
                raced, receipt = await self._worldbook_state(proposal.proposal_id)
                if raced is None or not self._same_worldbook_decision(
                    raced,
                    decision=normalized_decision,
                    reason_code=clean_reason,
                ):
                    raise
                persisted = raced
                exact_retry = True

        if persisted.decision == "reject":
            return self._worldbook_receipt(
                persisted,
                status="rejected",
                exact_retry=exact_retry,
                commit_status="not_requested",
                receipt_present=receipt is not None,
                reducer_commit_requested=False,
            )

        if receipt is not None:
            return self._worldbook_receipt(
                persisted,
                status="committed",
                exact_retry=True,
                commit_status="committed",
                receipt_present=True,
                reducer_commit_requested=False,
            )

        committer = self._worldbook_committer
        if committer is None:
            raise RuntimeError("Worldbook schedule committer is unavailable")
        outcome = await committer.commit_approved(proposal.proposal_id)
        commit_status = str(getattr(outcome, "status", "") or "blocked")
        receipt = await self._require_worldbook().get_commit_receipt(proposal.proposal_id)
        receipt_present = receipt is not None
        return self._worldbook_receipt(
            persisted,
            status="committed" if receipt_present else "approved_pending",
            exact_retry=exact_retry,
            commit_status=commit_status,
            receipt_present=receipt_present,
            reducer_commit_requested=True,
        )

    def _require_runtime(self) -> None:
        if getattr(self._runtime_source, "_db", None) is None:
            raise RuntimeError("runtime source is not available or initialized")

    def _require_memory(self) -> None:
        if getattr(self._memory_source, "_db", None) is None:
            raise RuntimeError("memory source is not available or initialized")

    def _require_worldbook(self) -> WorldbookGovernanceStore:
        source = self._worldbook_source
        if source is None or getattr(source, "_db", None) is None:
            raise RuntimeError("Worldbook source is not available or initialized")
        return source

    def _require_scope(self, scope: str) -> None:
        if scope not in self._operator.granted_scopes:
            raise PermissionError("operator scope is missing")

    async def _require_resource(self, scope: str, resource_ref: str) -> None:
        authorizer = self._resource_authorizer
        if authorizer is None:
            return
        if await authorizer(scope, resource_ref) is not True:
            raise PermissionError("operator resource is unavailable")

    async def _runtime_call(self, call_id: str) -> ToolCallRecord:
        clean_call_id = _safe_ascii(call_id, field="call_id", maximum=128)
        call = await self._runtime_source.get_tool_call(clean_call_id)
        if call is None:
            raise KeyError(clean_call_id)
        return call

    async def _memory_candidate(self, candidate_id: str) -> CandidateEnvelopeV1:
        clean_candidate_id = _safe_ascii(
            candidate_id,
            field="candidate_id",
            maximum=128,
        )
        candidate = await self._memory_source.get_candidate(clean_candidate_id)
        if candidate is None:
            raise KeyError(clean_candidate_id)
        return candidate

    async def _worldbook_schedule_proposal(
        self,
        proposal_id: str,
    ) -> WorldbookEventProposalV1:
        clean_proposal_id = _safe_ascii(
            proposal_id,
            field="proposal_id",
            maximum=128,
        )
        proposal = await self._require_worldbook().get_proposal(clean_proposal_id)
        if proposal is None:
            raise KeyError(clean_proposal_id)
        if (
            proposal.source is not WorldbookEventSource.SCHEDULE
            or proposal.world_ref.world_id != LEGACY_SINGLETON_WORLD_ID
        ):
            raise ValueError("Worldbook proposal is not a singleton Schedule proposal")
        return proposal

    async def _worldbook_state(
        self,
        proposal_id: str,
    ) -> tuple[Any | None, Any | None]:
        source = self._require_worldbook()
        decision = await source.get_operator_decision(proposal_id)
        receipt = await source.get_commit_receipt(proposal_id)
        return decision, receipt

    async def _memory_state(
        self,
        candidate_id: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        db = getattr(self._memory_source, "_db", None)
        lock = getattr(self._memory_source, "_write_lock", None)
        if db is None or lock is None:
            raise RuntimeError("memory source is not available or initialized")
        async with lock:
            event_cursor = await db.execute(
                """
                SELECT event_id
                FROM memory_governance_promotion_events
                WHERE candidate_id = ?
                ORDER BY event_seq
                """,
                (candidate_id,),
            )
            try:
                event_rows = await event_cursor.fetchall()
            finally:
                await event_cursor.close()
            conflict_cursor = await db.execute(
                """
                SELECT conflict_id
                FROM memory_governance_conflict_candidates
                WHERE candidate_id = ?
                UNION
                SELECT conflict_observations.conflict_id
                FROM memory_governance_conflict_observations AS conflict_observations
                JOIN memory_governance_candidates AS candidates
                  ON candidates.observation_id = conflict_observations.observation_id
                WHERE candidates.candidate_id = ?
                UNION
                SELECT conflicts.conflict_id
                FROM memory_governance_conflicts AS conflicts
                JOIN json_each(
                    conflicts.payload_json,
                    '$.candidate_ids'
                ) AS payload_candidates
                  ON payload_candidates.value = ?
                UNION
                SELECT conflicts.conflict_id
                FROM memory_governance_conflicts AS conflicts
                JOIN json_each(
                    conflicts.payload_json,
                    '$.observation_ids'
                ) AS payload_observations
                JOIN memory_governance_candidates AS candidates
                  ON candidates.observation_id = payload_observations.value
                WHERE candidates.candidate_id = ?
                ORDER BY conflict_id
                """,
                (candidate_id, candidate_id, candidate_id, candidate_id),
            )
            try:
                conflict_rows = await conflict_cursor.fetchall()
            finally:
                await conflict_cursor.close()
        return (
            tuple(str(row["event_id"]) for row in event_rows),
            tuple(str(row["conflict_id"]) for row in conflict_rows),
        )

    def _runtime_token(
        self,
        kind: str,
        call: ToolCallRecord,
        *,
        run_status: str = "",
        preview_digest: str,
    ) -> str:
        return self._token(
            kind,
            {
                "operator": self._operator.operator_id,
                "call_id": call.call_id,
                "run_id": call.run_id,
                "tool_name": call.tool_name,
                "tool_version": call.tool_version,
                "effect": call.effect,
                "status": call.status,
                "target_ref": call.target_ref,
                "args_digest": call.args_digest,
                "run_status": run_status,
                "preview_digest": preview_digest,
            },
        )

    def _memory_token(
        self,
        candidate: CandidateEnvelopeV1,
        event_ids: Sequence[str],
        conflict_ids: Sequence[str],
        *,
        preview_digest: str,
    ) -> str:
        return self._token(
            "memory_candidate",
            {
                "operator": self._operator.operator_id,
                "candidate_id": candidate.candidate_id,
                "candidate_sha256": candidate.candidate_sha256,
                "event_ids": tuple(event_ids),
                "conflict_ids": tuple(conflict_ids),
                "preview_digest": preview_digest,
            },
        )

    def _worldbook_token(
        self,
        proposal: WorldbookEventProposalV1,
        decision: Any | None,
        receipt: Any | None,
        *,
        preview_digest: str,
    ) -> str:
        return self._token(
            "worldbook_proposal",
            {
                "operator": self._operator.operator_id,
                "proposal_id": proposal.proposal_id,
                "proposal_sha256": proposal.proposal_sha256,
                "world_id": proposal.world_ref.world_id,
                "arc_id": proposal.world_ref.arc_id,
                "event_id": proposal.event.event_id,
                "schedule_date": str(
                    getattr(proposal.source_binding, "schedule_date", "") or ""
                ),
                "decision_id": str(getattr(decision, "decision_id", "") or ""),
                "decision": str(getattr(decision, "decision", "") or ""),
                "decision_sha256": str(
                    getattr(decision, "record_sha256", "") or ""
                ),
                "receipt_sha256": str(
                    getattr(receipt, "receipt_sha256", "") or ""
                ),
                "preview_digest": preview_digest,
            },
        )

    @staticmethod
    def _runtime_preview(call: ToolCallRecord) -> dict[str, Any]:
        target_class = str(call.target_ref or "").partition(":")[0].strip() or "none"
        return {
            "tool_name": str(call.tool_name)[:_MAX_PREVIEW_TEXT],
            "tool_version": str(call.tool_version)[:_MAX_PREVIEW_TEXT],
            "owner": str(call.owner)[:_MAX_PREVIEW_TEXT],
            "effect": str(call.effect)[:_MAX_PREVIEW_TEXT],
            "idempotency_mode": str(call.idempotency_mode)[:_MAX_PREVIEW_TEXT],
            "target_class": target_class[:_MAX_PREVIEW_TEXT],
            "target_fingerprint": _fingerprint(str(call.target_ref)),
            "argument_summary": "sealed_digest_only",
            "argument_fingerprint": _fingerprint(str(call.args_digest)),
        }

    @staticmethod
    def _memory_preview(
        candidate: CandidateEnvelopeV1,
        event_ids: Sequence[str],
        conflict_ids: Sequence[str],
    ) -> dict[str, Any]:
        observation = candidate.observation
        return {
            "projection_kind": candidate.proposal.projection_kind.value,
            "operation": candidate.proposal.operation.value,
            "source_kind": observation.source_kind.value,
            "producer_kind": observation.producer_kind.value,
            "owner_scope": observation.owner_scope.value,
            "visibility": observation.visibility.value,
            "evidence_count": len(observation.evidence),
            "conflict_count": len(conflict_ids),
            "conflict_ids": list(conflict_ids),
            "decision_event_count": len(event_ids),
            "produced_at": _iso_utc(candidate.produced_at),
        }

    @staticmethod
    def _worldbook_preview(
        proposal: WorldbookEventProposalV1,
        decision: Any | None,
        receipt: Any | None,
    ) -> dict[str, Any]:
        return {
            "source_kind": proposal.source.value,
            "world_id": proposal.world_ref.world_id,
            "arc_id": proposal.world_ref.arc_id,
            "event_id": proposal.event.event_id,
            "schedule_date": str(
                getattr(proposal.source_binding, "schedule_date", "") or ""
            ),
            "summary_sha256": str(
                getattr(proposal.source_binding, "summary_sha256", "") or ""
            ),
            "decision": str(getattr(decision, "decision", "") or "pending"),
            "receipt_present": receipt is not None,
        }

    @staticmethod
    def _worldbook_resource(proposal_id: str) -> str:
        return f"worldbook_proposal:{proposal_id}"

    @staticmethod
    def _worldbook_status(decision: Any | None, receipt: Any | None) -> str:
        if receipt is not None:
            return "committed"
        if decision is None:
            return "pending"
        return "approved" if decision.decision == "approve" else "rejected"

    @staticmethod
    def _worldbook_decision(value: str) -> str:
        if value not in {"approve", "reject"}:
            raise ValueError("unknown Worldbook decision")
        return value

    def _same_worldbook_decision(
        self,
        existing: Any,
        *,
        decision: str,
        reason_code: str,
    ) -> bool:
        return (
            str(getattr(existing, "decision", "") or "") == decision
            and str(getattr(existing, "reason_code", "") or "") == reason_code
            and str(getattr(existing, "operator_ref", "") or "")
            == self._operator.actor_ref
        )

    def _decision_context(
        self,
        *,
        resource_kind: str,
        resource_id: str,
        state: str,
        updated_at: datetime | str,
        preview: dict[str, Any],
        token: str,
    ) -> dict[str, Any]:
        digest = _preview_digest(preview)
        return {
            "contract_version": _CONTRACT_VERSION,
            "schema_version": _SCHEMA_VERSION,
            "mode": _MODE,
            "report_only": True,
            "resource": {"kind": resource_kind, "id": resource_id},
            "state": state,
            "updated_at": _iso_utc(updated_at),
            "preview": preview,
            "preview_digest": digest,
            "expected_token": token,
        }

    def _token(self, kind: str, material: dict[str, Any]) -> str:
        raw = json.dumps(
            {"v": 1, "kind": kind, "material": material},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        digest = hmac.new(_PROCESS_TOKEN_KEY, raw, hashlib.sha256).hexdigest()
        return f"oadm1_{digest}"

    @staticmethod
    def _verify_token(actual: object, expected: str) -> None:
        clean = str(actual or "").strip()
        if not clean or not hmac.compare_digest(clean, expected):
            raise ValueError("operator decision token is stale or invalid")

    def _select_offline_adapter(self, call: ToolCallRecord) -> ReconciliationAdapter:
        supporting = [
            adapter
            for adapter in self._reconciliation_adapters
            if adapter.supports(call) is True
        ]
        if len(supporting) != 1:
            raise ValueError("reconciliation requires exactly one supporting adapter")
        adapter = supporting[0]
        if getattr(adapter, "offline_only", False) is not True:
            raise ValueError("reconciliation adapter must be offline only")
        if getattr(adapter, "idempotent", False) is not True:
            raise ValueError("reconciliation adapter must be idempotent")
        return adapter

    def _adapter_for_existing(
        self,
        record: ToolReconciliationRecord,
        call: ToolCallRecord,
    ) -> ReconciliationAdapter:
        matches = [
            adapter
            for adapter in self._reconciliation_adapters
            if str(getattr(adapter, "adapter_id", "")) == record.adapter_id
            and adapter.supports(call) is True
        ]
        if len(matches) != 1:
            raise ValueError("existing reconciliation adapter is unavailable")
        adapter = matches[0]
        if getattr(adapter, "offline_only", False) is not True:
            raise ValueError("reconciliation adapter must be offline only")
        if getattr(adapter, "idempotent", False) is not True:
            raise ValueError("reconciliation adapter must be idempotent")
        return adapter

    def _verify_reconciliation_retry(
        self,
        record: ToolReconciliationRecord,
        *,
        decision: ReconciliationDecision,
        evidence_ref: str,
        note_digest: str,
        external_id: str,
        adapter: ReconciliationAdapter,
    ) -> None:
        if not _DIGEST_RE.fullmatch(note_digest):
            raise ValueError("invalid reconciliation note digest")
        if (
            record.decision != decision.value
            or record.actor != self._operator.actor_ref
            or record.adapter_id != str(adapter.adapter_id)
            or record.evidence_ref != evidence_ref
            or record.note_digest != note_digest
            or record.external_id != external_id
        ):
            raise ValueError("reconciliation already has a different decision")

    def _reconciliation_receipt(
        self,
        record: ToolReconciliationRecord,
        *,
        exact_retry: bool,
    ) -> dict[str, Any]:
        return _receipt(
            decision_id=self._decision_id(
                "reconciliation",
                str(record.event_id),
                record.call_id,
                record.decision,
            ),
            decision_type="tool_reconciliation",
            status=record.decision,
            exact_retry=exact_retry,
            started_field="execution_started",
        )

    @staticmethod
    def _memory_decision(value: str) -> tuple[PromotionKind, str]:
        if value == "approve":
            return PromotionKind.PROMOTION_APPROVED, "approved"
        if value == "reject":
            return PromotionKind.PROMOTION_REJECTED, "rejected"
        raise ValueError("unknown memory decision")

    @staticmethod
    def _memory_receipt(
        event: PromotionEventV1,
        *,
        status: str,
        exact_retry: bool,
    ) -> dict[str, Any]:
        return _receipt(
            decision_id=event.event_id,
            decision_type=event.event_kind.value,
            status=status,
            exact_retry=exact_retry,
            started_field="projection_started",
        )

    @staticmethod
    def _worldbook_receipt(
        decision: WorldbookOperatorDecisionV1,
        *,
        status: str,
        exact_retry: bool,
        commit_status: str,
        receipt_present: bool,
        reducer_commit_requested: bool,
    ) -> dict[str, Any]:
        return {
            **_receipt(
                decision_id=decision.decision_id,
                decision_type="worldbook_schedule_decision",
                status=status,
                exact_retry=exact_retry,
                started_field="reducer_commit_requested",
                started=reducer_commit_requested,
            ),
            "reducer_commit_completed": receipt_present,
            "commit_status": commit_status,
            "receipt_present": receipt_present,
        }

    @staticmethod
    def _decision_id(kind: str, *values: str) -> str:
        digest = hashlib.sha256(
            json.dumps((kind, *values), separators=(",", ":")).encode()
        ).hexdigest()
        return f"oadm_{digest[:24]}"


__all__ = ["OfflineAdminActionsV1", "OperatorIdentityV1"]
