"""JSON API: style learning inspection and manual extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request

from services.conversation_archive import add_evidence_message_ref, finish_scan_batch, read_scan_batch
from services.slang import SlangStore, SlangTerm, normalize_term
from services.style import (
    StyleExtraction,
    StyleExtractor,
    StyleStore,
    format_style_messages,
    select_style_source_row,
)

_AUTO_APPROVE_MIN_CONFIDENCE = 0.86
_AUTO_APPROVE_MIN_PERSONA_FIT = 0.72
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

        log = _message_log()
        if log is None:
            return {"ok": False, "error": "MessageLog not available"}
        llm = _llm_client()
        if llm is None:
            return {"ok": False, "error": "LLMClient not available"}

        if group_id:
            groups = [group_id]
        elif hasattr(log, "list_group_ids"):
            groups = [str(item) for item in await log.list_group_ids() if str(item).strip()]
        else:
            return {"ok": False, "error": "group_id is required when MessageLog cannot list groups"}

        style_store = await _store()
        extractor = StyleExtractor(llm)
        scanned = 0
        raw_scanned = 0
        backlog_raw = 0
        backlog_text = 0
        extracted_count = 0
        saved = 0
        approved = 0
        pending = 0
        filtered = 0
        expression_ids: list[str] = []
        per_group: list[dict[str, Any]] = []
        slang = _slang_store()

        try:
            for gid in groups:
                group_result: dict[str, Any] = {
                    "group_id": str(gid),
                    "scanned": 0,
                    "text_scanned": 0,
                    "raw_scanned": 0,
                    "batches": 0,
                    "backlog_raw": 0,
                    "backlog_text": 0,
                    "has_more": False,
                    "extracted": 0,
                    "filtered": 0,
                    "saved": 0,
                    "approved": 0,
                    "pending": 0,
                    "expression_ids": [],
                }
                per_group.append(group_result)
                slang_keys = await _load_style_slang_keys(slang, str(gid))
                for batch_index in range(max_batches):
                    batch = await read_scan_batch(
                        log,
                        scanner_name="style_manual_extract",
                        group_id=str(gid),
                        limit=limit,
                        scanner_version="v1",
                        params_hash="",
                        meta={
                            "admin_manual": True,
                            "scope": scope,
                            "batch_index": batch_index + 1,
                            "max_batches": max_batches,
                            "target_text_rows": target_text_rows,
                        },
                    )
                    if batch_index == 0:
                        group_result["scan_source"] = batch.get("source")
                        group_result["from_message_pk"] = batch.get("from_message_pk")
                    group_result["to_message_pk"] = batch.get("to_message_pk")
                    try:
                        rows = list(batch.get("rows") or [])
                        batch_raw_count = len(rows)
                        user_rows = [
                            row
                            for row in rows
                            if row.get("role") == "user" and str(row.get("content_text") or "").strip()
                        ]
                        batch_text_count = len(user_rows)
                        batch_filtered_count = 0
                        batch_saved_count = 0
                        raw_scanned += batch_raw_count
                        scanned += batch_text_count
                        group_result["raw_scanned"] += batch_raw_count
                        group_result["text_scanned"] += batch_text_count
                        group_result["scanned"] = group_result["text_scanned"]
                        group_result["batches"] += 1

                        extractions = await extractor.extract(user_rows)
                        extracted_count += len(extractions)
                        group_result["extracted"] += len(extractions)
                        for item in extractions:
                            if _style_extraction_mentions_slang(item, slang_keys):
                                filtered += 1
                                group_result["filtered"] += 1
                                batch_filtered_count += 1
                                continue
                            status = _status_for_extraction(item, auto_approve=auto_approve)
                            source = select_style_source_row(item.evidence, user_rows)
                            expression = await style_store.upsert_expression(
                                item.to_new_expression(group_id=str(gid), scope=scope, status=status),
                                evidence={
                                    "group_id": str(gid),
                                    "speaker": str(source.get("speaker") or ""),
                                    "raw_text": str(source.get("content_text") or item.evidence),
                                    "context": format_style_messages(user_rows[-12:]),
                                    "source_type": "human",
                                    "message_id": _message_id(source.get("message_id")),
                                },
                                actor="admin_manual_extract",
                                reason=item.reason or "manual style extraction",
                            )
                            if status == "approved" and expression.status != "approved":
                                await style_store.set_status(
                                    expression.expression_id,
                                    "approved",
                                    actor="admin_manual_extract",
                                    reason="high-confidence manual style extraction",
                                )
                                refreshed = await style_store.get_expression(expression.expression_id)
                                expression = refreshed or expression
                            saved += 1
                            expression_ids.append(expression.expression_id)
                            await add_evidence_message_ref(
                                log,
                                group_id=str(gid),
                                source_row=source,
                                ref_owner="style",
                                external_table="style_expressions",
                                external_id=expression.expression_id,
                                snapshot_text=str(source.get("content_text") or item.evidence),
                                meta={"source": "style_manual_extract"},
                            )
                            if expression.status == "approved":
                                approved += 1
                                group_result["approved"] += 1
                            else:
                                pending += 1
                                group_result["pending"] += 1
                            group_result["saved"] += 1
                            batch_saved_count += 1
                            group_result["expression_ids"].append(expression.expression_id)
                        await finish_scan_batch(
                            log,
                            batch,
                            status="success",
                            scanned_count=batch_text_count,
                            extracted_count=len(extractions),
                            filtered_count=batch_filtered_count,
                            saved_count=batch_saved_count,
                            meta={
                                "expression_ids": group_result["expression_ids"],
                                "batch_text_scanned": batch_text_count,
                                "batch_raw_scanned": batch_raw_count,
                            },
                        )
                    except Exception as exc:
                        await finish_scan_batch(
                            log,
                            batch,
                            status="failed",
                            scanned_count=group_result["text_scanned"],
                            extracted_count=group_result["extracted"],
                            filtered_count=group_result["filtered"],
                            saved_count=group_result["saved"],
                            error=str(exc),
                            advance_cursor=False,
                        )
                        raise
                    if (
                        batch.get("source") != "archive"
                        or not batch.get("can_advance")
                        or batch_raw_count < limit
                        or group_result["text_scanned"] >= target_text_rows
                    ):
                        break
                backlog = await _style_scan_backlog(log, str(gid), scanner_name="style_manual_extract")
                group_result["backlog_raw"] = backlog["raw"]
                group_result["backlog_text"] = backlog["text"]
                group_result["has_more"] = backlog["raw"] > 0
                backlog_raw += backlog["raw"]
                backlog_text += backlog["text"]
        except Exception as exc:
            if per_group:
                per_group[-1]["error"] = str(exc)
            return {
                "ok": False,
                "error": str(exc),
                "groups": groups,
                "scope": scope,
                "scanned": scanned,
                "text_scanned": scanned,
                "raw_scanned": raw_scanned,
                "backlog_raw": backlog_raw,
                "backlog_text": backlog_text,
                "has_more": backlog_raw > 0,
                "batch_limit": limit,
                "max_batches": max_batches,
                "target_text_rows": target_text_rows,
                "extracted": extracted_count,
                "filtered": filtered,
                "saved": saved,
                "approved": approved,
                "pending": pending,
                "expression_ids": expression_ids,
                "per_group": per_group,
            }

        return {
            "ok": True,
            "groups": groups,
            "scope": scope,
            "scanned": scanned,
            "text_scanned": scanned,
            "raw_scanned": raw_scanned,
            "backlog_raw": backlog_raw,
            "backlog_text": backlog_text,
            "has_more": backlog_raw > 0,
            "batch_limit": limit,
            "max_batches": max_batches,
            "target_text_rows": target_text_rows,
            "extracted": extracted_count,
            "filtered": filtered,
            "saved": saved,
            "approved": approved,
            "pending": pending,
            "expression_ids": expression_ids,
            "per_group": per_group,
        }

    return router


async def _load_style_slang_keys(slang_store: SlangStore | None, group_id: str) -> set[str]:
    if slang_store is None:
        return set()
    terms: list[SlangTerm] = []
    for kwargs in ({"group_id": group_id}, {"scope": "global"}):
        try:
            chunk, _total = await slang_store.list_terms(**kwargs, limit=1000)
        except Exception:
            continue
        terms.extend(chunk)
    keys: set[str] = set()
    for term in terms:
        if term.status not in {"candidate", "approved"}:
            continue
        for value in [term.term, *term.aliases]:
            key = normalize_term(value)
            if _is_style_slang_key(key):
                keys.add(key)
    return keys


async def _style_scan_backlog(message_log: Any, group_id: str, *, scanner_name: str) -> dict[str, int]:
    get_cursor = getattr(message_log, "get_cursor", None)
    count_after = getattr(message_log, "count_messages_after_pk", None)
    if not callable(get_cursor) or not callable(count_after):
        return {"raw": 0, "text": 0}
    try:
        cursor = await get_cursor(
            scanner_name=scanner_name,
            chat_type="group",
            chat_id=str(group_id),
            scope_key="chat",
        )
        after_pk = int(cursor["last_message_pk"] or 0) if cursor else 0
        counts = await count_after(chat_type="group", chat_id=str(group_id), after_message_pk=after_pk)
    except Exception:
        return {"raw": 0, "text": 0}
    return {"raw": int(counts.get("raw") or 0), "text": int(counts.get("text") or 0)}


def _style_extraction_mentions_slang(item: StyleExtraction, slang_keys: set[str]) -> bool:
    if not slang_keys:
        return False
    candidate_key = normalize_term(f"{item.situation} {item.style}")
    if not candidate_key:
        return False
    return any(key in candidate_key for key in slang_keys)


def _is_style_slang_key(key: str) -> bool:
    if len(key) >= 3:
        return True
    return len(key) >= 2 and any(ord(char) > 127 for char in key)


def _status_for_extraction(item: StyleExtraction, *, auto_approve: bool) -> str:
    if (
        auto_approve
        and item.confidence >= _AUTO_APPROVE_MIN_CONFIDENCE
        and item.persona_fit >= _AUTO_APPROVE_MIN_PERSONA_FIT
        and item.output_policy != "observe_only"
    ):
        return "approved"
    return "pending"


def _message_id(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "是", "开启"}
