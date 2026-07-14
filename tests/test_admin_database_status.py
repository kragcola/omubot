"""Admin JSON contract for governed database status."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api import create_api_router
from admin.routes.api.databases import create_databases_router
from services.storage.catalog import DEFAULT_DATABASE_CATALOG


def test_database_status_endpoint_exposes_read_only_catalog_snapshot(
    tmp_path: Path,
) -> None:
    usage_path = DEFAULT_DATABASE_CATALOG.resolve(tmp_path, "usage")
    usage_path.parent.mkdir(parents=True)
    with sqlite3.connect(usage_path) as connection:
        connection.execute("CREATE TABLE preserved (id INTEGER PRIMARY KEY)")
        connection.execute("PRAGMA user_version = 1")
    before_bytes = usage_path.read_bytes()

    app = FastAPI()
    app.include_router(create_databases_router(repo_root=tmp_path))
    response = TestClient(app).get("/databases")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total"] == 21
    assert payload["summary"]["ok_count"] == 1
    assert payload["summary"]["missing_count"] == 20
    by_id = {item["db_id"]: item for item in payload["items"]}
    usage = by_id["usage"]
    assert usage["owner"] == DEFAULT_DATABASE_CATALOG.get("usage").owner
    assert usage["user_version"] == 1
    assert usage["target_user_version"] == 1
    assert usage["connection_profile"] == "wal_normal"
    assert usage["backup_profile"] == "daily"
    assert usage["retention_profile"] == "forever"
    assert usage_path.read_bytes() == before_bytes


def test_database_status_router_is_mounted_under_admin_prefix(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}", encoding="utf-8")
    app = FastAPI()
    app.include_router(
        create_api_router(config_path=str(config_path), repo_root=tmp_path)
    )

    response = TestClient(app).get("/api/admin/databases")

    assert response.status_code == 200
    assert response.json()["summary"]["total"] == 21
