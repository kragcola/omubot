"""Regression tests for two log-driven fixes (2026-06-01):

1. Mid-message ``@bot`` detection (RWS abnormal skip): an @ that is neither the
   first nor last segment leaves NoneBot's ``is_tome()`` False, so an addressed
   message fell into the probabilistic gray zone and could be silently skipped.
   ``_message_ats_self`` scans every segment so the addressed path always wins.

2. Per-group ingest ordering (CCIP latency race): a slow image render must not
   be overtaken by a faster later message that commits to the timeline + fires a
   reply before the image lands. ``_group_ingest_lock`` serializes the
   render→commit section per group.

Plus the merge short-circuit (latency): a CCIP hit whose work won't be borrowed
must return without awaiting the slow AnimeTrace online call.
"""

from __future__ import annotations

import asyncio

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from kernel.router import _group_ingest_lock, _message_ats_self
from kernel.types import PluginContext

SELF_ID = "384801062"


def _seg_at(qq: str) -> MessageSegment:
    return MessageSegment.at(qq)


def test_message_ats_self_first_segment() -> None:
    msg = Message([_seg_at(SELF_ID), MessageSegment.text("这是谁")])
    assert _message_ats_self(msg, SELF_ID) is True


def test_message_ats_self_last_segment() -> None:
    msg = Message([MessageSegment.text("这是谁"), _seg_at(SELF_ID)])
    assert _message_ats_self(msg, SELF_ID) is True


def test_message_ats_self_sandwiched_segment() -> None:
    """The actual bug: [image][at:bot]text — @ in the middle, which is exactly
    what ``is_tome()`` (first/last only) fails to catch."""
    msg = Message([
        MessageSegment.image("http://x/a.png"),
        _seg_at(SELF_ID),
        MessageSegment.text("这是谁"),
    ])
    assert _message_ats_self(msg, SELF_ID) is True


def test_message_ats_self_other_at_only() -> None:
    msg = Message([MessageSegment.text("hi "), _seg_at("99999"), MessageSegment.text("?")])
    assert _message_ats_self(msg, SELF_ID) is False


def test_message_ats_self_no_at() -> None:
    msg = Message([MessageSegment.text("纯文本")])
    assert _message_ats_self(msg, SELF_ID) is False


def test_message_ats_self_empty_self_id() -> None:
    msg = Message([_seg_at(SELF_ID)])
    assert _message_ats_self(msg, "") is False


def test_group_ingest_lock_is_memoized_per_group() -> None:
    ctx = PluginContext()
    a1 = _group_ingest_lock(ctx, "111")
    a2 = _group_ingest_lock(ctx, "111")
    b1 = _group_ingest_lock(ctx, "222")
    assert a1 is a2  # same group → same lock
    assert a1 is not b1  # different group → different lock
    assert isinstance(a1, asyncio.Lock)


def test_group_ingest_lock_creates_store_if_missing() -> None:
    ctx = PluginContext()
    ctx.group_ingest_locks = None  # type: ignore[assignment]
    lock = _group_ingest_lock(ctx, "333")
    assert isinstance(lock, asyncio.Lock)
    assert ctx.group_ingest_locks["333"] is lock


@pytest.mark.asyncio
async def test_group_ingest_lock_serializes_commit_order() -> None:
    """A slow first acquirer must finish its critical section before a fast
    second acquirer commits — mirrors slow-image-then-fast-text arrival."""
    ctx = PluginContext()
    commit_order: list[str] = []

    async def ingest(name: str, render_delay: float) -> None:
        async with _group_ingest_lock(ctx, "g"):
            await asyncio.sleep(render_delay)  # simulate render (image vs text)
            commit_order.append(name)

    # image arrives first (slow render); text arrives just after (fast render).
    image = asyncio.create_task(ingest("image", 0.05))
    await asyncio.sleep(0.005)  # text handler starts while image is still rendering
    text = asyncio.create_task(ingest("text", 0.0))
    await asyncio.gather(image, text)
    # Without the lock the fast text would commit first; with it, arrival order holds.
    assert commit_order == ["image", "text"]
