"""工具注册表：统一管理、查找、转换工具。"""

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Iterable
from typing import Any

from loguru import logger

from services.tools.base import Tool
from services.tools.context import ToolContext


class _ToolDeadlineExceeded(Exception):
    """Raised only when ToolRegistry reaches its own wall-clock deadline."""


class ToolRegistry:
    def __init__(self, *, default_timeout_seconds: float = 30.0) -> None:
        if default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be positive")
        self._tools: dict[str, Tool] = {}
        self._default_timeout_seconds = float(default_timeout_seconds)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def replace_all(self, tools: Iterable[Tool]) -> None:
        """Validate a complete candidate registry before swapping it live."""
        candidate: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in candidate:
                raise ValueError(f"tool already registered: {tool.name}")
            candidate[tool.name] = tool
        self._tools = candidate

    def snapshot_tools(self) -> tuple[Tool, ...]:
        """Return the current tools in stable registry order."""
        return tuple(self._tools.values())

    def merge_all(self, tools: Iterable[Tool]) -> None:
        """Validate extensions against the live registry before swapping."""
        candidate = dict(self._tools)
        for tool in tools:
            if tool.name in candidate:
                raise ValueError(f"tool already registered: {tool.name}")
            candidate[tool.name] = tool
        self._tools = candidate

    def register_interaction_tools(
        self,
        *,
        resolved_humanization: Any | None = None,
        profile: str | None = None,
        passive: bool = False,
    ) -> None:
        from services.tools.interaction_tools import build_interaction_tools

        for tool in build_interaction_tools(
            resolved_humanization=resolved_humanization,
            profile=profile,
            passive=passive,
        ):
            self.register(tool)

    def clear(self) -> None:
        self._tools.clear()

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def to_openai_tools(self) -> list[dict[str, Any]]:
        return [t.to_openai_tool() for t in self._tools.values()]

    async def call(self, name: str, arguments: str, ctx: ToolContext) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"未知工具: {name}"
        timeout_seconds = self._timeout_seconds(tool)
        try:
            kwargs: dict[str, Any] = json.loads(arguments) if arguments else {}
            return await self._await_with_deadline(
                tool.execute(ctx, **kwargs),
                timeout_seconds=timeout_seconds,
            )
        except _ToolDeadlineExceeded:
            logger.warning("tool timeout | name={} timeout={:.3f}s", name, timeout_seconds)
            return "工具执行超时，请稍后重试"
        except Exception:
            logger.exception("tool error | name={}", name)
            return "工具执行出错，请稍后重试"

    def _timeout_seconds(self, tool: Tool) -> float:
        raw_timeout = getattr(tool, "timeout_seconds", self._default_timeout_seconds)
        try:
            timeout_seconds = float(raw_timeout)
        except (TypeError, ValueError):
            return self._default_timeout_seconds
        return timeout_seconds if timeout_seconds > 0 else self._default_timeout_seconds

    async def _await_with_deadline(
        self,
        awaitable: Awaitable[str],
        *,
        timeout_seconds: float,
    ) -> str:
        task = asyncio.ensure_future(awaitable)
        try:
            done, _pending = await asyncio.wait({task}, timeout=timeout_seconds)
        except asyncio.CancelledError:
            self._cancel_uncooperative_task(task)
            raise
        if task in done:
            return task.result()

        self._cancel_uncooperative_task(task)
        await asyncio.sleep(0)
        raise _ToolDeadlineExceeded

    @staticmethod
    def _cancel_uncooperative_task(task: asyncio.Future[str]) -> None:
        task.add_done_callback(ToolRegistry._consume_task_result)
        task.cancel()
        loop = task.get_loop()

        def cancel_again(remaining: int) -> None:
            if task.done():
                return
            task.cancel()
            if remaining > 0:
                loop.call_later(0.01, cancel_again, remaining - 1)
            else:
                logger.error("tool task ignored repeated cancellation")

        loop.call_later(0.01, cancel_again, 7)

    @staticmethod
    def _consume_task_result(task: asyncio.Future[str]) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            task.result()

    @property
    def empty(self) -> bool:
        return len(self._tools) == 0
