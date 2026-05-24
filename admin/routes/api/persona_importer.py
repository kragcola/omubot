"""JSON API: Persona Source Importer Part A."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from services.identity import Identity
from services.persona import (
    GroupOverrideSnapshot,
    compare_v1_vs_v2_dry_run,
    compile_persona_dry_run,
)
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
    identity_mgr: Any = None,
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

    def _resolve_soul_dir() -> Path:
        return Path(getattr(ctx, "soul_dir", soul_dir) if ctx is not None else soul_dir)

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

    @router.get("/parity/{persona_id}")
    async def parity_audit(persona_id: str):
        valid, namespace = _valid_namespace(persona_id)
        if not valid:
            return {"ok": False, "persona_id": namespace, "error": "persona_id is invalid"}

        compile_result = compile_persona_dry_run(
            persona_id,
            persona_root=_resolve_persona_root(),
            defaults_dir=_resolve_defaults_dir(),
        )
        if not compile_result.ok:
            return {
                "ok": False,
                "persona_id": namespace,
                "error": (compile_result.errors[0] if compile_result.errors else "compile failed"),
                "compile": compile_result.to_dict(),
            }

        identity = _resolve_identity()
        bot_self_id = _resolve_bot_self_id()
        instruction_text = _read_instruction_text()
        admins = _resolve_admins()
        group_pair = _resolve_group_override_snapshot()
        group_override = group_pair[0] if group_pair else None
        group_override_id = group_pair[1] if group_pair else None

        report = compare_v1_vs_v2_dry_run(
            identity=identity,
            bot_self_id=bot_self_id,
            instruction_text=instruction_text,
            admins=admins,
            proactive=identity.proactive,
            group_override=group_override,
            compile_result=compile_result,
        )
        return {
            "ok": True,
            "persona_id": namespace,
            "as_of": compile_result.mode,
            "compile": {
                "module_order": list(compile_result.module_order),
                "warnings": list(compile_result.warnings),
            },
            "v1_signals": {
                "bot_self_id": bot_self_id,
                "instruction_present": bool(instruction_text.strip()),
                "admins_count": len(admins),
                "proactive_present": bool((identity.proactive or "").strip()),
                "group_override_group_id": group_override_id,
            },
            "report": report.to_dict(),
        }

    def _resolve_identity() -> Identity:
        manager = identity_mgr or (getattr(ctx, "identity_mgr", None) if ctx is not None else None)
        if manager is not None and hasattr(manager, "resolve"):
            try:
                resolved = manager.resolve()
                if isinstance(resolved, Identity):
                    return resolved
            except Exception:
                pass
        return Identity(id="default", name="未加载", personality="", proactive=None)

    def _resolve_bot_self_id() -> str:
        candidate = bot if bot is not None else (getattr(ctx, "bot", None) if ctx is not None else None)
        self_id = getattr(candidate, "self_id", "") if candidate is not None else ""
        return str(self_id or "").strip()

    def _resolve_admins() -> dict[str, str]:
        cfg = config if config is not None else (getattr(ctx, "config", None) if ctx is not None else None)
        raw = getattr(cfg, "admins", {}) if cfg is not None else {}
        if not isinstance(raw, dict):
            return {}
        return {str(qq): str(label) for qq, label in raw.items() if str(qq).strip()}

    def _read_instruction_text() -> str:
        path = _resolve_soul_dir() / "instruction.md"
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _resolve_group_override_snapshot() -> tuple[GroupOverrideSnapshot, str] | None:
        cfg = config if config is not None else (getattr(ctx, "config", None) if ctx is not None else None)
        group_cfg = getattr(cfg, "group", None) if cfg is not None else None
        overrides = getattr(group_cfg, "overrides", None) if group_cfg is not None else None
        if not isinstance(overrides, dict) or not overrides:
            return None
        for group_id, override in overrides.items():
            snapshot = _build_group_override_snapshot(override)
            if snapshot is not None:
                return snapshot, str(group_id)
        return None

    return router


def _build_group_override_snapshot(override: Any) -> GroupOverrideSnapshot | None:
    if override is None:
        return None
    fields: dict[str, Any] = {}
    for name in (
        "reply_style",
        "custom_prompt",
        "presence_mode",
        "at_only",
        "talk_value",
        "planner_smooth",
        "debounce_seconds",
        "batch_size",
        "history_load_count",
        "tools_enabled",
        "allowed_tools",
        "blocked_tools",
        "sticker_mode",
        "slang_enabled",
        "blocked_users",
    ):
        value = getattr(override, name, None)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        fields[name] = value
    if not fields:
        return None
    return GroupOverrideSnapshot(**fields)
