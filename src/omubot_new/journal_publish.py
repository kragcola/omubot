"""One durable Journal publication exit; offline fake and unvalidated QZone wire.

No credentials are read here. An external port remains blocked until an independently
validated profile and explicit target gate exist. Unknown delivery never resubmits.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, cast

from .actions import Actions
from .journal import (
    FICTION_PREFIX,
    JournalDraft,
    JournalOwner,
    journal_digest,
    journal_identity,
    journal_list_after,
    journal_list_cursor,
    validate_journal_hash,
)
from .journal_qzone import JournalPublishRejected
from .store import StoreConnection, StoreRow, drain_on_cancel
from .types import ActionCall, OperationError, Scope, SendReceipt

PublishMode = Literal["dry_run", "live"]
PublishState = Literal["dispatching", "unknown", "published", "failed"]


class JournalPublishPort(Protocol):
    is_external: bool

    @property
    def wire_validated(self) -> bool: ...

    destination: str

    @property
    def validated_account_id(self) -> str: ...

    async def publish(
        self, body: str, *, idempotency_key: str, before_post: Callable[[], Awaitable[None]]
    ) -> SendReceipt: ...


@dataclass(frozen=True, slots=True)
class JournalPublishGate:
    enabled: bool = False
    allow_live_publish: bool = False
    allowed_groups: tuple[str, ...] = ()
    allowed_live_uins: tuple[str, ...] = ()
    max_posts_per_day: int = 1
    source_ttl_s: float = 86400
    timeout_s: float = 30

    def __post_init__(self) -> None:
        if (
            type(self.enabled) is not bool
            or type(self.allow_live_publish) is not bool
            or type(self.allowed_groups) is not tuple
            or any(type(group) is not str or not group for group in self.allowed_groups)
            or type(self.allowed_live_uins) is not tuple
            or any(
                type(uin) is not str or not uin.isdigit() or not uin.strip("0") or len(uin) > 20
                for uin in self.allowed_live_uins
            )
            or type(self.max_posts_per_day) is not int
            or not 1 <= self.max_posts_per_day <= 3
            or any(
                type(value) not in {int, float} or not math.isfinite(value) or value <= 0
                for value in (self.source_ttl_s, self.timeout_s)
            )
            or self.source_ttl_s > 86400
            or self.timeout_s > 300
        ):
            raise OperationError("journal_publish_gate_invalid")


@dataclass(frozen=True, slots=True)
class JournalDelivery:
    delivery_id: str
    draft_id: str
    mode: PublishMode
    state: PublishState
    payload_hash: str
    receipt: str
    code: str
    wire_validated: bool
    external_transport: bool


class UnvalidatedQZonePublisher:
    """Explicit running adapter status; no pretend CGI payload/profile/credentials."""

    is_external = True
    wire_validated = False
    destination = "journal-text"
    validated_account_id = ""

    async def publish(
        self, body: str, *, idempotency_key: str, before_post: Callable[[], Awaitable[None]]
    ) -> SendReceipt:
        raise OperationError("journal_wire_unvalidated")


class JournalPublisher:
    def __init__(
        self,
        owner: JournalOwner,
        actions: Actions,
        port: JournalPublishPort,
        *,
        gate: JournalPublishGate | None = None,
    ) -> None:
        if actions.store is not owner.store or actions.policy is not owner.policy:
            raise OperationError("journal_store_mismatch")
        if port.destination != owner.public_destination:
            raise OperationError("journal_destination_mismatch")
        self.owner, self.actions, self.port = owner, actions, port
        self.gate = JournalPublishGate() if gate is None else gate
        owner.live_gate = self.assert_live_ready

    def assert_live_ready(self, scope: Scope) -> None:
        if (
            not self.gate.enabled
            or not self.gate.allow_live_publish
            or scope.group_id not in self.gate.allowed_groups
            or self.port.is_external
            and (
                not self.port.wire_validated
                or self.port.validated_account_id not in self.gate.allowed_live_uins
            )
        ):
            raise OperationError("journal_live_gate_closed")

    def _row(self, row: StoreRow) -> JournalDelivery:
        return JournalDelivery(
            str(row["delivery_id"]),
            str(row["draft_id"]),
            cast(PublishMode, row["mode"]),
            cast(PublishState, row["state"]),
            str(row["payload_hash"]),
            str(row["receipt"]),
            str(row["code"]),
            self.port.wire_validated,
            self.port.is_external,
        )

    def _check(
        self,
        db: StoreConnection,
        *,
        actor: str,
        scope: Scope,
        draft_id: str,
        body_hash: str,
        source_hash: str,
        content_hash: str,
        mode: PublishMode,
    ) -> JournalDraft:
        self.owner.authorize_transaction(db, actor, scope)
        if not self.gate.enabled:
            raise OperationError("journal_disabled")
        if mode == "live":
            self.assert_live_ready(scope)
        draft = self.owner.draft_transaction(db, scope, draft_id)
        self.owner.assert_expected(draft, body_hash, source_hash)
        if (
            not draft.is_tip
            or draft.state != "approved"
            or draft.review is None
            or draft.review.decision != "approve"
            or draft.review.approval_scope != mode
            or (draft.review.body_hash, draft.review.source_hash, draft.review.content_hash)
            != (body_hash, source_hash, content_hash)
            or draft.content_hash != content_hash
        ):
            raise OperationError("journal_review_conflict")
        if draft.created_at + self.gate.source_ttl_s <= self.owner.clock():
            raise OperationError("journal_source_expired")
        if hashlib.sha256(draft.body.encode()).hexdigest() != body_hash:
            raise OperationError("journal_payload_changed")
        if draft.content_kind == "fiction":
            if not draft.body.startswith(FICTION_PREFIX):
                raise OperationError("journal_fiction_label_missing")
        elif draft.body != self.owner.public_source_transaction(db, scope, draft.source_event_id)[1]:
            raise OperationError("journal_factual_body_changed")
        return draft

    async def publish(
        self,
        *,
        actor: str,
        scope: Scope,
        draft_id: str,
        expected_body_hash: str,
        expected_source_hash: str,
        expected_content_hash: str,
        operation_id: str,
        mode: PublishMode = "dry_run",
    ) -> JournalDelivery:
        actor = self.owner.validate_caller(actor, scope)
        draft_id = journal_identity(draft_id, "invalid_journal_draft")
        operation_id = journal_identity(operation_id, "invalid_journal_operation")
        body_hash, source_hash, content_hash = map(
            validate_journal_hash, (expected_body_hash, expected_source_hash, expected_content_hash)
        )
        if mode not in {"dry_run", "live"}:
            raise OperationError("journal_approval_scope_invalid")
        delivery_id = "jpub_" + journal_digest([scope.key, operation_id])
        payload_hash = journal_digest(
            [scope.key, draft_id, body_hash, source_hash, content_hash, mode, self.port.destination]
        )
        key = "ja_" + payload_hash
        entered = False

        def claim(db: StoreConnection) -> tuple[JournalDraft, JournalDelivery | None]:
            draft = self._check(
                db,
                actor=actor,
                scope=scope,
                draft_id=draft_id,
                body_hash=body_hash,
                source_hash=source_hash,
                content_hash=content_hash,
                mode=mode,
            )
            prior = db.execute(
                "SELECT * FROM journal_deliveries WHERE delivery_id=? OR (root_id=? AND mode=?)",
                (delivery_id, draft.root_id, mode),
            ).fetchone()
            if prior is not None:
                if (prior["payload_hash"], prior["actor"], prior["draft_id"]) != (
                    payload_hash,
                    actor,
                    draft_id,
                ):
                    raise OperationError("journal_publication_conflict")
                return draft, self._row(prior)
            today = datetime.fromtimestamp(self.owner.clock(), UTC).date().isoformat()
            if (
                mode == "live"
                and db.execute(
                    "SELECT COUNT(*) FROM journal_deliveries WHERE bot_id=? AND mode='live' "
                    "AND state IN ('dispatching','unknown','published') AND date(created_at,'unixepoch')=?",
                    (scope.bot_id, today),
                ).fetchone()[0]
                >= self.gate.max_posts_per_day
            ):
                raise OperationError("journal_day_publish_budget")
            db.execute(
                "INSERT INTO journal_deliveries(delivery_id,draft_id,root_id,bot_id,group_id,actor,"
                "mode,payload_hash,state,action_key,created_at) VALUES (?,?,?,?,?,?,?,?,'dispatching',?,?)",
                (
                    delivery_id,
                    draft_id,
                    draft.root_id,
                    scope.bot_id,
                    scope.group_id,
                    actor,
                    mode,
                    payload_hash,
                    key,
                    self.owner.clock(),
                ),
            )
            return draft, None

        async with self.owner.policy.dispatch_boundary:
            draft, prior = await self.owner.store.transaction(claim)
        if prior is not None:
            # Same-root concurrent callers observe the one claim, never resubmit.
            # Root startup explicitly calls recover_interrupted before admitting traffic.
            return prior

        def check(db: StoreConnection) -> None:
            self._check(
                db,
                actor=actor,
                scope=scope,
                draft_id=draft_id,
                body_hash=body_hash,
                source_hash=source_hash,
                content_hash=content_hash,
                mode=mode,
            )
            row = db.execute(
                "SELECT payload_hash,state FROM journal_deliveries WHERE delivery_id=?", (delivery_id,)
            ).fetchone()
            if row is None or row["payload_hash"] != payload_hash or row["state"] != "dispatching":
                raise OperationError("journal_publication_conflict")

        async def before_post() -> None:
            nonlocal entered
            async with self.owner.policy.dispatch_boundary:
                await self.owner.store.transaction(check)
                entered = True

        async def submit() -> SendReceipt:
            # Revalidate under the same policy ordering lock at the actual port,
            # before this port can read credentials or initiate any request.
            async with self.owner.policy.dispatch_boundary:
                await self.owner.store.transaction(check)
                if mode == "dry_run":
                    return SendReceipt(message_id="dry_" + payload_hash)
                task = asyncio.create_task(
                    self.port.publish(draft.body, idempotency_key=delivery_id, before_post=before_post)
                )
            return await task

        try:
            receipt = await self.actions.execute(
                ActionCall(
                    key=key,
                    request_id=delivery_id,
                    subject=actor,
                    scope=scope,
                    action="tool.invoke:http.post",
                    payload_hash=payload_hash,
                    provider="qzone",
                    model=self.port.destination,
                ),
                submit,
                external=mode == "live" and self.port.is_external,
                preflight_transaction=check,
                timeout=self.gate.timeout_s,
            )
            result = await self._finish(delivery_id, "published", receipt=receipt.message_id)
        except BaseException as exc:
            state: PublishState = (
                "unknown"
                if entered and mode == "live" and not isinstance(exc, JournalPublishRejected)
                else "failed"
            )
            code = (
                exc.code
                if isinstance(exc, OperationError)
                else "cancelled"
                if isinstance(exc, asyncio.CancelledError)
                else "timeout"
                if isinstance(exc, TimeoutError)
                else "transport_error"
            )
            await drain_on_cancel(asyncio.create_task(self._finish(delivery_id, state, code=code)))
            raise
        return result

    async def _finish(
        self, delivery_id: str, state: PublishState, *, receipt: str = "", code: str = ""
    ) -> JournalDelivery:
        def finish(db: StoreConnection) -> JournalDelivery:
            db.execute(
                "UPDATE journal_deliveries SET state=?,receipt=?,code=?,finished_at=? "
                "WHERE delivery_id=? AND state='dispatching'",
                (state, receipt, code, self.owner.clock(), delivery_id),
            )
            row = db.execute(
                "SELECT * FROM journal_deliveries WHERE delivery_id=?", (delivery_id,)
            ).fetchone()
            assert row is not None
            db.execute(
                "INSERT INTO audit(kind,identity,revision,code) VALUES ('journal_delivery',?,0,?)",
                (delivery_id, state),
            )
            return self._row(row)

        return await self.owner.store.transaction(finish)

    async def resolve_unknown(
        self,
        *,
        actor: str,
        scope: Scope,
        delivery_id: str,
        outcome: Literal["published", "failed"],
        receipt: str = "",
    ) -> JournalDelivery:
        actor = self.owner.validate_caller(actor, scope)
        delivery_id = journal_identity(delivery_id, "invalid_journal_delivery")
        if outcome not in {"published", "failed"} or type(receipt) is not str or len(receipt) > 128:
            raise OperationError("journal_resolution_invalid")
        if outcome == "published" and not receipt:
            raise OperationError("journal_resolution_receipt_required")

        def resolve(db: StoreConnection) -> JournalDelivery:
            self.owner.authorize_transaction(db, actor, scope)
            row = db.execute(
                "SELECT * FROM journal_deliveries WHERE delivery_id=? AND bot_id=? AND group_id=?",
                (delivery_id, scope.bot_id, scope.group_id),
            ).fetchone()
            if row is None or row["state"] != "unknown":
                raise OperationError("journal_invalid_transition")
            db.execute(
                "UPDATE journal_deliveries SET state=?,receipt=?,code='manual_resolution',finished_at=? "
                "WHERE delivery_id=?",
                (outcome, receipt, self.owner.clock(), delivery_id),
            )
            db.execute(
                "INSERT INTO audit(kind,identity,revision,code) VALUES ('journal_resolve',?,0,?)",
                (delivery_id, actor + ":" + outcome),
            )
            current = db.execute(
                "SELECT * FROM journal_deliveries WHERE delivery_id=?", (delivery_id,)
            ).fetchone()
            assert current is not None
            return self._row(current)

        return await self.owner.store.transaction(resolve)

    async def recover_interrupted(self) -> int:
        """Root calls once during exclusive startup, before admitting any publish request."""

        def recover(db: StoreConnection) -> int:
            rows = db.execute(
                "SELECT delivery_id FROM journal_deliveries WHERE state='dispatching' AND bot_id=?",
                (self.owner.policy.bot_id,),
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE journal_deliveries SET state='unknown',code='startup_interrupted',finished_at=? "
                    "WHERE delivery_id=? AND state='dispatching'",
                    (self.owner.clock(), row["delivery_id"]),
                )
                db.execute(
                    "INSERT INTO audit(kind,identity,revision,code) "
                    "VALUES ('journal_delivery',?,0,'unknown')",
                    (row["delivery_id"],),
                )
            return len(rows)

        return await self.owner.store.transaction(recover)

    async def read_delivery(self, *, actor: str, scope: Scope, delivery_id: str) -> JournalDelivery:
        actor = self.owner.validate_caller(actor, scope)
        delivery_id = journal_identity(delivery_id, "invalid_journal_delivery")

        def read(db: StoreConnection) -> JournalDelivery:
            self.owner.authorize_transaction(db, actor, scope)
            row = db.execute(
                "SELECT * FROM journal_deliveries WHERE delivery_id=? AND bot_id=? AND group_id=?",
                (delivery_id, scope.bot_id, scope.group_id),
            ).fetchone()
            if row is None:
                raise OperationError("journal_delivery_not_found")
            return self._row(row)

        return await self.owner.store.transaction(read)

    async def list_deliveries(
        self, *, actor: str, scope: Scope, limit: int = 64, cursor: str | None = None,
    ) -> tuple[tuple[JournalDelivery, ...], bool, str | None]:
        actor = self.owner.validate_caller(actor, scope)
        if type(limit) is not int or not 1 <= limit <= 64:
            raise OperationError("invalid_journal_limit")
        after = journal_list_after(scope, "deliveries", cursor)

        def read(db: StoreConnection) -> tuple[tuple[JournalDelivery, ...], bool, str | None]:
            self.owner.authorize_transaction(db, actor, scope)
            if after is not None and db.execute(
                "SELECT 1 FROM journal_deliveries "
                "WHERE bot_id=? AND group_id=? AND created_at=? AND delivery_id=?",
                (scope.bot_id, scope.group_id, *after),
            ).fetchone() is None:
                raise OperationError("invalid_journal_cursor")
            rows = db.execute(
                "SELECT * FROM journal_deliveries WHERE bot_id=? AND group_id=? "
                + ("AND (created_at<? OR (created_at=? AND delivery_id>?)) " if after else "")
                + "ORDER BY created_at DESC,delivery_id LIMIT ?",
                (scope.bot_id, scope.group_id, *((after[0], after[0], after[1]) if after else ()), limit + 1),
            ).fetchall()
            return (tuple(self._row(row) for row in rows[:limit]), len(rows) > limit,
                    (journal_list_cursor(scope, "deliveries", float(rows[limit - 1]["created_at"]),
                                         str(rows[limit - 1]["delivery_id"])) if len(rows) > limit else None))

        return await self.owner.store.transaction(read)
