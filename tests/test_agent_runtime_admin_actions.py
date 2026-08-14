"""Behavioral RED contracts for offline P5 operator decisions."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import inspect
import json
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from services.agent_runtime.coordinator import RunCoordinator
from services.agent_runtime.executor import EffectExecutor
from services.agent_runtime.ledger import AgentRuntimeLedger
from services.agent_runtime.reconciliation import (
    ReconciliationCoordinator,
    ReconciliationReceipt,
)
from services.memory.governance_contracts import (
    CandidateEnvelopeV1,
    CardClaimV1,
    ConflictV1,
    EvidenceAtomV1,
    ObservationV1,
    OwnerScope,
    ProducerKind,
    ProjectionProposalV1,
    PromotionEventV1,
    SourceKind,
    TimeBasis,
    Visibility,
    sha256_text,
)
from services.memory.governance_store import MemoryGovernanceStore

CONTRACT_VERSION = "offline_admin_actions.v1"
SCHEMA_VERSION = 1
MODE = "offline_dark"
T0 = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)
OPERATOR_ID = "offline-reviewer-7"
OPERATOR_SCOPES = (
    "memory:candidate:decide",
    "runtime:tool:approve",
    "runtime:tool:reconcile",
)
FORBIDDEN_KEY_FRAGMENTS = (
    "operator",
    "principal",
    "target",
    "argument",
    "args",
    "approval_ref",
    "evidence",
    "note",
    "payload",
)


def _placeholder_envelope() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "decision_id": "not-implemented",
        "decision_type": "not_implemented",
        "status": "not_implemented",
        "exact_retry": False,
    }


@pytest.fixture
def actions_api(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Keep the proposed module importable until its implementation lands."""

    module_name = "services.agent_runtime.admin_actions"
    if importlib.util.find_spec(module_name) is None:
        placeholder = ModuleType(module_name)

        class OperatorIdentityV1:
            def __init__(
                self,
                operator_id: str,
                granted_scopes: Sequence[str],
            ) -> None:
                self.operator_id = operator_id
                self.granted_scopes = tuple(granted_scopes)

        class OfflineAdminActionsV1:
            def __init__(
                self,
                runtime_source: Any,
                memory_source: Any,
                operator: Any,
                reconciliation_adapters: Sequence[Any] = (),
            ) -> None:
                self._runtime_source = runtime_source
                self._memory_source = memory_source
                self._operator = operator
                self._reconciliation_adapters = tuple(reconciliation_adapters)

            async def tool_approval_token(self, call_id: str) -> str:
                return "opaque-placeholder-token"

            async def approve_tool_call(
                self,
                call_id: str,
                *,
                expected_token: str,
                approval_ref: str,
            ) -> dict[str, Any]:
                return {**_placeholder_envelope(), "execution_started": False}

            async def reconciliation_token(self, call_id: str) -> str:
                return "opaque-placeholder-token"

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
                return {**_placeholder_envelope(), "execution_started": False}

            async def memory_candidate_token(self, candidate_id: str) -> str:
                return "opaque-placeholder-token"

            async def decide_memory_candidate(
                self,
                candidate_id: str,
                *,
                expected_token: str,
                decision: str,
                reason_code: str,
                operator_note: str = "",
                occurred_at: datetime = T0,
            ) -> dict[str, Any]:
                return {**_placeholder_envelope(), "projection_started": False}

        placeholder.__dict__["OperatorIdentityV1"] = OperatorIdentityV1
        placeholder.__dict__["OfflineAdminActionsV1"] = OfflineAdminActionsV1
        monkeypatch.setitem(sys.modules, module_name, placeholder)

    module = importlib.import_module(module_name)
    assert isinstance(getattr(module, "OperatorIdentityV1", None), type)
    assert isinstance(getattr(module, "OfflineAdminActionsV1", None), type)
    return module


@pytest.fixture
async def opened_sources(
    tmp_path: Path,
) -> AsyncIterator[tuple[AgentRuntimeLedger, MemoryGovernanceStore]]:
    runtime = AgentRuntimeLedger(tmp_path / "runtime.db")
    memory = MemoryGovernanceStore(tmp_path / "memory.db")
    await runtime.init()
    await memory.init()
    try:
        yield runtime, memory
    finally:
        await runtime.close()
        await memory.close()


