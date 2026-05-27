"""Inbound notify coalescer that delays scheduler fire without losing timeline rows."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

FlushCallback = Callable[[list[Any]], Awaitable[None] | None]


@dataclass(slots=True)
class CoalesceBucket:
    messages: deque[Any] = field(default_factory=deque)
    created_at: float = 0.0
    idle_generation: int = 0
    idle_timer: asyncio.Task[None] | None = None
    max_window_timer: asyncio.Task[None] | None = None
    on_flush: FlushCallback | None = None


class MessageCoalescer:
    def __init__(
        self,
        *,
        idle_window_seconds: float = 5.0,
        max_window_seconds: float = 12.0,
    ) -> None:
        self._idle_window_seconds = max(0.1, float(idle_window_seconds))
        self._max_window_seconds = max(self._idle_window_seconds, float(max_window_seconds))
        self._buckets: dict[tuple[str, str], CoalesceBucket] = {}
        self._closed = False

    async def enqueue(
        self,
        group_id: str,
        sender_id: str,
        message: Any,
        *,
        on_flush: FlushCallback | None = None,
    ) -> None:
        if self._closed:
            await self._invoke_flush(on_flush, [message])
            return
        key = (str(group_id or "").strip(), str(sender_id or "").strip())
        bucket = self._buckets.get(key)
        now = time.monotonic()
        if bucket is None:
            bucket = CoalesceBucket(created_at=now, on_flush=on_flush)
            self._buckets[key] = bucket
            bucket.max_window_timer = asyncio.create_task(self._max_timer(key))
        bucket.idle_generation += 1
        if bucket.idle_timer is not None and not bucket.idle_timer.done():
            bucket.idle_timer.cancel()
        bucket.messages.append(message)
        if on_flush is not None:
            bucket.on_flush = on_flush
        generation = bucket.idle_generation
        bucket.idle_timer = asyncio.create_task(self._idle_timer(key, generation))

    async def flush(self, group_id: str, sender_id: str) -> list[Any]:
        key = (str(group_id or "").strip(), str(sender_id or "").strip())
        bucket = self._buckets.pop(key, None)
        if bucket is None:
            return []
        if bucket.idle_timer is not None and bucket.idle_timer is not asyncio.current_task():
            bucket.idle_timer.cancel()
        if bucket.max_window_timer is not None and bucket.max_window_timer is not asyncio.current_task():
            bucket.max_window_timer.cancel()
        messages = list(bucket.messages)
        await self._invoke_flush(bucket.on_flush, messages)
        return messages

    async def discard(self, group_id: str, sender_id: str) -> list[Any]:
        key = (str(group_id or "").strip(), str(sender_id or "").strip())
        bucket = self._buckets.pop(key, None)
        if bucket is None:
            return []
        if bucket.idle_timer is not None and bucket.idle_timer is not asyncio.current_task():
            bucket.idle_timer.cancel()
        if bucket.max_window_timer is not None and bucket.max_window_timer is not asyncio.current_task():
            bucket.max_window_timer.cancel()
        return list(bucket.messages)

    async def close(self) -> None:
        self._closed = True
        keys = list(self._buckets.keys())
        for group_id, sender_id in keys:
            await self.flush(group_id, sender_id)

    async def _idle_timer(self, key: tuple[str, str], generation: int) -> None:
        try:
            await asyncio.sleep(self._idle_window_seconds)
            bucket = self._buckets.get(key)
            if bucket is None or bucket.idle_generation != generation:
                return
            await self.flush(*key)
        except asyncio.CancelledError:
            bucket = self._buckets.get(key)
            if bucket is not None and bucket.idle_generation == generation:
                await self.flush(*key)
            raise

    async def _max_timer(self, key: tuple[str, str]) -> None:
        try:
            await asyncio.sleep(self._max_window_seconds)
            await self.flush(*key)
        except asyncio.CancelledError:
            raise

    async def _invoke_flush(self, callback: FlushCallback | None, messages: list[Any]) -> None:
        if callback is None or not messages:
            return
        result = callback(messages)
        if inspect.isawaitable(result):
            await result
