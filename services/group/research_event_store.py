"""Append-only SQLite store for raw group research events."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

import aiosqlite

from services.storage.sqlite import (
    close_with_checkpoint,
    connect_sqlite,
    read_user_version_read_only,
)


@dataclass(frozen=True, slots=True)
class ResearchEvent:
    """A raw inbound or outbound event captured for research."""

    event_uid: str
    run_id: str
    event_time: datetime
    direction: str
    actor_type: str
    actor_id: str
    source: str
    group_id: str
    message_id: int | None
    reply_to_message_id: int | None
    at_user_ids: tuple[str, ...]
    text: str | None
    content_type: str
    ingested_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class AppendResult:
    status: Literal["persisted", "duplicate"]


@dataclass(frozen=True, slots=True)
class ResearchEventMetrics:
    received: int
    persisted: int
    duplicate: int
    error: int


@dataclass(frozen=True, slots=True)
class ResearchRecorderMetrics:
    received: int
    enqueued: int
    dropped_queue_full: int
    write_error: int
    pending: int


@dataclass(frozen=True, slots=True)
class ResearchCaptureMetrics:
    received: int
    enqueued: int
    dropped_queue_full: int
    write_error: int
    pending: int
    persisted: int
    duplicate: int
    error: int


class _ResearchEventSink(Protocol):
    async def append(self, event: ResearchEvent) -> object: ...


class _PseudonymizationSecretError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.value = self


class ResearchEventStore:
    """Persist research events once, keyed by their stable event UID."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._received = 0
        self._persisted = 0
        self._duplicate = 0
        self._error = 0

    async def init(self) -> None:
        if self._db is not None:
            return
        preflight_version = await read_user_version_read_only(self._db_path)
        if preflight_version > 1:
            raise RuntimeError(
                "research event database is newer than this runtime: "
                f"version={preflight_version}"
            )
        db = await connect_sqlite(self._db_path)
        self._db = db
        try:
            Path(self._db_path).chmod(0o600)
            cursor = await db.execute("PRAGMA user_version")
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
            current_version = int(row[0]) if row is not None else 0
            if current_version > 1:
                raise RuntimeError(
                    f"research event database is newer than this runtime: version={current_version}"
                )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS research_message_event (
                    event_uid TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    ingested_at TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    message_id INTEGER,
                    reply_to_message_id INTEGER,
                    at_user_ids TEXT NOT NULL,
                    text TEXT,
                    content_type TEXT NOT NULL
                )
                """
            )
            if current_version == 0:
                await db.execute("PRAGMA user_version=1")
            await db.commit()
        except BaseException:
            self._db = None
            with contextlib.suppress(BaseException):
                await db.close()
            raise

    async def close(self) -> None:
        db = self._db
        self._db = None
        await close_with_checkpoint(db, name="research_message_event")

    async def append(self, event: ResearchEvent) -> AppendResult:
        return (await self.append_many((event,)))[0]

    async def append_many(self, events: list[ResearchEvent] | tuple[ResearchEvent, ...]) -> list[AppendResult]:
        batch = tuple(events)
        if not batch:
            return []
        self._received += len(batch)
        db = self._require_db()
        results: list[AppendResult] = []
        try:
            for event in batch:
                cursor = await db.execute(
                    """
                    INSERT OR IGNORE INTO research_message_event (
                        event_uid, run_id, event_time, ingested_at, direction,
                        actor_type, actor_id, source, group_id, message_id,
                        reply_to_message_id, at_user_ids, text, content_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_uid,
                        event.run_id,
                        event.event_time.isoformat(),
                        event.ingested_at.isoformat(),
                        event.direction,
                        event.actor_type,
                        event.actor_id,
                        event.source,
                        event.group_id,
                        event.message_id,
                        event.reply_to_message_id,
                        json.dumps(event.at_user_ids),
                        event.text,
                        event.content_type,
                    ),
                )
                results.append(AppendResult(status="persisted" if cursor.rowcount == 1 else "duplicate"))
                await cursor.close()
            await db.commit()
        except Exception:
            await db.rollback()
            self._error += len(batch)
            raise

        self._persisted += sum(result.status == "persisted" for result in results)
        self._duplicate += sum(result.status == "duplicate" for result in results)
        return results

    async def get_event(self, event_uid: str) -> ResearchEvent | None:
        db = self._require_db()
        async with db.execute("SELECT * FROM research_message_event WHERE event_uid = ?", (event_uid,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return ResearchEvent(
            event_uid=row["event_uid"],
            run_id=row["run_id"],
            event_time=datetime.fromisoformat(row["event_time"]),
            ingested_at=datetime.fromisoformat(row["ingested_at"]),
            direction=row["direction"],
            actor_type=row["actor_type"],
            actor_id=row["actor_id"],
            source=row["source"],
            group_id=row["group_id"],
            message_id=row["message_id"],
            reply_to_message_id=row["reply_to_message_id"],
            at_user_ids=tuple(json.loads(row["at_user_ids"])),
            text=row["text"],
            content_type=row["content_type"],
        )

    def metrics_snapshot(self) -> ResearchEventMetrics:
        return ResearchEventMetrics(
            received=self._received,
            persisted=self._persisted,
            duplicate=self._duplicate,
            error=self._error,
        )

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("ResearchEventStore is not initialized")
        return self._db


class ResearchEventRecorder:
    """Bounded, non-blocking queue that writes research events in order."""

    def __init__(
        self,
        store: _ResearchEventSink,
        *,
        max_queue_size: int,
        batch_size: int,
        flush_interval_seconds: float,
    ) -> None:
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be positive")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if flush_interval_seconds <= 0:
            raise ValueError("flush_interval_seconds must be positive")

        self._store = store
        self._queue: asyncio.Queue[ResearchEvent | None] = asyncio.Queue(maxsize=max_queue_size)
        self._batch_size = batch_size
        self._flush_interval_seconds = flush_interval_seconds
        self._writer_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._close_lock = asyncio.Lock()
        self._closed = False
        self._received = 0
        self._enqueued = 0
        self._dropped_queue_full = 0
        self._write_error = 0
        self._pending = 0

    async def start(self) -> None:
        if self._writer_task is not None:
            return
        if self._closed:
            raise RuntimeError("ResearchEventRecorder is closed")
        self._writer_task = asyncio.create_task(self._run())

    def enqueue(self, event: ResearchEvent) -> bool:
        self._received += 1
        if self._closed:
            self._dropped_queue_full += 1
            return False
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped_queue_full += 1
            return False
        self._enqueued += 1
        self._pending += 1
        return True

    async def close(self) -> None:
        async with self._close_lock:
            if self._close_task is None:
                self._closed = True
                if self._writer_task is None:
                    self._writer_task = asyncio.create_task(self._run())
                self._close_task = asyncio.create_task(self._drain_and_stop())
            close_task = self._close_task
        await asyncio.shield(close_task)

    def metrics_snapshot(self) -> ResearchRecorderMetrics:
        return ResearchRecorderMetrics(
            received=self._received,
            enqueued=self._enqueued,
            dropped_queue_full=self._dropped_queue_full,
            write_error=self._write_error,
            pending=self._pending,
        )

    async def _run(self) -> None:
        while True:
            first = await self._queue.get()
            if first is None:
                self._queue.task_done()
                return

            batch = [first]
            stop_after_batch = False
            deadline = asyncio.get_running_loop().time() + self._flush_interval_seconds
            while len(batch) < self._batch_size:
                timeout = deadline - asyncio.get_running_loop().time()
                if timeout <= 0:
                    break
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                except TimeoutError:
                    break
                if item is None:
                    self._queue.task_done()
                    stop_after_batch = True
                    break
                batch.append(item)

            append_many = getattr(self._store, "append_many", None)
            if callable(append_many):
                batch_append = cast(Callable[[list[ResearchEvent]], Awaitable[object]], append_many)
                try:
                    await batch_append(batch)
                except Exception:
                    self._write_error += len(batch)
                finally:
                    for _ in batch:
                        self._pending -= 1
                        self._queue.task_done()
            else:
                for event in batch:
                    try:
                        await self._store.append(event)
                    except Exception:
                        self._write_error += 1
                    finally:
                        self._pending -= 1
                        self._queue.task_done()

            if stop_after_batch:
                return

    async def _drain_and_stop(self) -> None:
        await self._queue.put(None)
        if self._writer_task is not None:
            await self._writer_task


class ResearchEventCapture:
    """Build persisted events from lightweight router and scheduler signals."""

    def __init__(
        self,
        recorder: ResearchEventRecorder,
        *,
        store: ResearchEventStore | None = None,
        pseudonymization_secret: str,
        group_allowlist: list[str] | tuple[str, ...] | None = None,
        run_id: str | None = None,
    ) -> None:
        if not pseudonymization_secret.strip():
            raise _PseudonymizationSecretError("pseudonymization_secret must not be empty")
        if not group_allowlist:
            raise _PseudonymizationSecretError("group_allowlist must not be empty")
        self._recorder = recorder
        self._store = store or getattr(recorder, "store", None)
        self._pseudonymization_secret = pseudonymization_secret.encode()
        self._group_allowlist = frozenset(str(group_id) for group_id in group_allowlist)
        self._run_id = run_id or uuid.uuid4().hex
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._recorder_closed = False
        self._store_closed = self._store is None

    def capture_inbound(
        self,
        *,
        group_id: str,
        actor_id: str,
        message_id: int,
        reply_to_message_id: int | None,
        at_targets: tuple[str, ...],
        text: str | None,
        content_type: str,
        event_time: datetime | None = None,
        source: str = "live",
    ) -> bool:
        if not self._allows_group(group_id):
            return False
        pseudonymous_group_id = self._pseudonym("group", group_id)
        return self._recorder.enqueue(
            ResearchEvent(
                event_uid=self._pseudonym("event", f"{source}:inbound:{group_id}:{message_id}"),
                run_id=self._run_id,
                event_time=event_time or datetime.now(UTC),
                direction="inbound",
                actor_type="human",
                actor_id=self._pseudonym("actor", actor_id),
                source=source,
                group_id=pseudonymous_group_id,
                message_id=message_id,
                reply_to_message_id=reply_to_message_id,
                at_user_ids=tuple(self._pseudonym("actor", item) for item in at_targets),
                text=text,
                content_type=content_type,
            )
        )

    def capture_outbound(
        self,
        *,
        group_id: str,
        actor_id: str,
        message_id: int | None,
        reply_to_message_id: int | None,
        at_targets: tuple[str, ...] = (),
        text: str | None,
        content_type: str = "text",
        event_time: datetime | None = None,
        source: str = "live",
    ) -> bool:
        if not self._allows_group(group_id):
            return False
        pseudonymous_group_id = self._pseudonym("group", group_id)
        event_message_key = str(message_id) if message_id is not None else f"missing:{uuid.uuid4().hex}"
        return self._recorder.enqueue(
            ResearchEvent(
                event_uid=self._pseudonym("event", f"{source}:outbound:{group_id}:{event_message_key}"),
                run_id=self._run_id,
                event_time=event_time or datetime.now(UTC),
                direction="outbound",
                actor_type="ai",
                actor_id=self._pseudonym("actor", actor_id),
                source=source,
                group_id=pseudonymous_group_id,
                message_id=message_id,
                reply_to_message_id=reply_to_message_id,
                at_user_ids=tuple(self._pseudonym("actor", item) for item in at_targets),
                text=text,
                content_type=content_type,
            )
        )

    async def close(self) -> None:
        if self._recorder_closed and self._store_closed:
            return
        if self._close_task is None or self._close_task.done():
            self._closed = True
            self._close_task = asyncio.create_task(self._close_owned_resources())
        await asyncio.shield(self._close_task)

    def metrics_snapshot(self) -> ResearchCaptureMetrics:
        recorder_metrics = self._recorder.metrics_snapshot()
        store_metrics = self._store.metrics_snapshot() if self._store is not None else None
        return ResearchCaptureMetrics(
            received=getattr(recorder_metrics, "received", 0),
            enqueued=getattr(recorder_metrics, "enqueued", 0),
            dropped_queue_full=getattr(recorder_metrics, "dropped_queue_full", 0),
            write_error=getattr(recorder_metrics, "write_error", 0),
            pending=getattr(recorder_metrics, "pending", 0),
            persisted=getattr(store_metrics, "persisted", 0),
            duplicate=getattr(store_metrics, "duplicate", 0),
            error=getattr(store_metrics, "error", 0),
        )

    async def _close_owned_resources(self) -> None:
        errors: list[BaseException] = []
        if not self._recorder_closed:
            try:
                await self._recorder.close()
                self._recorder_closed = True
            except BaseException as exc:
                errors.append(exc)
        if not self._store_closed and self._store is not None:
            try:
                await self._store.close()
                self._store_closed = True
            except BaseException as exc:
                errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("research capture shutdown failed", errors)

    def _allows_group(self, group_id: str) -> bool:
        return group_id in self._group_allowlist

    def _pseudonym(self, namespace: str, raw_id: str) -> str:
        digest = hmac.new(
            self._pseudonymization_secret,
            f"{namespace}:{raw_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{namespace}-pseudo-{digest[:16]}"
