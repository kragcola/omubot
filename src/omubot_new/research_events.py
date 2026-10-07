"""Authorized Phase1 research capture and anonymous, fixed-source CPU replay.

This owner never sends, learns, or turns AI output into a human archive. Raw
text has its own encrypted spool; Store retains only keyed identity metadata
and references to existing successful Action receipts. Capture failures are
isolated from the chat caller. Replay failures remain explicit and fail closed.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import stat
import time
import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from .archive_spool import EncryptedTextSpool
from .policy import Policy
from .rich_messages import TextSegment
from .store import Store, StoreConnection, drain_on_cancel, request_digest
from .topic_replay import AuthorizedReplaySnapshot, ReplayEvent, ReplayParameters, project_snapshot
from .types import Event, OperationError, Scope, SendReceipt

ResearchSource = Literal["live", "history", "offline"]
ResearchOrigin = Literal["human", "main_llm", "scheduler"]
_RAW_TTL = 86400
_DEFAULT_PARAMETERS = ReplayParameters()
_COLUMNS = (
    "event_uid", "run_id", "bot_key", "group_key", "actor_key", "permission_subject_key",
    "event_time", "ingested_at", "expires_at", "source_expires_at", "source_revision",
    "policy_revision", "source_hash", "spool_id", "direction", "actor_type", "source",
    "origin", "event_time_origin", "message_key", "reply_key", "mention_keys", "content_type",
    "action_key", "action_digest",
)


class ResearchDeriveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    group_id: str = Field(min_length=1, max_length=64)
    version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    parameters: ReplayParameters = _DEFAULT_PARAMETERS


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def load_research_key(path: Path) -> bytes:
    """Load an existing private purpose key; never create or copy a key."""
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise OperationError("invalid_research_key")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or hasattr(os, "geteuid") and metadata.st_uid != os.geteuid()):
            raise OperationError("invalid_research_key")
        key = os.read(descriptor, 33)
        if len(key) != 32:
            raise OperationError("invalid_research_key")
        return key
    finally:
        os.close(descriptor)


class _RawEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    bot_id: str
    group_id: str
    actor_id: str
    permission_subject: str
    source_event_id: str
    source_digest: str
    event_time: float = Field(ge=0, allow_inf_nan=False)
    source_expires_at: float = Field(gt=0, allow_inf_nan=False)
    direction: Literal["inbound", "outbound"]
    actor_type: Literal["human", "ai"]
    source: ResearchSource
    origin: ResearchOrigin
    event_time_origin: Literal["platform", "sender_success"]
    message_id: str
    reply_to: str
    mention_targets: tuple[str, ...]
    text: str = Field(max_length=8192)
    content_type: str
    action_key: str = ""
    action_digest: str = ""

    @property
    def scope(self) -> Scope:
        return Scope(bot_id=self.bot_id, group_id=self.group_id)


class _WireSegment(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    type: str = Field(min_length=1, max_length=64)
    data: dict[str, JsonValue]


class _InboundEnvelope(BaseModel):
    """Research-only wire projection; it cannot make a business Event eligible."""

    model_config = ConfigDict(extra="allow", strict=True)
    post_type: Literal["message"]
    message_type: Literal["group"]
    self_id: str
    group_id: str
    user_id: str
    message_id: str
    time: int = Field(ge=946684800, le=4102444800)
    message: str | list[_WireSegment] = Field(max_length=8000)

    @field_validator("self_id", "group_id", "user_id", "message_id", mode="before")
    @classmethod
    def wire_id(cls, value: object) -> str:
        if type(value) not in {str, int}:
            raise ValueError("invalid wire identity")
        identity = str(value)
        if not identity or identity != identity.strip() or len(identity) > 64:
            raise ValueError("invalid wire identity")
        return identity

    @model_validator(mode="after")
    def no_privilege_claim(self) -> _InboundEnvelope:
        extra = self.model_extra or {}
        sender = extra.get("sender")
        if (any(key in extra for key in ("is_admin", "capabilities"))
                or isinstance(sender, dict) and any(key in sender for key in ("is_admin", "capabilities"))):
            raise ValueError("forged privilege")
        return self


def _wire_plain_parts(envelope: _InboundEnvelope) -> tuple[str, str, tuple[str, ...], str]:
    segments = envelope.message
    if isinstance(segments, str):
        # Only transport CQ codes are removed. Their payload/URLs never enter raw text.
        converted: list[_WireSegment] = []
        cursor = 0
        for match in re.finditer(r"\[CQ:([A-Za-z][A-Za-z0-9_]*)(?:,([^\[\]]*))?\]", segments):
            plain = segments[cursor:match.start()]
            if "[CQ:" in plain:
                raise ValueError("invalid research CQ envelope")
            if plain:
                converted.append(_WireSegment(type="text", data={"text": plain}))
            params: dict[str, JsonValue] = {}
            for pair in (match.group(2) or "").split(","):
                if pair:
                    key, value = pair.split("=", 1)
                    params[key] = value
            converted.append(_WireSegment(type=match.group(1), data=params))
            cursor = match.end()
        trailing = segments[cursor:]
        if "[CQ:" in trailing:
            raise ValueError("invalid research CQ envelope")
        if trailing:
            converted.append(_WireSegment(type="text", data={"text": trailing}))
        segments = converted
    if len(segments) > 128:
        raise ValueError("research message segment limit")
    text: list[str] = []
    reply = ""
    mentions: list[str] = []
    types: set[str] = set()
    escapes = {"&amp;": "&", "&#44;": ",", "&#91;": "[", "&#93;": "]"}
    for segment in segments:
        types.add(segment.type)
        if segment.type == "text":
            literal = segment.data.get("text")
            if not isinstance(literal, str):
                raise ValueError("invalid research text")
            # CQ-string text is escaped; native array Text data is already literal.
            if isinstance(envelope.message, str):
                literal = re.sub(r"&amp;|&#44;|&#91;|&#93;", lambda match: escapes[match.group()], literal)
            text.append(literal)
        elif segment.type == "reply":
            if reply:
                raise ValueError("duplicate research reply")
            reply = _InboundEnvelope.wire_id(segment.data.get("id"))
        elif segment.type == "at":
            target = _InboundEnvelope.wire_id(segment.data.get("qq"))
            if target not in mentions:
                mentions.append(target)
    content_type = next(iter(types)) if len(types) == 1 else "mixed" if types else "empty"
    return "".join(text), reply, tuple(mentions), content_type


@dataclass(slots=True)
class ResearchCounters:
    received: int = 0
    persisted: int = 0
    duplicate: int = 0
    dropped: int = 0
    error: int = 0


@dataclass(frozen=True, slots=True)
class _Pending:
    raw: _RawEvent
    ingested_at: float
    policy_revision: int


class ResearchEvents:
    def __init__(
        self, store: Store, policy: Policy, *, enabled: bool = False,
        groups: Collection[str] = (), spool: EncryptedTextSpool | None = None,
        key: bytes | None = None, clock: Callable[[], float] = time.time,
        queue_limit: int = 256,
    ) -> None:
        if not 1 <= queue_limit <= 2048:
            raise ValueError("research queue must be bounded")
        if enabled and (not groups or spool is None or key is None or len(key) != 32):
            raise OperationError("research_storage_unavailable")
        self.store, self.policy, self.enabled = store, policy, enabled
        self.groups, self.spool, self._key = frozenset(groups), spool, key
        self.clock, self.run_id = clock, uuid.uuid4().hex
        self.counters = ResearchCounters()
        self._queue: asyncio.Queue[_Pending | None] = asyncio.Queue(maxsize=queue_limit)
        self._pending_count = 0
        self._worker: asyncio.Task[None] | None = None
        self._closing: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def pending(self) -> int:
        return self._pending_count

    def _hash(self, kind: str, *parts: object) -> str:
        if self._key is None:
            raise OperationError("research_storage_unavailable")
        return hmac.new(self._key, _canonical([kind, *parts]).encode(), hashlib.sha256).hexdigest()

    def _allowed(self, event: Event) -> bool:
        return (self.enabled and not self._closed and event.scope.kind == "group"
                and event.scope.bot_id == self.policy.bot_id and event.scope.group_id in self.groups)

    def _capture_permission(self, db: StoreConnection, subject: str, scope: Scope) -> int:
        revision = self.policy.check_transaction(db, subject, scope, "message.read", "", "", False)
        self.policy.check_transaction(db, subject, scope, "research.capture", "", "", False)
        return revision

    def _action_proof(self, db: StoreConnection, raw: _RawEvent) -> str:
        row = db.execute(
            "SELECT a.*,r.digest AS source_digest FROM actions a "
            "JOIN request_sources rs ON rs.owner_request_id=a.request_id "
            "JOIN requests r ON r.id=rs.source_request_id "
            "WHERE a.id=? AND rs.source_request_id=?",
            (raw.action_key, raw.source_event_id),
        ).fetchone()
        if (row is None or row["source_digest"] != raw.source_digest
                or row["scope_kind"] != "group" or row["bot_id"] != raw.bot_id
                or row["group_id"] != raw.group_id or row["subject"] != raw.permission_subject
                or row["action"] != "message.reply" or row["state"] != "succeeded"
                or not raw.message_id or row["receipt"] != raw.message_id
                or raw.action_digest and row["digest"] != raw.action_digest):
            raise OperationError("research_receipt_unavailable")
        return str(row["digest"])

    async def observe_inbound(
        self, event: Event, *, source: ResearchSource,
        source_expires_at: float | None = None,
    ) -> bool:
        """Authenticated canonical ingress calls this before reply/presence filtering."""
        if not self._allowed(event) or event.user_id == self.policy.bot_id:
            return False
        self.counters.received += 1
        if event.event_time is None:
            self.counters.dropped += 1
            return False
        # Keep plain text only; typed nontext messages still produce empty raw text.
        text = "".join(segment.text for segment in event.rich_segments if isinstance(segment, TextSegment))
        if not event.rich_segments:
            text = event.text
        types = {type(segment).__name__.removesuffix("Segment").lower() for segment in event.rich_segments}
        content_type = "text" if not types else next(iter(types)) if len(types) == 1 else "mixed"
        return await self._enqueue_raw(
            event, text=text, event_time=float(event.event_time), source=source, origin="human",
            actor_id=event.user_id, message_id=event.message_id, reply_to=event.reply_to,
            mention_targets=event.mention_targets, content_type=content_type,
            source_expires_at=source_expires_at,
        )

    async def observe_wire_inbound(
        self, payload: object, *, source: ResearchSource, known_bot_ids: Collection[str] = (),
    ) -> bool:
        """Authenticated ingress before the business parser, including nontext/empty input."""
        if not self.enabled or self._closed or not isinstance(payload, dict):
            return False
        wire = cast(dict[str, object], payload)
        if (wire.get("post_type") != "message" or wire.get("message_type") != "group"
                or str(wire.get("self_id")) != self.policy.bot_id
                or str(wire.get("group_id")) not in self.groups
                or str(wire.get("user_id")) in {self.policy.bot_id, *known_bot_ids}):
            return False
        self.counters.received += 1
        try:
            envelope = _InboundEnvelope.model_validate(payload)
            scope = Scope(bot_id=envelope.self_id, group_id=envelope.group_id)
            text, reply, mentions, content_type = _wire_plain_parts(envelope)
            event_id = "onebot:" + hashlib.sha256(json.dumps(
                [scope.bot_id, scope.group_id, envelope.message_id], separators=(",", ":")
            ).encode()).hexdigest()
            raw = _RawEvent(
                bot_id=scope.bot_id, group_id=scope.group_id, actor_id=envelope.user_id,
                permission_subject=envelope.user_id, source_event_id=event_id,
                source_digest=self._hash("inbound", scope.bot_id, scope.group_id, envelope.user_id,
                    envelope.message_id, envelope.time, text, reply, mentions, content_type),
                event_time=float(envelope.time), source_expires_at=float(envelope.time + _RAW_TTL),
                direction="inbound", actor_type="human", source=source, origin="human",
                event_time_origin="platform", message_id=envelope.message_id, reply_to=reply,
                mention_targets=mentions, text=text, content_type=content_type,
            )
            return await self._enqueue(raw)
        except Exception:
            self.counters.error += 1
            return False

    async def observe_outbound(
        self, event: Event, text: str, receipt: SendReceipt, action_key: str, *,
        sent_at: float, origin: Literal["main_llm", "scheduler"], source: ResearchSource,
        reply_to: str = "", mention_targets: tuple[str, ...] = (),
        source_expires_at: float | None = None,
    ) -> bool:
        """Only the actual successful main-model/scheduler text send calls this."""
        if not self._allowed(event):
            return False
        self.counters.received += 1
        if not text or not action_key or not receipt.message_id:
            self.counters.dropped += 1
            return False
        return await self._enqueue_raw(
            event, text=text, event_time=sent_at, source=source, origin=origin,
            actor_id=self.policy.bot_id, message_id=receipt.message_id, reply_to=reply_to,
            mention_targets=mention_targets, content_type="text", action_key=action_key,
            source_expires_at=source_expires_at,
        )

    async def _enqueue_raw(
        self, event: Event, *, text: str, event_time: float, source: ResearchSource,
        origin: ResearchOrigin, actor_id: str, message_id: str, reply_to: str,
        mention_targets: tuple[str, ...], content_type: str,
        source_expires_at: float | None, action_key: str = "",
    ) -> bool:
        assert isinstance(event.scope, Scope)
        try:
            deadline = event_time + _RAW_TTL
            if source_expires_at is not None:
                deadline = min(deadline, source_expires_at)
            raw = _RawEvent(
                bot_id=event.scope.bot_id, group_id=event.scope.group_id, actor_id=actor_id,
                permission_subject=event.user_id, source_event_id=event.event_id,
                source_digest=request_digest(event), event_time=event_time,
                source_expires_at=deadline, direction="outbound" if action_key else "inbound",
                actor_type="ai" if action_key else "human", source=source, origin=origin,
                event_time_origin="sender_success" if action_key else "platform",
                message_id=message_id, reply_to=reply_to, mention_targets=mention_targets,
                text=text, content_type=content_type, action_key=action_key,
            )
            return await self._enqueue(raw)
        except Exception:
            self.counters.error += 1
            return False

    async def _enqueue(self, raw: _RawEvent) -> bool:
        try:
            now = self.clock()
            if raw.source_expires_at <= now:
                self.counters.dropped += 1
                return False
            async with self.policy.dispatch_boundary:
                def authorize(db: StoreConnection) -> tuple[int, str]:
                    revision = self._capture_permission(db, raw.permission_subject, raw.scope)
                    return revision, self._action_proof(db, raw) if raw.action_key else ""
                revision, digest = await self.store.transaction(authorize)
            raw = raw.model_copy(update={"action_digest": digest})
            if self._closed:
                return False
            self._queue.put_nowait(_Pending(raw, now, revision))
            self._pending_count += 1
            if self._worker is None:
                self._worker = asyncio.create_task(self._run(), name="research-events-writer")
            return True
        except asyncio.QueueFull:
            self.counters.dropped += 1
        except Exception:
            # The explicitly isolated research boundary reports counts, never raw data.
            self.counters.error += 1
        return False

    def _metadata(self, raw: _RawEvent) -> dict[str, Any]:
        uid = self._hash("event", raw.bot_id, raw.group_id, raw.source, raw.direction,
                         raw.message_id or raw.source_event_id, raw.event_time)
        return {
            "event_uid": uid, "bot_key": self._hash("bot", raw.bot_id),
            "group_key": self._hash("group", raw.bot_id, raw.group_id),
            "actor_key": self._hash("actor", raw.bot_id, raw.actor_id),
            "permission_subject_key": self._hash("actor", raw.bot_id, raw.permission_subject),
            "event_time": raw.event_time, "source_expires_at": raw.source_expires_at,
            "source_revision": 1, "source_hash": self._hash("source", raw.model_dump(mode="json")),
            "spool_id": "src_" + uid, "direction": raw.direction, "actor_type": raw.actor_type,
            "source": raw.source, "origin": raw.origin, "event_time_origin": raw.event_time_origin,
            "message_key": (self._hash("message", raw.bot_id, raw.group_id, raw.message_id)
                            if raw.message_id else ""),
            "reply_key": (self._hash("message", raw.bot_id, raw.group_id, raw.reply_to)
                          if raw.reply_to else ""),
            "mention_keys": _canonical([
                self._hash("actor", raw.bot_id, target) for target in raw.mention_targets
            ]),
            "content_type": raw.content_type, "action_key": raw.action_key,
            "action_digest": raw.action_digest,
        }

    async def _persist(self, pending: _Pending) -> None:
        raw, spool = pending.raw, self.spool
        assert spool is not None
        metadata = self._metadata(raw)
        async with self.policy.dispatch_boundary:
            def check(db: StoreConnection) -> bool:
                self._capture_permission(db, raw.permission_subject, raw.scope)
                if raw.source_expires_at <= self.clock():
                    raise OperationError("research_source_expired")
                if raw.direction == "outbound":
                    self._action_proof(db, raw)
                row = db.execute("SELECT source_hash FROM research_message_events WHERE event_uid=?",
                                 (metadata["event_uid"],)).fetchone()
                if row is not None and row["source_hash"] != metadata["source_hash"]:
                    raise OperationError("idempotency_conflict")
                return row is not None
            if await self.store.transaction(check):
                self.counters.duplicate += 1
                return
        # Disk IO cannot hold up the chat dispatch/revocation ordering boundary.
        expiry = await asyncio.to_thread(spool.put, metadata["spool_id"], raw.model_dump_json())
        metadata.update(run_id=self.run_id, ingested_at=pending.ingested_at, expires_at=expiry,
                        policy_revision=pending.policy_revision)
        try:
            async with self.policy.dispatch_boundary:
                def commit(db: StoreConnection) -> None:
                    check(db)
                    db.execute(
                        "INSERT INTO research_message_events (" + ",".join(_COLUMNS) + ") VALUES ("
                        + ",".join("?" for _ in _COLUMNS) + ")",
                        tuple(metadata[column] for column in _COLUMNS),
                    )
                await self.store.transaction(commit)
        except BaseException:
            await asyncio.to_thread(spool.delete, metadata["spool_id"])
            raise
        self.counters.persisted += 1

    async def _run(self) -> None:
        while True:
            pending = await self._queue.get()
            try:
                if pending is None:
                    return
                try:
                    await drain_on_cancel(asyncio.create_task(self._persist(pending)))
                except Exception:
                    self.counters.error += 1
            finally:
                if pending is not None:
                    self._pending_count -= 1
                self._queue.task_done()
                # An idle writer must not retain the last plaintext queue entry.
                pending = None

    async def flush(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        self._closed = True
        if self._worker is None:
            return
        async def drain() -> None:
            await self._queue.put(None)
            assert self._worker is not None
            await self._worker
        if self._closing is None:
            self._closing = asyncio.create_task(drain())
        await drain_on_cancel(self._closing)
        self._worker = None

    async def derive(
        self, *, actor: str, scope: Scope, version: str,
        parameters: ReplayParameters = _DEFAULT_PARAMETERS,
    ) -> dict[str, Any]:
        """Read current authorized raw once, recheck proofs, return anonymous metadata."""
        if not self.enabled or scope.group_id not in self.groups or scope.bot_id != self.policy.bot_id:
            raise OperationError("research_unavailable")
        spool = self.spool
        assert spool is not None
        async with self.policy.dispatch_boundary:
            _, grants = await self.policy.snapshot()
            subjects = {
                grant.subject for grant in grants if grant.scope == scope and grant.expires_at > self.clock()
            }
            def read(db: StoreConnection) -> tuple[list[tuple[dict[str, Any], str]], int]:
                self.policy.check_transaction(db, actor, scope, "research.read", "", "", False)
                rows = db.execute(
                    "SELECT * FROM research_message_events WHERE bot_key=? AND group_key=? "
                    "AND expires_at>? AND source_expires_at>? ORDER BY event_time,event_uid LIMIT 2048",
                    (self._hash("bot", scope.bot_id), self._hash("group", scope.bot_id, scope.group_id),
                     self.clock(), self.clock()),
                ).fetchall()
                result: list[tuple[dict[str, Any], str]] = []
                for row in rows:
                    matches = [subject for subject in subjects if
                               self._hash("actor", scope.bot_id, subject) == row["permission_subject_key"]]
                    if len(matches) != 1:
                        continue
                    try:
                        self._capture_permission(db, matches[0], scope)
                    except OperationError as exc:
                        if exc.code != "denied":
                            raise
                        continue
                    result.append((dict(row), matches[0]))
                total = int(db.execute(
                    "SELECT count(*) FROM research_message_events WHERE bot_key=? AND group_key=?",
                    (self._hash("bot", scope.bot_id), self._hash("group", scope.bot_id, scope.group_id)),
                ).fetchone()[0])
                return result, total
            proof, total = await self.store.transaction(read)
        events: list[ReplayEvent] = []
        sources: list[str] = []
        deadlines: list[float] = []
        kinds: set[ResearchSource] = set()
        raw_proofs: list[tuple[dict[str, Any], _RawEvent]] = []
        for row, subject in proof:
            async with self.policy.dispatch_boundary:
                def before_decrypt(
                    db: StoreConnection, row: dict[str, Any] = row, subject: str = subject,
                ) -> None:
                    self.policy.check_transaction(db, actor, scope, "research.read", "", "", False)
                    self._capture_permission(db, subject, scope)
                    current_row = db.execute("SELECT * FROM research_message_events WHERE event_uid=?",
                                             (row["event_uid"],)).fetchone()
                    if (current_row is None or dict(current_row) != row
                            or min(row["expires_at"], row["source_expires_at"]) <= self.clock()):
                        raise OperationError("research_source_changed")
                await self.store.transaction(before_decrypt)
                raw_text = await drain_on_cancel(asyncio.create_task(
                    asyncio.to_thread(spool.read, row["spool_id"])
                ))
            if raw_text is None:
                raise OperationError("research_source_unavailable")
            raw = _RawEvent.model_validate_json(raw_text)
            if raw.scope != scope or raw.permission_subject != subject or any(
                row[key] != value for key, value in self._metadata(raw).items()
            ):
                raise OperationError("research_source_changed")
            actual_expiry = await asyncio.to_thread(spool.expiry, row["spool_id"])
            if actual_expiry != row["expires_at"]:
                raise OperationError("research_source_changed")
            events.append(ReplayEvent(
                event_id=row["event_uid"], bot_id=row["bot_key"], group_id=row["group_key"],
                actor_id=row["actor_key"], event_time=raw.event_time, direction=raw.direction,
                actor_type=raw.actor_type, message_id=row["message_key"], reply_to=row["reply_key"],
                mention_targets=tuple(json.loads(row["mention_keys"])), text=raw.text,
            ))
            sources.append(row["source_hash"])
            deadlines.append(min(row["expires_at"], row["source_expires_at"]))
            kinds.add(raw.source)
            raw_proofs.append((row, raw))
        source_kind = next(iter(kinds)) if len(kinds) == 1 else "mixed" if kinds else "empty"
        snapshot = AuthorizedReplaySnapshot(
            source_kind=source_kind, expires_at=min(deadlines, default=self.clock() + 1),
            events=tuple(events), source_hashes=tuple(sources),
        )
        async with self.policy.dispatch_boundary:
            def current(db: StoreConnection) -> None:
                self.policy.check_transaction(db, actor, scope, "research.read", "", "", False)
                for row, raw in raw_proofs:
                    self._capture_permission(db, raw.permission_subject, scope)
                    current_row = db.execute("SELECT * FROM research_message_events WHERE event_uid=?",
                                             (row["event_uid"],)).fetchone()
                    if (current_row is None or dict(current_row) != row
                            or min(row["expires_at"], row["source_expires_at"]) <= self.clock()):
                        raise OperationError("research_source_changed")
                    if raw.direction == "outbound":
                        self._action_proof(db, raw)
            await self.store.transaction(current)
            result = project_snapshot(snapshot, version=version, parameters=parameters, now=self.clock())
            result["unavailable_count"] = total - len(events)
            await self.store.transaction(current)
        return result
