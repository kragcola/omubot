"""JSON API: logs — log file listing and content tail."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query


def create_logs_router(*, log_dir: str = "storage/logs") -> APIRouter:
    router = APIRouter()

    def _log_path(file: str) -> Path:
        p = Path(log_dir) / file
        p.resolve().relative_to(Path(log_dir).resolve())
        return p

    @router.get("/logs")
    async def list_logs():
        base = Path(log_dir)
        if not base.is_dir():
            return {"files": []}

        files = sorted(
            [f.name for f in base.iterdir() if f.is_file() and f.suffix in (".log", ".txt")],
            reverse=True,
        )
        return {"files": files}

    @router.get("/logs/view")
    async def view_log(
        file: str = Query(...),
        lines: int = Query(200),
    ):
        p = _log_path(file)
        if not p.is_file():
            return {"file": file, "content": "", "error": "File not found"}

        try:
            with open(p, encoding="utf-8") as f:
                all_lines = f.readlines()
            tail = "".join(all_lines[-lines:])
            return {"file": file, "content": tail, "total_lines": len(all_lines)}
        except Exception as e:
            return {"file": file, "content": "", "error": str(e)}

    return router
