"""One authorized recent OneBot page, with a local finite-snapshot checkpoint.

The provider's message IDs are never treated as durable platform cursors. Only
the fixed local metadata snapshot has a high-water mark; upstream completeness
is always unknown. Text remains transient and is not archived or learned here.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast

from pydantic import Field, JsonValue

from .actions import Actions
from .adapters import parse_event
from .archive import (
    ArchiveBackfillItem,
    ArchiveBackfillPage,
    ArchiveBackfillRunner,
    ArchiveService,
    ArchiveSourceInput,
    BackfillCommit,
    BackfillRequest,
)
from .store import StoreConnection
from .types import ActionCall, Event, OperationError, Scope, StrictModel

HISTORY_ACTION = "history.read"
HISTORY_MODEL = "onebot.history"
HISTORY_VERSION = "onebot-recent-snapshot-v1"
OneBotRequest = Callable[[str, dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]]


class RecentHistoryRequest(StrictModel):
    group_id: str = Field(pattern=r"^[1-9][0-9]{0,31}$")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    count: int = Field(default=20, ge=1, le=256)


class RecentHistoryView(StrictModel):
    snapshot_id: str
    scope: Scope
    received_count: int
    excluded_count: int
    restored_count: int
    checkpoint_status: str
    cursor_marker: str
    upstream_complete: None = None


@dataclass(frozen=True)
class RecentHistorySnapshot:
    snapshot_id: str
    scope: Scope
    received_count: int
    excluded_count: int
    events: tuple[Event, ...] = field(repr=False)
    checkpoint: BackfillCommit
    upstream_complete: None = None


class _FixedMetadataPage:
    def __init__(self, scope: Scope, items: tuple[ArchiveBackfillItem, ...], end: str) -> None:
        self.scope, self.items, self.end = scope, items, end

    async def fetch_page(
        self, *, scope: Scope, after_marker: str, through_marker: str, limit: int,
    ) -> ArchiveBackfillPage:
        if scope != self.scope or through_marker != self.end:
            raise OperationError("history_snapshot_changed")
        remaining = tuple(item for item in self.items if item.marker > after_marker)
        return ArchiveBackfillPage(remaining[:limit], high_water_confirmed=not remaining)


class RecentHistoryOwner:
    """Reuse the existing transport, Actions and Archive owners; no new client."""

    def __init__(
        self, actions: Actions, archive: ArchiveService, *, request: OneBotRequest,
        transport_identity: str, allowed_groups: tuple[str, ...],
        bot_ids: frozenset[str] = frozenset(), external: bool = True,
        transport_ready: Callable[[], None] | None = None,
    ) -> None:
        if actions.store is not archive.store or actions.policy is not archive.policy:
            raise OperationError("history_owner_mismatch")
        self.actions, self.archive = actions, archive
        self.request, self.transport_identity = request, transport_identity
        self.allowed_groups, self.bot_ids = frozenset(allowed_groups), bot_ids
        self.external, self.transport_ready = external, transport_ready

    async def read(self, *, actor: str, request: RecentHistoryRequest) -> RecentHistorySnapshot:
        scope = Scope(bot_id=self.actions.policy.bot_id, group_id=request.group_id)
        if request.group_id not in self.allowed_groups:
            raise OperationError("history_scope_denied")
        material = json.dumps([scope.key, request.operation_id, request.count], separators=(",", ":"))
        digest = hashlib.sha256(material.encode()).hexdigest()
        snapshot_id = "hs_" + digest
        call = ActionCall(
            key=snapshot_id, request_id=snapshot_id, subject=actor, scope=scope,
            action=HISTORY_ACTION, payload_hash=digest,
            provider=self.transport_identity, model=HISTORY_MODEL, includes_history=True,
        )

        def authorize(db: StoreConnection) -> None:
            self.actions.policy.check_transaction(db, actor, scope, "memory.archive", "", "", False)

        async def operation() -> tuple[int, tuple[Event, ...]]:
            # Recheck at the actual port after the durable intent. No retry or
            # raw response enters the action audit or a source row.
            await self.actions.store.transaction(lambda db: (
                self.actions.policy.check_transaction(
                    db, actor, scope, HISTORY_ACTION, self.transport_identity, HISTORY_MODEL, True,
                ), authorize(db),
            ))
            if self.transport_ready is not None:
                self.transport_ready()
            envelope = await self.request("get_group_msg_history", {
                "group_id": scope.group_id, "count": request.count,
            })
            return self._decode(envelope, scope, request.count)

        received_count, events = await self.actions.execute(
            call, operation, external=self.external, timeout=5,
            preflight_transaction=authorize, before_operation=self.transport_ready,
        )
        def select(db: StoreConnection) -> tuple[Event, ...]:
            authorize(db)
            self.actions.policy.check_transaction(
                db, actor, scope, HISTORY_ACTION, self.transport_identity, HISTORY_MODEL, True,
            )
            selected: list[Event] = []
            for event in events:
                try:
                    for action in ("message.read", "memory.archive"):
                        self.actions.policy.check_transaction(db, event.user_id, scope, action, "", "", False)
                except OperationError as exc:
                    if exc.code != "denied":
                        raise
                    continue
                selected.append(event)
            return tuple(sorted(selected, key=lambda event: (cast(float, event.event_time), event.event_id)))

        selected = await self.actions.store.transaction(select)
        if len({event.event_id for event in selected}) != len(selected):
            raise OperationError("history_duplicate_message")
        items = tuple(ArchiveBackfillItem(f"{index:06d}", ArchiveSourceInput(
            scope=scope, event_id=event.event_id, observed_at=cast(float, event.event_time),
            source_kind="human_message", speaker_kind="human", speaker_id=event.user_id,
            platform_message_id=event.message_id,
        )) for index, event in enumerate(selected, 1))
        end = f"{max(1, len(items)):06d}"
        checkpoint_request = BackfillRequest(
            subject=actor, scope=scope, run_id=snapshot_id, scanner="recent_" + digest[:32],
            scanner_version=HISTORY_VERSION, params_hash=digest, from_marker="", to_marker=end,
        )
        checkpoint = await ArchiveBackfillRunner(
            self.archive, _FixedMetadataPage(scope, items, end), page_size=256, max_pages=2,
        ).run(checkpoint_request)
        # Permission changes during persistence must also prevent return of the
        # transient text; the Archive runner itself rechecks metadata writes.
        final = await self.actions.store.transaction(select)
        if final != selected:
            raise OperationError("history_snapshot_changed")
        return RecentHistorySnapshot(snapshot_id, scope, received_count, received_count - len(selected),
                                     selected, checkpoint)

    def _decode(
        self, envelope: dict[str, JsonValue], scope: Scope, count: int,
    ) -> tuple[int, tuple[Event, ...]]:
        if (envelope.get("status") != "ok" or type(envelope.get("retcode")) is not int
                or envelope["retcode"] != 0):
            raise OperationError("history_protocol_error")
        data = envelope.get("data")
        rows = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(rows, list) or len(rows) > count:
            raise OperationError("history_protocol_error")
        events: list[Event] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("message_type") != "group":
                raise OperationError("history_protocol_error")
            if str(row.get("group_id")) != scope.group_id:
                raise OperationError("history_wrong_group")
            if "self_id" in row and str(row["self_id"]) != scope.bot_id:
                raise OperationError("wrong_bot")
            sender = row.get("sender")
            author = sender.get("user_id") if isinstance(sender, dict) else None
            if author is None or ("user_id" in row and str(row["user_id"]) != str(author)):
                raise OperationError("history_wrong_author")
            if str(author) == scope.bot_id or str(author) in self.bot_ids:
                continue
            event = parse_event({**row, "post_type": "message", "self_id": scope.bot_id,
                                 "user_id": author}, expected_bot_id=scope.bot_id)
            if event.event_time is None or not 0 <= event.event_time <= time.time() + 300:
                raise OperationError("history_protocol_error")
            # Fixed source identity across local reads, never an ordering cursor.
            # Original time and author distinguish reused short platform IDs.
            event = event.model_copy(update={"event_id": "history_" + hashlib.sha256(
                json.dumps([self.transport_identity, event.event_id, event.event_time, event.user_id],
                           separators=(",", ":")).encode()).hexdigest()})
            events.append(event)
        return len(rows), tuple(events)
