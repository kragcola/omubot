"""HTTP contracts for credential-authenticated Agent Runtime operators."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from admin.auth import AdminAuthMiddleware, _derive_signing_key, _sign_value
from admin.routes.api import create_api_router
from services.agent_runtime.ledger import AgentRuntimeLedger
from services.memory.governance_contracts import (
    CandidateEnvelopeV1,
    CardClaimV1,
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
OPERATOR_ID = "ops-alice"
OPERATOR_CREDENTIAL = "operator-credential-0123456789-abcdefghij"
WEB_SESSION_TOKEN = "web-session-token-not-an-operator-credential"
T0 = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)


def _operator_api() -> Any:
    module_name = "services.agent_runtime.admin_operator"
    if importlib.util.find_spec(module_name) is None:
        pytest.fail("credential-authenticated Admin operator factory is required")
    module = importlib.import_module(module_name)
    factory = getattr(module, "AdminOperatorActionsFactoryV1", None)
    assert isinstance(factory, type), "AdminOperatorActionsFactoryV1 is required"
    return module


async def _approval_pending_call(
    ledger: AgentRuntimeLedger,
    *,
    suffix: str,
    target_ref: str,
) -> tuple[str, str]:
    run_id = f"run-admin-operator-{suffix}"
    call_id = f"call-admin-operator-{suffix}"
    await ledger.create_run(
        run_id=run_id,
        trigger_type="message",
        trigger_ref=f"message:admin-operator:{suffix}",
        principal_kind="user",
        principal_id="42",
    )
    await ledger.transition_run(run_id, to_status="running", actor="runtime")
    await ledger.create_tool_call(
        call_id=call_id,
        run_id=run_id,
        step_id=f"step-{suffix}",
        tool_name="bounded_operator_tool",
        tool_version="1",
        owner="offline-test",
        effect="external_irreversible",
        principal_kind="user",
        principal_id="42",
        target_ref=target_ref,
        args_digest="sha256:" + hashlib.sha256(suffix.encode()).hexdigest(),
        idempotency_mode="reconcile_only",
        concurrency_mode="keyed_serial",
        concurrency_key=target_ref,
    )
    await ledger.transition_tool_call(
        call_id,
        to_status="approval_pending",
        actor="policy",
    )
    return run_id, call_id


def _candidate(suffix: str) -> CandidateEnvelopeV1:
    quote = f"operator-http-evidence-{suffix}"
    observation = ObservationV1(
        source_kind=SourceKind.USER_STATEMENT,
        producer_kind=ProducerKind.MEMO,
        producer_version="operator-http-v1",
        producer_run_id=f"operator-http-run-{suffix}",
        subject_ref="user:qq:42",
        owner_scope=OwnerScope.USER,
        owner_id="42",
        visibility=Visibility.PRIVATE,
        origin_group_ref=None,
        claim=CardClaimV1(category="preference", content=f"candidate-{suffix}"),
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
        payload={"category": "preference", "content": f"candidate-{suffix}"},
    )
    return CandidateEnvelopeV1.create(
        observation=observation,
        proposal=proposal,
        producer_kind="memo",
        producer_run_id=f"operator-http-run-{suffix}",
        producer_item_id=f"operator-http-item-{suffix}",
        produced_at=T0 + timedelta(seconds=2),
        model_output=f"operator-http-model-output-{suffix}",
    )


@pytest.fixture
async def opened_sources(
    tmp_path: Path,
) -> AsyncIterator[tuple[AgentRuntimeLedger, MemoryGovernanceStore, Any]]:
    from services.agent_runtime.operator_auth import OperatorAuthorizationStoreV1

    runtime = AgentRuntimeLedger(tmp_path / "runtime.db")
    memory = MemoryGovernanceStore(tmp_path / "memory.db")
    operators = OperatorAuthorizationStoreV1(tmp_path / "operators.db")
    await runtime.init()
    await memory.init()
    await operators.init()
    try:
        yield runtime, memory, operators
    finally:
        await operators.close()
        await memory.close()
        await runtime.close()


def _headers() -> dict[str, str]:
    return {
        "X-Agent-Runtime-Operator-Id": OPERATOR_ID,
        "Authorization": f"Bearer {OPERATOR_CREDENTIAL}",
    }


def _app(factory: Any) -> FastAPI:
    app = FastAPI()
    app.add_middleware(AdminAuthMiddleware, admin_token=WEB_SESSION_TOKEN)
    app.include_router(create_api_router(operator_action_factory=factory))
    return app


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    session = _sign_value(
        WEB_SESSION_TOKEN,
        _derive_signing_key(WEB_SESSION_TOKEN),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://admin.test",
        cookies={"admin_session": session},
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_cookie_is_only_a_web_session_and_named_credential_binds_actor(
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore, Any],
) -> None:
    runtime, memory, operators = opened_sources
    target_ref = "onebot:group:123:message:789"
    run_id, call_id = await _approval_pending_call(
        runtime,
        suffix="cookie",
        target_ref=target_ref,
    )
    await operators.provision(
        operator_id=OPERATOR_ID,
        credential=OPERATOR_CREDENTIAL,
        granted_scopes=("runtime:tool:approve",),
        resource_grants=(("runtime:tool:approve", target_ref),),
    )
    factory = _operator_api().AdminOperatorActionsFactoryV1(
        runtime_source=runtime,
        memory_source=memory,
        operator_source=operators,
    )
    app = _app(factory)

    async with _client(app) as client:
        cookie_only = await client.get(
            f"{API}/agent-runtime/tool-calls/{call_id}/approval/context"
        )
        wrong_credential = await client.get(
            f"{API}/agent-runtime/tool-calls/{call_id}/approval/context",
            headers={**_headers(), "Authorization": "Bearer wrong-credential-0123456789"},
        )
        context = await client.get(
            f"{API}/agent-runtime/tool-calls/{call_id}/approval/context",
            headers=_headers(),
        )

        assert cookie_only.status_code == 401
        assert wrong_credential.status_code == 401
        assert context.status_code == 200
        assert OPERATOR_ID not in context.text

        approved = await client.post(
            f"{API}/agent-runtime/tool-calls/{call_id}/approval",
            headers=_headers(),
            json={
                "expected_token": context.json()["expected_token"],
                "approval_ref": "approval:operator-http:cookie",
            },
        )

    assert approved.status_code == 200
    events = await runtime.list_events(run_id=run_id)
    approval_events = [event for event in events if event.event_type == "approval_granted"]
    assert len(approval_events) == 1
    assert approval_events[0].actor == f"operator:{OPERATOR_ID}"


@pytest.mark.asyncio
async def test_exact_target_and_memory_candidate_acl_gate_context_and_decision(
    opened_sources: tuple[AgentRuntimeLedger, MemoryGovernanceStore, Any],
) -> None:
    runtime, memory, operators = opened_sources
    allowed_target = "onebot:group:123:message:789"
    denied_target = "onebot:group:123:message:790"
    _allowed_run, allowed_call = await _approval_pending_call(
        runtime,
        suffix="allowed",
        target_ref=allowed_target,
    )
    denied_run, denied_call = await _approval_pending_call(
        runtime,
        suffix="denied",
        target_ref=denied_target,
    )
    allowed_candidate = _candidate("allowed")
    denied_candidate = _candidate("denied")
    await memory.append_candidate(allowed_candidate)
    await memory.append_candidate(denied_candidate)
    await operators.provision(
        operator_id=OPERATOR_ID,
        credential=OPERATOR_CREDENTIAL,
        granted_scopes=("memory:candidate:decide", "runtime:tool:approve"),
        resource_grants=(
            ("runtime:tool:approve", allowed_target),
            (
                "memory:candidate:decide",
                f"memory_candidate:{allowed_candidate.candidate_id}",
            ),
        ),
    )
    factory = _operator_api().AdminOperatorActionsFactoryV1(
        runtime_source=runtime,
        memory_source=memory,
        operator_source=operators,
    )
    app = _app(factory)

    async with _client(app) as client:
        allowed_context = await client.get(
            f"{API}/agent-runtime/tool-calls/{allowed_call}/approval/context",
            headers=_headers(),
        )
        denied_context = await client.get(
            f"{API}/agent-runtime/tool-calls/{denied_call}/approval/context",
            headers=_headers(),
        )
        denied_approval = await client.post(
            f"{API}/agent-runtime/tool-calls/{denied_call}/approval",
            headers=_headers(),
            json={
                "expected_token": "untrusted-browser-token",
                "approval_ref": "approval:operator-http:denied",
            },
        )
        allowed_memory_context = await client.get(
            f"{API}/memory-governance/candidates/{allowed_candidate.candidate_id}/decision/context",
            headers=_headers(),
        )
        denied_memory_context = await client.get(
            f"{API}/memory-governance/candidates/{denied_candidate.candidate_id}/decision/context",
            headers=_headers(),
        )
        denied_memory_decision = await client.post(
            f"{API}/memory-governance/candidates/{denied_candidate.candidate_id}/decision",
            headers=_headers(),
            json={
                "expected_token": "untrusted-browser-token",
                "decision": "reject",
                "reason_code": "operator_acl_denied",
                "occurred_at": T0.isoformat(),
            },
        )

    assert allowed_context.status_code == 200
    assert denied_context.status_code == 403
    assert denied_approval.status_code == 403
    assert allowed_memory_context.status_code == 200
    assert denied_memory_context.status_code == 403
    assert denied_memory_decision.status_code == 403

    denied_events = await runtime.list_events(run_id=denied_run)
    assert not any(event.event_type == "approval_granted" for event in denied_events)
    assert await memory.list_promotion_events(denied_candidate.candidate_id) == ()
