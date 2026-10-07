"""Explicit runtime orchestration for the opt-in daily schedule clock.

The runner has no ticker and owns no setting.  A caller supplies the already
assembled :class:`ScheduleLife`, policy, action boundary, model port, shared
model budget, persona projection, and exact group list.  One invocation may create at most one
schedule day per group; durable ``actions`` rows are the daily attempt budget.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import time as datetime_time
from zoneinfo import ZoneInfo

from .actions import Actions
from .config import Config
from .model_budget import ModelBudget
from .policy import Policy
from .schedule_life import (
    PersonaRolePoints,
    ScheduleDayRecord,
    ScheduleLife,
)
from .schedule_model import ScheduleModelPlanner
from .store import StoreConnection
from .types import ActionCall, ModelPort, ModelRequest, OperationError, Scope
from .worldbook import CanonRegistry

_SCHEDULE_SUBJECT = "system:schedule"
_SCHEDULE_ACTION = "model.schedule"
_DAILY_ATTEMPT_LIMIT = 2
_DUE_AT = datetime_time(hour=2)


@dataclass(frozen=True, slots=True)
class ScheduleRunFailure:
    scope: Scope
    code: str


@dataclass(frozen=True, slots=True)
class ScheduleRunReport:
    """One explicit run result; no field implies a background task."""

    local_day: str | None
    committed: tuple[ScheduleDayRecord, ...] = ()
    attempted: tuple[Scope, ...] = ()
    failed: tuple[ScheduleRunFailure, ...] = ()
    reused: tuple[Scope, ...] = ()


def _request_digest(request: ModelRequest) -> str:
    try:
        material = request.model_dump(mode="json")
        encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OperationError("invalid_schedule_model_request") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _error_code(error: BaseException) -> str:
    if isinstance(error, OperationError):
        return error.code
    if isinstance(error, TimeoutError):
        return "timeout"
    return "transport_error"


@dataclass(frozen=True, slots=True)
class _GroupOutcome:
    record: ScheduleDayRecord | None = None
    attempted: bool = False
    reused: bool = False
    failure: ScheduleRunFailure | None = None


class DailyScheduleRunner:
    """Run a due schedule day under the shared budget and model action gate."""

    def __init__(
        self,
        schedule: ScheduleLife,
        actions: Actions,
        policy: Policy,
        model: ModelPort,
        *,
        budget: ModelBudget,
        model_config: Config | None = None,
        model_name: str | None = None,
        persona: PersonaRolePoints,
        allowed_groups: Sequence[str],
        canon_registry: CanonRegistry | None = None,
        provider: str | None = None,
        timeout: float | None = None,
    ) -> None:
        if actions.policy is not policy or actions.store is not policy.store:
            raise OperationError("schedule_runtime_boundary_mismatch")
        if schedule.store is not actions.store:
            raise OperationError("schedule_runtime_store_mismatch")
        if type(persona) is not PersonaRolePoints:
            raise OperationError("invalid_schedule_persona")
        if model_config is not None and type(model_config) is not Config:
            raise OperationError("invalid_schedule_model_config")
        if model_config is not None:
            configured_name = model_config.model
            configured_provider = model_config.policy_provider
            configured_timeout = model_config.model_timeout
            if model_name is not None and model_name != configured_name:
                raise OperationError("schedule_model_config_conflict")
            if provider is not None and provider != configured_provider:
                raise OperationError("schedule_model_config_conflict")
            if timeout is not None and timeout != configured_timeout:
                raise OperationError("schedule_model_config_conflict")
            model_name = configured_name
            provider = configured_provider
            timeout = configured_timeout
        else:
            if model_name is None:
                raise OperationError("invalid_schedule_model_config")
            provider = "anthropic" if provider is None else provider
            timeout = 30.0 if timeout is None else timeout
        if type(model_name) is not str or not model_name.strip() or len(model_name) > 200:
            raise OperationError("invalid_schedule_model_config")
        if type(provider) is not str or not provider.strip() or len(provider) > 200:
            raise OperationError("invalid_schedule_model_config")
        if type(timeout) not in (int, float):
            raise OperationError("invalid_schedule_timeout")
        if not math.isfinite(float(timeout)) or timeout <= 0:
            raise OperationError("invalid_schedule_timeout")
        if isinstance(allowed_groups, (str, bytes)):
            raise OperationError("invalid_schedule_groups")
        normalized_groups: set[str] = set()
        for group_id in allowed_groups:
            try:
                scope = Scope(bot_id=policy.bot_id, group_id=group_id)
            except (TypeError, ValueError) as exc:
                raise OperationError("invalid_schedule_groups") from exc
            normalized_groups.add(scope.group_id)
        self.schedule = schedule
        self.actions = actions
        self.policy = policy
        self.model = model
        self.budget = budget
        self.model_name = model_name.strip()
        self.provider = provider.strip()
        self.persona = persona
        self.allowed_groups = frozenset(normalized_groups)
        self.canon_registry = canon_registry
        self.timeout = float(timeout)

    def _local_time(self, at: datetime) -> tuple[datetime, str]:
        if type(at) is not datetime or at.tzinfo is None or at.utcoffset() is None:
            raise OperationError("invalid_schedule_clock")
        try:
            local = at.astimezone(ZoneInfo(self.schedule.default_timezone))
        except Exception as exc:  # ZoneInfo is validated by ScheduleLife; fail closed if mutated.
            raise OperationError("invalid_schedule_timezone") from exc
        return local, local.date().isoformat()

    @staticmethod
    def _request_id(scope: Scope, local_day: str) -> str:
        return f"schedule:{scope.bot_id}:{scope.group_id}:{local_day}"

    async def _attempt_count(self, scope: Scope, local_day: str) -> int:
        request_id = self._request_id(scope, local_day)

        def read(db: StoreConnection) -> int:
            row = db.execute(
                "SELECT count(*) AS count FROM actions "
                "WHERE request_id=? AND bot_id=? AND group_id=? AND action=?",
                (request_id, scope.bot_id, scope.group_id, _SCHEDULE_ACTION),
            ).fetchone()
            if row is None or type(row["count"]) is not int:
                raise OperationError("invalid_schedule_attempts")
            return int(row["count"])

        return await self.actions.store.transaction(read)

    async def _record_postprocess_failure(self, action_key: str, local_day: str, code: str) -> None:
        details = json.dumps(
            {"action_key": action_key, "local_day": local_day, "stage": "postprocess"},
            separators=(",", ":"),
        )

        def record(db: StoreConnection) -> None:
            db.execute(
                "INSERT INTO audit(kind,identity,revision,code,details) "
                "VALUES ('schedule_failure',?,0,?,?)",
                (action_key, code, details),
            )

        await self.actions.store.transaction(record)

    async def _run_group(self, scope: Scope, local_day: str, at: datetime) -> _GroupOutcome:
        if scope.group_id not in self.schedule.allowed_groups:
            return _GroupOutcome(
                failure=ScheduleRunFailure(scope, "schedule_scope_denied")
            )

        existing = await self.schedule.read_day(scope, local_day)
        if existing is not None:
            return _GroupOutcome(record=existing, reused=True)

        # This is only an early no-work check.  The authoritative limit is the
        # transaction callback below, which runs before Actions inserts intent.
        count = await self._attempt_count(scope, local_day)
        if count >= _DAILY_ATTEMPT_LIMIT:
            return _GroupOutcome(
                failure=ScheduleRunFailure(scope, "schedule_daily_limit")
            )
        try:
            await self.policy.check(
                _SCHEDULE_SUBJECT,
                scope,
                _SCHEDULE_ACTION,
                provider=self.provider,
                model=self.model_name,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _GroupOutcome(
                failure=ScheduleRunFailure(scope, _error_code(exc))
            )

        attempt = count + 1
        request_id = self._request_id(scope, local_day)
        action_key = f"{request_id}:attempt:{attempt}"
        attempted = False
        model_completed = False

        async def invoke(request: ModelRequest):
            nonlocal attempted, model_completed
            async with self.budget.slot("reply"):
                attempted = True
                call = ActionCall(
                    key=action_key,
                    request_id=request_id,
                    subject=_SCHEDULE_SUBJECT,
                    scope=scope,
                    action=_SCHEDULE_ACTION,
                    payload_hash=_request_digest(request),
                    provider=self.provider,
                    model=self.model_name,
                )

                def preflight(db: StoreConnection) -> None:
                    row = db.execute(
                        "SELECT count(*) AS count FROM actions "
                        "WHERE request_id=? AND bot_id=? AND group_id=? AND action=?",
                        (request_id, scope.bot_id, scope.group_id, _SCHEDULE_ACTION),
                    ).fetchone()
                    if row is None or type(row["count"]) is not int:
                        raise OperationError("invalid_schedule_attempts")
                    if int(row["count"]) >= _DAILY_ATTEMPT_LIMIT:
                        raise OperationError("schedule_daily_limit")
                    duplicate = db.execute(
                        "SELECT 1 FROM actions WHERE id=?", (action_key,)
                    ).fetchone()
                    if duplicate is not None:
                        raise OperationError("schedule_attempt_conflict")

                reply = await self.actions.execute(
                    call,
                    lambda: self.model.request(request),
                    external=self.model.is_external,
                    preflight_transaction=preflight,
                    timeout=self.timeout,
                )
                model_completed = True
                return reply

        planner = ScheduleModelPlanner(
            invoke,
            model=self.model_name,
            canon_registry=self.canon_registry,
        )
        try:
            record = await self.schedule.advance(
                scope,
                persona=self.persona,
                planner=planner,
                at=at,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = _error_code(exc)
            if model_completed:
                await self._record_postprocess_failure(action_key, local_day, code)
            return _GroupOutcome(
                attempted=attempted,
                failure=ScheduleRunFailure(scope, code),
            )
        if record is None:
            if model_completed:
                await self._record_postprocess_failure(action_key, local_day, "schedule_not_committed")
            return _GroupOutcome(
                attempted=attempted,
                failure=ScheduleRunFailure(scope, "schedule_not_committed"),
            )
        return _GroupOutcome(record=record, attempted=attempted)

    async def run_due(self, at: datetime) -> ScheduleRunReport:
        """Attempt the configured local day after 02:00, without background work."""

        if not self.schedule.enabled or not self.allowed_groups:
            return ScheduleRunReport(local_day=None)
        local, local_day = self._local_time(at)
        if local.timetz().replace(tzinfo=None) < _DUE_AT:
            return ScheduleRunReport(local_day=None)

        committed: list[ScheduleDayRecord] = []
        attempted: list[Scope] = []
        failed: list[ScheduleRunFailure] = []
        reused: list[Scope] = []
        for group_id in sorted(self.allowed_groups):
            scope = Scope(bot_id=self.policy.bot_id, group_id=group_id)
            outcome = await self._run_group(scope, local_day, at)
            if outcome.record is not None:
                committed.append(outcome.record)
            if outcome.attempted:
                attempted.append(scope)
            if outcome.reused:
                reused.append(scope)
            if outcome.failure is not None:
                failed.append(outcome.failure)
        return ScheduleRunReport(
            local_day=local_day,
            committed=tuple(committed),
            attempted=tuple(attempted),
            failed=tuple(failed),
            reused=tuple(reused),
        )
