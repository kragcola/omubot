"""Dark/offline Agent Runtime v2 Admin API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from services.agent_runtime.admin_operator import AdminOperatorAuthenticationError
from services.agent_runtime.admin_query import RuntimeAdminQueryV1
from services.agent_runtime.rollout_readiness import RolloutReadinessV1
from services.memory.governance_query import MemoryGovernanceAdminQueryV1
from services.worldbook.governance_query import WorldbookGovernanceAdminQueryV1


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ApprovalBody(_StrictBody):
    expected_token: str = Field(min_length=1, max_length=512)
    approval_ref: str = Field(min_length=1, max_length=240)


class _ReconciliationBody(_StrictBody):
    expected_token: str = Field(min_length=1, max_length=512)
    decision: Literal["confirmed_succeeded", "confirmed_not_applied"]
    evidence_ref: str = Field(min_length=1, max_length=240)
    operator_note: str = Field(min_length=1, max_length=500)
    external_id: str | None = Field(default=None, max_length=240)


class _MemoryDecisionBody(_StrictBody):
    expected_token: str = Field(min_length=1, max_length=512)
    decision: Literal["approve", "reject"]
    reason_code: str = Field(min_length=1, max_length=120)
    operator_note: str | None = Field(default=None, max_length=500)
    occurred_at: datetime


class _WorldbookDecisionBody(_StrictBody):
    expected_token: str = Field(min_length=1, max_length=512)
    decision: Literal["approve", "reject"]
    reason_code: str = Field(min_length=1, max_length=120)


_OPERATOR_ID_HEADER = "x-agent-runtime-operator-id"
_AUTHORIZATION_HEADER = "authorization"


def _clean_id(value: str, field: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 128:
        raise HTTPException(status_code=400, detail=f"invalid {field}")
    return clean


async def _query_result(awaitable: Any) -> Any:
    try:
        return await awaitable
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="record not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid query") from exc


async def _action_result(awaitable: Any) -> Any:
    try:
        return await awaitable
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="record not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="decision conflict") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="operator scope unavailable") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="operator source unavailable") from exc


def _forbid_context_query(request: Request) -> None:
    if request.query_params:
        raise HTTPException(
            status_code=422,
            detail="decision context does not accept browser trust inputs",
        )


def _forbid_unknown_query(request: Request, *, allowed: frozenset[str]) -> None:
    if set(request.query_params) - allowed:
        raise HTTPException(status_code=422, detail="unexpected query parameter")


def _clean_filter(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    if not clean or len(clean) > 120:
        raise HTTPException(status_code=400, detail=f"invalid {field}")
    return clean


def _operator_authentication_required() -> HTTPException:
    return HTTPException(status_code=401, detail="operator authentication required")


def _single_header(request: Request, header: str) -> str:
    values = request.headers.getlist(header)
    if len(values) != 1:
        raise _operator_authentication_required()
    value = str(values[0])
    if not value or value != value.strip() or not value.isascii():
        raise _operator_authentication_required()
    return value


def _operator_credentials(request: Request) -> tuple[str, str]:
    operator_id = _single_header(request, _OPERATOR_ID_HEADER)
    authorization = _single_header(request, _AUTHORIZATION_HEADER)
    scheme, separator, credential = authorization.partition(" ")
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not credential
        or credential != credential.strip()
        or " " in credential
    ):
        raise _operator_authentication_required()
    return operator_id, credential


async def _actions_for_request(
    request: Request,
    *,
    actions: Any,
    operator_action_factory: Any,
) -> Any:
    if operator_action_factory is None:
        if actions is None:
            raise HTTPException(status_code=503, detail="operator actions unavailable")
        return actions
    operator_id, credential = _operator_credentials(request)
    try:
        return await operator_action_factory.authenticate(
            operator_id=operator_id,
            credential=credential,
        )
    except AdminOperatorAuthenticationError as exc:
        raise _operator_authentication_required() from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="operator source unavailable",
        ) from exc


def create_agent_runtime_router(
    *,
    runtime_query: Any = None,
    memory_query: Any = None,
    worldbook_query: Any = None,
    readiness: Any = None,
    actions: Any = None,
    operator_action_factory: Any = None,
) -> APIRouter:
    """Create P5 routes from explicitly injected, already-open services only."""

    runtime = runtime_query or RuntimeAdminQueryV1(None)
    memory = memory_query or MemoryGovernanceAdminQueryV1(None)
    worldbook = worldbook_query or WorldbookGovernanceAdminQueryV1(None)
    rollout = readiness or RolloutReadinessV1(None, None)
    router = APIRouter(tags=["agent-runtime"])

    @router.get("/agent-runtime/summary")
    async def runtime_summary(request: Request) -> Any:
        _forbid_unknown_query(request, allowed=frozenset())
        return await _query_result(runtime.summary())

    @router.get("/agent-runtime/runs")
    async def runtime_runs(
        request: Request,
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = None,
        status: str | None = None,
    ) -> Any:
        _forbid_unknown_query(
            request,
            allowed=frozenset({"limit", "cursor", "status"}),
        )
        return await _query_result(
            runtime.list_runs(limit=limit, cursor=cursor, status=status)
        )

    @router.get("/agent-runtime/runs/{run_id}")
    async def runtime_run(run_id: str, request: Request) -> Any:
        _forbid_unknown_query(request, allowed=frozenset())
        return await _query_result(runtime.get_run(_clean_id(run_id, "run_id")))

    @router.get("/agent-runtime/tool-calls")
    async def runtime_tool_calls(
        request: Request,
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = None,
        status: str | None = None,
        run_id: str | None = None,
    ) -> Any:
        _forbid_unknown_query(
            request,
            allowed=frozenset({"limit", "cursor", "status", "run_id"}),
        )
        return await _query_result(
            runtime.list_tool_calls(
                limit=limit,
                cursor=cursor,
                status=status,
                run_id=run_id,
            )
        )

    @router.get("/agent-runtime/runs/{run_id}/events")
    async def runtime_events(
        run_id: str,
        request: Request,
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = None,
    ) -> Any:
        _forbid_unknown_query(
            request,
            allowed=frozenset({"limit", "cursor"}),
        )
        return await _query_result(
            runtime.list_events(
                _clean_id(run_id, "run_id"),
                limit=limit,
                cursor=cursor,
            )
        )

    @router.get("/agent-runtime/readiness/{kind}")
    async def runtime_readiness(
        kind: Literal["dark", "activation", "rollback"],
        request: Request,
    ) -> Any:
        _forbid_unknown_query(request, allowed=frozenset())
        methods = {
            "dark": rollout.dark_readiness,
            "activation": rollout.activation_readiness,
            "rollback": rollout.rollback_readiness,
        }
        return await _query_result(methods[kind]())

    @router.get("/memory-governance/summary")
    async def memory_summary(request: Request) -> Any:
        _forbid_unknown_query(request, allowed=frozenset())
        return await _query_result(memory.summary())

    @router.get("/memory-governance/observations")
    async def memory_observations(
        request: Request,
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = None,
    ) -> Any:
        _forbid_unknown_query(
            request,
            allowed=frozenset({"limit", "cursor"}),
        )
        return await _query_result(
            memory.list_observations(limit=limit, cursor=cursor)
        )

    @router.get("/memory-governance/candidates")
    async def memory_candidates(
        request: Request,
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = None,
        projection_kind: str | None = None,
        operation: str | None = None,
    ) -> Any:
        _forbid_unknown_query(
            request,
            allowed=frozenset(
                {"limit", "cursor", "projection_kind", "operation"}
            ),
        )
        return await _query_result(
            memory.list_candidates(
                limit=limit,
                cursor=cursor,
                projection_kind=projection_kind,
                operation=operation,
            )
        )

    @router.get("/memory-governance/candidates/{candidate_id}")
    async def memory_candidate(candidate_id: str, request: Request) -> Any:
        _forbid_unknown_query(request, allowed=frozenset())
        return await _query_result(
            memory.get_candidate(_clean_id(candidate_id, "candidate_id"))
        )

    @router.get("/memory-governance/conflicts")
    async def memory_conflicts(
        request: Request,
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = None,
    ) -> Any:
        _forbid_unknown_query(
            request,
            allowed=frozenset({"limit", "cursor"}),
        )
        return await _query_result(memory.list_conflicts(limit=limit, cursor=cursor))

    @router.get("/worldbook-governance/summary")
    async def worldbook_summary(request: Request) -> Any:
        _forbid_unknown_query(request, allowed=frozenset())
        return await _query_result(worldbook.summary())

    @router.get("/worldbook-governance/proposals")
    async def worldbook_proposals(
        request: Request,
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = None,
        world_id: str | None = None,
        source_kind: Literal["schedule", "social_evidence"] | None = None,
        status: Literal["pending", "approved", "rejected", "committed"] | None = None,
    ) -> Any:
        _forbid_unknown_query(
            request,
            allowed=frozenset(
                {"limit", "cursor", "world_id", "source_kind", "status"}
            ),
        )
        return await _query_result(
            worldbook.list_proposals(
                limit=limit,
                cursor=cursor,
                world_id=_clean_filter(world_id, "world_id"),
                source_kind=source_kind,
                status=status,
            )
        )

    @router.get("/worldbook-governance/proposals/{proposal_id}")
    async def worldbook_proposal(proposal_id: str, request: Request) -> Any:
        _forbid_unknown_query(request, allowed=frozenset())
        return await _query_result(
            worldbook.get_proposal(_clean_id(proposal_id, "proposal_id"))
        )

    @router.get("/agent-runtime/tool-calls/{call_id}/approval/context")
    async def tool_approval_context(call_id: str, request: Request) -> Any:
        _forbid_context_query(request)
        request_actions = await _actions_for_request(
            request,
            actions=actions,
            operator_action_factory=operator_action_factory,
        )
        return await _action_result(
            request_actions.tool_approval_context(_clean_id(call_id, "call_id"))
        )

    @router.get("/agent-runtime/tool-calls/{call_id}/reconciliation/context")
    async def reconciliation_context(call_id: str, request: Request) -> Any:
        _forbid_context_query(request)
        request_actions = await _actions_for_request(
            request,
            actions=actions,
            operator_action_factory=operator_action_factory,
        )
        return await _action_result(
            request_actions.reconciliation_context(_clean_id(call_id, "call_id"))
        )

    @router.get("/memory-governance/candidates/{candidate_id}/decision/context")
    async def memory_candidate_context(candidate_id: str, request: Request) -> Any:
        _forbid_context_query(request)
        request_actions = await _actions_for_request(
            request,
            actions=actions,
            operator_action_factory=operator_action_factory,
        )
        return await _action_result(
            request_actions.memory_candidate_context(
                _clean_id(candidate_id, "candidate_id")
            )
        )

    @router.get("/worldbook-governance/proposals/{proposal_id}/decision/context")
    async def worldbook_proposal_context(proposal_id: str, request: Request) -> Any:
        _forbid_context_query(request)
        request_actions = await _actions_for_request(
            request,
            actions=actions,
            operator_action_factory=operator_action_factory,
        )
        return await _action_result(
            request_actions.worldbook_proposal_context(
                _clean_id(proposal_id, "proposal_id")
            )
        )

    @router.post("/agent-runtime/tool-calls/{call_id}/approval")
    async def approve_tool_call(
        call_id: str,
        body: _ApprovalBody,
        request: Request,
    ) -> Any:
        request_actions = await _actions_for_request(
            request,
            actions=actions,
            operator_action_factory=operator_action_factory,
        )
        return await _action_result(
            request_actions.approve_tool_call(
                _clean_id(call_id, "call_id"),
                expected_token=body.expected_token,
                approval_ref=body.approval_ref,
            )
        )

    @router.post("/agent-runtime/tool-calls/{call_id}/reconciliation")
    async def reconcile_tool_call(
        call_id: str,
        body: _ReconciliationBody,
        request: Request,
    ) -> Any:
        request_actions = await _actions_for_request(
            request,
            actions=actions,
            operator_action_factory=operator_action_factory,
        )
        return await _action_result(
            request_actions.reconcile_tool_call(
                _clean_id(call_id, "call_id"),
                expected_token=body.expected_token,
                decision=body.decision,
                evidence_ref=body.evidence_ref,
                operator_note=body.operator_note,
                external_id=body.external_id,
            )
        )

    @router.post("/memory-governance/candidates/{candidate_id}/decision")
    async def decide_memory_candidate(
        candidate_id: str,
        body: _MemoryDecisionBody,
        request: Request,
    ) -> Any:
        request_actions = await _actions_for_request(
            request,
            actions=actions,
            operator_action_factory=operator_action_factory,
        )
        return await _action_result(
            request_actions.decide_memory_candidate(
                _clean_id(candidate_id, "candidate_id"),
                expected_token=body.expected_token,
                decision=body.decision,
                reason_code=body.reason_code,
                operator_note=body.operator_note,
                occurred_at=body.occurred_at,
            )
        )

    @router.post("/worldbook-governance/proposals/{proposal_id}/decision")
    async def decide_worldbook_proposal(
        proposal_id: str,
        body: _WorldbookDecisionBody,
        request: Request,
    ) -> Any:
        request_actions = await _actions_for_request(
            request,
            actions=actions,
            operator_action_factory=operator_action_factory,
        )
        return await _action_result(
            request_actions.decide_worldbook_proposal(
                _clean_id(proposal_id, "proposal_id"),
                expected_token=body.expected_token,
                decision=body.decision,
                reason_code=body.reason_code,
            )
        )

    return router


__all__ = ["create_agent_runtime_router"]
