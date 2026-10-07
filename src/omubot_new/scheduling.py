"""Bounded per-session workers. The application owns decisions and persistence."""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal

from .diagnostic_commands import DiagnosticCommand
from .echo import EchoDecision
from .element_rules import ElementMatch
from .group_board import GroupStateSnapshot
from .runtime import DecisionBinding, StageADecision, Turn
from .types import BotContactInput, ConversationKey, Event, Message, ModelRequest, OperationError, ReplyOwner

SessionKey = ConversationKey
EvidenceKind = Literal["none", "mention", "reply", "mention_and_reply"]
ResolutionStatus = Literal["resolved", "unknown", "conflict"]
_PERSONA_VERSION_PREFIX = "persona-v1:sha256:"
_EMPTY_PERSONA_VERSION = _PERSONA_VERSION_PREFIX + hashlib.sha256(b"").hexdigest()
_CONFIG_VERSION_PREFIX = "config-v1:sha256:"
_EMPTY_CONFIG_VERSION = _CONFIG_VERSION_PREFIX + hashlib.sha256(b"").hexdigest()
_MAX_CLIMATE_SOURCE_STATES = 6
_MAX_CLIMATE_HINT_CHARS = 320
_MAX_FICTION_CONTEXT_CHARS = 1800


@dataclass(frozen=True)
class SourceIdentity:
    """Identity fields copied from the accepted source event."""

    bot_id: str
    group_id: str | None
    user_id: str
    message_id: str
    kind: Literal["group", "private"] = "group"
    private_user_id: str | None = None


@dataclass(frozen=True)
class TopicResolution:
    """A proposed reply-based attribution; it never updates a Pending itself."""

    status: ResolutionStatus
    topic_id: str | None
    evidence_kind: EvidenceKind
    revision: int
    matched_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TimelineSourceProof:
    """Body-free identity and original lifetime of a retained owner row."""

    received_at: float
    sources: tuple[tuple[str, str], ...]
    source_ids: tuple[str, ...]
    event_id: str | None
    role: str


