"""Lightweight speculative task executor for read-only prefetch work."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class SpeculativeExecutor:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    async def __aenter__(self) -> SpeculativeExecutor:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def submit(
        self,
        coro_func: Callable[..., Awaitable[Any]],
        *args: Any,
        timeout: float = 0.5,
        **kwargs: Any,
    ) -> asyncio.Task[Any]:
        async def _runner() -> Any:
            return await asyncio.wait_for(coro_func(*args, **kwargs), timeout=max(0.01, float(timeout)))

        task = asyncio.create_task(_runner())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def cancel(self, task: asyncio.Task[Any] | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
