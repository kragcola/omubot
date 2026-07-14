"""Black-box contracts for backup routes assembled under the Admin API."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api import create_api_router
from kernel.config import BotConfig


class _BackupServiceStub:
    def __init__(self) -> None:
        self.requested_profiles: list[str] = []

    def list_backups(self, profile: str = "daily") -> list[dict[str, Any]]:
        self.requested_profiles.append(profile)
        return [{"backup_id": "fixture", "profile": profile}]


class _BackupSchedulerStub:
    def __init__(self) -> None:
        self._settings = {
            "enabled": True,
            "daily_time": "04:30",
            "keep_days": 7,
            "default_profile": "daily",
            "quick_check_enabled": True,
            "quick_check_interval_minutes": 60,
        }
        self._service = _BackupServiceStub()
        self.run_profiles: list[str | None] = []
        self.quick_check_runs = 0
        self._last_quick_check = (
            datetime(2026, 7, 13, 8, 30, tzinfo=UTC),
            [
                SimpleNamespace(
                    db_id="usage",
                    path="storage/usage.db",
                    ok=True,
                    quick_check="ok",
                    journal_mode="wal",
                    error=None,
                ),
                SimpleNamespace(
                    db_id="slang",
                    path="storage/slang.db",
                    ok=False,
                    quick_check="error",
                    journal_mode="",
                    error="corrupt",
                ),
            ],
        )

    @property
    def settings(self) -> dict[str, Any]:
        return dict(self._settings)

    @property
    def last_quick_check(self) -> tuple[datetime | None, list[Any]]:
        checked_at, results = self._last_quick_check
        return checked_at, list(results)

    async def run_now(self, profile: str | None = None) -> dict[str, Any]:
        self.run_profiles.append(profile)
        return {
            "backup_id": f"manual-{profile or 'daily'}",
            "profile": profile or "daily",
            "summary": {"trusted": True},
            "complete": True,
            "skipped_host_only": [],
        }

    async def run_quick_check_now(self) -> list[Any]:
        self.quick_check_runs += 1
        return self.last_quick_check[1]


class _CancellationSafeScheduler(_BackupSchedulerStub):
    def __init__(self) -> None:
        super().__init__()
        self.stop_entered = asyncio.Event()
        self.allow_stop = asyncio.Event()
        self.reload_completed = asyncio.Event()

    async def reload(
        self,
        daily_time: str,
        keep_days: int,
        default_profile: str,
        enabled: bool,
        *,
        quick_check_enabled: bool | None = None,
        quick_check_interval_minutes: int | None = None,
    ) -> None:
        next_settings = {
            "enabled": enabled,
            "daily_time": daily_time,
            "keep_days": keep_days,
            "default_profile": default_profile,
            "quick_check_enabled": (
                self._settings["quick_check_enabled"] if quick_check_enabled is None else quick_check_enabled
            ),
            "quick_check_interval_minutes": (
                self._settings["quick_check_interval_minutes"]
                if quick_check_interval_minutes is None
                else quick_check_interval_minutes
            ),
        }
        self.stop_entered.set()
        try:
            await self.allow_stop.wait()
        except asyncio.CancelledError:
            await self.allow_stop.wait()
            self._settings = next_settings
            self.reload_completed.set()
            raise
        self._settings = next_settings
        self.reload_completed.set()


class _BlockingFirstReloadScheduler(_BackupSchedulerStub):
    def __init__(self) -> None:
        super().__init__()
        self._reload_lock = asyncio.Lock()
        self._reload_attempts = 0
        self.first_reload_entered = asyncio.Event()
        self.second_reload_attempted = asyncio.Event()
        self.allow_first_reload = asyncio.Event()

    async def reload(
        self,
        daily_time: str,
        keep_days: int,
        default_profile: str,
        enabled: bool,
        *,
        quick_check_enabled: bool | None = None,
        quick_check_interval_minutes: int | None = None,
    ) -> None:
        self._reload_attempts += 1
        attempt = self._reload_attempts
        if attempt == 2:
            self.second_reload_attempted.set()
        async with self._reload_lock:
            if attempt == 1:
                self.first_reload_entered.set()
                await self.allow_first_reload.wait()
            self._settings = {
                "enabled": enabled,
                "daily_time": daily_time,
                "keep_days": keep_days,
                "default_profile": default_profile,
                "quick_check_enabled": (
                    self._settings["quick_check_enabled"] if quick_check_enabled is None else quick_check_enabled
                ),
                "quick_check_interval_minutes": (
                    self._settings["quick_check_interval_minutes"]
                    if quick_check_interval_minutes is None
                    else quick_check_interval_minutes
                ),
            }


def _assembled_app(tmp_path: Path, scheduler: _BackupSchedulerStub) -> FastAPI:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    app = FastAPI()
    app.include_router(
        create_api_router(
            ctx=SimpleNamespace(backup_scheduler=scheduler),
            config=BotConfig(),
            config_path=str(config_path),
            repo_root=tmp_path,
        )
    )
    return app


def test_assembled_backup_routes_have_one_handler_per_method_and_path(
    tmp_path: Path,
) -> None:
    app = _assembled_app(tmp_path, _BackupSchedulerStub())
    method_paths = Counter(
        (method, str(getattr(route, "path", "")))
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
    )

    assert method_paths[("GET", "/api/admin/backup/settings")] == 1
    assert method_paths[("POST", "/api/admin/backup/settings")] == 1
    assert method_paths[("GET", "/api/admin/backup/list")] == 1
    assert method_paths[("GET", "/api/admin/backup/quick-check")] == 1
    assert method_paths[("POST", "/api/admin/backup/quick-check")] == 1


@pytest.mark.asyncio
async def test_cancelled_settings_request_persists_completed_reload_before_propagating(
    tmp_path: Path,
) -> None:
    scheduler = _CancellationSafeScheduler()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"backup": scheduler.settings}),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(
        create_api_router(
            ctx=SimpleNamespace(backup_scheduler=scheduler),
            config=BotConfig(),
            config_path=str(config_path),
            repo_root=tmp_path,
        )
    )
    requested_settings = {
        "enabled": True,
        "daily_time": "07:40",
        "keep_days": 14,
        "default_profile": "migration",
        "quick_check_enabled": False,
        "quick_check_interval_minutes": 120,
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        request_task = asyncio.create_task(
            client.post(
                "/api/admin/backup/settings",
                json=requested_settings,
            )
        )
        await asyncio.wait_for(scheduler.stop_entered.wait(), timeout=1)
        request_task.cancel()
        await asyncio.sleep(0)
        assert request_task.done() is False
        scheduler.allow_stop.set()
        with pytest.raises(asyncio.CancelledError):
            await request_task

    assert scheduler.reload_completed.is_set()
    assert scheduler.settings == requested_settings
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["backup"] == scheduler.settings


@pytest.mark.asyncio
async def test_concurrent_partial_settings_updates_preserve_both_changes(
    tmp_path: Path,
) -> None:
    scheduler = _BlockingFirstReloadScheduler()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"backup": scheduler.settings}),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(
        create_api_router(
            ctx=SimpleNamespace(backup_scheduler=scheduler),
            config=BotConfig(),
            config_path=str(config_path),
            repo_root=tmp_path,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        request_a = asyncio.create_task(
            client.post(
                "/api/admin/backup/settings",
                json={"keep_days": 14},
            )
        )
        await asyncio.wait_for(scheduler.first_reload_entered.wait(), timeout=1)
        request_b = asyncio.create_task(
            client.post(
                "/api/admin/backup/settings",
                json={"daily_time": "07:40"},
            )
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                asyncio.shield(scheduler.second_reload_attempted.wait()),
                timeout=0.2,
            )
        scheduler.allow_first_reload.set()
        response_a, response_b = await asyncio.gather(request_a, request_b)

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    expected = {
        "enabled": True,
        "daily_time": "07:40",
        "keep_days": 14,
        "default_profile": "daily",
        "quick_check_enabled": True,
        "quick_check_interval_minutes": 60,
    }
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert scheduler.settings == expected
    assert persisted["backup"] == expected
    assert persisted["backup"] == scheduler.settings


def test_get_backup_settings_url_and_json_contract_remains_stable(
    tmp_path: Path,
) -> None:
    scheduler = _BackupSchedulerStub()
    client = TestClient(_assembled_app(tmp_path, scheduler))

    settings_response = client.get("/api/admin/backup/settings")
    assert settings_response.status_code == 200
    assert settings_response.json() == scheduler.settings


def test_get_backup_list_url_and_json_contract_remains_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    scheduler = _BackupSchedulerStub()
    client = TestClient(_assembled_app(tmp_path, scheduler))

    list_response = client.get(
        "/api/admin/backup/list",
        params={"profile": "migration"},
    )
    assert list_response.status_code == 200
    assert list_response.json() == {
        "items": [{"backup_id": "fixture", "profile": "migration"}],
        "profile": "migration",
    }
    assert scheduler._service.requested_profiles == ["migration"]


def test_post_backup_create_url_and_json_contract_remains_stable(
    tmp_path: Path,
) -> None:
    scheduler = _BackupSchedulerStub()
    client = TestClient(_assembled_app(tmp_path, scheduler))

    create_response = client.post(
        "/api/admin/backup/create",
        json={"profile": "migration"},
    )
    assert create_response.status_code == 200
    assert create_response.json() == {
        "manifest": {
            "backup_id": "manual-migration",
            "profile": "migration",
            "summary": {"trusted": True},
            "complete": True,
            "skipped_host_only": [],
        }
    }
    assert scheduler.run_profiles == ["migration"]


def _expected_quick_check_payload() -> dict[str, Any]:
    return {
        "last_run_at": "2026-07-13T08:30:00+00:00",
        "results": [
            {
                "db_id": "usage",
                "path": "storage/usage.db",
                "ok": True,
                "quick_check": "ok",
                "journal_mode": "wal",
                "error": None,
            },
            {
                "db_id": "slang",
                "path": "storage/slang.db",
                "ok": False,
                "quick_check": "error",
                "journal_mode": "",
                "error": "corrupt",
            },
        ],
        "ok_count": 1,
        "fail_count": 1,
    }


def test_get_backup_quick_check_json_contract_remains_stable(
    tmp_path: Path,
) -> None:
    scheduler = _BackupSchedulerStub()
    client = TestClient(_assembled_app(tmp_path, scheduler))

    response = client.get("/api/admin/backup/quick-check")

    assert response.status_code == 200
    assert response.json() == _expected_quick_check_payload()
    assert scheduler.quick_check_runs == 0


def test_post_backup_quick_check_runs_sweep_and_returns_json_contract(
    tmp_path: Path,
) -> None:
    scheduler = _BackupSchedulerStub()
    client = TestClient(_assembled_app(tmp_path, scheduler))

    response = client.post("/api/admin/backup/quick-check")

    assert response.status_code == 200
    assert response.json() == _expected_quick_check_payload()
    assert scheduler.quick_check_runs == 1


def test_legacy_post_backup_url_and_json_contract_remains_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    scheduler = _BackupSchedulerStub()
    client = TestClient(_assembled_app(tmp_path, scheduler))

    response = client.post(
        "/api/admin/backup",
        params={"profile": "migration"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "ok",
        "backup_id",
        "summary",
        "complete",
        "skipped_host_only",
        "message",
    }
    assert payload["ok"] is True
    assert payload["backup_id"] == "manual-migration"
    assert payload["summary"] == {"trusted": True}
    assert payload["complete"] is True
    assert payload["skipped_host_only"] == []
    assert isinstance(payload["message"], str)
    assert payload["message"]
    assert scheduler.run_profiles == ["migration"]
