from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.auth import AdminAuthMiddleware
from admin.routes.api import create_api_router

_ZERO_METRICS = {
    "received": 0,
    "enqueued": 0,
    "dropped_queue_full": 0,
    "write_error": 0,
    "pending": 0,
    "persisted": 0,
    "duplicate": 0,
    "error": 0,
}


class _Capture:
    def __init__(self, **metrics: int) -> None:
        self._metrics = {**_ZERO_METRICS, **metrics}

    def metrics_snapshot(self) -> Any:
        return SimpleNamespace(**self._metrics)


class _FailingCapture:
    def metrics_snapshot(self) -> Any:
        raise RuntimeError("sensitive storage details")


def _client(*, capture: Any = None, config: Any = None) -> TestClient:
    app = FastAPI()
    ctx = SimpleNamespace(research_event_capture=capture, config=config)
    app.include_router(create_api_router(ctx=ctx))
    return TestClient(app)


def test_research_event_status_reports_healthy_capture_metrics() -> None:
    metrics = {
        "received": 13,
        "enqueued": 12,
        "dropped_queue_full": 0,
        "write_error": 0,
        "pending": 1,
        "persisted": 9,
        "duplicate": 2,
        "error": 0,
    }

    response = _client(capture=_Capture(**metrics)).get("/api/admin/research-events/status")

    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "status": "healthy",
        **metrics,
    }


def test_research_event_status_reads_capture_from_context_at_request_time() -> None:
    ctx = SimpleNamespace(research_event_capture=None, config=None)
    app = FastAPI()
    app.include_router(create_api_router(ctx=ctx))
    client = TestClient(app)

    ctx.research_event_capture = _Capture(received=7, enqueued=7, persisted=6)
    response = client.get("/api/admin/research-events/status")

    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "status": "healthy",
        **_ZERO_METRICS,
        "received": 7,
        "enqueued": 7,
        "persisted": 6,
    }


def test_research_event_status_requires_admin_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    app = FastAPI()
    app.add_middleware(AdminAuthMiddleware)
    ctx = SimpleNamespace(research_event_capture=_Capture(), config=None)
    app.include_router(create_api_router(ctx=ctx))
    client = TestClient(app)

    unauthenticated = client.get("/api/admin/research-events/status")
    assert unauthenticated.status_code == 401

    login = client.post("/api/admin/login", json={"token": "secret"})
    assert login.status_code == 200
    authenticated = client.get("/api/admin/research-events/status")
    assert authenticated.status_code == 200
    assert authenticated.json()["available"] is True


def test_research_event_status_reports_disabled_when_capture_is_missing() -> None:
    response = _client().get("/api/admin/research-events/status")

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "status": "disabled",
        **_ZERO_METRICS,
    }


def test_research_event_status_reports_unavailable_when_enabled_capture_is_missing() -> None:
    config = SimpleNamespace(research_event_capture=SimpleNamespace(enabled=True))

    response = _client(config=config).get("/api/admin/research-events/status")

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "status": "unavailable",
        "reason": "capture_not_initialized",
        **_ZERO_METRICS,
    }


def test_research_event_status_contains_snapshot_failures_without_leaking_details() -> None:
    response = _client(capture=_FailingCapture()).get("/api/admin/research-events/status")

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "status": "error",
        "reason": "metrics_snapshot_failed",
        **_ZERO_METRICS,
    }
    assert "sensitive storage details" not in response.text


@pytest.mark.parametrize(
    "failure_metric",
    ("dropped_queue_full", "write_error", "error"),
)
def test_research_event_status_reports_degraded_for_capture_failures(
    failure_metric: str,
) -> None:
    metrics = {**_ZERO_METRICS, "received": 1, failure_metric: 1}

    response = _client(capture=_Capture(**metrics)).get("/api/admin/research-events/status")

    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "status": "degraded",
        **metrics,
    }


def test_research_event_status_keeps_pending_capture_healthy() -> None:
    metrics = {**_ZERO_METRICS, "received": 1, "enqueued": 1, "pending": 1}

    response = _client(capture=_Capture(**metrics)).get("/api/admin/research-events/status")

    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "status": "healthy",
        **metrics,
    }
