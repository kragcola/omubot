"""Tests for per-group visible send queue."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.send_queue import GroupSendQueue, ReplySegmentBatch


async def test_group_send_queue_preserves_text_and_message_order() -> None:
    sent: list[tuple[int, object]] = []

    async def send_group_msg(*, group_id: int, message: object) -> None:
        sent.append((group_id, message))

    bot = MagicMock()
    bot.send_group_msg = AsyncMock(side_effect=send_group_msg)
    queue = GroupSendQueue(bot=bot)

    marker = object()
    first = asyncio.create_task(queue.send_group_text("123", "hello", humanize="skip"))
    await asyncio.sleep(0)
    second = asyncio.create_task(queue.send_group_message("123", marker, description="sticker"))

    await asyncio.gather(first, second)

    assert [item[0] for item in sent] == [123, 123]
    assert str(sent[0][1]) == "hello"
    assert sent[1][1] is marker

    await queue.close()


async def test_group_send_queue_drops_muted_group() -> None:
    bot = MagicMock()
    bot.send_group_msg = AsyncMock()
    queue = GroupSendQueue(bot=bot, muted_checker=lambda gid: gid == "123")

    elapsed = await queue.send_group_text("123", "hello", humanize="skip")

    assert elapsed == 0.0
    bot.send_group_msg.assert_not_awaited()

    await queue.close()


async def test_enqueue_group_text_returns_before_delivery() -> None:
    release_send = asyncio.Event()
    sent: list[object] = []

    async def send_group_msg(*, group_id: int, message: object) -> None:
        del group_id
        await release_send.wait()
        sent.append(message)

    bot = MagicMock()
    bot.send_group_msg = AsyncMock(side_effect=send_group_msg)
    queue = GroupSendQueue(bot=bot)

    future = await queue.enqueue_group_text("123", "hello", humanize="skip")

    assert not future.done()
    assert sent == []

    release_send.set()
    assert await future >= 0.0
    assert str(sent[0]) == "hello"

    await queue.close()


async def test_reply_batch_stays_contiguous_before_later_items() -> None:
    sent: list[object] = []

    async def send_group_msg(*, group_id: int, message: object) -> None:
        sent.append(message)

    bot = MagicMock()
    bot.send_group_msg = AsyncMock(side_effect=send_group_msg)
    queue = GroupSendQueue(bot=bot)
    marker = object()

    handle = await queue.enqueue_reply_batch(
        ReplySegmentBatch(group_id="123", segments=["first", "second"])
    )
    later = asyncio.create_task(queue.send_group_message("123", marker, description="sticker"))

    queue_wait = await handle.started
    first_elapsed = await handle.first_segment_sent
    total_elapsed = await handle.done
    interleave_count = await handle.interleave_count
    await later

    assert queue_wait >= 0.0
    assert first_elapsed >= 0.0
    assert total_elapsed >= first_elapsed
    assert interleave_count == 0
    assert str(sent[0]) == "first"
    assert str(sent[1]) == "second"
    assert sent[2] is marker

    await queue.close()


async def test_reply_batch_can_yield_between_segments_for_later_visible_text() -> None:
    sent: list[object] = []
    first_sent = asyncio.Event()
    release_first_send = asyncio.Event()
    allow_second = asyncio.Event()

    async def send_group_msg(*, group_id: int, message: object) -> None:
        del group_id
        sent.append(message)
        if str(message) == "first":
            first_sent.set()
            await release_first_send.wait()
        if str(message) == "second":
            await allow_second.wait()

    bot = MagicMock()
    bot.send_group_msg = AsyncMock(side_effect=send_group_msg)
    queue = GroupSendQueue(bot=bot)

    handle = await queue.enqueue_reply_batch(
        ReplySegmentBatch(
            group_id="123",
            segments=["first", "second", "third"],
            allow_interleaved_between_segments=True,
        )
    )
    await first_sent.wait()
    interleaved = await queue.enqueue_group_text(
        "123",
        "对",
        humanize="skip",
        description="element_detector",
    )
    release_first_send.set()

    assert await interleaved >= 0.0
    allow_second.set()
    await handle.done
    assert await handle.interleave_count == 1

    assert [str(item) for item in sent] == ["first", "对", "second", "third"]

    await queue.close()


async def test_reply_batch_waits_segment_gap_before_yielding() -> None:
    sent: list[tuple[str, float]] = []
    first_sent = asyncio.Event()

    async def send_group_msg(*, group_id: int, message: object) -> None:
        del group_id
        sent.append((str(message), asyncio.get_running_loop().time()))
        if str(message) == "first":
            first_sent.set()

    bot = MagicMock()
    bot.send_group_msg = AsyncMock(side_effect=send_group_msg)
    queue = GroupSendQueue(bot=bot)

    handle = await queue.enqueue_reply_batch(
        ReplySegmentBatch(
            group_id="123",
            segments=["first", "second"],
            allow_interleaved_between_segments=True,
            inter_segment_delay_s=0.02,
        )
    )
    await first_sent.wait()
    interleaved = await queue.enqueue_group_text(
        "123",
        "对",
        humanize="skip",
        description="element_detector",
    )

    assert await interleaved >= 0.0
    await handle.done
    assert await handle.interleave_count == 1
    assert [item for item, _ in sent] == ["first", "对", "second"]

    await queue.close()


async def test_reply_batch_first_segment_failure_sets_both_futures() -> None:
    bot = MagicMock()
    bot.send_group_msg = AsyncMock(side_effect=RuntimeError("network down"))
    queue = GroupSendQueue(bot=bot)

    handle = await queue.enqueue_reply_batch(
        ReplySegmentBatch(group_id="123", segments=["first", "second"])
    )

    try:
        with pytest.raises(RuntimeError, match="network down"):
            await handle.first_segment_sent
        with pytest.raises(RuntimeError, match="network down"):
            await handle.done
    finally:
        await queue.close()


async def test_reply_batch_tail_failure_keeps_first_segment_signal() -> None:
    bot = MagicMock()
    bot.send_group_msg = AsyncMock(side_effect=[None, RuntimeError("tail failed")])
    queue = GroupSendQueue(bot=bot)

    handle = await queue.enqueue_reply_batch(
        ReplySegmentBatch(group_id="123", segments=["first", "second"])
    )

    try:
        assert await handle.first_segment_sent >= 0.0
        with pytest.raises(RuntimeError, match="tail failed"):
            await handle.done
    finally:
        await queue.close()
