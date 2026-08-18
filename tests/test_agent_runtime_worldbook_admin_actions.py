"""Contract tests for per-proposal Worldbook schedule decisions."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from admin.auth import AdminAuthMiddleware, _derive_signing_key, _sign_value
from admin.routes.api import create_api_router
from services.agent_runtime.admin_actions import OfflineAdminActionsV1, OperatorIdentityV1
from services.agent_runtime.admin_operator import AdminOperatorActionsFactoryV1
from services.agent_runtime.ledger import AgentRuntimeLedger
from services.agent_runtime.operator_auth import OperatorAuthorizationStoreV1
from services.memory.governance_store import MemoryGovernanceStore
from services.social_narrative import SocialExperience
from services.worldbook.governance_contracts import LEGACY_SINGLETON_WORLD_ID
from services.worldbook.governance_store import WorldbookGovernanceStore
from services.worldbook.governed_adapters import (
    build_schedule_event_proposal,
    build_social_event_proposal,
)

T0 = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
SCOPE = "worldbook:proposal:decide"


class _Committer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def commit_approved(self, proposal_id: str) -> Any:
        self.calls.append(proposal_id)
        return SimpleNamespace(status="committed", reason="", receipt_present=True)


async def _proposal(store: WorldbookGovernanceStore) -> Any:
    proposal = build_schedule_event_proposal(
        world_id=LEGACY_SINGLETON_WORLD_ID,
        target_arc_id="arc.main",
        schedule_date="2026-08-18",
        summary="设备检查日；排练前先解决设备隐患。",
        proposed_at=T0,
    )
    await store.append_proposal(proposal)
    return proposal


async def _social_proposal(store: WorldbookGovernanceStore) -> Any:
    proposal = build_social_event_proposal(
        record=SocialExperience(
            experience_id="social.exp.admin-worldbook",
            group_id="123456789",
            user_id="987654321",
            entity_kind="factual",
            evidence_message_id="admin-worldbook-message",
            evidence_time="2026-08-18T18:00:00+08:00",
            evidence_source="onebot",
            user_text="bounded social evidence",
            bot_reply="bounded reply",
            status="active",
            created_at="2026-08-18T18:01:00+08:00",
        ),
        world_id=LEGACY_SINGLETON_WORLD_ID,
        target_arc_id="arc.main",
        current_group_id="123456789",
        current_user_id="987654321",
        proposed_at=T0,
    )
    await store.append_proposal(proposal)
    return proposal


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


@pytest.mark.asyncio
async def test_worldbook_schedule_decision_is_exact_proposal_scoped(tmp_path) -> None:
    runtime = AgentRuntimeLedger(tmp_path / "runtime.db")
    memory = MemoryGovernanceStore(tmp_path / "memory.db")
    worldbook = WorldbookGovernanceStore(tmp_path / "worldbook.db")
    await runtime.init()
    await memory.init()
    await worldbook.init()
    committer = _Committer()
    actions = OfflineAdminActionsV1(
        runtime_source=runtime,
        memory_source=memory,
        worldbook_source=worldbook,
        worldbook_committer=committer,
        operator=OperatorIdentityV1(
            operator_id="ops-alice",
            granted_scopes=(SCOPE,),
        ),
        resource_authorizer=lambda scope, resource: _allow(scope, resource),
    )
    proposal = await _proposal(worldbook)
    try:
        context = await actions.worldbook_proposal_context(proposal.proposal_id)
        assert context["resource"] == {
            "kind": "worldbook_proposal",
            "id": proposal.proposal_id,
        }
        assert context["state"] == "pending"

        result = await actions.decide_worldbook_proposal(
            proposal.proposal_id,
            expected_token=context["expected_token"],
            decision="approve",
            reason_code="schedule_reviewed",
        )
        decision = await worldbook.get_operator_decision(proposal.proposal_id)
        assert result["status"] == "approved_pending"
        assert result["commit_status"] == "committed"
        assert result["receipt_present"] is False
        assert result["reducer_commit_requested"] is True
        assert result["reducer_commit_completed"] is False
        assert decision is not None
        assert decision.operator_ref == "operator:ops-alice"
        assert committer.calls == [proposal.proposal_id]
    finally:
        await worldbook.close()
        await memory.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_worldbook_schedule_decision_routes_accept_only_context_token_and_decision(
    tmp_path,
) -> None:
    runtime = AgentRuntimeLedger(tmp_path / "runtime.db")
    memory = MemoryGovernanceStore(tmp_path / "memory.db")
    worldbook = WorldbookGovernanceStore(tmp_path / "worldbook.db")
    await runtime.init()
    await memory.init()
    await worldbook.init()
    committer = _Committer()
    actions = OfflineAdminActionsV1(
        runtime_source=runtime,
        memory_source=memory,
        worldbook_source=worldbook,
        worldbook_committer=committer,
        operator=OperatorIdentityV1(
            operator_id="ops-alice",
            granted_scopes=(SCOPE,),
        ),
        resource_authorizer=lambda scope, resource: _allow(scope, resource),
    )
    proposal = await _proposal(worldbook)
    app = FastAPI()
    app.include_router(create_api_router(actions=actions))
    try:
        async with await _client(app) as client:
            context = await client.get(
                "/api/admin/worldbook-governance/proposals/"
                f"{proposal.proposal_id}/decision/context"
            )
            assert context.status_code == 200
            expected_token = context.json()["expected_token"]

            invalid = await client.post(
                "/api/admin/worldbook-governance/proposals/"
                f"{proposal.proposal_id}/decision",
                json={
                    "expected_token": expected_token,
                    "decision": "approve",
                    "reason_code": "schedule_reviewed",
                    "operator_ref": "forged",
                },
            )
            assert invalid.status_code == 422

            decided = await client.post(
                "/api/admin/worldbook-governance/proposals/"
                f"{proposal.proposal_id}/decision",
                json={
                    "expected_token": expected_token,
                    "decision": "approve",
                    "reason_code": "schedule_reviewed",
                },
            )
        assert decided.status_code == 200
        assert decided.json()["status"] == "approved_pending"
        assert committer.calls == [proposal.proposal_id]
    finally:
        await worldbook.close()
        await memory.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_authenticated_factory_requires_the_exact_worldbook_proposal_grant(
    tmp_path,
) -> None:
    runtime = AgentRuntimeLedger(tmp_path / "runtime.db")
    memory = MemoryGovernanceStore(tmp_path / "memory.db")
    worldbook = WorldbookGovernanceStore(tmp_path / "worldbook.db")
    operators = OperatorAuthorizationStoreV1(tmp_path / "operators.db")
    await runtime.init()
    await memory.init()
    await worldbook.init()
    await operators.init()
    committer = _Committer()
    proposal = await _proposal(worldbook)
    credential = "operator-credential-0123456789-abcdefghij"
    await operators.provision(
        operator_id="ops-alice",
        credential=credential,
        granted_scopes=(SCOPE,),
        resource_grants=((SCOPE, f"worldbook_proposal:{proposal.proposal_id}"),),
    )
    factory = AdminOperatorActionsFactoryV1(
        runtime_source=runtime,
        memory_source=memory,
        worldbook_source=worldbook,
        worldbook_committer=committer,
        operator_source=operators,
    )
    try:
        actions = await factory.authenticate(
            operator_id="ops-alice",
            credential=credential,
        )
        context = await actions.worldbook_proposal_context(proposal.proposal_id)
        result = await actions.decide_worldbook_proposal(
            proposal.proposal_id,
            expected_token=context["expected_token"],
            decision="reject",
            reason_code="schedule_rejected",
        )

        assert result["status"] == "rejected"
        assert committer.calls == []
    finally:
        await operators.close()
        await worldbook.close()
        await memory.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_missing_worldbook_committer_does_not_persist_an_approval(tmp_path) -> None:
    runtime = AgentRuntimeLedger(tmp_path / "runtime.db")
    memory = MemoryGovernanceStore(tmp_path / "memory.db")
    worldbook = WorldbookGovernanceStore(tmp_path / "worldbook.db")
    await runtime.init()
    await memory.init()
    await worldbook.init()
    actions = OfflineAdminActionsV1(
        runtime_source=runtime,
        memory_source=memory,
        worldbook_source=worldbook,
        operator=OperatorIdentityV1(
            operator_id="ops-alice",
            granted_scopes=(SCOPE,),
        ),
        resource_authorizer=lambda scope, resource: _allow(scope, resource),
    )
    proposal = await _proposal(worldbook)
    try:
        context = await actions.worldbook_proposal_context(proposal.proposal_id)
        with pytest.raises(RuntimeError, match="committer is unavailable"):
            await actions.decide_worldbook_proposal(
                proposal.proposal_id,
                expected_token=context["expected_token"],
                decision="approve",
                reason_code="schedule_reviewed",
            )
        assert await worldbook.get_operator_decision(proposal.proposal_id) is None
    finally:
        await worldbook.close()
        await memory.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_worldbook_decision_http_fails_closed_for_cookie_acl_token_and_source(
    tmp_path,
) -> None:
    runtime = AgentRuntimeLedger(tmp_path / "runtime.db")
    memory = MemoryGovernanceStore(tmp_path / "memory.db")
    worldbook = WorldbookGovernanceStore(tmp_path / "worldbook.db")
    operators = OperatorAuthorizationStoreV1(tmp_path / "operators.db")
    await runtime.init()
    await memory.init()
    await worldbook.init()
    await operators.init()
    proposal = await _proposal(worldbook)
    social = await _social_proposal(worldbook)
    credential = "worldbook-operator-credential-0123456789"
    session_token = "worldbook-admin-session-token-0123456789"
    await operators.provision(
        operator_id="ops-alice",
        credential=credential,
        granted_scopes=(SCOPE,),
        resource_grants=(
            (SCOPE, f"worldbook_proposal:{proposal.proposal_id}"),
            (SCOPE, f"worldbook_proposal:{social.proposal_id}"),
        ),
    )
    await operators.provision(
        operator_id="ops-bob",
        credential="worldbook-wrong-scope-credential-0123456789",
        granted_scopes=("runtime:tool:approve",),
        resource_grants=(),
    )
    await operators.provision(
        operator_id="ops-carol",
        credential="worldbook-wrong-resource-credential-0123456789",
        granted_scopes=(SCOPE,),
        resource_grants=((SCOPE, "worldbook_proposal:not-this-proposal"),),
    )
    factory = AdminOperatorActionsFactoryV1(
        runtime_source=runtime,
        memory_source=memory,
        worldbook_source=worldbook,
        worldbook_committer=_Committer(),
        operator_source=operators,
    )
    app = FastAPI()
    app.add_middleware(AdminAuthMiddleware, admin_token=session_token)
    app.include_router(create_api_router(operator_action_factory=factory))
    base = "/api/admin/worldbook-governance/proposals"
    alice_headers = {
        "X-Agent-Runtime-Operator-Id": "ops-alice",
        "Authorization": f"Bearer {credential}",
    }
    bob_headers = {
        "X-Agent-Runtime-Operator-Id": "ops-bob",
        "Authorization": "Bearer worldbook-wrong-scope-credential-0123456789",
    }
    carol_headers = {
        "X-Agent-Runtime-Operator-Id": "ops-carol",
        "Authorization": "Bearer worldbook-wrong-resource-credential-0123456789",
    }
    session = _sign_value(session_token, _derive_signing_key(session_token))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://admin.test",
        ) as operator_client:
            no_cookie_context = await operator_client.get(
                f"{base}/{proposal.proposal_id}/decision/context",
                headers=alice_headers,
            )
            operator_headers_on_other_route = await operator_client.get(
                "/api/admin/agent-runtime/summary",
                headers=alice_headers,
            )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://admin.test",
            cookies={"admin_session": session},
        ) as client:
            cookie_only = await client.get(
                f"{base}/{proposal.proposal_id}/decision/context"
            )
            wrong_scope = await client.get(
                f"{base}/{proposal.proposal_id}/decision/context",
                headers=bob_headers,
            )
            wrong_resource = await client.get(
                f"{base}/{proposal.proposal_id}/decision/context",
                headers=carol_headers,
            )
            context = await client.get(
                f"{base}/{proposal.proposal_id}/decision/context",
                headers=alice_headers,
            )
            assert context.status_code == 200
            token = context.json()["expected_token"]
            extra_body = await client.post(
                f"{base}/{proposal.proposal_id}/decision",
                headers=alice_headers,
                json={
                    "expected_token": token,
                    "decision": "reject",
                    "reason_code": "schedule_rejected",
                    "operator_ref": "forged",
                },
            )
            rejected = await client.post(
                f"{base}/{proposal.proposal_id}/decision",
                headers=alice_headers,
                json={
                    "expected_token": token,
                    "decision": "reject",
                    "reason_code": "schedule_rejected",
                },
            )
            stale = await client.post(
                f"{base}/{proposal.proposal_id}/decision",
                headers=alice_headers,
                json={
                    "expected_token": token,
                    "decision": "reject",
                    "reason_code": "schedule_rejected",
                },
            )
            non_schedule = await client.get(
                f"{base}/{social.proposal_id}/decision/context",
                headers=alice_headers,
            )

        assert no_cookie_context.status_code == 200
        assert operator_headers_on_other_route.status_code == 401
        assert cookie_only.status_code == 401
        assert wrong_scope.status_code == 403
        assert wrong_resource.status_code == 403
        assert extra_body.status_code == 422
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"
        assert stale.status_code == 409
        assert non_schedule.status_code == 409
        assert await worldbook.get_operator_decision(proposal.proposal_id) is not None
        assert await worldbook.get_operator_decision(social.proposal_id) is None
    finally:
        await operators.close()
        await worldbook.close()
        await memory.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_operator_header_bypass_fails_closed_without_named_operator_factory(
    tmp_path,
) -> None:
    runtime = AgentRuntimeLedger(tmp_path / "runtime.db")
    memory = MemoryGovernanceStore(tmp_path / "memory.db")
    worldbook = WorldbookGovernanceStore(tmp_path / "worldbook.db")
    await runtime.init()
    await memory.init()
    await worldbook.init()
    proposal = await _proposal(worldbook)
    actions = OfflineAdminActionsV1(
        runtime_source=runtime,
        memory_source=memory,
        worldbook_source=worldbook,
        worldbook_committer=_Committer(),
        operator=OperatorIdentityV1(
            operator_id="ops-static",
            granted_scopes=(SCOPE,),
        ),
        resource_authorizer=lambda scope, resource: _allow(scope, resource),
    )
    app = FastAPI()
    app.add_middleware(AdminAuthMiddleware, admin_token="browser-session")
    app.include_router(create_api_router(actions=actions))
    browser_session = _sign_value(
        "browser-session",
        _derive_signing_key("browser-session"),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://admin.test",
        ) as client:
            response = await client.get(
                "/api/admin/worldbook-governance/proposals/"
                f"{proposal.proposal_id}/decision/context",
                headers={
                    "X-Agent-Runtime-Operator-Id": "forged",
                    "Authorization": "Bearer forged-credential",
                },
            )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://admin.test",
            cookies={"admin_session": browser_session},
        ) as client:
            cookie_response = await client.get(
                "/api/admin/worldbook-governance/proposals/"
                f"{proposal.proposal_id}/decision/context",
                headers={
                    "X-Agent-Runtime-Operator-Id": "forged",
                    "Authorization": "Bearer forged-credential",
                },
            )
        assert response.status_code == 503
        assert cookie_response.status_code == 503
        assert await worldbook.get_operator_decision(proposal.proposal_id) is None
    finally:
        await worldbook.close()
        await memory.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_worldbook_decision_rechecks_a_revoked_exact_resource_grant(tmp_path) -> None:
    runtime = AgentRuntimeLedger(tmp_path / "runtime.db")
    memory = MemoryGovernanceStore(tmp_path / "memory.db")
    worldbook = WorldbookGovernanceStore(tmp_path / "worldbook.db")
    operators = OperatorAuthorizationStoreV1(tmp_path / "operators.db")
    await runtime.init()
    await memory.init()
    await worldbook.init()
    await operators.init()
    proposal = await _proposal(worldbook)
    credential = "worldbook-revocation-credential-0123456789"
    await operators.provision(
        operator_id="ops-alice",
        credential=credential,
        granted_scopes=(SCOPE,),
        resource_grants=((SCOPE, f"worldbook_proposal:{proposal.proposal_id}"),),
    )
    factory = AdminOperatorActionsFactoryV1(
        runtime_source=runtime,
        memory_source=memory,
        worldbook_source=worldbook,
        worldbook_committer=_Committer(),
        operator_source=operators,
    )
    try:
        actions = await factory.authenticate(
            operator_id="ops-alice",
            credential=credential,
        )
        context = await actions.worldbook_proposal_context(proposal.proposal_id)
        await operators.provision(
            operator_id="ops-alice",
            credential=credential,
            granted_scopes=(SCOPE,),
            resource_grants=(),
        )

        with pytest.raises(PermissionError, match="resource is unavailable"):
            await actions.decide_worldbook_proposal(
                proposal.proposal_id,
                expected_token=context["expected_token"],
                decision="reject",
                reason_code="schedule_rejected",
            )
        assert await worldbook.get_operator_decision(proposal.proposal_id) is None
    finally:
        await operators.close()
        await worldbook.close()
        await memory.close()
        await runtime.close()


async def _allow(scope: str, resource: str) -> bool:
    return scope == SCOPE and resource.startswith("worldbook_proposal:")
