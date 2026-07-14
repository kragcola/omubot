"""Read-only DatabaseCatalog status API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from services.storage.status import inspect_database_catalog


def create_databases_router(*, repo_root: str | Path) -> APIRouter:
    router = APIRouter(prefix="/databases", tags=["databases"])
    root = Path(repo_root).resolve()

    @router.get("")
    def get_databases() -> dict[str, Any]:
        return inspect_database_catalog(root).to_dict()

    return router
