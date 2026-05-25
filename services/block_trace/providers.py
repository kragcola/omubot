"""ContextProvider protocol and QueryContext for prompt-block providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from services.block_trace.types import PromptBlockCandidate


@dataclass(frozen=True)
class QueryContext:
    """Input context passed to each ContextProvider at provide-time."""

    request_id: str
    session_id: str
    user_id: str
    group_id: str | None
    conversation_text: str
    runtime_state: Any = None
    turn_id: str = ""
    mood_fit_target: float | None = None


@runtime_checkable
class ContextProvider(Protocol):
    """Protocol for prompt-block providers.

    Unlike ContextSource (which returns raw ContextHit for search/pack),
    a ContextProvider returns ready-to-inject PromptBlockCandidate objects
    with priority, position, and label already set.
    """

    @property
    def name(self) -> str: ...

    async def provide(self, ctx: QueryContext) -> list[PromptBlockCandidate]: ...