@dataclass(frozen=True)
class TurnStateSnapshot:
    """The effective per-request state available to the current chat path."""

    persona_system: str = field(default="", repr=False)
    persona_version: str = _EMPTY_PERSONA_VERSION
    persona_mode: Literal["simple", "source"] = "simple"
    persona_source: str = "static_config"
    persona_source_hash: str | None = None
    persona_source_version: str | None = None
    persona_canonical_name: str | None = field(default=None, repr=False)
    persona_status: Literal["default", "configured"] = "default"
    climate_mode: Literal["off", "observe", "active"] = "off"
    climate_status: Literal["not_configured", "available", "unavailable", "degraded"] = (
        "not_configured"
    )
    climate_version: str | None = None
    climate_revision: int | None = None
    climate_source_status: tuple[str, ...] = ()
    climate_hint_status: Literal[
        "not_configured", "disabled", "available", "missing", "budget_skipped", "degraded"
    ] = "not_configured"
    climate_hint: str = field(default="", repr=False)
    climate_energy: float | None = None
    delay_multiplier: float | None = None
    interaction_heat: float | None = None
    fiction_status: Literal["disabled", "missing", "available", "budget_skipped", "degraded"] = (
        "disabled"
    )
    fiction_context: str = field(default="", repr=False)
    calendar_status: Literal["disabled", "missing", "available", "budget_skipped", "degraded"] = (
        "disabled"
    )
    calendar_context: str = field(default="", repr=False)
    configuration_version: str = _EMPTY_CONFIG_VERSION

    def __post_init__(self) -> None:
        if not self.persona_source or len(self.persona_source) > 128:
            raise ValueError("persona source metadata must be bounded")
        for value in (self.persona_source_hash, self.persona_source_version):
            if value is not None and (not value or len(value) > 128):
                raise ValueError("persona source metadata must be bounded")
        if self.climate_version is not None and (
            not self.climate_version or len(self.climate_version) > 128
        ):
            raise ValueError("climate version metadata must be bounded")
        if self.climate_revision is not None and self.climate_revision < 0:
            raise ValueError("climate revision must be non-negative")
        if len(self.climate_source_status) > _MAX_CLIMATE_SOURCE_STATES or any(
            not item or len(item) > 64 for item in self.climate_source_status
        ):
            raise ValueError("climate source status must be bounded")
        if len(self.climate_hint) > _MAX_CLIMATE_HINT_CHARS:
            raise ValueError("climate expression hint must be bounded")
        if len(self.fiction_context) > _MAX_FICTION_CONTEXT_CHARS:
            raise ValueError("fiction context must be bounded")
        if len(self.calendar_context) > 900:
            raise ValueError("calendar context must be bounded")

    @property
    def model_system(self) -> str:
        """Return the fixed persona with an optional active-mode tone hint."""
        parts = [self.persona_system]
        if self.climate_hint:
            parts.append(self.climate_hint)
        if self.calendar_context:
            parts.append(self.calendar_context)
        if self.fiction_context:
            parts.append(self.fiction_context)
        return "\n\n".join(parts)

    @property
    def context_version(self) -> str:
        """Bind judgments to the exact system context frozen for this turn."""
        return "context-v1:sha256:" + hashlib.sha256(
            self.model_system.encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_persona_system(
        cls,
        persona_system: str,
        configuration_version: str = _EMPTY_CONFIG_VERSION,
        *,
        persona_mode: Literal["simple", "source"] = "simple",
        persona_source: str = "static_config",
        persona_source_hash: str | None = None,
        persona_source_version: str | None = None,
        persona_canonical_name: str | None = None,
        persona_status: Literal["default", "configured"] | None = None,
    ) -> TurnStateSnapshot:
        effective_status: Literal["default", "configured"] = persona_status or (
            "configured" if persona_system else "default"
        )
        version = _PERSONA_VERSION_PREFIX + hashlib.sha256(
            persona_system.encode("utf-8")
        ).hexdigest()
        return cls(
            persona_system=persona_system,
            persona_version=version,
            persona_mode=persona_mode,
            persona_source=persona_source,
            persona_source_hash=persona_source_hash,
            persona_source_version=persona_source_version,
            persona_canonical_name=persona_canonical_name,
            persona_status=effective_status,
            configuration_version=configuration_version,
        )


@dataclass(kw_only=True)
class ReplyWork:
    """Delivery state reused by concrete human input and native Bot intent owners."""

    owner: ReplyOwner = field(init=False)
    submitted: float = field(default_factory=monotonic)
    planned_reply_calls_started: set[tuple[int, str, int]] = field(
        default_factory=lambda: set[tuple[int, str, int]]()
    )
    # One child phase; the parent retains its visible primary reply and lifecycle.
    followup_request: ModelRequest | None = None
    followup_history_messages: tuple[Message, ...] = ()
    followup_history_subjects: tuple[str, ...] = ()
    followup_binding: DecisionBinding | None = None
    followup_cancel: asyncio.Event = field(default_factory=asyncio.Event)
    followup_task: asyncio.Task[None] | None = None
    followup_active: bool = False
    followup_append: tuple[int, int] | None = None
    followup_calls_started: int = 0
    last_visible_segment: str | None = None
    main_llm_output: bool = False
    generation: int = 0
    abandon_code: str = "cancelled"
    b_gate_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reply_history_subjects: tuple[str, ...] = ()
    turn_state_snapshot: TurnStateSnapshot = field(default_factory=TurnStateSnapshot)

    @property
    def order(self) -> int:
        """Runtime-local enqueue order, assigned alongside the turn generation."""
        return self.generation


@dataclass
class Pending(ReplyWork):
    event: Event
    future: asyncio.Future[str]
    decision_done: asyncio.Event = field(default_factory=asyncio.Event)
    hold_ready: asyncio.Event = field(default_factory=asyncio.Event)
    decision_task: asyncio.Task[None] | None = None
    assessment: Turn | None = None
    decision_error: str = ""
    stage_a_decision: StageADecision | None = None
    # Frozen by the existing Conversation admission result, never inferred from trace retention.
    proactive_admitted: bool = False
    # Read-only projection of the existing owner window, scoped to this request.
    group_board: GroupStateSnapshot | None = None
    group_board_used: bool = False
    heat_sources: tuple[TimelineSourceProof, ...] = ()
    board_sources: tuple[TimelineSourceProof, ...] = ()
    history_board_sources: tuple[TimelineSourceProof, ...] = ()
    # Conversation-owned, deterministic weak-reply classification. This is never
    # supplied by Thinker and is only used to shape the existing reply request.
    weak_reply_kind: Literal["closing", "greeting", "companion"] | None = None
    # One allowlisted read-only chat command uses the ordinary delivery owner,
    # but never enters either model stage.
    help_reply: str | None = None
    echo_decision: EchoDecision | None = None
    element_match: ElementMatch | None = None
    food_command: tuple[str, str] | None = None
    diagnostic_command: DiagnosticCommand | None = None
    character_lookup: Literal["ccip", "animetrace"] | None = None
    interrupts: int = 0
    held: bool = False
    handed_off: bool = False
    running_claimed: bool = False
    hold_deadline: float | None = None
    hold_fallback_deadline: float | None = None
    hold_clarification: bool = False
    merged_items: list[Pending] = field(default_factory=lambda: list[Pending]())
    # Inputs received while this turn is generating or delivering output. They
    # remain independent durable requests until the B gate releases them.
    related_requests: list[Pending] = field(default_factory=lambda: list[Pending]())
    # The active reply owner watches this signal and shares this lock with each
    # candidate send gate so early B cannot overlap a segment already dispatching.
    related_changed: asyncio.Event = field(default_factory=asyncio.Event)
    # A source transfer already owns the next state change and must be drained.
    early_b_handoff_active: bool = False
    # After output preparation, the send gate owns any further B re-evaluation.
    preparation_ready: bool = False
    candidate_gate_active: bool = False
    inputs: list[Event] = field(default_factory=lambda: list[Event]())
    original_event_id: str = field(init=False)
    source_identity: SourceIdentity = field(init=False)
    mentioned: bool = field(init=False)
    reply_to: str = field(init=False)
    mention_targets: tuple[str, ...] = field(init=False)
    # None means unknown. N1 stores explicit evidence but does not infer topic links.
    topic_id: str | None = None
    revision: int = 0
    input_revision: int = 0

    @property
    def is_local_operation(self) -> bool:
        return (self.help_reply is not None or self.character_lookup is not None
                or self.food_command is not None or self.diagnostic_command is not None
                or self.element_match is not None)

    def __post_init__(self) -> None:
        self.owner = ReplyOwner(self.event.event_id, self.event.scope, self.event.user_id, "inbound_event")
        self.original_event_id = self.event.event_id
        self.source_identity = SourceIdentity(
            bot_id=self.event.scope.bot_id,
            group_id=self.event.scope.group_id if self.event.scope.kind == "group" else None,
            user_id=self.event.user_id,
            message_id=self.event.message_id,
            kind=self.event.scope.kind,
            private_user_id=(
                self.event.scope.private_user_id if self.event.scope.kind == "private" else None
            ),
        )
        self.mentioned = self.event.mentioned
        self.reply_to = self.event.reply_to
        self.mention_targets = self.event.mention_targets

    @property
    def topic_revision(self) -> int:
        """Revision of topic attribution; `revision` remains its compatibility alias."""
        return self.revision

    @topic_revision.setter
    def topic_revision(self, value: int) -> None:
        if type(value) is not int or value < 0:
            raise ValueError("topic revision must be a non-negative integer")
        self.revision = value

    def include_inputs(self, incoming: Iterable[Event]) -> bool:
        """Add new same-scope source events once, advancing only the input version."""
        by_id = {source.event_id: source for source in self.inputs}
        additions: list[Event] = []
        for source in incoming:
            if source.scope != self.event.scope:
                raise OperationError("cross_scope_merge")
            existing = by_id.get(source.event_id)
            if existing is not None:
                if existing != source:
                    raise OperationError("idempotency_conflict")
                continue
            by_id[source.event_id] = source
            additions.append(source)
        if not additions:
            return False
        self.inputs.extend(additions)
        self.input_revision += 1
        return True

    def decision_binding(self) -> DecisionBinding:
        return DecisionBinding(
            turn_id=self.original_event_id,
            generation=self.generation,
            topic_id=self.topic_id,
            topic_revision=self.topic_revision,
            input_revision=self.input_revision,
            persona_version=self.turn_state_snapshot.persona_version,
            configuration_version=self.turn_state_snapshot.configuration_version,
            context_version=self.turn_state_snapshot.context_version,
        )

    @property
    def evidence_kind(self) -> EvidenceKind:
        if self.mentioned and self.reply_to:
            return "mention_and_reply"
        if self.mentioned:
            return "mention"
        if self.reply_to:
            return "reply"
        return "none"

    def resolve_reply_topic(self, candidates: Iterable[Pending]) -> TopicResolution:
        """Resolve only an exact same-scope reply target, without mutating either item."""
        if not self.reply_to:
            return TopicResolution("unknown", None, self.evidence_kind, self.topic_revision)

        matches = [
            candidate
            for candidate in candidates
            if candidate is not self
            and candidate.original_event_id != self.original_event_id
            and candidate.event.scope.key == self.event.scope.key
            and candidate.source_identity.message_id == self.reply_to
        ]
        if not matches:
            return TopicResolution("unknown", None, self.evidence_kind, self.topic_revision)

        matched_event_ids = tuple(dict.fromkeys(item.original_event_id for item in matches))
        known_topic_ids = {
            item.topic_id
            for item in [self, *matches]
            if item.topic_id is not None
        }
        if len(known_topic_ids) > 1:
            return TopicResolution(
                "conflict", None, self.evidence_kind, self.topic_revision, matched_event_ids
            )

        # A colliding message ID is usable only when every matching request already
        # agrees on one explicit topic. Otherwise there is no unique source to inherit.
        if len(matched_event_ids) > 1:
            candidate_topic_ids = {item.topic_id for item in matches}
            if None in candidate_topic_ids or len(candidate_topic_ids) != 1:
                return TopicResolution(
                    "unknown", None, self.evidence_kind, self.topic_revision, matched_event_ids
                )

        topic_id = next(iter(known_topic_ids), matches[0].original_event_id)
        revision = self.topic_revision + (topic_id != self.topic_id)
        return TopicResolution(
            "resolved", topic_id, self.evidence_kind, revision, matched_event_ids
        )


@dataclass
class ContactPending(ReplyWork):
    """An already-selected native intent, with no human event or Stage A state."""

    contact: BotContactInput
    future: asyncio.Future[str]

    def __post_init__(self) -> None:
        self.owner = ReplyOwner(
            self.contact.request_id, self.contact.scope, self.contact.target_user_id, "bot_contact",
        )

    def decision_binding(self) -> DecisionBinding:
        return DecisionBinding(
            turn_id=self.owner.request_id, generation=self.generation,
            topic_id=None, topic_revision=0, input_revision=0,
            persona_version=self.turn_state_snapshot.persona_version,
            configuration_version=self.turn_state_snapshot.configuration_version,
            context_version=self.turn_state_snapshot.context_version,
        )


type RuntimeWork = Pending | ContactPending


@dataclass
class Running:
    item: RuntimeWork
    turn: Turn
    task: asyncio.Task[None]
    preparation: Lookahead | None = None


@dataclass
class Lookahead:
    """One bounded preparation slot for the FIFO head behind an active owner."""

    item: Pending
    turn: Turn
    binding: DecisionBinding
    task: asyncio.Task[None]
    result: object | None = None
    error: BaseException | None = None


class SessionRuntime:
    def __init__(
        self,
        capacity: int,
        active_limit: int,
        run: Callable[[RuntimeWork, Turn], Awaitable[None]],
        abandon: Callable[[RuntimeWork], Awaitable[None]],
        expire_held: Callable[[Pending], Awaitable[None]] | None = None,
        *,
        prepare_held: Callable[[Pending], Awaitable[None]] | None = None,
        lookahead_timeout: float = 0.0,
    ) -> None:
        self.capacity, self.active_limit = capacity, active_limit
        self.run, self.abandon = run, abandon
        self.expire_held = expire_held
        self.prepare_held = prepare_held
        self.waiting: OrderedDict[SessionKey, deque[RuntimeWork]] = OrderedDict()
        self.held: OrderedDict[SessionKey, deque[Pending]] = OrderedDict()
        self.active: dict[SessionKey, Running] = {}
        self.lookaheads: dict[SessionKey, Lookahead] = {}
        self.lookahead_timeout = lookahead_timeout
        self._hold_expiries: dict[int, asyncio.Task[None]] = {}
        self.accepting = False
        self._generation = 0
        self.completed = 0
        self.interrupted = 0

    @property
    def queued(self) -> int:
        return (
            sum(len(items) for items in self.waiting.values())
            + sum(len(items) for items in self.held.values())
            + sum(
                len(running.item.related_requests)
                for running in self.active.values() if isinstance(running.item, Pending)
            )
        )

    def held_for(self, key: SessionKey) -> tuple[Pending, ...]:
        return tuple(self.held.get(key, ()))

    def start_lookahead(
        self,
        key: SessionKey,
        prepare: Callable[[Pending, Turn], Awaitable[object]],
    ) -> Lookahead | None:
        """Start at most one model preparation for the current FIFO head."""
        if not self.accepting or key not in self.active:
            return None
        queued = self.waiting.get(key)
        if not queued:
            return None
        item = queued[0]
        if (
            not isinstance(item, Pending)
            or item.held
            or item.handed_off
            or item.future.done()
            or item.related_requests
            or not item.decision_done.is_set()
            or item.decision_error
            or item.stage_a_decision is None
            or item.stage_a_decision.outcome != "complete"
        ):
            return None
        current = self.lookaheads.get(key)
        if current is not None:
            return current if current.item is item else None
        turn = Turn(item.owner.request_id, item.generation)
        if self.lookahead_timeout > 0:
            turn.deadline = item.submitted + self.lookahead_timeout
        slot = Lookahead(
            item=item,
            turn=turn,
            binding=item.decision_binding(),
            task=asyncio.create_task(
                self._prepare_lookahead(prepare, item, turn),
                name=f"lookahead:{turn.generation}",
            ),
        )
        self.lookaheads[key] = slot
        return slot

    async def _prepare_lookahead(
        self,
        prepare: Callable[[Pending, Turn], Awaitable[object]],
        item: Pending,
        turn: Turn,
    ) -> None:
        slot = self.lookaheads.get(item.event.scope.key)
        try:
            result = await prepare(item, turn)
        except BaseException as exc:
            if slot is not None and slot.item is item:
                slot.error = exc
        else:
            if slot is not None and slot.item is item:
                slot.result = result

    def ready_lookahead(self, key: SessionKey) -> Lookahead | None:
        """Return the completed FIFO-head preparation without waiting for it."""
        slot = self.lookaheads.get(key)
        queued = self.waiting.get(key)
        if (
            slot is None
            or not queued
            or queued[0] is not slot.item
            or not slot.task.done()
            or slot.error is not None
        ):
            return None
        return slot

    async def await_lookahead(self, item: Pending) -> Lookahead | None:
        slot = self.lookahead_for(item)
        if slot is None or slot.item is not item:
            return None
        await slot.task
        return slot

    def lookahead_for(self, item: Pending) -> Lookahead | None:
        key = item.event.scope.key
        slot = self.lookaheads.get(key)
        if slot is not None and slot.item is item:
            return slot
        running = self.active.get(key)
        if running is not None and running.item is item:
            return running.preparation
        return None

    def consume_lookahead(self, key: SessionKey, item: Pending) -> bool:
        """Remove a FIFO candidate after its own delivery attempt is terminal."""
        queued = self.waiting.get(key)
        slot = self.lookaheads.get(key)
        if not queued or queued[0] is not item or slot is None or slot.item is not item:
            return False
        queued.popleft()
        if not queued:
            self.waiting.pop(key, None)
        self.lookaheads.pop(key, None)
        return True

    async def discard_lookahead(
        self, key: SessionKey, *, item: RuntimeWork | None = None
    ) -> None:
        slot = self.lookaheads.get(key)
        active = self.active.get(key)
        if slot is None and active is not None and (
            item is None or active.item is item
        ):
            slot = active.preparation
            if slot is not None:
                active.preparation = None
        if slot is None or item is not None and slot.item is not item:
            return
        if self.lookaheads.get(key) is slot:
            self.lookaheads.pop(key, None)
        if not slot.task.done():
            slot.turn.invalidate("lookahead_discarded")
            slot.task.cancel()
            await asyncio.gather(slot.task, return_exceptions=True)

    async def release_held(self, item: Pending, *, pump: bool = True) -> bool:
        key = item.event.scope.key
        pending = self.held.get(key)
        if pending is None:
            return False
        try:
            pending.remove(item)
        except ValueError:
            return False
        if not pending:
            self.held.pop(key, None)
        item.held = False
        expiry = self._hold_expiries.pop(id(item), None)
        if expiry is not None and expiry is not asyncio.current_task():
            expiry.cancel()
            await asyncio.gather(expiry, return_exceptions=True)
        if pump:
            self._pump()
        return True

    def check_capacity(self, key: SessionKey, *, replacing_held: bool = False) -> None:
        _ = key  # Retain the established call signature; capacity is shared across sessions.
        if not self.accepting:
            raise OperationError("stopping")
        if self.queued >= self.capacity and not replacing_held:
            raise OperationError("busy")

    def enqueue(self, item: RuntimeWork, *, earlier_generation: int | None = None) -> None:
        key = item.owner.scope.key
        self._generation += 1
        item.generation = earlier_generation or self._generation
        items = self.waiting.setdefault(key, deque())
        if earlier_generation is None:
            items.append(item)
        else:
            ordered = list(items)
            insertion = next(
                (index for index, queued in enumerate(ordered) if queued.generation > item.generation),
                len(ordered),
            )
            ordered.insert(insertion, item)
            self.waiting[key] = deque(ordered)
        self._pump()

    def _pump(self) -> None:
        if not self.accepting:
            return
        for key in list(self.waiting):
            if len(self.active) >= self.active_limit:
                break
            if key in self.active:
                continue
            items = self.waiting[key]
            item = items.popleft()
            if not items:
                del self.waiting[key]
            else:
                self.waiting.move_to_end(key)
            slot = self.lookaheads.get(key)
            turn = (
                slot.turn
                if slot is not None and slot.item is item
                else Turn(item.owner.request_id, item.generation)
            )
            preparation = slot if slot is not None and slot.item is item else None
            if preparation is not None:
                self.lookaheads.pop(key, None)
            task = asyncio.create_task(self._run(key, item, turn), name=f"turn:{turn.generation}")
            self.active[key] = Running(item, turn, task, preparation)

    def pump(self) -> None:
        """Resume eligible sessions after an owner releases a held slot."""
        self._pump()

    async def _run(self, key: SessionKey, item: RuntimeWork, turn: Turn) -> None:
        try:
            await self.run(item, turn)
        finally:
            await self.discard_lookahead(key, item=item)
            running = self.active.get(key)
            if running is not None and running.item is item and running.preparation is not None:
                preparation = running.preparation
                if not preparation.task.done():
                    preparation.turn.invalidate("lookahead_discarded")
                    preparation.task.cancel()
                    await asyncio.gather(preparation.task, return_exceptions=True)
            if isinstance(item, Pending) and item.held and self.accepting:
                self.held.setdefault(key, deque()).append(item)
                if self.expire_held is not None and item.hold_deadline is not None:
                    expiry = asyncio.create_task(self._expire_after(item))
                    self._hold_expiries[id(item)] = expiry
            elif not isinstance(item, Pending) or not item.held:
                self.completed += 1
            # All children must be drained by the application before releasing the session.
            self.active.pop(key, None)
            self._pump()

    async def _expire_after(self, item: Pending) -> None:
        assert item.hold_deadline is not None and self.expire_held is not None
        try:
            if self.prepare_held is not None and item.hold_fallback_deadline is not None:
                await asyncio.sleep(max(0, item.hold_fallback_deadline - monotonic()))
                if item.held:
                    await self.prepare_held(item)
                if not item.held:
                    return
            await asyncio.sleep(max(0, item.hold_deadline - monotonic()))
            await self.expire_held(item)
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        self.accepting = False
        active = list(self.active.values())
        lookaheads = list(self.lookaheads.values())
        lookaheads.extend(
            running.preparation
            for running in active
            if running.preparation is not None
        )
        for running in active:
            running.turn.invalidate("stopping")
            running.task.cancel()
        for slot in lookaheads:
            slot.turn.invalidate("stopping")
            slot.task.cancel()
        await asyncio.gather(
            *(r.task for r in active),
            *(slot.task for slot in lookaheads),
            return_exceptions=True,
        )
        self.lookaheads.clear()
        for running in active:
            if not running.item.future.done():
                await self.abandon(running.item)
        self.active.clear()
        pending = [item for items in self.waiting.values() for item in items]
        self.waiting.clear()
        held = [item for items in self.held.values() for item in items]
        self.held.clear()
        expiry_tasks = list(self._hold_expiries.values())
        self._hold_expiries.clear()
        for expiry in expiry_tasks:
            expiry.cancel()
        await asyncio.gather(*expiry_tasks, return_exceptions=True)
        for item in held:
            item.held = False
            if not item.future.done():
                await self.abandon(item)
        for item in pending:
            await self.abandon(item)
