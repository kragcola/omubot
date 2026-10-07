"""Immutable Journal previews/reviews and explicit source-author public consent.

Fiction uses committed Story evidence. Facts require literal authenticated human
statements bound to the current Archive; raw text never becomes public narrative.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from .archive import ArchiveService
from .policy import Policy
from .store import Store, StoreConnection, StoreRow
from .story import StoryArcStore, StoryEventRecord
from .types import Event, OperationError, Scope

FICTION_PREFIX = "虚构故事里，"
DEFAULT_MAX_CHARS = 280
JournalState = Literal["pending_review", "approved", "rejected"]
JournalListKind = Literal["heads", "consents", "deliveries"]


def journal_list_cursor(scope: Scope, kind: JournalListKind, created_at: float, identity: str) -> str:
    payload = [1, kind, journal_digest(scope.key), created_at, identity]
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def journal_list_after(scope: Scope, kind: JournalListKind, cursor: str | None) -> tuple[float, str] | None:
    """Decode only the closed Journal keyset and bind it to this scope/list."""
    if cursor is None:
        return None
    try:
        if not cursor or len(cursor) > 512:
            raise ValueError
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        decoded: object = json.loads(raw)
        if type(decoded) is not list:
            raise ValueError
        value = cast(list[object], decoded)
        created_at = value[3] if len(value) == 5 else None
        if (len(value) != 5 or type(value[0]) is not int or value[0] != 1
                or value[1] != kind or value[2] != journal_digest(scope.key)
                or isinstance(created_at, bool) or not isinstance(created_at, (int, float))
                or not math.isfinite(created_at)):
            raise ValueError
        prefix = {"heads": "jd_", "consents": "src_", "deliveries": "jpub_"}[kind]
        identity = value[4]
        if (type(identity) is not str or not identity.startswith(prefix)
                or len(identity) != len(prefix) + 64
                or any(char not in "0123456789abcdef" for char in identity[len(prefix):])):
            raise ValueError
        return float(created_at), identity
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        raise OperationError("invalid_journal_cursor") from None


def journal_identity(value: str, code: str) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > 128:
        raise OperationError(code)
    return value


def validate_journal_hash(value: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise OperationError("invalid_journal_hash")
    return value


def journal_digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _body(value: str, max_chars: int) -> str:
    if type(value) is not str:
        raise OperationError("invalid_journal_body")
    text = value.strip()
    wording = text[len(FICTION_PREFIX):].strip() if text.startswith(FICTION_PREFIX) else text
    if not wording:
        raise OperationError("invalid_journal_body")
    framed = FICTION_PREFIX + wording
    if len(framed) > max_chars:
        raise OperationError("invalid_journal_body")
    return framed


def journal_source_hash(event: StoryEventRecord) -> str:
    # The Story owner verifies the full event row before returning this immutable
    # record; its payload and decision digests bind the committed event contents.
    return journal_digest({
        "bot": event.bot_id, "group": event.group_id, "event": event.event_id,
        "arc": event.arc_id, "source_kind": event.source_kind, "author": event.author,
        "event_type": event.event_type, "origin_kind": event.origin_kind,
        "payload": event.payload_digest, "decision": event.decision_digest,
        "from_revision": event.from_revision, "to_revision": event.to_revision,
        "committed_at": event.committed_at,
    })


@dataclass(frozen=True, slots=True)
class JournalHead:
    draft_id: str
    root_id: str
    revision: int
    source_event_id: str
    source_hash: str
    body_hash: str
    content_hash: str
    state: JournalState
    created_at: float


@dataclass(frozen=True, slots=True)
class JournalHeadPage:
    items: tuple[JournalHead, ...]
    has_more: bool
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class JournalPublicConsent:
    source_id: str
    template_id: str
    labels: tuple[str, ...]
    label_index: int
    expires_at: float
    current: bool
    code: str


@dataclass(frozen=True, slots=True)
class JournalPublicConsentPage:
    items: tuple[JournalPublicConsent, ...]
    has_more: bool
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class JournalReview:
    actor: str
    decision: Literal["approve", "reject"]
    body_hash: str
    source_hash: str
    content_hash: str
    approval_scope: Literal["dry_run", "live"]
    created_at: float


@dataclass(frozen=True, slots=True)
class JournalDraft:
    draft_id: str
    scope: Scope
    root_id: str
    revision: int
    supersedes_draft_id: str | None
    source_event_id: str
    source_hash: str
    body: str
    body_hash: str
    content_hash: str
    state: JournalState
    is_tip: bool
    review: JournalReview | None
    created_at: float
    content_kind: Literal["fiction", "factual"] = "fiction"


@dataclass(frozen=True, slots=True)
class JournalDryRun:
    draft_id: str
    revision: int
    body: str
    body_hash: str
    source_hash: str
    content_hash: str
    scope_digest: str
    mode: Literal["dry_run"] = "dry_run"


class JournalOwner:
    """The sole mutable owner of local Journal drafts and exact-content reviews."""

    def __init__(
        self,
        store: Store,
        policy: Policy,
        story: StoryArcStore | None,
        *,
        max_chars: int = DEFAULT_MAX_CHARS,
        clock: Callable[[], float] = time.time,
        archive: ArchiveService | None = None,
        public_destination: str = "journal-text",
        max_drafts_per_day: int = 1,
        fiction_groups: tuple[str, ...] | None = None,
    ) -> None:
        if policy.store is not store or story is not None and story.store is not store:
            raise OperationError("journal_store_mismatch")
        if type(max_chars) is not int or max_chars < 1:
            raise OperationError("invalid_journal_max_chars")
        self.store, self.policy, self.story = store, policy, story
        self.fiction_groups = fiction_groups
        if archive is not None and (archive.store is not store or archive.policy is not policy):
            raise OperationError("journal_store_mismatch")
        self.archive, self.public_destination = (
            archive,
            journal_identity(public_destination, "invalid_journal_destination"),
        )
        self.live_gate: Callable[[Scope], None] | None = None
        if type(max_drafts_per_day) is not int or not 1 <= max_drafts_per_day <= 3:
            raise OperationError("invalid_journal_budget")
        self.max_drafts_per_day = max_drafts_per_day
        self.max_chars = max_chars
        self.clock = clock

    def authorize_transaction(self, db: StoreConnection, actor: str, scope: Scope) -> int:
        return self.policy.check_transaction(db, actor, scope, "journal.manage", "", "", False)

    @staticmethod
    def validate_caller(actor: str, scope: Scope) -> str:
        actor = journal_identity(actor, "invalid_journal_actor")
        if type(scope) is not Scope:
            raise OperationError("invalid_journal_scope")
        return actor

    def source_hash_transaction(self, db: StoreConnection, scope: Scope, event_id: str) -> str:
        if event_id.startswith("jp_"):
            return self.public_source_transaction(db, scope, event_id)[0]
        if (self.story is None
                or self.fiction_groups is not None and scope.group_id not in self.fiction_groups):
            raise OperationError("journal_source_unavailable")
        event = self.story.read_event_transaction(db, scope, event_id)
        if event is None:
            raise OperationError("journal_source_unavailable")
        if event.origin_kind == "social_experience":
            raise OperationError("journal_factual_source_deferred")
        return journal_source_hash(event)

    def draft_transaction(self, db: StoreConnection, scope: Scope, draft_id: str) -> JournalDraft:
        row = db.execute(
            "SELECT * FROM journal_drafts WHERE draft_id=? AND bot_id=? AND group_id=?",
            (draft_id, scope.bot_id, scope.group_id),
        ).fetchone()
        if row is None:
            raise OperationError("journal_draft_not_found")
        source_hash = self.source_hash_transaction(db, scope, str(row["source_event_id"]))
        if source_hash != row["source_hash"]:
            raise OperationError("journal_source_changed")
        review_row = db.execute("SELECT * FROM journal_reviews WHERE draft_id=?", (draft_id,)).fetchone()
        review = (
            None
            if review_row is None
            else JournalReview(
                actor=str(review_row["actor"]),
                decision=cast(Literal["approve", "reject"], review_row["decision"]),
                body_hash=str(review_row["body_hash"]),
                source_hash=str(review_row["source_hash"]),
                content_hash=str(review_row["content_hash"]),
                approval_scope=cast(Literal["dry_run", "live"], review_row["approval_scope"]),
                created_at=float(review_row["created_at"]),
            )
        )
        tip = (
            db.execute("SELECT 1 FROM journal_drafts WHERE supersedes_draft_id=?", (draft_id,)).fetchone()
            is None
        )
        return JournalDraft(
            draft_id=draft_id,
            scope=scope,
            root_id=str(row["root_id"]),
            revision=int(row["revision"]),
            supersedes_draft_id=row["supersedes_draft_id"],
            source_event_id=str(row["source_event_id"]),
            source_hash=source_hash,
            body=str(row["body"]),
            body_hash=str(row["body_hash"]),
            content_hash=str(row["content_hash"]),
            state=cast(JournalState, row["state"]),
            is_tip=tip,
            review=review,
            created_at=float(row["created_at"]),
            content_kind=cast(
                Literal["fiction", "factual"],
                row["content_kind"],
            ),
        )

    @staticmethod
    def assert_expected(draft: JournalDraft, body_hash: str, source_hash: str) -> None:
        if (draft.body_hash, draft.source_hash) != (body_hash, source_hash):
            raise OperationError("journal_revision_conflict")

    def _replay(
        self, db: StoreConnection, scope: Scope, operation_id: str, digest: str,
    ) -> JournalDraft | None:
        row = db.execute(
            "SELECT digest,result_draft_id FROM journal_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        if row["digest"] != digest:
            raise OperationError("journal_operation_conflict")
        return self.draft_transaction(db, scope, str(row["result_draft_id"]))

    def _record(
        self, db: StoreConnection, *, actor: str, scope: Scope, operation_id: str,
        operation: str, digest: str, draft_id: str, policy_revision: int,
    ) -> JournalDraft:
        db.execute(
            "INSERT INTO journal_operations(operation_id,bot_id,group_id,actor,operation,digest,"
            "result_draft_id,policy_revision,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (operation_id, scope.bot_id, scope.group_id, actor, operation, digest,
             draft_id, policy_revision, self.clock()),
        )
        return self.draft_transaction(db, scope, draft_id)

    async def create(
        self,
        *,
        actor: str,
        scope: Scope,
        source_event_id: str,
        body: str,
        operation_id: str,
        composition_operation_id: str | None = None,
    ) -> JournalDraft:
        actor = self.validate_caller(actor, scope)
        source_event_id = journal_identity(source_event_id, "invalid_journal_source")
        operation_id = journal_identity(operation_id, "invalid_journal_operation")
        body = self._requested_body(source_event_id, body)
        digest = journal_digest(["create", actor, scope.key, source_event_id, body])

        def create(db: StoreConnection) -> JournalDraft:
            policy_revision = self.authorize_transaction(db, actor, scope)
            replay = self._replay(db, scope, operation_id, digest)
            if replay is not None:
                return replay
            source_hash = self.source_hash_transaction(db, scope, source_event_id)
            kind = "factual" if source_event_id.startswith("jp_") else "fiction"
            if kind == "factual" and body != self.public_source_transaction(db, scope, source_event_id)[1]:
                raise OperationError("journal_factual_body_changed")
            if (
                db.execute(
                    "SELECT 1 FROM journal_drafts WHERE bot_id=? AND group_id=? AND source_event_id=?",
                    (scope.bot_id, scope.group_id, source_event_id),
                ).fetchone()
                is not None
            ):
                raise OperationError("journal_event_already_drafted")
            if composition_operation_id is not None:
                claim = db.execute(
                    "SELECT source_event_id,source_hash,state FROM journal_compositions "
                    "WHERE operation_id=? AND bot_id=? AND group_id=?",
                    (composition_operation_id, scope.bot_id, scope.group_id),
                ).fetchone()
                if claim is None or (claim["source_event_id"], claim["source_hash"], claim["state"]) != (
                    source_event_id,
                    source_hash,
                    "composing",
                ):
                    raise OperationError("journal_composition_conflict")
            if (
                self.day_draft_count_transaction(
                    db,
                    scope,
                    self.source_date_transaction(db, scope, source_event_id),
                    exclude_composition=composition_operation_id,
                )
                >= self.max_drafts_per_day
            ):
                raise OperationError("journal_day_draft_budget")
            root_id = "jr_" + journal_digest([scope.key, source_event_id])
            draft_id = "jd_" + digest
            body_hash = hashlib.sha256(body.encode()).hexdigest()
            content_hash = journal_digest(
                [scope.key, kind, root_id, 1, source_event_id, source_hash, body_hash]
            )
            db.execute(
                "INSERT INTO journal_drafts(draft_id,bot_id,group_id,root_id,revision,"
                "supersedes_draft_id,source_event_id,source_hash,body,body_hash,"
                "content_hash,state,created_at,content_kind) "
                "VALUES (?,?,?,?,1,NULL,?,?,?,?,?,'pending_review',?,?)",
                (
                    draft_id,
                    scope.bot_id,
                    scope.group_id,
                    root_id,
                    source_event_id,
                    source_hash,
                    body,
                    body_hash,
                    content_hash,
                    self.clock(),
                    kind,
                ),
            )
            result = self._record(
                db,
                actor=actor,
                scope=scope,
                operation_id=operation_id,
                operation="create",
                digest=digest,
                draft_id=draft_id,
                policy_revision=policy_revision,
            )
            if composition_operation_id is not None:
                db.execute(
                    "UPDATE journal_compositions SET state='complete' WHERE operation_id=?",
                    (composition_operation_id,),
                )
            return result

        return await self.store.transaction(create)

    async def read(self, *, actor: str, scope: Scope, draft_id: str) -> JournalDraft:
        actor = self.validate_caller(actor, scope)
        draft_id = journal_identity(draft_id, "invalid_journal_draft")

        def read(db: StoreConnection) -> JournalDraft:
            self.authorize_transaction(db, actor, scope)
            return self.draft_transaction(db, scope, draft_id)

        return await self.store.transaction(read)

    async def list_heads(
        self, *, actor: str, scope: Scope, limit: int = 64, cursor: str | None = None,
    ) -> JournalHeadPage:
        """Read bounded tip metadata; source bodies still require the full read gate."""
        actor = self.validate_caller(actor, scope)
        if type(limit) is not int or not 1 <= limit <= 64:
            raise OperationError("invalid_journal_limit")
        after = journal_list_after(scope, "heads", cursor)

        def read(db: StoreConnection) -> JournalHeadPage:
            self.authorize_transaction(db, actor, scope)
            if after is not None and db.execute(
                "SELECT 1 FROM journal_drafts WHERE bot_id=? AND group_id=? AND created_at=? AND draft_id=?",
                (scope.bot_id, scope.group_id, *after),
            ).fetchone() is None:
                raise OperationError("invalid_journal_cursor")
            rows = db.execute(
                "SELECT draft_id,root_id,revision,source_event_id,source_hash,body_hash,"
                "content_hash,state,created_at FROM journal_drafts AS d "
                "WHERE bot_id=? AND group_id=? AND NOT EXISTS "
                "(SELECT 1 FROM journal_drafts AS child WHERE child.supersedes_draft_id=d.draft_id) "
                + ("AND (created_at<? OR (created_at=? AND draft_id<?)) " if after else "")
                + "ORDER BY created_at DESC,draft_id DESC LIMIT ?",
                (scope.bot_id, scope.group_id, *((after[0], after[0], after[1]) if after else ()), limit + 1),
            ).fetchall()
            return JournalHeadPage(items=tuple(JournalHead(
                draft_id=str(row["draft_id"]), root_id=str(row["root_id"]),
                revision=int(row["revision"]), source_event_id=str(row["source_event_id"]),
                source_hash=str(row["source_hash"]), body_hash=str(row["body_hash"]),
                content_hash=str(row["content_hash"]), state=cast(JournalState, row["state"]),
                created_at=float(row["created_at"]),
            ) for row in rows[:limit]), has_more=len(rows) > limit,
                next_cursor=(journal_list_cursor(scope, "heads", float(rows[limit - 1]["created_at"]),
                                                 str(rows[limit - 1]["draft_id"]))
                             if len(rows) > limit else None))

        return await self.store.transaction(read)

    async def revise(
        self,
        *,
        actor: str,
        scope: Scope,
        draft_id: str,
        body: str,
        expected_body_hash: str,
        expected_source_hash: str,
        operation_id: str,
    ) -> JournalDraft:
        actor = self.validate_caller(actor, scope)
        draft_id = journal_identity(draft_id, "invalid_journal_draft")
        operation_id = journal_identity(operation_id, "invalid_journal_operation")
        if type(body) is not str or not body.strip() or len(body) > self.max_chars:
            raise OperationError("invalid_journal_body")
        expected_body_hash, expected_source_hash = (
            validate_journal_hash(expected_body_hash),
            validate_journal_hash(expected_source_hash),
        )

        def revise(db: StoreConnection) -> JournalDraft:
            policy_revision = self.authorize_transaction(db, actor, scope)
            parent = self.draft_transaction(db, scope, draft_id)
            revised_body = self._requested_body(parent.source_event_id, body)
            digest = journal_digest(
                ["revise", actor, scope.key, draft_id, revised_body, expected_body_hash, expected_source_hash]
            )
            replay = self._replay(db, scope, operation_id, digest)
            if replay is not None:
                return replay
            if parent.content_kind == "factual" and revised_body != parent.body:
                raise OperationError("journal_factual_body_changed")
            self.assert_expected(parent, expected_body_hash, expected_source_hash)
            if not parent.is_tip or parent.state not in {"pending_review", "rejected"}:
                raise OperationError("journal_invalid_transition")
            if (
                parent.state == "rejected"
                and self.day_draft_count_transaction(
                    db, scope, self.source_date_transaction(db, scope, parent.source_event_id)
                )
                >= self.max_drafts_per_day
            ):
                raise OperationError("journal_day_draft_budget")
            child_id = "jd_" + digest
            body_hash = hashlib.sha256(revised_body.encode()).hexdigest()
            content_hash = journal_digest(
                [
                    scope.key,
                    parent.content_kind,
                    parent.root_id,
                    parent.revision + 1,
                    parent.source_event_id,
                    parent.source_hash,
                    body_hash,
                ]
            )
            db.execute(
                "INSERT INTO journal_drafts(draft_id,bot_id,group_id,root_id,revision,"
                "supersedes_draft_id,source_event_id,source_hash,body,body_hash,"
                "content_hash,state,created_at,content_kind) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending_review',?,?)",
                (
                    child_id,
                    scope.bot_id,
                    scope.group_id,
                    parent.root_id,
                    parent.revision + 1,
                    parent.draft_id,
                    parent.source_event_id,
                    parent.source_hash,
                    revised_body,
                    body_hash,
                    content_hash,
                    self.clock(),
                    parent.content_kind,
                ),
            )
            return self._record(
                db,
                actor=actor,
                scope=scope,
                operation_id=operation_id,
                operation="revise",
                digest=digest,
                draft_id=child_id,
                policy_revision=policy_revision,
            )

        return await self.store.transaction(revise)

    async def _review(
        self,
        *,
        decision: Literal["approve", "reject"],
        actor: str,
        scope: Scope,
        draft_id: str,
        expected_body_hash: str,
        expected_source_hash: str,
        operation_id: str,
        approval_scope: Literal["dry_run", "live"] = "dry_run",
    ) -> JournalDraft:
        actor = self.validate_caller(actor, scope)
        draft_id = journal_identity(draft_id, "invalid_journal_draft")
        operation_id = journal_identity(operation_id, "invalid_journal_operation")
        expected_body_hash, expected_source_hash = (
            validate_journal_hash(expected_body_hash),
            validate_journal_hash(expected_source_hash),
        )
        if approval_scope not in {"dry_run", "live"}:
            raise OperationError("journal_approval_scope_invalid")
        values: list[object] = [
            decision,
            actor,
            scope.key,
            draft_id,
            expected_body_hash,
            expected_source_hash,
        ]
        if approval_scope == "live":
            values.append("live")
        digest = journal_digest(values)

        def review(db: StoreConnection) -> JournalDraft:
            if approval_scope == "live":
                if self.live_gate is None:
                    raise OperationError("journal_live_gate_closed")
                self.live_gate(scope)
            policy_revision = self.authorize_transaction(db, actor, scope)
            replay = self._replay(db, scope, operation_id, digest)
            if replay is not None:
                if not replay.is_tip:
                    raise OperationError("journal_invalid_transition")
                return replay
            draft = self.draft_transaction(db, scope, draft_id)
            self.assert_expected(draft, expected_body_hash, expected_source_hash)
            if not draft.is_tip or draft.state != "pending_review":
                raise OperationError("journal_invalid_transition")
            db.execute(
                "INSERT INTO journal_reviews(draft_id,actor,decision,body_hash,source_hash,"
                "content_hash,approval_scope,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    draft_id,
                    actor,
                    decision,
                    draft.body_hash,
                    draft.source_hash,
                    draft.content_hash,
                    approval_scope,
                    self.clock(),
                ),
            )
            db.execute(
                "UPDATE journal_drafts SET state=? WHERE draft_id=?",
                ("approved" if decision == "approve" else "rejected", draft_id),
            )
            return self._record(
                db,
                actor=actor,
                scope=scope,
                operation_id=operation_id,
                operation=decision,
                digest=digest,
                draft_id=draft_id,
                policy_revision=policy_revision,
            )

        return await self.store.transaction(review)

    async def approve(
        self,
        *,
        actor: str,
        scope: Scope,
        draft_id: str,
        expected_body_hash: str,
        expected_source_hash: str,
        operation_id: str,
        approval_scope: Literal["dry_run", "live"] = "dry_run",
    ) -> JournalDraft:
        return await self._review(
            decision="approve",
            actor=actor,
            scope=scope,
            draft_id=draft_id,
            approval_scope=approval_scope,
            expected_body_hash=expected_body_hash,
            expected_source_hash=expected_source_hash,
            operation_id=operation_id,
        )

    async def reject(
        self, *, actor: str, scope: Scope, draft_id: str, expected_body_hash: str,
        expected_source_hash: str, operation_id: str,
    ) -> JournalDraft:
        return await self._review(
            decision="reject", actor=actor, scope=scope, draft_id=draft_id,
            expected_body_hash=expected_body_hash, expected_source_hash=expected_source_hash,
            operation_id=operation_id,
        )

    async def dry_run(
        self, *, actor: str, scope: Scope, draft_id: str,
        expected_body_hash: str, expected_source_hash: str,
    ) -> JournalDryRun:
        actor = self.validate_caller(actor, scope)
        draft_id = journal_identity(draft_id, "invalid_journal_draft")
        expected_body_hash, expected_source_hash = (
            validate_journal_hash(expected_body_hash), validate_journal_hash(expected_source_hash),
        )

        def describe(db: StoreConnection) -> JournalDryRun:
            self.authorize_transaction(db, actor, scope)
            draft = self.draft_transaction(db, scope, draft_id)
            self.assert_expected(draft, expected_body_hash, expected_source_hash)
            if not draft.is_tip or draft.state != "approved" or draft.review is None:
                raise OperationError("journal_invalid_transition")
            if (draft.review.decision, draft.review.body_hash, draft.review.source_hash,
                draft.review.content_hash) != (
                "approve", draft.body_hash, draft.source_hash, draft.content_hash,
            ):
                raise OperationError("journal_review_conflict")
            return JournalDryRun(
                draft_id=draft.draft_id, revision=draft.revision, body=draft.body,
                body_hash=draft.body_hash, source_hash=draft.source_hash,
                content_hash=draft.content_hash,
                scope_digest=journal_digest(scope.key),
            )

        return await self.store.transaction(describe)

    def _requested_body(self, source_event_id: str, body: str) -> str:
        if not source_event_id.startswith("jp_"):
            return _body(body, self.max_chars)
        if type(body) is not str or not body or body != body.strip() or len(body) > self.max_chars:
            raise OperationError("invalid_journal_body")
        return body

    def _consent_current(self, db: StoreConnection, scope: Scope, source_id: str) -> StoreRow:
        if self.archive is None:
            raise OperationError("journal_archive_unavailable")
        row = db.execute(
            "SELECT * FROM journal_public_consents WHERE source_id=? AND bot_id=? AND group_id=?",
            (source_id, scope.bot_id, scope.group_id),
        ).fetchone()
        if row is None:
            raise OperationError("journal_public_consent_unavailable")
        if float(row["expires_at"]) <= self.clock():
            raise OperationError("journal_source_expired")
        self.archive.assert_human_source_transaction(
            db,
            actor=str(row["author"]),
            scope=scope,
            source_id=source_id,
            expected_revision=int(row["source_revision"]),
            subject_id=str(row["author"]),
            event_id=str(row["event_id"]),
        )
        self.policy.check_transaction(
            db,
            str(row["author"]),
            scope,
            "tool.invoke:http.post",
            "qzone",
            self.public_destination,
            True,
            False,
        )
        return row

    async def list_public_consents(
        self, *, actor: str, scope: Scope, limit: int = 64, cursor: str | None = None,
    ) -> JournalPublicConsentPage:
        """Authorized bounded refs, closed labels and current eligibility; no raw author/text."""
        actor = self.validate_caller(actor, scope)
        if type(limit) is not int or not 1 <= limit <= 64:
            raise OperationError("invalid_journal_limit")
        after = journal_list_after(scope, "consents", cursor)

        def read(db: StoreConnection) -> JournalPublicConsentPage:
            self.authorize_transaction(db, actor, scope)
            if after is not None and db.execute(
                "SELECT 1 FROM journal_public_consents "
                "WHERE bot_id=? AND group_id=? AND created_at=? AND source_id=?",
                (scope.bot_id, scope.group_id, *after),
            ).fetchone() is None:
                raise OperationError("invalid_journal_cursor")
            rows = db.execute(
                "SELECT source_id,template_id,labels_json,label_index,expires_at,created_at "
                "FROM journal_public_consents "
                "WHERE bot_id=? AND group_id=? "
                + ("AND (created_at<? OR (created_at=? AND source_id>?)) " if after else "")
                + "ORDER BY created_at DESC,source_id LIMIT ?",
                (scope.bot_id, scope.group_id, *((after[0], after[0], after[1]) if after else ()), limit + 1),
            ).fetchall()
            items: list[JournalPublicConsent] = []
            for row in rows[:limit]:
                code = ""
                try:
                    self._consent_current(db, scope, str(row["source_id"]))
                except OperationError as exc:
                    if exc.code not in {
                        "denied", "source_revoked", "archive_source_changed", "archive_source_expired",
                        "archive_source_not_found", "archive_source_unavailable", "journal_source_expired",
                        "journal_archive_unavailable",
                    }:
                        raise
                    code = "journal_source_unavailable"
                items.append(JournalPublicConsent(
                    str(row["source_id"]), str(row["template_id"]), tuple(json.loads(row["labels_json"])),
                    int(row["label_index"]), float(row["expires_at"]), not code, code,
                ))
            return JournalPublicConsentPage(
                tuple(items), len(rows) > limit,
                (journal_list_cursor(scope, "consents", float(rows[limit - 1]["created_at"]),
                                     str(rows[limit - 1]["source_id"])) if len(rows) > limit else None),
            )

        return await self.store.transaction(read)

    async def register_public_consent(
        self, event: Event, *, source_id: str, expected_source_revision: int
    ) -> str:
        """Ingress-only caller after Archive.capture_text; Web cannot mint author consent.

        Root supplies the parser-owned Event and the actual returned Archive revision.
        A command is a literal human assertion of a closed claim, not model learning.
        No personal relationship/profile or arbitrary narrative is projected.
        """
        from .journal_compose import public_statement
        from .rich_messages import TextSegment

        if self.archive is None:
            raise OperationError("journal_archive_unavailable")
        if type(event) is not Event or type(event.scope) is not Scope or not event.message_id:
            raise OperationError("journal_public_event_invalid")
        if not event.rich_segments or any(
            type(segment) is not TextSegment for segment in event.rich_segments
        ):
            raise OperationError("journal_public_event_invalid")
        text = "".join(segment.text for segment in event.rich_segments if type(segment) is TextSegment)
        if text != event.text:
            raise OperationError("journal_public_event_invalid")
        scope = event.scope
        observed_at = event.event_time
        statement = public_statement(text)
        if observed_at is None or not self.clock() - 86400 <= observed_at <= self.clock():
            raise OperationError("journal_source_expired")
        archived = await self.archive.read_local_text(event.user_id, source_id, event.scope)
        if archived != text:
            raise OperationError("journal_source_text_mismatch")
        text_hash = hashlib.sha256(text.encode()).hexdigest()

        assert observed_at is not None

        def register(db: StoreConnection) -> str:
            assert self.archive is not None
            self.archive.assert_human_source_transaction(
                db,
                actor=event.user_id,
                scope=scope,
                source_id=source_id,
                expected_revision=expected_source_revision,
                subject_id=event.user_id,
                event_id=event.event_id,
            )
            self.policy.check_transaction(
                db,
                event.user_id,
                event.scope,
                "tool.invoke:http.post",
                "qzone",
                self.public_destination,
                True,
                False,
            )
            source = db.execute(
                "SELECT platform_message_id,text_expires_at FROM archive_sources WHERE source_id=?",
                (source_id,),
            ).fetchone()
            assert source is not None
            if source["platform_message_id"] != event.message_id or source["text_expires_at"] is None:
                raise OperationError("journal_source_changed")
            expiry = min(float(source["text_expires_at"]), observed_at + 86400)
            if expiry <= self.clock():
                raise OperationError("journal_source_expired")
            existing = db.execute(
                "SELECT * FROM journal_public_consents WHERE source_id=?", (source_id,)
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["text_hash"]),
                    int(existing["source_revision"]),
                    str(existing["author"]),
                ) != (
                    text_hash,
                    expected_source_revision,
                    event.user_id,
                ):
                    raise OperationError("journal_public_consent_conflict")
                self._consent_current(db, scope, source_id)
                return source_id
            db.execute(
                "INSERT INTO journal_public_consents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    event.scope.bot_id,
                    event.scope.group_id,
                    expected_source_revision,
                    event.user_id,
                    event.event_id,
                    event.message_id,
                    text_hash,
                    statement.template_id,
                    json.dumps(statement.labels, ensure_ascii=False),
                    statement.label_index,
                    expiry,
                    self.clock(),
                ),
            )
            db.execute(
                "INSERT INTO audit(kind,identity,revision,code) VALUES ('journal_consent',?,?,'registered')",
                (source_id, expected_source_revision),
            )
            return source_id

        return await self.store.transaction(register)

    def public_source_transaction(
        self, db: StoreConnection, scope: Scope, source_event_id: str
    ) -> tuple[str, str]:
        from .journal_compose import render_public

        row = db.execute(
            "SELECT * FROM journal_public_sources WHERE source_event_id=? AND bot_id=? AND group_id=?",
            (source_event_id, scope.bot_id, scope.group_id),
        ).fetchone()
        if row is None or float(row["expires_at"]) <= self.clock():
            raise OperationError("journal_source_expired")
        labels = tuple(json.loads(str(row["labels_json"])))
        consent_ids = tuple(json.loads(str(row["consent_ids_json"])))
        template = str(row["template_id"])
        body = render_public(template, labels)
        bindings: list[object] = []
        authors: set[str] = set()
        if len(consent_ids) != len(labels):
            raise OperationError("journal_public_projection_invalid")
        for index, source_id in enumerate(consent_ids):
            consent = self._consent_current(db, scope, source_id)
            if (
                str(consent["template_id"]) != template
                or str(consent["labels_json"]) != row["labels_json"]
                or int(consent["label_index"]) != index
                or str(consent["author"]) in authors
            ):
                raise OperationError("journal_public_projection_invalid")
            authors.add(str(consent["author"]))
            bindings.append(
                [
                    source_id,
                    int(consent["source_revision"]),
                    str(consent["author"]),
                    str(consent["text_hash"]),
                    index,
                    labels[index],
                    float(consent["expires_at"]),
                ]
            )
        current = journal_digest([scope.key, template, labels, bindings])
        if current != row["source_hash"]:
            raise OperationError("journal_source_changed")
        return current, body

    async def preview_factual(
        self, *, actor: str, scope: Scope, consent_source_ids: tuple[str, ...], operation_id: str
    ) -> JournalDraft:
        """Admin selects existing current consent refs; cannot edit facts/labels/authors."""
        from .journal_compose import render_public

        actor = self.validate_caller(actor, scope)
        operation_id = journal_identity(operation_id, "invalid_journal_operation")
        if (
            type(consent_source_ids) is not tuple
            or not 1 <= len(consent_source_ids) <= 2
            or len(set(consent_source_ids)) != len(consent_source_ids)
        ):
            raise OperationError("journal_public_projection_invalid")

        def freeze(db: StoreConnection) -> tuple[str, str]:
            self.authorize_transaction(db, actor, scope)
            consents = [self._consent_current(db, scope, source) for source in consent_source_ids]
            consents.sort(key=lambda row: int(row["label_index"]))
            template = str(consents[0]["template_id"])
            labels = tuple(json.loads(str(consents[0]["labels_json"])))
            body = render_public(template, labels)
            if len(consents) != len(labels):
                raise OperationError("journal_public_projection_invalid")
            ids = tuple(str(row["source_id"]) for row in consents)
            bindings = [
                [
                    row["source_id"],
                    int(row["source_revision"]),
                    row["author"],
                    row["text_hash"],
                    int(row["label_index"]),
                    labels[index],
                    float(row["expires_at"]),
                ]
                for index, row in enumerate(consents)
            ]
            source_hash = journal_digest([scope.key, template, labels, bindings])
            source_event_id = "jp_" + journal_digest([scope.key, ids])
            existing = db.execute(
                "SELECT source_hash FROM journal_public_sources WHERE source_event_id=?", (source_event_id,)
            ).fetchone()
            if existing is None:
                db.execute(
                    "INSERT INTO journal_public_sources VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        source_event_id,
                        scope.bot_id,
                        scope.group_id,
                        source_hash,
                        template,
                        json.dumps(labels, ensure_ascii=False),
                        json.dumps(ids),
                        min(float(row["expires_at"]) for row in consents),
                        self.clock(),
                    ),
                )
            elif existing["source_hash"] != source_hash:
                raise OperationError("journal_source_changed")
            self.public_source_transaction(db, scope, source_event_id)
            return source_event_id, body

        source_event_id, body = await self.store.transaction(freeze)
        return await self.create(
            actor=actor, scope=scope, source_event_id=source_event_id, body=body, operation_id=operation_id
        )

    def source_date_transaction(self, db: StoreConnection, scope: Scope, source_event_id: str) -> str:
        if source_event_id.startswith("jp_"):
            row = db.execute(
                "SELECT created_at FROM journal_public_sources WHERE source_event_id=?", (source_event_id,)
            ).fetchone()
            assert row is not None
            stamp = float(row["created_at"])
        else:
            if self.story is None:
                raise OperationError("journal_source_unavailable")
            event = self.story.read_event_transaction(db, scope, source_event_id)
            if event is None:
                raise OperationError("journal_source_unavailable")
            stamp = event.committed_at
        return datetime.fromtimestamp(stamp, UTC).date().isoformat()

    def day_draft_count_transaction(
        self, db: StoreConnection, scope: Scope, event_date: str, *, exclude_composition: str | None = None
    ) -> int:
        occupied = db.execute(
            "SELECT COUNT(*) FROM journal_drafts d LEFT JOIN story_events e "
            "ON e.bot_id=d.bot_id AND e.event_id=d.source_event_id "
            "LEFT JOIN journal_public_sources p ON p.source_event_id=d.source_event_id "
            "WHERE d.bot_id=? AND d.group_id=? AND d.state!='rejected' "
            "AND NOT EXISTS (SELECT 1 FROM journal_drafts c WHERE c.supersedes_draft_id=d.draft_id) "
            "AND NOT EXISTS (SELECT 1 FROM journal_deliveries j WHERE j.root_id=d.root_id "
            "AND j.mode='live' AND j.state='failed') "
            "AND date(COALESCE(e.committed_at,p.created_at),'unixepoch')=?",
            (scope.bot_id, scope.group_id, event_date),
        ).fetchone()[0]
        reserved = db.execute(
            "SELECT COUNT(*) FROM journal_compositions WHERE bot_id=? AND group_id=? "
            "AND event_date=? AND state='composing' AND (? IS NULL OR operation_id!=?)",
            (scope.bot_id, scope.group_id, event_date, exclude_composition, exclude_composition),
        ).fetchone()[0]
        return int(occupied) + int(reserved)
