from __future__ import annotations

import asyncio

import pytest

from services.coalesce import MessageCoalescer


async def _wait_until(predicate, *, timeout: float = 0.2) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_poll(), timeout=timeout)


@pytest.mark.asyncio
async def test_coalesce_idle_window_flushes_messages() -> None:
    coalescer = MessageCoalescer(idle_window_seconds=0.01, max_window_seconds=0.05)
    flushed: list[list[str]] = []

    async def _on_flush(messages: list[object]) -> None:
        flushed.append([str(item) for item in messages])

    await coalescer.enqueue("100", "u1", "a", on_flush=_on_flush)
    await _wait_until(lambda: bool(flushed))

    assert flushed == [["a"]]


@pytest.mark.asyncio
async def test_coalesce_max_window_forces_flush() -> None:
    coalescer = MessageCoalescer(idle_window_seconds=0.1, max_window_seconds=0.04)
    flushed: list[list[str]] = []

    async def _on_flush(messages: list[object]) -> None:
        flushed.append([str(item) for item in messages])

    await coalescer.enqueue("100", "u1", "a", on_flush=_on_flush)
    await asyncio.sleep(0.01)
    await coalescer.enqueue("100", "u1", "b", on_flush=_on_flush)
    await _wait_until(lambda: bool(flushed))

    assert flushed == [["a", "b"]]


@pytest.mark.asyncio
async def test_coalesce_isolated_per_sender_and_group() -> None:
    coalescer = MessageCoalescer(idle_window_seconds=0.01, max_window_seconds=0.05)
    flushed: list[tuple[str, ...]] = []

    async def _mk_flush(label: str):
        async def _on_flush(messages: list[object]) -> None:
            flushed.append((label, *(str(item) for item in messages)))
        return _on_flush

    await coalescer.enqueue("100", "u1", "a", on_flush=await _mk_flush("100:u1"))
    await coalescer.enqueue("100", "u2", "b", on_flush=await _mk_flush("100:u2"))
    await coalescer.enqueue("200", "u1", "c", on_flush=await _mk_flush("200:u1"))
    await _wait_until(lambda: len(flushed) == 3)

    assert sorted(flushed) == [
        ("100:u1", "a"),
        ("100:u2", "b"),
        ("200:u1", "c"),
    ]


@pytest.mark.asyncio
async def test_coalesce_discard_returns_buffer_without_flushing() -> None:
    coalescer = MessageCoalescer(idle_window_seconds=0.05, max_window_seconds=0.1)
    flushed: list[list[str]] = []

    async def _on_flush(messages: list[object]) -> None:
        flushed.append([str(item) for item in messages])

    await coalescer.enqueue("100", "u1", "a", on_flush=_on_flush)
    await coalescer.enqueue("100", "u1", "b", on_flush=_on_flush)

    discarded = await coalescer.discard("100", "u1")

    assert discarded == ["a", "b"]
    assert flushed == []


@pytest.mark.asyncio
async def test_coalesce_close_flushes_all_buckets() -> None:
    coalescer = MessageCoalescer(idle_window_seconds=1.0, max_window_seconds=2.0)
    flushed: list[tuple[str, ...]] = []

    async def _mk_flush(label: str):
        async def _on_flush(messages: list[object]) -> None:
            flushed.append((label, *(str(item) for item in messages)))
        return _on_flush

    await coalescer.enqueue("100", "u1", "a", on_flush=await _mk_flush("100:u1"))
    await coalescer.enqueue("100", "u2", "b", on_flush=await _mk_flush("100:u2"))

    await coalescer.close()

    assert sorted(flushed) == [("100:u1", "a"), ("100:u2", "b")]


@pytest.mark.asyncio
async def test_coalesce_idle_timer_cancel_path_flushes_bucket() -> None:
    coalescer = MessageCoalescer(idle_window_seconds=1.0, max_window_seconds=2.0)
    flushed: list[list[str]] = []

    async def _on_flush(messages: list[object]) -> None:
        flushed.append([str(item) for item in messages])

    await coalescer.enqueue("100", "u1", "a", on_flush=_on_flush)
    bucket = coalescer._buckets[("100", "u1")]
    assert bucket.idle_timer is not None

    await asyncio.sleep(0)
    bucket.idle_timer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await bucket.idle_timer
    await _wait_until(lambda: bool(flushed))

    assert flushed == [["a"]]
    assert ("100", "u1") not in coalescer._buckets
