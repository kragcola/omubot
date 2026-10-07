"""Exact-scope grants and the shared dispatch/revocation ordering boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, TypeAdapter, ValidationError, field_validator

from .store import Store, drain_on_cancel
from .types import (
    ContactAuthorityProof,
    ContactPurpose,
    ConversationScope,
    Grant,
    OperationError,
    Scope,
    StrictModel,
    VisibilityGrant,
    VisibilityMaterial,
    VisibilityObjectRef,
    VisibilityReceipt,
)

CHARACTER_TOOL_ACTIONS = frozenset({
    "tool.invoke:ccip.identify", "tool.invoke:ccip.embed", "tool.invoke:animetrace.identify",
})
ONEBOT_INTERACTION_ACTIONS = frozenset({"manage_group", "poke", "reaction"})
ONEBOT_HISTORY_ACTIONS = frozenset({"history.read"})
_DESTINATION_ACTIONS = frozenset({
    "model.invoke", "model.schedule", "model.dream", "tool.invoke:web.search", "tool.invoke:web.fetch",
    "tool.invoke:http.get", "tool.invoke:http.post",
}) | CHARACTER_TOOL_ACTIONS | ONEBOT_INTERACTION_ACTIONS | ONEBOT_HISTORY_ACTIONS

KNOWN = frozenset(
    {
        "message.read",
        "media.read",
        "media.send",
        "message.mention",
        "model.invoke",
        "model.schedule",
        "model.dream",
        "message.reply",
        "message.sticker",
        "sticker.manage",
        "journal.manage",
        "research.capture", "research.read",
        "affection.manage",
        "food.search.configure",
        "memory.archive",
        "memory.learn",
        "memory.retrieve",
        "memory.review",
        "memory.apply",
        "knowledge.import",
        "knowledge.review",
        "knowledge.apply",
        "knowledge.retrieve",
        "knowledge.remove",
        "graph.learn",
        "graph.review",
        "graph.apply",
        "graph.retrieve",
        "graph.remove",
        "tool.invoke:time.now",
        "tool.invoke:web.search",
        "tool.invoke:web.fetch", "tool.invoke:http.get", "tool.invoke:http.post",
        *CHARACTER_TOOL_ACTIONS,
        *ONEBOT_INTERACTION_ACTIONS,
        *ONEBOT_HISTORY_ACTIONS,
        "status.read",
        "policy.configure",
    }
)
GRANTS = TypeAdapter(list[Grant])
VISIBILITY_GRANTS = TypeAdapter(tuple[VisibilityGrant, ...])
_MAX_VISIBILITY_BYTES = 65_536


class ContactConsent(StrictModel):
    """An admin-managed named switch, independent of every ordinary action Grant."""

    kind: Literal["user", "group"]
    bot_id: str = Field(min_length=1, max_length=64)
    subject_id: str = Field(min_length=1, max_length=64)
    enabled: bool
    generation: str

    @field_validator("bot_id", "subject_id")
    @classmethod
    def exact_identity(cls, value: str) -> str:
        if value != value.strip() or "*" in value:
            raise ValueError("contact identity must be exact")
        return value

    @field_validator("generation")
    @classmethod
    def exact_generation(cls, value: str) -> str:
        generation = UUID(value)
        if generation.version != 4 or str(generation) != value:
            raise ValueError("contact generation must be a canonical UUID4")
        return value


CONTACT_CONSENTS = TypeAdapter(tuple[ContactConsent, ...])
_MAX_CONTACT_CONSENTS = 256


@dataclass(frozen=True, slots=True)
class ContactConsentSnapshot:
    revision: int
    consents: tuple[ContactConsent, ...]


@dataclass(frozen=True, slots=True)
class OwnPermissionProjection:
    """Current own-scope permission proof; destinations never belong in chat output."""

    subject: str
    scope: ConversationScope
    revision: int
    grants: tuple[Grant, ...]
    actions: tuple[str, ...]


@dataclass(slots=True)
class TransportProof:
    """Actual adapter exit and durable failure for one Actions-owned run."""

    adapter_done: asyncio.Event
    cancel_requested: bool = False
    settlement_error: Exception | None = None


class Policy:
    def __init__(self, store: Store, bot_id: str, mode: str) -> None:
        if mode not in {"offline", "live"}:
            raise OperationError("invalid_mode")
        self.store = store
        self.bot_id = bot_id
        self.mode = mode
        self.dispatch_boundary = asyncio.Lock()
        self.active_transports: dict[asyncio.Task[object], TransportProof] = {}
        self.transport_fault = False
        self._qq_invalidation: Callable[[str], None] | None = None
        self._contact_invalidation: Callable[[ContactConsent], None] | None = None
        self._read_revocation_cleanup: tuple[
            Callable[[Callable[[str, ConversationScope], bool]], None], Callable[[], None]
        ] | None = None

    def bind_qq_invalidation(self, invalidate: Callable[[str], None]) -> None:
        if self._qq_invalidation is not None:
            raise RuntimeError("Policy already has a QQ admission owner")
        self._qq_invalidation = invalidate

    def unbind_qq_invalidation(self, invalidate: Callable[[str], None]) -> None:
        if self._qq_invalidation == invalidate:
            self._qq_invalidation = None

    def bind_contact_invalidation(self, invalidate: Callable[[ContactConsent], None]) -> None:
        """Bind the Conversation owner that cancels proposals for this named switch."""
        if self._contact_invalidation is not None and self._contact_invalidation != invalidate:
            raise RuntimeError("policy already has a contact invalidation owner")
        self._contact_invalidation = invalidate

    def unbind_contact_invalidation(self, invalidate: Callable[[ContactConsent], None]) -> None:
        if self._contact_invalidation == invalidate:
            self._contact_invalidation = None

    async def contact_snapshot(self) -> ContactConsentSnapshot:
        def read(db: sqlite3.Connection) -> ContactConsentSnapshot:
            revision, _, _, _, _, consents, _ = self._read_stored(db)
            return ContactConsentSnapshot(revision, consents)
        return await self.store.transaction(read)

    def contact_authority_transaction(
        self, db: sqlite3.Connection, *, scope: Scope, target_user_id: str, purpose: ContactPurpose,
    ) -> ContactAuthorityProof:
        revision, _, _, _, _, consents, _ = self._read_stored(db)
        if self.transport_fault:
            raise OperationError("transport_cancel_failed")
        if (scope.bot_id != self.bot_id or purpose not in {"autonomous_chat", "role_life_broadcast"}):
            raise OperationError("contact_denied")
        user = next((item for item in consents if item.kind == "user"
                     and item.subject_id == target_user_id), None)
        group = next((item for item in consents if item.kind == "group"
                      and item.subject_id == scope.group_id), None)
        if user is None or group is None or not user.enabled or not group.enabled:
            raise OperationError("contact_denied")
        return ContactAuthorityProof(
            policy_revision=revision, scope=scope, target_user_id=target_user_id, purpose=purpose,
            user_generation=user.generation, group_generation=group.generation,
        )

    def assert_contact_authority_transaction(
        self, db: sqlite3.Connection, *, authority: ContactAuthorityProof,
    ) -> None:
        try:
            current = self.contact_authority_transaction(
                db, scope=authority.scope, target_user_id=authority.target_user_id, purpose=authority.purpose,
            )
        except OperationError as exc:
            if exc.code == "contact_denied":
                raise OperationError("stale_contact_authority") from exc
            raise
        if current != authority:
            raise OperationError("stale_contact_authority")

    async def set_contact_consent(
        self, *, actor: str, kind: Literal["user", "group"], subject_id: str,
        enabled: bool, expected_revision: int,
    ) -> tuple[int, ContactConsent]:
        """Trusted admin ingress supplies actor; a switch never supplies an action Grant."""
        if not actor or actor != actor.strip() or type(expected_revision) is not int:
            raise OperationError("invalid_policy")
        try:
            proposed = ContactConsent(kind=kind, bot_id=self.bot_id, subject_id=subject_id,
                                      enabled=enabled, generation=str(uuid4()))
        except ValidationError as exc:
            raise OperationError("invalid_contact_consent") from exc

        async def commit_and_drain() -> tuple[int, ContactConsent]:
            async with self.dispatch_boundary:
                def update(db: sqlite3.Connection) -> tuple[int, ContactConsent, bool]:
                    db.execute("BEGIN IMMEDIATE")
                    revision, _, ordinary, _, visibility, consents, previous = self._read_stored(db)
                    if revision != expected_revision:
                        raise OperationError("revision_conflict")
                    existing = next((item for item in consents if item.kind == proposed.kind
                                     and item.subject_id == proposed.subject_id), None)
                    if existing is not None and existing.enabled == proposed.enabled:
                        return revision, existing, False
                    updated = tuple(item for item in consents if item != existing) + (proposed,)
                    if len(updated) > _MAX_CONTACT_CONSENTS:
                        raise OperationError("contact_capacity")
                    updated = tuple(sorted(updated, key=lambda item: (item.kind, item.subject_id)))
                    serialized = CONTACT_CONSENTS.dump_json(updated)
                    revision += 1
                    db.execute("UPDATE policy SET revision=?,contact_consents=? WHERE id=1",
                               (revision, serialized.decode()))
                    db.execute(
                        "INSERT INTO audit(kind,identity,revision,code,details) VALUES ('policy',?,?,?,?)",
                        (actor, revision, "contact_enabled" if enabled else "contact_disabled",
                         json.dumps({"old": hashlib.sha256(ordinary).hexdigest(),
                                     "new": hashlib.sha256(ordinary).hexdigest(),
                                     "visibility_old": hashlib.sha256(visibility).hexdigest(),
                                     "visibility_new": hashlib.sha256(visibility).hexdigest(),
                                     "contact_old": hashlib.sha256(previous).hexdigest(),
                                     "contact_new": hashlib.sha256(serialized).hexdigest()})),
                    )
                    return revision, proposed, True
                revision, consent, changed = await self.store.transaction(update)
                if not changed:
                    return revision, consent
                cleanup_error: Exception | None = None
                if self._contact_invalidation is not None:
                    try:
                        self._contact_invalidation(consent)
                    except Exception as exc:
                        cleanup_error = exc
                if self._qq_invalidation is not None:
                    try:
                        self._qq_invalidation("revoked")
                    except Exception as exc:
                        cleanup_error = exc
                if not await self.cancel_transports(tuple(self.active_transports)):
                    raise OperationError("transport_cancel_failed")
                if cleanup_error is not None:
                    raise OperationError("contact_cleanup_failed") from cleanup_error
                return revision, consent

        return await drain_on_cancel(asyncio.create_task(commit_and_drain()))

    def bind_read_revocation_cleanup(
        self,
        cleanup: Callable[[Callable[[str, ConversationScope], bool]], None],
        clear: Callable[[], None],
    ) -> None:
        """Bind the one Conversation that owns this Policy's short-term observations."""
        callbacks = (cleanup, clear)
        if self._read_revocation_cleanup is not None and self._read_revocation_cleanup != callbacks:
            raise RuntimeError("policy already has a read-revocation cleanup owner")
        self._read_revocation_cleanup = callbacks

    def unbind_read_revocation_cleanup(
        self,
        cleanup: Callable[[Callable[[str, ConversationScope], bool]], None],
        clear: Callable[[], None],
    ) -> None:
        if self._read_revocation_cleanup == (cleanup, clear):
            self._read_revocation_cleanup = None

    def _read(self, db: sqlite3.Connection) -> tuple[int, list[Grant]]:
        revision, grants, _, _, _, _, _ = self._read_stored(db)
        return revision, grants

    def _read_stored(
        self, db: sqlite3.Connection
    ) -> tuple[int, list[Grant], bytes, tuple[VisibilityGrant, ...], bytes,
               tuple[ContactConsent, ...], bytes]:
        row = db.execute(
            "SELECT revision,CAST(grants AS BLOB) AS grants_bytes,"
            "CAST(visibility_grants AS BLOB) AS visibility_bytes,"
            "CAST(contact_consents AS BLOB) AS contact_bytes FROM policy WHERE id=1"
        ).fetchone()
        if row is None or type(row["revision"]) is not int or row["revision"] < 0:
            raise OperationError("invalid_policy")
        raw_grants = row["grants_bytes"]
        raw_visibility = row["visibility_bytes"]
        raw_contact = row["contact_bytes"]
        if (not isinstance(raw_grants, bytes) or not isinstance(raw_visibility, bytes)
                or not isinstance(raw_contact, bytes)
                or len(raw_visibility) > _MAX_VISIBILITY_BYTES):
            raise OperationError("invalid_policy")
        try:
            grants = GRANTS.validate_json(raw_grants, strict=True)
            visibility = VISIBILITY_GRANTS.validate_json(raw_visibility, strict=True)
            contact = CONTACT_CONSENTS.validate_json(raw_contact, strict=True)
            if len(contact) > _MAX_CONTACT_CONSENTS:
                raise OperationError("invalid_policy")
        except (ValidationError, TypeError) as exc:
            raise OperationError("invalid_policy") from exc
        self._validate(grants)
        self._validate_visibility(visibility)
        identities = {(item.kind, item.subject_id) for item in contact}
        if len(identities) != len(contact) or any(item.bot_id != self.bot_id for item in contact):
            raise OperationError("invalid_policy")
        revision = row["revision"]
        if revision == 0:
            if grants or visibility or contact:
                raise OperationError("invalid_policy")
        else:
            change = db.execute(
                "SELECT revision,details FROM audit WHERE kind='policy' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            try:
                valid = (
                    change is not None
                    and change["revision"] == revision
                    and json.loads(change["details"])["new"]
                    == hashlib.sha256(raw_grants).hexdigest()
                    and json.loads(change["details"])["visibility_new"]
                    == hashlib.sha256(raw_visibility).hexdigest()
                    and json.loads(change["details"])["contact_new"]
                    == hashlib.sha256(raw_contact).hexdigest()
                )
            except (ValueError, KeyError, TypeError) as exc:
                raise OperationError("invalid_policy") from exc
            if not valid:
                raise OperationError("invalid_policy")
        return revision, grants, raw_grants, visibility, raw_visibility, contact, raw_contact

    def _validate(self, grants: list[Grant]) -> None:
        for grant in grants:
            if grant.scope.bot_id != self.bot_id or not grant.actions or not set(grant.actions) <= KNOWN:
                raise OperationError("invalid_policy")
            if grant.subject.startswith("system:"):
                expected = {
                    "system:schedule": "model.schedule",
                    "system:dream": "model.dream",
                }.get(grant.subject)
                if (
                    grant.scope.kind != "group"
                    or expected is None
                    or grant.actions != [expected]
                    or grant.allow_history
                    or grant.allow_images
                ):
                    raise OperationError("invalid_policy")

    def _validate_visibility(self, grants: tuple[VisibilityGrant, ...]) -> None:
        if (len(grants) > 256 or sum(len(grant.object_refs) for grant in grants) > 256
                or len({grant.grant_id for grant in grants}) != len(grants)
                or any(grant.source_scope.bot_id != self.bot_id for grant in grants)):
            raise OperationError("invalid_visibility_policy")

    async def visibility_snapshot(self) -> tuple[int, tuple[VisibilityGrant, ...]]:
        def read(db: sqlite3.Connection) -> tuple[int, tuple[VisibilityGrant, ...]]:
            revision, _, _, visibility, _, _, _ = self._read_stored(db)
            return revision, visibility
        return await self.store.transaction(read)

    def visibility_receipt_transaction(
        self, db: sqlite3.Connection, *, actor: str, grant_id: str,
        source_scope: Scope, target_scope: Scope, material_type: VisibilityMaterial,
        object_refs: tuple[VisibilityObjectRef, ...],
    ) -> VisibilityReceipt:
        """Authorize selected visibility, not source validity or external upload."""
        revision, grants, _, visibility, _, _, _ = self._read_stored(db)
        action = "knowledge.retrieve" if material_type == "knowledge" else "memory.retrieve"
        self._check_grants(grants, actor, target_scope, action, "", "", False)
        grant = next((item for item in visibility if item.grant_id == grant_id), None)
        if (grant is None or grant.status != "active" or grant.expires_at <= time.time()
                or grant.source_scope != source_scope or grant.target_scope != target_scope
                or grant.material_type != material_type or not object_refs
                or len(set(object_refs)) != len(object_refs)
                or any(ref not in grant.object_refs for ref in object_refs)):
            raise OperationError("visibility_denied")
        return VisibilityReceipt(
            policy_revision=revision, grant_id=grant.grant_id,
            grant_revision=grant.grant_revision, reader_id=actor, source_scope=source_scope,
            target_scope=target_scope, material_type=material_type, object_refs=object_refs,
            expires_at=grant.expires_at,
        )

    async def read_visibility_receipt(
        self, *, actor: str, grant_id: str, source_scope: Scope, target_scope: Scope,
        material_type: VisibilityMaterial, object_refs: tuple[VisibilityObjectRef, ...],
    ) -> VisibilityReceipt:
        return await self.store.transaction(lambda db: self.visibility_receipt_transaction(
            db, actor=actor, grant_id=grant_id, source_scope=source_scope,
            target_scope=target_scope, material_type=material_type, object_refs=object_refs,
        ))

    def assert_visibility_receipt_transaction(
        self, db: sqlite3.Connection, *, actor: str, target_scope: Scope,
        receipt: VisibilityReceipt,
    ) -> None:
        if actor != receipt.reader_id or target_scope != receipt.target_scope:
            raise OperationError("stale_visibility")
        current = self.visibility_receipt_transaction(
            db, actor=actor, grant_id=receipt.grant_id, source_scope=receipt.source_scope,
            target_scope=target_scope, material_type=receipt.material_type,
            object_refs=receipt.object_refs,
        )
        if current != receipt:
            raise OperationError("stale_visibility")

    async def grant_visibility(
        self, *, actor: str, grant_id: str, source_scope: Scope, target_scope: Scope,
        material_type: VisibilityMaterial, object_refs: tuple[VisibilityObjectRef, ...],
        non_personal_projection_confirmed: bool, expires_at: float,
        expected_policy_revision: int, expected_grant_revision: int | None,
        source_preflight: Callable[[sqlite3.Connection, VisibilityGrant], None],
    ) -> tuple[int, VisibilityGrant]:
        """The source owner must attest the proposed exact refs in this transaction."""
        if (non_personal_projection_confirmed is not True or not callable(source_preflight)
                or type(expected_policy_revision) is not int or expected_policy_revision < 0
                or (expected_grant_revision is not None
                    and (type(expected_grant_revision) is not int or expected_grant_revision < 1))):
            raise OperationError("invalid_visibility_grant")
        proposed = VisibilityGrant(
            grant_id=grant_id, grant_revision=(expected_grant_revision or 0) + 1,
            source_scope=source_scope, target_scope=target_scope, material_type=material_type,
            object_refs=object_refs, non_personal_projection_confirmed=True, actor=actor,
            issued_at=time.time(), expires_at=expires_at,
        )
        proposed = VisibilityGrant.model_validate_json(proposed.model_dump_json(), strict=True)
        return await self._mutate_visibility(
            actor=actor, grant_id=grant_id, proposed=proposed,
            expected_policy_revision=expected_policy_revision,
            expected_grant_revision=expected_grant_revision, source_preflight=source_preflight,
        )

    async def revoke_visibility(
        self, *, actor: str, grant_id: str, expected_policy_revision: int,
        expected_grant_revision: int,
    ) -> tuple[int, VisibilityGrant]:
        """Revoking invalid or expired sources never requires their source preflight."""
        if (type(expected_policy_revision) is not int or expected_policy_revision < 0
                or type(expected_grant_revision) is not int or expected_grant_revision < 1):
            raise OperationError("invalid_visibility_grant")
        return await self._mutate_visibility(
            actor=actor, grant_id=grant_id, proposed=None,
            expected_policy_revision=expected_policy_revision,
            expected_grant_revision=expected_grant_revision, source_preflight=None,
        )

    async def _mutate_visibility(
        self, *, actor: str, grant_id: str, proposed: VisibilityGrant | None,
        expected_policy_revision: int, expected_grant_revision: int | None,
        source_preflight: Callable[[sqlite3.Connection, VisibilityGrant], None] | None,
    ) -> tuple[int, VisibilityGrant]:
        async def commit_and_drain() -> tuple[int, VisibilityGrant]:
            async with self.dispatch_boundary:
                def update(db: sqlite3.Connection) -> tuple[int, VisibilityGrant]:
                    db.execute("BEGIN IMMEDIATE")
                    (revision, grants, ordinary_bytes, visibility,
                     previous, _, contact_bytes) = self._read_stored(db)
                    if revision != expected_policy_revision:
                        raise OperationError("revision_conflict")
                    existing = next((g for g in visibility if g.grant_id == grant_id), None)
                    if ((existing is None and expected_grant_revision is not None)
                            or (existing is not None
                                and existing.grant_revision != expected_grant_revision)):
                        raise OperationError("visibility_revision_conflict")
                    if proposed is None:
                        if existing is None:
                            raise OperationError("visibility_not_found")
                        changed = VisibilityGrant.model_validate_json(existing.model_copy(update={
                            "status": "revoked", "actor": actor,
                            "grant_revision": existing.grant_revision + 1,
                        }).model_dump_json(), strict=True)
                    else:
                        changed = proposed
                        if changed.expires_at <= time.time():
                            raise OperationError("visibility_expired")
                    authority_scopes = (changed.source_scope, changed.target_scope)
                    if existing is not None:
                        authority_scopes += (existing.source_scope, existing.target_scope)
                    for scope in dict.fromkeys(authority_scopes):
                        self._check_grants(grants, actor, scope, "policy.configure", "", "", False)
                    if proposed is not None:
                        # Public active mutations always provide this hook. A missing
                        # internal hook is a broken owner contract, not success.
                        if source_preflight is None:
                            raise RuntimeError("active visibility mutation has no source owner")
                        source_preflight(db, changed)
                    updated = tuple(g for g in visibility if g.grant_id != grant_id) + (changed,)
                    updated = tuple(sorted(updated, key=lambda grant: grant.grant_id))
                    self._validate_visibility(updated)
                    serialized = VISIBILITY_GRANTS.dump_json(updated)
                    if len(serialized) > _MAX_VISIBILITY_BYTES:
                        raise OperationError("visibility_budget_exceeded")
                    revision += 1
                    db.execute("UPDATE policy SET revision=?,visibility_grants=? WHERE id=1",
                               (revision, serialized.decode()))
                    db.execute(
                        "INSERT INTO audit(kind,identity,revision,code,details) "
                        "VALUES ('policy',?,?,?,?)",
                        (actor, revision, "visibility_granted" if proposed else "visibility_revoked",
                         json.dumps({"old": hashlib.sha256(ordinary_bytes).hexdigest(),
                                     "new": hashlib.sha256(ordinary_bytes).hexdigest(),
                                     "visibility_old": hashlib.sha256(previous).hexdigest(),
                                     "visibility_new": hashlib.sha256(serialized).hexdigest(),
                                     "contact_old": hashlib.sha256(contact_bytes).hexdigest(),
                                     "contact_new": hashlib.sha256(contact_bytes).hexdigest()})),
                    )
                    return revision, changed
                result = await self.store.transaction(update)
                if self._qq_invalidation is not None:
                    self._qq_invalidation("revoked")
                if not await self.cancel_transports(tuple(self.active_transports)):
                    raise OperationError("transport_cancel_failed")
                return result
        return await drain_on_cancel(asyncio.create_task(commit_and_drain()))

    def check_transaction(
        self,
        db: sqlite3.Connection,
        subject: str,
        scope: ConversationScope,
        action: str,
        provider: str,
        model: str,
        includes_history: bool,
        includes_images: bool = False,
    ) -> int:
        revision, grants = self._read(db)
        if self.transport_fault and action in {
            "model.invoke", "model.schedule", "model.dream", "message.reply", "message.sticker",
            "tool.invoke:web.search", "tool.invoke:web.fetch",
            "tool.invoke:http.get", "tool.invoke:http.post",
            *CHARACTER_TOOL_ACTIONS, *ONEBOT_INTERACTION_ACTIONS, *ONEBOT_HISTORY_ACTIONS,
        }:
            raise OperationError("transport_cancel_failed")
        self._check_grants(
            grants, subject, scope, action, provider, model, includes_history, includes_images
        )
        return revision

    def _check_grants(
        self,
        grants: list[Grant],
        subject: str,
        scope: ConversationScope,
        action: str,
        provider: str,
        model: str,
        includes_history: bool,
        includes_images: bool = False,
    ) -> None:
        if action not in KNOWN or scope.bot_id != self.bot_id:
            raise OperationError("denied")
        grants = sorted(grants, key=lambda grant: grant.effect != "deny")
        for grant in grants:
            if (
                grant.subject != subject
                or grant.scope != scope
                or action not in grant.actions
                or grant.expires_at <= time.time()
            ):
                continue
            if action in _DESTINATION_ACTIONS and (
                grant.provider != provider or grant.model != model
            ):
                continue
            if grant.effect == "deny":
                raise OperationError("denied")
            if action in _DESTINATION_ACTIONS and (
                includes_history
                and not grant.allow_history
                or includes_images
                and not grant.allow_images
            ):
                continue
            return
        raise OperationError("denied")

    async def revision(self) -> int:
        return await self.store.transaction(lambda db: self._read(db)[0])

    async def check(
        self,
        subject: str,
        scope: ConversationScope,
        action: str,
        provider: str = "anthropic",
        model: str = "offline-model",
        includes_history: bool = False,
        includes_images: bool = False,
    ) -> int:
        return await self.store.transaction(
            lambda db: self.check_transaction(
                db, subject, scope, action, provider, model, includes_history, includes_images
            )
        )

    async def cancel_transports(self, tasks: tuple[asyncio.Task[object], ...]) -> bool:
        """Bound cleanup of trusted clients; fail closed if they violate cancellation."""
        proofs: list[TransportProof] = []
        for task in tasks:
            proof = self.active_transports.get(task)
            if proof is None:
                assert task.done()
                continue
            proofs.append(proof)
            if not proof.cancel_requested:
                proof.cancel_requested = True
                # Even an exited adapter can leave run waiting for this Policy's
                # boundary after a verified ACK. Its first cancel wakes that path.
                if not task.cancelling() and not task.done():
                    task.cancel()
        if not tasks:
            return True
        waits = {asyncio.create_task(proof.adapter_done.wait()) for proof in proofs}
        try:
            if waits:
                _, pending = await asyncio.wait(waits, timeout=0.1)
                if pending:
                    self.transport_fault = True
                    return False
        finally:
            for waiter in waits:
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(*waits, return_exceptions=True)

        async def join_settlement() -> None:
            # Adapter closure proves cooperation; only run completion proves the
            # original Actions finalizer has committed. Normal run exceptions
            # are not storage failures.
            await asyncio.gather(*tasks, return_exceptions=True)
            for proof in proofs:
                if proof.settlement_error is not None:
                    raise proof.settlement_error

        await drain_on_cancel(asyncio.create_task(join_settlement()))
        return True

    async def complete_snapshot(self) -> tuple[int, list[Grant], tuple[VisibilityGrant, ...]]:
        """Read both permission collections at one policy revision for administration."""
        def read(db: sqlite3.Connection) -> tuple[int, list[Grant], tuple[VisibilityGrant, ...]]:
            revision, grants, _, visibility, _, _, _ = self._read_stored(db)
            return revision, grants, visibility
        return await self.store.transaction(read)

    async def snapshot(self) -> tuple[int, list[Grant]]:
        return await self.store.transaction(self._read)

    def _own_permission_projection_transaction(
        self, db: sqlite3.Connection, *, subject: str, scope: ConversationScope,
    ) -> OwnPermissionProjection:
        revision, grants = self._read(db)
        self._check_grants(grants, subject, scope, "status.read", "", "", False)
        now = time.time()
        current = tuple(grant for grant in grants if grant.subject == subject
                        and grant.scope == scope and grant.expires_at > now)
        active_grants = list(current)
        actions: set[str] = set()
        for grant in current:
            if grant.effect != "allow":
                continue
            for action in grant.actions:
                if action in actions:
                    continue
                try:
                    self._check_grants(
                        active_grants, subject, scope, action, grant.provider, grant.model, False,
                    )
                except OperationError as exc:
                    if exc.code != "denied":
                        raise
                else:
                    actions.add(action)
        return OwnPermissionProjection(subject, scope, revision, current, tuple(sorted(actions)))

    async def read_own_permission_projection(
        self, *, subject: str, scope: ConversationScope,
    ) -> OwnPermissionProjection:
        """Disclose only this authenticated subject's current exact-scope grants."""
        return await self.store.transaction(lambda db: self._own_permission_projection_transaction(
            db, subject=subject, scope=scope,
        ))

    def assert_own_permission_projection_transaction(
        self, db: sqlite3.Connection, *, subject: str, scope: ConversationScope,
        frozen: OwnPermissionProjection,
    ) -> None:
        current = self._own_permission_projection_transaction(db, subject=subject, scope=scope)
        if current != frozen:
            raise OperationError("stale_own_permissions")

    async def replace(self, grants: list[Grant], expected_revision: int, actor: str) -> int:
        # Memory depends on Policy for reads; resolve its concrete write owner
        # here so qualification invalidation is atomic with policy replacement.
        from .memory import MemoryService

        if not actor or type(expected_revision) is not int:
            raise OperationError("invalid_policy")
        # Revalidate even values constructed outside normal Pydantic validation.
        validated = GRANTS.validate_json(json.dumps([g.model_dump() for g in grants]), strict=True)
        self._validate(validated)

        async def commit_and_drain() -> int:
            async with self.dispatch_boundary:

                def update(db: sqlite3.Connection) -> int:
                    (revision, _, previous_grants_bytes, _, visibility_bytes,
                     _, contact_bytes) = self._read_stored(db)
                    if revision != expected_revision:
                        raise OperationError("revision_conflict")
                    previous_grants = GRANTS.validate_json(previous_grants_bytes, strict=True)
                    def permitted(
                        rules: list[Grant], subject: str, scope: Scope, action: str,
                    ) -> bool:
                        try:
                            self._check_grants(rules, subject, scope, action, "", "", False)
                        except OperationError:
                            return False
                        return True

                    def matter_remains_qualified(subject: str, scope: Scope) -> bool:
                        return all(permitted(rules, subject, scope, action)
                                   for rules in (previous_grants, validated)
                                   for action in ("message.read", "memory.archive", "memory.learn"))

                    MemoryService.invalidate_matter_qualification_transaction(
                        db, bot_id=self.bot_id, actor=actor, now=time.time(),
                        remains_qualified=matter_remains_qualified,
                    )

                    for key_row in db.execute(
                        "SELECT bot_id,group_id,user_id FROM climate_states WHERE bot_id=?",
                        (self.bot_id,),
                    ).fetchall():
                        key = (str(key_row[0]), str(key_row[1]), str(key_row[2]))
                        scope = Scope(bot_id=key[0], group_id=key[1])
                        old_read = permitted(previous_grants, key[2], scope, "message.read")
                        new_read = permitted(validated, key[2], scope, "message.read")
                        if (not new_read or old_read != new_read
                                or permitted(previous_grants, key[2], scope, "message.reply")
                                != permitted(validated, key[2], scope, "message.reply")):
                            self.store.climate_invalidate_transaction(
                                db, key, actor=actor, reason="policy_source_changed",
                            )
                    revision += 1
                    serialized = GRANTS.dump_json(validated)
                    db.execute(
                        "UPDATE policy SET revision=?,grants=? WHERE id=1",
                        (revision, serialized.decode("utf-8")),
                    )
                    db.execute(
                        "INSERT INTO audit(kind,identity,revision,code,details) "
                        "VALUES ('policy',?,?,'replaced',?)",
                        (
                            actor,
                            revision,
                            json.dumps(
                                {
                                    "old": hashlib.sha256(previous_grants_bytes).hexdigest(),
                                    "new": hashlib.sha256(serialized).hexdigest(),
                                    "visibility_old": hashlib.sha256(visibility_bytes).hexdigest(),
                                    "visibility_new": hashlib.sha256(visibility_bytes).hexdigest(),
                                    "contact_old": hashlib.sha256(contact_bytes).hexdigest(),
                                    "contact_new": hashlib.sha256(contact_bytes).hexdigest(),
                                }
                            ),
                        ),
                    )
                    return revision

                revision = await self.store.transaction(update)
                if self._qq_invalidation is not None:
                    self._qq_invalidation("revoked")
                cleanup_callbacks = self._read_revocation_cleanup
                cleanup_error: Exception | None = None
                if cleanup_callbacks is not None:
                    cleanup, clear = cleanup_callbacks

                    def has_read_permission(subject: str, scope: ConversationScope) -> bool:
                        try:
                            self._check_grants(
                                validated,
                                subject,
                                scope,
                                "message.read",
                                "",
                                "",
                                False,
                                False,
                            )
                        except OperationError:
                            return False
                        return True

                    # This is synchronous and only filters Conversation-owned memory.
                    # Run before transport draining so a later failure cannot leave
                    # revoked sources available in the local short-term context.
                    try:
                        cleanup(has_read_permission)
                    except Exception as exc:
                        cleanup_error = exc
                        try:
                            clear()
                        except Exception as clear_exc:
                            cleanup_error = clear_exc
                if not await self.cancel_transports(tuple(self.active_transports)):
                    raise OperationError("transport_cancel_failed")
                if cleanup_error is not None:
                    raise OperationError("observation_cleanup_failed") from cleanup_error
                return revision

        task = asyncio.create_task(commit_and_drain())
        return await drain_on_cancel(task)
