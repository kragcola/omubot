from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.routes.api.plugins import create_plugins_router
from kernel.bus import PluginBus

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins"


class _DegradedVisionClient:
    def health_snapshot(self) -> dict[str, object]:
        return {
            "available": True,
            "status": "failed",
            "calls": 7,
            "errors": 2,
            "last_error": "vision probe failed",
        }


class _HealthyVisionClient:
    def health_snapshot(self) -> dict[str, object]:
        return {
            "available": True,
            "status": "healthy",
            "calls": 3,
            "errors": 1,
            "last_error": "",
        }


def test_vision_admin_state_reflects_unavailable_runtime_client() -> None:
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            bus=PluginBus(),
            ctx=SimpleNamespace(vision_client=None),
            plugin_root=PLUGIN_ROOT,
        ),
        prefix="/api/admin",
    )
    client = TestClient(app)

    list_response = client.get("/api/admin/plugins?include_system=true")
    detail_response = client.get("/api/admin/plugins/vision")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    vision_list = next(
        item
        for item in list_response.json()["plugins"]
        if item["name"] == "vision"
    )
    vision_detail = detail_response.json()

    for payload in (vision_list, vision_detail):
        assert payload["capability_only"] is True
        assert payload["enabled"] is False
        assert payload["health"]["enabled"] is False
        assert payload["health"]["state"] == "disabled"


def test_vision_admin_health_reflects_degraded_runtime_probe() -> None:
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            bus=PluginBus(),
            ctx=SimpleNamespace(vision_client=_DegradedVisionClient()),
            plugin_root=PLUGIN_ROOT,
        ),
        prefix="/api/admin",
    )
    client = TestClient(app)

    list_response = client.get("/api/admin/plugins?include_system=true")
    detail_response = client.get("/api/admin/plugins/vision")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    vision_list = next(
        item
        for item in list_response.json()["plugins"]
        if item["name"] == "vision"
    )
    vision_detail = detail_response.json()

    for payload in (vision_list, vision_detail):
        assert payload["capability_only"] is True
        assert payload["enabled"] is True
        assert payload["health"]["enabled"] is True
        assert payload["health"]["state"] == "degraded"
        assert payload["health"]["calls"] == 7
        assert payload["health"]["errors"] == 2
        assert payload["health"]["last_error"] == "vision probe failed"


def test_vision_admin_health_reflects_successful_runtime_probe() -> None:
    app = FastAPI()
    app.include_router(
        create_plugins_router(
            bus=PluginBus(),
            ctx=SimpleNamespace(vision_client=_HealthyVisionClient()),
            plugin_root=PLUGIN_ROOT,
        ),
        prefix="/api/admin",
    )
    client = TestClient(app)

    vision_list = next(
        item
        for item in client.get(
            "/api/admin/plugins?include_system=true"
        ).json()["plugins"]
        if item["name"] == "vision"
    )
    vision_detail = client.get("/api/admin/plugins/vision").json()

    for payload in (vision_list, vision_detail):
        assert payload["enabled"] is True
        assert payload["health"]["state"] == "healthy"
        assert payload["health"]["calls"] == 3
        assert payload["health"]["errors"] == 1
        assert payload["health"]["last_error"] == ""
