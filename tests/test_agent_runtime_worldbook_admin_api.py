"""RED HTTP contracts for Worldbook governance Admin API wiring."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from admin.routes.api import create_api_router
from services.social_narrative import SocialExperience
from services.worldbook.governance_contracts import WorldbookOperatorDecisionV1
from services.worldbook.governance_query import WorldbookGovernanceAdminQueryV1
from services.worldbook.governance_store import WorldbookGovernanceStore
from services.worldbook.governed_adapters import build_social_event_proposal

API = "/api/admin"
T0 = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)


def _route_module() -> ModuleType:
    module_name = "admin.routes.api.agent_runtime"
    spec = importlib.util.find_spec(module_name)
    assert spec is not None, f"{module_name} is required"
    return importlib.import_module(module_name)


def _factory() -> Any:
    factory = getattr(_route_module(), "create_agent_runtime_router", None)
    assert callable(factory), "create_agent_runtime_router() is required"
    return factory


def _create_agent_runtime_router(**dependencies: Any) -> APIRouter:
    router = _factory()(**dependencies)
    assert isinstance(router, APIRouter)
    return router


def _direct_app(**dependencies: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(_create_agent_runtime_router(**dependencies), prefix=API)
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
        return set(value) | {key for item in value.values() for key in _recursive_keys(item)}
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return {key for item in value for key in _recursive_keys(item)}
    return set()


class _WorldbookQuerySpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def _record(
        self,
        name: str,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append((name, args, kwargs))
        return {"source": "explicit-worldbook-query", "method": name}

    async def summary(self) -> dict[str, Any]:
        return await self._record("summary")

    async def list_proposals(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        world_id: str | None = None,
        source_kind: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        return await self._record(
            "list_proposals",
            limit=limit,
            cursor=cursor,
            world_id=world_id,
            source_kind=source_kind,
            status=status,
        )

    async def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        return await self._record("get_proposal", proposal_id)


class _ThreeSourceReadiness:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def dark_readiness(self) -> dict[str, Any]:
        self.calls.append("dark_readiness")
        return {
            "report_only": True,
            "status": "ready",
            "sources": {
                "runtime": {"available": True},
                "memory": {"available": True},
                "worldbook": {"available": True},
            },
        }

    async def activation_readiness(self) -> dict[str, Any]:
        self.calls.append("activation_readiness")
        return {"report_only": True, "activation_authorized": False}

    async def rollback_readiness(self) -> dict[str, Any]:
        self.calls.append("rollback_readiness")
        return {"report_only": True, "status": "not_ready"}


def _social_proposal() -> Any:
    record = SocialExperience(
        experience_id="social.exp.ADMIN_API_EXPERIENCE_CANARY",
        group_id="881337881337",
        user_id="991337991337",
        entity_kind="factual",
        evidence_message_id="ADMIN_API_MESSAGE_CANARY",
        evidence_time="2026-07-22T15:30:00+08:00",
        evidence_source="ADMIN_API_EVIDENCE_SOURCE_CANARY",
        user_text="Alice SecretName said RAW_SOCIAL_TEXT_ADMIN_API_CANARY",
        bot_reply="RAW_SOCIAL_REPLY_ADMIN_API_CANARY",
        status="active",
        created_at="2026-07-22T15:31:00+08:00",
    )
    return build_social_event_proposal(
        record=record,
        world_id="world.admin.api",
        target_arc_id="arc.social.admin.api",
        current_group_id="881337881337",
        current_user_id="991337991337",
        proposed_at=T0,
    )


def _reject(proposal: Any) -> WorldbookOperatorDecisionV1:
    return WorldbookOperatorDecisionV1.create(
        proposal_id=proposal.proposal_id,
        decision="reject",
        reason_code="ADMIN_API_REASON_CANARY",
        operator_ref="operator:ADMIN_API_OPERATOR_CANARY",
        decided_at=T0 + timedelta(minutes=1),
    )


def test_router_factory_exposes_explicit_optional_worldbook_dependency() -> None:
    parameter = inspect.signature(_factory()).parameters.get("worldbook_query")

    assert parameter is not None, "create_agent_runtime_router() must accept explicit worldbook_query"
    assert parameter.default is None


async def test_missing_worldbook_source_is_generic_unavailable_without_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    before = tuple(tmp_path.iterdir())
    app = _direct_app(
        runtime_query=None,
        memory_query=None,
        worldbook_query=None,
        readiness=None,
        actions=None,
    )
    paths = (
        "/worldbook-governance/summary",
        "/worldbook-governance/proposals",
        "/worldbook-governance/proposals/wprop_missing",
    )
    async with _client(app) as client:
        responses = [await client.get(f"{API}{path}") for path in paths]

    assert tuple(tmp_path.iterdir()) == before
    for path, response in zip(paths, responses, strict=True):
        assert response.status_code == 200, path
        body = response.json()
        assert body["available"] is False
        assert body["reason"] == "source_unavailable"
        assert body["mode"] == "offline_dark"
        assert "exception" not in _serialized(body).lower()
        assert str(tmp_path) not in _serialized(body)
    assert responses[1].json()["items"] == []
    assert responses[1].json()["next_cursor"] is None
    assert responses[2].json()["item"] is None


async def test_get_routes_forward_exact_bounded_filters_to_explicit_query() -> None:
    worldbook = _WorldbookQuerySpy()
    app = _direct_app(
        runtime_query=None,
        memory_query=None,
        worldbook_query=worldbook,
        readiness=None,
        actions=None,
    )
    requests = (
        ("/worldbook-governance/summary", None),
        (
            "/worldbook-governance/proposals",
            {
                "limit": "17",
                "cursor": "worldbook-cursor",
                "world_id": "world.main",
                "source_kind": "social_evidence",
                "status": "rejected",
            },
        ),
        ("/worldbook-governance/proposals/wprop_7", None),
    )
    async with _client(app) as client:
        for path, params in requests:
            response = await client.get(f"{API}{path}", params=params)
            assert response.status_code == 200, path
            assert response.json()["source"] == "explicit-worldbook-query"

    assert worldbook.calls == [
        ("summary", (), {}),
        (
            "list_proposals",
            (),
            {
                "limit": 17,
                "cursor": "worldbook-cursor",
                "world_id": "world.main",
                "source_kind": "social_evidence",
                "status": "rejected",
            },
        ),
        ("get_proposal", ("wprop_7",), {}),
    ]


async def test_aggregate_router_forwards_only_explicit_worldbook_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    derived_path = tmp_path / "must-not-open-derived-worldbook.db"
    derived = _WorldbookQuerySpy()
    ctx = SimpleNamespace(
        worldbook_governance_query=derived,
        worldbook_query=derived,
        worldbook_governance_store=WorldbookGovernanceStore(derived_path),
        storage_dir=tmp_path,
    )
    explicit = _WorldbookQuerySpy()
    explicit_app = FastAPI()
    explicit_app.include_router(
        _aggregate_router(
            ctx=ctx,
            runtime_query=None,
            memory_query=None,
            worldbook_query=explicit,
            readiness=None,
            actions=None,
            repo_root=tmp_path,
        )
    )
    async with _client(explicit_app) as client:
        response = await client.get(f"{API}/worldbook-governance/summary")
    assert response.status_code == 200
    assert response.json() == {
        "source": "explicit-worldbook-query",
        "method": "summary",
    }
    assert explicit.calls == [("summary", (), {})]
    assert derived.calls == []

    unwired_app = FastAPI()
    unwired_app.include_router(_aggregate_router(ctx=ctx, repo_root=tmp_path))
    async with _client(unwired_app) as client:
        unwired = await client.get(f"{API}/worldbook-governance/summary")
    assert unwired.status_code == 200
    assert unwired.json()["reason"] == "source_unavailable"
    assert derived.calls == []
    assert not derived_path.exists()


async def test_query_parameters_are_closed_and_invalid_values_are_sanitized(
    tmp_path: Path,
) -> None:
    store = WorldbookGovernanceStore(tmp_path / "validation.db")
    await store.init()
    try:
        app = _direct_app(
            runtime_query=None,
            memory_query=None,
            worldbook_query=WorldbookGovernanceAdminQueryV1(store),
            readiness=None,
            actions=None,
        )
        invalid_paths = (
            "/worldbook-governance/summary?unexpected=value",
            "/worldbook-governance/proposals?limit=0",
            "/worldbook-governance/proposals?limit=101",
            "/worldbook-governance/proposals?limit=true",
            "/worldbook-governance/proposals?source_kind=web_guess",
            "/worldbook-governance/proposals?status=executed",
            "/worldbook-governance/proposals?world_id=%20",
            "/worldbook-governance/proposals?cursor=CURSOR_EXCEPTION_ADMIN_API_CANARY",
            "/worldbook-governance/proposals?unknown_filter=secret",
            "/worldbook-governance/proposals/wprop_7?unexpected=value",
            "/worldbook-governance/proposals/%20",
        )
        async with _client(app) as client:
            responses = [await client.get(f"{API}{path}") for path in invalid_paths]
    finally:
        await store.close()

    for path, response in zip(invalid_paths, responses, strict=True):
        assert response.status_code in {400, 422}, path
        assert "CURSOR_EXCEPTION_ADMIN_API_CANARY" not in response.text
        assert str(tmp_path) not in response.text


async def test_real_open_store_dtos_remain_deeply_redacted_over_http(
    tmp_path: Path,
) -> None:
    path = tmp_path / "DO_NOT_LEAK_WORLDBOOK_DB_PATH_ADMIN_API.db"
    proposal = _social_proposal()
    store = WorldbookGovernanceStore(path)
    await store.init()
    try:
        await store.append_proposal(proposal)
        await store.append_operator_decision(_reject(proposal))
        app = _direct_app(
            runtime_query=None,
            memory_query=None,
            worldbook_query=WorldbookGovernanceAdminQueryV1(store),
            readiness=None,
            actions=None,
        )
        paths = (
            "/worldbook-governance/summary",
            (
                "/worldbook-governance/proposals?limit=1"
                "&world_id=world.admin.api&source_kind=social_evidence&status=rejected"
            ),
            f"/worldbook-governance/proposals/{proposal.proposal_id}",
        )
        async with _client(app) as client:
            responses = [await client.get(f"{API}{item}") for item in paths]
    finally:
        await store.close()

    for route, response in zip(paths, responses, strict=True):
        assert response.status_code == 200, route
    bodies = [response.json() for response in responses]
    listed = bodies[1]["items"][0]
    detail = bodies[2]["item"]
    assert listed == detail
    assert listed["proposal_id"] == proposal.proposal_id
    assert listed["event_id"] == proposal.event.event_id
    assert listed["world_id"] == "world.admin.api"
    assert listed["source_kind"] == "social_evidence"
    assert listed["status"] == "rejected"
    assert listed["decision_present"] is True
    assert listed["receipt_present"] is False

    forbidden_keys = {
        "arc_id",
        "consequences",
        "db_path",
        "event",
        "event_payload",
        "evidence",
        "evidence_message_id",
        "evidence_ref",
        "group_id",
        "name",
        "operator_ref",
        "quote",
        "reason_code",
        "source_binding",
        "source_ref",
        "summary",
        "user_id",
        "variable_deltas",
    }
    assert forbidden_keys.isdisjoint(_recursive_keys(bodies))
    forbidden_values = (
        str(path),
        "881337881337",
        "991337991337",
        "ADMIN_API_MESSAGE_CANARY",
        "ADMIN_API_EVIDENCE_SOURCE_CANARY",
        "ADMIN_API_EXPERIENCE_CANARY",
        "ADMIN_API_OPERATOR_CANARY",
        "ADMIN_API_REASON_CANARY",
        "RAW_SOCIAL_TEXT_ADMIN_API_CANARY",
        "RAW_SOCIAL_REPLY_ADMIN_API_CANARY",
        "Alice SecretName",
        proposal.event.summary,
    )
    serialized = _serialized(bodies)
    assert all(value not in serialized for value in forbidden_values)


async def test_worldbook_routes_are_read_only_and_expose_no_activation_surface() -> None:
    app = _direct_app(
        runtime_query=None,
        memory_query=None,
        worldbook_query=_WorldbookQuerySpy(),
        readiness=None,
        actions=None,
    )
    expected = {
        f"{API}/worldbook-governance/summary",
        f"{API}/worldbook-governance/proposals",
        f"{API}/worldbook-governance/proposals/{{proposal_id}}",
    }
    discovered = {
        str(getattr(route, "path", "")): set(getattr(route, "methods", set()) or set())
        for route in app.routes
        if "/worldbook-governance" in str(getattr(route, "path", ""))
    }
    assert set(discovered) == expected
    assert all(methods == {"GET"} for methods in discovered.values())

    async with _client(app) as client:
        post_list = await client.post(f"{API}/worldbook-governance/proposals")
        post_detail = await client.post(
            f"{API}/worldbook-governance/proposals/wprop_7",
            json={"decision": "approve"},
        )
        activate = await client.post(f"{API}/worldbook-governance/activate")
    assert post_list.status_code == 405
    assert post_detail.status_code == 405
    assert activate.status_code == 404


async def test_dark_readiness_response_keeps_all_three_report_only_sources() -> None:
    readiness = _ThreeSourceReadiness()
    app = _direct_app(
        runtime_query=None,
        memory_query=None,
        worldbook_query=None,
        readiness=readiness,
        actions=None,
    )
    async with _client(app) as client:
        response = await client.get(f"{API}/agent-runtime/readiness/dark")

    assert response.status_code == 200
    body = response.json()
    assert body["report_only"] is True
    assert body["status"] == "ready"
    assert set(body["sources"]) == {"runtime", "memory", "worldbook"}
    assert all(source["available"] is True for source in body["sources"].values())
    assert readiness.calls == ["dark_readiness"]
