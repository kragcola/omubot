import asyncio

import pytest
from loguru import logger

from kernel.types import PluginContext, ReplyContext
from plugins.memo.plugin import MemoPlugin


@pytest.mark.asyncio
async def test_shutdown_cancels_and_clears_pending_extractions() -> None:
    plugin = MemoPlugin()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocking_extraction() -> None:
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(blocking_extraction())
    plugin._pending_extractions.add(task)

    try:
        await entered.wait()
        await plugin.on_shutdown(PluginContext())

        assert task.done()
        assert task.cancelled()
        assert cancelled.is_set()
        assert plugin._pending_extractions == set()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_extraction_is_reaped_and_logged() -> None:
    class FailingMemoExtractor:
        async def extract_after_turn(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("memo task failed")

    plugin = MemoPlugin()
    plugin._memo_extractor = FailingMemoExtractor()
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="ERROR")

    try:
        await plugin.on_post_reply(
            ReplyContext(
                session_id="s",
                group_id="1",
                user_id="u",
                reply_content="r",
                user_msg="m",
            )
        )

        while plugin._pending_extractions:
            await asyncio.sleep(0)

        captured = "".join(messages)
        assert "memo extraction task failed" in captured
        assert "memo task failed" in captured
    finally:
        logger.remove(sink_id)
        await plugin.on_shutdown(PluginContext())
