"""Lightweight in-process event broker for the admin SSE stream.

Why this lives in ``services/`` rather than ``admin/``: the kernel router
must publish events without importing anything under ``admin/`` (admin is a
consumer of kernel/services, not the other way around). Both layers can
import ``services``, so this module is the natural seam.

Usage:

    from services.admin_events import publish_group_message, subscribe

    # Publisher (kernel/router.py)
    publish_group_message(group_id="123", user_id="456", ts=time.time(),
                          is_bot=False, presence_mode="active")

    # Consumer (admin SSE endpoint)
    queue = subscribe()
    try:
        while True:
            event = await queue.get()
            ...
    finally:
        unsubscribe(queue)

The broker uses bounded per-subscriber queues so a stalled SSE client can
only drop its own events; publishers never block. The current implementation
is single-process; if we ever need multi-worker fan-out, swap the in-memory
list for Redis pub/sub behind the same API.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

_QUEUE_MAXSIZE = 256

_subscribers: list[asyncio.Queue[dict[str, Any]]] = []


def subscribe() -> asyncio.Queue[dict[str, Any]]:
    """Register a new subscriber queue and return it."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    _subscribers.append(queue)
    return queue


def unsubscribe(queue: asyncio.Queue[dict[str, Any]]) -> None:
    """Remove a subscriber queue. Safe to call multiple times."""
    with contextlib.suppress(ValueError):
        _subscribers.remove(queue)


def _publish(event: dict[str, Any]) -> None:
    """Fan an event out to every subscriber, never blocking."""
    if not _subscribers:
        return
    for queue in list(_subscribers):
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(event)


def publish_group_message(
    *,
    group_id: str,
    user_id: str,
    ts: float | None = None,
    is_bot: bool = False,
    presence_mode: str | None = None,
    consumed: bool = False,
) -> None:
    """Publish a group_message event for the SSE stream.

    Called from the kernel router on every inbound group message that
    survives self-loop filtering. Cheap and fire-and-forget.
    """
    _publish(
        {
            "type": "group_message",
            "group_id": str(group_id),
            "user_id": str(user_id),
            "ts": float(ts) if ts is not None else time.time(),
            "is_bot": bool(is_bot),
            "presence_mode": presence_mode,
            "consumed": bool(consumed),
        }
    )


def publish_block_trace_recorded(
    *,
    request_id: str,
    count: int,
    accepted: int = 0,
    trimmed: int = 0,
    rejected: int = 0,
    shadow_only: int = 0,
) -> None:
    """Publish a lightweight block_trace event for the SSE stream.

    Carries only counts, not the full trace payload — the BlockTraceView
    re-fetches /alignment, /stats, /recent on receipt. This keeps the SSE
    queue small even under high prompt-block volume.
    """
    _publish(
        {
            "type": "block_trace_recorded",
            "request_id": request_id,
            "count": int(count),
            "accepted": int(accepted),
            "trimmed": int(trimmed),
            "rejected": int(rejected),
            "shadow_only": int(shadow_only),
            "ts": time.time(),
        }
    )
