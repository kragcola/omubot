"""Backup admin API — exposes BackupScheduler + BackupService to the dashboard.

Wires the Phase 2 backup machinery into `/api/admin/backup/*`:

- `GET  /settings`       — current scheduler config (daily_time / keep_days /
                           default_profile / quick_check_*)
- `POST /settings`       — hot-reload scheduler config + persist into config.json
- `GET  /list`           — manifest list (most recent first)
- `POST /create`         — trigger an immediate backup
- `GET  /quick-check`    — last quick_check sweep results + timestamp
- `POST /quick-check`    — trigger an immediate quick_check sweep

All write paths require admin auth (handled by AdminAuthMiddleware on the
parent router); responses follow the existing admin convention of plain dicts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

_L = logger.bind(channel="backup")


class BackupSettingsPayload(BaseModel):
    enabled: bool | None = None
    daily_time: str | None = None
    keep_days: int | None = None
    default_profile: str | None = None
    quick_check_enabled: bool | None = None
    quick_check_interval_minutes: int | None = None


class BackupCreatePayload(BaseModel):
    profile: str | None = None


def _serialize_quick_check(scheduler: Any) -> dict[str, Any]:
    """Render the scheduler's last quick_check snapshot as JSON-friendly dict."""
    last_at, results = scheduler.last_quick_check
    return {
        "last_run_at": last_at.isoformat() if last_at is not None else None,
        "results": [
            {
                "db_id": r.db_id,
                "path": r.path,
                "ok": r.ok,
                "quick_check": r.quick_check,
                "journal_mode": r.journal_mode,
                "error": r.error,
            }
            for r in results
        ],
        "ok_count": sum(1 for r in results if r.ok),
        "fail_count": sum(1 for r in results if not r.ok),
    }


def _persist_backup_config(config_path: str, payload: dict[str, Any]) -> None:
    """Patch config.json's `backup` block in-place; admin auth is upstream."""
    p = Path(config_path)
    if not p.exists():
        # Nothing to patch; scheduler still gets the live update via reload().
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover — corrupt config json
        raise HTTPException(status_code=500, detail=f"读取 config.json 失败: {exc}") from exc
    backup_block = data.get("backup") or {}
    backup_block.update({k: v for k, v in payload.items() if v is not None})
    data["backup"] = backup_block
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def create_backup_router(
    *,
    backup_scheduler: Any,
    config_path: str,
) -> APIRouter:
    router = APIRouter(prefix="/backup", tags=["backup"])

    @router.get("/settings")
    async def get_settings() -> dict[str, Any]:
        if backup_scheduler is None:
            raise HTTPException(status_code=503, detail="backup scheduler 未启用")
        return backup_scheduler.settings

    @router.post("/settings")
    async def update_settings(payload: BackupSettingsPayload) -> dict[str, Any]:
        if backup_scheduler is None:
            raise HTTPException(status_code=503, detail="backup scheduler 未启用")

        # Pydantic-bound payload — only fields explicitly set are applied.
        body = payload.model_dump(exclude_none=True)

        # Validate via Pydantic before applying — daily_time format & ranges.
        from kernel.config import BackupConfig
        merged = {**backup_scheduler.settings, **body}
        try:
            BackupConfig(**merged)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"配置非法: {exc}") from exc

        backup_scheduler.reload(
            daily_time=merged["daily_time"],
            keep_days=merged["keep_days"],
            default_profile=merged["default_profile"],
            enabled=merged["enabled"],
            quick_check_enabled=merged["quick_check_enabled"],
            quick_check_interval_minutes=merged["quick_check_interval_minutes"],
        )
        _persist_backup_config(config_path, body)
        _L.info(f"backup settings updated via admin: {body}")
        return backup_scheduler.settings

    @router.get("/list")
    async def list_backups(profile: str = "daily") -> dict[str, Any]:
        if backup_scheduler is None:
            raise HTTPException(status_code=503, detail="backup scheduler 未启用")
        service = backup_scheduler._service
        items = service.list_backups(profile=profile) if hasattr(service, "list_backups") else []
        return {"items": items, "profile": profile}

    @router.post("/create")
    async def create_backup(payload: BackupCreatePayload | None = None) -> dict[str, Any]:
        if backup_scheduler is None:
            raise HTTPException(status_code=503, detail="backup scheduler 未启用")
        profile = payload.profile if payload else None
        manifest = await backup_scheduler.run_now(profile=profile)
        return {"manifest": manifest}

    @router.get("/quick-check")
    async def get_quick_check() -> dict[str, Any]:
        if backup_scheduler is None:
            raise HTTPException(status_code=503, detail="backup scheduler 未启用")
        return _serialize_quick_check(backup_scheduler)

    @router.post("/quick-check")
    async def run_quick_check() -> dict[str, Any]:
        if backup_scheduler is None:
            raise HTTPException(status_code=503, detail="backup scheduler 未启用")
        await backup_scheduler.run_quick_check_now()
        return _serialize_quick_check(backup_scheduler)

    return router
