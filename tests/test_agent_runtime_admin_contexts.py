"""RED contracts for server-owned offline Admin decision contexts."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from services.agent_runtime.admin_actions import (
    OfflineAdminActionsV1,
    OperatorIdentityV1,
)
from services.agent_runtime.ledger import AgentRuntimeLedger
from services.memory.governance_contracts import (
    CandidateEnvelopeV1,
    CardClaimV1,
    ConflictV1,
    EvidenceAtomV1,
    ObservationV1,
    OwnerScope,
    ProducerKind,
    ProjectionProposalV1,
    SourceKind,
    TimeBasis,
    Visibility,
    sha256_text,
)
from services.memory.governance_store import MemoryGovernanceStore

API = "/api/admin"
CONTRACT_VERSION = "offline_admin_actions.v1"
T0 = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)
OPERATOR_ID = "offline-context-reviewer-7"
OPERATOR_SCOPES = (
    "memory:candidate:decide",
    "runtime:tool:approve",
    "runtime:tool:reconcile",
)
FORBIDDEN_KEYS = frozenset(
    {
        "actor_ref",
        "args",
        "args_digest",
        "evidence",
        "evidence_ref",
        "metadata",
        "model_output",
        "note",
        "operator",
        "owner_id",
        "payload",
        "principal",
        "principal_id",
        "quote",
        "subject_ref",
        "target",
        "target_ref",
    }
)


@pytest.fixture
async def opened_sources(
    tmp_path: Path,
) -> AsyncIterator[tuple[AgentRuntimeLedger, MemoryGovernanceStore]]:
    runtime = AgentRuntimeLedger(tmp_path / "runtime-context.db")
    memory = MemoryGovernanceStore(tmp_path / "memory-context.db")
    await runtime.init()
    await memory.init()
    try:
        yield runtime, memory
    finally:
        await runtime.close()
        await memory.close()


def _new_actions(
    runtime: AgentRuntimeLedger,
    memory: MemoryGovernanceStore,
    *,
    operator_id: str = OPERATOR_ID,
) -> OfflineAdminActionsV1:
    return OfflineAdminActionsV1(
        runtime_source=runtime,
        memory_source=memory,
        operator=OperatorIdentityV1(
            operator_id=operator_id,
            granted_scopes=OPERATOR_SCOPES,
        ),
        reconciliation_adapters=(),
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
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return getattr(value, "value", value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_tree(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _recursive_keys(value: Any) -> set[str]:
    tree = _json_tree(value)
    if isinstance(tree, Mapping):
        return set(tree) | {
            key for item in tree.values() for key in _recursive_keys(item)
        }
    if isinstance(tree, list):
        return {key for item in tree for key in _recursive_keys(item)}
    return set()


def _strings(value: Any) -> tuple[str, ...]:
    tree = _json_tree(value)
    if isinstance(tree, str):
        return (tree,)
    if isinstance(tree, Mapping):
        return tuple(item for value in tree.values() for item in _strings(value))
    if isinstance(tree, list):
        return tuple(item for value in tree for item in _strings(value))
    return ()


def _parse_timestamp(value: Any) -> datetime:
    assert isinstance(value, str) and value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None and parsed.utcoffset() is not None
    return parsed.astimezone(UTC)


def _assert_context(
    value: Any,
    *,
    resource_kind: str,
    resource_id: str,
    state: str,
    preview_fields: Mapping[str, Any],
    canaries: Sequence[str],
) -> Mapping[str, Any]:
    context = _mapping(value)
    assert set(context) == {
        "contract_version",
        "expected_token",
        "mode",
        "preview",
        "preview_digest",
        "report_only",
        "resource",
        "schema_version",
        "state",
        "updated_at",
    }
    assert context["contract_version"] == CONTRACT_VERSION
    assert context["schema_version"] == 1
    assert context["mode"] == "offline_dark"
    assert context["report_only"] is True
    assert _mapping(context["resource"]) == {
        "kind": resource_kind,
        "id": resource_id,
    }
    assert context["state"] == state
    _parse_timestamp(context["updated_at"])

    preview = _mapping(context["preview"])
    for field, expected in preview_fields.items():
        assert preview[field] == expected
    assert len(_canonical_json(preview).encode("utf-8")) <= 2_048
    assert all(len(item) <= 240 for item in _strings(preview))
    expected_digest = "sha256:" + hashlib.sha256(
        _canonical_json(preview).encode("utf-8")
    ).hexdigest()
    assert context["preview_digest"] == expected_digest

    token = context["expected_token"]
    assert isinstance(token, str) and 32 <= len(token) <= 512
    assert all(32 <= ord(character) < 127 for character in token)
    assert resource_id not in token
    assert state not in token
    assert OPERATOR_ID not in token
    assert expected_digest not in token

    assert FORBIDDEN_KEYS.isdisjoint(_recursive_keys(context))
    serialized = _canonical_json(context)
    assert all(canary not in serialized for canary in canaries)
    return context


async def _approval_pending_call(ledger: AgentRuntimeLedger, suffix: str) -> str:
    run_id = f"run-context-approval-{suffix}"
    call_id = f"call-context-approval-{suffix}"
    await ledger.create_run(
        run_id=run_id,
        trigger_type="message",
        trigger_ref=f"message:TRIGGER_CANARY_{suffix}",
        principal_kind="user",
        principal_id=f"PRINCIPAL_CANARY_{suffix}",
        metadata={"secret": f"RUN_METADATA_CANARY_{suffix}"},
    )
    await ledger.transition_run(run_id, to_status="running", actor="runtime")
    await ledger.create_tool_call(
        call_id=call_id,
        run_id=run_id,
        step_id=f"step-{suffix}",
        tool_name="bounded_preview_tool",
        tool_version="1",
        owner="offline-test",
        effect="external_irreversible",
        principal_kind="user",
        principal_id=f"PRINCIPAL_CANARY_{suffix}",
        target_ref=f"external:TARGET_CANARY_{suffix}",
        args_digest=f"sha256:ARGS_DIGEST_CANARY_{suffix}",
        idempotency_mode="reconcile_only",
        concurrency_mode="keyed_serial",
        concurrency_key=f"external:TARGET_CANARY_{suffix}",
        metadata={"raw": f"TOOL_METADATA_CANARY_{suffix}"},
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


async def _unknown_call(ledger: AgentRuntimeLedger, suffix: str) -> str:
    run_id = f"run-context-reconcile-{suffix}"
    call_id = f"call-context-reconcile-{suffix}"
    await ledger.create_run(
        run_id=run_id,
        trigger_type="recovery",
        trigger_ref=f"runtime:{suffix}",
        principal_kind="service",
        principal_id=f"PRINCIPAL_CANARY_{suffix}",
    )
    await ledger.transition_run(run_id, to_status="running", actor="runtime")
    await ledger.create_tool_call(
        call_id=call_id,
        run_id=run_id,
        step_id=f"step-{suffix}",
        tool_name="bounded_reconciliation_tool",
        tool_version="1",
        owner="offline-test",
        effect="external_irreversible",
        principal_kind="service",
        principal_id=f"PRINCIPAL_CANARY_{suffix}",
        target_ref=f"external:TARGET_CANARY_{suffix}",
        args_digest=f"sha256:ARGS_DIGEST_CANARY_{suffix}",
        idempotency_mode="reconcile_only",
        concurrency_mode="keyed_serial",
        concurrency_key=f"external:TARGET_CANARY_{suffix}",
        metadata={"raw": f"TOOL_METADATA_CANARY_{suffix}"},
    )
    await ledger.transition_tool_call(call_id, to_status="ready", actor="policy")
    await ledger.claim_tool_call(
        call_id,
        lease_owner="context-worker",
        lease_until="2099-01-01T00:00:00+00:00",
        actor="context-worker",
    )
    await ledger.transition_tool_call(
        call_id,
        to_status="dispatching",
        actor="context-worker",
        expected_lease_owner="context-worker",
    )
    await ledger.transition_tool_call(
        call_id,
        to_status="unknown",
        actor="context-worker",
        error_code="provider_unknown",
        expected_lease_owner="context-worker",
    )
    await ledger.transition_run(
        run_id,
        to_status="waiting_external",
        actor="runtime",
    )
    return call_id


def _candidate(suffix: str, content: str) -> CandidateEnvelopeV1:
    quote = f"RAW_EVIDENCE_CANARY_{suffix}"
    observation = ObservationV1(
        source_kind=SourceKind.USER_STATEMENT,
        producer_kind=ProducerKind.MEMO,
        producer_version="memo-context-v1",
        producer_run_id=f"producer-run-{suffix}",
        subject_ref="user:qq:42",
        owner_scope=OwnerScope.USER,
        owner_id="42",
        visibility=Visibility.PRIVATE,
        origin_group_ref=None,
        claim=CardClaimV1(category="preference", content=content),
        evidence=(
            EvidenceAtomV1(
                evidence_ref=f"message:onebot:private:42:{suffix}",
                content_sha256=sha256_text(quote),
                quote=quote,
                actor_ref="user:qq:42",
                occurred_at=T0,
            ),
        ),
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
        payload={"category": "preference", "content": content},
    )
    return CandidateEnvelopeV1.create(
        observation=observation,
        proposal=proposal,
        producer_kind="memo",
        producer_run_id=f"producer-run-{suffix}",
        producer_item_id=f"producer-item-{suffix}",
        produced_at=T0 + timedelta(seconds=2),
        model_output=f"RAW_MODEL_OUTPUT_CANARY_{suffix}",
    )


def _conflict(
    first: CandidateEnvelopeV1,
    second: CandidateEnvelopeV1,
) -> ConflictV1:
    return ConflictV1.create(
        kind="contradiction",
        subject_ref="user:qq:42",
        claim_key="preference:context-preview",
        observation_ids=(
            first.observation.observation_id,
            second.observation.observation_id,
        ),
        candidate_ids=(first.candidate_id, second.candidate_id),
        detected_at=T0 + timedelta(seconds=3),
        basis_evidence_refs=tuple(
            atom.evidence_ref
            for candidate in (first, second)
            for atom in candidate.observation.evidence
        ),
    )


async def _call_context(actions: Any, method_name: str, item_id: str) -> Any:
    method = getattr(actions, method_name, None)
    assert callable(method), f"OfflineAdminActionsV1.{method_name}() is required"
    return await cast(Callable[[str], Awaitable[Any]], method)(item_id)


@pytest.mark.asyncio
async def test_tool_approval_context_is_bounded_redacted_and_token_bound(
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    call_id = await _approval_pending_call(runtime, "approval")
    actions = _new_actions(runtime, memory)

    context = _assert_context(
        await _call_context(actions, "tool_approval_context", call_id),
        resource_kind="tool_call",
        resource_id=call_id,
        state="approval_pending",
        preview_fields={
            "tool_name": "bounded_preview_tool",
            "effect": "external_irreversible",
        },
        canaries=(
            "PRINCIPAL_CANARY_approval",
            "TARGET_CANARY_approval",
            "ARGS_DIGEST_CANARY_approval",
            "RUN_METADATA_CANARY_approval",
            "TOOL_METADATA_CANARY_approval",
        ),
    )

    other_operator = _new_actions(runtime, memory, operator_id="other-reviewer")
    other = _mapping(
        await _call_context(other_operator, "tool_approval_context", call_id)
    )
    assert other["preview_digest"] == context["preview_digest"]
    assert other["expected_token"] != context["expected_token"]
    with pytest.raises(ValueError, match=r"token|operator|stale|context"):
        await other_operator.approve_tool_call(
            call_id,
            expected_token=str(context["expected_token"]),
            approval_ref="approval:offline:operator-bound",
        )

    await runtime.transition_tool_call(call_id, to_status="ready", actor="test")
    with pytest.raises(ValueError, match=r"token|state|stale|context|pending"):
        await actions.approve_tool_call(
            call_id,
            expected_token=str(context["expected_token"]),
            approval_ref="approval:offline:stale",
        )


@pytest.mark.asyncio
async def test_reconciliation_context_is_bounded_and_redacted(
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    call_id = await _unknown_call(runtime, "reconciliation")
    actions = _new_actions(runtime, memory)

    _assert_context(
        await _call_context(actions, "reconciliation_context", call_id),
        resource_kind="tool_call",
        resource_id=call_id,
        state="unknown",
        preview_fields={
            "tool_name": "bounded_reconciliation_tool",
            "effect": "external_irreversible",
        },
        canaries=(
            "PRINCIPAL_CANARY_reconciliation",
            "TARGET_CANARY_reconciliation",
            "ARGS_DIGEST_CANARY_reconciliation",
            "TOOL_METADATA_CANARY_reconciliation",
        ),
    )


@pytest.mark.asyncio
async def test_memory_context_token_tracks_redacted_preview_and_durable_conflicts(
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore],
) -> None:
    runtime, memory = opened_sources
    first = _candidate("memory-a", "PAYLOAD_CANARY_MEMORY_A")
    second = _candidate("memory-b", "PAYLOAD_CANARY_MEMORY_B")
    await memory.append_candidate(first)
    actions = _new_actions(runtime, memory)

    initial = _assert_context(
        await _call_context(
            actions,
            "memory_candidate_context",
            first.candidate_id,
        ),
        resource_kind="memory_candidate",
        resource_id=first.candidate_id,
        state="pending",
        preview_fields={
            "projection_kind": "card",
            "operation": "create",
            "conflict_count": 0,
        },
        canaries=(
            "PAYLOAD_CANARY_MEMORY_A",
            "RAW_EVIDENCE_CANARY_memory-a",
            "RAW_MODEL_OUTPUT_CANARY_memory-a",
            "user:qq:42",
        ),
    )

    await memory.append_candidate(second)
    conflict = _conflict(first, second)
    await memory.append_conflict(conflict)
    current = _assert_context(
        await _call_context(
            actions,
            "memory_candidate_context",
            first.candidate_id,
        ),
        resource_kind="memory_candidate",
        resource_id=first.candidate_id,
        state="pending",
        preview_fields={
            "projection_kind": "card",
            "operation": "create",
            "conflict_count": 1,
        },
        canaries=(
            "PAYLOAD_CANARY_MEMORY_A",
            "PAYLOAD_CANARY_MEMORY_B",
            "RAW_EVIDENCE_CANARY_memory-a",
            "RAW_EVIDENCE_CANARY_memory-b",
        ),
    )
    assert _mapping(current["preview"]).get("conflict_ids") == [conflict.conflict_id]
    assert current["preview_digest"] != initial["preview_digest"]
    assert current["expected_token"] != initial["expected_token"]
    with pytest.raises(ValueError, match=r"token|state|stale|context|conflict"):
        await actions.decide_memory_candidate(
            first.candidate_id,
            expected_token=str(initial["expected_token"]),
            decision="reject",
            reason_code="stale_context",
            occurred_at=T0 + timedelta(minutes=1),
        )
    assert await memory.list_promotion_events(first.candidate_id) == ()


class _ContextActionsFake:
    def __init__(self) -> None:
        self.context_calls: list[tuple[str, str]] = []
        self.approval_calls: list[dict[str, Any]] = []
        self.memory_decision_calls: list[dict[str, Any]] = []

    async def _context(self, method: str, item_id: str) -> dict[str, Any]:
        self.context_calls.append((method, item_id))
        return {
            "source": "explicit-actions",
            "method": method,
            "item_id": item_id,
            "expected_token": f"opaque-token-for-{method}",
        }

    async def tool_approval_context(self, call_id: str) -> dict[str, Any]:
        return await self._context("tool_approval_context", call_id)

    async def reconciliation_context(self, call_id: str) -> dict[str, Any]:
        return await self._context("reconciliation_context", call_id)

    async def memory_candidate_context(self, candidate_id: str) -> dict[str, Any]:
        return await self._context("memory_candidate_context", candidate_id)

    async def approve_tool_call(
        self,
        call_id: str,
        *,
        expected_token: str,
        approval_ref: str,
    ) -> dict[str, Any]:
        if expected_token != "opaque-context-token":
            raise ValueError(
                "STALE_CONTEXT_EXCEPTION_CANARY with /private/operator/path"
            )
        self.approval_calls.append(
            {
                "call_id": call_id,
                "expected_token": expected_token,
                "approval_ref": approval_ref,
            }
        )
        return {
            "status": "recorded",
            "decision_id": "decision-context-1",
            "exact_retry": len(self.approval_calls) > 1,
        }

    async def decide_memory_candidate(
        self,
        candidate_id: str,
        *,
        expected_token: str,
        decision: str,
        reason_code: str,
        operator_note: str | None,
        occurred_at: datetime,
    ) -> dict[str, Any]:
        self.memory_decision_calls.append(
            {
                "candidate_id": candidate_id,
                "expected_token": expected_token,
                "decision": decision,
                "reason_code": reason_code,
                "operator_note": operator_note,
                "occurred_at": occurred_at,
            }
        )
        return {
            "status": "recorded",
            "decision_id": "memory-decision-context-1",
            "exact_retry": False,
        }


def _direct_app(actions: Any) -> FastAPI:
    module = importlib.import_module("admin.routes.api.agent_runtime")
    factory = getattr(module, "create_agent_runtime_router", None)
    assert callable(factory), "create_agent_runtime_router() is required"
    router = factory(
        runtime_query=None,
        memory_query=None,
        readiness=None,
        actions=actions,
    )
    assert isinstance(router, APIRouter)
    app = FastAPI()
    app.include_router(router, prefix=API)
    return app


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://admin.test",
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_get_context_routes_forward_only_server_owned_resource_ids() -> None:
    actions = _ContextActionsFake()
    app = _direct_app(actions)
    paths = (
        (
            "/agent-runtime/tool-calls/call-7/approval/context",
            "tool_approval_context",
            "call-7",
        ),
        (
            "/agent-runtime/tool-calls/call-8/reconciliation/context",
            "reconciliation_context",
            "call-8",
        ),
        (
            "/memory-governance/candidates/mcand_9/decision/context",
            "memory_candidate_context",
            "mcand_9",
        ),
    )
    async with _client(app) as client:
        responses = [await client.get(f"{API}{path}") for path, _, _ in paths]

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert actions.context_calls == [
        (method, item_id) for _, method, item_id in paths
    ]
    assert [response.json()["method"] for response in responses] == [
        method for _, method, _ in paths
    ]


@pytest.mark.asyncio
async def test_context_routes_are_generic_503_without_actions() -> None:
    app = _direct_app(None)
    paths = (
        "/agent-runtime/tool-calls/call-secret/approval/context",
        "/agent-runtime/tool-calls/call-secret/reconciliation/context",
        "/memory-governance/candidates/mcand_secret/decision/context",
    )
    async with _client(app) as client:
        responses = [await client.get(f"{API}{path}") for path in paths]

    for response in responses:
        assert response.status_code == 503
        detail = response.json().get("detail")
        assert isinstance(detail, str) and detail
        assert "call-secret" not in detail
        assert "mcand_secret" not in detail
        assert "traceback" not in detail.casefold()


@pytest.mark.asyncio
async def test_browser_cannot_supply_context_trust_inputs_or_post_action_identity() -> None:
    actions = _ContextActionsFake()
    app = _direct_app(actions)
    forbidden = {
        "operator": "browser-reviewer",
        "principal": "browser-principal",
        "adapter": "browser-adapter",
        "target": "browser-target",
        "args": "browser-args",
    }
    async with _client(app) as client:
        for field, value in forbidden.items():
            get_response = await client.get(
                f"{API}/agent-runtime/tool-calls/call-7/approval/context",
                params={field: value},
            )
            assert get_response.status_code == 422, field

            post_response = await client.post(
                f"{API}/agent-runtime/tool-calls/call-7/approval",
                json={
                    "expected_token": "opaque-context-token",
                    "approval_ref": "approval:offline:7",
                    field: value,
                },
            )
            assert post_response.status_code == 422, field
    assert actions.context_calls == []
    assert actions.approval_calls == []


@pytest.mark.asyncio
async def test_memory_decision_rejects_browser_supplied_conflict_ids() -> None:
    actions = _ContextActionsFake()
    app = _direct_app(actions)
    async with _client(app) as client:
        response = await client.post(
            f"{API}/memory-governance/candidates/mcand-7/decision",
            json={
                "expected_token": "opaque-context-token",
                "decision": "approve",
                "reason_code": "reviewed_current_context",
                "operator_note": "bounded operator note",
                "conflict_ids": ["mconf_BROWSER_SUPPLIED_CANARY"],
                "occurred_at": T0.isoformat(),
            },
        )

    assert response.status_code == 422
    assert actions.memory_decision_calls == []


@pytest.mark.asyncio
async def test_memory_decision_posts_without_forwarding_conflict_ids() -> None:
    actions = _ContextActionsFake()
    app = _direct_app(actions)
    async with _client(app) as client:
        response = await client.post(
            f"{API}/memory-governance/candidates/mcand-7/decision",
            json={
                "expected_token": "opaque-context-token",
                "decision": "reject",
                "reason_code": "reviewed_current_context",
                "operator_note": "bounded operator note",
                "occurred_at": T0.isoformat(),
            },
        )

    assert response.status_code == 200
    assert len(actions.memory_decision_calls) == 1
    assert actions.memory_decision_calls[0] == {
        "candidate_id": "mcand-7",
        "expected_token": "opaque-context-token",
        "decision": "reject",
        "reason_code": "reviewed_current_context",
        "operator_note": "bounded operator note",
        "occurred_at": T0,
    }


@pytest.mark.asyncio
async def test_post_uses_context_token_and_preserves_exact_retry_and_stale_conflict() -> None:
    actions = _ContextActionsFake()
    app = _direct_app(actions)
    body = {
        "expected_token": "opaque-context-token",
        "approval_ref": "approval:offline:7",
    }
    async with _client(app) as client:
        first = await client.post(
            f"{API}/agent-runtime/tool-calls/call-7/approval",
            json=body,
        )
        retry = await client.post(
            f"{API}/agent-runtime/tool-calls/call-7/approval",
            json=body,
        )
        stale = await client.post(
            f"{API}/agent-runtime/tool-calls/call-7/approval",
            json={**body, "expected_token": "stale-context-token"},
        )

    assert first.status_code == 200
    assert first.json()["exact_retry"] is False
    assert retry.status_code == 200
    assert retry.json()["exact_retry"] is True
    assert stale.status_code == 409
    assert "STALE_CONTEXT_EXCEPTION_CANARY" not in stale.text
    assert "/private/operator/path" not in stale.text
    assert len(actions.approval_calls) == 2
