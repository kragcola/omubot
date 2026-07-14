import asyncio

import pytest
from loguru import logger

from kernel.types import PluginContext
from plugins.food.plugin import FoodPlugin


@pytest.mark.asyncio
async def test_shutdown_cancels_and_clears_feedback_task_state() -> None:
    plugin = FoodPlugin()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocking_feedback() -> None:
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(blocking_feedback())
    plugin._feedback_tasks.add(task)
    plugin._feedback_running.add(("user-1", "group-1"))

    try:
        await entered.wait()
        await plugin.on_shutdown(PluginContext())

        assert task.done()
        assert task.cancelled()
        assert cancelled.is_set()
        assert plugin._feedback_tasks == set()
        assert plugin._feedback_running == set()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_feedback_task_is_reclaimed_and_logged() -> None:
    plugin = FoodPlugin()
    messages: list[str] = []
    sink_id = logger.add(
        lambda message: messages.append(str(message)),
        level="ERROR",
        format="{message}",
    )

    async def failing_feedback() -> None:
        raise RuntimeError("food task failed")

    task = asyncio.create_task(failing_feedback())
    plugin._feedback_tasks.add(task)
    task.add_done_callback(plugin._on_feedback_task_done)

    try:
        while not task.done():
            await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert task not in plugin._feedback_tasks
        logged = "\n".join(messages)
        assert "feedback task failed" in logged
        assert "food task failed" in logged
    finally:
        logger.remove(sink_id)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
