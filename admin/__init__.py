"""Admin dashboard: visual management panel for the bot.

Factory function pattern — accepts PluginContext and derives all runtime state
from it so the admin router stays decoupled from individual services.

AdminAuthMiddleware must be registered in bot.py BEFORE nonebot.run().
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.staticfiles import StaticFiles

from admin.auth import create_login_router
from admin.routes.config_viewer import create_config_router
from admin.routes.dashboard import create_dashboard_router
from admin.routes.groups import create_groups_router
from admin.routes.logs import create_logs_router
from admin.routes.soul import create_soul_router
from admin.routes.usage import create_usage_admin_router


def create_admin_router(ctx: Any, *, config_path: str = "") -> APIRouter:
    """Create the admin FastAPI router from PluginContext.

    Args:
        ctx: PluginContext with all system service references.
        config_path: Path to config.toml. Defaults to BOT_CONFIG_PATH env var
                     or "config/config.toml".
    """
    usage_tracker = getattr(ctx, "usage_tracker", None)
    message_log = getattr(ctx, "msg_log", None)
    config = ctx.config
    group_config = getattr(config, "group", None) if config else None
    admins: dict[str, str] | None = getattr(config, "admins", None) if config else None
    bot_start_time: float = getattr(ctx, "bot_start_time", 0.0)
    soul_cfg = getattr(config, "soul", None) if config else None
    soul_dir: str = soul_cfg.dir if soul_cfg else "config/soul"
    log_cfg = getattr(config, "log", None) if config else None
    log_dir: str = log_cfg.dir if log_cfg else "storage/logs"

    if not config_path:
        config_path = os.environ.get("BOT_CONFIG_PATH", "config/config.toml")

    router = APIRouter()

    # --- Login/logout routes ---
    router.include_router(create_login_router())

    # --- Static files ---
    _static_dir = Path(__file__).parent / "static"
    if _static_dir.is_dir():
        router.mount("/admin/static", StaticFiles(directory=str(_static_dir)), name="admin_static")

    # --- Sub-routers (protected by AdminAuthMiddleware) ---
    router.include_router(
        create_dashboard_router(
            usage_tracker=usage_tracker,
            message_log=message_log,
            group_config=group_config,
            admins=admins,
            bot_start_time=bot_start_time,
        )
    )
    router.include_router(create_usage_admin_router(usage_tracker))
    router.include_router(
        create_groups_router(
            group_config=group_config,
            message_log=message_log,
        )
    )
    router.include_router(create_config_router(config_path))
    router.include_router(create_soul_router(soul_dir))
    router.include_router(create_logs_router(log_dir))

    return router
