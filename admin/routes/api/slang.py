"""JSON API: slang — group slang review, settings, and manual extraction."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from loguru import logger

from services.conversation_archive import add_evidence_message_ref, finish_scan_batch, read_scan_batch
from services.slang import (
    SlangDailyReviewer,
    SlangDatabaseCorruptError,
    SlangDriftReviewer,
    SlangExtractor,
    SlangSettings,
    SlangStore,
    normalize_term,
)
from services.slang.quality import estimate_slang_occurrences, select_slang_source_row, speaker_to_user_id
from services.slang.types import (
    SlangDriftReview,
    SlangExtractionRun,
    SlangObservation,
    SlangPendingCandidate,
    SlangTerm,
    SlangTermRevision,
)

_L = logger.bind(channel="system")
_REPAIR_SCRIPT = "scripts/dev/slang_db_repair.py"


def _slang_db_error_payload(detail: str, db_path: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error_code": "slang_db_corrupt",
        "error": str(detail or "").strip() or "slang database corrupt",
        "repair_script": _REPAIR_SCRIPT,
    }
    if db_path:
        payload["db_path"] = db_path
    return payload


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
        "normalization": term.meta.get("normalization_cluster_id") and {
            "cluster_id": term.meta.get("normalization_cluster_id"),
            "item_id": term.meta.get("normalization_item_id"),
            "canonical_text": term.meta.get("normalized_from"),
            "normalized_key": term.meta.get("normalized_key"),
            "method": term.meta.get("normalization_method"),
            "score": term.meta.get("normalization_score"),
            "auto_merged": term.meta.get("auto_merged"),
            "features": term.meta.get("normalization_features") or {},
        },
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
        "normalization": candidate.meta.get("normalization_cluster_id") and {
            "cluster_id": candidate.meta.get("normalization_cluster_id"),
            "item_id": candidate.meta.get("normalization_item_id"),
            "canonical_text": candidate.meta.get("normalized_from"),
            "normalized_key": candidate.meta.get("normalized_key"),
            "method": candidate.meta.get("normalization_method"),
            "score": candidate.meta.get("normalization_score"),
            "auto_merged": candidate.meta.get("auto_merged"),
            "features": candidate.meta.get("normalization_features") or {},
        },
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


def create_slang_router(
    *,
    ctx: Any = None,
    bus: Any = None,
    message_log: Any = None,
    llm_client: Any = None,
    store: SlangStore | None = None,
) -> APIRouter:
    fallback_store: SlangStore | None = store

    def _slang_db_path() -> str:
        plugin = _plugin()
        plugin_store = getattr(plugin, "store", None)
        if plugin_store is not None:
            return str(getattr(plugin_store, "db_path", getattr(plugin_store, "_db_path", "")) or "")
        if ctx is not None:
            ctx_store = getattr(ctx, "slang_store", None)
            if ctx_store is not None:
                return str(getattr(ctx_store, "db_path", getattr(ctx_store, "_db_path", "")) or "")
        if fallback_store is not None:
            return str(getattr(fallback_store, "db_path", getattr(fallback_store, "_db_path", "")) or "")
        return ""

    class SlangDBGuardRoute(APIRoute):
        def get_route_handler(self):
            original_handler = super().get_route_handler()

            async def custom_route_handler(request: Request):
                try:
                    return await original_handler(request)
                except SlangDatabaseCorruptError as exc:
                    _L.error(
                        "slang database corrupt | db={} error={} repair={}",
                        exc.db_path,
                        exc.detail,
                        _REPAIR_SCRIPT,
                    )
                    return JSONResponse(
                        status_code=503,
                        content=_slang_db_error_payload(exc.detail, exc.db_path),
                    )
                except (sqlite3.DatabaseError, aiosqlite.DatabaseError) as exc:
                    db_path = _slang_db_path()
                    _L.error(
                        "slang database corrupt | db={} error={} repair={}",
                        db_path or "unknown",
                        str(exc),
                        _REPAIR_SCRIPT,
                    )
                    return JSONResponse(
                        status_code=503,
                        content=_slang_db_error_payload(str(exc), db_path),
                    )

            return custom_route_handler

    router = APIRouter(route_class=SlangDBGuardRoute)

    def _plugin() -> Any:
        if bus is not None and hasattr(bus, "get_plugin"):
            return bus.get_plugin("slang")
        return getattr(ctx, "slang_plugin", None) if ctx is not None else None

    async def _store() -> SlangStore:
        nonlocal fallback_store
        plugin = _plugin()
        plugin_store = getattr(plugin, "store", None)
        if plugin_store is not None:
            if getattr(plugin_store, "_drift_reviewer", None) is None:
                plugin_store.set_drift_reviewer(SlangDriftReviewer(_llm_client()))
            return plugin_store
        ctx_store = getattr(ctx, "slang_store", None) if ctx is not None else None
        if ctx_store is not None:
            if getattr(ctx_store, "_drift_reviewer", None) is None:
                ctx_store.set_drift_reviewer(SlangDriftReviewer(_llm_client()))
            return ctx_store
        if fallback_store is None:
            storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx is not None else Path("storage")
            fallback_store = SlangStore(storage_dir / "slang.db")
            await fallback_store.init()
            fallback_store.set_drift_reviewer(SlangDriftReviewer(_llm_client()))
            if ctx is not None:
                ctx.slang_store = fallback_store
        elif not fallback_store.initialized:
            await fallback_store.init()
            fallback_store.set_drift_reviewer(SlangDriftReviewer(_llm_client()))
        elif getattr(fallback_store, "_drift_reviewer", None) is None:
            fallback_store.set_drift_reviewer(SlangDriftReviewer(_llm_client()))
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
        sort: str = Query("default"),
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
            sort="time" if sort == "time" else "default",
        )
        return {
            "terms": [_term_to_dict(term) for term in terms],
            "total": total,
            "page": page,
            "page_size": page_size,
            "sort": "time" if sort == "time" else "default",
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

    @router.post("/slang/drift/replay")
    async def replay_drift_reviews(request: Request):
        body = await _read_json(request)
        apply = bool(body.get("apply", False))
        try:
            limit = max(1, min(int(body.get("limit") or 100), 200))
        except Exception:
            limit = 100
        slang_store = await _store()
        return await slang_store.replay_open_drift_reviews(limit=limit, apply=apply)

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

    @router.post("/slang/review/run")
    async def run_daily_review(request: Request):
        body = await _read_json(request)
        group_id = str(body.get("group_id") or "").strip() or None
        force = bool(body.get("force", False))
        review_candidates = bool(body.get("review_candidates", False))
        review_all_pending = bool(body.get("review_all_pending", False))
        rerun_reviewed_candidates = bool(body.get("rerun_reviewed_candidates", False))
        candidate_review_filter = str(body.get("candidate_review_filter") or "").strip()
        slang_store = await _store()
        settings = await slang_store.load_settings()
        plugin = _plugin()
        if plugin is not None:
            if force and hasattr(plugin, "run_daily_ai_review"):
                return await plugin.run_daily_ai_review(
                    ctx,
                    settings=settings,
                    group_id=group_id,
                    review_candidates=review_candidates,
                    review_all_pending=review_all_pending,
                    rerun_reviewed_candidates=rerun_reviewed_candidates,
                    candidate_review_filter=candidate_review_filter,
                )
            if hasattr(plugin, "run_daily_ai_review_if_due"):
                return await plugin.run_daily_ai_review_if_due(ctx, settings=settings)

        log = _message_log()
        if log is None:
            return {"ok": False, "error": "MessageLog not available"}
        llm = _llm_client()
        if llm is None:
            return {"ok": False, "error": "LLMClient not available"}
        reviewer = SlangDailyReviewer(llm)
        return await reviewer.run(
            store=slang_store,
            message_log=log,
            settings=settings,
            tool_registry=getattr(ctx, "tool_registry", None) if ctx is not None else None,
            group_id=group_id,
            review_candidates=review_candidates,
            review_all_pending=review_all_pending,
            rerun_reviewed_candidates=rerun_reviewed_candidates,
            candidate_review_filter=candidate_review_filter,
        )

    @router.post("/slang/debug/message/seed")
    async def seed_debug_message(request: Request):
        body = await _read_json(request)
        group_id = str(body.get("group_id") or "").strip()
        content_text = str(body.get("content_text") or "").strip()
        if not group_id or not content_text:
            return {"ok": False, "error": "group_id and content_text are required"}
        role = str(body.get("role") or "user").strip() or "user"
        speaker = str(body.get("speaker") or "SmokeUser(999000)").strip() or "SmokeUser(999000)"
        message_id = body.get("message_id")
        try:
            message_id_int = int(message_id) if message_id is not None and str(message_id).strip() != "" else None
        except Exception:
            message_id_int = None
        log = _message_log()
        if log is None or not hasattr(log, "record"):
            return {"ok": False, "error": "MessageLog not available"}
        await log.record(
            group_id=group_id,
            role=role,
            speaker=speaker,
            content_text=content_text,
            content_json=None,
            message_id=message_id_int,
        )
        return {
            "ok": True,
            "group_id": group_id,
            "role": role,
            "speaker": speaker,
            "content_text": content_text,
            "message_id": message_id_int,
        }

    @router.post("/slang/debug/message/delete")
    async def delete_debug_message(request: Request):
        body = await _read_json(request)
        group_id = str(body.get("group_id") or "").strip()
        content_text = str(body.get("content_text") or "").strip()
        if not group_id or not content_text:
            return {"ok": False, "error": "group_id and content_text are required"}
        message_id = body.get("message_id")
        try:
            message_id_int = int(message_id) if message_id is not None and str(message_id).strip() != "" else None
        except Exception:
            message_id_int = None
        log = _message_log()
        db = getattr(log, "_db", None) if log is not None else None
        if db is None:
            return {"ok": False, "error": "MessageLog database not available"}
        if message_id_int is None:
            await db.execute(
                "DELETE FROM group_messages WHERE group_id = ? AND content_text = ?",
                (group_id, content_text),
            )
        else:
            await db.execute(
                "DELETE FROM group_messages WHERE group_id = ? AND content_text = ? AND message_id = ?",
                (group_id, content_text, message_id_int),
            )
        await db.commit()
        return {
            "ok": True,
            "group_id": group_id,
            "content_text": content_text,
            "message_id": message_id_int,
        }

    @router.post("/slang/debug/pending/seed")
    async def seed_debug_pending(request: Request):
        slang_store = await _store()
        body = await _read_json(request)
        group_id = str(body.get("group_id") or "").strip()
        term = str(body.get("term") or "").strip()
        if not group_id or not term:
            return {"ok": False, "error": "group_id and term are required"}
        aliases = body.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [part.strip() for part in aliases.replace("，", ",").split(",") if part.strip()]
        if not isinstance(aliases, list):
            aliases = []
        try:
            count = max(1, int(body.get("count") or 2))
        except Exception:
            count = 2
        try:
            confidence = max(0.0, min(1.0, float(body.get("confidence") or 0.6)))
        except Exception:
            confidence = 0.6
        pending_id = str(body.get("pending_id") or f"pending_smoke_{time.time_ns():x}")
        term_key = normalize_term(term)
        if not term_key:
            return {"ok": False, "error": "term is required"}
        meaning = str(body.get("meaning") or "自动化烟雾测试样本")
        evidence = str(body.get("evidence") or f"{term} 这是临时烟雾测试样本")
        reason = str(body.get("reason") or "semantic_smoke_seed")
        repeat_policy = str(body.get("repeat_policy") or "understand_only")
        meta = body.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        meta = {**meta, "smoke_seed": True, "smoke_seed_term": term}
        now = datetime.now().isoformat(timespec="seconds")

        db = slang_store._require_db()
        delete_observation_sql = (
            "DELETE FROM slang_observations WHERE term_id = ? OR term_id IN "
            "(SELECT term_id FROM slang_terms WHERE group_id = ? AND term_key = ?)"
        )
        delete_revisions_sql = (
            "DELETE FROM slang_term_revisions WHERE term_id IN "
            "(SELECT term_id FROM slang_terms WHERE group_id = ? AND term_key = ?)"
        )
        delete_drift_sql = (
            "DELETE FROM slang_drift_reviews WHERE term_id IN "
            "(SELECT term_id FROM slang_terms WHERE group_id = ? AND term_key = ?)"
        )
        term_cursor = await db.execute(
            "SELECT term_id FROM slang_terms WHERE group_id = ? AND term_key = ?",
            (group_id, term_key),
        )
        term_ids = [str(row[0]) for row in await term_cursor.fetchall()]
        await db.execute(delete_observation_sql, (pending_id, group_id, term_key))
        await db.execute(delete_revisions_sql, (group_id, term_key))
        await db.execute(delete_drift_sql, (group_id, term_key))
        await db.execute(
            "DELETE FROM slang_pending_candidates WHERE pending_id = ? OR (group_id = ? AND term_key = ?)",
            (pending_id, group_id, term_key),
        )
        await db.execute(
            "DELETE FROM slang_terms WHERE group_id = ? AND term_key = ?",
            (group_id, term_key),
        )
        for term_id in term_ids:
            await db.execute("DELETE FROM slang_observations WHERE term_id = ?", (term_id,))
            await db.execute("DELETE FROM slang_term_revisions WHERE term_id = ?", (term_id,))
            await db.execute("DELETE FROM slang_drift_reviews WHERE term_id = ?", (term_id,))

        await db.execute(
            """INSERT INTO slang_pending_candidates
               (pending_id, term_key, term, meaning, aliases_json, group_id, confidence,
                count, unique_users_json, evidence, reason, repeat_policy, first_seen_at,
                last_seen_at, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pending_id,
                term_key,
                term,
                meaning,
                json.dumps(aliases, ensure_ascii=False),
                group_id,
                confidence,
                count,
                json.dumps(["smoke"], ensure_ascii=False),
                evidence,
                reason,
                repeat_policy,
                now,
                now,
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        await db.execute(
            """INSERT INTO slang_observations
               (observation_id, term_id, group_id, user_id, message_id, raw_text, context, observed_at, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"obs_smoke_{pending_id.removeprefix('pending_smoke_')}",
                pending_id,
                group_id,
                "smoke",
                None,
                evidence,
                evidence,
                now,
                reason,
            ),
        )
        await db.commit()
        await slang_store.rebuild_pending_key_index()
        await db.commit()
        return {
            "ok": True,
            "pending_id": pending_id,
            "group_id": group_id,
            "term": term,
            "term_key": term_key,
            "count": count,
            "confidence": confidence,
        }

    @router.post("/slang/debug/pending/delete")
    async def delete_debug_pending(request: Request):
        slang_store = await _store()
        body = await _read_json(request)
        pending_id = str(body.get("pending_id") or "").strip()
        term = str(body.get("term") or body.get("term_key") or "").strip()
        group_id = str(body.get("group_id") or "").strip()
        if not pending_id and not (group_id and term):
            return {"ok": False, "error": "pending_id or group_id+term is required"}
        term_key = normalize_term(term)
        db = slang_store._require_db()
        if pending_id and (not group_id or not term_key):
            cursor = await db.execute(
                "SELECT group_id, term_key FROM slang_pending_candidates WHERE pending_id = ?",
                (pending_id,),
            )
            row = await cursor.fetchone()
            if row is not None:
                group_id = group_id or str(row["group_id"])
                term_key = term_key or str(row["term_key"])
        if not group_id or not term_key:
            return {"ok": False, "error": "pending not found"}

        delete_observation_sql = (
            "DELETE FROM slang_observations WHERE term_id = ? OR term_id IN "
            "(SELECT term_id FROM slang_terms WHERE group_id = ? AND term_key = ?)"
        )
        delete_revisions_sql = (
            "DELETE FROM slang_term_revisions WHERE term_id IN "
            "(SELECT term_id FROM slang_terms WHERE group_id = ? AND term_key = ?)"
        )
        delete_drift_sql = (
            "DELETE FROM slang_drift_reviews WHERE term_id IN "
            "(SELECT term_id FROM slang_terms WHERE group_id = ? AND term_key = ?)"
        )
        term_cursor = await db.execute(
            "SELECT term_id FROM slang_terms WHERE group_id = ? AND term_key = ?",
            (group_id, term_key),
        )
        term_ids = [str(row[0]) for row in await term_cursor.fetchall()]
        await db.execute(delete_observation_sql, (pending_id, group_id, term_key))
        await db.execute(delete_revisions_sql, (group_id, term_key))
        await db.execute(delete_drift_sql, (group_id, term_key))
        await db.execute(
            "DELETE FROM slang_pending_candidates WHERE pending_id = ? OR (group_id = ? AND term_key = ?)",
            (pending_id, group_id, term_key),
        )
        await db.execute(
            "DELETE FROM slang_terms WHERE group_id = ? AND term_key = ?",
            (group_id, term_key),
        )
        for term_id in term_ids:
            await db.execute("DELETE FROM slang_observations WHERE term_id = ?", (term_id,))
            await db.execute("DELETE FROM slang_term_revisions WHERE term_id = ?", (term_id,))
            await db.execute("DELETE FROM slang_drift_reviews WHERE term_id = ?", (term_id,))
        await db.commit()
        await slang_store.rebuild_pending_key_index()
        await db.commit()
        return {
            "ok": True,
            "pending_id": pending_id,
            "group_id": group_id,
            "term_key": term_key,
            "deleted_terms": len(term_ids),
        }

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
                batch = await read_scan_batch(
                    log,
                    scanner_name="slang_manual_extract",
                    group_id=str(gid),
                    limit=limit,
                    scanner_version="v1",
                    meta={"admin_fallback": True, "run_id": run_id},
                )
                group_scanned = 0
                group_extracted = 0
                group_promoted = 0
                try:
                    rows = list(batch.get("rows") or [])
                    user_rows = [row for row in rows if row.get("role") == "user" and row.get("content_text")]
                    group_scanned = len(user_rows)
                    scanned += group_scanned
                    extractions = await extractor.extract(user_rows, settings=settings)
                    group_extracted = len(extractions)
                    extracted += group_extracted
                    for item in extractions:
                        source = select_slang_source_row(item.evidence, user_rows)
                        term_id = await slang_store.upsert_candidate(
                            term=item.term,
                            meaning=item.meaning,
                            aliases=item.aliases,
                            group_id=str(gid),
                            user_id=speaker_to_user_id(source.get("speaker")),
                            message_id=source.get("message_id"),
                            raw_text=str(source.get("content_text") or item.evidence),
                            context="\n".join(str(row.get("content_text") or "") for row in user_rows[-8:]),
                            confidence=item.confidence,
                            reason=item.reason,
                            repeat_policy=item.repeat_policy,
                            meta={"evidence": item.evidence},
                            min_count=settings.candidate_min_count,
                            observed_count=estimate_slang_occurrences(item.term, item.aliases, user_rows),
                            settings=settings,
                        )
                        if term_id:
                            await add_evidence_message_ref(
                                log,
                                group_id=str(gid),
                                source_row=source,
                                ref_owner="slang",
                                external_table="slang_terms",
                                external_id=term_id,
                                snapshot_text=str(source.get("content_text") or item.evidence),
                                meta={"source": "slang_manual_extract", "slang_run_id": run_id},
                            )
                            promoted += 1
                            group_promoted += 1
                    await finish_scan_batch(
                        log,
                        batch,
                        status="success",
                        scanned_count=group_scanned,
                        extracted_count=group_extracted,
                        saved_count=group_promoted,
                        meta={"slang_run_id": run_id},
                    )
                except Exception as exc:
                    await finish_scan_batch(
                        log,
                        batch,
                        status="failed",
                        scanned_count=group_scanned,
                        extracted_count=group_extracted,
                        saved_count=group_promoted,
                        error=str(exc),
                        advance_cursor=False,
                    )
                    raise
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
