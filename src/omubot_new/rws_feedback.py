"""Default-off, receipt-bound delayed RWS feedback and legacy scalar bandit.

Root connects the sole authorized ingress and calls these async APIs outside
Policy.dispatch_boundary. This owner adds no transport, task, persistent table,
model request or second Policy cleanup callback. All retained data is body-free.
"""

from __future__ import annotations

import math
import random
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from time import time
from typing import Literal

from .policy import Policy
from .social_feedback import GroupSocialNotice, message_sentiment, reaction_sentiment
from .store import Store, StoreConnection, request_digest
from .types import Event, OperationError, Scope

FEEDBACK_VERSION = "legacy-social-reward-300s-typed-v2"
BANDIT_VERSION = "legacy-thompson-per-observation-v1"


@dataclass(frozen=True, slots=True)
class BanditParameters:
    algo: Literal["epsilon", "thompson"] = "thompson"
    theta: float = 0.5
    epsilon: float = 0.1
    learning_rate: float = 0.05
    min_theta: float = 0.35
    max_theta: float = 0.65
    min_obs: int = 50
    decay_per_obs: float = 0.99
    frozen: bool = True

    def __post_init__(self) -> None:
        values = (
            self.theta,
            self.epsilon,
            self.learning_rate,
            self.min_theta,
            self.max_theta,
            self.decay_per_obs,
        )
        if (
            not all(math.isfinite(v) for v in values)
            or self.algo not in {"epsilon", "thompson"}
            or not 0 <= self.min_theta <= self.theta <= self.max_theta <= 1
            or not 0 <= self.epsilon <= 1
            or not 0 <= self.decay_per_obs <= 1
            or self.learning_rate < 0
            or type(self.min_obs) is not int
            or self.min_obs < 0
        ):
            raise ValueError("invalid bandit parameters")


_DEFAULT_PARAMETERS = BanditParameters()


class RwsBandit:
    """Exact legacy selected algorithms; only theta adapts, never RWS weights."""

    def __init__(
        self, parameters: BanditParameters = _DEFAULT_PARAMETERS, *, rng: random.Random | None = None
    ) -> None:
        self.parameters = parameters
        self.rng = random.Random() if rng is None else rng
        self.theta = parameters.theta
        self.observations = 0
        self.last_reward = 0.0
        self.alpha = self.beta = 2.0

    def _clamp(self, value: float) -> float:
        p = self.parameters
        return min(p.max_theta, max(p.min_theta, value))

    def current_theta(self) -> float:
        p = self.parameters
        base = self._clamp(self.theta)
        if p.frozen or self.observations < p.min_obs:
            return base
        if p.algo == "thompson":
            return self._clamp(1 - self.rng.betavariate(max(0.01, self.alpha), max(0.01, self.beta)))
        if self.rng.random() < p.epsilon:
            return self._clamp(base + self.rng.choice((-0.05, 0.05)))
        return base

    def observe(self, *, fired: bool, reward: float) -> None:
        if not math.isfinite(reward) or not -1 <= reward <= 1:
            raise ValueError("invalid reward")
        self.observations += 1
        self.last_reward = reward
        p = self.parameters
        if p.frozen:
            return
        if p.algo == "epsilon":
            direction = -1 if fired and reward < 0 else 1 if not fired and reward < 0 else 0
            self.theta = self._clamp(self.theta + direction * p.learning_rate)
            return
        self.alpha = max(1.0, self.alpha * p.decay_per_obs)
        self.beta = max(1.0, self.beta * p.decay_per_obs)
        if fired:
            if reward > 0:
                self.alpha += reward
            else:
                self.beta -= reward
        elif reward < 0:
            self.alpha -= reward * 0.5
        self.theta = self._clamp(1 - self.alpha / (self.alpha + self.beta))


@dataclass(frozen=True, slots=True)
class BanditSnapshot:
    """Transient local proof for the threshold actually adopted by a Pending."""

    scope: Scope
    theta: float
    subjects: tuple[str, ...]
    generation: int
    enabled: bool
    bandit_enabled: bool
    parameters: BanditParameters
    state: tuple[float, float, float, int, float] | None
    bandit: RwsBandit | None = field(repr=False, compare=False)
    version: str = BANDIT_VERSION


@dataclass(frozen=True, slots=True)
class ReactionEvent:
    event_id: str
    subject_id: str
    observed_at: float
    reply_to: str
    mentions_bot: bool
    direct_stop: bool
    topic_switched: bool = False
    sentiment: str = "neutral"
    banned_bot: bool = False
    kind: str = "message"
    emoji_code: str = ""
    action_key: str = ""


