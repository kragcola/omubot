"""Config viewer — read-only display of config.toml."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from admin.templates import render


def create_config_router(config_path: str = "config/config.toml") -> APIRouter:
    router = APIRouter()

    def _read_config() -> str:
        p = Path(config_path)
        if not p.is_file():
            return "# config.toml not found"
        return p.read_text(encoding="utf-8")

    @router.get("/admin/config")
    async def config_page(request: Request):
        content = _read_config()
        return await render("config_viewer.html", {
            "request": request,
            "active_page": "config",
            "config_content": content,
        })

    return router
