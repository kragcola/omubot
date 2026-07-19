"""Pure visible-reply guardrail pipeline stage."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

GuardrailResult = tuple[str, tuple[Any, ...], Mapping[str, object], bool]
GuardrailCallable = Callable[..., GuardrailResult | Awaitable[GuardrailResult]]


@dataclass(frozen=True, slots=True)
class VisibleReplyGuardrailInput:
    reply: str
    enabled: bool
    thinker_thought: str
    last_assistant_text: str
    user_message: str
    session_count: int
    bot_name: str
    assistant_history: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VisibleReplyGuardrailOutput:
    reply: str
    hits: tuple[Any, ...]
    metadata: Mapping[str, object]
    blocked: bool


class VisibleReplyGuardrailStage:
    """Delegate guardrail evaluation without owning runtime state."""

    def __init__(self, guardrail: GuardrailCallable) -> None:
        self._guardrail = guardrail

    async def run(self, stage_input: VisibleReplyGuardrailInput) -> VisibleReplyGuardrailOutput:
        kwargs: dict[str, Any] = {
            "enabled": stage_input.enabled,
            "thinker_thought": stage_input.thinker_thought,
            "last_assistant_text": stage_input.last_assistant_text,
            "user_message": stage_input.user_message,
            "session_count": stage_input.session_count,
            "bot_name": stage_input.bot_name,
        }
        # Backward-compatible: pass assistant_history when the callable accepts
        # it by name or via **kwargs (VAR_KEYWORD).
        try:
            sig = inspect.signature(self._guardrail)
            params = sig.parameters
            accepts_history = "assistant_history" in params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
            if accepts_history:
                kwargs["assistant_history"] = stage_input.assistant_history
        except (TypeError, ValueError):
            # Builtins / C callables without inspectable signature: try pass-through.
            kwargs["assistant_history"] = stage_input.assistant_history
        try:
            result = self._guardrail(stage_input.reply, **kwargs)
        except TypeError:
            # Callable rejected unexpected kw; retry without history.
            kwargs.pop("assistant_history", None)
            result = self._guardrail(stage_input.reply, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        reply, hits, metadata, blocked = result
        return VisibleReplyGuardrailOutput(
            reply=reply,
            hits=hits,
            metadata=metadata,
            blocked=blocked,
        )