def _new_actions(
    api: ModuleType,
    runtime: AgentRuntimeLedger,
    memory: MemoryGovernanceStore,
    *,
    adapters: Sequence[Any] = (),
    operator_id: str = OPERATOR_ID,
    scopes: Sequence[str] = OPERATOR_SCOPES,
) -> Any:
    operator = api.OperatorIdentityV1(
        operator_id=operator_id,
        granted_scopes=tuple(scopes),
    )
    return api.OfflineAdminActionsV1(
        runtime_source=runtime,
        memory_source=memory,
        operator=operator,
        reconciliation_adapters=tuple(adapters),
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        assert isinstance(dumped, Mapping)
        return dumped
    pytest.fail(f"expected a mapping-like DTO, got {type(value).__name__}")


def _json_tree(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_tree(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_tree(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_tree(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return getattr(value, "value", value)


def _serialized(value: Any) -> str:
    return json.dumps(_json_tree(value), ensure_ascii=False, sort_keys=True)


def _recursive_keys(value: Any) -> set[str]:
    tree = _json_tree(value)
    if isinstance(tree, Mapping):
        return set(tree) | {key for item in tree.values() for key in _recursive_keys(item)}
    if isinstance(tree, list):
        return {key for item in tree for key in _recursive_keys(item)}
    return set()


def _assert_receipt(
    value: Any,
    *,
    decision_type: str,
    status: str,
    exact_retry: bool,
    started_field: str,
    canaries: Sequence[str] = (),
) -> Mapping[str, Any]:
    receipt = _mapping(value)
    assert receipt["contract_version"] == CONTRACT_VERSION
    assert receipt["schema_version"] == SCHEMA_VERSION
    assert receipt["mode"] == MODE
    assert isinstance(receipt["decision_id"], str) and receipt["decision_id"]
    assert receipt["decision_type"] == decision_type
    assert receipt["status"] == status
    assert receipt["exact_retry"] is exact_retry
    assert receipt[started_field] is False
    for key in _recursive_keys(receipt):
        normalized = key.casefold().replace("-", "_")
        assert not any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS), (
            f"operator receipt leaks denied key {key!r}"
        )
    serialized = _serialized(receipt)
    assert OPERATOR_ID not in serialized
    assert all(canary not in serialized for canary in canaries)
    return receipt


def _without_retry(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in receipt.items() if key != "exact_retry"}


def _tamper(token: str) -> str:
    assert token
    index = len(token) // 2
    replacement = "A" if token[index] != "A" else "B"
    return f"{token[:index]}{replacement}{token[index + 1 :]}"


async def _approval_pending_call(
    ledger: AgentRuntimeLedger,
    *,
    suffix: str,
) -> str:
    run_id = f"run-approval-{suffix}"
    call_id = f"call-approval-{suffix}"
    await ledger.create_run(
        run_id=run_id,
        trigger_type="message",
        trigger_ref=f"message:{suffix}",
        principal_kind="user",
        principal_id=f"principal-{suffix}",
    )
    await ledger.transition_run(run_id, to_status="running", actor="runtime")
    await ledger.create_tool_call(
        call_id=call_id,
        run_id=run_id,
        step_id=f"step-{suffix}",
        tool_name="offline_approval_tool",
        tool_version="1",
        owner="offline-test",
        effect="external_irreversible",
        principal_kind="user",
        principal_id=f"principal-{suffix}",
        target_ref=f"external:target-canary-{suffix}",
        args_digest=f"sha256:args-canary-{suffix}",
        idempotency_mode="reconcile_only",
        concurrency_mode="keyed_serial",
        concurrency_key=f"external:target-canary-{suffix}",
    )
    await ledger.transition_tool_call(
        call_id,
        to_status="approval_pending",
        actor="policy",
    )
    await ledger.transition_run(
        run_id,
        to_status="waiting_approval",
        actor="runtime",
    )
    return call_id


async def _unknown_call(
    ledger: AgentRuntimeLedger,
    *,
    suffix: str,
) -> str:
    run_id = f"run-reconcile-{suffix}"
    call_id = f"call-reconcile-{suffix}"
    target_ref = f"offline:target-canary-{suffix}"
    await ledger.create_run(
        run_id=run_id,
        trigger_type="recovery",
        trigger_ref=f"runtime:{suffix}",
        principal_kind="service",
        principal_id="runtime",
    )
    await ledger.transition_run(run_id, to_status="running", actor="runtime")
    await ledger.create_tool_call(
        call_id=call_id,
        run_id=run_id,
        step_id=f"step-{suffix}",
        tool_name="offline_reconciliation_tool",
        tool_version="1",
        owner="offline-test",
        effect="external_irreversible",
        principal_kind="service",
        principal_id="runtime",
        target_ref=target_ref,
        args_digest=f"sha256:args-canary-{suffix}",
        idempotency_mode="reconcile_only",
        concurrency_mode="keyed_serial",
        concurrency_key=target_ref,
    )
    await ledger.transition_tool_call(call_id, to_status="ready", actor="policy")
    await ledger.claim_tool_call(
        call_id,
        lease_owner="worker",
        lease_until="2099-01-01T00:00:00+00:00",
        actor="worker",
    )
    await ledger.transition_tool_call(
        call_id,
        to_status="dispatching",
        actor="worker",
        expected_lease_owner="worker",
    )
    await ledger.transition_tool_call(
        call_id,
        to_status="unknown",
        actor="worker",
        error_code="provider_unknown",
        expected_lease_owner="worker",
    )
    await ledger.transition_run(
        run_id,
        to_status="waiting_external",
        actor="runtime",
    )
    return call_id


class _OfflineAdapter:
    adapter_id = "tmp-offline-adapter-v1"
    offline_only = True
    idempotent = True

    def __init__(
        self,
        marker_path: Path,
        *,
        supported_owner: str = "offline-test",
    ) -> None:
        self.marker_path = marker_path
        self.supported_owner = supported_owner
        self.reconcile_calls = 0

    def supports(self, call: Any) -> bool:
        return call.owner == self.supported_owner

    async def reconcile(self, call: Any, command: Any) -> ReconciliationReceipt:
        self.reconcile_calls += 1
        self.marker_path.write_text(
            f"{command.decision.value}:{self.reconcile_calls}",
            encoding="utf-8",
        )
        return ReconciliationReceipt(
            decision=command.decision,
            target_ref=call.target_ref,
            evidence_ref=command.evidence_ref,
            external_id=command.external_id,
        )


def _candidate(*, suffix: str, content: str | None = None) -> CandidateEnvelopeV1:
    quote = f"raw-evidence-canary-{suffix}"
    evidence = EvidenceAtomV1(
        evidence_ref=f"message:onebot:private:42:msg-{suffix}",
        content_sha256=sha256_text(quote),
        quote=quote,
        actor_ref="user:qq:42",
        occurred_at=T0,
    )
    claim_content = content or f"likes tea {suffix}"
    observation = ObservationV1(
        source_kind=SourceKind.USER_STATEMENT,
        producer_kind=ProducerKind.MEMO,
        producer_version="memo-v2",
        producer_run_id=f"memo-run-{suffix}",
        subject_ref="user:qq:42",
        owner_scope=OwnerScope.USER,
        owner_id="42",
        visibility=Visibility.PRIVATE,
        origin_group_ref=None,
        claim=CardClaimV1(category="preference", content=claim_content),
        evidence=(evidence,),
        observed_at=T0 + timedelta(seconds=1),
        source_occurred_at=T0,
        time_basis=TimeBasis.SOURCE_EVENT,
        valid_from=None,
        valid_to=None,
        confidence=0.8,
    )
    proposal = ProjectionProposalV1.create(
        projection_kind="card",
        operation="create",
        payload={"category": "preference", "content": claim_content},
    )
    return CandidateEnvelopeV1.create(
        observation=observation,
        proposal=proposal,
        producer_kind="memo",
        producer_run_id=f"memo-run-{suffix}",
        producer_item_id=f"item-{suffix}",
        produced_at=T0 + timedelta(seconds=2),
        model_output=f"raw-model-payload-canary-{suffix}",
    )


def _conflict(
    first: CandidateEnvelopeV1,
    second: CandidateEnvelopeV1,
) -> ConflictV1:
    return ConflictV1.create(
        kind="contradiction",
        subject_ref="user:qq:42",
        claim_key="preference:tea",
        observation_ids=(
            first.observation.observation_id,
            second.observation.observation_id,
        ),
        candidate_ids=(first.candidate_id, second.candidate_id),
        detected_at=T0 + timedelta(seconds=3),
        basis_evidence_refs=tuple(
            atom.evidence_ref for candidate in (first, second) for atom in candidate.observation.evidence
        ),
    )


async def test_constructor_sources_identity_and_command_surface_are_explicit(
    tmp_path: Path,
    actions_api: ModuleType,
) -> None:
    runtime = AgentRuntimeLedger(tmp_path / "runtime.db")
    memory = MemoryGovernanceStore(tmp_path / "memory.db")
    operator = actions_api.OperatorIdentityV1(
        operator_id=OPERATOR_ID,
        granted_scopes=OPERATOR_SCOPES,
    )

    with pytest.raises((TypeError, ValueError, RuntimeError), match="source"):
        actions_api.OfflineAdminActionsV1(
            runtime_source=None,
            memory_source=memory,
            operator=operator,
        )
    with pytest.raises((TypeError, ValueError, RuntimeError), match="source"):
        actions_api.OfflineAdminActionsV1(
            runtime_source=runtime,
            memory_source=None,
            operator=operator,
        )
    with pytest.raises((TypeError, ValueError), match="operator"):
        actions_api.OfflineAdminActionsV1(
            runtime_source=runtime,
            memory_source=memory,
            operator=None,
        )

    actions = _new_actions(actions_api, runtime, memory)
    public_callables = {name for name in dir(actions) if not name.startswith("_") and callable(getattr(actions, name))}
    assert public_callables == {
        "approve_tool_call",
        "decide_memory_candidate",
        "memory_candidate_context",
        "memory_candidate_token",
        "reconcile_tool_call",
        "reconciliation_context",
        "reconciliation_token",
        "tool_approval_context",
        "tool_approval_token",
    }
    expected_parameters = {
        "approve_tool_call": {
            "self",
            "call_id",
            "expected_token",
            "approval_ref",
        },
        "reconcile_tool_call": {
            "self",
            "call_id",
            "expected_token",
            "decision",
            "evidence_ref",
            "operator_note",
            "external_id",
        },
        "decide_memory_candidate": {
            "self",
            "candidate_id",
            "expected_token",
            "decision",
            "reason_code",
            "operator_note",
            "occurred_at",
        },
    }
    for method_name, expected in expected_parameters.items():
        parameters = inspect.signature(getattr(type(actions), method_name)).parameters
        assert set(parameters) == expected
        assert not any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
        identifier_name = "candidate_id" if method_name.startswith("decide") else "call_id"
        for name, parameter in parameters.items():
            if name not in {"self", identifier_name}:
                assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert tuple(tmp_path.iterdir()) == ()


async def test_uninitialized_and_closed_sources_fail_without_initializing_or_creating(
    tmp_path: Path,
    actions_api: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_path = tmp_path / "must-not-create-runtime.db"
    memory_path = tmp_path / "must-not-create-memory.db"
    runtime = AgentRuntimeLedger(runtime_path)
    memory = MemoryGovernanceStore(memory_path)
    actions = _new_actions(actions_api, runtime, memory)

    async def forbidden_init() -> None:
        pytest.fail("offline actions must not initialize caller-owned sources")

    monkeypatch.setattr(runtime, "init", forbidden_init)
    monkeypatch.setattr(memory, "init", forbidden_init)
    with pytest.raises(RuntimeError, match=r"source|initialized|available"):
        await actions.tool_approval_token("missing-call")
    with pytest.raises(RuntimeError, match=r"source|initialized|available"):
        await actions.memory_candidate_token("missing-candidate")
    assert not runtime_path.exists()
    assert not memory_path.exists()

    opened_runtime = AgentRuntimeLedger(tmp_path / "closed-runtime.db")
    opened_memory = MemoryGovernanceStore(tmp_path / "closed-memory.db")
    await opened_runtime.init()
    await opened_memory.init()
    await opened_runtime.close()
    await opened_memory.close()
    closed_actions = _new_actions(actions_api, opened_runtime, opened_memory)
    with pytest.raises(RuntimeError, match=r"source|initialized|available"):
        await closed_actions.tool_approval_token("missing-call")
    with pytest.raises(RuntimeError, match=r"source|initialized|available"):
        await closed_actions.memory_candidate_token("missing-candidate")


async def test_tool_approval_records_only_server_digest_and_exact_retry(
    actions_api: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    call_id = await _approval_pending_call(runtime, suffix="record")

    async def forbidden_execution(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("offline approval must never enter execution coordination")

    monkeypatch.setattr(RunCoordinator, "resume_approved_tool", forbidden_execution)
    monkeypatch.setattr(EffectExecutor, "execute", forbidden_execution)
    monkeypatch.setattr(
        ReconciliationCoordinator,
        "resolve_unknown",
        forbidden_execution,
    )
    actions = _new_actions(actions_api, runtime, memory)
    approval_ref = "approval:ticket:APPROVAL_REF_CANARY"
    token = await actions.tool_approval_token(call_id)
    assert isinstance(token, str) and token
    assert call_id not in token
    assert "approval_pending" not in token

    first = _assert_receipt(
        await actions.approve_tool_call(
            call_id,
            expected_token=token,
            approval_ref=approval_ref,
        ),
        decision_type="tool_approval",
        status="recorded",
        exact_retry=False,
        started_field="execution_started",
        canaries=(approval_ref, "target-canary-record", "args-canary-record"),
    )
    retry = _assert_receipt(
        await actions.approve_tool_call(
            call_id,
            expected_token=token,
            approval_ref=approval_ref,
        ),
        decision_type="tool_approval",
        status="recorded",
        exact_retry=True,
        started_field="execution_started",
        canaries=(approval_ref, "target-canary-record", "args-canary-record"),
    )
    assert _without_retry(retry) == _without_retry(first)
    with pytest.raises(ValueError, match=r"approval|decision|retry|token"):
        await actions.approve_tool_call(
            call_id,
            expected_token=token,
            approval_ref="approval:ticket:CONFLICTING_REF_CANARY",
        )

    call = await runtime.get_tool_call(call_id)
    events = await runtime.list_events(run_id="run-approval-record")
    assert call is not None and call.status == "approval_pending"
    assert call.approval_ref_digest == "sha256:" + hashlib.sha256(approval_ref.encode("utf-8")).hexdigest()
    approval_events = [event for event in events if event.event_type == "approval_granted"]
    assert len(approval_events) == 1
    assert approval_events[0].actor == f"operator:{OPERATOR_ID}"
    assert approval_ref not in _serialized(approval_events)


async def test_tool_approval_ref_and_state_token_fail_closed(
    actions_api: ModuleType,
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    call_id = await _approval_pending_call(runtime, suffix="token-a")
    other_call_id = await _approval_pending_call(runtime, suffix="token-b")
    actions = _new_actions(actions_api, runtime, memory)
    token = await actions.tool_approval_token(call_id)

    for invalid_ref in ("", "x" * 241, "approval:\x00control"):
        with pytest.raises(ValueError, match="approval_ref"):
            await actions.approve_tool_call(
                call_id,
                expected_token=token,
                approval_ref=invalid_ref,
            )
    for invalid_token, invalid_call in (
        (_tamper(token), call_id),
        (token, other_call_id),
    ):
        with pytest.raises(ValueError, match=r"token|state|stale"):
            await actions.approve_tool_call(
                invalid_call,
                expected_token=invalid_token,
                approval_ref="approval:ticket:valid",
            )

    await runtime.transition_tool_call(call_id, to_status="ready", actor="test")
    with pytest.raises(ValueError, match=r"token|state|stale|pending"):
        await actions.approve_tool_call(
            call_id,
            expected_token=token,
            approval_ref="approval:ticket:valid",
        )
    first = await runtime.get_tool_call(call_id)
    second = await runtime.get_tool_call(other_call_id)
    assert first is not None and first.approval_ref_digest == ""
    assert second is not None and second.approval_ref_digest == ""


async def test_tool_approval_cancellation_leaves_no_partial_decision(
    actions_api: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    call_id = await _approval_pending_call(runtime, suffix="cancel")
    actions = _new_actions(actions_api, runtime, memory)
    token = await actions.tool_approval_token(call_id)
    entered = asyncio.Event()

    async def blocked_record(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "record_tool_call_approval", blocked_record)
    task = asyncio.create_task(
        actions.approve_tool_call(
            call_id,
            expected_token=token,
            approval_ref="approval:ticket:cancel",
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    call = await runtime.get_tool_call(call_id)
    events = await runtime.list_events(run_id="run-approval-cancel")
    assert call is not None and call.approval_ref_digest == ""
    assert not any(event.event_type == "approval_granted" for event in events)


async def test_reconciliation_selects_one_local_adapter_and_is_idempotent(
    tmp_path: Path,
    actions_api: ModuleType,
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    call_id = await _unknown_call(runtime, suffix="success")
    unsupported = _OfflineAdapter(
        tmp_path / "must-not-run.txt",
        supported_owner="other-owner",
    )
    adapter = _OfflineAdapter(tmp_path / "local-adapter-receipt.txt")
    actions = _new_actions(actions_api, runtime, memory, adapters=(unsupported, adapter))
    token = await actions.reconciliation_token(call_id)
    assert call_id not in token
    evidence_ref = "operator:ticket:EVIDENCE_REF_CANARY"
    operator_note = "OPERATOR_NOTE_CANARY confirmed in offline fixture"
    external_id = "EXTERNAL_ID_CANARY_2001"

    first = _assert_receipt(
        await actions.reconcile_tool_call(
            call_id,
            expected_token=token,
            decision="confirmed_succeeded",
            evidence_ref=evidence_ref,
            operator_note=operator_note,
            external_id=external_id,
        ),
        decision_type="tool_reconciliation",
        status="confirmed_succeeded",
        exact_retry=False,
        started_field="execution_started",
        canaries=(evidence_ref, operator_note, external_id, "target-canary-success"),
    )
    retry = _assert_receipt(
        await actions.reconcile_tool_call(
            call_id,
            expected_token=token,
            decision="confirmed_succeeded",
            evidence_ref=evidence_ref,
            operator_note=operator_note,
            external_id=external_id,
        ),
        decision_type="tool_reconciliation",
        status="confirmed_succeeded",
        exact_retry=True,
        started_field="execution_started",
        canaries=(evidence_ref, operator_note, external_id, "target-canary-success"),
    )
    assert _without_retry(retry) == _without_retry(first)
    assert adapter.reconcile_calls == 1
    assert unsupported.reconcile_calls == 0
    assert (tmp_path / "local-adapter-receipt.txt").read_text(encoding="utf-8") == "confirmed_succeeded:1"

    with pytest.raises(ValueError, match=r"reconciliation|decision|retry|token"):
        await actions.reconcile_tool_call(
            call_id,
            expected_token=token,
            decision="confirmed_not_applied",
            evidence_ref="operator:ticket:conflict",
            operator_note="conflicting retry",
        )
    record = await runtime.get_tool_reconciliation(call_id)
    call = await runtime.get_tool_call(call_id)
    run = await runtime.get_run("run-reconcile-success")
    assert record is not None and record.actor == f"operator:{OPERATOR_ID}"
    assert call is not None and call.status == "unknown"
    assert run is not None and run.status == "running"
    assert adapter.reconcile_calls == 1


@pytest.mark.parametrize(
    ("offline_only", "idempotent"),
    [(False, True), (True, False)],
)
async def test_reconciliation_rejects_unsafe_or_ambiguous_server_adapters(
    tmp_path: Path,
    actions_api: ModuleType,
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
    offline_only: bool,
    idempotent: bool,
) -> None:
    runtime, memory = opened_sources
    call_id = await _unknown_call(runtime, suffix=f"unsafe-{offline_only}-{idempotent}")
    unsafe = _OfflineAdapter(tmp_path / "unsafe-must-not-run.txt")
    unsafe.offline_only = offline_only
    unsafe.idempotent = idempotent
    actions = _new_actions(actions_api, runtime, memory, adapters=(unsafe,))
    token = await actions.reconciliation_token(call_id)
    with pytest.raises(ValueError, match=r"adapter|offline|idempotent"):
        await actions.reconcile_tool_call(
            call_id,
            expected_token=token,
            decision="confirmed_not_applied",
            evidence_ref="operator:ticket:unsafe",
            operator_note="offline fixture says absent",
        )
    assert unsafe.reconcile_calls == 0

    first = _OfflineAdapter(tmp_path / "ambiguous-first.txt")
    second = _OfflineAdapter(tmp_path / "ambiguous-second.txt")
    ambiguous_actions = _new_actions(
        actions_api,
        runtime,
        memory,
        adapters=(first, second),
    )
    with pytest.raises(ValueError, match=r"adapter|ambiguous|one"):
        await ambiguous_actions.reconcile_tool_call(
            call_id,
            expected_token=token,
            decision="confirmed_not_applied",
            evidence_ref="operator:ticket:ambiguous",
            operator_note="offline fixture says absent",
        )
    record = await runtime.get_tool_reconciliation(call_id)
    assert first.reconcile_calls == second.reconcile_calls == 0
    assert record is None


async def test_reconciliation_token_is_tamper_call_and_state_bound(
    tmp_path: Path,
    actions_api: ModuleType,
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    call_id = await _unknown_call(runtime, suffix="token-a")
    other_call_id = await _unknown_call(runtime, suffix="token-b")
    adapter = _OfflineAdapter(tmp_path / "token-adapter.txt")
    actions = _new_actions(actions_api, runtime, memory, adapters=(adapter,))
    token = await actions.reconciliation_token(call_id)

    for invalid_token, invalid_call in (
        (_tamper(token), call_id),
        (token, other_call_id),
    ):
        with pytest.raises(ValueError, match=r"token|state|stale"):
            await actions.reconcile_tool_call(
                invalid_call,
                expected_token=invalid_token,
                decision="confirmed_not_applied",
                evidence_ref="operator:ticket:invalid-token",
                operator_note="offline fixture says absent",
            )
    await runtime.transition_run(
        "run-reconcile-token-a",
        to_status="running",
        actor="test",
    )
    with pytest.raises(ValueError, match=r"token|state|stale|waiting"):
        await actions.reconcile_tool_call(
            call_id,
            expected_token=token,
            decision="confirmed_not_applied",
            evidence_ref="operator:ticket:stale",
            operator_note="offline fixture says absent",
        )
    assert adapter.reconcile_calls == 0


async def test_reconciliation_cancellation_leaves_no_runtime_decision(
    tmp_path: Path,
    actions_api: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    call_id = await _unknown_call(runtime, suffix="cancel")
    adapter = _OfflineAdapter(tmp_path / "cancel-adapter.txt")
    actions = _new_actions(actions_api, runtime, memory, adapters=(adapter,))
    token = await actions.reconciliation_token(call_id)
    entered = asyncio.Event()

    async def blocked_record(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "record_tool_reconciliation", blocked_record)
    task = asyncio.create_task(
        actions.reconcile_tool_call(
            call_id,
            expected_token=token,
            decision="confirmed_not_applied",
            evidence_ref="operator:ticket:cancel",
            operator_note="offline fixture says absent",
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    record = await runtime.get_tool_reconciliation(call_id)
    call = await runtime.get_tool_call(call_id)
    run = await runtime.get_run("run-reconcile-cancel")
    assert adapter.reconcile_calls == 1
    assert record is None
    assert call is not None and call.status == "unknown"
    assert run is not None and run.status == "waiting_external"


@pytest.mark.parametrize(
    ("decision", "event_kind", "status"),
    [
        ("approve", "promotion_approved", "approved"),
        ("reject", "promotion_rejected", "rejected"),
    ],
)
async def test_memory_decision_appends_only_operator_event_and_exact_retry(
    actions_api: ModuleType,
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
    decision: str,
    event_kind: str,
    status: str,
) -> None:
    runtime, memory = opened_sources
    candidate = _candidate(suffix=decision)
    await memory.append_candidate(candidate)
    actions = _new_actions(actions_api, runtime, memory)
    token = await actions.memory_candidate_token(candidate.candidate_id)
    assert candidate.candidate_id not in token
    operator_note = f"OPERATOR_NOTE_CANARY_MEMORY_{decision}"
    occurred_at = T0 + timedelta(minutes=1)

    first = _assert_receipt(
        await actions.decide_memory_candidate(
            candidate.candidate_id,
            expected_token=token,
            decision=decision,
            reason_code=f"offline_{decision}",
            operator_note=operator_note,
            occurred_at=occurred_at,
        ),
        decision_type=event_kind,
        status=status,
        exact_retry=False,
        started_field="projection_started",
        canaries=(
            operator_note,
            f"raw-evidence-canary-{decision}",
            f"raw-model-payload-canary-{decision}",
        ),
    )
    retry = _assert_receipt(
        await actions.decide_memory_candidate(
            candidate.candidate_id,
            expected_token=token,
            decision=decision,
            reason_code=f"offline_{decision}",
            operator_note=operator_note,
            occurred_at=occurred_at,
        ),
        decision_type=event_kind,
        status=status,
        exact_retry=True,
        started_field="projection_started",
        canaries=(operator_note,),
    )
    assert _without_retry(retry) == _without_retry(first)
    opposite = "reject" if decision == "approve" else "approve"
    with pytest.raises(ValueError, match=r"decision|promotion|retry|token"):
        await actions.decide_memory_candidate(
            candidate.candidate_id,
            expected_token=token,
            decision=opposite,
            reason_code="conflicting_retry",
            occurred_at=occurred_at,
        )

    events = await memory.list_promotion_events(candidate.candidate_id)
    folded = await memory.fold_candidate(candidate.candidate_id)
    assert len(events) == 1
    assert events[0].event_kind.value == event_kind
    assert events[0].actor_kind.value == "operator"
    assert events[0].actor_ref == f"operator:{OPERATOR_ID}"
    assert events[0].projection_ref is None and events[0].receipt_ref is None
    assert folded.status == status
    assert folded.projection_event_id == ""


@pytest.mark.parametrize(
    ("decision", "event_kind", "status"),
    [
        ("approve", "promotion_approved", "approved"),
        ("reject", "promotion_rejected", "rejected"),
    ],
)
async def test_memory_decision_derives_exact_underlying_conflict_set(
    actions_api: ModuleType,
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
    decision: str,
    event_kind: str,
    status: str,
) -> None:
    runtime, memory = opened_sources
    first = _candidate(suffix=f"conflict-{decision}-a", content="likes jasmine tea")
    second = _candidate(suffix=f"conflict-{decision}-b", content="dislikes jasmine tea")
    conflict = _conflict(first, second)
    await memory.append_candidate(first)
    await memory.append_candidate(second)
    await memory.append_conflict(conflict)
    actions = _new_actions(actions_api, runtime, memory)
    token = await actions.memory_candidate_token(first.candidate_id)

    receipt = _assert_receipt(
        await actions.decide_memory_candidate(
            first.candidate_id,
            expected_token=token,
            decision=decision,
            reason_code=f"conflicts_reviewed_{decision}",
            occurred_at=T0 + timedelta(minutes=1),
        ),
        decision_type=event_kind,
        status=status,
        exact_retry=False,
        started_field="projection_started",
        canaries=(conflict.conflict_id,),
    )
    assert receipt["projection_started"] is False
    events = await memory.list_promotion_events(first.candidate_id)
    assert len(events) == 1
    assert events[0].conflict_ids == (conflict.conflict_id,)


async def test_concurrent_exact_memory_decisions_report_one_first_write_and_one_retry(
    actions_api: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    candidate = _candidate(suffix="concurrent-exact")
    await memory.append_candidate(candidate)
    actions = _new_actions(actions_api, runtime, memory)
    token = await actions.memory_candidate_token(candidate.candidate_id)
    occurred_at = T0 + timedelta(minutes=1)
    original_list = memory.list_promotion_events
    both_read_empty = asyncio.Barrier(2)

    async def synchronized_list(candidate_id: str) -> tuple[PromotionEventV1, ...]:
        events = await original_list(candidate_id)
        await both_read_empty.wait()
        return events

    monkeypatch.setattr(memory, "list_promotion_events", synchronized_list)
    results = await asyncio.gather(
        *(
            actions.decide_memory_candidate(
                candidate.candidate_id,
                expected_token=token,
                decision="reject",
                reason_code="concurrent_exact_review",
                operator_note="CONCURRENT_OPERATOR_NOTE_CANARY",
                occurred_at=occurred_at,
            )
            for _ in range(2)
        )
    )

    receipts = tuple(_mapping(result) for result in results)
    assert sorted(receipt["exact_retry"] for receipt in receipts) == [False, True]
    assert len({receipt["decision_id"] for receipt in receipts}) == 1
    for receipt in receipts:
        _assert_receipt(
            receipt,
            decision_type="promotion_rejected",
            status="rejected",
            exact_retry=bool(receipt["exact_retry"]),
            started_field="projection_started",
            canaries=("CONCURRENT_OPERATOR_NOTE_CANARY",),
        )
    assert len(await original_list(candidate.candidate_id)) == 1


async def test_memory_token_is_tamper_candidate_and_state_bound(
    actions_api: ModuleType,
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    candidate = _candidate(suffix="token-a")
    other = _candidate(suffix="token-b")
    await memory.append_candidate(candidate)
    await memory.append_candidate(other)
    actions = _new_actions(actions_api, runtime, memory)
    token = await actions.memory_candidate_token(candidate.candidate_id)

    for invalid_token, invalid_candidate in (
        (_tamper(token), candidate.candidate_id),
        (token, other.candidate_id),
    ):
        with pytest.raises(ValueError, match=r"token|state|stale"):
            await actions.decide_memory_candidate(
                invalid_candidate,
                expected_token=invalid_token,
                decision="reject",
                reason_code="invalid_token",
                occurred_at=T0 + timedelta(minutes=1),
            )

    queued = PromotionEventV1.create(
        candidate_id=candidate.candidate_id,
        candidate_sha256=candidate.candidate_sha256,
        event_kind="review_queued",
        actor_kind="policy",
        actor_ref="policy:offline-review",
        occurred_at=T0 + timedelta(seconds=10),
        reason_code="manual_review",
        operator_note="",
        conflict_ids=(),
        projection_kind=candidate.proposal.projection_kind,
        operation=candidate.proposal.operation,
        projection_ref=None,
        receipt_ref=None,
    )
    await memory.append_promotion_event(queued)
    with pytest.raises(ValueError, match=r"token|state|stale"):
        await actions.decide_memory_candidate(
            candidate.candidate_id,
            expected_token=token,
            decision="reject",
            reason_code="stale_review",
            occurred_at=T0 + timedelta(minutes=1),
        )
    events = await memory.list_promotion_events(candidate.candidate_id)
    assert events == (queued,)


async def test_memory_decision_cancellation_leaves_no_partial_event(
    actions_api: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    candidate = _candidate(suffix="cancel")
    await memory.append_candidate(candidate)
    actions = _new_actions(actions_api, runtime, memory)
    token = await actions.memory_candidate_token(candidate.candidate_id)
    entered = asyncio.Event()

    async def blocked_append(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(memory, "append_promotion_event_with_outcome", blocked_append)
    task = asyncio.create_task(
        actions.decide_memory_candidate(
            candidate.candidate_id,
            expected_token=token,
            decision="reject",
            reason_code="cancelled_review",
            occurred_at=T0 + timedelta(minutes=1),
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    events = await memory.list_promotion_events(candidate.candidate_id)
    assert events == ()
