"""Read-only API for the explainable effective configuration snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from fastapi import APIRouter

from services.effective_config import build_effective_config_snapshot


def create_effective_config_router(
    *,
    config_path: str = "config/config.json",
    project_root: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/config", tags=["config"])

    @router.get("/effective")
    async def get_effective_config() -> dict[str, object]:
        return build_effective_config_snapshot(
            config_path=config_path,
            project_root=project_root,
            environment=environment,
        ).to_dict()

    return router
