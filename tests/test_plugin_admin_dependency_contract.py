from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.routes.api.plugins import create_plugins_router
from kernel.bus import PluginBus
from kernel.types import AmadeusPlugin
from services.health import _check_plugin_bus


class DependencyAdminPlugin(AmadeusPlugin):
    name = "dependency_admin"
    display_name: ClassVar[dict[str, str]] = {
        "zh": "依赖管理",
        "en": "Dependency Admin",
    }
    version = "1.0.0"
    priority = 100
    dependencies: ClassVar[dict[str, str]] = {"legacy": ">=1.0.0"}
    required_dependencies: ClassVar[dict[str, str]] = {"required": ">=2.0.0"}
    optional_dependencies: ClassVar[dict[str, str]] = {"optional": ">=3.0.0"}


def test_admin_plugin_surfaces_expose_effective_dependencies(tmp_path: Path) -> None:
    bus = PluginBus()
    bus.register(DependencyAdminPlugin())
    app = FastAPI()
    app.include_router(
        create_plugins_router(bus=bus, plugin_root=tmp_path / "plugins"),
        prefix="/api/admin",
    )
    client = TestClient(app)

    list_response = client.get("/api/admin/plugins?include_system=true")
    detail_response = client.get("/api/admin/plugins/dependency_admin")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    list_item = next(
        item
        for item in list_response.json()["plugins"]
        if item["name"] == "dependency_admin"
    )
    detail = detail_response.json()
    expected_required = {
        "legacy": ">=1.0.0",
        "required": ">=2.0.0",
    }
    expected_optional = {"optional": ">=3.0.0"}

    for payload in (list_item, detail):
        assert payload.get("required_dependencies", {}) == expected_required
        assert payload.get("optional_dependencies", {}) == expected_optional
    assert detail.get("dependencies", {}) == {"legacy": ">=1.0.0"}


def test_admin_plugin_surfaces_expose_optional_dependency_degraded_truth(
    tmp_path: Path,
) -> None:
    plugin = DependencyAdminPlugin()
    cast(Any, plugin).dependencies = {}
    cast(Any, plugin).required_dependencies = {}
    cast(Any, plugin).optional_dependencies = {"missing_optional": ">=1.0.0"}
    bus = PluginBus()
    bus.register(plugin)
    asyncio.run(bus.fire_on_startup(cast(Any, None)))
    app = FastAPI()
    app.include_router(
        create_plugins_router(bus=bus, plugin_root=tmp_path / "plugins"),
        prefix="/api/admin",
    )
    client = TestClient(app)

    list_item = next(
        item
        for item in client.get(
            "/api/admin/plugins?include_system=true"
        ).json()["plugins"]
        if item["name"] == plugin.name
    )
    detail = client.get(f"/api/admin/plugins/{plugin.name}").json()

    for payload in (list_item, detail):
        health = payload["health"]
        assert health["state"] == "degraded"
        assert health["optional_dependency_degraded"] is True
        assert any(
            "missing_optional" in error
            for error in health["optional_dependency_errors"]
        )
        assert payload["package"]["optional_dependency_degraded"] is True

    system_health = _check_plugin_bus(ctx=SimpleNamespace(bus=bus))
    assert system_health["status"] == "warning"
    assert system_health["meta"]["optional_dependency_degraded"] == 1
