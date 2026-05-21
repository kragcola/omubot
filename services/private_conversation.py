"""Private conversation actor primitives.

This module keeps the private-chat turn state small and explicit:
- wait can be interrupted by a new message
- a finished reply marks the turn complete
- the state is in-process only for now
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from loguru import logger

PrivateConversationPhase = Literal["idle", "processing", "waiting", "complete"]
PrivateConversationAction = Literal["wait", "complete"]

_L = logger.bind(channel="reply_workflow")


@dataclass(frozen=True)
class PrivateConversationTransition:
    """A single private-conversation state change."""

    session_id: str
    event_id: str
    user_id: str
    phase_before: PrivateConversationPhase
    phase_after: PrivateConversationPhase
    action: PrivateConversationAction
    reason: str
    interrupted_wait: bool = False
    resumed_from_complete: bool = False
    text_len: int = 0
    reply_len: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def log_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "session_id": self.session_id,
            "event_id": self.event_id,
            "user_id": self.user_id,
            "phase_before": self.phase_before,
            "phase_after": self.phase_after,
            "action": self.action,
            "reason": self.reason,
            "interrupted_wait": self.interrupted_wait,
            "resumed_from_complete": self.resumed_from_complete,
            "text_len": self.text_len,
            "reply_len": self.reply_len,
            "started_at": round(self.started_at, 3),
            "finished_at": round(self.finished_at, 3),
        }
        fields.update(self.metadata)
        return fields


@dataclass
class PrivateConversationState:
    """Mutable state for one private conversation."""

    phase: PrivateConversationPhase = "idle"
    last_event_id: str = ""
    last_user_id: str = ""
    last_reason: str = ""
    last_updated_at: float = 0.0
    processing_started_at: float = 0.0
    wait_started_at: float = 0.0
    completed_at: float = 0.0
    last_text_len: int = 0
    last_reply_len: int = 0


class PrivateConversationTurn:
    """In-flight private turn. Use mark_wait() or mark_complete() once."""

    def __init__(
        self,
        actor: PrivateConversationActor,
        *,
        event_id: str,
        user_id: str,
        text: str,
        phase_before: PrivateConversationPhase,
        interrupted_wait: bool,
        resumed_from_complete: bool,
        started_at: float,
    ) -> None:
        self._actor = actor
        self._event_id = event_id
        self._user_id = user_id
        self._text = text or ""
        self._phase_before = phase_before
        self._interrupted_wait = interrupted_wait
        self._resumed_from_complete = resumed_from_complete
        self._started_at = started_at
        self._final_transition: PrivateConversationTransition | None = None

    @property
    def phase_before(self) -> PrivateConversationPhase:
        return self._phase_before

    @property
    def interrupted_wait(self) -> bool:
        return self._interrupted_wait

    @property
    def resumed_from_complete(self) -> bool:
        return self._resumed_from_complete

    def mark_wait(
        self,
        reason: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> PrivateConversationTransition:
        if self._final_transition is not None:
            return self._final_transition
        now = time.time()
        state = self._actor._state
        state.phase = "waiting"
        state.wait_started_at = now
        state.completed_at = 0.0
        state.last_reason = reason
        state.last_updated_at = now
        state.last_event_id = self._event_id
        state.last_user_id = self._user_id
        state.last_text_len = len(self._text)
        state.last_reply_len = 0
        transition = PrivateConversationTransition(
            session_id=self._actor.session_id,
            event_id=self._event_id,
            user_id=self._user_id,
            phase_before=self._phase_before,
            phase_after="waiting",
            action="wait",
            reason=reason,
            interrupted_wait=self._interrupted_wait,
            resumed_from_complete=self._resumed_from_complete,
            text_len=len(self._text),
            reply_len=0,
            started_at=self._started_at,
            finished_at=now,
            metadata=dict(metadata or {}),
        )
        self._final_transition = transition
        return transition

    def mark_complete(
        self,
        reason: str,
        *,
        reply_text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> PrivateConversationTransition:
        if self._final_transition is not None:
            return self._final_transition
        now = time.time()
        state = self._actor._state
        state.phase = "complete"
        state.completed_at = now
        state.wait_started_at = 0.0
        state.last_reason = reason
        state.last_updated_at = now
        state.last_event_id = self._event_id
        state.last_user_id = self._user_id
        state.last_text_len = len(self._text)
        state.last_reply_len = len(reply_text or "")
        transition = PrivateConversationTransition(
            session_id=self._actor.session_id,
            event_id=self._event_id,
            user_id=self._user_id,
            phase_before=self._phase_before,
            phase_after="complete",
            action="complete",
            reason=reason,
            interrupted_wait=self._interrupted_wait,
            resumed_from_complete=self._resumed_from_complete,
            text_len=len(self._text),
            reply_len=len(reply_text or ""),
            started_at=self._started_at,
            finished_at=now,
            metadata=dict(metadata or {}),
        )
        self._final_transition = transition
        return transition


class PrivateConversationActor:
    """Serializes a single private conversation in process."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._state = PrivateConversationState()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> PrivateConversationState:
        return self._state

    def snapshot(self) -> dict[str, Any]:
        state = self._state
        return {
            "session_id": self.session_id,
            "phase": state.phase,
            "last_event_id": state.last_event_id,
            "last_user_id": state.last_user_id,
            "last_reason": state.last_reason,
            "last_updated_at": state.last_updated_at,
            "processing_started_at": state.processing_started_at,
            "wait_started_at": state.wait_started_at,
            "completed_at": state.completed_at,
            "last_text_len": state.last_text_len,
            "last_reply_len": state.last_reply_len,
        }

    @asynccontextmanager
    async def turn(
        self,
        *,
        event_id: str,
        user_id: str,
        text: str,
    ) -> AsyncIterator[PrivateConversationTurn]:
        started_at = time.time()
        async with self._lock:
            state = self._state
            phase_before = state.phase
            interrupted_wait = phase_before == "waiting"
            resumed_from_complete = phase_before == "complete"
            state.phase = "processing"
            state.processing_started_at = started_at
            state.last_updated_at = started_at
            state.last_event_id = event_id
            state.last_user_id = user_id
            state.last_text_len = len(text or "")
            state.last_reason = "turn_started"
            turn = PrivateConversationTurn(
                self,
                event_id=event_id,
                user_id=user_id,
                text=text,
                phase_before=phase_before,
                interrupted_wait=interrupted_wait,
                resumed_from_complete=resumed_from_complete,
                started_at=started_at,
            )
            try:
                yield turn
            finally:
                # If the caller exited without committing a terminal state,
                # fall back to idle so the next message can proceed.
                if state.phase == "processing":
                    state.phase = "idle"
                    state.last_reason = "turn_abandoned"
                    state.last_updated_at = time.time()


