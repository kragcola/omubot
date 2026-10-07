"""Explicit finite QQ test composition, shipped in the same native wheel.

The CLI requires a dedicated instance, an exact plan and --live. Validate-only
parses those files without creating a client, database, lock or authorization.
No model, raw write client, retry, login or automatic hold recovery exists here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import Field, ValidationError, model_validator

from .actions import Actions
from .adapters import OneBotSender
from .config import Config
from .instances import load_instance
from .policy import Policy
from .qq_delivery import QQDelivery
from .qq_ownership import QQAccountOwnership
from .store import Store
from .types import (
    ActionCall,
    Grant,
    OperationError,
    QQAdmissionBinding,
    QQDeliveryLimits,
    QQSenderPort,
    QQWriteGrant,
    Scope,
    SendReceipt,
    StrictModel,
)
from .web_contracts import QQBotBatchProof

SECOND_ACCOUNT_LIMITS = QQDeliveryLimits(
    account_min_interval=20.0, account_hour_limit=15, account_day_limit=45,
    target_min_interval=20.0, target_hour_limit=15, target_day_limit=45,
)


def second_account_limits(existing: QQDeliveryLimits) -> QQDeliveryLimits:
    """Apply the test ceiling while retaining any stricter instance limits."""
    return QQDeliveryLimits(
        account_min_interval=max(20.0, existing.account_min_interval),
        account_hour_limit=min(15, existing.account_hour_limit),
        account_day_limit=min(45, existing.account_day_limit),
        target_min_interval=max(20.0, existing.target_min_interval),
        target_hour_limit=min(15, existing.target_hour_limit),
        target_day_limit=min(45, existing.target_day_limit),
        admission_wait_seconds=existing.admission_wait_seconds,
        account_queue_limit=existing.account_queue_limit,
        target_queue_limit=existing.target_queue_limit,
    )


class QQTestCase(StrictModel):
    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    text: str = Field(min_length=1, max_length=2000, repr=False)


class QQTestResume(StrictModel):
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=160)
    reviewed_unknown_action_ids: tuple[str, ...] = Field(default=(), max_length=64)


class QQTestPlan(StrictModel):
    mode: Literal["qq_test_runner"]
    instance_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    account_id: str = Field(min_length=1, max_length=64)
    bot_account_id: str = Field(min_length=1, max_length=64)
    bot_batch_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    bot_status_origin: str = Field(min_length=1, max_length=256)
    bot_status_token_env: str = Field(pattern=r"^[A-Z_][A-Z0-9_]{0,127}$")
    batch_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    scope: Scope
    subject: str = Field(pattern=r"^qq-test:[A-Za-z0-9_-]{1,56}$", max_length=64)
    authorized_by: str = Field(min_length=1, max_length=64)
    authorization_expires_at: float = Field(gt=0, allow_inf_nan=False)
    policy_expected_revision: int = Field(ge=0)
    duration_seconds: float = Field(gt=0, le=1200, allow_inf_nan=False)
    resume: QQTestResume
    cases: tuple[QQTestCase, ...] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def exact_identity_and_cases(self) -> QQTestPlan:
        if self.instance_id == "standalone" or self.account_id != self.scope.bot_id:
            raise ValueError("test plan requires its own exact instance/account scope")
        if self.account_id == self.bot_account_id:
            raise ValueError("the second account must differ from the Bot account")
        if any(value != value.strip() or not value for value in (
            self.account_id, self.bot_account_id, self.authorized_by,
        )):
            raise ValueError("test identities must be exact")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("test case IDs must be unique within the batch")
        parsed = urlsplit(self.bot_status_origin)
        if (self.bot_status_origin != self.bot_status_origin.strip()
                or parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
                or parsed.port is None or not 1 <= parsed.port <= 65535
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("Bot status requires a literal loopback HTTP origin and explicit port")
        return self


class QQTestSender(QQSenderPort, Protocol):
    @property
    def bot_id(self) -> str: ...

    @property
    def ready(self) -> bool: ...

    async def probe_online(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class QQTestReceipt:
    case_id: str
    action_key: str
    message_id: str


class QQBotBatchGuard:
    """One authenticated read; cached checks make no I/O under the Policy lock."""

    def __init__(
        self, client: httpx.AsyncClient, plan: QQTestPlan, token: str,
        *, monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._plan = plan
        self._token = token
        self._mono = monotonic
        self._valid_until = 0.0

    async def verify(self) -> bool:
        self._valid_until = 0.0
        observed_at = self._mono()
        try:
            response = await self._client.get(
                self._plan.bot_status_origin.rstrip("/") + "/api/qq-test-batch",
                headers={"Authorization": "Bearer " + self._token}, follow_redirects=False,
            )
            response.raise_for_status()
            if len(response.content) > 65536:
                raise OperationError("qq_bot_batch_proof_invalid")
            proof = QQBotBatchProof.model_validate_json(response.content)
        except (httpx.HTTPError, ValidationError) as exc:
            raise OperationError("qq_bot_batch_probe_failed") from exc
        batch = proof.test_batch
        if (proof.account_id != self._plan.bot_account_id or not proof.available or proof.held
                or not proof.connection_ready or batch is None or batch.ended
                or batch.batch_id != self._plan.bot_batch_id or batch.write_limit != 12
                or batch.scope_key != (self._plan.bot_account_id, self._plan.scope.kind,
                                       self._plan.scope.group_id)
                or batch.committed_cost >= 12 or batch.expires_in_seconds <= 0.0):
            return False
        self._valid_until = observed_at + batch.expires_in_seconds
        return self._mono() < self._valid_until

    def check(self) -> None:
        if self._mono() >= self._valid_until:
            raise OperationError("qq_bot_batch_proof_expired")


def validate_instance_plan(instance: Path, config: Config, plan: QQTestPlan) -> None:
    if config.instance_id == "standalone" or config.instance_id != plan.instance_id:
        raise OperationError("qq_test_instance_mismatch")
    if Path(config.db_path).resolve() != instance.resolve() / "state.sqlite3":
        raise OperationError("qq_test_database_mismatch")
    if config.bot_id != plan.account_id:
        raise OperationError("qq_test_account_mismatch")


async def execute_test_plan(
    plan: QQTestPlan,
    actions: Actions,
    sender: QQTestSender,
    *,
    send_timeout: float,
    verify_bot_batch: Callable[[], Awaitable[bool]],
    check_bot_batch: Callable[[], None],
    monotonic: Callable[[], float] = time.monotonic,
    utc_clock: Callable[[], float] = time.time,
) -> tuple[QQTestReceipt, ...]:
    """Execute only explicit cases through the existing Actions commit boundary."""
    owner = actions.qq_delivery
    if owner is None:
        raise OperationError("qq_test_delivery_profile_mismatch")
    limits = owner.limits
    if (limits.account_min_interval < 20.0 or limits.target_min_interval < 20.0
            or limits.account_hour_limit > 15 or limits.target_hour_limit > 15
            or limits.account_day_limit > 45 or limits.target_day_limit > 45):
        raise OperationError("qq_test_delivery_profile_mismatch")
    if owner.account_id != plan.account_id or sender.bot_id != plan.account_id or not sender.ready:
        raise OperationError("qq_test_identity_unverified")
    started_at = utc_clock()
    authorization_deadline = min(plan.authorization_expires_at, started_at + plan.duration_seconds)
    duration = authorization_deadline - started_at
    if duration <= 0:
        raise OperationError("qq_test_authorization_expired")
    deadline = monotonic() + duration

    def check_authorization() -> None:
        if utc_clock() >= authorization_deadline or monotonic() >= deadline:
            raise OperationError("qq_test_authorization_expired")

    def check_boundary() -> None:
        check_authorization()
        check_bot_batch()

    async def verify_bot() -> None:
        check_authorization()
        try:
            async with asyncio.timeout(min(send_timeout, deadline - monotonic())):
                if not await verify_bot_batch():
                    raise OperationError("qq_bot_batch_unavailable")
        except TimeoutError as exc:
            raise OperationError("qq_bot_batch_probe_timeout") from exc
        check_boundary()

    await verify_bot()

    revision, existing = await actions.policy.snapshot()
    if revision != plan.policy_expected_revision:
        raise OperationError("revision_conflict")
    # The local explicit plan grants only this batch subject's exact target.
    # Existing instance permissions are retained; the new grant has a hard expiry.
    await actions.policy.replace(
        [*existing, Grant(
            subject=plan.subject, scope=plan.scope, actions=["message.read", "message.reply"],
            expires_at=authorization_deadline,
        )], plan.policy_expected_revision, plan.authorized_by,
    )
    receipts: list[QQTestReceipt] = []
    begun = False
    reason = "qq_test_batch_completed"
    try:
        check_authorization()
        await actions.begin_qq_test_batch(
            batch_id=plan.batch_id, scope_key=plan.scope.key, role="runner",
            duration_seconds=max(0.0, deadline - monotonic()), actor=plan.authorized_by,
        )
        begun = True
        await actions.resume_qq(
            scope_key=None, expected_revision=plan.resume.expected_revision,
            reason=plan.resume.reason, actor=plan.authorized_by,
            reviewed_unknown_action_ids=plan.resume.reviewed_unknown_action_ids,
            verify_online=sender.probe_online, test_batch_id=plan.batch_id,
        )
        for index, case in enumerate(plan.cases):
            await verify_bot()
            write = sender.describe_send(plan.scope, case.text)
            key = f"qq-test:{plan.batch_id}:{case.case_id}"
            call = ActionCall(
                key, f"qq-test:{plan.batch_id}", plan.subject, plan.scope,
                "message.reply", write.params_hash,
            )
            admission = await actions.await_qq_admission(
                write, QQAdmissionBinding(key, index, plan.batch_id, sender.connection_generation),
                deadline=deadline - send_timeout, check=check_boundary,
            )
            try:
                await verify_bot()
                # A ticket does not extend the batch's original transmission budget.
                if monotonic() + send_timeout > deadline:
                    raise OperationError("qq_test_authorization_expired")

                async def send_case(grant: QQWriteGrant, *, text: str = case.text) -> SendReceipt:
                    return await sender.send(plan.scope, text, grant=grant)

                receipt = await actions.execute(
                    call, send_case,
                    external=True, qq_admission=admission, timeout=send_timeout,
                    before_intent=check_boundary, before_operation=check_boundary,
                )
                receipts.append(QQTestReceipt(case.case_id, key, receipt.message_id))
            finally:
                owner.release(admission, False)  # Actions already releases any committed slot.
        return tuple(receipts)
    except BaseException as exc:
        reason = exc.code if isinstance(exc, OperationError) else "qq_test_batch_interrupted"
        raise
    finally:
        if begun:
            await actions.end_qq_test_batch(
                batch_id=plan.batch_id, reason=reason, actor=plan.authorized_by,
            )


async def run_native(instance: Path, config: Config, plan: QQTestPlan) -> tuple[QQTestReceipt, ...]:
    validate_instance_plan(instance, config, plan)
    if config.mode != "live":
        raise OperationError("qq_test_live_required")
    token = os.environ.get(config.onebot_token_env)
    status_token = os.environ.get(plan.bot_status_token_env)
    if not token:
        raise OperationError("onebot_token_missing")
    if not status_token:
        raise OperationError("qq_bot_status_token_missing")
    async with httpx.AsyncClient(
        timeout=config.send_timeout, trust_env=False, follow_redirects=False,
    ) as client:
        bot_guard = QQBotBatchGuard(client, plan, status_token)
        sender = OneBotSender(client, config.onebot_endpoint, token, config.bot_id)
        await sender.verify_identity()
        if not await sender.probe_online():
            raise OperationError("qq_offline")
        store = Store(Path(config.db_path))
        await store.open()
        policy = Policy(store, plan.account_id, "live")
        actions = Actions(store, policy)
        try:
            ownership = QQAccountOwnership(
                sender.bot_id, config.instance_id, await store.qq_database_id(), Path(config.db_path),
            )
            delivery = QQDelivery(
                store, sender.bot_id, second_account_limits(config.qq_delivery_limits), ownership=ownership,
            )
            actions.bind_qq_delivery(delivery, lambda: sender.connection_generation)
            await delivery.open()
            return await execute_test_plan(
                plan, actions, sender, send_timeout=config.send_timeout,
                verify_bot_batch=bot_guard.verify, check_bot_batch=bot_guard.check,
            )
        finally:
            try:
                await actions.close()
            finally:
                await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        plan = QQTestPlan.model_validate_json(args.plan.read_bytes())
        config = load_instance(args.instance, live=args.live)
        validate_instance_plan(args.instance, config, plan)
        if args.validate_only:
            print(json.dumps({"validated": True, "batch_id": plan.batch_id, "cases": len(plan.cases),
                              "account_id": plan.account_id, "scope": plan.scope.model_dump()}))
            return 0
        receipts = asyncio.run(run_native(args.instance, config, plan))
        print(json.dumps({"batch_id": plan.batch_id, "receipts": [asdict(item) for item in receipts]}))
        return 0
    except (OperationError, ValidationError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, OperationError) else "qq_test_plan_invalid"
        print(json.dumps({"error": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
