"""JSON API: MemoryConsolidator dry-run runs + candidates.

Dry-run only — promotion to production stores is intentionally out of
scope here. ``decide`` only updates the candidate's state field; it does
not write to slang/style/episodic/knowledge_graph.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from services.memory_consolidator import (
    CANDIDATE_DOMAINS,
    CANDIDATE_SCOPES,
    CANDIDATE_STATES,
    Candidate,
    ConsolidatorCandidatesStore,
    EpisodePromoter,
    MemoryConsolidator,
    ScanRun,
)


def create_memory_consolidator_router(*, ctx: Any = None) -> APIRouter:
    router = APIRouter(prefix="/memory_consolidator", tags=["memory_consolidator"])

    _store_instance: ConsolidatorCandidatesStore | None = None

    async def _store() -> ConsolidatorCandidatesStore:
        nonlocal _store_instance
        if _store_instance is not None:
            return _store_instance
        if ctx is not None:
            wired = getattr(ctx, "memory_consolidator_store", None)
            if wired is not None:
                _store_instance = wired
                return wired
        storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx else Path("storage")
        s = ConsolidatorCandidatesStore(str(storage_dir / "consolidator_candidates.db"))
        await s.init()
        _store_instance = s
        return s

    def _consolidator() -> MemoryConsolidator | None:
        if ctx is None:
            return None
        return getattr(ctx, "memory_consolidator", None)

    def _episode_promoter() -> EpisodePromoter | None:
        if ctx is None:
            return None
        return getattr(ctx, "episode_promoter", None)

    async def _read_json(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    def _run_to_dict(run: ScanRun) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "triggered_by": run.triggered_by,
            "group_id": run.group_id,
            "scope": run.scope,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "status": run.status,
            "scanned_count": run.scanned_count,
            "candidates_count": run.candidates_count,
            "error_text": run.error_text,
            "meta": run.meta,
        }

    def _candidate_to_dict(c: Candidate) -> dict[str, Any]:
        return {
            "candidate_id": c.candidate_id,
            "run_id": c.run_id,
            "domain": c.domain,
            "scope": c.scope,
            "group_id": c.group_id,
            "source_message_pks": list(c.source_message_pks),
            "payload": c.payload,
            "confidence": c.confidence,
            "state": c.state,
            "decision_reason": c.decision_reason,
            "decided_by": c.decided_by,
            "decided_at": c.decided_at,
            "normalizer_cluster_id": c.normalizer_cluster_id,
            "created_at": c.created_at,
        }

    def _clamp_int(raw: str | None, default: int, *, lo: int, hi: int) -> int:
        try:
            value = int(raw) if raw is not None else default
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    @router.get("/runs")
    async def list_runs(request: Request):
        store = await _store()
        params = request.query_params
        limit = _clamp_int(params.get("limit"), 50, lo=1, hi=200)
        offset = _clamp_int(params.get("offset"), 0, lo=0, hi=10_000_000)
        runs = await store.list_runs(limit=limit, offset=offset)
        return {
            "ok": True,
            "data": [_run_to_dict(r) for r in runs],
            "count": len(runs),
        }

    @router.get("/runs/{run_id}/candidates")
    async def list_run_candidates(run_id: str, request: Request):
        store = await _store()
        params = request.query_params
        domain = params.get("domain", "") or ""
        state = params.get("state", "") or ""
        if domain and domain not in CANDIDATE_DOMAINS:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": f"invalid domain: {domain!r}"},
            )
        if state and state not in CANDIDATE_STATES:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": f"invalid state: {state!r}"},
            )
        limit = _clamp_int(params.get("limit"), 50, lo=1, hi=200)
        offset = _clamp_int(params.get("offset"), 0, lo=0, hi=10_000_000)
        if not run_id:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "run_id required"},
            )
        run = await store.get_run(run_id)
        if run is None:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": "run not found"},
            )
        candidates = await store.list_candidates(
            run_id=run_id,
            domain=domain,
            state=state,
            limit=limit,
            offset=offset,
        )
        return {
            "ok": True,
            "data": [_candidate_to_dict(c) for c in candidates],
            "run": _run_to_dict(run),
            "count": len(candidates),
        }

    @router.get("/candidates")
    async def list_candidates(request: Request):
        store = await _store()
        params = request.query_params
        domain = params.get("domain", "") or ""
        state = params.get("state", "") or ""
        scope = params.get("scope", "") or ""
        group_id = params.get("group_id", "") or ""
        if domain and domain not in CANDIDATE_DOMAINS:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": f"invalid domain: {domain!r}"},
            )
        if state and state not in CANDIDATE_STATES:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": f"invalid state: {state!r}"},
            )
        if scope and scope not in CANDIDATE_SCOPES:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": f"invalid scope: {scope!r}"},
            )
        limit = _clamp_int(params.get("limit"), 50, lo=1, hi=200)
        offset = _clamp_int(params.get("offset"), 0, lo=0, hi=10_000_000)
        candidates = await store.list_candidates(
            domain=domain,
            state=state,
            scope=scope,
            group_id=group_id,
            limit=limit,
            offset=offset,
        )
        return {
            "ok": True,
            "data": [_candidate_to_dict(c) for c in candidates],
            "count": len(candidates),
        }

    @router.post("/runs")
    async def trigger_run(request: Request):
        body = await _read_json(request)
        consolidator = _consolidator()
        if consolidator is None:
            return JSONResponse(
                status_code=503,
                content={
                    "ok": False,
                    "error": "memory consolidator not wired (ctx.memory_consolidator missing)",
                },
            )
        group_id = str(body.get("group_id", "") or "").strip()
        if not group_id:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "group_id required"},
            )
        scope_raw = str(body.get("scope", "group") or "group").strip()
        if scope_raw not in CANDIDATE_SCOPES:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": f"invalid scope: {scope_raw!r}"},
            )
        try:
            max_batches = int(body.get("max_batches", 1) or 1)
        except (TypeError, ValueError):
            max_batches = 1
        max_batches = max(1, min(max_batches, 5))
        try:
            batch_size = int(body.get("batch_size", 50) or 50)
        except (TypeError, ValueError):
            batch_size = 50
        batch_size = max(1, min(batch_size, 200))
        triggered_by = (
            str(body.get("triggered_by", "") or "").strip() or "admin"
        )
        try:
            report = await consolidator.run_once(
                group_id=group_id,
                triggered_by=triggered_by,
                scope=scope_raw,
                max_batches=max_batches,
                batch_size=batch_size,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            )
        return {
            "ok": True,
            "data": {
                "run_id": report.run_id,
                "scanned": report.scanned,
                "candidates": report.candidates,
                "status": report.status,
                "error_text": report.error_text,
            },
        }

    @router.post("/candidates/{candidate_id}/decide")
    async def decide(candidate_id: str, request: Request):
        store = await _store()
        body = await _read_json(request)
        if not candidate_id:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "candidate_id required"},
            )
        new_state = str(body.get("state", "") or "").strip()
        if new_state not in CANDIDATE_STATES:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": f"invalid state: {new_state!r}"},
            )
        decided_by = str(body.get("decided_by", "") or "admin").strip() or "admin"
        reason = str(body.get("reason", "") or "").strip()
        try:
            ok = await store.decide_candidate(
                candidate_id,
                state=new_state,  # type: ignore[arg-type]
                decided_by=decided_by,
                reason=reason,
            )
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": str(exc)},
            )
        if not ok:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": "candidate not found"},
            )

        # D.1 promote bridge — only when admin moves an episode-domain
        # candidate to ``approved``. Failures here never roll back the
        # candidate state; the candidate row is the source of truth.
        promote_info: dict[str, Any] | None = None
        if new_state == "approved":
            promoter = _episode_promoter()
            candidate = await store.get_candidate(candidate_id)
            if (
                promoter is not None
                and candidate is not None
                and candidate.domain == "episode"
            ):
                result = await promoter.promote(candidate_id, actor=decided_by)
                promote_info = {
                    "episode_id": result.episode_id,
                    "promoted": result.promoted,
                    "skipped_reason": result.skipped_reason,
                }

        response: dict[str, Any] = {
            "ok": True,
            "data": {
                "candidate_id": candidate_id,
                "new_state": new_state,
                "decided_by": decided_by,
            },
        }
        if promote_info is not None:
            response["data"]["promote"] = promote_info
        return response

    return router