class PrivateConversationManager:
    """In-memory registry for private conversation actors."""

    def __init__(self) -> None:
        self._actors: dict[str, PrivateConversationActor] = {}

    def get(self, session_id: str) -> PrivateConversationActor:
        actor = self._actors.get(session_id)
        if actor is None:
            actor = PrivateConversationActor(session_id)
            self._actors[session_id] = actor
        return actor

    def reset(self) -> None:
        self._actors.clear()

    def snapshot(self, session_id: str) -> dict[str, Any]:
        actor = self._actors.get(session_id)
        if actor is None:
            return {"session_id": session_id, "phase": "idle"}
        return actor.snapshot()


PRIVATE_CONVERSATION_MANAGER = PrivateConversationManager()


def get_private_conversation_actor(session_id: str) -> PrivateConversationActor:
    return PRIVATE_CONVERSATION_MANAGER.get(session_id)


def reset_private_conversation_manager() -> None:
    PRIVATE_CONVERSATION_MANAGER.reset()


def log_private_transition(transition: PrivateConversationTransition) -> None:
    _L.info(
        "reply_workflow | conversation={} event_id={} mode=private_actor action={} "
        "phase_before={} phase_after={} interrupted_wait={} resumed_from_complete={} "
        "reason={} fields={}",
        transition.session_id,
        transition.event_id,
        transition.action,
        transition.phase_before,
        transition.phase_after,
        transition.interrupted_wait,
        transition.resumed_from_complete,
        transition.reason,
        transition.log_fields(),
    )
