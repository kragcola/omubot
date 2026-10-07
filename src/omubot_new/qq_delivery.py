"""One account's bounded RAM admission owner; Store owns persistent evidence.

All synchronous state mutations run on the owning asyncio event loop. No RAM
mutation holds a lock across Store, candidate, Policy or transport waits. Each
caller owns its wait task; this owner creates no scheduler or transport tasks.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from math import ceil
from typing import Literal

from .qq_ownership import QQAccountOwnership
from .store import Store
from .types import (
    OperationError,
    QQAdmission,
    QQAdmissionBinding,
    QQDeliveryLimits,
    QQDeliverySnapshot,
    QQQuotaAttempt,
    QQScopeKey,
    QQStateRow,
    QQWriteGrant,
    QQWriteSpec,
)


@dataclass(slots=True)
class _Waiter:
    spec: QQWriteSpec
    binding: QQAdmissionBinding
    deadline: float
    check: Callable[[], None]
    not_before: float
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    admission: QQAdmission | None = None
    failure: Exception | None = None


@dataclass(slots=True)
class _Active:
    admission: QQAdmission
    check: Callable[[], None]
    epoch: int
    target_revision: int
    batch_id: str = ""
    preparing: bool = False
    committed: bool = False
    started: bool = False
    invalid_reason: str = ""


@dataclass(frozen=True, slots=True)
class QQTestBatchStatus:
    batch_id: str
    scope_key: QQScopeKey
    write_limit: Literal[6, 12]
    deadline: float
    committed_cost: int
    ended: bool
    reason: str


class QQDelivery:
    def __init__(
        self,
        store: Store,
        account_id: str,
        limits: QQDeliveryLimits,
        *,
        ownership: QQAccountOwnership | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.account_id = account_id
        self.limits = limits
        self._ownership = ownership
        self._ownership_acquired = False
        self._identity_confirmed = False
        self._mono = monotonic
        self._utc = utc_clock
        self._opened = False
        self._closing = False
        self._epoch = 0
        self._budget_generation = 0
        self._snapshot: QQDeliverySnapshot | None = None
        self._targets: dict[QQScopeKey, QQStateRow] = {}
        self._target_attempts: dict[QQScopeKey, tuple[QQQuotaAttempt, ...]] = {}
        self._target_generation: dict[QQScopeKey, int] = {}
        self._account_ready = 0.0
        self._target_ready: dict[QQScopeKey, float] = {}
        self._local_holds: dict[QQScopeKey | None, tuple[int, str]] = {}
        self._hold_sequence = 0
        self._queues: dict[QQScopeKey, deque[_Waiter]] = {}
        self._last_target: QQScopeKey | None = None
        self._active: _Active | None = None
        self._test_batch: QQTestBatchStatus | None = None

    @property
    def snapshot(self) -> QQDeliverySnapshot:
        if self._snapshot is None:
            raise OperationError("qq_governor_not_open")
        return self._snapshot

    @property
    def pending_count(self) -> int:
        return sum(len(queue) for queue in self._queues.values())

    @property
    def pending_counts(self) -> dict[QQScopeKey, int]:
        return {scope: len(queue) for scope, queue in self._queues.items()}

    def spacing_segment_limit(
        self, scope_key: QQScopeKey, *, deadline: float, limit: int,
        first_dispatch_deadline: float | None = None, group_seconds: float | None = None,
    ) -> int:
        """Optimistic layout ceiling from known spacing, never an admission.

        Quotas, queue waits, wire duration and subsequent settlements can still
        reduce availability. The existing per-write admission remains mandatory.
        This read does not refresh, reserve, spend, unhold or mutate any state.
        """
        first = max(self._mono(), self._account_ready, self._target_ready.get(scope_key, 0.0))
        if first_dispatch_deadline is not None and first >= first_dispatch_deadline:
            return 0
        if group_seconds is not None:
            deadline = min(deadline, first + group_seconds)
        remaining = deadline - first
        if remaining <= 0:
            return 0
        interval = max(self.limits.account_min_interval, self.limits.target_min_interval)
        return min(limit, ceil(remaining / interval))

    def local_hold_reason(self, scope_key: QQScopeKey | None = None) -> str:
        """Runtime isolation only; this does not claim a durable Store commit."""
        if self._closing:
            return "qq_governor_closed"
        if not self._opened:
            return "qq_governor_not_open"
        if not self._identity_confirmed:
            return "qq_identity_unconfirmed"
        batch = self.test_batch_status()
        if batch is not None and batch.ended:
            return batch.reason
        hold = self._local_holds.get(None, self._local_holds.get(scope_key))
        return hold[1] if hold is not None else ""

    def resume_revision(self, scope_key: QQScopeKey | None) -> tuple[int, int, int]:
        """Bind an explicit resume to the holds observed before its online probe."""
        account = self._local_holds.get(None)
        target = self._local_holds.get(scope_key) if scope_key is not None else None
        return self._epoch, account[0] if account else 0, target[0] if target else 0

    def assert_resume_revision(
        self, scope_key: QQScopeKey | None, expected: tuple[int, int, int],
    ) -> None:
        if self.resume_revision(scope_key) != expected:
            raise OperationError("qq_governance_changed")

    def begin_test_batch(
        self, batch_id: str, scope_key: QQScopeKey, write_limit: Literal[6, 12], deadline: float,
    ) -> None:
        if not self._opened or self._closing:
            raise OperationError("qq_governor_not_open")
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", batch_id) is None:
            raise OperationError("qq_test_batch_id_invalid")
        if scope_key[0] != self.account_id or write_limit not in {6, 12}:
            raise OperationError("qq_test_batch_invalid")
        if not self.held() or self._active is not None or self.pending_count:
            raise OperationError("qq_test_batch_requires_idle_hold")
        now = self._mono()
        if not now < deadline <= now + 1200.0:
            raise OperationError("qq_test_batch_deadline_invalid")
        if self._test_batch is not None and self._test_batch.batch_id == batch_id:
            raise OperationError("qq_test_batch_reused")
        self._test_batch = QQTestBatchStatus(batch_id, scope_key, write_limit, deadline, 0, False, "")
        self.invalidate("qq_test_batch_started")

    def test_batch_status(self) -> QQTestBatchStatus | None:
        batch = self._test_batch
        if batch is not None and not batch.ended and self._mono() >= batch.deadline:
            return QQTestBatchStatus(
                batch.batch_id, batch.scope_key, batch.write_limit, batch.deadline,
                batch.committed_cost, True, "qq_test_batch_expired",
            )
        return batch

    def assert_test_batch_resume(self, test_batch_id: str | None) -> None:
        batch = self.test_batch_status()
        if batch is None:
            if test_batch_id is not None:
                raise OperationError("qq_test_batch_mismatch")
            return
        if test_batch_id != batch.batch_id:
            raise OperationError("qq_test_batch_mismatch")
        if batch.ended:
            raise OperationError(batch.reason)

    def end_test_batch(self, reason: str = "qq_test_batch_ended") -> None:
        batch = self.test_batch_status()
        if batch is None:
            raise OperationError("qq_test_batch_missing")
        self._test_batch = QQTestBatchStatus(
            batch.batch_id, batch.scope_key, batch.write_limit, batch.deadline,
            batch.committed_cost, True, batch.reason if batch.ended else reason,
        )
        self.hold_local(self._test_batch.reason)

    def test_batch_final_attempt(self, admission: QQAdmission) -> bool:
        active = self._active
        if active is None or active.admission is not admission:
            return False  # A late terminal callback cannot affect a newer batch/slot.
        batch = self.test_batch_status()
        return batch is not None and active.batch_id == batch.batch_id and (
            batch.ended or batch.committed_cost + admission.spec.cost >= batch.write_limit
        )

    def _check_test_batch(self, spec: QQWriteSpec) -> None:
        batch = self.test_batch_status()
        if batch is None:
            return
        if batch.ended:
            self.end_test_batch(batch.reason)
            raise OperationError(batch.reason)
        if spec.scope_key != batch.scope_key:
            raise OperationError("qq_test_batch_scope_mismatch")
        if batch.committed_cost + spec.cost > batch.write_limit:
            self.end_test_batch("qq_test_batch_exhausted")
            raise OperationError("qq_test_batch_exhausted")

    def held(self, scope_key: QQScopeKey | None = None) -> bool:
        """Fail-closed runtime view for early rejection and truthful status UI."""
        if self.local_hold_reason(scope_key):
            return True
        if self.snapshot.account.held:
            return True
        return scope_key is not None and any(
            target.scope_key == scope_key and target.held for target in self.snapshot.targets
        )

    async def open(self, *, defer_ownership: bool = False) -> None:
        if self._opened or self._closing:
            raise RuntimeError("QQ delivery lifecycle already started")
        if self._ownership is not None:
            if self._ownership.account_id != self.account_id:
                raise ValueError("account ownership does not match governor")
        if not defer_ownership:
            self._acquire_ownership()
        try:
            await self.store.qq_ensure_account(self.account_id, now=self._utc())
            snapshot = await self.store.qq_delivery_snapshot(self.account_id, now=self._utc())
            self._account_ready = self._mono() + self.limits.account_min_interval
            self._apply_snapshot(snapshot)
            if snapshot.unsettled_action_ids or snapshot.unreviewed_unknown_action_ids:
                self.hold_local("qq_unreviewed_dispatch")
            self._opened = True
        except BaseException:
            if self._ownership is not None and self._ownership_acquired:
                self._ownership.close()
                self._ownership_acquired = False
            self._identity_confirmed = False
            raise

    def confirm_ownership(self, account_id: str) -> None:
        """Call only after the transport has verified its actual account identity."""
        if account_id != self.account_id:
            raise OperationError("qq_account_mismatch")
        if not self._opened or self._closing:
            raise OperationError("qq_governor_not_open")
        if not self._identity_confirmed:
            self._acquire_ownership()
            self._notify()

    def _acquire_ownership(self) -> None:
        if self._ownership is not None:
            self._ownership.acquire()
            self._ownership_acquired = True
        self._identity_confirmed = True

    async def refresh(
        self, scope_key: QQScopeKey | None = None, *, resume: bool = False,
        resume_revision: tuple[int, int, int] | None = None,
    ) -> QQDeliverySnapshot:
        """Project a Store commit. Only explicit successful resume clears local hold."""
        generation = self._budget_generation
        epoch = self._epoch
        expected_resume = self.resume_revision(scope_key) if resume_revision is None else resume_revision
        try:
            snapshot = await self.store.qq_delivery_snapshot(
                self.account_id, scope_key, now=self._utc(),
            )
        except Exception:
            self.hold_local("qq_storage_failed")
            raise
        if epoch != self._epoch or generation != self._budget_generation:
            if resume:
                raise OperationError("qq_governance_changed")
            return snapshot  # Caller will reread the changed projection before admission.
        if resume:
            self.assert_resume_revision(scope_key, expected_resume)
            row = snapshot.account if scope_key is None else snapshot.target
            if row is None or row.held or snapshot.clock_inconsistent:
                raise OperationError("qq_resume_not_committed")
            self._local_holds.pop(scope_key, None)
        self._apply_snapshot(snapshot)
        if scope_key is not None:
            self._target_attempts[scope_key] = snapshot.quota.target_attempts
            self._target_generation[scope_key] = generation
        self._notify()
        self._prune_targets()
        return snapshot

    async def wait(
        self,
        spec: QQWriteSpec,
        binding: QQAdmissionBinding,
        deadline: float,
        check: Callable[[], None],
        *,
        not_before: float = 0.0,
    ) -> QQAdmission:
        self._assert_account(spec)
        self._check_test_batch(spec)
        self._assert_available(spec.scope_key)
        check()
        deadline = min(deadline, self._mono() + self.limits.admission_wait_seconds)
        if self._test_batch is not None:
            deadline = min(deadline, self._test_batch.deadline)
        if deadline <= self._mono():
            raise OperationError("qq_admission_expired")
        queue = self._queues.get(spec.scope_key)
        if self.pending_count >= self.limits.account_queue_limit or (
            queue is not None and len(queue) >= self.limits.target_queue_limit
        ):
            raise OperationError("qq_capacity_exhausted")
        waiter = _Waiter(spec, binding, deadline, check, not_before)
        self._queues.setdefault(spec.scope_key, deque()).append(waiter)
        self._notify()
        try:
            while True:
                waiter.wake.clear()
                await self._refresh_pending()
                self._pump()
                if waiter.failure is not None:
                    raise waiter.failure
                if waiter.admission is not None:
                    return waiter.admission
                timeout = self._wait_timeout(waiter)
                try:
                    await asyncio.wait_for(waiter.wake.wait(), timeout)
                except TimeoutError:
                    pass  # One exact interval/expiry timer, never a polling interval.
        except BaseException:
            if waiter.admission is not None:
                self.release(waiter.admission, False)
            raise
        finally:
            self._remove(waiter)
            self._prune_targets()
            self._notify()

    def assert_current(self, admission: QQAdmission) -> None:
        self._check_test_batch(admission.spec)
        active = self._active
        if active is None or active.admission is not admission:
            raise OperationError("qq_admission_invalid")
        if active.invalid_reason:
            raise OperationError(active.invalid_reason)
        if active.epoch != self._epoch:
            raise OperationError("qq_admission_invalid")
        self._assert_available(admission.spec.scope_key)
        if self._mono() >= admission.deadline:
            raise OperationError("qq_admission_expired")
        if admission.governor_revision != self.snapshot.account.revision:
            raise OperationError("qq_governance_changed")
        if active.target_revision != self._targets[admission.spec.scope_key].revision:
            raise OperationError("qq_governance_changed")
        active.check()

    def prepare_commit(self, admission: QQAdmission) -> tuple[int, int]:
        self.assert_current(admission)
        active = self._require_active(admission)
        # Store may await its own lock. Preserve the slot even if the ticket
        # expires during that await, until Actions rolls back or settles it.
        active.preparing = True
        return admission.governor_revision, active.target_revision

    def grant(
        self, admission: QQAdmission, generation: int,
        *, on_wire_start: Callable[[], None] | None = None,
    ) -> QQWriteGrant:
        self.assert_current(admission)
        active = self._require_active(admission)
        if active.committed:
            raise OperationError("qq_write_grant_reused")
        if generation != admission.binding.connection_generation:
            raise OperationError("qq_connection_changed")
        active.committed = True

        def consume(spec: QQWriteSpec, connection_generation: int) -> None:
            self.assert_current(admission)
            current = self._require_active(admission)
            if current.started:
                raise OperationError("qq_write_grant_reused")
            if spec != admission.spec:
                raise OperationError("qq_write_spec_changed")
            if connection_generation != generation:
                raise OperationError("qq_connection_changed")
            if on_wire_start is not None:
                on_wire_start()
            current.started = True

        return QQWriteGrant(admission.spec, consume)

    def wire_started(self, admission: QQAdmission) -> bool:
        active = self._active
        return active is not None and active.admission is admission and active.started

    def release(self, admission: QQAdmission, committed: bool) -> None:
        active = self._active
        if active is None or active.admission is not admission:
            return
        if committed:
            now = self._mono()
            self._account_ready = max(self._account_ready, now + self.limits.account_min_interval)
            scope = admission.spec.scope_key
            self._target_ready[scope] = max(
                self._target_ready.get(scope, 0.0), now + self.limits.target_min_interval,
            )
            self._budget_generation += 1
            batch = self._test_batch
            if batch is not None and active.batch_id == batch.batch_id:
                self._test_batch = QQTestBatchStatus(
                    batch.batch_id, batch.scope_key, batch.write_limit, batch.deadline,
                    batch.committed_cost + admission.spec.cost, batch.ended, batch.reason,
                )
                if self._test_batch.committed_cost >= self._test_batch.write_limit:
                    self.end_test_batch("qq_test_batch_exhausted")
        self._active = None
        self._prune_targets()
        self._notify()

    def hold_local(self, reason: str, scope_key: QQScopeKey | None = None) -> None:
        self._hold_sequence += 1
        self._local_holds[scope_key] = (self._hold_sequence, reason)
        self._invalidate(reason, scope_key)

    def invalidate(self, reason: str) -> None:
        """Policy/connection invalidation is synchronous and performs no I/O."""
        self._epoch += 1
        self._invalidate(reason, None)

    async def close(self) -> None:
        self._closing = True
        self.invalidate("qq_governor_closed")
        # Actions owns and drains actual writes before closing this owner.
        if self._active is not None and (self._active.preparing or self._active.committed):
            raise OperationError("qq_write_cleanup_pending")
        self._active = None
        self._opened = False
        if self._ownership is not None and self._ownership_acquired:
            self._ownership.close()
            self._ownership_acquired = False
        self._identity_confirmed = False

    def _require_active(self, admission: QQAdmission) -> _Active:
        active = self._active
        if active is None or active.admission is not admission:
            raise OperationError("qq_admission_invalid")
        return active

    def _assert_account(self, spec: QQWriteSpec) -> None:
        if spec.account_id != self.account_id or spec.scope_key[0] != self.account_id:
            raise OperationError("qq_account_mismatch")

    def _assert_available(self, scope_key: QQScopeKey) -> None:
        if not self._opened or self._closing:
            raise OperationError("qq_governor_closed" if self._closing else "qq_governor_not_open")
        if not self._identity_confirmed:
            raise OperationError("qq_identity_unconfirmed")
        if None in self._local_holds or self.snapshot.account.held:
            raise OperationError("qq_account_held")
        target = self._targets.get(scope_key)
        if scope_key in self._local_holds or (target is not None and target.held):
            raise OperationError("qq_target_held")

    def _apply_snapshot(self, snapshot: QQDeliverySnapshot) -> None:
        previous = self._snapshot
        if previous is not None and previous.account.revision != snapshot.account.revision:
            self.invalidate("qq_governance_changed")
        self._snapshot = snapshot
        now_mono, now_utc = self._mono(), self._utc()
        if snapshot.account.last_settled_at is not None:
            self._account_ready = max(
                self._account_ready,
                now_mono + max(
                    0.0, snapshot.account.last_settled_at + self.limits.account_min_interval - now_utc,
                ),
            )
        relevant_targets = set(self._queues)
        if self._active is not None:
            relevant_targets.add(self._active.admission.spec.scope_key)
        if snapshot.target is not None and snapshot.target.scope_key is not None:
            relevant_targets.add(snapshot.target.scope_key)
        for target in snapshot.targets:
            scope = target.scope_key
            if scope is None:
                raise RuntimeError("target projection contains account state")
            if scope not in relevant_targets:
                continue
            old = self._targets.get(scope)
            if old is not None and old.revision != target.revision:
                self._invalidate("qq_governance_changed", scope)
            self._targets[scope] = target
            if target.last_settled_at is not None:
                self._target_ready[scope] = max(
                    self._target_ready.get(scope, 0.0),
                    now_mono + max(0.0, target.last_settled_at + self.limits.target_min_interval - now_utc),
                )
            if target.held:
                self._invalidate("qq_target_held", scope)
        if snapshot.account.held:
            self._invalidate("qq_account_held", None)
        if snapshot.clock_inconsistent:
            self.hold_local("clock_inconsistent")

    async def _refresh_pending(self) -> None:
        generation = self._budget_generation
        scopes = tuple(self._queues)
        for scope in scopes:
            if self._target_generation.get(scope) != generation:
                await self.refresh(scope)

    def _invalidate(self, reason: str, scope: QQScopeKey | None) -> None:
        for key, queue in tuple(self._queues.items()):
            if scope is None or scope == key:
                for waiter in queue:
                    waiter.failure = OperationError(reason)
                    waiter.wake.set()
                del self._queues[key]
        active = self._active
        if active is not None and (scope is None or active.admission.spec.scope_key == scope):
            active.invalid_reason = reason
        self._notify()

    def _remove(self, waiter: _Waiter) -> None:
        scope = waiter.spec.scope_key
        queue = self._queues.get(scope)
        if queue is not None and waiter in queue:
            queue.remove(waiter)
            if not queue:
                del self._queues[scope]

    def _notify(self) -> None:
        for queue in self._queues.values():
            for waiter in queue:
                waiter.wake.set()

    def _prune_targets(self) -> None:
        retained = set(self._queues)
        if self._active is not None:
            retained.add(self._active.admission.spec.scope_key)
        for scope in tuple(self._targets):
            if scope not in retained:
                del self._targets[scope]
                self._target_attempts.pop(scope, None)
                self._target_generation.pop(scope, None)
        now = self._mono()
        for scope, ready in tuple(self._target_ready.items()):
            if scope not in retained and ready <= now:
                del self._target_ready[scope]

    def _quota_ready(
        self, attempts: tuple[QQQuotaAttempt, ...], cost: int, window: float, limit: int,
    ) -> float:
        now_utc = self._utc()
        relevant = tuple(attempt for attempt in attempts if attempt.budget_anchor > now_utc - window)
        excess = sum(attempt.cost for attempt in relevant) + cost - limit
        if excess <= 0:
            return 0.0
        for attempt in relevant:
            excess -= attempt.cost
            if excess <= 0:
                delay = attempt.budget_anchor + window - now_utc
                return 0.0 if delay <= 0.0 else self._mono() + delay
        raise RuntimeError("write cost exceeds the configured quota")

    def _ready_at(self, waiter: _Waiter) -> tuple[float, float]:
        spec, limits = waiter.spec, self.limits
        account = self.snapshot.quota.account_attempts
        target = self._target_attempts[spec.scope_key]
        quota_ready = max(
            self._quota_ready(account, spec.cost, 3600.0, limits.account_hour_limit),
            self._quota_ready(account, spec.cost, 86400.0, limits.account_day_limit),
            self._quota_ready(target, spec.cost, 3600.0, limits.target_hour_limit),
            self._quota_ready(target, spec.cost, 86400.0, limits.target_day_limit),
        )
        return max(
            waiter.not_before, self._account_ready,
            self._target_ready.get(spec.scope_key, 0.0), quota_ready,
        ), quota_ready

    def _pump(self) -> None:
        now = self._mono()
        active = self._active
        if active is not None and not active.preparing and not active.committed:
            if now >= active.admission.deadline or active.invalid_reason:
                self._active = None
        for queue in tuple(self._queues.values()):
            for waiter in tuple(queue):
                try:
                    if now >= waiter.deadline:
                        raise OperationError("qq_admission_expired")
                    self._assert_available(waiter.spec.scope_key)
                    self._check_test_batch(waiter.spec)
                    waiter.check()
                except Exception as exc:
                    waiter.failure = exc
                    self._remove(waiter)
                    waiter.wake.set()
        if self._active is not None or not self._queues:
            return
        keys = list(self._queues)
        if any(self._target_generation.get(key) != self._budget_generation for key in keys):
            return  # A new settlement requires all queued target projections to be current.
        if self._last_target in keys:
            pivot = keys.index(self._last_target) + 1
            keys = keys[pivot:] + keys[:pivot]
        for scope in keys:
            waiter = self._queues[scope][0]
            ready, quota_ready = self._ready_at(waiter)
            if quota_ready >= waiter.deadline:
                waiter.failure = OperationError("qq_capacity_exhausted")
                self._remove(waiter)
                waiter.wake.set()
                continue
            if ready > now:
                continue
            admission = QQAdmission(
                uuid.uuid4().hex, waiter.spec, waiter.binding,
                self.snapshot.account.revision, waiter.deadline,
            )
            self._active = _Active(
                admission, waiter.check, self._epoch, self._targets[scope].revision,
                self._test_batch.batch_id if self._test_batch is not None else "",
            )
            self._last_target = scope
            waiter.admission = admission
            self._remove(waiter)
            waiter.wake.set()
            self._notify()
            return

    def _wait_timeout(self, waiter: _Waiter) -> float:
        now = self._mono()
        next_at = waiter.deadline
        active = self._active
        if active is not None:
            if not active.preparing and not active.committed and not active.invalid_reason:
                next_at = min(next_at, active.admission.deadline)
        elif self._target_generation.get(waiter.spec.scope_key) == self._budget_generation:
            ready, _ = self._ready_at(waiter)
            if ready > now:
                next_at = min(next_at, ready)
        return max(0.0, next_at - now)
