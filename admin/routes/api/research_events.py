"""Read-only runtime status for raw research event capture."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

_METRIC_KEYS = (
    "received",
    "enqueued",
    "dropped_queue_full",
    "write_error",
    "pending",
    "persisted",
    "duplicate",
    "error",
)
_FAILURE_KEYS = ("dropped_queue_full", "write_error", "error")


def _zero_metrics() -> dict[str, int]:
    return {key: 0 for key in _METRIC_KEYS}


def create_research_events_router(*, ctx: Any = None) -> APIRouter:
    router = APIRouter(prefix="/research-events", tags=["research-events"])

    @router.get("/status")
    async def research_event_status() -> dict[str, object]:
        capture = getattr(ctx, "research_event_capture", None) if ctx is not None else None
        if capture is None:
            policy = getattr(getattr(ctx, "config", None), "research_event_capture", None)
            if bool(getattr(policy, "enabled", False)):
                return {
                    "available": False,
                    "status": "unavailable",
                    "reason": "capture_not_initialized",
                    **_zero_metrics(),
                }
            return {
                "available": False,
                "status": "disabled",
                **_zero_metrics(),
            }

        snapshot_fn = getattr(capture, "metrics_snapshot", None)
        try:
            if not callable(snapshot_fn):
                raise TypeError("capture metrics snapshot is unavailable")
            snapshot = snapshot_fn()
            metrics = {
                key: int(getattr(snapshot, key, 0) or 0)
                for key in _METRIC_KEYS
            }
        except Exception:
            return {
                "available": False,
                "status": "error",
                "reason": "metrics_snapshot_failed",
                **_zero_metrics(),
            }

        status = "degraded" if any(metrics[key] > 0 for key in _FAILURE_KEYS) else "healthy"
        return {
            "available": True,
            "status": status,
            **metrics,
        }

    return router
