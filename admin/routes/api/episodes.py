"""JSON API: episodic memory management."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from services.episodic.store import Episode, EpisodeStore


def create_episodes_router(*, ctx: Any = None, bus: Any = None) -> APIRouter:
    router = APIRouter(prefix="/episodes", tags=["episodes"])

    _store_instance: EpisodeStore | None = None

    async def _store() -> EpisodeStore:
        nonlocal _store_instance
        if _store_instance is not None:
            return _store_instance
        if ctx is not None:
            s = getattr(ctx, "episode_store", None)
            if s is not None:
                _store_instance = s
                return s
        storage_dir = Path(getattr(ctx, "storage_dir", Path("storage"))) if ctx else Path("storage")
        s = EpisodeStore(str(storage_dir / "episodic.db"))
        await s.init()
        _store_instance = s
        return s

    async def _read_json(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    def _episode_to_dict(ep: Episode) -> dict[str, Any]:
        return {
            "episode_id": ep.episode_id,
            "group_id": ep.group_id,
            "scope": ep.scope,
            "situation": ep.situation,
            "observed_context": ep.observed_context,
            "action_taken": ep.action_taken,
            "outcome_signal": ep.outcome_signal,
            "reflection": ep.reflection,
            "linked_memory_ids": ep.linked_memory_ids,
            "confidence": ep.confidence,
            "episode_state": ep.episode_state,
            "source": ep.source,
            "decay_at": ep.decay_at,
            "last_used_at": ep.last_used_at,
            "created_at": ep.created_at,
            "updated_at": ep.updated_at,
            "disabled_by_admin": ep.disabled_by_admin,
            "cross_group_visible": ep.cross_group_visible,
            "cross_group_enabled_by": ep.cross_group_enabled_by,
            "cross_group_enabled_at": ep.cross_group_enabled_at,
            "cross_group_enabled_for_groups": list(ep.cross_group_enabled_for_groups),
            "cross_group_enabled_reason": ep.cross_group_enabled_reason,
            "meta": ep.meta,
        }

    @router.get("")
    async def list_episodes(request: Request):
        """List episodes with optional state/group_id filter."""
        store = await _store()
        params = request.query_params
        group_id = params.get("group_id", "")
        state_filter = params.get("state", None)
        limit = min(int(params.get("limit", "50")), 200)
        offset = int(params.get("offset", "0"))
        episodes = await store.list_episodes(
            group_id=group_id, state_filter=state_filter,
            limit=limit, offset=offset,
        )
        return {"ok": True, "episodes": [_episode_to_dict(e) for e in episodes], "count": len(episodes)}

    @router.get("/stats")
    async def episode_stats(request: Request):
        """Count episodes by state."""
        store = await _store()
        group_id = request.query_params.get("group_id", "")
        stats = await store.count_by_state(group_id=group_id)
        return {"ok": True, "stats": stats}

    @router.get("/{episode_id}")
    async def get_episode(episode_id: str):
        """Get a single episode by ID."""
        store = await _store()
        ep = await store.get_episode(episode_id)
        if ep is None:
            return JSONResponse(status_code=404, content={"ok": False, "error": "episode not found"})
        return {"ok": True, "episode": _episode_to_dict(ep)}

    @router.post("/{episode_id}/approve")
    async def approve_episode(episode_id: str, request: Request):
        """Transition candidate -> approved."""
        store = await _store()
        body = await _read_json(request)
        reason = str(body.get("reason", "")).strip() or "admin approve"
        try:
            ok = await store.transition_state(
                episode_id, new_state="approved", actor="admin", reason=reason,
            )
        except ValueError as e:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
        if not ok:
            return JSONResponse(status_code=404, content={"ok": False, "error": "episode not found"})
        return {"ok": True, "episode_id": episode_id, "new_state": "approved"}

    @router.post("/{episode_id}/disable")
    async def disable_episode(episode_id: str, request: Request):
        """Transition any -> disabled."""
        store = await _store()
        body = await _read_json(request)
        reason = str(body.get("reason", "")).strip() or "admin disable"
        try:
            ok = await store.transition_state(
                episode_id, new_state="disabled", actor="admin", reason=reason,
            )
        except ValueError as e:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
        if not ok:
            return JSONResponse(status_code=404, content={"ok": False, "error": "episode not found"})
        return {"ok": True, "episode_id": episode_id, "new_state": "disabled"}

    @router.post("/{episode_id}/restore")
    async def restore_episode(episode_id: str, request: Request):
        """Transition disabled -> approved."""
        store = await _store()
        body = await _read_json(request)
        reason = str(body.get("reason", "")).strip() or "admin restore"
        try:
            ok = await store.transition_state(
                episode_id, new_state="approved", actor="admin", reason=reason,
            )
        except ValueError as e:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
        if not ok:
            return JSONResponse(status_code=404, content={"ok": False, "error": "episode not found"})
        return {"ok": True, "episode_id": episode_id, "new_state": "approved"}

    @router.get("/{episode_id}/revisions")
    async def list_revisions(episode_id: str, request: Request):
        """List revision history for an episode."""
        store = await _store()
        limit = min(int(request.query_params.get("limit", "50")), 200)
        revisions = await store.list_revisions(episode_id, limit=limit)
        return {
            "ok": True,
            "revisions": [
                {
                    "revision_id": r.revision_id,
                    "episode_id": r.episode_id,
                    "action": r.action,
                    "actor": r.actor,
                    "prev_state": r.prev_state,
                    "new_state": r.new_state,
                    "before": r.before,
                    "after": r.after,
                    "reason": r.reason,
                    "created_at": r.created_at,
                }
                for r in revisions
            ],
        }

    return router
