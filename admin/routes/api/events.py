"""SSE endpoint — real-time event push for admin dashboard.

Pushes:
- Heartbeat (every 5s)
- Log lines (loguru sink → asyncio.Queue → SSE)
- Scheduler state changes (polled every 10s when scheduler is wired)

Usage:
    from admin.routes.api.events import log_sink_queue
    # Add loguru sink that pushes to log_sink_queue
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from services.errors import RuntimeErrorStore

# Global queue that loguru sink pushes into (populated in Phase 2 wiring)
log_sink_queue: asyncio.Queue[dict[str, Any]] | None = None
runtime_error_store: RuntimeErrorStore | None = None
_loguru_sink_id: int | None = None


def create_events_router(
    *,
    scheduler: Any = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/events")
    async def events(request: Request):
        """SSE endpoint — pushes heartbeat, logs, and scheduler updates."""

        async def event_stream():
            try:
                while True:
                    if await request.is_disconnected():
                        break

                    # Drain log queue (non-blocking)
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
                        yield f"event: log\ndata: {json.dumps({'entries': logs_batch}, ensure_ascii=False)}\n\n"

                    # Scheduler state (polled)
                    if scheduler is not None:
                        try:
                            slots = scheduler.get_all_slots() if hasattr(scheduler, "get_all_slots") else {}
                            yield f"event: scheduler\ndata: {json.dumps({'slots': slots})}\n\n"
                        except Exception:
                            pass

                    # Heartbeat
                    yield f"event: heartbeat\ndata: {json.dumps({'ts': time.time()})}\n\n"

                    await asyncio.sleep(10)
            except asyncio.CancelledError:
                pass

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
