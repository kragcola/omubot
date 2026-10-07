"""Small asyncio resource budget for model work."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal, TypeGuard

SlotKind = Literal["reply", "thinker", "vision"]


def _is_strict_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


class ModelBudget:
    """Bound concurrent model work and reserve capacity for Thinker tasks."""

    def __init__(self, limit: int, reserve: int) -> None:
        if not _is_strict_int(limit) or not 2 <= limit <= 32:
            raise ValueError("limit must be an integer from 2 through 32")
        if not _is_strict_int(reserve) or not 1 <= reserve < limit:
            raise ValueError("reserve must be an integer from 1 through limit - 1")

        self._total = asyncio.Semaphore(limit)
        self._regular = asyncio.Semaphore(limit - reserve)
        self._active = 0
        self._waiting = 0
        self._reply_active = 0
        self._thinker_active = 0

    @asynccontextmanager
    async def slot(self, kind: SlotKind) -> AsyncGenerator[None, None]:
        """Acquire capacity for one model operation and release it on exit."""
        if kind not in {"reply", "thinker", "vision"}:
            raise ValueError("unknown slot kind")

        regular_acquired = False
        total_acquired = False
        waiting = True
        self._waiting += 1
        try:
            # Keep the regular gate first so regular work cannot consume total
            # capacity while it is waiting for its reserved share.
            if kind != "thinker":
                await self._regular.acquire()
                regular_acquired = True
            await self._total.acquire()
            total_acquired = True
        except BaseException:
            if waiting:
                self._waiting -= 1
                waiting = False
            if total_acquired:
                self._total.release()
            if regular_acquired:
                self._regular.release()
            raise

        self._waiting -= 1
        waiting = False
        self._active += 1
        if kind == "thinker":
            self._thinker_active += 1
        else:
            self._reply_active += 1

        try:
            yield
        finally:
            self._active -= 1
            if kind == "thinker":
                self._thinker_active -= 1
            else:
                self._reply_active -= 1
            self._total.release()
            if regular_acquired:
                self._regular.release()

    def snapshot(self) -> dict[str, int]:
        """Return a detached view of current budget usage."""
        return {
            "active": self._active,
            "waiting": self._waiting,
            "reply_active": self._reply_active,
            "thinker_active": self._thinker_active,
        }
