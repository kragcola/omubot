"""HTTP contracts for the dark/offline Agent Runtime v2 Admin API."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from admin.routes.api import create_api_router
from services.agent_runtime.admin_query import RuntimeAdminQueryV1
from services.agent_runtime.ledger import AgentRuntimeLedger
from services.agent_runtime.rollout_readiness import RolloutReadinessV1
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
from services.memory.governance_query import MemoryGovernanceAdminQueryV1
from services.memory.governance_store import MemoryGovernanceStore
from services.worldbook.governance_store import WorldbookGovernanceStore

API = "/api/admin"
T0 = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)


def _create_agent_runtime_router(**dependencies: Any) -> APIRouter:
    """Keep pre-implementation RED collectible and failing at HTTP behavior."""

    module_name = "admin.routes.api.agent_runtime"
    if importlib.util.find_spec(module_name) is None:
        return APIRouter()
    module = importlib.import_module(module_name)
    factory = getattr(module, "create_agent_runtime_router", None)
    assert callable(factory), "create_agent_runtime_router() is required"
    router = factory(**dependencies)
    assert isinstance(router, APIRouter)
    return router


def _direct_app(**dependencies: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(
        _create_agent_runtime_router(**dependencies),
        prefix=API,
    )
    return app


def _aggregate_router(**dependencies: Any) -> APIRouter:
    factory: Any = create_api_router
    router = factory(**dependencies)
    assert isinstance(router, APIRouter)
    return router


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://admin.test",
    ) as client:
        yield client


def _serialized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _recursive_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return set(value) | {
            key for item in value.values() for key in _recursive_keys(item)
        }
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return {key for item in value for key in _recursive_keys(item)}
    return set()


class _RuntimeQuerySpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def _record(
        self,
        name: str,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append((name, args, kwargs))
        return {"source": "explicit-runtime-query", "method": name}

    async def summary(self) -> dict[str, Any]:
        return await self._record("summary")

    async def list_runs(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        return await self._record(
            "list_runs",
            limit=limit,
            cursor=cursor,
            status=status,
        )

    async def get_run(self, run_id: str) -> dict[str, Any]:
        return await self._record("get_run", run_id)

    async def list_tool_calls(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        status: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._record(
            "list_tool_calls",
            limit=limit,
            cursor=cursor,
            status=status,
            run_id=run_id,
        )

    async def list_events(
        self,
        run_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return await self._record(
            "list_events",
            run_id,
            limit=limit,
            cursor=cursor,
        )


class _MemoryQuerySpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def _record(
        self,
        name: str,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append((name, args, kwargs))
        return {"source": "explicit-memory-query", "method": name}

    async def summary(self) -> dict[str, Any]:
        return await self._record("summary")

    async def list_observations(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return await self._record(
            "list_observations",
            limit=limit,
            cursor=cursor,
        )

    async def list_candidates(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        projection_kind: str | None = None,
        operation: str | None = None,
    ) -> dict[str, Any]:
        return await self._record(
            "list_candidates",
            limit=limit,
            cursor=cursor,
            projection_kind=projection_kind,
            operation=operation,
        )

    async def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        return await self._record("get_candidate", candidate_id)

    async def list_conflicts(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return await self._record(
            "list_conflicts",
            limit=limit,
            cursor=cursor,
        )


class _ReadinessSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def dark_readiness(self) -> dict[str, Any]:
        self.calls.append("dark_readiness")
        return {"source": "explicit-readiness", "method": "dark_readiness"}

    async def activation_readiness(self) -> dict[str, Any]:
        self.calls.append("activation_readiness")
        return {"source": "explicit-readiness", "method": "activation_readiness"}

    async def rollback_readiness(self) -> dict[str, Any]:
        self.calls.append("rollback_readiness")
        return {"source": "explicit-readiness", "method": "rollback_readiness"}


class _ActionsFake:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def _record(
        self,
        action: str,
        item_id: str,
        **values: Any,
    ) -> dict[str, Any]:
        self.calls.append((action, item_id, values))
        if self.error is not None:
            raise self.error
        return {"accepted": True, "action": action, "item_id": item_id}

    async def approve_tool_call(
        self,
        call_id: str,
        *,
        expected_token: str,
        approval_ref: str,
    ) -> dict[str, Any]:
        return await self._record(
            "approve_tool_call",
            call_id,
            expected_token=expected_token,
            approval_ref=approval_ref,
        )

    async def reconcile_tool_call(
        self,
        call_id: str,
        *,
        expected_token: str,
        decision: str,
        evidence_ref: str,
        operator_note: str,
        external_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._record(
            "reconcile_tool_call",
            call_id,
            expected_token=expected_token,
            decision=decision,
            evidence_ref=evidence_ref,
            operator_note=operator_note,
            external_id=external_id,
        )

    async def decide_memory_candidate(
        self,
        candidate_id: str,
        *,
        expected_token: str,
        decision: str,
        reason_code: str,
        operator_note: str | None = None,
        occurred_at: datetime,
    ) -> dict[str, Any]:
        return await self._record(
            "decide_memory_candidate",
            candidate_id,
            expected_token=expected_token,
            decision=decision,
            reason_code=reason_code,
            operator_note=operator_note,
            occurred_at=occurred_at,
        )


class _FailingRuntimeQuery(_RuntimeQuerySpy):
    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error

    async def summary(self) -> dict[str, Any]:
        raise self.error

    async def list_runs(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        raise self.error

    async def get_run(self, run_id: str) -> dict[str, Any]:
        raise self.error


def _memory_candidate() -> CandidateEnvelopeV1:
    quote = "EVIDENCE_QUOTE_CANARY_ADMIN_API"
    evidence = EvidenceAtomV1(
        evidence_ref="message:onebot:private:42:EVIDENCE_REF_CANARY_ADMIN_API",
        content_sha256=sha256_text(quote),
        quote=quote,
        actor_ref="user:qq:42",
        occurred_at=T0,
    )
    observation = ObservationV1(
        source_kind=SourceKind.USER_STATEMENT,
        producer_kind=ProducerKind.MEMO,
        producer_version="memo-admin-api-v1",
        producer_run_id="run-MEMORY_PRODUCER_CANARY_ADMIN_API",
        subject_ref="user:qq:42",
        owner_scope=OwnerScope.USER,
        owner_id="42",
        visibility=Visibility.PRIVATE,
        origin_group_ref=None,
        claim=CardClaimV1(
            category="preference",
            content="MEMORY_PAYLOAD_CANARY_ADMIN_API",
        ),
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
        target_ref=None,
        payload={
            "category": "preference",
            "content": "MEMORY_PAYLOAD_CANARY_ADMIN_API",
        },
    )
    return CandidateEnvelopeV1.create(
        observation=observation,
        proposal=proposal,
        producer_kind="memo",
        producer_run_id="run-MEMORY_PRODUCER_CANARY_ADMIN_API",
        producer_item_id="item-admin-api",
        produced_at=T0 + timedelta(seconds=2),
        model_output="MODEL_ARGS_CANARY_ADMIN_API",
    )


async def test_missing_dependencies_return_existing_unavailable_reports_without_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    before = tuple(tmp_path.iterdir())
    app = _direct_app(
        runtime_query=None,
        memory_query=None,
        readiness=None,
        actions=None,
    )
    query_paths = (
        "/agent-runtime/summary",
        "/agent-runtime/runs",
        "/agent-runtime/runs/run-missing",
        "/agent-runtime/tool-calls",
        "/agent-runtime/runs/run-missing/events",
        "/memory-governance/summary",
        "/memory-governance/observations",
        "/memory-governance/candidates",
        "/memory-governance/candidates/mcand_missing",
        "/memory-governance/conflicts",
    )
    async with _client(app) as client:
        for path in query_paths:
            response = await client.get(f"{API}{path}")
            assert response.status_code == 200, path
            body = response.json()
            assert body["available"] is False
            assert body["reason"] == "source_unavailable"
            assert body["mode"] == "offline_dark"

        dark = (await client.get(f"{API}/agent-runtime/readiness/dark")).json()
        activation = (
            await client.get(f"{API}/agent-runtime/readiness/activation")
        ).json()
        rollback = (
            await client.get(f"{API}/agent-runtime/readiness/rollback")
        ).json()

    assert dark["report_only"] is True
    assert dark["status"] == "not_ready"
    assert dark["sources"]["runtime"]["reason"] == "source_unavailable"
    assert dark["sources"]["memory"]["reason"] == "source_unavailable"
    assert activation["report_only"] is True
    assert activation["activation_authorized"] is False
    assert rollback["report_only"] is True
    assert tuple(tmp_path.iterdir()) == before


async def test_aggregate_router_uses_only_explicit_p5_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    runtime_path = tmp_path / "must-not-open-runtime.db"
    memory_path = tmp_path / "must-not-open-memory.db"
    derived_runtime = _RuntimeQuerySpy()
    derived_memory = _MemoryQuerySpy()
    ctx = SimpleNamespace(
        agent_runtime_query=derived_runtime,
        runtime_query=derived_runtime,
        agent_runtime_ledger=AgentRuntimeLedger(runtime_path),
        memory_governance_query=derived_memory,
        memory_query=derived_memory,
        memory_governance_store=MemoryGovernanceStore(memory_path),
        storage_dir=tmp_path,
    )
    explicit_runtime = _RuntimeQuerySpy()
    explicit_memory = _MemoryQuerySpy()
    explicit_readiness = _ReadinessSpy()
    explicit_actions = _ActionsFake()

    explicit_app = FastAPI()
    explicit_app.include_router(
        _aggregate_router(
            ctx=ctx,
            runtime_query=explicit_runtime,
            memory_query=explicit_memory,
            readiness=explicit_readiness,
            actions=explicit_actions,
            repo_root=tmp_path,
        )
    )
    async with _client(explicit_app) as client:
        response = await client.get(f"{API}/agent-runtime/summary")
    assert response.status_code == 200
    assert response.json() == {
        "source": "explicit-runtime-query",
        "method": "summary",
    }
    assert explicit_runtime.calls == [("summary", (), {})]
    assert derived_runtime.calls == []
    assert derived_memory.calls == []

    unwired_app = FastAPI()
    unwired_app.include_router(_aggregate_router(ctx=ctx, repo_root=tmp_path))
    async with _client(unwired_app) as client:
        unwired = await client.get(f"{API}/agent-runtime/summary")
    assert unwired.status_code == 200
    assert unwired.json()["reason"] == "source_unavailable"
    assert not runtime_path.exists()
    assert not memory_path.exists()


async def test_get_routes_forward_exact_bounded_filters_to_explicit_services() -> None:
    runtime = _RuntimeQuerySpy()
    memory = _MemoryQuerySpy()
    readiness = _ReadinessSpy()
    app = _direct_app(
        runtime_query=runtime,
        memory_query=memory,
        readiness=readiness,
        actions=None,
    )
    requests = (
        ("/agent-runtime/summary", None),
        (
            "/agent-runtime/runs",
            {"limit": "7", "cursor": "run-cursor", "status": "failed"},
        ),
        ("/agent-runtime/runs/run-7", None),
        (
            "/agent-runtime/tool-calls",
            {
                "limit": "9",
                "cursor": "call-cursor",
                "status": "unknown",
                "run_id": "run-7",
            },
        ),
        (
            "/agent-runtime/runs/run-7/events",
            {"limit": "11", "cursor": "event-cursor"},
        ),
        ("/memory-governance/summary", None),
        (
            "/memory-governance/observations",
            {"limit": "13", "cursor": "observation-cursor"},
        ),
        (
            "/memory-governance/candidates",
            {
                "limit": "17",
                "cursor": "candidate-cursor",
                "projection_kind": "card",
                "operation": "create",
            },
        ),
        ("/memory-governance/candidates/mcand_7", None),
        (
            "/memory-governance/conflicts",
            {"limit": "19", "cursor": "conflict-cursor"},
        ),
        ("/agent-runtime/readiness/dark", None),
        ("/agent-runtime/readiness/activation", None),
        ("/agent-runtime/readiness/rollback", None),
    )
    async with _client(app) as client:
        for path, params in requests:
            response = await client.get(f"{API}{path}", params=params)
            assert response.status_code == 200, path

    assert runtime.calls == [
        ("summary", (), {}),
        (
            "list_runs",
            (),
            {"limit": 7, "cursor": "run-cursor", "status": "failed"},
        ),
        ("get_run", ("run-7",), {}),
        (
            "list_tool_calls",
            (),
            {
                "limit": 9,
                "cursor": "call-cursor",
                "status": "unknown",
                "run_id": "run-7",
            },
        ),
        (
            "list_events",
            ("run-7",),
            {"limit": 11, "cursor": "event-cursor"},
        ),
    ]
    assert memory.calls == [
        ("summary", (), {}),
        (
            "list_observations",
            (),
            {"limit": 13, "cursor": "observation-cursor"},
        ),
        (
            "list_candidates",
            (),
            {
                "limit": 17,
                "cursor": "candidate-cursor",
                "projection_kind": "card",
                "operation": "create",
            },
        ),
        ("get_candidate", ("mcand_7",), {}),
        (
            "list_conflicts",
            (),
            {"limit": 19, "cursor": "conflict-cursor"},
        ),
    ]
    assert readiness.calls == [
        "dark_readiness",
        "activation_readiness",
        "rollback_readiness",
    ]


async def test_runtime_and_memory_get_routes_reject_unknown_query_parameters() -> None:
    runtime = _RuntimeQuerySpy()
    memory = _MemoryQuerySpy()
    readiness = _ReadinessSpy()
    app = _direct_app(
        runtime_query=runtime,
        memory_query=memory,
        readiness=readiness,
        actions=None,
    )
    paths = (
        "/agent-runtime/summary?browser_trust=forbidden",
        "/agent-runtime/runs?browser_trust=forbidden",
        "/agent-runtime/runs/run-query?browser_trust=forbidden",
        "/agent-runtime/tool-calls?browser_trust=forbidden",
        "/agent-runtime/runs/run-query/events?browser_trust=forbidden",
        "/agent-runtime/readiness/dark?browser_trust=forbidden",
        "/memory-governance/summary?browser_trust=forbidden",
        "/memory-governance/observations?browser_trust=forbidden",
        "/memory-governance/candidates?browser_trust=forbidden",
        "/memory-governance/candidates/mcand-query?browser_trust=forbidden",
        "/memory-governance/conflicts?browser_trust=forbidden",
    )

    async with _client(app) as client:
        for path in paths:
            response = await client.get(f"{API}{path}")
            assert response.status_code == 422, path

    assert runtime.calls == []
    assert memory.calls == []
    assert readiness.calls == []


async def test_real_open_sources_expose_only_redacted_query_and_readiness_dtos(
    tmp_path: Path,
) -> None:
    candidate = _memory_candidate()
    runtime_source = AgentRuntimeLedger(tmp_path / "runtime.db")
    memory_source = MemoryGovernanceStore(tmp_path / "memory.db")
    worldbook_source = WorldbookGovernanceStore(tmp_path / "worldbook.db")
    await runtime_source.init()
    await memory_source.init()
    await worldbook_source.init()
    try:
        await runtime_source.create_run(
            run_id="run-admin-api-safe",
            trigger_type="message",
            trigger_ref="TRIGGER_CANARY_ADMIN_API",
            principal_kind="user",
            principal_id="PRINCIPAL_CANARY_ADMIN_API",
            session_id="SESSION_CANARY_ADMIN_API",
            group_id="GROUP_CANARY_ADMIN_API",
            metadata={"secret": "RUN_METADATA_CANARY_ADMIN_API"},
        )
        await runtime_source.create_tool_call(
            call_id="call-admin-api-safe",
            run_id="run-admin-api-safe",
            step_id="step-admin-api-safe",
            tool_name="safe_tool",
            tool_version="1",
            owner="test-owner",
            effect="write_local",
            principal_kind="user",
            principal_id="PRINCIPAL_CANARY_ADMIN_API",
            target_ref="TARGET_CANARY_ADMIN_API",
            args_digest="ARGS_CANARY_ADMIN_API",
            idempotency_mode="not_needed",
            metadata={"secret": "TOOL_METADATA_CANARY_ADMIN_API"},
        )
        await memory_source.append_candidate(candidate)

        app = _direct_app(
            runtime_query=RuntimeAdminQueryV1(runtime_source),
            memory_query=MemoryGovernanceAdminQueryV1(memory_source),
            readiness=RolloutReadinessV1(
                runtime_source,
                memory_source,
                worldbook_source=worldbook_source,
            ),
            actions=None,
        )
        paths = (
            "/agent-runtime/summary",
            "/agent-runtime/runs?limit=1&status=queued",
            "/agent-runtime/runs/run-admin-api-safe",
            (
                "/agent-runtime/tool-calls?limit=1&status=proposed"
                "&run_id=run-admin-api-safe"
            ),
            "/agent-runtime/runs/run-admin-api-safe/events?limit=10",
            "/memory-governance/summary",
            "/memory-governance/observations?limit=1",
            "/memory-governance/candidates?limit=1&projection_kind=card&operation=create",
            f"/memory-governance/candidates/{candidate.candidate_id}",
            "/memory-governance/conflicts?limit=1",
            "/agent-runtime/readiness/dark",
            "/agent-runtime/readiness/activation",
            "/agent-runtime/readiness/rollback",
        )
        bodies: list[dict[str, Any]] = []
        async with _client(app) as client:
            for path in paths:
                response = await client.get(f"{API}{path}")
                assert response.status_code == 200, path
                bodies.append(response.json())
    finally:
        await runtime_source.close()
        await memory_source.close()
        await worldbook_source.close()

    assert bodies[1]["items"][0]["run_id"] == "run-admin-api-safe"
    assert bodies[2]["item"]["run_id"] == "run-admin-api-safe"
    assert bodies[3]["items"][0]["call_id"] == "call-admin-api-safe"
    assert all(
        event["run_id"] == "run-admin-api-safe" for event in bodies[4]["items"]
    )
    assert bodies[6]["items"][0]["observation_id"] == (
        candidate.observation.observation_id
    )
    assert bodies[7]["items"][0]["candidate_id"] == candidate.candidate_id
    assert bodies[8]["item"]["candidate_id"] == candidate.candidate_id
    assert bodies[9]["items"] == []
    assert bodies[10]["status"] == "ready"
    assert bodies[10]["report_only"] is True
    assert bodies[11]["status"] == "not_ready"
    assert bodies[11]["activation_authorized"] is False
    assert bodies[12]["status"] == "not_ready"

    forbidden_keys = {
        "actor_ref",
        "args_digest",
        "evidence",
        "metadata",
        "operator_note",
        "origin_group_ref",
        "owner_id",
        "payload",
        "principal_id",
        "quote",
        "session_id",
        "subject_ref",
        "target_ref",
        "trigger_ref",
    }
    assert forbidden_keys.isdisjoint(_recursive_keys(bodies))
    serialized = _serialized(bodies)
    canaries = (
        "TRIGGER_CANARY_ADMIN_API",
        "PRINCIPAL_CANARY_ADMIN_API",
        "SESSION_CANARY_ADMIN_API",
        "GROUP_CANARY_ADMIN_API",
        "RUN_METADATA_CANARY_ADMIN_API",
        "TARGET_CANARY_ADMIN_API",
        "ARGS_CANARY_ADMIN_API",
        "TOOL_METADATA_CANARY_ADMIN_API",
        "EVIDENCE_QUOTE_CANARY_ADMIN_API",
        "EVIDENCE_REF_CANARY_ADMIN_API",
        "MEMORY_PAYLOAD_CANARY_ADMIN_API",
        "MEMORY_PRODUCER_CANARY_ADMIN_API",
        "MODEL_ARGS_CANARY_ADMIN_API",
    )
    assert all(canary not in serialized for canary in canaries)


async def test_post_routes_forward_only_the_frozen_decision_fields() -> None:
    actions = _ActionsFake()
    app = _direct_app(
        runtime_query=None,
        memory_query=None,
        readiness=None,
        actions=actions,
    )
    approval = {
        "expected_token": "token-approval-1",
        "approval_ref": "approval:offline:7",
    }
    reconciliation = {
        "expected_token": "token-reconciliation-1",
        "decision": "confirmed_succeeded",
        "evidence_ref": "attestation:offline:7",
        "operator_note": "provider dashboard confirms success",
        "external_id": "provider:external:7",
    }
    memory = {
        "expected_token": "token-memory-1",
        "decision": "approve",
        "reason_code": "operator_verified",
        "operator_note": "evidence and scope verified",
        "occurred_at": "2026-07-22T08:30:00Z",
    }
    async with _client(app) as client:
        responses = (
            await client.post(
                f"{API}/agent-runtime/tool-calls/call-7/approval",
                json=approval,
            ),
            await client.post(
                f"{API}/agent-runtime/tool-calls/call-7/reconciliation",
                json=reconciliation,
            ),
            await client.post(
                f"{API}/memory-governance/candidates/mcand_7/decision",
                json=memory,
            ),
        )

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert [response.json()["action"] for response in responses] == [
        "approve_tool_call",
        "reconcile_tool_call",
        "decide_memory_candidate",
    ]
    assert actions.calls[0] == (
        "approve_tool_call",
        "call-7",
        approval,
    )
    assert actions.calls[1] == (
        "reconcile_tool_call",
        "call-7",
        reconciliation,
    )
    memory_call = actions.calls[2]
    assert memory_call[0:2] == ("decide_memory_candidate", "mcand_7")
    assert memory_call[2]["expected_token"] == "token-memory-1"
    assert memory_call[2]["decision"] == "approve"
    assert memory_call[2]["reason_code"] == "operator_verified"
    assert memory_call[2]["operator_note"] == "evidence and scope verified"
    assert "conflict_ids" not in memory_call[2]
    assert memory_call[2]["occurred_at"] == datetime(
        2026,
        7,
        22,
        8,
        30,
        tzinfo=UTC,
    )


async def test_post_bodies_forbid_all_browser_supplied_trust_inputs() -> None:
    actions = _ActionsFake()
    app = _direct_app(
        runtime_query=None,
        memory_query=None,
        readiness=None,
        actions=actions,
    )
    cases = (
        (
            "/agent-runtime/tool-calls/call-7/approval",
            {
                "expected_token": "token-approval-1",
                "approval_ref": "approval:offline:7",
            },
        ),
        (
            "/agent-runtime/tool-calls/call-7/reconciliation",
            {
                "expected_token": "token-reconciliation-1",
                "decision": "confirmed_not_applied",
                "evidence_ref": "attestation:offline:7",
                "operator_note": "confirmed absent",
            },
        ),
        (
            "/memory-governance/candidates/mcand_7/decision",
            {
                "expected_token": "token-memory-1",
                "decision": "reject",
                "reason_code": "insufficient_evidence",
                "occurred_at": "2026-07-22T08:30:00Z",
            },
        ),
    )
    forbidden = {
        "principal": {"kind": "operator", "id": "browser"},
        "operator": "browser-supplied",
        "adapter": "browser-selected-adapter",
        "target": "browser-selected-target",
        "args": {"execute": True},
        "conflict_ids": ["mconf_BROWSER_SUPPLIED_CANARY"],
    }
    async with _client(app) as client:
        for path, valid in cases:
            for field, value in forbidden.items():
                response = await client.post(
                    f"{API}{path}",
                    json={**valid, field: value},
                )
                assert response.status_code == 422, (path, field)
    assert actions.calls == []


async def test_post_routes_are_generic_503_when_actions_are_not_injected() -> None:
    app = _direct_app(
        runtime_query=None,
        memory_query=None,
        readiness=None,
        actions=None,
    )
    cases = (
        (
            "/agent-runtime/tool-calls/call-7/approval",
            {
                "expected_token": "token-approval-1",
                "approval_ref": "approval:offline:7",
            },
        ),
        (
            "/agent-runtime/tool-calls/call-7/reconciliation",
            {
                "expected_token": "token-reconciliation-1",
                "decision": "confirmed_not_applied",
                "evidence_ref": "attestation:offline:7",
                "operator_note": "confirmed absent",
            },
        ),
        (
            "/memory-governance/candidates/mcand_7/decision",
            {
                "expected_token": "token-memory-1",
                "decision": "reject",
                "reason_code": "insufficient_evidence",
                "occurred_at": "2026-07-22T08:30:00Z",
            },
        ),
    )
    async with _client(app) as client:
        for path, body in cases:
            response = await client.post(f"{API}{path}", json=body)
            assert response.status_code == 503, path
            detail = response.json().get("detail")
            assert isinstance(detail, str) and detail
            assert "traceback" not in detail.casefold()


async def test_invalid_limits_filters_cursors_and_ids_are_sanitized() -> None:
    actions = _ActionsFake()
    app = _direct_app(
        runtime_query=RuntimeAdminQueryV1(None),
        memory_query=MemoryGovernanceAdminQueryV1(None),
        readiness=None,
        actions=actions,
    )
    invalid_gets = (
        "/agent-runtime/runs?limit=0",
        "/agent-runtime/runs?limit=101",
        "/agent-runtime/runs?status=not-a-status",
        "/agent-runtime/runs?cursor=CURSOR_EXCEPTION_CANARY_ADMIN_API",
        "/agent-runtime/runs/%20",
        "/memory-governance/observations?limit=0",
        "/memory-governance/candidates?projection_kind=not-a-projection",
        "/memory-governance/candidates/%20",
    )
    async with _client(app) as client:
        for path in invalid_gets:
            response = await client.get(f"{API}{path}")
            assert response.status_code in {400, 422}, path
            assert "CURSOR_EXCEPTION_CANARY_ADMIN_API" not in response.text

        invalid_action_id = await client.post(
            f"{API}/agent-runtime/tool-calls/%20/approval",
            json={
                "expected_token": "token-approval-1",
                "approval_ref": "approval:offline:7",
            },
        )
    assert invalid_action_id.status_code in {400, 422}
    assert actions.calls == []


async def test_service_key_and_value_errors_map_to_sanitized_http_statuses() -> None:
    missing_canary = "MISSING_EXCEPTION_CANARY_ADMIN_API"
    conflict_canary = "CONFLICT_EXCEPTION_CANARY_ADMIN_API"
    query_canary = "QUERY_EXCEPTION_CANARY_ADMIN_API"

    key_app = _direct_app(
        runtime_query=_FailingRuntimeQuery(KeyError(missing_canary)),
        memory_query=None,
        readiness=None,
        actions=_ActionsFake(KeyError(missing_canary)),
    )
    async with _client(key_app) as client:
        get_missing = await client.get(f"{API}/agent-runtime/runs/run-missing")
        post_missing = await client.post(
            f"{API}/agent-runtime/tool-calls/call-missing/approval",
            json={
                "expected_token": "token-approval-1",
                "approval_ref": "approval:offline:7",
            },
        )
    assert get_missing.status_code == 404
    assert post_missing.status_code == 404
    assert missing_canary not in get_missing.text
    assert missing_canary not in post_missing.text

    value_app = _direct_app(
        runtime_query=_FailingRuntimeQuery(ValueError(query_canary)),
        memory_query=None,
        readiness=None,
        actions=_ActionsFake(ValueError(conflict_canary)),
    )
    async with _client(value_app) as client:
        bad_query = await client.get(f"{API}/agent-runtime/runs")
        conflict = await client.post(
            f"{API}/agent-runtime/tool-calls/call-7/approval",
            json={
                "expected_token": "stale-token",
                "approval_ref": "approval:offline:7",
            },
        )
    assert bad_query.status_code == 400
    assert conflict.status_code == 409
    assert query_canary not in bad_query.text
    assert conflict_canary not in conflict.text


async def test_query_and_action_cancellation_propagate() -> None:
    query_app = _direct_app(
        runtime_query=_FailingRuntimeQuery(asyncio.CancelledError()),
        memory_query=None,
        readiness=None,
        actions=None,
    )
    async with _client(query_app) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.get(f"{API}/agent-runtime/summary")

    action_app = _direct_app(
        runtime_query=None,
        memory_query=None,
        readiness=None,
        actions=_ActionsFake(asyncio.CancelledError()),
    )
    async with _client(action_app) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.post(
                f"{API}/agent-runtime/tool-calls/call-7/approval",
                json={
                    "expected_token": "token-approval-1",
                    "approval_ref": "approval:offline:7",
                },
            )
