"""Soul editor — view and edit identity.md / instruction.md."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request

from admin.templates import render


def create_soul_router(soul_dir: str = "config/soul") -> APIRouter:
    router = APIRouter()

    def _read_file(name: str) -> str:
        p = Path(soul_dir) / name
        if not p.is_file():
            return ""
        return p.read_text(encoding="utf-8")

    def _write_file(name: str, content: str) -> None:
        p = Path(soul_dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    @router.get("/admin/soul")
    async def soul_page(request: Request):
        identity = _read_file("identity.md")
        instruction = _read_file("instruction.md")
        return await render("soul.html", {
            "request": request,
            "active_page": "soul",
            "identity": identity,
            "instruction": instruction,
        })

    @router.post("/admin/soul/save")
    async def save_soul(
        request: Request,
        file: str = Form(...),
        content: str = Form(...),
    ):
        if file not in ("identity.md", "instruction.md"):
            return await render("soul.html", {
                "request": request,
                "active_page": "soul",
                "identity": _read_file("identity.md"),
                "instruction": _read_file("instruction.md"),
                "messages": [{"type": "danger", "text": f"Invalid file: {file}"}],
            })
        try:
            _write_file(file, content)
        except Exception as e:
            return await render("soul.html", {
                "request": request,
                "active_page": "soul",
                "identity": _read_file("identity.md"),
                "instruction": _read_file("instruction.md"),
                "messages": [{"type": "danger", "text": f"保存失败: {e}"}],
            })

        return await render("soul.html", {
            "request": request,
            "active_page": "soul",
            "identity": _read_file("identity.md"),
            "instruction": _read_file("instruction.md"),
            "messages": [{"type": "success", "text": f"{file} 已保存。执行 docker compose restart bot 后生效。"}],
        })

    return router
