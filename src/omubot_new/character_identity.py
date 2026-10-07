"""Source-bound, teacher-scoped exact image names; no personal memory or model."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass

from .archive import ArchiveService, ArchiveSourceInput, archive_source_id
from .policy import Policy
from .rich_messages import ImageSegment, TextSegment
from .store import Store, StoreConnection, StoreRow
from .types import Event, OperationError, Scope, VisualOwner, VisualSource
from .visual_transport import ImageBytes, VisualTransport

_NAMING = re.compile(r"(?:这张图是|这个角色叫)([^\n]{1,40})[。！!]?")
_SHA = re.compile(r"[a-f0-9]{64}")


def parse_character_teaching(text: str) -> str | None:
    """Only an explicit single-image naming statement, never a question."""
    match = _NAMING.fullmatch(text.strip())
    if match is None:
        return None
    label = match[1].strip().rstrip("。！!").strip('"“”「」')
    if (
        not label
        or any(ch in label for ch in "?？\n，,；;：:")
        or label in {"谁", "什么", "不知道"}
        or label.endswith(("吗", "吧", "呢"))
    ):
        return None
    return label


def _digest(values: object) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CharacterSourceRef:
    source_id: str
    source_revision: int


@dataclass(frozen=True, slots=True)
class ExactCharacterIdentity:
    identity_id: str
    scope: Scope
    teacher_id: str
    image_sha256: str
    label: str
    revision: int
    status: str
    sources: tuple[CharacterSourceRef, ...]


@dataclass(frozen=True, slots=True)
class CharacterTeachingReceipt:
    receipt_id: str
    operation_id: str
    identity_id: str
    identity_revision: int
    action: str
    status: str
    audit_id: int
    policy_revision: int


@dataclass(frozen=True, slots=True)
class ExactCharacterMatch:
    identity: ExactCharacterIdentity
    current_owner: VisualOwner
    teacher_id: str


class CharacterIdentityService:
    def __init__(self, store: Store, policy: Policy) -> None:
        self.store, self.policy = store, policy
        self._visual = VisualTransport()
        self._archive = ArchiveService(store, policy)

    def _current_image(self, event: Event, sources: tuple[VisualSource, ...]) -> tuple[str, VisualOwner]:
        images = [i for i, segment in enumerate(event.rich_segments) if isinstance(segment, ImageSegment)]
        if (
            event.reply_to
            or len(images) != 1
            or len(sources) != 1
            or any(not isinstance(segment, (ImageSegment, TextSegment)) for segment in event.rich_segments)
        ):
            raise OperationError("character_single_current_image_required")
        source = sources[0]
        owner = source.owner
        if (
            source.source_subject != event.user_id
            or owner.scope != event.scope
            or owner.event_id != event.event_id
            or owner.message_id != event.message_id
            or owner.segment_index != images[0]
            or owner.source_kind != "direct"
        ):
            raise OperationError("character_image_owner_mismatch")
        checked = self._visual.validate_bytes(owner, source.data, source.content_type)
        if not isinstance(checked, ImageBytes):
            raise OperationError("character_image_unavailable")
        return hashlib.sha256(checked.data).hexdigest(), owner

    def _permission(self, db: StoreConnection, teacher: str, scope: Scope, *, read: bool = False) -> int:
        for action in ("message.read", "media.read", "memory.archive", "memory.learn"):
            self.policy.check_transaction(db, teacher, scope, action, "", "", False)
        if read:
            self.policy.check_transaction(db, teacher, scope, "memory.retrieve", "", "", False)
        return self.policy.check_transaction(
            db, self.policy.bot_id, scope, "memory.retrieve" if read else "memory.learn", "", "", False
        )

    @staticmethod
    def _source(
        db: StoreConnection,
        ref: CharacterSourceRef,
        teacher: str,
        scope: Scope,
        *,
        event: Event | None = None,
    ) -> None:
        source = db.execute("SELECT * FROM archive_sources WHERE source_id=?", (ref.source_id,)).fetchone()
        tombstone = db.execute(
            "SELECT 1 FROM archive_source_tombstones WHERE source_id=?", (ref.source_id,)
        ).fetchone()
        if tombstone is not None or source is not None and source["status"] != "active":
            raise OperationError("source_revoked")
        if source is None:
            raise OperationError("source_not_found")
        if (
            source["bot_id"] != scope.bot_id
            or source["group_id"] != scope.group_id
            or source["speaker_id"] != teacher
            or source["source_kind"] != "human_message"
            or source["speaker_kind"] != "human"
        ):
            raise OperationError("denied")
        if source["source_revision"] != ref.source_revision:
            raise OperationError("source_revision_conflict")
        if event is not None and (
            source["origin_event_id"] != event.event_id or source["platform_message_id"] != event.message_id
        ):
            raise OperationError("character_teaching_source_mismatch")

    @staticmethod
    def _row(db: StoreConnection, identity_id: str) -> StoreRow:
        row = db.execute("SELECT * FROM character_identities WHERE identity_id=?", (identity_id,)).fetchone()
        if row is None:
            raise OperationError("character_identity_not_found")
        return row

    @staticmethod
    def _identity(db: StoreConnection, row: StoreRow) -> ExactCharacterIdentity:
        sources = db.execute(
            "SELECT source_id,source_revision FROM character_identity_sources "
            "WHERE identity_id=? AND identity_revision=? ORDER BY source_id",
            (row["identity_id"], row["revision"]),
        ).fetchall()
        return ExactCharacterIdentity(
            str(row["identity_id"]),
            Scope(bot_id=row["bot_id"], group_id=row["group_id"]),
            str(row["teacher_id"]),
            str(row["image_sha256"]),
            str(row["label"]),
            int(row["revision"]),
            str(row["status"]),
            tuple(CharacterSourceRef(str(s[0]), int(s[1])) for s in sources),
        )

    @staticmethod
    def _receipt(row: StoreRow) -> CharacterTeachingReceipt:
        return CharacterTeachingReceipt(
            str(row["receipt_id"]),
            str(row["operation_id"]),
            str(row["identity_id"]),
            int(row["identity_revision"]),
            str(row["action"]),
            str(row["status"]),
            int(row["audit_id"]),
            int(row["policy_revision"]),
        )

    def assert_recall_transaction(self, db: StoreConnection, match: ExactCharacterMatch) -> None:
        """Use inside the actual model/send intent transaction, after every await."""
        identity = match.identity
        if (
            identity.teacher_id != match.teacher_id
            or identity.scope != match.current_owner.scope
            or match.current_owner.source_kind != "direct"
        ):
            raise OperationError("denied")
        current = self._identity(db, self._row(db, identity.identity_id))
        if current != identity or current.status != "active":
            raise OperationError("character_identity_stale")
        self._permission(db, match.teacher_id, identity.scope, read=True)
        if not current.sources:
            raise OperationError("character_identity_no_evidence")
        for ref in current.sources:
            self._source(db, ref, match.teacher_id, identity.scope)

    def assert_receipt_transaction(
        self,
        db: StoreConnection,
        receipt: CharacterTeachingReceipt,
        *,
        actor: str,
    ) -> None:
        """A historic write receipt alone cannot justify a current saved-name claim."""
        saved = db.execute(
            "SELECT * FROM character_identity_receipts WHERE receipt_id=?", (receipt.receipt_id,)
        ).fetchone()
        if saved is None or self._receipt(saved) != receipt:
            raise OperationError("character_receipt_mismatch")
        identity = self._identity(db, self._row(db, receipt.identity_id))
        if identity.teacher_id != actor:
            raise OperationError("denied")
        self._permission(db, actor, identity.scope)
        if identity.revision != receipt.identity_revision or identity.status != receipt.status:
            raise OperationError("character_identity_stale")
        if receipt.status == "active":
            for ref in identity.sources:
                self._source(db, ref, actor, identity.scope)

    def _write_receipt(
        self,
        db: StoreConnection,
        *,
        receipt_id: str,
        operation_id: str,
        identity_id: str,
        revision: int,
        digest: str,
        action: str,
        status: str,
        policy_revision: int,
        now: float,
    ) -> CharacterTeachingReceipt:
        cursor = db.execute(
            "INSERT INTO audit(kind,identity,revision,code,details) VALUES ('character_identity',?,?,?,'')",
            (identity_id, revision, action),
        )
        db.execute(
            "INSERT INTO character_identity_receipts VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                receipt_id,
                operation_id,
                identity_id,
                revision,
                digest,
                action,
                status,
                cursor.lastrowid,
                policy_revision,
                now,
            ),
        )
        row = db.execute(
            "SELECT * FROM character_identity_receipts WHERE receipt_id=?", (receipt_id,)
        ).fetchone()
        if row is None:
            raise OperationError("character_receipt_missing")
        return self._receipt(row)

    async def teach(
        self,
        event: Event,
        sources: tuple[VisualSource, ...],
        *,
        actor: str,
        source_id: str,
        expected_source_revision: int,
        expected_revision: int,
    ) -> CharacterTeachingReceipt:
        scope = event.scope
        if not isinstance(scope, Scope):
            raise OperationError("unsupported_scope")
        label = parse_character_teaching(event.text)
        if label is None:
            raise OperationError("character_explicit_naming_required")
        if actor != event.user_id:
            raise OperationError("denied")
        sha, owner = self._current_image(event, sources)
        if type(expected_revision) is not int or expected_revision < 0:
            raise OperationError("character_revision_conflict")
        if type(expected_source_revision) is not int or expected_source_revision < 1:
            raise OperationError("source_revision_conflict")
        identity_id = _digest([scope.bot_id, scope.group_id, actor, sha])
        receipt_id = _digest(["teach", scope.bot_id, scope.group_id, actor, event.event_id])
        digest = _digest(
            [
                identity_id,
                label,
                source_id,
                expected_source_revision,
                expected_revision,
                owner.model_dump(exclude={"turn_id"}),
            ]
        )
        ref = CharacterSourceRef(source_id, expected_source_revision)

        def commit(db: StoreConnection) -> CharacterTeachingReceipt:
            policy_revision = self._permission(db, actor, scope)
            self._source(db, ref, actor, scope, event=event)
            existing = db.execute(
                "SELECT * FROM character_identity_receipts WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != digest:
                    raise OperationError("character_teaching_event_conflict")
                receipt = self._receipt(existing)
                self.assert_receipt_transaction(db, receipt, actor=actor)
                return receipt
            old = db.execute(
                "SELECT * FROM character_identities WHERE identity_id=?", (identity_id,)
            ).fetchone()
            if (0 if old is None else int(old["revision"])) != expected_revision:
                raise OperationError("character_revision_conflict")
            action = (
                "teach"
                if old is None or old["status"] == "revoked"
                else ("reinforce" if old["label"] == label else "rename")
            )
            revision = expected_revision + 1
            now = time.time()
            if old is None:
                db.execute(
                    "INSERT INTO character_identities VALUES (?,?,?,?,?,?,?,'active',?,?)",
                    (
                        identity_id,
                        scope.bot_id,
                        scope.group_id,
                        actor,
                        sha,
                        label,
                        revision,
                        now,
                        now,
                    ),
                )
            else:
                if action == "reinforce":
                    for old_ref in self._identity(db, old).sources:
                        self._source(db, old_ref, actor, scope)
                    db.execute(
                        "INSERT INTO character_identity_sources SELECT identity_id,?,source_id,"
                        "source_revision,"
                        "origin_event_id,platform_message_id,turn_id,segment_index,source_kind "
                        "FROM character_identity_sources WHERE identity_id=? AND identity_revision=?",
                        (revision, identity_id, expected_revision),
                    )
                db.execute(
                    "UPDATE character_identities SET label=?,revision=?,status='active',updated_at=? "
                    "WHERE identity_id=?",
                    (label, revision, now, identity_id),
                )
            db.execute(
                "INSERT INTO character_identity_sources VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identity_id,
                    revision,
                    source_id,
                    expected_source_revision,
                    owner.event_id,
                    owner.message_id,
                    owner.turn_id,
                    owner.segment_index,
                    "direct",
                ),
            )
            return self._write_receipt(
                db,
                receipt_id=receipt_id,
                operation_id=event.event_id,
                identity_id=identity_id,
                revision=revision,
                digest=digest,
                action=action,
                status="active",
                policy_revision=policy_revision,
                now=now,
            )

        return await self.store.transaction(commit)

    async def recall(self, event: Event, sources: tuple[VisualSource, ...]) -> ExactCharacterMatch | None:
        scope = event.scope
        if not isinstance(scope, Scope):
            raise OperationError("unsupported_scope")
        sha, owner = self._current_image(event, sources)
        identity_id = _digest([scope.bot_id, scope.group_id, event.user_id, sha])

        def read(db: StoreConnection) -> ExactCharacterMatch | None:
            row = db.execute(
                "SELECT * FROM character_identities WHERE identity_id=?", (identity_id,)
            ).fetchone()
            if row is None or row["status"] != "active":
                self._permission(db, event.user_id, scope, read=True)
                return None
            match = ExactCharacterMatch(self._identity(db, row), owner, event.user_id)
            self.assert_recall_transaction(db, match)
            return match

        return await self.store.transaction(read)

    async def read_teaching_receipt(self, event: Event) -> CharacterTeachingReceipt | None:
        """Authenticated event replay can read its existing commit without old pixels."""
        receipt_id = _digest(
            ["teach", event.scope.bot_id, event.scope.group_id, event.user_id, event.event_id]
        )

        def read(db: StoreConnection) -> CharacterTeachingReceipt | None:
            row = db.execute(
                "SELECT * FROM character_identity_receipts WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
            if row is None:
                return None
            receipt = self._receipt(row)
            identity = self._row(db, receipt.identity_id)
            if parse_character_teaching(event.text) != identity["label"]:
                raise OperationError("character_teaching_event_conflict")
            provenance = db.execute(
                "SELECT platform_message_id FROM character_identity_sources WHERE identity_id=? "
                "AND identity_revision=? AND origin_event_id=?",
                (receipt.identity_id, receipt.identity_revision, event.event_id),
            ).fetchone()
            if provenance is None or provenance[0] != event.message_id:
                raise OperationError("character_teaching_source_mismatch")
            self.assert_receipt_transaction(db, receipt, actor=event.user_id)
            return receipt

        return await self.store.transaction(read)

    async def teach_current(
        self, event: Event, sources: tuple[VisualSource, ...]
    ) -> CharacterTeachingReceipt:
        """Register only authenticated human source metadata, then CAS exact teaching."""
        scope = event.scope
        if not isinstance(scope, Scope):
            raise OperationError("unsupported_scope")
        source_id = archive_source_id(scope, event.event_id)
        source = await self.store.transaction(
            lambda db: db.execute("SELECT * FROM archive_sources WHERE source_id=?", (source_id,)).fetchone()
        )
        observed_at = float(event.event_time) if event.event_time is not None else time.time()
        if source is not None:
            observed_at = float(source["observed_at"])
        archived = await self._archive.archive_source(
            event.user_id,
            ArchiveSourceInput(
                scope=scope,
                event_id=event.event_id,
                platform_message_id=event.message_id,
                source_kind="human_message",
                speaker_kind="human",
                speaker_id=event.user_id,
                observed_at=observed_at,
                source_revision=1 if source is None else int(source["source_revision"]),
                content_digest=None if source is None else source["content_digest"],
            ),
        )
        sha = hashlib.sha256(sources[0].data).hexdigest()
        identity_id = _digest([scope.bot_id, scope.group_id, event.user_id, sha])
        revision = await self.store.transaction(
            lambda db: db.execute(
                "SELECT revision FROM character_identities WHERE identity_id=?", (identity_id,)
            ).fetchone()
        )
        return await self.teach(
            event,
            sources,
            actor=event.user_id,
            source_id=archived.source_id,
            expected_source_revision=archived.source_revision,
            expected_revision=0 if revision is None else int(revision[0]),
        )

    async def receipt(self, receipt_id: str, *, actor: str) -> CharacterTeachingReceipt:
        """Read a durable receipt without replaying unavailable image bytes."""

        def read(db: StoreConnection) -> CharacterTeachingReceipt:
            row = db.execute(
                "SELECT * FROM character_identity_receipts WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
            if row is None:
                raise OperationError("character_receipt_missing")
            receipt = self._receipt(row)
            self.assert_receipt_transaction(db, receipt, actor=actor)
            return receipt

        return await self.store.transaction(read)

    async def revoke(
        self,
        *,
        scope: Scope,
        teacher_id: str,
        image_sha256: str,
        actor: str,
        expected_revision: int,
        operation_id: str,
    ) -> CharacterTeachingReceipt:
        if actor != teacher_id:
            raise OperationError("denied")
        if not _SHA.fullmatch(image_sha256) or not operation_id or len(operation_id) > 128:
            raise OperationError("invalid_character_revocation")
        identity_id = _digest([scope.bot_id, scope.group_id, teacher_id, image_sha256])
        receipt_id = _digest(["revoke", scope.bot_id, scope.group_id, actor, operation_id])
        digest = _digest([identity_id, expected_revision])

        def commit(db: StoreConnection) -> CharacterTeachingReceipt:
            policy_revision = self._permission(db, actor, scope)
            old_receipt = db.execute(
                "SELECT * FROM character_identity_receipts WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
            if old_receipt is not None:
                if old_receipt["request_digest"] != digest:
                    raise OperationError("character_teaching_event_conflict")
                receipt = self._receipt(old_receipt)
                self.assert_receipt_transaction(db, receipt, actor=actor)
                return receipt
            row = self._row(db, identity_id)
            if type(expected_revision) is not int or row["revision"] != expected_revision:
                raise OperationError("character_revision_conflict")
            revision, now = expected_revision + 1, time.time()
            db.execute(
                "UPDATE character_identities SET status='revoked',revision=?,updated_at=? "
                "WHERE identity_id=?",
                (revision, now, identity_id),
            )
            return self._write_receipt(
                db,
                receipt_id=receipt_id,
                operation_id=operation_id,
                identity_id=identity_id,
                revision=revision,
                digest=digest,
                action="revoke",
                status="revoked",
                policy_revision=policy_revision,
                now=now,
            )

        return await self.store.transaction(commit)
