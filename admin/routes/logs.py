"""Log viewer — list and tail log files."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request

from admin.templates import render


def create_logs_router(log_dir: str = "storage/logs") -> APIRouter:
    router = APIRouter()

    def _list_logs() -> list[dict]:
        p = Path(log_dir)
        if not p.is_dir():
            return []
        files = sorted(p.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
        return [
            {"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime}
            for f in files
        ]

    def _tail(file_name: str, lines: int = 200) -> str:
        p = Path(log_dir) / file_name
        if not p.is_file():
            return f"File not found: {file_name}"
        if ".." in file_name or "/" in file_name:
            return "Invalid file name"
        content = p.read_text(encoding="utf-8", errors="replace")
        all_lines = content.splitlines()
        return "\n".join(all_lines[-lines:])

    @router.get("/admin/logs")
    async def logs_page(request: Request):
        files = _list_logs()
        return await render("logs.html", {
            "request": request,
            "active_page": "logs",
            "log_files": files,
            "selected_file": None,
            "log_content": "",
            "lines": 200,
        })

    @router.get("/admin/logs/view")
    async def view_log(
        request: Request,
        file: str = Query(...),
        lines: int = Query(200),
    ):
        files = _list_logs()
        content = _tail(file, lines)
        return await render("logs.html", {
            "request": request,
            "active_page": "logs",
            "log_files": files,
            "selected_file": file,
            "log_content": content,
            "lines": lines,
        })

    return router
