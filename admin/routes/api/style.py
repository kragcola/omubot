"""JSON API: style learning inspection and manual extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request

from services.slang import SlangStore
from services.style import StyleStore, run_style_manual_extract

_DEFAULT_BATCH_LIMIT = 120
_DEFAULT_MAX_BATCHES = 5
_DEFAULT_TARGET_TEXT_ROWS = 200
_MAX_MANUAL_BATCH_LIMIT = 500
_MAX_MANUAL_BATCHES = 12
_MAX_TARGET_TEXT_ROWS = 800


def create_style_router(
    *,
    ctx: Any = None,
    store: StyleStore | None = None,
    message_log: Any = None,
    llm_client: Any = None,
    slang_store: SlangStore | None = None,
) -> APIRouter:
    router = APIRouter()
    fallback_store: StyleStore | None = store

    async def _store() -> StyleStore:
        nonlocal fallback_store
        ctx_store = getattr(ctx, "style_store", None) if ctx is not None else None
        if ctx_store is not None:
            return ctx_store
        if fallback_store is None:
            storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx is not None else Path("storage")
            fallback_store = StyleStore(storage_dir / "style.db")
            await fallback_store.init()
            if ctx is not None:
                ctx.style_store = fallback_store
        elif not fallback_store.initialized:
            await fallback_store.init()
        return fallback_store

    def _message_log() -> Any:
        return message_log or (getattr(ctx, "msg_log", None) if ctx is not None else None)

    def _llm_client() -> Any:
        return llm_client or (getattr(ctx, "llm_client", None) if ctx is not None else None)

    def _slang_store() -> SlangStore | None:
        return slang_store or (getattr(ctx, "slang_store", None) if ctx is not None else None)

    async def _read_json(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    @router.get("/style/summary")
    async def summary() -> dict[str, Any]:
        style_store = await _store()
        return await style_store.summary()

    @router.get("/style/expressions")
    async def list_expressions(
        status: str = Query(""),
        scope: str = Query(""),
        group_id: str = Query(""),
        sort: str = Query("default"),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        style_store = await _store()
        expressions, total = await style_store.list_expressions(
            status=status or None,
            scope=scope or None,
            group_id=group_id or None,
            limit=limit,
            offset=offset,
            sort="time" if sort == "time" else "default",
        )
        return {
            "expressions": [style_store.expression_to_dict(item) for item in expressions],
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": "time" if sort == "time" else "default",
        }

    @router.get("/style/expressions/{expression_id}")
    async def get_expression(expression_id: str) -> dict[str, Any]:
        style_store = await _store()
        expression = await style_store.get_expression(expression_id)
        if expression is None:
            return {"ok": False, "error": "Style expression not found"}
        return {"ok": True, "expression": style_store.expression_to_dict(expression)}

    @router.get("/style/expressions/{expression_id}/evidence")
    async def list_evidence(
        expression_id: str,
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        style_store = await _store()
        evidence = await style_store.list_evidence(expression_id, limit=limit)
        return {"evidence": [style_store.evidence_to_dict(item) for item in evidence]}

    @router.get("/style/expressions/{expression_id}/revisions")
    async def list_revisions(
        expression_id: str,
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        style_store = await _store()
        revisions = await style_store.list_revisions(expression_id, limit=limit)
        return {"revisions": [style_store.revision_to_dict(item) for item in revisions]}

    @router.get("/style/feedback")
    async def list_feedback(
        target_type: str = Query(""),
        target_id: str = Query(""),
        group_id: str = Query(""),
        sort: str = Query("default"),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        style_store = await _store()
        feedback, total = await style_store.list_feedback(
            target_type=target_type or None,
            target_id=target_id or None,
            group_id=group_id or None,
            limit=limit,
            offset=offset,
            sort="time" if sort == "time" else "default",
        )
        return {
            "feedback": [style_store.feedback_to_dict(item) for item in feedback],
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": "time" if sort == "time" else "default",
        }

    @router.post("/style/expressions/{expression_id}/feedback")
    async def record_expression_feedback(expression_id: str, request: Request) -> dict[str, Any]:
        body = await _read_json(request)
        rating = str(body.get("rating") or "neutral").strip()
        reason = str(body.get("reason") or "").strip()
        actor = str(body.get("actor") or "admin").strip() or "admin"
        group_id = str(body.get("group_id") or "").strip()
        raw_text = str(body.get("raw_text") or "").strip()
        context = str(body.get("context") or "").strip()
        try:
            style_store = await _store()
            expression = await style_store.record_expression_feedback(
                expression_id,
                rating=rating,  # type: ignore[arg-type]
                actor=actor,
                reason=reason,
                group_id=group_id,
                raw_text=raw_text,
                context=context,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if expression is None:
            return {"ok": False, "error": "Style expression not found"}
        return {"ok": True, "expression": style_store.expression_to_dict(expression)}

    @router.post("/style/expressions/{expression_id}/status")
    async def set_expression_status(expression_id: str, request: Request) -> dict[str, Any]:
        body = await _read_json(request)
        status = str(body.get("status") or "").strip()
        actor = str(body.get("actor") or "admin").strip() or "admin"
        reason = str(body.get("reason") or "").strip()
        try:
            style_store = await _store()
            ok = await style_store.set_status(
                expression_id,
                status,  # type: ignore[arg-type]
                actor=actor,
                reason=reason,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not ok:
            return {"ok": False, "error": "Style expression not found"}
        expression = await style_store.get_expression(expression_id)
        return {"ok": True, "expression": style_store.expression_to_dict(expression)}

    @router.get("/style/profiles")
    async def list_profiles(
        scope: str = Query(""),
        group_id: str = Query(""),
        status: str = Query(""),
        sort: str = Query("default"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        style_store = await _store()
        try:
            profiles, total = await style_store.list_profiles(
                scope=scope or None,
                group_id=group_id or None,
                status=status or None,
                limit=limit,
                offset=offset,
                sort="time" if sort == "time" else "default",
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "profiles": [style_store.profile_to_dict(item) for item in profiles],
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": "time" if sort == "time" else "default",
        }

    @router.get("/style/profiles/current")
    async def current_profiles(
        group_id: str = Query(""),
        include_global: bool = Query(False),
    ) -> dict[str, Any]:
        if not group_id:
            return {"ok": False, "error": "group_id is required"}
        style_store = await _store()
        profiles = await style_store.get_enabled_profiles(group_id=group_id, include_global=include_global)
        block = await style_store.build_profile_prompt_block(group_id=group_id, include_global=include_global)
        return {
            "ok": True,
            "profiles": [style_store.profile_to_dict(item) for item in profiles],
            "prompt_block": block,
        }

    @router.post("/style/profiles/generate")
    async def generate_profile(request: Request) -> dict[str, Any]:
        body = await _read_json(request)
        scope = str(body.get("scope") or "group").strip()
        group_id = str(body.get("group_id") or "").strip()
        include_global = _bool_value(body.get("include_global", False))
        enable = _bool_value(body.get("enable", True))
        actor = str(body.get("actor") or "admin").strip() or "admin"
        reason = str(body.get("reason") or "").strip()
        try:
            max_items = max(1, min(int(body.get("max_items") or 12), 40))
        except (TypeError, ValueError):
            max_items = 12
        try:
            style_store = await _store()
            profile = await style_store.generate_profile(
                scope=scope,  # type: ignore[arg-type]
                group_id=group_id,
                include_global=include_global,
                max_items=max_items,
                enable=enable,
                actor=actor,
                reason=reason,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "profile": style_store.profile_to_dict(profile)}

    @router.post("/style/profiles/rollback")
    async def rollback_profile(request: Request) -> dict[str, Any]:
        body = await _read_json(request)
        scope = str(body.get("scope") or "group").strip()
        group_id = str(body.get("group_id") or "").strip()
        actor = str(body.get("actor") or "admin").strip() or "admin"
        reason = str(body.get("reason") or "rollback").strip()
        try:
            style_store = await _store()
            profile = await style_store.rollback_profile(
                scope=scope,  # type: ignore[arg-type]
                group_id=group_id,
                actor=actor,
                reason=reason,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if profile is None:
            return {"ok": False, "error": "No previous style profile available"}
        return {"ok": True, "profile": style_store.profile_to_dict(profile)}

    @router.post("/style/profiles/{profile_id}/enable")
    async def enable_profile(profile_id: str, request: Request) -> dict[str, Any]:
        body = await _read_json(request)
        style_store = await _store()
        profile = await style_store.set_profile_status(
            profile_id,
            "enabled",
            actor=str(body.get("actor") or "admin"),
            reason=str(body.get("reason") or "enable profile"),
        )
        if profile is None:
            return {"ok": False, "error": "Style profile not found"}
        return {"ok": True, "profile": style_store.profile_to_dict(profile)}

    @router.post("/style/profiles/{profile_id}/disable")
    async def disable_profile(profile_id: str, request: Request) -> dict[str, Any]:
        body = await _read_json(request)
        style_store = await _store()
        profile = await style_store.set_profile_status(
            profile_id,
            "disabled",
            actor=str(body.get("actor") or "admin"),
            reason=str(body.get("reason") or "disable profile"),
        )
        if profile is None:
            return {"ok": False, "error": "Style profile not found"}
        return {"ok": True, "profile": style_store.profile_to_dict(profile)}

    @router.post("/style/extract/run")
    async def run_extract(request: Request) -> dict[str, Any]:
        body = await _read_json(request)
        group_id = str(body.get("group_id") or "").strip()
        scope = str(body.get("scope") or "group").strip()
        if scope not in {"group", "global"}:
            return {"ok": False, "error": "scope must be group or global"}
        try:
            limit = max(1, min(int(body.get("limit") or _DEFAULT_BATCH_LIMIT), _MAX_MANUAL_BATCH_LIMIT))
        except (TypeError, ValueError):
            limit = _DEFAULT_BATCH_LIMIT
        try:
            max_batches = max(1, min(int(body.get("max_batches") or _DEFAULT_MAX_BATCHES), _MAX_MANUAL_BATCHES))
        except (TypeError, ValueError):
            max_batches = _DEFAULT_MAX_BATCHES
        try:
            target_text_rows = max(
                1,
                min(int(body.get("target_text_rows") or _DEFAULT_TARGET_TEXT_ROWS), _MAX_TARGET_TEXT_ROWS),
            )
        except (TypeError, ValueError):
            target_text_rows = _DEFAULT_TARGET_TEXT_ROWS
        auto_approve = _bool_value(body.get("auto_approve", False))

        return await run_style_manual_extract(
            style_store=await _store(),
            message_log=_message_log(),
            llm_client=_llm_client(),
            slang_store=_slang_store(),
            group_id=group_id,
            scope=scope,
            limit=limit,
            max_batches=max_batches,
            target_text_rows=target_text_rows,
            auto_approve=auto_approve,
        )

    return router


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "是", "开启"}
