"""JSON API: Persona Source Importer Part A."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from services.persona.writer import PersonaDraftWriter, persona_namespace


class PersonaImportPayload(BaseModel):
    persona_id: str
    source_path: str = ""
    strict: bool = False
    write: bool = True


class PersonaFreezePayload(BaseModel):
    confirm: bool = False


class PersonaSourcePayload(BaseModel):
    content: str = ""


def create_persona_importer_router(
    *,
    persona_root: str | Path = "config/persona",
    defaults_dir: str | Path = "config/persona/_defaults/v2",
    soul_dir: str | Path = "config/soul",
    ctx: Any = None,
    config: Any = None,
    bot: Any = None,
) -> APIRouter:
    router = APIRouter(prefix="/persona", tags=["persona-importer"])

    def _writer() -> PersonaDraftWriter:
        root = getattr(ctx, "persona_root", persona_root) if ctx is not None else persona_root
        defaults = getattr(ctx, "persona_defaults_dir", defaults_dir) if ctx is not None else defaults_dir
        return PersonaDraftWriter(persona_root=root, defaults_dir=defaults)

    def _resolve_persona_root() -> Path:
        return Path(getattr(ctx, "persona_root", persona_root) if ctx is not None else persona_root)

    def _resolve_defaults_dir() -> Path:
        return Path(getattr(ctx, "persona_defaults_dir", defaults_dir) if ctx is not None else defaults_dir)

    def _valid_namespace(persona_id: str) -> tuple[bool, str]:
        namespace = persona_namespace(persona_id)
        if not persona_id.strip():
            return False, namespace
        if "/" in namespace or "\\" in namespace or namespace in {".", ".."}:
            return False, namespace
        if ".." in Path(namespace).parts:
            return False, namespace
        return True, namespace

    @router.post("/import")
    async def import_persona(payload: PersonaImportPayload, request: Request):
        del request
        persona_id = payload.persona_id.strip()
        valid, namespace = _valid_namespace(persona_id)
        if not valid:
            return {"ok": False, "persona_id": namespace, "error": "persona_id is invalid"}
        writer = _writer()
        try:
            result = writer.import_source(
                persona_id,
                source_path=Path(payload.source_path) if payload.source_path else None,
                strict=payload.strict,
                write=payload.write,
            )
        except FileNotFoundError as exc:
            return {"ok": False, "persona_id": persona_namespace(persona_id), "error": str(exc)}
        return {
            "ok": not result.report.has_errors,
            "persona_id": result.persona_id,
            "draft_dir": (
                str(writer.draft_dir(result.persona_id))
                if payload.write and not (payload.strict and result.report.has_errors)
                else ""
            ),
            "report": result.report.to_dict(),
        }

    @router.get("/source/{persona_id}")
    async def get_source(persona_id: str):
        valid, namespace = _valid_namespace(persona_id)
        if not valid:
            return {"ok": False, "persona_id": namespace, "error": "persona_id is invalid"}
        path = _writer().source_path(namespace)
        exists = path.is_file()
        return {
            "ok": True,
            "persona_id": namespace,
            "path": str(path),
            "exists": exists,
            "content": path.read_text(encoding="utf-8") if exists else "",
        }

    @router.put("/source/{persona_id}")
    async def put_source(persona_id: str, payload: PersonaSourcePayload):
        valid, namespace = _valid_namespace(persona_id)
        if not valid:
            return {"ok": False, "persona_id": namespace, "error": "persona_id is invalid"}
        path = _writer().source_path(namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload.content, encoding="utf-8")
        return {
            "ok": True,
            "persona_id": namespace,
            "path": str(path),
            "exists": True,
            "bytes": len(payload.content.encode("utf-8")),
            "content": payload.content,
        }

    @router.get("/draft/{persona_id}")
    async def get_draft(persona_id: str):
        valid, namespace = _valid_namespace(persona_id)
        if not valid:
            return {"ok": False, "persona_id": namespace, "error": "persona_id is invalid"}
        return _writer().read_draft_summary(persona_id)

    @router.post("/freeze/{persona_id}")
    async def pending_freeze(persona_id: str, payload: PersonaFreezePayload):
        valid, namespace = _valid_namespace(persona_id)
        if not valid:
            return {"ok": False, "persona_id": namespace, "error": "persona_id is invalid"}
        if not payload.confirm:
            return {
                "ok": False,
                "persona_id": namespace,
                "error": "confirm=true is required for Pending Freeze",
            }
        return _writer().pending_freeze(persona_id)

    return router
