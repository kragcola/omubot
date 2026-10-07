"""One native contact candidate per group, with one frozen timing selection."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .affection import AffectionProjection
from .climate import ClimateSnapshot
from .config import ContactEndpointRules
from .runtime import Turn
from .schedule_life import ScheduleDayRecord
from .scheduling import ContactPending, TimelineSourceProof
from .story import StoryChatProjection
from .types import ConversationKey, Message, StrictModel


class ContactTiming(StrictModel):
    decision: Literal["now", "defer", "abandon"]
    reason_code: Literal[
        "relevant_context", "role_life_update", "defer_window", "busy", "uncertain", "no_reason"
    ]
    due_at: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def due_only_for_defer(self) -> ContactTiming:
        if (self.decision == "defer") != (self.due_at is not None):
            raise ValueError("only defer requires an original-expiry-bounded due_at")
        return self


def contact_window_open(rules: ContactEndpointRules, at: datetime) -> bool:
    minute = at.hour * 60 + at.minute
    return any(window.start_minute <= minute < window.end_minute for window in rules.windows)


@dataclass
class ContactCandidate:
    item: ContactPending
    revision: int
    timeline: TimelineSourceProof
    message: Message
    source_digest: str
    retained_at: float
    timing_system: str = field(repr=False)
    story: StoryChatProjection | None = None
    day: ScheduleDayRecord | None = None
    affection: AffectionProjection | None = None
    climate: ClimateSnapshot | None = None
    turn: Turn | None = None
    task: asyncio.Task[None] | None = None
    admitted: bool = False


class ContactLifecycle:
    """Own only current candidates/selection timers; Store owns durable cause and usage."""

    def __init__(self) -> None:
        self.candidates: dict[str, ContactCandidate] = {}

    def register(
        self, candidate: ContactCandidate, select: Callable[[ContactCandidate], Awaitable[None]]
    ) -> None:
        self.candidates[candidate.item.owner.request_id] = candidate

        async def selection() -> None:
            await select(candidate)

        candidate.task = asyncio.create_task(selection(), name="contact:select")

    def for_group(self, key: ConversationKey) -> tuple[ContactCandidate, ...]:
        return tuple(
            candidate for candidate in self.candidates.values() if candidate.item.owner.scope.key == key
        )

    def forget(self, candidate: ContactCandidate) -> None:
        self.candidates.pop(candidate.item.owner.request_id, None)

    def invalidate(self, candidate: ContactCandidate, reason: str) -> None:
        candidate.item.abandon_code = reason
        if candidate.turn is not None:
            candidate.turn.invalidate(reason)
        if candidate.task is not None and not candidate.task.done():
            candidate.task.cancel()

    async def close(self) -> None:
        candidates = tuple(self.candidates.values())
        for candidate in candidates:
            self.invalidate(candidate, "stopping")
        await asyncio.gather(
            *(candidate.task for candidate in candidates if candidate.task is not None),
            return_exceptions=True,
        )