@dataclass(frozen=True, slots=True)
class DecisionSource:
    event_id: str
    user_id: str
    scope: Scope
    digest: str


@dataclass(frozen=True, slots=True)
class PendingFeedback:
    source: DecisionSource
    fired: bool
    action_key: str | None
    receipt: str | None
    started_at: float
    coverage_generation: int
    score: float


@dataclass(frozen=True, slots=True)
class FeedbackMeasurement:
    event_id: str
    scope: Scope
    fired: bool
    acknowledged: bool | None
    went_cold: bool | None
    explicit_negative: bool | None
    reward: float | None
    source_event_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    version: str = FEEDBACK_VERSION


def compute_reward(*, acknowledged: bool, went_cold: bool, explicit_negative: bool) -> float:
    return min(1.0, max(-1.0, float(acknowledged) - 0.8 * went_cold - explicit_negative))


class RwsFeedback:
    def __init__(
        self,
        store: Store,
        policy: Policy,
        *,
        enabled: bool = False,
        bandit_enabled: bool = False,
        parameters: BanditParameters = _DEFAULT_PARAMETERS,
        window_s: float = 300.0,
        max_pending: int = 128,
        max_groups: int = 64,
        max_events: int = 256,
        clock: Callable[[], float] = time,
    ) -> None:
        if store is not policy.store:
            raise ValueError("feedback requires the same Store/Policy")
        if not math.isfinite(window_s) or window_s <= 0 or min(max_pending, max_groups, max_events) < 1:
            raise ValueError("invalid feedback bounds")
        self.store, self.policy = store, policy
        self.enabled, self.bandit_enabled = enabled, bandit_enabled
        self.parameters, self.window_s, self.clock = parameters, window_s, clock
        self.max_pending, self.max_groups, self.max_events = max_pending, max_groups, max_events
        self._pending: OrderedDict[tuple[str, str, str], PendingFeedback] = OrderedDict()
        self._events: OrderedDict[tuple[str, str], OrderedDict[str, ReactionEvent]] = OrderedDict()
        self._completed: OrderedDict[tuple[str, str, str], float] = OrderedDict()
        self._bandits: dict[tuple[str, str], RwsBandit] = {}
        self._learned_subjects: dict[tuple[str, str], set[str]] = {}
        self._coverage = False
        self._notice_coverage = False
        self._generation = 0

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def ingress_connected(self) -> bool:
        return self.enabled and self._coverage

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:
            self._coverage = False
            self.clear()

    def connect_ingress(self, connected: bool) -> None:
        """Root certifies a complete, continuous producer; gaps cancel its windows."""
        if connected != self._coverage:
            self.clear()
        self._coverage = connected

    def connect_notice_ingress(self, connected: bool) -> None:
        """The authenticated ingress owner certifies notice delivery coverage separately."""
        self._notice_coverage = connected

    def clear(self) -> None:
        self._generation += 1
        self._pending.clear()
        self._events.clear()
        self._completed.clear()
        self._bandits.clear()
        self._learned_subjects.clear()

    def _now(self) -> float:
        now = self.clock()
        if not math.isfinite(now):
            raise ValueError("invalid feedback clock")
        return now

    @staticmethod
    def _key(scope: Scope) -> tuple[str, str]:
        return scope.bot_id, scope.group_id

    def _prune(self, now: float) -> None:
        for key, events in tuple(self._events.items()):
            needed = [
                pending.started_at for pending_key, pending in self._pending.items() if pending_key[:2] == key
            ]
            cutoff = min(now - self.window_s, min(needed)) if needed else now - self.window_s
            for event_id, event in tuple(events.items()):
                if event.observed_at < cutoff:
                    del events[event_id]
            if not events:
                del self._events[key]
        for key, settled_at in tuple(self._completed.items()):
            if settled_at < now - self.window_s:
                del self._completed[key]

    def _read_gate(self, db: StoreConnection, subject: str, scope: Scope) -> None:
        self.policy.check_transaction(db, subject, scope, "message.read", "", "", False, False)

    def _receipt_gate(self, db: StoreConnection, pending: PendingFeedback) -> str:
        event = pending.source
        request = db.execute("SELECT digest FROM requests WHERE id=?", (event.event_id,)).fetchone()
        row = db.execute("SELECT * FROM actions WHERE id=?", (pending.action_key,)).fetchone()
        if (
            request is None
            or request["digest"] != event.digest
            or row is None
            or row["request_id"] != event.event_id
            or row["subject"] != event.user_id
            or row["bot_id"] != event.scope.bot_id
            or row["group_id"] != event.scope.group_id
            or row["action"] not in {"message.reply", "message.sticker"}
            or row["state"] != "succeeded"
            or not str(row["receipt"]).strip()
            or pending.receipt is not None
            and row["receipt"] != pending.receipt
        ):
            raise OperationError("invalid_feedback_receipt")
        return str(row["receipt"])

    async def record(self, event: Event, *, fired: bool, score: float, action_key: str | None = None) -> bool:
        if not self.enabled:
            return False
        scope = event.scope
        if not isinstance(scope, Scope):
            raise OperationError("unsupported_scope")
        if not math.isfinite(score) or not 0 <= score <= 1 or (fired != (action_key is not None)):
            raise ValueError("invalid feedback decision")
        now = self._now()
        if event.event_time is None or not now - self.window_s < event.event_time <= now:
            raise OperationError("missing_current_feedback_timestamp")
        async with self.policy.dispatch_boundary:
            generation = self._generation
            source = DecisionSource(event.event_id, event.user_id, scope, request_digest(event))
            pending = PendingFeedback(source, fired, action_key, None, now, generation, score)

            def validate(db: StoreConnection) -> str | None:
                self._read_gate(db, event.user_id, scope)
                return self._receipt_gate(db, pending) if fired else None

            receipt = await self.store.transaction(validate)
            if not self.enabled or generation != self._generation:
                return False
            self._prune(now)
            key = (*self._key(scope), event.event_id)
            old = self._pending.get(key)
            if old is not None:
                if (old.source, old.fired, old.action_key, old.score) != (source, fired, action_key, score):
                    raise OperationError("idempotency_conflict")
                return False
            if key in self._completed:
                return False
            groups = (
                {pending_key[:2] for pending_key in self._pending} | set(self._events) | set(self._bandits)
            )
            if (
                len(self._pending) >= self.max_pending
                or self._key(scope) not in groups
                and len(groups) >= self.max_groups
            ):
                raise OperationError("feedback_capacity")
            self._pending[key] = PendingFeedback(source, fired, action_key, receipt, now, generation, score)
            return True

    async def observe(self, event: Event) -> bool:
        if not self.enabled:
            return False
        scope = event.scope
        if not isinstance(scope, Scope):
            raise OperationError("unsupported_scope")
        now = self._now()
        if event.event_time is None or not now - self.window_s < event.event_time <= now:
            self.connect_ingress(False)
            return False
        async with self.policy.dispatch_boundary:
            generation = self._generation
            try:
                await self.store.transaction(lambda db: self._read_gate(db, event.user_id, scope))
            except OperationError as exc:
                if exc.code != "denied":
                    raise
                self.connect_ingress(False)
                return False
            if not self.enabled or generation != self._generation:
                return False
            self._prune(now)
            key = self._key(scope)
            if key not in self._events and len(self._events) >= self.max_groups:
                self.connect_ingress(False)
                raise OperationError("feedback_capacity")
            events = self._events.setdefault(key, OrderedDict())
            reaction = ReactionEvent(
                event.event_id,
                event.user_id,
                now,
                event.reply_to,
                event.mentioned and scope.bot_id in event.mention_targets,
                event.text.strip() == "别说话",
                sentiment=message_sentiment(event.text),
            )
            previous = events.get(event.event_id)
            if previous is not None:
                if previous != reaction:
                    # Arrival time cannot turn replay into a new reaction.
                    if (
                        previous.subject_id,
                        previous.reply_to,
                        previous.mentions_bot,
                        previous.direct_stop,
                    ) != (
                        reaction.subject_id,
                        reaction.reply_to,
                        reaction.mentions_bot,
                        reaction.direct_stop,
                    ):
                        raise OperationError("idempotency_conflict")
                return False
            if len(events) >= self.max_events:
                self.connect_ingress(False)
                raise OperationError("feedback_capacity")
            events[event.event_id] = reaction
            return True

    def observe_topic(self, event: Event, *, topic_switched: bool) -> None:
        """Consume the Conversation topic owner's already-authorized explicit reframe."""
        if not isinstance(event.scope, Scope):
            return
        events = self._events.get(self._key(event.scope))
        old = None if events is None else events.get(event.event_id)
        if old is not None and topic_switched:
            assert events is not None
            events[event.event_id] = replace(old, topic_switched=True)

    def _notice_receipt_gate(self, db: StoreConnection, event: ReactionEvent, scope: Scope) -> None:
        row = db.execute("SELECT * FROM actions WHERE id=?", (event.action_key,)).fetchone()
        if (row is None or row["bot_id"] != scope.bot_id or row["group_id"] != scope.group_id
                or row["action"] not in {"message.reply", "message.sticker"}
                or row["state"] != "succeeded" or row["receipt"] != event.reply_to):
            raise OperationError("invalid_feedback_receipt")
        self._read_gate(db, str(row["subject"]), scope)

    async def observe_notice(self, notice: GroupSocialNotice, *, action_key: str = "") -> bool:
        if not self.enabled:
            return False
        now = self._now()
        if not now-self.window_s < notice.observed_at <= now:
            return False
        polarity, _ = reaction_sentiment(notice.emoji_code)
        event = ReactionEvent(notice.event_id, notice.actor_id, notice.observed_at,
            notice.message_id, False, False, sentiment=polarity if notice.kind == "reaction" else "neutral",
            banned_bot=notice.kind == "ban" and notice.target_id == notice.scope.bot_id,
            kind=notice.kind, emoji_code=notice.emoji_code, action_key=action_key)
        async with self.policy.dispatch_boundary:
            def validate(db: StoreConnection) -> None:
                self._read_gate(db, notice.actor_id, notice.scope)
                if notice.kind == "reaction":
                    self._notice_receipt_gate(db, event, notice.scope)
            await self.store.transaction(validate)
            self._prune(now)
            key = self._key(notice.scope)
            if key not in self._events and len(self._events) >= self.max_groups:
                self.connect_ingress(False)
                raise OperationError("feedback_capacity")
            events = self._events.setdefault(key, OrderedDict())
            if notice.removed:
                for identity, old in tuple(events.items()):
                    if (old.kind, old.subject_id, old.reply_to, old.emoji_code) == (
                            "reaction", notice.actor_id, notice.message_id, notice.emoji_code):
                        del events[identity]
                return False
            previous = events.get(event.event_id)
            if previous is not None:
                if previous != event:
                    raise OperationError("idempotency_conflict")
                return False
            if len(events) >= self.max_events:
                self.connect_ingress(False)
                raise OperationError("feedback_capacity")
            events[event.event_id] = event
            return True

    def purge_unreadable(self, has_read_permission: Callable[[str, Scope], bool]) -> None:
        """Call from the existing Conversation cleanup owner; never bind a second callback."""
        affected: set[tuple[str, str]] = set()
        for key, pending in tuple(self._pending.items()):
            if not has_read_permission(pending.source.user_id, pending.source.scope):
                affected.add(key[:2])
        for key, events in self._events.items():
            scope = Scope(bot_id=key[0], group_id=key[1])
            if any(not has_read_permission(event.subject_id, scope) for event in events.values()):
                affected.add(key)
        for key, subjects in self._learned_subjects.items():
            scope = Scope(bot_id=key[0], group_id=key[1])
            if any(not has_read_permission(subject, scope) for subject in subjects):
                affected.add(key)
        for key in affected:
            self._events.pop(key, None)
            self._bandits.pop(key, None)
            self._learned_subjects.pop(key, None)
            for pending_key in tuple(self._pending):
                if pending_key[:2] == key:
                    del self._pending[pending_key]

    def learned_subjects(self, scope: Scope) -> tuple[str, ...]:
        """Immutable provenance view; callers cannot mutate the owner's subject set."""
        return tuple(sorted(self._learned_subjects.get(self._key(scope), ())))

    @staticmethod
    def _bandit_state(bandit: RwsBandit | None) -> tuple[float, float, float, int, float] | None:
        return (
            None
            if bandit is None
            else (bandit.theta, bandit.alpha, bandit.beta, bandit.observations, bandit.last_reward)
        )

    def theta_snapshot_transaction(self, db: StoreConnection, scope: Scope) -> BanditSnapshot:
        """Adopt theta in the same authorized transaction as RWS sources.

        The caller holds Policy.dispatch_boundary, just as at the Actions gates.
        Retain this exact snapshot locally and assert it at model/send preflight;
        do not resample theta after an await or upload the snapshot to a model.
        """
        subjects = self.learned_subjects(scope)
        for subject in subjects:
            self._read_gate(db, subject, scope)
        bandit = self._bandits.get(self._key(scope))
        theta = (
            self.parameters.theta
            if not self.enabled or not self.bandit_enabled or bandit is None
            else bandit.current_theta()
        )
        return BanditSnapshot(
            scope,
            theta,
            subjects,
            self._generation,
            self.enabled,
            self.bandit_enabled,
            self.parameters,
            self._bandit_state(bandit),
            bandit,
        )

    def assert_theta_snapshot_transaction(self, db: StoreConnection, snapshot: BanditSnapshot) -> None:
        """Reject changed owner/Bandit/source state and naturally expired read grants."""
        bandit = self._bandits.get(self._key(snapshot.scope))
        if (
            snapshot.generation != self._generation
            or snapshot.enabled != self.enabled
            or snapshot.bandit_enabled != self.bandit_enabled
            or snapshot.parameters != self.parameters
            or snapshot.bandit is not bandit
            or snapshot.state != self._bandit_state(bandit)
            or snapshot.subjects != self.learned_subjects(snapshot.scope)
        ):
            raise OperationError("stale_rws_bandit")
        for subject in snapshot.subjects:
            self._read_gate(db, subject, snapshot.scope)

    def current_theta(self, scope: Scope) -> float:
        """Diagnostic read only; actual decisions use theta_snapshot_transaction."""
        bandit = self._bandits.get(self._key(scope))
        return (
            self.parameters.theta
            if not self.enabled or not self.bandit_enabled or bandit is None
            else bandit.current_theta()
        )

    async def settle_due(self) -> tuple[FeedbackMeasurement, ...]:
        if not self.enabled:
            return ()
        now = self._now()
        due: list[tuple[tuple[str, str, str], PendingFeedback, tuple[ReactionEvent, ...]]] = []
        # Freeze every due window before draining or awaiting. Incoming observe
        # can prune the live cache while earlier due windows are being checked.
        for key, pending in self._pending.items():
            if now - pending.started_at < self.window_s:
                continue
            retained = self._events.get(self._key(pending.source.scope))
            events = (
                ()
                if retained is None
                else tuple(
                    event
                    for event in retained.values()
                    if pending.started_at < event.observed_at <= pending.started_at + self.window_s
                )
            )
            due.append((key, pending, events))
        # Cancellation drains due items, matching the final legacy cancel contract.
        for key, _, _ in due:
            del self._pending[key]
            self._completed[key] = now
        results: list[FeedbackMeasurement] = []
        for _key, pending, events in due:
            scope = pending.source.scope
            group = self._key(scope)
            async with self.policy.dispatch_boundary:

                def validate(
                    db: StoreConnection,
                    pending: PendingFeedback = pending,
                    scope: Scope = scope,
                    events: tuple[ReactionEvent, ...] = events,
                ) -> None:
                    self._read_gate(db, pending.source.user_id, scope)
                    if pending.fired:
                        self._receipt_gate(db, pending)
                    for event in events:
                        self._read_gate(db, event.subject_id, scope)
                        if event.kind == "reaction":
                            self._notice_receipt_gate(db, event, scope)

                await self.store.transaction(validate)
                if not self.enabled or pending.coverage_generation != self._generation:
                    continue
                connected = self._coverage
                addressed = tuple(
                    event
                    for event in events
                    if (event.mentions_bot or event.banned_bot
                        or (pending.fired and event.reply_to == pending.receipt))
                )
                ack = bool(addressed) if pending.fired else bool(events)
                neg = any(event.direct_stop or event.banned_bot
                          or event.sentiment == "negative" for event in addressed)
                cold = ((not bool(events) or any(event.topic_switched for event in events))
                        if connected else None)
                reward = (
                    compute_reward(acknowledged=ack, went_cold=bool(cold), explicit_negative=neg)
                    if connected
                    else None
                )
                reasons = () if self._notice_coverage else ("ban_producer_missing",)
                if not connected:
                    reasons += ("complete_ingress_window_missing",)
                measurement = FeedbackMeasurement(
                    pending.source.event_id,
                    scope,
                    pending.fired,
                    ack,
                    cold,
                    neg,
                    reward,
                    tuple(event.event_id for event in events),
                    reasons,
                )
                results.append(measurement)
                if self.bandit_enabled and reward is not None:
                    subjects = self._learned_subjects.setdefault(group, set())
                    additions = {pending.source.user_id, *(event.subject_id for event in events)}
                    if len(subjects | additions) > self.max_events or (
                        group not in self._bandits and len(self._bandits) >= self.max_groups
                    ):
                        raise OperationError("feedback_capacity")
                    subjects.update(additions)
                    bandit = self._bandits.setdefault(group, RwsBandit(self.parameters))
                    bandit.observe(fired=pending.fired, reward=reward)
        return tuple(results)
