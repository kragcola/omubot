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


def create_persona_importer_router(
    *,
    persona_root: str | Path = "config/persona",
    defaults_dir: str | Path = "config/persona/_defaults/v2",
    ctx: Any = None,
) -> APIRouter:
    router = APIRouter(prefix="/persona", tags=["persona-importer"])

    def _writer() -> PersonaDraftWriter:
        root = getattr(ctx, "persona_root", persona_root) if ctx is not None else persona_root
        defaults = getattr(ctx, "persona_defaults_dir", defaults_dir) if ctx is not None else defaults_dir
        return PersonaDraftWriter(persona_root=root, defaults_dir=defaults)

    @router.post("/import")
    async def import_persona(payload: PersonaImportPayload, request: Request):
        del request
        persona_id = payload.persona_id.strip()
        if not persona_id:
            return {"ok": False, "error": "persona_id is required"}
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

    @router.get("/draft/{persona_id}")
    async def get_draft(persona_id: str):
        return _writer().read_draft_summary(persona_id)

    @router.post("/freeze/{persona_id}")
    async def pending_freeze(persona_id: str, payload: PersonaFreezePayload):
        if not payload.confirm:
            return {
                "ok": False,
                "persona_id": persona_namespace(persona_id),
                "error": "confirm=true is required for Pending Freeze",
            }
        return _writer().pending_freeze(persona_id)

    return router
