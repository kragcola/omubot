"""Soul editor — fallback view for identity.md / instruction.md."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request

from admin.templates import render


def create_soul_router(soul_dir: str = "config/soul", identity_mgr: Any = None) -> APIRouter:
    router = APIRouter()
    _soul = Path(soul_dir)

    def _read(name: str) -> str:
        p = _soul / name
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    def _write(name: str, content: str) -> None:
        _soul.mkdir(parents=True, exist_ok=True)
        (_soul / name).write_text(content, encoding="utf-8")

    async def _page(request: Request, **extra):
        ctx: dict = {"request": request, "active_page": "soul"}
        ctx.update(
            identity=_read("identity.md"),
            instruction=_read("instruction.md"),
        )
        ctx.update(extra)
        return await render("soul.html", ctx)

    @router.get("/admin/soul")
    async def soul_page(request: Request):
        return await _page(request)

    @router.post("/admin/soul/save")
    async def save_soul(
        request: Request,
        file: str = Form(...),
        content: str = Form(...),
    ):
        valid = {"identity.md", "instruction.md"}
        if file not in valid:
            return await _page(request, messages=[
                {"type": "danger", "text": f"Invalid file: {file}"}
            ])

        try:
            _write(file, content)
        except Exception as e:
            return await _page(request, messages=[
                {"type": "danger", "text": f"保存失败: {e}"}
            ])

        # Phase 0 P6: hot-reload identity after save
        reload_note = ""
        if identity_mgr is not None:
            try:
                await identity_mgr.load_file(str(_soul / "identity.md"))
                reload_note = "（已自动重载，无需重启）"
            except Exception:
                reload_note = "（重载失败，请执行 docker compose restart bot）"

        return await _page(request, messages=[
            {"type": "success", "text": f"{file} 已保存。{reload_note}"}
        ])

    return router
