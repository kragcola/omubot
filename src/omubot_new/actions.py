"""The only controlled model/reply exit, with durable intent before dispatch."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from time import monotonic, time
from typing import Literal, TypeVar, cast

from .domain_learning import SlangChatProjection, StyleChatProjection
from .knowledge import KnowledgeChunkPointer
from .policy import (
    CHARACTER_TOOL_ACTIONS,
    ONEBOT_HISTORY_ACTIONS,
    ONEBOT_INTERACTION_ACTIONS,
    Policy,
    TransportProof,
)
from .pricing import FrozenModelPrice, ModelPrice, ModelTask, freeze_model_price
from .qq_delivery import QQDelivery
from .rich_messages import ImageSegment
from .runtime import Turn
from .store import Store, action_digest, contact_input_digest, drain_on_cancel, request_digest
from .types import (
    ActionCall,
    Event,
    ModelReply,
    OperationError,
    QQAdmission,
    QQAdmissionBinding,
    QQDeliverySnapshot,
    QQScopeKey,
    QQTransportError,
    QQTransportEvidence,
    QQWriteGrant,
    QQWriteSpec,
    Scope,
    SendReceipt,
    VisibilityReceipt,
    VisualOwner,
)

T = TypeVar("T")
_STICKER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_MODEL_ACTIONS = frozenset({"model.invoke", "model.schedule", "model.dream"})
_SEND_ACTIONS = frozenset({"message.reply", "message.sticker"})
_EXTERNAL_TOOL_ACTIONS = frozenset({
    "tool.invoke:web.search", "tool.invoke:web.fetch", "tool.invoke:http.get", "tool.invoke:http.post",
}) | CHARACTER_TOOL_ACTIONS | ONEBOT_INTERACTION_ACTIONS
_ALLOWED_ACTIONS = _MODEL_ACTIONS | _SEND_ACTIONS | _EXTERNAL_TOOL_ACTIONS | ONEBOT_HISTORY_ACTIONS


@dataclass(frozen=True, slots=True)
class SharedUploadAuthority:
    """Exact source proof and its authors, separate from target message history."""

    receipt: VisibilityReceipt
    object_identity: KnowledgeChunkPointer | SlangChatProjection | StyleChatProjection
    provider: str
    model: str

    @property
    def subjects(self) -> tuple[str, ...]:
        identity = self.object_identity
        if isinstance(identity, KnowledgeChunkPointer):
            return (identity.uploader_id,)
        return tuple(dict.fromkeys(entry.subject_id for entry in identity.items))


class Actions:
    def __init__(self, store: Store, policy: Policy, *, prices: Collection[ModelPrice] = ()) -> None:
        self.store = store
        self.policy = policy
        self._closed = False
        self._prices = tuple(prices)
        self.qq_delivery: QQDelivery | None = None
        self._qq_generation: Callable[[], int] | None = None
        self._qq_transports: dict[asyncio.Task[object], QQAdmission] = {}
        self._contact_preflight: Callable[[sqlite3.Connection, ActionCall], None] | None = None

    def bind_contact_preflight(self, preflight: Callable[[sqlite3.Connection, ActionCall], None]) -> None:
        """Conversation is the mandatory runtime/source owner for Bot-origin actions."""
        if self._contact_preflight is not None and self._contact_preflight != preflight:
            raise RuntimeError("Actions already has a contact preflight owner")
        self._contact_preflight = preflight

    def unbind_contact_preflight(self, preflight: Callable[[sqlite3.Connection, ActionCall], None]) -> None:
        if self._contact_preflight == preflight:
            self._contact_preflight = None

    def _assert_contact_preflight_transaction(self, db: sqlite3.Connection, call: ActionCall, *,
        stage: Literal["intent", "operation"], now: float, qq_spec: QQWriteSpec | None = None,
    ) -> None:
        row = db.execute("SELECT origin_kind FROM requests WHERE id=?", (call.request_id,)).fetchone()
        if row is None or row[0] != "bot_contact":
            if call.contact is not None or call.contact_addressee is not None:
                raise OperationError("invalid_contact_origin")
            return
        record = self.store.assert_contact_request_transaction(db, call, stage=stage, now=now)
        assert call.contact is not None
        if qq_spec is not None and call.action == "message.reply":
            expected_mentions = (() if call.contact_addressee is None
                else (call.contact_addressee.user_id,))
            if qq_spec.api != "send_group_msg" or qq_spec.mention_targets != expected_mentions:
                raise OperationError("contact_wire_target_mismatch")
        phase = call.contact.phase
        tools = _EXTERNAL_TOOL_ACTIONS | ONEBOT_HISTORY_ACTIONS
        if (phase == "reply" and call.action != "model.invoke"
                or phase == "tool" and call.action not in tools
                or phase == "send" and call.action != "message.reply"
                or phase == "followup" and call.action not in {"model.invoke", "message.reply", *tools}):
            raise OperationError("invalid_contact_phase")
        self.policy.assert_contact_authority_transaction(db, authority=record.contact.authority)
        self.policy.check_transaction(db, call.subject, call.scope, call.action,
            call.provider, call.model, call.includes_history, call.includes_images)
        self.policy.check_transaction(db, call.subject, call.scope, "message.read",
            call.provider, call.model, False, False)
        if call.action == "message.reply" and call.contact_addressee is not None:
            self.policy.check_transaction(db, call.subject, call.scope, "message.mention",
                call.provider, call.model, False, False)
        preflight = self._contact_preflight
        if preflight is None:
            raise OperationError("contact_owner_unbound")
        preflight(db, call)

    def bind_qq_delivery(self, delivery: QQDelivery, generation: Callable[[], int]) -> None:
        if self.qq_delivery is not None:
            raise RuntimeError("Actions already has a QQ delivery owner")
        self.qq_delivery = delivery
        self._qq_generation = generation
        self.policy.bind_qq_invalidation(delivery.invalidate)

    async def await_qq_admission(
        self, spec: QQWriteSpec, binding: QQAdmissionBinding, *, deadline: float,
        check: Callable[[], None], not_before: float = 0.0,
    ) -> QQAdmission:
        owner = self.qq_delivery
        if owner is None or self._closed:
            raise OperationError("qq_delivery_unavailable")
        return await owner.wait(spec, binding, deadline, check, not_before=not_before)

    async def pause_qq(
        self, *, scope_key: QQScopeKey | None, expected_revision: int, reason: str, actor: str,
    ) -> QQDeliverySnapshot:
        owner = self.qq_delivery
        if owner is None:
            raise OperationError("qq_delivery_unavailable")
        async with self.policy.dispatch_boundary:
            owner.hold_local(reason, scope_key)
            await self.store.qq_hold(
                owner.account_id, scope_key=scope_key, expected_revision=expected_revision,
                reason=reason, actor=actor,
            )
            tasks = tuple(task for task, admission in self._qq_transports.items()
                          if scope_key is None or admission.spec.scope_key == scope_key)
            if not await self.policy.cancel_transports(tasks):
                raise OperationError("transport_cancel_failed")
            return await owner.refresh(scope_key)

    async def hold_qq_transport(self, reason: str) -> QQDeliverySnapshot:
        owner = self.qq_delivery
        if owner is None:
            raise OperationError("qq_delivery_unavailable")
        owner.hold_local(reason)
        async with self.policy.dispatch_boundary:
            await self.store.qq_hold_transport(owner.account_id, reason=reason)
            if not await self.policy.cancel_transports(tuple(self._qq_transports)):
                raise OperationError("transport_cancel_failed")
            return await owner.refresh()

    async def begin_qq_test_batch(
        self, *, batch_id: str, scope_key: QQScopeKey, role: Literal["runner", "bot"],
        duration_seconds: float, actor: str,
    ) -> QQDeliverySnapshot:
        owner = self.qq_delivery
        if owner is None:
            raise OperationError("qq_delivery_unavailable")
        if not 0 < duration_seconds <= 1200:
            raise OperationError("qq_test_batch_invalid")
        async with self.policy.dispatch_boundary:
            if self.policy.transport_fault or self._qq_transports:
                raise OperationError("transport_cancel_failed")
            limit: Literal[6, 12] = 6 if role == "runner" else 12
            owner.begin_test_batch(batch_id, scope_key, limit, monotonic() + duration_seconds)
            try:
                await self.store.qq_begin_test_batch(
                    owner.account_id, batch_id=batch_id, scope_key=scope_key,
                    write_limit=limit, expires_at=time() + duration_seconds, actor=actor,
                )
            except BaseException:
                owner.end_test_batch("qq_test_batch_begin_failed")
                raise
            return await owner.refresh()

    async def end_qq_test_batch(
        self, *, batch_id: str, reason: str, actor: str,
    ) -> QQDeliverySnapshot:
        owner = self.qq_delivery
        if owner is None:
            raise OperationError("qq_delivery_unavailable")
        batch = owner.test_batch_status()
        if batch is None or batch.batch_id != batch_id:
            raise OperationError("qq_test_batch_mismatch")
        owner.end_test_batch(reason)
        async with self.policy.dispatch_boundary:
            batch = owner.test_batch_status()
            if batch is None or batch.batch_id != batch_id:
                raise OperationError("qq_test_batch_mismatch")
            await self.store.qq_hold_transport(owner.account_id, reason=reason, actor=actor)
            if not await self.policy.cancel_transports(tuple(self._qq_transports)):
                raise OperationError("transport_cancel_failed")
            return await owner.refresh()

    async def resume_qq(
        self, *, scope_key: QQScopeKey | None, expected_revision: int, reason: str, actor: str,
        reviewed_unknown_action_ids: tuple[str, ...], verify_online: Callable[[], Awaitable[bool]],
        test_batch_id: str | None = None,
    ) -> QQDeliverySnapshot:
        owner = self.qq_delivery
        if owner is None:
            raise OperationError("qq_delivery_unavailable")
        owner.assert_test_batch_resume(test_batch_id)
        if self.policy.transport_fault or self._qq_transports:
            raise OperationError("transport_cancel_failed")
        assert self._qq_generation is not None
        generation = self._qq_generation()
        resume_revision = owner.resume_revision(scope_key)
        # Read-only identity/online checks do not block permission replacement.
        if not await verify_online():
            raise OperationError("qq_offline")
        async with self.policy.dispatch_boundary:
            if self.policy.transport_fault or self._qq_transports:
                raise OperationError("transport_cancel_failed")
            if self._qq_generation() != generation:
                raise OperationError("qq_connection_changed")
            owner.assert_resume_revision(scope_key, resume_revision)
            owner.assert_test_batch_resume(test_batch_id)
            await self.store.qq_resume(
                owner.account_id, scope_key=scope_key, expected_revision=expected_revision,
                reason=reason, actor=actor, reviewed_unknown_action_ids=reviewed_unknown_action_ids,
                test_batch_id=test_batch_id,
            )
            return await owner.refresh(scope_key, resume=True, resume_revision=resume_revision)

    def shared_receipt_for_reader_transaction(
        self, db: sqlite3.Connection, *, actor: str, receipt: VisibilityReceipt,
    ) -> VisibilityReceipt:
        """A later reader gets its own target permission without refreshing frozen revisions."""
        if actor == receipt.reader_id:
            self.policy.assert_visibility_receipt_transaction(
                db, actor=actor, target_scope=receipt.target_scope, receipt=receipt,
            )
            return receipt
        current = self.policy.visibility_receipt_transaction(
            db, actor=actor, grant_id=receipt.grant_id, source_scope=receipt.source_scope,
            target_scope=receipt.target_scope, material_type=receipt.material_type,
            object_refs=receipt.object_refs,
        )
        if current != receipt.model_copy(update={"reader_id": actor}):
            raise OperationError("stale_visibility")
        return current

    def assert_upload_permissions_transaction(
        self, db: sqlite3.Connection, call: ActionCall, *,
        document_upload_subjects: tuple[str, ...] = (),
        shared_upload_authorities: tuple[SharedUploadAuthority, ...] = (),
    ) -> None:
        """Recheck the frozen upload authority at intent and at the actual port.

        Immutable call/source shapes are established by the intent owner once.
        """
        for subject in document_upload_subjects:
            # The uploader authorizes this destination independently
            # of ordinary human-message history and personal memory.
            self.policy.check_transaction(
                db, subject, call.scope, "knowledge.import",
                call.provider, call.model, False, False,
            )
            self.policy.check_transaction(
                db, subject, call.scope, "model.invoke",
                call.provider, call.model, True, False,
            )
        for authority in shared_upload_authorities:
            receipt = self.shared_receipt_for_reader_transaction(
                db, actor=call.subject, receipt=authority.receipt,
            )
            source = receipt.source_scope
            action = "model.invoke" if call.action in _SEND_ACTIONS else call.action
            provider = authority.provider if call.action in _SEND_ACTIONS else call.provider
            model = authority.model if call.action in _SEND_ACTIONS else call.model
            for subject in authority.subjects:
                source_actions = (
                    ("knowledge.import",) if receipt.material_type == "knowledge"
                    else ("message.read", "memory.archive", "memory.learn")
                )
                for source_action in source_actions:
                    self.policy.check_transaction(
                        db, subject, source, source_action, provider, model, False, False,
                    )
                self.policy.check_transaction(
                    db, subject, source,
                    "model.invoke" if receipt.material_type == "knowledge" else action,
                    provider, model, True, False,
                )

    async def execute(
        self,
        call: ActionCall,
        operation: Callable[[], Awaitable[T]] | Callable[[QQWriteGrant], Awaitable[T]],
        *,
        external: bool,
        qq_admission: QQAdmission | None = None,
        turn: Turn | None = None,
        before_intent: Callable[[], None | Awaitable[None]] | None = None,
        before_operation: Callable[[], None] | None = None,
        preflight_transaction: Callable[[sqlite3.Connection], None] | None = None,
        committed_delivery_total: int | None = None,
        first_delta_ms: Callable[[float], int | None] | None = None,
        document_upload_subjects: tuple[str, ...] = (),
        shared_upload_authorities: tuple[SharedUploadAuthority, ...] = (),
        source_event: Event | None = None,
        image_owner: VisualOwner | None = None,
        model_task: ModelTask = "unknown",
        timeout: float,  # noqa: ASYNC109 - explicit adapter budget, implemented with asyncio.timeout
    ) -> T:
        qq_owner = self.qq_delivery
        qq_committed = False
        if qq_admission is not None:
            if qq_owner is None:
                raise OperationError("qq_delivery_unavailable")
            if (qq_admission.binding.action_key != call.key
                    or qq_admission.spec.scope_key != call.scope.key):
                qq_owner.release(qq_admission, False)
                raise OperationError("qq_admission_mismatch")
        try:
            async with self.policy.dispatch_boundary:
                qq_revisions: tuple[int, int] | None = None
                if qq_admission is not None:
                    assert qq_owner is not None
                    qq_revisions = qq_owner.prepare_commit(qq_admission)
                # A caller may use this narrow synchronous check to linearize an
                # application send gate with creation of the durable action intent.
                # It must never wait for model work while holding this boundary.
                if before_intent is not None:
                    ready = before_intent()
                    if ready is not None:
                        await ready
                if self._closed:
                    raise OperationError("closed")
                if turn is not None:
                    turn.check()
                if qq_admission is not None and turn is not None:
                    completion_deadline = (
                        turn.send_completion_deadline() if call.action in _SEND_ACTIONS else turn.deadline
                    )
                    if monotonic() + timeout > completion_deadline:
                        raise OperationError("deadline")
                frozen_price = (
                    freeze_model_price(
                        self._prices, provider=call.provider, model=call.model,
                        task="schedule" if call.action == "model.schedule" else
                        "dream" if call.action == "model.dream" else model_task,
                    ) if call.action in _MODEL_ACTIONS else None
                )
                scope_identity: list[str] = (
                    [call.scope.bot_id, call.scope.group_id] if call.scope.kind == "group"
                    else [call.scope.bot_id, "private", call.scope.private_user_id]
                )
                digest_values: list[str | bool] = [
                    call.request_id,
                    call.subject,
                    *scope_identity,
                    call.action,
                    call.payload_hash,
                    call.provider,
                    call.model,
                    call.includes_history,
                    *call.history_subjects,
                    call.reply_target_status,
                ]
                # Preserve the existing plain-text digest while making every image
                # intent distinguishable by both its explicit flag and sources.
                if call.includes_images or call.image_subjects:
                    digest_values.extend(["images", call.includes_images, *call.image_subjects])
                if call.action == "message.sticker" or call.sticker_id or call.catalog_revision:
                    digest_values.extend(["sticker", call.sticker_id, str(call.catalog_revision)])
                if document_upload_subjects:
                    digest_values.extend(["document_upload", *document_upload_subjects])
                for authority in shared_upload_authorities:
                    digest_values.extend([
                        "shared_upload", authority.receipt.model_dump_json(),
                        authority.object_identity.model_dump_json()
                        if isinstance(authority.object_identity, KnowledgeChunkPointer)
                        else repr(authority.object_identity),
                        *authority.subjects, authority.provider, authority.model,
                    ])
                if (call.action in CHARACTER_TOOL_ACTIONS
                        and type(source_event) is Event and type(image_owner) is VisualOwner):
                    image_identity = image_owner.model_dump_json(
                        exclude={"scope": {"kind"}} if image_owner.scope.kind == "group" else None,
                    )
                    digest_values.extend(["character_source", request_digest(source_event), image_identity])
                if call.contact is not None:
                    digest_values.extend(["bot_contact", contact_input_digest(call.contact.contact),
                        str(call.contact.revision), call.contact.phase])
                if call.contact_addressee is not None:
                    digest_values.extend(["contact_addressee", call.contact_addressee.model_dump_json()])
                digest = action_digest(digest_values)

                def intent(db: sqlite3.Connection) -> str:
                    existing = db.execute("SELECT digest FROM actions WHERE id=?", (call.key,)).fetchone()
                    if existing is not None:
                        return "duplicate" if existing["digest"] == digest else "idempotency_conflict"
                    db.execute("SAVEPOINT action_intent")
                    try:
                        revision = self.policy.check_transaction(
                            db,
                            call.subject,
                            call.scope,
                            call.action,
                            call.provider,
                            call.model,
                            call.includes_history,
                            call.includes_images,
                        )
                        if external and self.policy.mode != "live":
                            raise OperationError("offline")
                        if call.action not in _ALLOWED_ACTIONS:
                            raise OperationError("denied")
                        if call.action in CHARACTER_TOOL_ACTIONS:
                            if (type(source_event) is not Event or type(image_owner) is not VisualOwner
                                    or turn is None or image_owner.source_kind != "direct"
                                    or image_owner.scope != source_event.scope
                                    or image_owner.event_id != source_event.event_id
                                    or image_owner.message_id != source_event.message_id
                                    or image_owner.turn_id != turn.id
                                    or call.scope != source_event.scope
                                    or call.subject != source_event.user_id
                                    or call.request_id != source_event.event_id
                                    or not call.includes_images
                                    or call.image_subjects != (source_event.user_id,)
                                    or call.includes_history or call.history_subjects):
                                raise OperationError("invalid_character_source")
                            index = image_owner.segment_index
                            if index >= len(source_event.rich_segments) or not isinstance(
                                source_event.rich_segments[index], ImageSegment
                            ):
                                raise OperationError("invalid_character_source")
                            claimed = db.execute("SELECT digest FROM requests WHERE id=?",
                                                 (source_event.event_id,)).fetchone()
                            if claimed is None or claimed["digest"] != request_digest(source_event):
                                raise OperationError("character_event_changed")
                        if call.action in {"model.schedule", "model.dream"}:
                            if (
                                call.subject != "system:" + call.action.removeprefix("model.")
                                or call.includes_history
                                or call.history_subjects
                                or call.includes_images
                                or call.image_subjects
                                or call.reply_target_status != "none"
                            ):
                                raise OperationError("invalid_background_model_action")
                        else:
                            self.policy.check_transaction(
                                db, call.subject, call.scope, "message.read",
                                call.provider, call.model, False,
                            )
                        if call.history_subjects:
                            if (
                                call.action not in {
                                    "model.invoke", "tool.invoke:web.search", "tool.invoke:web.fetch",
                                    "tool.invoke:http.get", "tool.invoke:http.post",
                                }
                                or not call.includes_history
                                or len(call.history_subjects) > 8
                                or len(set(call.history_subjects)) != len(call.history_subjects)
                                or any(not subject or subject != subject.strip()
                                       for subject in call.history_subjects)
                            ):
                                raise OperationError("invalid_history_sources")
                            for subject in call.history_subjects:
                                self.policy.check_transaction(
                                    db, subject, call.scope, "message.read",
                                    call.provider, call.model, False,
                                )
                                self.policy.check_transaction(
                                    db, subject, call.scope, call.action,
                                    call.provider, call.model, True, False,
                                )
                        if document_upload_subjects:
                            if (
                                call.action not in {
                                    "model.invoke", "tool.invoke:web.search", "tool.invoke:web.fetch",
                                    "tool.invoke:http.get", "tool.invoke:http.post",
                                }
                                or not call.includes_history
                                or type(document_upload_subjects) is not tuple
                                or not 1 <= len(document_upload_subjects) <= 20
                                or len(set(document_upload_subjects)) != len(document_upload_subjects)
                                or any(
                                    type(subject) is not str or not subject
                                    or subject != subject.strip() or len(subject) > 64
                                    for subject in document_upload_subjects
                                )
                            ):
                                raise OperationError("invalid_document_upload_sources")
                        if shared_upload_authorities:
                            if (not isinstance(call.scope, Scope) or preflight_transaction is None
                                    or call.action not in {
                                "model.invoke", *_EXTERNAL_TOOL_ACTIONS, "message.reply", "message.sticker",
                            } or call.action not in _SEND_ACTIONS and not call.includes_history):
                                raise OperationError("invalid_shared_upload_sources")
                            for authority in shared_upload_authorities:
                                receipt = authority.receipt
                                identity = authority.object_identity
                                material = ("knowledge" if isinstance(identity, KnowledgeChunkPointer)
                                            else "slang" if isinstance(identity, SlangChatProjection)
                                            else "style")
                                if (identity.scope != receipt.source_scope
                                    or material != receipt.material_type):
                                    raise OperationError("invalid_shared_upload_sources")
                                if receipt.target_scope != call.scope:
                                    raise OperationError("invalid_shared_upload_sources")
                        self.assert_upload_permissions_transaction(
                            db, call, document_upload_subjects=document_upload_subjects,
                            shared_upload_authorities=shared_upload_authorities,
                        )
                        if call.action != "message.sticker" and (
                            call.includes_images or call.image_subjects
                        ):
                            sources = call.image_subjects
                            if (
                                call.action not in {"model.invoke", *_EXTERNAL_TOOL_ACTIONS}
                                or not call.includes_images
                                or type(sources) is not tuple
                                or not 1 <= len(sources) <= 8
                            ):
                                raise OperationError("invalid_image_sources")
                            if any(
                                type(subject) is not str
                                or not subject
                                or subject != subject.strip()
                                or len(subject) > 64
                                for subject in sources
                            ) or len(set(sources)) != len(sources):
                                raise OperationError("invalid_image_sources")
                            for subject in sources:
                                self.policy.check_transaction(
                                    db, subject, call.scope, "message.read",
                                    call.provider, call.model, False, False,
                                )
                                self.policy.check_transaction(
                                    db, subject, call.scope, "media.read",
                                    call.provider, call.model, False, False,
                                )
                                if call.action == "model.invoke" and subject != call.subject:
                                    self.policy.check_transaction(
                                        db, subject, call.scope, "model.invoke",
                                        call.provider, call.model, False, True,
                                    )
                                if call.action in _EXTERNAL_TOOL_ACTIONS:
                                    self.policy.check_transaction(
                                        db, subject, call.scope, call.action,
                                        call.provider, call.model, False, True,
                                    )
                        if call.action == "message.sticker":
                            if (
                                type(call.sticker_id) is not str
                                or _STICKER_ID_RE.fullmatch(call.sticker_id) is None
                                or type(call.catalog_revision) is not int
                                or call.catalog_revision < 1
                                or call.catalog_revision > 1_000_000_000
                            ):
                                raise OperationError("invalid_sticker")
                            if call.includes_images or call.image_subjects:
                                raise OperationError("invalid_sticker_sources")
                            self.policy.check_transaction(
                                db, call.subject, call.scope, "media.send",
                                call.provider, call.model, False, False,
                            )
                        elif call.sticker_id or call.catalog_revision:
                            raise OperationError("invalid_sticker_action")
                        contact_now = time()
                        self._assert_contact_preflight_transaction(db, call, stage="intent", now=contact_now,
                            qq_spec=qq_admission.spec if qq_admission is not None else None)
                        if preflight_transaction is not None:
                            preflight_transaction(db)
                        if call.contact is not None:
                            self.store.mark_contact_action_intent_transaction(db, call, now=contact_now)
                        db.execute(
                            "INSERT INTO actions(id,request_id,digest,state,revision,action,subject,"
                            "bot_id,group_id,"
                            "scope_kind,private_user_id) VALUES (?,?,?,'dispatching',?,?,?,?,?,?,?)",
                            (
                                call.key,
                                call.request_id,
                                digest,
                                revision,
                                call.action,
                                call.subject,
                                call.scope.bot_id,
                                call.scope.group_id if call.scope.kind == "group" else None,
                                call.scope.kind,
                                call.scope.private_user_id if call.scope.kind == "private" else None,
                            ),
                        )
                        db.execute(
                            "INSERT INTO audit(kind,identity,revision,code,details) "
                            "VALUES ('dispatch',?,?,'allowed',?)",
                            (call.key, revision, json.dumps({
                                "provider": call.provider, "model": call.model, "task": frozen_price.task,
                                "pricing": frozen_price.evidence(),
                            }, separators=(",", ":")) if frozen_price is not None else ""),
                        )
                        if qq_admission is not None:
                            assert qq_owner is not None and qq_revisions is not None
                            account_revision, target_revision = qq_revisions
                            self.store.qq_consume_transaction(
                                db, action_id=call.key, spec=qq_admission.spec,
                                expected_account_revision=account_revision,
                                expected_target_revision=target_revision,
                                limits=qq_owner.limits, now=time(),
                                governor_revision=qq_admission.governor_revision,
                            )
                    except OperationError as exc:
                        db.execute("ROLLBACK TO action_intent")
                        db.execute("RELEASE action_intent")
                        db.execute(
                            "INSERT INTO audit(kind,identity,revision,code) VALUES ('deny',?,0,?)",
                            (call.key, exc.code),
                        )
                        return exc.code
                    db.execute("RELEASE action_intent")
                    return ""

                try:
                    error = await self.store.transaction(intent)
                except asyncio.CancelledError:
                    if qq_admission is not None:
                        row = await self.store.transaction(lambda db: db.execute(
                            "SELECT qq_account_id FROM actions WHERE id=?", (call.key,),
                        ).fetchone())
                        qq_committed = row is not None and row["qq_account_id"] is not None
                    if turn is not None and committed_delivery_total is not None:
                        # transaction() drains a cancelled SQL worker. Its append may
                        # already be durable even though the await raised cancellation.
                        row = await self.store.transaction(lambda db: db.execute(
                            "SELECT total FROM deliveries WHERE request_id=?", (call.request_id,),
                        ).fetchone())
                        assert row is not None
                        turn.segments_total = int(row["total"])
                        if turn.segments_sent != turn.segments_total:
                            turn.emission = "partial"
                    if turn is not None and call.action in _SEND_ACTIONS:
                        turn.dispatching_segment_index = None
                    await self._finish(call.key, "cancelled_before_dispatch")
                    raise
                except OperationError as exc:
                    if qq_owner is not None and exc.code == "storage_unavailable":
                        qq_owner.hold_local("storage_unavailable")
                    raise
                if error:
                    if turn is not None and call.action in _SEND_ACTIONS:
                        turn.dispatching_segment_index = None
                    raise OperationError(error)

                qq_committed = qq_admission is not None
                # A finite extension total is established by the caller's Store
                # preflight in this exact intent transaction, never before commit.
                if committed_delivery_total is not None:
                    assert turn is not None and call.action in _SEND_ACTIONS
                    turn.segments_total = committed_delivery_total
                # The durable-intent await is inside the same ordering lock as invalidation.
                if turn is not None:
                    try:
                        turn.check()
                    except OperationError:
                        await self._finish(call.key, "cancelled_before_dispatch")
                        raise
                    if call.action in _SEND_ACTIONS:
                        turn.emission = "dispatching"
                dispatched = False
                acknowledged = False
                receipt_task: asyncio.Task[None] | None = None
                failure_task: asyncio.Task[None] | None = None
                acknowledged_result: T
                acknowledged_completed_at = 0.0
                proof = TransportProof(asyncio.Event())
                proof.adapter_done.set()
                startup = asyncio.Event()
                operation_timed_out = False
                operation_deadline = monotonic() + timeout

                async def record_acknowledgment() -> None:
                    result = acknowledged_result
                    try:
                        await self._finish(
                            call.key, "succeeded",
                            result.message_id if isinstance(result, SendReceipt) else "",
                            details=self._metrics(
                                call, operation_started, result, first_delta_ms=first_delta_ms,
                                frozen_price=frozen_price,
                            ),
                        )
                    except Exception as exc:
                        proof.settlement_error = exc
                        raise
                    if turn is not None and call.action in _SEND_ACTIONS:
                        turn.last_segment_completed_at = acknowledged_completed_at
                        turn.segments_sent += 1
                        if isinstance(result, SendReceipt):
                            turn.sent_receipts.append(result.message_id)
                        turn.emission = "sent" if turn.segments_sent == turn.segments_total else "partial"
                        turn.dispatching_segment_index = None

                async def finish_acknowledgment() -> None:
                    nonlocal receipt_task
                    if failure_task is not None:
                        # A quarantined late return cannot replace its already
                        # established unknown/failed terminal or invent a receipt.
                        await drain_on_cancel(failure_task)
                        return
                    if receipt_task is None:
                        receipt_task = asyncio.create_task(record_acknowledgment())
                    await drain_on_cancel(receipt_task)

                async def finish_failure(
                    state: str, *, code: str = "", details: str = "",
                    evidence: QQTransportEvidence | None = None,
                ) -> None:
                    nonlocal failure_task
                    if acknowledged:
                        await finish_acknowledgment()
                        return
                    if failure_task is None:
                        async def commit_failure() -> None:
                            try:
                                await self._finish(call.key, state, code=code,
                                                   details=details, evidence=evidence)
                            except Exception as exc:
                                proof.settlement_error = exc
                                raise
                        failure_task = asyncio.create_task(commit_failure())
                    await drain_on_cancel(failure_task)

                operation_started = 0.0

                async def run() -> T:
                    nonlocal dispatched, acknowledged, acknowledged_result, operation_timed_out
                    nonlocal acknowledged_completed_at, operation_started
                    started = operation_started = monotonic()
                    try:
                        # Install this exception/finalizer path before registration
                        # can be exposed to Policy cancellation.
                        startup.set()
                        async with asyncio.timeout_at(operation_deadline):
                            if turn is not None:
                                turn.check()
                            def contact_port(db: sqlite3.Connection) -> None:
                                db.execute("BEGIN IMMEDIATE")
                                self._assert_contact_preflight_transaction(
                                    db, call, stage="operation", now=time(),
                                    qq_spec=qq_admission.spec if qq_admission is not None else None)
                            async with self.policy.dispatch_boundary:
                                await self.store.transaction(contact_port)
                            # A due timeout callback need not have run before this
                            # synchronous port boundary. Do not open an expired action.
                            if monotonic() >= operation_deadline:
                                raise TimeoutError
                            if before_operation is not None:
                                before_operation()
                            dispatched = True
                            proof.adapter_done.clear()
                            try:
                                if qq_admission is not None:
                                    assert qq_owner is not None and self._qq_generation is not None
                                    grant = qq_owner.grant(
                                        qq_admission, self._qq_generation(),
                                        on_wire_start=(
                                            turn.mark_message_dispatch
                                            if turn is not None and call.action in _SEND_ACTIONS else None
                                        ),
                                    )
                                    result = await cast(
                                        Callable[[QQWriteGrant], Awaitable[T]], operation,
                                    )(grant)
                                else:
                                    result = await cast(Callable[[], Awaitable[T]], operation)()
                                # Freeze verified ACK before publishing adapter closure.
                                acknowledged_completed_at = monotonic()
                                acknowledged_result = result
                                acknowledged = True
                            finally:
                                proof.adapter_done.set()
                    except BaseException as exc:
                        if isinstance(exc, TimeoutError) and not task.cancelling():
                            operation_timed_out = True
                        if qq_admission is not None:
                            assert qq_owner is not None
                            dispatched = qq_owner.wire_started(qq_admission)
                            if dispatched and not acknowledged:
                                qq_owner.hold_local("unknown_delivery")
                        if turn is not None and call.action in _SEND_ACTIONS and not acknowledged:
                            # Revocation holds this boundary while draining run;
                            # terminal commit must never wait for it again.
                            turn.emission = "unknown" if dispatched else "pending"
                            turn.dispatching_segment_index = None
                        await finish_failure(
                            "cancelled_before_dispatch"
                            if not dispatched
                            else "unknown"
                            if external
                            else "failed",
                            code=exc.code if isinstance(exc, OperationError) else
                            "timeout" if isinstance(exc, TimeoutError) or (
                                isinstance(exc, asyncio.CancelledError) and operation_timed_out
                            ) else "cancelled" if isinstance(exc, asyncio.CancelledError)
                            else "transport_error",
                            evidence=exc.evidence if isinstance(exc, QQTransportError) else None,
                            details=(json.dumps(exc.safe_dump(), separators=(",", ":"))
                                     if isinstance(exc, QQTransportError) else
                                     self._metrics(call, started, first_delta_ms=first_delta_ms,
                                                   frozen_price=frozen_price)),
                        )
                        raise
                    # A verified adapter receipt survives cancellation even while
                    # run waits for Policy's boundary. Its finalizer owns no such lock.
                    if turn is not None and call.action in _SEND_ACTIONS:
                        try:
                            async with self.policy.dispatch_boundary:
                                await finish_acknowledgment()
                        except asyncio.CancelledError:
                            await finish_acknowledgment()
                            raise
                    else:
                        await finish_acknowledgment()
                    if monotonic() >= operation_deadline:
                        raise TimeoutError
                    return result

                async def cancel_operation() -> bool:
                    # Caller cancellation can win the startup await itself. Let
                    # run install its terminal path before the first child cancel.
                    await startup.wait()
                    drained = await self.policy.cancel_transports((registered,))
                    if acknowledged:
                        await finish_acknowledgment()
                    elif failure_task is not None:
                        # Run may already have completed and left registration;
                        # its actual terminal error still belongs to this caller.
                        await drain_on_cancel(failure_task)
                    elif not drained:
                        await finish_failure("unknown" if external else "failed")
                    elif qq_admission is not None:
                        assert qq_owner is not None
                        await finish_failure(
                            "unknown" if qq_owner.wire_started(qq_admission)
                            else "cancelled_before_dispatch",
                        )
                    elif not dispatched:
                        await finish_failure("cancelled_before_dispatch")
                    return drained

                async def cancel_caller() -> None:
                    drained = await drain_on_cancel(asyncio.create_task(cancel_operation()))
                    if not drained:
                        raise OperationError("transport_cancel_failed") from None
                    caller = asyncio.current_task()
                    if caller is not None and not caller.cancelling():
                        raise OperationError("revoked") from None

                task = asyncio.create_task(run())
                registered = cast("asyncio.Task[object]", task)
                self.policy.active_transports[registered] = proof
                if qq_admission is not None:
                    self._qq_transports[registered] = qq_admission

                def settled(completed: asyncio.Task[T]) -> None:
                    self.policy.active_transports.pop(registered, None)
                    self._qq_transports.pop(registered, None)
                    if not completed.cancelled():
                        completed.exception()  # Retrieve late failures after a quarantined operation.

                task.add_done_callback(settled)
                try:
                    # Still hold the original boundary: Policy cannot cancel a run
                    # before its exception path is installed. This await itself is
                    # protected by the same caller cleanup as the later wait.
                    await startup.wait()
                except asyncio.CancelledError:
                    await cancel_caller()
                    raise

            try:
                done, _ = await asyncio.wait({task}, timeout=max(0.0, operation_deadline - monotonic()))
                if not done:
                    if not proof.cancel_requested and not task.cancelling():
                        operation_timed_out = True
                    drained = await drain_on_cancel(asyncio.create_task(cancel_operation()))
                    if not drained:
                        raise OperationError("transport_cancel_failed")
                    raise TimeoutError
                return task.result()
            except asyncio.CancelledError:
                # Repeated caller cancellation cannot skip the one durable task.
                await cancel_caller()
                raise

        finally:
            if qq_admission is not None:
                assert qq_owner is not None
                qq_owner.release(qq_admission, qq_committed)

    @staticmethod
    def _metrics(
        call: ActionCall,
        started: float,
        result: object = None,
        *,
        first_delta_ms: Callable[[float], int | None] | None = None,
        frozen_price: FrozenModelPrice | None = None,
    ) -> str:
        usage = result.usage if isinstance(result, ModelReply) else None
        task = frozen_price.task if frozen_price is not None else "send"
        metrics: dict[str, object] = {
            "elapsed_ms": round(max(0, monotonic() - started) * 1000),
            "task": task,
            "usage": usage.model_dump() if usage is not None else None,
            "reply_target": call.reply_target_status if call.action in _SEND_ACTIONS else None,
        }
        if first_delta_ms is not None:
            metrics["first_delta_ms"] = first_delta_ms(started)
        if call.action in _MODEL_ACTIONS:
            # Policy provider is the frozen profile/destination fingerprint,
            # not an endpoint or credential reconstructed from current config.
            metrics["provider"] = call.provider
            metrics["model"] = call.model
            assert frozen_price is not None
            metrics["pricing"] = frozen_price.evidence(usage)
        return json.dumps(metrics, separators=(",", ":"))

    async def _finish(
        self, key: str, state: str, receipt: str = "", *, code: str = "", details: str = "",
        evidence: QQTransportEvidence | None = None,
    ) -> None:
        final_batch_attempt = any(
            admission.binding.action_key == key and self.qq_delivery is not None
            and self.qq_delivery.test_batch_final_attempt(admission)
            for admission in self._qq_transports.values()
        )
        def finish(db: sqlite3.Connection) -> bool:
            row = db.execute("SELECT revision,state,qq_account_id FROM actions WHERE id=?", (key,)).fetchone()
            if row is None:
                return False
            if row["qq_account_id"] is not None:
                if row["state"] != "dispatching":
                    return False
                self.store.qq_settle_transaction(
                    db, action_id=key,
                    state=cast("Literal['succeeded','failed','cancelled_before_dispatch','unknown']", state),
                    code=code, receipt=receipt, now=time(), evidence=evidence,
                    hold_scope="account" if final_batch_attempt else None,
                    hold_reason=("qq_test_batch_complete"
                                 if final_batch_attempt and state != "unknown" else ""),
                )
            else:
                db.execute("UPDATE actions SET state=?,code=?,receipt=? WHERE id=?",
                       (state, code or state, receipt, key))
            db.execute(
                "INSERT INTO audit(kind,identity,revision,code,details) VALUES ('terminal',?,?,?,?)",
                (key, row["revision"], code or state, details),
            )

            return row["qq_account_id"] is not None

        try:
            governed = await self.store.transaction(finish)
            if governed:
                assert self.qq_delivery is not None
                await self.qq_delivery.refresh()
        except Exception:
            self.policy.transport_fault = True
            if self.qq_delivery is not None:
                self.qq_delivery.hold_local("storage_unavailable")
            raise

    async def close(self) -> None:
        async def shutdown() -> None:
            async with self.policy.dispatch_boundary:
                self._closed = True
                if self.qq_delivery is not None:
                    self.qq_delivery.invalidate("closed")
                if not await self.policy.cancel_transports(tuple(self.policy.active_transports)):
                    raise OperationError("transport_cancel_failed")

        await drain_on_cancel(asyncio.create_task(shutdown()))
        if self.qq_delivery is not None:
            self.policy.unbind_qq_invalidation(self.qq_delivery.invalidate)
            await self.qq_delivery.close()
