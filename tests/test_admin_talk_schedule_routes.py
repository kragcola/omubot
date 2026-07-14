from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.routes.api import create_api_router
from admin.routes.api.schedule import create_schedule_router
from admin.routes.api.system import create_system_router
from services.talk_schedule import TalkSchedule


class _StaticTalkSchedule:
    def __init__(self, multiplier: float) -> None:
        self._multiplier = multiplier

    def get_time_multiplier(self) -> float:
        return self._multiplier


def _as_talk_schedule(multiplier: float) -> TalkSchedule:
    return cast(TalkSchedule, _StaticTalkSchedule(multiplier))


def test_schedule_endpoint_uses_injected_talk_schedule() -> None:
    app = FastAPI()
    app.include_router(
        create_schedule_router(talk_schedule=_as_talk_schedule(2.5)),
        prefix="/api/admin",
    )

    response = TestClient(app).get("/api/admin/schedule")

    assert response.status_code == 200
    assert response.json()["time_multiplier"] == 2.5


def test_system_talk_schedule_endpoint_uses_injected_talk_schedule() -> None:
    app = FastAPI()
    app.include_router(
        create_system_router(talk_schedule=_as_talk_schedule(0.75)),
        prefix="/api/admin",
    )

    response = TestClient(app).get("/api/admin/talk-schedule")

    assert response.status_code == 200
    assert response.json() == {"time_multiplier": 0.75}


def test_aggregated_admin_routes_share_runtime_schedule_after_custom_file_breaks(
    tmp_path: Path,
) -> None:
    schedule_path = tmp_path / "custom-talk-schedule.json"
    schedule_path.write_text(
        '{"global_multiplier": 1.75, "schedule": []}',
        encoding="utf-8",
    )
    runtime_schedule = TalkSchedule(str(schedule_path))
    assert runtime_schedule.get_time_multiplier() == 1.75

    original_stat = schedule_path.stat()
    schedule_path.write_text("{broken json", encoding="utf-8")
    os.utime(
        schedule_path,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000_000),
    )

    app = FastAPI()
    app.include_router(
        create_api_router(
            talk_schedule=runtime_schedule,
            config_path=str(tmp_path / "config.json"),
            repo_root=tmp_path,
        )
    )
    client = TestClient(app)

    schedule_response = client.get("/api/admin/schedule")
    system_response = client.get("/api/admin/talk-schedule")

    assert schedule_response.status_code == 200
    assert schedule_response.json()["time_multiplier"] == 1.75
    assert system_response.status_code == 200
    assert system_response.json() == {"time_multiplier": 1.75}
