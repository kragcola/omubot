"""JSON API: slang — group slang review, settings, and manual extraction."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from services.slang import (
    SlangDatabaseCorruptError,
    SlangExtractor,
    SlangSettings,
    SlangStore,
    normalize_term,
)
from services.slang.types import (
    SlangDriftReview,
    SlangExtractionRun,
    SlangObservation,
    SlangPendingCandidate,
    SlangTerm,
    SlangTermRevision,
)


def _term_to_dict(term: SlangTerm) -> dict[str, Any]:
    return {
        "term_id": term.term_id,
        "term": term.term,
        "meaning": term.meaning,
        "aliases": term.aliases,
        "scope": term.scope,
        "group_id": term.group_id,
        "confidence": term.confidence,
        "status": term.status,
        "usage_count": term.usage_count,
        "unique_user_count": term.unique_user_count,
        "unique_users": term.unique_users,
        "first_seen_at": term.first_seen_at,
        "last_seen_at": term.last_seen_at,
        "last_inferred_at": term.last_inferred_at,
        "source": term.source,
        "repeat_policy": term.repeat_policy,
        "notes": term.notes,
        "meta": term.meta,
        "created_at": term.created_at,
        "updated_at": term.updated_at,
    }


def _observation_to_dict(observation: SlangObservation) -> dict[str, Any]:
    return {
        "observation_id": observation.observation_id,
        "term_id": observation.term_id,
        "group_id": observation.group_id,
        "user_id": observation.user_id,
        "message_id": observation.message_id,
        "raw_text": observation.raw_text,
        "context": observation.context,
        "observed_at": observation.observed_at,
        "reason": observation.reason,
    }


def _pending_to_dict(candidate: SlangPendingCandidate) -> dict[str, Any]:
    return {
        "pending_id": candidate.pending_id,
        "term": candidate.term,
        "meaning": candidate.meaning,
        "aliases": candidate.aliases,
        "group_id": candidate.group_id,
        "confidence": candidate.confidence,
        "count": candidate.count,
        "unique_user_count": len(candidate.unique_users),
        "unique_users": candidate.unique_users,
        "evidence": candidate.evidence,
        "reason": candidate.reason,
        "repeat_policy": candidate.repeat_policy,
        "first_seen_at": candidate.first_seen_at,
        "last_seen_at": candidate.last_seen_at,
        "meta": candidate.meta,
    }


def _run_to_dict(run: SlangExtractionRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "status": run.status,
        "group_count": run.group_count,
        "scanned_messages": run.scanned_messages,
        "extracted_terms": run.extracted_terms,
        "promoted_candidates": run.promoted_candidates,
        "error": run.error,
        "duration_ms": run.duration_ms,
        "meta": run.meta,
    }


def _revision_to_dict(revision: SlangTermRevision) -> dict[str, Any]:
    return {
        "revision_id": revision.revision_id,
        "term_id": revision.term_id,
        "action": revision.action,
        "actor": revision.actor,
        "before": revision.before,
        "after": revision.after,
        "reason": revision.reason,
        "created_at": revision.created_at,
        "meta": revision.meta,
    }


def _drift_to_dict(drift: SlangDriftReview) -> dict[str, Any]:
    return {
        "drift_id": drift.drift_id,
        "term_id": drift.term_id,
        "term": drift.term,
        "group_id": drift.group_id,
        "old_meaning": drift.old_meaning,
        "new_meaning": drift.new_meaning,
        "aliases": drift.aliases,
        "evidence": drift.evidence,
        "confidence": drift.confidence,
        "reason": drift.reason,
        "status": drift.status,
        "created_at": drift.created_at,
        "updated_at": drift.updated_at,
        "meta": drift.meta,
    }


def _speaker_to_user_id(speaker: str | None) -> str:
    if not speaker:
        return ""
    tail = str(speaker).rsplit("(", 1)
    if len(tail) == 2 and tail[1].endswith(")"):
        value = tail[1][:-1]
        return value if value.isdigit() else ""
    return ""


def _estimate_occurrences(term: str, aliases: list[str], rows: list[dict[str, Any]]) -> int:
    keys = {normalize_term(term), *(normalize_term(alias) for alias in aliases)}
    keys = {key for key in keys if len(key) >= 2}
    if not keys:
        return 1
    count = 0
    for row in rows:
        text_key = normalize_term(str(row.get("content_text") or ""))
        if any(key in text_key for key in keys):
            count += 1
    return max(1, count)


def create_slang_router(
    *,
    ctx: Any = None,
    bus: Any = None,
    message_log: Any = None,
    llm_client: Any = None,
    store: SlangStore | None = None,
) -> APIRouter:
    class _SlangCorruptGuardRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Any]]:
            original = super().get_route_handler()

            async def handler(request: Request) -> Any:
                try:
                    return await original(request)
                except SlangDatabaseCorruptError as exc:
                    return JSONResponse(
                        status_code=503,
                        content={
                            "ok": False,
                            "error_code": "slang_db_corrupt",
                            "message": "黑话数据库损坏，请运行恢复脚本后重启 bot。",
                            "db_path": exc.db_path,
                            "repair_script": "scripts/dev/slang_db_repair.py",
                        },
                    )

            return handler

    router = APIRouter(route_class=_SlangCorruptGuardRoute)
    fallback_store: SlangStore | None = store

    def _plugin() -> Any:
        if bus is not None and hasattr(bus, "get_plugin"):
            return bus.get_plugin("slang")
        return getattr(ctx, "slang_plugin", None) if ctx is not None else None

    async def _store() -> SlangStore:
        nonlocal fallback_store
        plugin = _plugin()
        plugin_store = getattr(plugin, "store", None)
        if plugin_store is not None:
            return plugin_store
        ctx_store = getattr(ctx, "slang_store", None) if ctx is not None else None
        if ctx_store is not None:
            return ctx_store
        if fallback_store is None:
            storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx is not None else Path("storage")
            candidate = SlangStore(storage_dir / "slang.db")
            await candidate.init()
            fallback_store = candidate
            if ctx is not None:
                ctx.slang_store = fallback_store
        elif not fallback_store.initialized:
            await fallback_store.init()
        return fallback_store

    def _message_log() -> Any:
        return message_log or (getattr(ctx, "msg_log", None) if ctx is not None else None)

    def _llm_client() -> Any:
        return llm_client or (getattr(ctx, "llm_client", None) if ctx is not None else None)

    async def _read_json(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    @router.get("/slang/summary")
    async def get_summary():
        slang_store = await _store()
        return await slang_store.summary()

    @router.get("/slang/groups")
    async def list_groups():
        slang_store = await _store()
        groups = set(await slang_store.list_groups())
        log = _message_log()
        if log is not None and hasattr(log, "list_group_ids"):
            with contextlib.suppress(Exception):
                groups.update(str(group_id) for group_id in await log.list_group_ids())
        return {"groups": sorted(groups)}

    @router.get("/slang/terms")
    async def list_terms(
        group_id: str = Query(""),
        scope: str = Query(""),
        status: str = Query(""),
        search: str = Query(""),
        min_confidence: float | None = Query(None, ge=0, le=1),
        review_filter: str = Query(""),
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=200),
    ):
        slang_store = await _store()
        terms, total = await slang_store.list_terms(
            group_id=group_id,
            scope=scope,
            status=status,
            search=search,
            min_confidence=min_confidence,
            review_filter=review_filter,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return {
            "terms": [_term_to_dict(term) for term in terms],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    @router.post("/slang/terms/bulk")
    async def bulk_terms(request: Request):
        slang_store = await _store()
        body = await _read_json(request)
        action = str(body.get("action") or "").strip()
        term_ids = [str(item) for item in body.get("term_ids", []) if str(item).strip()]
        if not term_ids:
            return {"ok": False, "error": "term_ids is required"}
        if action in {"approve", "mute", "expire"}:
            status = {"approve": "approved", "mute": "muted", "expire": "expired"}[action]
            result = await slang_store.bulk_set_status(term_ids, status)  # type: ignore[arg-type]
            return {"ok": True, **result}
        if action == "delete_observations":
            deleted = await slang_store.delete_observations_for_terms(term_ids)
            return {"ok": True, "requested": len(term_ids), "deleted": deleted}
        return {"ok": False, "error": "Unsupported bulk action"}

    @router.post("/slang/terms/merge")
    async def merge_terms(request: Request):
        slang_store = await _store()
        body = await _read_json(request)
        target_id = str(body.get("target_id") or "").strip()
        source_ids = [str(item) for item in body.get("source_ids", []) if str(item).strip()]
        if not target_id or not source_ids:
            return {"ok": False, "error": "target_id and source_ids are required"}
        term = await slang_store.merge_terms(target_id=target_id, source_ids=source_ids)
        if term is None:
            return {"ok": False, "error": "Target term not found"}
        return {"ok": True, "term": _term_to_dict(term)}

    @router.post("/slang/terms/create")
    async def create_term(request: Request):
        slang_store = await _store()
        body = await _read_json(request)
        aliases = body.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [part.strip() for part in aliases.replace("，", ",").split(",") if part.strip()]
        if not isinstance(aliases, list):
            aliases = []
        try:
            term = await slang_store.create_term(
                term=str(body.get("term") or ""),
                meaning=str(body.get("meaning") or ""),
                aliases=[str(alias).strip() for alias in aliases if str(alias).strip()],
                scope=str(body.get("scope") or "group"),  # type: ignore[arg-type]
                group_id=str(body.get("group_id") or ""),
                confidence=float(body.get("confidence") or 0.8),
                status=str(body.get("status") or "approved"),  # type: ignore[arg-type]
                repeat_policy=str(body.get("repeat_policy") or "understand_only"),  # type: ignore[arg-type]
                notes=str(body.get("notes") or ""),
                evidence=str(body.get("evidence") or ""),
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "term": _term_to_dict(term)}

    @router.post("/slang/terms/{term_id}/recompute-confidence")
    async def recompute_confidence(term_id: str):
        slang_store = await _store()
        term = await slang_store.recompute_confidence(term_id)
        if term is None:
            return {"ok": False, "error": "Slang term not found"}
        return {"ok": True, "term": _term_to_dict(term)}

    @router.get("/slang/terms/{term_id}/revisions")
    async def list_term_revisions(term_id: str, limit: int = Query(50, ge=1, le=200)):
        slang_store = await _store()
        revisions = await slang_store.list_revisions(term_id, limit=limit)
        return {"revisions": [_revision_to_dict(item) for item in revisions]}

    @router.get("/slang/drift")
    async def list_drift_reviews(
        status: str = Query("open"),
        group_id: str = Query(""),
        search: str = Query(""),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        slang_store = await _store()
        reviews, total = await slang_store.list_drift_reviews(
            status=status,
            group_id=group_id,
            search=search,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return {
            "reviews": [_drift_to_dict(item) for item in reviews],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    @router.post("/slang/drift/{drift_id}/accept")
    async def accept_drift(drift_id: str):
        slang_store = await _store()
        term = await slang_store.accept_drift_review(drift_id)
        if term is None:
            return {"ok": False, "error": "Drift review not found"}
        return {"ok": True, "term": _term_to_dict(term)}

    @router.post("/slang/drift/{drift_id}/reject")
    async def reject_drift(drift_id: str):
        slang_store = await _store()
        ok = await slang_store.reject_drift_review(drift_id)
        return {"ok": ok, "error": "" if ok else "Drift review not found"}

    @router.post("/slang/drift/{drift_id}/alias")
    async def alias_drift(drift_id: str):
        slang_store = await _store()
        term = await slang_store.alias_drift_review(drift_id)
        if term is None:
            return {"ok": False, "error": "Drift review not found"}
        return {"ok": True, "term": _term_to_dict(term)}

    @router.post("/slang/drift/{drift_id}/mute")
    async def mute_drift(drift_id: str):
        slang_store = await _store()
        term = await slang_store.mute_drift_review(drift_id)
        if term is None:
            return {"ok": False, "error": "Drift review not found"}
        return {"ok": True, "term": _term_to_dict(term)}

    @router.get("/slang/terms/{term_id}")
    async def get_term(term_id: str):
        slang_store = await _store()
        term = await slang_store.get_term(term_id)
        if term is None:
            return {"error": "Slang term not found"}
        observations = await slang_store.list_observations(term_id, limit=40)
        return {
            "term": _term_to_dict(term),
            "observations": [_observation_to_dict(item) for item in observations],
        }

    @router.post("/slang/terms/{term_id}")
    async def update_term(term_id: str, request: Request):
        slang_store = await _store()
        body = await _read_json(request)
        allowed = {
            "term", "meaning", "aliases", "scope", "group_id", "confidence",
            "status", "repeat_policy", "notes", "meta",
        }
        fields = {key: value for key, value in body.items() if key in allowed}
        if not fields:
            return {"ok": False, "error": "No valid fields"}
        try:
            ok = await slang_store.update_term(term_id, **fields)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        term = await slang_store.get_term(term_id)
        return {"ok": ok, "term": _term_to_dict(term) if term else None}

    @router.post("/slang/terms/{term_id}/approve")
    async def approve_term(term_id: str):
        slang_store = await _store()
        ok = await slang_store.set_status(term_id, "approved")
        term = await slang_store.get_term(term_id)
        return {"ok": ok, "term": _term_to_dict(term) if term else None}

    @router.post("/slang/terms/{term_id}/human-approve")
    async def human_approve_term(term_id: str):
        slang_store = await _store()
        term = await slang_store.mark_human_reviewed(term_id)
        if term is None:
            return {"ok": False, "error": "Slang term not found"}
        return {"ok": True, "term": _term_to_dict(term)}

    @router.post("/slang/terms/{term_id}/deny")
    async def deny_term(term_id: str):
        slang_store = await _store()
        term = await slang_store.deny_ai_reviewed_term(term_id)
        if term is None:
            return {"ok": False, "error": "Slang term not found"}
        return {"ok": True, "term": _term_to_dict(term)}

    @router.post("/slang/terms/{term_id}/return-candidate")
    async def return_candidate_term(term_id: str):
        slang_store = await _store()
        term = await slang_store.return_ai_reviewed_term_to_candidate(term_id)
        if term is None:
            return {"ok": False, "error": "Slang term not found"}
        return {"ok": True, "term": _term_to_dict(term)}

    @router.post("/slang/terms/{term_id}/mute")
    async def mute_term(term_id: str):
        slang_store = await _store()
        ok = await slang_store.set_status(term_id, "muted")
        term = await slang_store.get_term(term_id)
        return {"ok": ok, "term": _term_to_dict(term) if term else None}

    @router.post("/slang/terms/{term_id}/expire")
    async def expire_term(term_id: str):
        slang_store = await _store()
        ok = await slang_store.set_status(term_id, "expired")
        term = await slang_store.get_term(term_id)
        return {"ok": ok, "term": _term_to_dict(term) if term else None}

    @router.get("/slang/pending")
    async def list_pending(
        group_id: str = Query(""),
        search: str = Query(""),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        slang_store = await _store()
        pending, total = await slang_store.list_pending(
            group_id=group_id,
            search=search,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return {
            "pending": [_pending_to_dict(item) for item in pending],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    @router.get("/slang/extract/runs")
    async def list_extract_runs(limit: int = Query(10, ge=1, le=50)):
        slang_store = await _store()
        runs = await slang_store.list_extraction_runs(limit=limit)
        return {"runs": [_run_to_dict(run) for run in runs]}

    @router.post("/slang/global/scan")
    async def scan_global_candidates(request: Request):
        slang_store = await _store()
        body = await _read_json(request)
        settings = await slang_store.load_settings()
        min_groups = int(body.get("min_groups") or settings.global_promote_min_groups)
        result = await slang_store.scan_global_candidates(min_groups=min_groups)
        return result

    @router.get("/slang/stats")
    async def get_stats(days: int | None = Query(None, ge=1, le=120)):
        slang_store = await _store()
        settings = await slang_store.load_settings()
        return await slang_store.stats(days=days or settings.stats_days)

    @router.get("/slang/settings")
    async def get_settings():
        slang_store = await _store()
        return {"settings": (await slang_store.load_settings()).model_dump()}

    @router.post("/slang/settings")
    async def save_settings(request: Request):
        slang_store = await _store()
        body = await _read_json(request)
        payload = body.get("settings", body)
        try:
            current = (await slang_store.load_settings()).model_dump()
            current.update(payload)
            settings = await slang_store.save_settings(SlangSettings.model_validate(current))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "settings": settings.model_dump()}

    @router.post("/slang/extract/run")
    async def run_extract(request: Request):
        body = await _read_json(request)
        group_id = str(body.get("group_id") or "").strip() or None
        limit = int(body.get("limit") or 80)
        plugin = _plugin()
        if plugin is not None and hasattr(plugin, "run_manual_extract"):
            return await plugin.run_manual_extract(group_id=group_id, limit=limit)

        slang_store = await _store()
        log = _message_log()
        if log is None:
            return {"ok": False, "error": "MessageLog not available"}
        llm = _llm_client()
        if llm is None:
            return {"ok": False, "error": "LLMClient not available"}
        extractor = SlangExtractor(llm)
        settings = await slang_store.load_settings()
        groups = [group_id] if group_id else await log.list_group_ids()
        groups = [str(gid) for gid in groups if gid and settings.allows_group(str(gid))]
        run_id = await slang_store.start_extraction_run(
            group_count=len(groups),
            meta={"manual": True, "fallback": True},
        )
        promoted = 0
        extracted = 0
        scanned = 0
        try:
            for gid in groups:
                rows = await log.query_recent(str(gid), limit=limit)
                user_rows = [row for row in rows if row.get("role") == "user" and row.get("content_text")]
                scanned += len(user_rows)
                extractions = await extractor.extract(user_rows, settings=settings)
                extracted += len(extractions)
                for item in extractions:
                    source = user_rows[-1] if user_rows else {}
                    term_id = await slang_store.upsert_candidate(
                        term=item.term,
                        meaning=item.meaning,
                        aliases=item.aliases,
                        group_id=str(gid),
                        user_id=_speaker_to_user_id(source.get("speaker")),
                        message_id=source.get("message_id"),
                        raw_text=str(source.get("content_text") or item.evidence),
                        context="\n".join(str(row.get("content_text") or "") for row in user_rows[-8:]),
                        confidence=item.confidence,
                        reason=item.reason,
                        repeat_policy=item.repeat_policy,
                        meta={"evidence": item.evidence},
                        min_count=settings.candidate_min_count,
                        observed_count=_estimate_occurrences(item.term, item.aliases, user_rows),
                        settings=settings,
                    )
                    if term_id:
                        promoted += 1
            await slang_store.set_meta("last_extracted_at", time.strftime("%Y-%m-%d %H:%M:%S"))
            await slang_store.finish_extraction_run(
                run_id,
                status="success",
                group_count=len(groups),
                scanned_messages=scanned,
                extracted_terms=extracted,
                promoted_candidates=promoted,
            )
            return {
                "ok": True,
                "run_id": run_id,
                "groups": groups,
                "scanned": scanned,
                "extracted": extracted,
                "candidates": promoted,
            }
        except Exception as exc:
            await slang_store.finish_extraction_run(
                run_id,
                status="failed",
                group_count=len(groups),
                scanned_messages=scanned,
                extracted_terms=extracted,
                promoted_candidates=promoted,
                error=str(exc),
            )
            return {"ok": False, "run_id": run_id, "error": str(exc)}

    return router
