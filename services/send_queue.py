"""Per-group visible output queue.

This is a conservative preparation layer for future generation/send
decoupling: callers still await completion, but text segments and visible tool
outputs can share one ordered per-group send path.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger

_L = logger.bind(channel="send_queue")

SendKind = Literal["text", "message", "reply_batch"]


@dataclass(frozen=True)
class ReplySegmentBatch:
    """A group reply batch that must stay contiguous in visible chat."""

    group_id: str
    segments: list[str]
    first_segment_humanize: str = "skip"
    later_segment_humanize: str = "normal"
    allow_interleaved_between_segments: bool = False
    inter_segment_delay_s: float = 0.0
    description: str = "reply_batch"


@dataclass(frozen=True)
class BatchSendHandle:
    """Completion signals for a queued reply batch."""

    started: asyncio.Future[float]
    first_segment_sent: asyncio.Future[float]
    interleave_count: asyncio.Future[int]
    done: asyncio.Future[float]


@dataclass(frozen=True)
class SendItem:
    """One visible group output to be delivered in queue order."""

    group_id: str
    kind: SendKind
    payload: Any
    humanize: str = "normal"
    description: str = ""


@dataclass
class _QueuedSend:
    item: SendItem
    future: asyncio.Future[float]
    queued_at: float = 0.0
    started_future: asyncio.Future[float] | None = None
    first_segment_future: asyncio.Future[float] | None = None
    interleave_count_future: asyncio.Future[int] | None = None


class GroupSendQueue:
    """Serialize visible sends per group while preserving await semantics."""

    def __init__(
        self,
        *,
        bot: Any = None,
        humanizer: Any = None,
        muted_checker: Callable[[str], bool] | None = None,
        send_allowed_checker: Callable[[str], bool] | None = None,
    ) -> None:
        self._bot = bot
        self._humanizer = humanizer
        self._muted_checker = muted_checker
        self._send_allowed_checker = send_allowed_checker
        self._queues: dict[str, asyncio.Queue[_QueuedSend]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._closed = False

    def set_bot(self, bot: Any) -> None:
        self._bot = bot

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._workers.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()
        self._queues.clear()

    async def send_group_text(self, group_id: str, text: str, *, humanize: str = "normal") -> float:
        return await self.send(
            SendItem(
                group_id=group_id,
                kind="text",
                payload=text,
                humanize=humanize,
                description="scheduler_text",
            )
        )

    async def enqueue_group_text(
        self,
        group_id: str,
        text: str,
        *,
        humanize: str = "normal",
        description: str = "scheduler_text",
    ) -> asyncio.Future[float]:
        return await self.enqueue(
            SendItem(
                group_id=group_id,
                kind="text",
                payload=text,
                humanize=humanize,
                description=description,
            )
        )

    async def send_group_message(
        self,
        group_id: str,
        message: Any,
        *,
        description: str = "tool_message",
    ) -> float:
        return await self.send(
            SendItem(
                group_id=group_id,
                kind="message",
                payload=message,
                humanize="skip",
                description=description,
            )
        )

    async def enqueue_reply_batch(self, batch: ReplySegmentBatch) -> BatchSendHandle:
        if self._closed:
            raise RuntimeError("send queue is closed")
        self._ensure_send_allowed(batch.group_id)
        loop = asyncio.get_running_loop()
        started_future: asyncio.Future[float] = loop.create_future()
        first_segment_future: asyncio.Future[float] = loop.create_future()
        interleave_count_future: asyncio.Future[int] = loop.create_future()
        done_future: asyncio.Future[float] = loop.create_future()
        queued_at = time.monotonic()
        queue = self._queue_for_group(batch.group_id)
        await queue.put(
            _QueuedSend(
                item=SendItem(
                    group_id=batch.group_id,
                    kind="reply_batch",
                    payload=batch,
                    humanize="skip",
                    description=batch.description,
                ),
                future=done_future,
                queued_at=queued_at,
                started_future=started_future,
                first_segment_future=first_segment_future,
                interleave_count_future=interleave_count_future,
            )
        )
        return BatchSendHandle(
            started=started_future,
            first_segment_sent=first_segment_future,
            interleave_count=interleave_count_future,
            done=done_future,
        )

    async def send_reply_batch(self, batch: ReplySegmentBatch) -> float:
        handle = await self.enqueue_reply_batch(batch)
        return await handle.done

    async def send(self, item: SendItem) -> float:
        future = await self.enqueue(item)
        return await future

    async def enqueue(self, item: SendItem) -> asyncio.Future[float]:
        if self._closed:
            raise RuntimeError("send queue is closed")
        self._ensure_send_allowed(item.group_id)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[float] = loop.create_future()
        queue = self._queue_for_group(item.group_id)
        await queue.put(_QueuedSend(item=item, future=future))
        return future

    def _ensure_send_allowed(self, group_id: str) -> None:
        if self._send_allowed_checker is None:
            return
        if not self._send_allowed_checker(group_id):
            raise PermissionError(f"group {group_id} is not allowed to receive active output")

    def _queue_for_group(self, group_id: str) -> asyncio.Queue[_QueuedSend]:
        queue = self._queues.get(group_id)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[group_id] = queue
        worker = self._workers.get(group_id)
        if worker is None or worker.done():
            self._workers[group_id] = asyncio.create_task(self._worker(group_id, queue))
        return queue

    async def _worker(self, group_id: str, queue: asyncio.Queue[_QueuedSend]) -> None:
        while True:
            queued = await queue.get()
            try:
                if queued.future.cancelled():
                    continue
                if queued.item.kind == "reply_batch":
                    elapsed = await self._deliver_reply_batch(
                        queued,
                        group_id,
                        queue,
                    )
                else:
                    elapsed = await self._deliver(queued.item)
                if not queued.future.done():
                    queued.future.set_result(elapsed)
            except Exception as exc:
                if queued.started_future is not None and not queued.started_future.done():
                    queued.started_future.set_exception(exc)
                if queued.first_segment_future is not None and not queued.first_segment_future.done():
                    queued.first_segment_future.set_exception(exc)
                if not queued.future.done():
                    queued.future.set_exception(exc)
            finally:
                queue.task_done()

    async def _deliver(self, item: SendItem) -> float:
        if not self._bot:
            return 0.0
        self._ensure_send_allowed(item.group_id)
        if self._muted_checker is not None and self._muted_checker(item.group_id):
            _L.warning("send queue | group={} muted, dropping {}", item.group_id, item.description or item.kind)
            return 0.0

        from nonebot.adapters.onebot.v11 import Message
        from nonebot.adapters.onebot.v11.exception import ActionFailed

        delay = 2.0
        max_delay = 60.0
        while True:
            try:
                t_send = time.monotonic()
                if item.kind == "text":
                    text = str(item.payload)
                    if self._humanizer is not None and item.humanize != "skip":
                        await self._humanizer.delay(text)
                    await self._bot.send_group_msg(group_id=int(item.group_id), message=Message(text))
                    elapsed = time.monotonic() - t_send
                    self._log_send(item, elapsed, length=len(text))
                    return elapsed

                await self._bot.send_group_msg(group_id=int(item.group_id), message=item.payload)
                elapsed = time.monotonic() - t_send
                self._log_send(item, elapsed, length=None)
                return elapsed
            except ActionFailed as exc:
                info = getattr(exc, "info", {}) or {}
                _L.warning(
                    "send queue | group={} {} failed: {} | retry in {}s",
                    item.group_id,
                    item.description or item.kind,
                    info.get("wording") or info.get("message", str(exc)),
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    async def _deliver_reply_batch(
        self,
        queued: _QueuedSend,
        group_id: str,
        queue: asyncio.Queue[_QueuedSend],
    ) -> float:
        item = queued.item
        batch = item.payload
        if not isinstance(batch, ReplySegmentBatch):
            raise TypeError("reply_batch item payload must be ReplySegmentBatch")
        queue_wait = max(0.0, time.monotonic() - queued.queued_at)
        if queued.started_future is not None and not queued.started_future.done():
            queued.started_future.set_result(queue_wait)
        _L.debug(
            "reply batch send start | group={} segments={} queue_wait={:.3f}s desc={}",
            batch.group_id, len(batch.segments), queue_wait, batch.description,
        )
        if not batch.segments:
            if queued.first_segment_future is not None and not queued.first_segment_future.done():
                queued.first_segment_future.set_result(0.0)
            if queued.interleave_count_future is not None and not queued.interleave_count_future.done():
                queued.interleave_count_future.set_result(0)
            return 0.0

        interleave_count = 0
        total_elapsed = 0.0
        try:
            for idx, segment in enumerate(batch.segments):
                elapsed = await self._deliver(
                    SendItem(
                        group_id=batch.group_id,
                        kind="text",
                        payload=segment,
                        humanize=batch.first_segment_humanize if idx == 0 else batch.later_segment_humanize,
                        description=f"{batch.description}:segment:{idx + 1}/{len(batch.segments)}",
                    )
                )
                total_elapsed += elapsed
                if idx == 0 and queued.first_segment_future is not None and not queued.first_segment_future.done():
                    queued.first_segment_future.set_result(elapsed)
                if idx < len(batch.segments) - 1:
                    if batch.inter_segment_delay_s > 0:
                        await asyncio.sleep(batch.inter_segment_delay_s)
                    interleaved_elapsed = await self._interleave_one_between_segments(batch, group_id, queue)
                    if interleaved_elapsed > 0:
                        interleave_count += 1
                    total_elapsed += interleaved_elapsed
        except Exception:
            if queued.interleave_count_future is not None and not queued.interleave_count_future.done():
                queued.interleave_count_future.set_result(interleave_count)
            raise
        else:
            if queued.interleave_count_future is not None and not queued.interleave_count_future.done():
                queued.interleave_count_future.set_result(interleave_count)
        return total_elapsed

    async def _interleave_one_between_segments(
        self,
        batch: ReplySegmentBatch,
        group_id: str,
        queue: asyncio.Queue[_QueuedSend],
    ) -> float:
        if not batch.allow_interleaved_between_segments or queue.empty():
            return 0.0
        pending = getattr(queue, "_queue", None)
        if not pending or pending[0].item.kind == "reply_batch":
            return 0.0

        queued = queue.get_nowait()
        try:
            if queued.future.cancelled():
                return 0.0
            elapsed = await self._deliver(queued.item)
            if not queued.future.done():
                queued.future.set_result(elapsed)
            _L.info(
                "reply batch yielded between segments | group={} desc={} interleaved={}",
                group_id,
                batch.description,
                queued.item.description or queued.item.kind,
            )
            return elapsed
        except Exception as exc:
            if queued.started_future is not None and not queued.started_future.done():
                queued.started_future.set_exception(exc)
            if queued.first_segment_future is not None and not queued.first_segment_future.done():
                queued.first_segment_future.set_exception(exc)
            if not queued.future.done():
                queued.future.set_exception(exc)
            return 0.0
        finally:
            queue.task_done()

    def _log_send(self, item: SendItem, elapsed: float, *, length: int | None) -> None:
        if elapsed >= 8.0:
            _L.warning(
                "send queue slow | group={} kind={} desc={} humanize={} len={} elapsed={:.1f}s",
                item.group_id,
                item.kind,
                item.description,
                item.humanize,
                length,
                elapsed,
            )
        else:
            _L.debug(
                "send queue ok | group={} kind={} desc={} humanize={} len={} elapsed={:.1f}s",
                item.group_id,
                item.kind,
                item.description,
                item.humanize,
                length,
                elapsed,
            )
