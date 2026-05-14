"""SSE endpoint — real-time event push for admin dashboard.

Streams to subscribed clients:

- ``log`` — buffered loguru entries pushed via ``log_sink_queue``
- ``group_message`` — per-message events from ``services.admin_events``
- ``group_activity`` — full ``MessageLog.group_activity_summary`` snapshot
  (every ``GROUP_ACTIVITY_SNAPSHOT_INTERVAL`` seconds, for reconciliation)
- ``scheduler`` — slot snapshot (when scheduler is wired)
- ``heartbeat`` — every loop tick so the client can detect a dead connection

The loop wakes every second so incremental updates land within ~1s of an
inbound QQ message, while the heavier snapshot only fires every 30s.

Usage:
    from admin.routes.api.events import log_sink_queue, install_loguru_sink
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from services.admin_events import subscribe as subscribe_admin_events
from services.admin_events import unsubscribe as unsubscribe_admin_events
from services.errors import RuntimeErrorStore

GROUP_ACTIVITY_SNAPSHOT_INTERVAL = 30.0
LOOP_TICK_SECONDS = 1.0
HEARTBEAT_INTERVAL = 10.0
ACTIVITY_WINDOW_SECONDS = 24 * 3600

# Global queue that loguru sink pushes into (populated in Phase 2 wiring)
log_sink_queue: asyncio.Queue[dict[str, Any]] | None = None
runtime_error_store: RuntimeErrorStore | None = None
_loguru_sink_id: int | None = None


def create_events_router(
    *,
    scheduler: Any = None,
    message_log: Any = None,
    ctx: Any = None,
) -> APIRouter:
    router = APIRouter()

    async def _activity_snapshot() -> dict[str, Any] | None:
        log = message_log
        if log is None and ctx is not None:
            log = getattr(ctx, "message_log", None)
        if log is None or not hasattr(log, "group_activity_summary"):
            return None
        since = datetime.now().timestamp() - ACTIVITY_WINDOW_SECONDS
        try:
            summary = await log.group_activity_summary(since=since)
        except Exception:
            return None
        return {
            "ts": time.time(),
            "window_seconds": ACTIVITY_WINDOW_SECONDS,
            "groups": summary,
        }

    @router.get("/events")
    async def events(request: Request):
        """SSE endpoint — pushes heartbeat, logs, group events, scheduler updates."""

        async def event_stream():
            group_queue = subscribe_admin_events()
            last_snapshot_at = 0.0
            last_heartbeat_at = 0.0
            try:
                while True:
                    if await request.is_disconnected():
                        break

                    # 1) Drain log queue (non-blocking)
                    logs_batch: list[dict] = []
                    queue = log_sink_queue
                    if queue is not None:
                        while True:
                            try:
                                entry = queue.get_nowait()
                                logs_batch.append(entry)
                            except asyncio.QueueEmpty:
                                break
                    if logs_batch:
                        yield (
                            f"event: log\ndata: "
                            f"{json.dumps({'entries': logs_batch}, ensure_ascii=False)}\n\n"
                        )

                    # 2) Drain group_message events (non-blocking)
                    group_events: list[dict[str, Any]] = []
                    while True:
                        try:
                            event = group_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        group_events.append(event)
                    for event in group_events:
                        if event.get("type") == "group_message":
                            payload = json.dumps(event, ensure_ascii=False)
                            yield f"event: group_message\ndata: {payload}\n\n"

                    # 3) Periodic activity snapshot for reconciliation
                    now = time.time()
                    if now - last_snapshot_at >= GROUP_ACTIVITY_SNAPSHOT_INTERVAL:
                        snapshot = await _activity_snapshot()
                        if snapshot is not None:
                            payload = json.dumps(snapshot, ensure_ascii=False)
                            yield f"event: group_activity\ndata: {payload}\n\n"
                        last_snapshot_at = now

                    # 4) Scheduler state (cheap, gated by interval as well)
                    if scheduler is not None and now - last_heartbeat_at >= HEARTBEAT_INTERVAL:
                        with contextlib.suppress(Exception):
                            slots = (
                                scheduler.get_all_slots()
                                if hasattr(scheduler, "get_all_slots")
                                else {}
                            )
                            yield f"event: scheduler\ndata: {json.dumps({'slots': slots})}\n\n"

                    # 5) Heartbeat
                    if now - last_heartbeat_at >= HEARTBEAT_INTERVAL:
                        yield f"event: heartbeat\ndata: {json.dumps({'ts': now})}\n\n"
                        last_heartbeat_at = now

                    await asyncio.sleep(LOOP_TICK_SECONDS)
            except asyncio.CancelledError:
                pass
            finally:
                unsubscribe_admin_events(group_queue)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


def install_loguru_sink(error_store: RuntimeErrorStore | None = None) -> None:
    """Install a loguru sink that pushes log entries into the SSE queue.

    Call this during bot startup after the events router is created.
    """
    global _loguru_sink_id, log_sink_queue, runtime_error_store
    if log_sink_queue is None:
        log_sink_queue = asyncio.Queue(maxsize=500)
    if error_store is not None:
        runtime_error_store = error_store

    from loguru import logger

    if _loguru_sink_id is not None:
        return

    def _sink(message):
        record = message.record
        level_name = record["level"].name
        channel = record["extra"].get("channel", "")
        log_message = record["message"]
        entry = {
            "ts": record["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if record["time"] else "",
            "level": level_name,
            "channel": channel,
            "message": log_message,
        }
        with contextlib.suppress(asyncio.QueueFull):
            log_sink_queue.put_nowait(entry)
        if runtime_error_store is not None:
            runtime_error_store.record(
                level=level_name,
                channel=channel,
                logger_name=record["name"],
                message=log_message,
                ts=record["time"].timestamp() if record["time"] else None,
            )

    _loguru_sink_id = logger.add(
        _sink,
        level="DEBUG",
        format="{message}",
        filter=lambda r: bool(r["extra"].get("channel", "")) or r["level"].no >= 30,
    )
