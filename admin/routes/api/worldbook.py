"""Read-only Worldbook admin API — status/gates/registry/ledger/life/lifecycle.

GET-only under ``/api/admin/worldbook``. Never instantiates runtime, never
calls ``ensure_loaded``, never validate/commit/process proposals, never writes
state, and never exposes raw Social Narrative or unrestricted proposal payloads.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from loguru import logger

_L = logger.bind(channel="admin.worldbook")

_LIST_CAP = 100
_TRACE_CAP = 200
_SHADOW_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB
_SHADOW_NAME = "shadow_report.json"

# Keys that must never appear in serialized admin responses (defense in depth).
_FORBIDDEN_RESPONSE_MARKERS = (
    "user_text",
    "bot_reply",
    "raw_social",
    "social_narrative",
)


def create_worldbook_router(*, ctx: Any = None) -> APIRouter:
    router = APIRouter(prefix="/worldbook", tags=["worldbook"])

    @router.get("/snapshot")
    async def worldbook_snapshot() -> dict[str, Any]:
        """Bounded sanitized Worldbook observability snapshot.

        Fail-closed when runtime is missing or config.enabled is false:
        no ensure_loaded, no store I/O, no file creation, no exceptions.
        """
        runtime = getattr(ctx, "worldbook_runtime", None) if ctx is not None else None
        if runtime is None:
            return _unavailable("runtime_not_mounted")

        cfg = getattr(runtime, "config", None)
        if cfg is None:
            cfg = getattr(ctx, "worldbook_config", None) if ctx is not None else None
        if cfg is None or not bool(getattr(cfg, "enabled", False)):
            return _unavailable("worldbook_disabled")

        try:
            return await _build_snapshot(runtime, ctx=ctx)
        except Exception as exc:
            # Never leak exception messages (paths/secrets).
            err_type = type(exc).__name__
            _L.warning("worldbook snapshot failed | error={}", err_type)
            return {
                "ok": True,
                "available": False,
                "reason": f"snapshot_failed:{err_type}",
            }

    return router


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "ok": True,
        "available": False,
        "reason": reason,
        "gates": None,
        "registry": None,
        "ledger": None,
        "life": None,
        "lifecycle": None,
        "block_traces": [],
        "shadow": {"status": "skipped", "reason": reason},
    }


async def _build_snapshot(runtime: Any, *, ctx: Any) -> dict[str, Any]:
    cfg = runtime.config
    return {
        "ok": True,
        "available": True,
        "reason": "ok",
        "gates": _gates_summary(cfg),
        "registry": _registry_summary(runtime),
        "ledger": _ledger_summary(runtime),
        "life": _life_summary(runtime),
        "lifecycle": _lifecycle_summary(runtime),
        "block_traces": await _block_traces(ctx),
        "shadow": _shadow_summary(cfg, runtime),
    }


def _gates_summary(cfg: Any) -> dict[str, Any]:
    allowlist = getattr(cfg, "social_group_allowlist", None) or []
    try:
        allowlist_count = len(list(allowlist))
    except TypeError:
        allowlist_count = 0
    return {
        "enabled": bool(getattr(cfg, "enabled", False)),
        "chat_projection_enabled": bool(
            getattr(cfg, "chat_projection_enabled", False)
        ),
        "schedule_projection_enabled": bool(
            getattr(cfg, "schedule_projection_enabled", False)
        ),
        "storylet_enabled": bool(getattr(cfg, "storylet_enabled", False)),
        "dream_proposal_enabled": bool(
            getattr(cfg, "dream_proposal_enabled", False)
        ),
        "social_evidence_enabled": bool(
            getattr(cfg, "social_evidence_enabled", False)
        ),
        # Count only — never emit group ids (allowlist contents).
        "allowlist_count": int(allowlist_count),
        "total_budget_chars": int(getattr(cfg, "total_budget_chars", 0) or 0),
        "max_setbacks_per_arc": int(getattr(cfg, "max_setbacks_per_arc", 0) or 0),
        "max_events_per_tick": int(getattr(cfg, "max_events_per_tick", 0) or 0),
        "state_dir": str(getattr(cfg, "state_dir", "") or ""),
        "canon_dir": str(getattr(cfg, "canon_dir", "") or ""),
        "storylet_dir": str(getattr(cfg, "storylet_dir", "") or ""),
    }


def _registry_summary(runtime: Any) -> dict[str, Any]:
    canon = getattr(runtime, "canon_registry", None)
    storylets = getattr(runtime, "storylet_registry", None)
    canon_loaded = bool(getattr(canon, "loaded", False)) if canon is not None else False
    storylet_loaded = (
        bool(getattr(storylets, "_loaded", False)) if storylets is not None else False
    )
    # Prefer public attribute if present; StoryletRegistry uses _loaded only.
    if storylets is not None and hasattr(storylets, "loaded"):
        storylet_loaded = bool(storylets.loaded)

    canon_meta: list[dict[str, Any]] = []
    canon_count = 0
    if canon is not None and canon_loaded:
        try:
            entries = list(canon.list_entries())
        except Exception:
            entries = []
        canon_count = len(entries)
        for entry in entries[:_LIST_CAP]:
            canon_meta.append(
                {
                    "entry_id": str(getattr(entry, "entry_id", "") or ""),
                    "title": str(getattr(entry, "title", "") or ""),
                    "priority": int(getattr(entry, "priority", 0) or 0),
                    "always_active": bool(getattr(entry, "always_active", False)),
                    "keyword_count": len(getattr(entry, "keywords", ()) or ()),
                    "alias_count": len(getattr(entry, "aliases", ()) or ()),
                    # Deliberately omit ``text`` (canon body).
                }
            )

    storylet_meta: list[dict[str, Any]] = []
    storylet_count = 0
    if storylets is not None and storylet_loaded:
        try:
            items = list(storylets.list_storylets())
        except Exception:
            items = []
        storylet_count = len(items)
        for item in items[:_LIST_CAP]:
            storylet_meta.append(
                {
                    "storylet_id": str(getattr(item, "storylet_id", "") or ""),
                    "title": str(getattr(item, "title", "") or ""),
                    "severity": str(getattr(item, "severity", "") or ""),
                    "priority": int(getattr(item, "priority", 0) or 0),
                    "saliency": float(getattr(item, "saliency", 0.0) or 0.0),
                    "target_arc_id": str(getattr(item, "target_arc_id", "") or ""),
                    "once": bool(getattr(item, "once", False)),
                    # Deliberately omit ``text`` (storylet body).
                }
            )
    elif storylets is not None:
        # Unloaded: report empty without calling ensure_loaded / load().
        storylet_count = 0

    runtime_loaded = bool(getattr(runtime, "_loaded", False))
    return {
        "runtime_loaded": runtime_loaded,
        "canon": {
            "loaded": canon_loaded,
            "count": canon_count,
            "entries": canon_meta,
        },
        "storylets": {
            "loaded": storylet_loaded,
            "count": storylet_count,
            "entries": storylet_meta,
        },
    }


def _ledger_summary(runtime: Any) -> dict[str, Any]:
    ledger = getattr(runtime, "ledger", None)
    if ledger is None or not hasattr(ledger, "load_stack"):
        return {
            "main": None,
            "sides": [],
            "ambient": [],
            "ordering": [],
            "main_count": 0,
            "side_count": 0,
            "ambient_count": 0,
        }
    try:
        view = ledger.load_stack()
    except Exception as exc:
        _L.warning("worldbook ledger load failed | error={}", type(exc).__name__)
        return {
            "main": None,
            "sides": [],
            "ambient": [],
            "ordering": [],
            "main_count": 0,
            "side_count": 0,
            "ambient_count": 0,
            "error": f"ledger_load_failed:{type(exc).__name__}",
        }

    main = _arc_summary(getattr(view, "main", None))
    sides_raw = list(getattr(view, "sides", ()) or ())
    ambient_raw = list(getattr(view, "ambient", ()) or ())
    sides = [_arc_summary(a) for a in sides_raw[:_LIST_CAP] if a is not None]
    ambient = [_arc_summary(a) for a in ambient_raw[:_LIST_CAP] if a is not None]
    ordering = [
        str(x)
        for x in (getattr(view, "ordering", ()) or ())
        if str(x or "").strip()
    ][:_LIST_CAP]
    return {
        "main": main,
        "sides": sides,
        "ambient": ambient,
        "ordering": ordering,
        "main_count": 1 if main is not None else 0,
        "side_count": len(sides_raw),
        "ambient_count": len(ambient_raw),
    }


def _arc_summary(arc: Any) -> dict[str, Any] | None:
    if arc is None:
        return None
    if isinstance(arc, dict):
        data = arc
    else:
        to_dict = getattr(arc, "to_dict", None)
        if callable(to_dict):
            raw = to_dict()
            data = dict(raw) if isinstance(raw, dict) else {}
        else:
            data = {
                "arc_id": getattr(arc, "arc_id", ""),
                "title": getattr(arc, "title", ""),
                "stage": getattr(arc, "stage", ""),
                "status": getattr(arc, "status", ""),
                "arc_role": getattr(arc, "arc_role", ""),
                "stack_order": getattr(arc, "stack_order", 0),
            }
    goals = data.get("goals") or []
    threads = data.get("open_threads") or []
    return {
        "arc_id": str(data.get("arc_id") or ""),
        "title": str(data.get("title") or ""),
        "stage": str(data.get("stage") or ""),
        "status": str(data.get("status") or ""),
        "arc_role": str(data.get("arc_role") or data.get("role") or ""),
        "stack_order": int(data.get("stack_order") or 0),
        "goal_count": len(goals) if isinstance(goals, (list, tuple)) else 0,
        "open_thread_count": (
            len(threads) if isinstance(threads, (list, tuple)) else 0
        ),
        # Deliberately omit goals/open_threads text bodies.
    }


def _life_summary(runtime: Any) -> dict[str, Any]:
    store = getattr(runtime, "life_store", None)
    if store is None:
        return {
            "revision": 0,
            "updated_at": "",
            "item_count": 0,
            "expired_count": 0,
            "active_count": 0,
            "items": [],
            "applied_event_id_count": 0,
        }
    load = getattr(store, "load_readonly", None)
    if not callable(load):
        load = getattr(store, "load", None)
    if not callable(load):
        return {
            "revision": 0,
            "updated_at": "",
            "item_count": 0,
            "expired_count": 0,
            "active_count": 0,
            "items": [],
            "applied_event_id_count": 0,
        }
    try:
        state = load()
    except Exception as exc:
        _L.warning("worldbook life load failed | error={}", type(exc).__name__)
        return {
            "revision": 0,
            "updated_at": "",
            "item_count": 0,
            "expired_count": 0,
            "active_count": 0,
            "items": [],
            "applied_event_id_count": 0,
            "error": f"life_load_failed:{type(exc).__name__}",
        }

    items_map = getattr(state, "items", {}) or {}
    applied = list(getattr(state, "applied_event_ids", []) or [])
    now = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    expired_count = 0
    active_count = 0
    for key, item in sorted(items_map.items(), key=lambda kv: str(kv[0])):
        meta = getattr(item, "meta", None)
        decay_at = str(getattr(meta, "decay_at", None) or "") if meta else ""
        expired = _is_expired(decay_at, now=now)
        if expired:
            expired_count += 1
        else:
            active_count += 1
        if len(rows) >= _LIST_CAP:
            continue
        rows.append(
            {
                "key": str(getattr(item, "key", key) or key),
                # Life values are bot self-state (not Social Narrative raw text).
                # Bound length; never include proposal/social payloads.
                "value": _bound_str(getattr(item, "value", ""), 240),
                "expired": expired,
                "decay_at": decay_at or None,
                "provenance": {
                    "source": str(getattr(meta, "source", "") or "") if meta else "",
                    "scope": str(getattr(meta, "scope", "") or "") if meta else "",
                    "confidence": (
                        str(getattr(meta, "confidence", "") or "") if meta else ""
                    ),
                    "privacy": (
                        str(getattr(meta, "privacy", "") or "") if meta else ""
                    ),
                    "updated_at": (
                        str(getattr(meta, "updated_at", "") or "") if meta else ""
                    ),
                    "revision": int(getattr(meta, "revision", 0) or 0) if meta else 0,
                    "evidence_ref_count": (
                        len(getattr(meta, "evidence_refs", ()) or ()) if meta else 0
                    ),
                    "evidence_refs": [
                        str(ref)
                        for ref in (getattr(meta, "evidence_refs", ()) or ())
                        if str(ref or "").strip()
                    ][:_LIST_CAP]
                    if meta
                    else [],
                },
            }
        )
    return {
        "revision": int(getattr(state, "revision", 0) or 0),
        "updated_at": str(getattr(state, "updated_at", "") or ""),
        "item_count": len(items_map),
        "expired_count": expired_count,
        "active_count": active_count,
        "items": rows,
        "applied_event_id_count": len(applied),
    }


def _is_expired(decay_at: str | None, *, now: datetime | None = None) -> bool:
    """Mirror projection TTL semantics: missing/invalid decay → expired."""
    if not decay_at:
        return True
    try:
        parsed = datetime.fromisoformat(str(decay_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    clock = now if now is not None else datetime.now(UTC)
    return parsed <= clock


def _lifecycle_summary(runtime: Any) -> dict[str, Any]:
    proposals = _safe_list(
        getattr(runtime, "proposal_store", None),
        "list_proposals",
    )
    decisions = _safe_list(
        getattr(runtime, "decision_store", None),
        "list_decisions",
    )
    commits = _safe_list(
        getattr(runtime, "commit_store", None),
        "list_commits",
    )
    return {
        "proposal_count": len(proposals),
        "decision_count": len(decisions),
        "commit_count": len(commits),
        "proposals": [_sanitize_proposal(p) for p in proposals[:_LIST_CAP]],
        "decisions": [_sanitize_decision(d) for d in decisions[:_LIST_CAP]],
        "commits": [_sanitize_commit(c) for c in commits[:_LIST_CAP]],
    }


def _safe_list(store: Any, method_name: str) -> list[Any]:
    if store is None:
        return []
    method = getattr(store, method_name, None)
    if not callable(method):
        return []
    try:
        result = method()
    except Exception as exc:
        _L.warning(
            "worldbook lifecycle list failed | method={} error={}",
            method_name,
            type(exc).__name__,
        )
        return []
    if result is None:
        return []
    if isinstance(result, list):
        return list(result)
    if isinstance(result, tuple):
        return list(result)
    try:
        return list(result)  # type: ignore[arg-type]
    except TypeError:
        return []


def _sanitize_proposal(proposal: Any) -> dict[str, Any]:
    payload = getattr(proposal, "payload", None)
    payload_keys: list[str] = []
    payload_key_count = 0
    # EventProposal freezes payload as MappingProxyType (Mapping, not dict).
    if isinstance(payload, Mapping):
        payload_key_count = len(payload)
        payload_keys = sorted(
            str(k)
            for k in payload
            if str(k).strip().lower() not in _FORBIDDEN_RESPONSE_MARKERS
        )[:_LIST_CAP]
    return {
        "proposal_id": str(getattr(proposal, "proposal_id", "") or ""),
        "kind": str(getattr(proposal, "kind", "") or ""),
        "status": str(getattr(proposal, "status", "") or ""),
        "arc_id": str(getattr(proposal, "arc_id", "") or ""),
        "source": str(getattr(proposal, "source", "") or ""),
        "created_at": str(getattr(proposal, "created_at", "") or ""),
        "payload_key_count": payload_key_count,
        "payload_keys": payload_keys,
        # Explicitly omit: payload body, effects text, social fields.
    }


def _sanitize_decision(decision: Any) -> dict[str, Any]:
    effects = getattr(decision, "effects", None)
    effects_summary: dict[str, Any] | None = None
    if effects is not None:
        partners = getattr(effects, "partner_updates", ()) or ()
        lives = getattr(effects, "life_updates", ()) or ()
        deltas = getattr(effects, "variable_deltas", {}) or {}
        effects_summary = {
            "stage": (
                str(getattr(effects, "stage", "") or "")
                if getattr(effects, "stage", None) is not None
                else None
            ),
            "open_thread_count": len(getattr(effects, "open_threads", ()) or ()),
            "resolve_thread_count": len(
                getattr(effects, "resolve_threads", ()) or ()
            ),
            "partner_update_count": len(partners),
            "life_update_count": len(lives),
            "variable_delta_keys": sorted(str(k) for k in deltas)[:_LIST_CAP],
        }
    return {
        "decision_id": str(getattr(decision, "decision_id", "") or ""),
        "proposal_id": str(getattr(decision, "proposal_id", "") or ""),
        "status": str(getattr(decision, "status", "") or ""),
        "reason_code": str(getattr(decision, "reason_code", "") or ""),
        "reason": _bound_str(getattr(decision, "reason", ""), 200),
        "arc_id": str(getattr(decision, "arc_id", "") or ""),
        "decided_at": str(getattr(decision, "decided_at", "") or ""),
        "proposal_fingerprint": str(
            getattr(decision, "proposal_fingerprint", "") or ""
        ),
        "effects": effects_summary,
        # Explicitly omit normalized_payload (may hold unrestricted fields).
    }


def _sanitize_commit(commit: Any) -> dict[str, Any]:
    return {
        "commit_id": str(getattr(commit, "commit_id", "") or ""),
        "proposal_id": str(getattr(commit, "proposal_id", "") or ""),
        "decision_id": str(getattr(commit, "decision_id", "") or ""),
        "event_id": str(getattr(commit, "event_id", "") or ""),
        "arc_id": str(getattr(commit, "arc_id", "") or ""),
        "status": str(getattr(commit, "status", "") or ""),
        "committed_at": str(getattr(commit, "committed_at", "") or ""),
    }


async def _block_traces(ctx: Any) -> list[dict[str, Any]]:
    store = getattr(ctx, "block_trace_store", None) if ctx is not None else None
    if store is None or not hasattr(store, "recent"):
        return []
    try:
        traces = await store.recent(limit=_TRACE_CAP)
    except Exception as exc:
        _L.warning("worldbook block_trace recent failed | error={}", type(exc).__name__)
        return []
    out: list[dict[str, Any]] = []
    for trace in traces:
        source = str(getattr(trace, "source", "") or "")
        provider = str(getattr(trace, "provider", "") or "")
        if source != "worldbook" and provider != "worldbook":
            continue
        out.append(
            {
                "trace_id": str(getattr(trace, "trace_id", "") or ""),
                "request_id": str(getattr(trace, "request_id", "") or ""),
                "task": str(getattr(trace, "task", "") or ""),
                "source": source,
                "provider": provider,
                "candidate_id": str(getattr(trace, "candidate_id", "") or ""),
                "decision": str(getattr(trace, "decision", "") or ""),
                "hit_reason": _bound_str(getattr(trace, "hit_reason", ""), 120),
                "label": _bound_str(getattr(trace, "label", ""), 120),
                "char_count": int(getattr(trace, "char_count", 0) or 0),
                "priority": int(getattr(trace, "priority", 0) or 0),
                "budget_reason": _bound_str(getattr(trace, "budget_reason", ""), 120),
                "created_at": str(getattr(trace, "created_at", "") or ""),
                # Omit metadata/evidence_refs text to avoid social/payload leakage.
            }
        )
        if len(out) >= _LIST_CAP:
            break
    return out


def _shadow_summary(cfg: Any, runtime: Any) -> dict[str, Any]:
    """Resolve only configured ``state_dir/shadow_report.json``.

    Missing / corrupt / oversized → explicit safe status, never exception text.
    """
    state_dir = _resolve_state_dir(cfg, runtime)
    if state_dir is None:
        return {"status": "unavailable", "reason": "state_dir_unresolved"}

    path = (state_dir / _SHADOW_NAME).resolve()
    # Path traversal guard: must remain under resolved state_dir.
    try:
        state_resolved = state_dir.resolve()
        if not str(path).startswith(str(state_resolved) + "/") and path != (
            state_resolved / _SHADOW_NAME
        ).resolve():
            return {"status": "unavailable", "reason": "path_outside_state_dir"}
    except OSError:
        return {"status": "unavailable", "reason": "path_resolve_failed"}

    if not path.is_file():
        return {
            "status": "missing",
            "reason": "shadow_report_missing",
            "path_name": _SHADOW_NAME,
        }

    try:
        size = path.stat().st_size
    except OSError:
        return {
            "status": "unavailable",
            "reason": "stat_failed",
            "path_name": _SHADOW_NAME,
        }

    if size > _SHADOW_MAX_BYTES:
        return {
            "status": "oversized",
            "reason": "shadow_report_over_2mib",
            "path_name": _SHADOW_NAME,
            "size_bytes": int(size),
            "max_bytes": _SHADOW_MAX_BYTES,
        }

    try:
        raw_text = path.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return {
            "status": "corrupt",
            "reason": "shadow_report_invalid_json",
            "path_name": _SHADOW_NAME,
            "size_bytes": int(size),
        }
    except OSError:
        return {
            "status": "unavailable",
            "reason": "read_failed",
            "path_name": _SHADOW_NAME,
            "size_bytes": int(size),
        }

    if not isinstance(payload, dict):
        return {
            "status": "corrupt",
            "reason": "shadow_report_not_object",
            "path_name": _SHADOW_NAME,
            "size_bytes": int(size),
        }

    return {
        "status": "ok",
        "reason": "ok",
        "path_name": _SHADOW_NAME,
        "size_bytes": int(size),
        "content": _shadow_content_summary(payload),
    }


def _resolve_state_dir(cfg: Any, runtime: Any) -> Path | None:
    # Prefer runtime-resolved absolute path when available.
    paths = getattr(runtime, "_paths", None)
    if isinstance(paths, dict) and paths.get("state_dir") is not None:
        try:
            return Path(paths["state_dir"])
        except (TypeError, ValueError):
            pass
    raw = str(getattr(cfg, "state_dir", "") or "").strip()
    if not raw:
        return None
    try:
        path = Path(raw)
    except (TypeError, ValueError):
        return None
    # Relative paths are resolved against runtime root when present.
    root = getattr(runtime, "_root", None)
    if not path.is_absolute() and root is not None:
        try:
            return Path(root) / path
        except (TypeError, ValueError):
            return path
    return path


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _shadow_content_summary(payload: dict[str, Any]) -> dict[str, Any]:
    registry = _as_dict(payload.get("registry"))
    continuity = _as_dict(payload.get("continuity"))
    setback = _as_dict(payload.get("setback_recovery"))
    invariants = _as_dict(payload.get("invariants"))
    metadata = _as_dict(payload.get("metadata"))
    steps_raw = payload.get("steps")
    steps: list[Any] = steps_raw if isinstance(steps_raw, list) else []
    storylet_obs = _as_dict(payload.get("storylet_observations"))
    selected_ids = storylet_obs.get("selected_storylet_ids")
    selected_count = len(selected_ids) if isinstance(selected_ids, list) else 0
    return {
        "overall_verdict": str(payload.get("overall_verdict") or ""),
        "step_count": len(steps),
        "registry_counts": {
            "canon": registry.get("canon_count"),
            "storylet": registry.get("storylet_count"),
        },
        "continuity": {
            "main_arc_id": continuity.get("main_arc_id"),
            "side_arc_ids": _bound_id_list(continuity.get("side_arc_ids")),
            "ambient_arc_ids": _bound_id_list(continuity.get("ambient_arc_ids")),
        },
        "setback_recovery": {
            "major_setback_count": setback.get("major_setback_count"),
            "recovery_observed": setback.get("recovery_observed"),
        },
        "invariant_verdict": invariants.get("verdict"),
        "selected_storylet_count": selected_count,
        "report_essential_hash": str(metadata.get("report_essential_hash") or ""),
        "pack_id": str(metadata.get("pack_id") or ""),
        # Deliberately omit steps body, social raw fields, full invariants.
    }


def _bound_id_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(x) for x in value if str(x or "").strip()][:_LIST_CAP]


def _bound_str(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


# Exported for tests that assert response marker hygiene.
FORBIDDEN_RESPONSE_MARKERS = _FORBIDDEN_RESPONSE_MARKERS
