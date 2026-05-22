"""Admin dashboard: visual management panel for the bot.

Factory function pattern — accepts PluginContext and derives all runtime state
from it so the admin router stays decoupled from individual services.

AdminAuthMiddleware must be registered in bot.py BEFORE nonebot.run().
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from admin.auth import create_login_router
from admin.routes.api import create_api_router
from admin.routes.config_viewer import create_config_router
from admin.routes.dashboard import create_dashboard_router
from admin.routes.group_memory import create_group_memory_router
from admin.routes.groups import create_groups_router
from admin.routes.logs import create_logs_router
from admin.routes.soul import create_soul_router
from admin.routes.usage import create_usage_admin_router


def create_admin_router(ctx: Any, *, config_path: str = "") -> APIRouter:
    """Create the admin FastAPI router from PluginContext.

    Args:
        ctx: PluginContext with all system service references.
        config_path: Path to config file. Defaults to BOT_CONFIG_PATH env var
                     or "config/config.json".
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

    # Phase 0 P3: extract all service references for JSON API layer
    card_store = getattr(ctx, "card_store", None)
    retrieval_gate = getattr(ctx, "retrieval", None)
    group_memory_config = getattr(ctx, "group_memory_config", None)
    state_board = getattr(ctx, "state_board", None)
    tool_registry = getattr(ctx, "tool_registry", None)
    sticker_store = getattr(ctx, "sticker_store", None)
    mood_engine = getattr(ctx, "mood_engine", None)
    schedule_store = getattr(ctx, "schedule_store", None)
    affection_store = getattr(ctx, "affection_store", None)
    affection_engine = getattr(ctx, "affection_engine", None)
    scheduler = getattr(ctx, "scheduler", None)
    identity_mgr = getattr(ctx, "identity_mgr", None)
    bus = getattr(ctx, "bus", None)
    plugin_state_store = getattr(ctx, "plugin_state_store", None)
    plugin_config_store = getattr(ctx, "plugin_config_store", None)
    dream_agent = getattr(ctx, "dream", None)
    knowledge_base = getattr(ctx, "knowledge_base", None)
    memo_store = getattr(ctx, "memo_store", None)
    short_term_memory = getattr(ctx, "short_term", None)
    humanizer = getattr(ctx, "humanizer", None)
    talk_schedule = getattr(ctx, "talk_schedule", None)
    llm_client = getattr(ctx, "llm_client", None)

    if not config_path:
        config_path = os.environ.get("BOT_CONFIG_PATH", "config/config.json")

    router = APIRouter()

    # --- Static files ---
    _static_dir = Path(__file__).parent / "static"
    _index_html = _static_dir / "index.html"

    def _static_headers() -> dict[str, str]:
        return {
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        }

    def _immutable_asset_headers() -> dict[str, str]:
        # Vite emits hashed filenames under /admin/static/assets/* — content
        # changes ⇒ filename changes, so the browser can cache forever and
        # skip the network entirely on reload.
        return {"Cache-Control": "public, max-age=31536000, immutable"}

    def _headers_for_asset(asset_path: str) -> dict[str, str]:
        if asset_path.startswith("assets/"):
            return _immutable_asset_headers()
        return _static_headers()

    def _spa_headers() -> dict[str, str]:
        # Don't emit Clear-Site-Data: "cache" — it nukes the immutable hashed
        # bundle cache on every visit, defeating the whole point of long-lived
        # asset caching. Hash-based filenames already guarantee freshness.
        return _static_headers()

    def _spa_index_response() -> HTMLResponse | dict[str, str]:
        if not _index_html.is_file():
            return {"error": "SPA not built"}
        html = _index_html.read_text(encoding="utf-8")
        version = str(int(_index_html.stat().st_mtime))
        html = html.replace('.js"></script>', f'.js?v={version}"></script>')
        html = html.replace('.css">', f'.css?v={version}">')
        html = html.replace('href="favicon.svg"', f'href="/admin/static/favicon.svg?v={version}"')
        return HTMLResponse(html, headers=_spa_headers())

    if _static_dir.is_dir():
        router.mount("/admin/static", StaticFiles(directory=str(_static_dir)), name="admin_static")

    @router.get("/admin/static/{asset_path:path}", include_in_schema=False)
    async def admin_static_file(asset_path: str):
        """Serve built SPA assets before the history fallback catches /admin/*."""
        target = (_static_dir / asset_path).resolve()
        static_root = _static_dir.resolve()
        if target.is_file() and target.is_relative_to(static_root):
            return FileResponse(str(target), headers=_headers_for_asset(asset_path))
        raise HTTPException(status_code=404, detail="static asset not found")

    # --- SPA history mode fallback (must be before Jinja2 routes) ---
    # Any /admin/* path → serve Vue SPA index.html for browser requests

    @router.get("/admin/{rest:path}")
    async def spa_fallback(request: Request, rest: str):
        return _spa_index_response()

    @router.get("/admin")
    async def spa_fallback_root(request: Request):
        return _spa_index_response()

    # --- Login/logout routes ---
    router.include_router(create_login_router())

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
    router.include_router(create_soul_router(soul_dir, identity_mgr=identity_mgr))
    router.include_router(create_logs_router(log_dir))
    router.include_router(create_group_memory_router(
        card_store=card_store,
        group_memory_config=group_memory_config,
        retrieval_gate=retrieval_gate,
    ))

    # --- JSON API router (/api/admin/*) ---
    router.include_router(create_api_router(
        ctx=ctx,
        usage_tracker=usage_tracker,
        message_log=message_log,
        config=config,
        group_config=group_config,
        bot_start_time=bot_start_time,
        soul_dir=soul_dir,
        log_dir=log_dir,
        config_path=config_path,
        card_store=card_store,
        group_memory_config=group_memory_config,
        retrieval_gate=retrieval_gate,
        state_board=state_board,
        tool_registry=tool_registry,
        sticker_store=sticker_store,
        mood_engine=mood_engine,
        schedule_store=schedule_store,
        affection_store=affection_store,
        affection_engine=affection_engine,
        scheduler=scheduler,
        identity_mgr=identity_mgr,
        bus=bus,
        plugin_state_store=plugin_state_store,
        plugin_config_store=plugin_config_store,
        dream_agent=dream_agent,
        knowledge_base=knowledge_base,
        memo_store=memo_store,
        short_term_memory=short_term_memory,
        humanizer=humanizer,
        talk_schedule=talk_schedule,
        llm_client=llm_client,
        bot=getattr(ctx, "bot", None),
    ))

    return router
