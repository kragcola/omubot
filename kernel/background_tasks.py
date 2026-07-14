"""Kernel-owned background task lifecycle supervision."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from loguru import logger

TaskFactory = Callable[[], Awaitable[None]]


class RestartPolicy(StrEnum):
    NEVER = "never"
    ON_FAILURE = "on_failure"


class ShutdownPolicy(StrEnum):
    CANCEL = "cancel"
    WAIT = "wait"


class TaskKind(StrEnum):
    INLINE = "inline"
    DEFERRED = "deferred"
    PERIODIC = "periodic"
    HEAVY = "heavy"


@dataclass(frozen=True, slots=True)
class TaskSpec:
    name: str
    owner: str
    kind: TaskKind
    restart: RestartPolicy = RestartPolicy.NEVER
    shutdown: ShutdownPolicy = ShutdownPolicy.CANCEL
    max_restarts: int = 0
    backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    shutdown_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        try:
            kind = self.kind if isinstance(self.kind, TaskKind) else TaskKind(self.kind)
        except ValueError as exc:
            raise ValueError(f"unknown background task kind: {self.kind}") from exc
        object.__setattr__(self, "kind", kind)
        for field_name, value in (
            ("name", self.name),
            ("owner", self.owner),
            ("kind", self.kind),
        ):
            if not value.strip():
                raise ValueError(f"task {field_name} must not be empty")
        if self.max_restarts < 0:
            raise ValueError("max_restarts must be non-negative")
        if self.backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("restart backoff must be non-negative")
        if self.shutdown_timeout_seconds < 0:
            raise ValueError("shutdown timeout must be non-negative")


@dataclass(frozen=True, slots=True)
class TaskFailure:
    occurred_at: str
    attempt: int
    error: str


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    name: str
    owner: str
    kind: TaskKind
    restart: RestartPolicy
    shutdown: ShutdownPolicy
    state: str
    attempts: int
    restarts: int
    max_restarts: int
    last_error: str
    done: bool
    cancelled: bool
    failure_history: tuple[TaskFailure, ...]


@dataclass(slots=True)
class _TaskRecord:
    spec: TaskSpec
    factory: TaskFactory
    task: asyncio.Task[None] | None = None
    state: str = "registered"
    attempts: int = 0
    restarts: int = 0
    last_error: str = ""
    failures: deque[TaskFailure] = field(
        default_factory=lambda: deque(maxlen=20)
    )


class BackgroundTaskSupervisor:
    """Own process-level tasks under one observable shutdown boundary."""

    def __init__(self) -> None:
        self._records: dict[str, _TaskRecord] = {}
        self._stopping = False
        self._stopped = False
        self._stop_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Participate in the application lifecycle without eager task creation."""
        if self._stopping or self._stopped:
            raise RuntimeError("background task supervisor is stopped")

    def spawn(self, spec: TaskSpec, factory: TaskFactory) -> asyncio.Task[None]:
        if self._stopping or self._stopped:
            raise RuntimeError("background task supervisor is stopped")
        existing = self._records.get(spec.name)
        if existing is not None:
            if existing.task is None or not existing.task.done():
                raise ValueError(f"background task already registered: {spec.name}")
            del self._records[spec.name]

        record = _TaskRecord(spec=spec, factory=factory)
        self._records[spec.name] = record
        task = asyncio.create_task(self._run(record), name=f"background:{spec.name}")
        record.task = task
        return task

    def snapshot(self) -> tuple[TaskSnapshot, ...]:
        snapshots: list[TaskSnapshot] = []
        for record in self._records.values():
            task = record.task
            snapshots.append(
                TaskSnapshot(
                    name=record.spec.name,
                    owner=record.spec.owner,
                    kind=record.spec.kind,
                    restart=record.spec.restart,
                    shutdown=record.spec.shutdown,
                    state=record.state,
                    attempts=record.attempts,
                    restarts=record.restarts,
                    max_restarts=record.spec.max_restarts,
                    last_error=record.last_error,
                    done=bool(task is not None and task.done()),
                    cancelled=bool(task is not None and task.cancelled()),
                    failure_history=tuple(record.failures),
                )
            )
        return tuple(snapshots)

    async def stop_owner(self, owner: str) -> None:
        records = [record for record in self._records.values() if record.spec.owner == owner]
        if not records:
            return
        await asyncio.gather(*(self._stop_record(record) for record in records))

    async def stop(self) -> None:
        if self._stopped:
            return
        if self._stop_task is None:
            self._stopping = True
            self._stop_task = asyncio.create_task(
                self._stop_all(),
                name="background:supervisor-stop",
            )
        await asyncio.shield(self._stop_task)

    async def _run(self, record: _TaskRecord) -> None:
        while True:
            record.state = "running"
            record.attempts += 1
            try:
                await record.factory()
            except asyncio.CancelledError:
                record.state = "cancelled"
                raise
            except Exception as exc:
                record.last_error = f"{type(exc).__name__}: {exc}"
                record.failures.append(
                    TaskFailure(
                        occurred_at=datetime.now(UTC).isoformat(),
                        attempt=record.attempts,
                        error=record.last_error,
                    )
                )
                if (
                    record.spec.restart is not RestartPolicy.ON_FAILURE
                    or record.restarts >= record.spec.max_restarts
                    or self._stopping
                ):
                    record.state = "failed"
                    logger.bind(channel="background_tasks").error(
                        "background task failed | name={} owner={} attempts={} err={}",
                        record.spec.name,
                        record.spec.owner,
                        record.attempts,
                        record.last_error,
                    )
                    return

                record.restarts += 1
                record.state = "backoff"
                delay = min(
                    record.spec.backoff_seconds * (2 ** (record.restarts - 1)),
                    record.spec.max_backoff_seconds,
                )
                logger.bind(channel="background_tasks").warning(
                    "background task restarting | name={} owner={} restart={}/{} delay={:.3f}s err={}",
                    record.spec.name,
                    record.spec.owner,
                    record.restarts,
                    record.spec.max_restarts,
                    delay,
                    record.last_error,
                )
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    record.state = "cancelled"
                    raise
            else:
                record.state = "completed"
                return

    async def _stop_all(self) -> None:
        try:
            await asyncio.gather(*(self._stop_record(record) for record in self._records.values()))
        finally:
            self._stopped = True
            self._stopping = False

    async def _stop_record(self, record: _TaskRecord) -> None:
        task = record.task
        if task is None or task.done():
            return

        if record.spec.shutdown is ShutdownPolicy.WAIT:
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=record.spec.shutdown_timeout_seconds,
                )
                return
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                if task.cancelled():
                    return
                raise

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise
