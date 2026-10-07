"""Identity-only group source archive and bounded backfill primitives.

Archive source rows remain body-free. An optional, separately encrypted spool
can hold explicitly authorized human text for a short retention window.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, cast

from .archive_spool import EncryptedTextSpool, EncryptedTextSpoolError
from .policy import Policy
from .store import Store, StoreConnection
from .types import OperationError, Scope

ArchiveSourceKind = Literal[
    "human_message",
    "bot_reply",
    "visual_observation",
    "document",
    "admin_feedback",
    "fiction",
    "imported_snapshot",
]
ArchiveSpeakerKind = Literal["human", "bot", "system", "importer", "fiction"]

_SOURCE_KINDS = frozenset(
    {
        "human_message",
        "bot_reply",
        "visual_observation",
        "document",
        "admin_feedback",
        "fiction",
        "imported_snapshot",
    }
)
_SPEAKER_KINDS = frozenset({"human", "bot", "system", "importer", "fiction"})
_MAX_PAGE_ITEMS = 256
_MAX_BACKFILL_RUNNER_PAGES = 128
_MAX_IDENTIFIER = 128
_MAX_SCANNER = 64
_MAX_REASON = 256
_SOURCE_STATUSES = frozenset(
    {"absent", "active", "revoked", "deleted", "expired", "quarantined"}
)


def _identity(value: object, *, limit: int = _MAX_IDENTIFIER, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise OperationError("invalid_archive_identity")
    if not value and allow_empty:
        return value
    if (
        not value
        or value != value.strip()
        or len(value) > limit
        or any(ord(char) < 32 for char in value)
        or "://" in value
        or "/" in value
        or "\\" in value
    ):
        raise OperationError("invalid_archive_identity")
    return value


def _source_identity(value: object) -> str:
    identity = _identity(value, limit=68)
    if len(identity) != 68 or not identity.startswith("src_"):
        raise OperationError("invalid_archive_source_id")
    if any(char not in "0123456789abcdef" for char in identity[4:]):
        raise OperationError("invalid_archive_source_id")
    return identity


def _reason(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > _MAX_REASON:
        raise OperationError("invalid_archive_reason")
    if any(ord(char) < 32 for char in value):
        raise OperationError("invalid_archive_reason")
    return value


def _timestamp(value: object, code: str = "invalid_archive_timestamp") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OperationError(code)
    number = float(value)
    if not math.isfinite(number):
        raise OperationError(code)
    return number


def _digest(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise OperationError("invalid_content_digest")
    raise OperationError("content_digest_not_available")


@dataclass(frozen=True, slots=True)
class ArchiveSourceInput:
    """A source envelope with no reversible body field."""

    scope: Scope
    event_id: str
    source_kind: ArchiveSourceKind
    speaker_kind: ArchiveSpeakerKind
    observed_at: float
    platform_message_id: str = ""
    speaker_id: str = ""
    source_revision: int = 1
    visibility_grant_id: str = ""
    content_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.scope) is not Scope:
            raise OperationError("invalid_archive_scope")
        _identity(self.event_id)
        _identity(self.platform_message_id, allow_empty=True)
        _identity(self.speaker_id, allow_empty=True)
        _identity(self.visibility_grant_id, allow_empty=True)
        if self.visibility_grant_id:
            raise OperationError("visibility_grant_not_available")
        if self.source_kind not in _SOURCE_KINDS or self.speaker_kind not in _SPEAKER_KINDS:
            raise OperationError("invalid_archive_source")
        if self.source_kind == "human_message":
            if self.speaker_kind != "human" or not self.speaker_id:
                raise OperationError("invalid_archive_source")
        if type(self.source_revision) is not int or not 1 <= self.source_revision <= 1_000_000:
            raise OperationError("invalid_archive_source")
        _timestamp(self.observed_at)
        _digest(self.content_digest)


@dataclass(frozen=True, slots=True)
class ArchiveSourceRecord:
    source_id: str
    scope: Scope
    event_id: str
    source_kind: ArchiveSourceKind
    speaker_kind: ArchiveSpeakerKind
    speaker_id: str
    platform_message_id: str
    observed_at: float
    ingested_at: float
    content_digest: str | None
    body_omitted: bool
    source_revision: int
    status: str
    visibility_grant_id: str
    text_expires_at: float | None = None


@dataclass(frozen=True, slots=True)
class ArchiveExtractionBatch:
    sources: tuple[ArchiveSourceRecord, ...]
    last_scanned_source_id: str | None


@dataclass(frozen=True, slots=True)
class ArchiveHumanSourcePointer:
    source_id: str
    source_revision: int
    scope: Scope
    subject_id: str
    event_id: str


@dataclass(frozen=True, slots=True)
class ArchiveSourceStatus:
    """Scoped source state, including an absent state distinct from revoked."""

    source_id: str
    scope: Scope
    status: Literal["absent", "active", "revoked", "deleted", "expired", "quarantined"]
    source_revision: int | None
    reason: str
    revoked_at: float | None
    actor: str
    source: ArchiveSourceRecord | None = None


@dataclass(frozen=True, slots=True)
class BackfillRequest:
    subject: str
    run_id: str
    scanner: str
    scope: Scope
    scanner_version: str
    params_hash: str
    from_marker: str
    to_marker: str

    def __post_init__(self) -> None:
        _identity(self.subject, limit=64)
        _identity(self.run_id)
        _identity(self.scanner, limit=_MAX_SCANNER)
        if type(self.scope) is not Scope:
            raise OperationError("invalid_archive_scope")
        _identity(self.scanner_version, limit=64)
        _identity(self.params_hash, limit=128)
        _identity(self.from_marker, allow_empty=True)
        _identity(self.to_marker)


@dataclass(frozen=True, slots=True)
class ArchiveBackfillItem:
    marker: str
    source: ArchiveSourceInput

    def __post_init__(self) -> None:
        _identity(self.marker)


@dataclass(frozen=True, slots=True)
class ArchiveBackfillPage:
    """One bounded page from an explicitly supplied source provider.

    ``high_water_confirmed`` is an affirmative provider result that the fixed
    requested high-water mark was fully scanned. An empty result alone is not
    proof that the scan reached that mark.
    """

    items: tuple[ArchiveBackfillItem, ...]
    high_water_confirmed: bool = False

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or any(
            type(item) is not ArchiveBackfillItem for item in self.items
        ):
            raise OperationError("invalid_archive_page")
        if type(self.high_water_confirmed) is not bool:
            raise OperationError("invalid_archive_page")


class ArchiveSourceProvider(Protocol):
    """Caller-owned, pre-authorized source reader; Archive opens no source DB."""

    async def fetch_page(
        self,
        *,
        scope: Scope,
        after_marker: str,
        through_marker: str,
        limit: int,
    ) -> ArchiveBackfillPage: ...


@dataclass(frozen=True, slots=True)
class BackfillCommit:
    run_id: str
    status: str
    cursor_marker: str
    scanned_count: int
    emitted_count: int
    committed_count: int


@dataclass(frozen=True, slots=True)
class ArchiveCursorRecord:
    scanner: str
    scope: Scope
    scanner_version: str
    params_hash: str
    last_committed_marker: str
    last_observed_at: float | None
    status: str
    updated_at: float


@dataclass(frozen=True, slots=True)
class _ArchiveTextSnapshot:
    source_id: str
    scope: Scope
    event_id: str
    source_kind: str
    speaker_kind: str
    speaker_id: str
    platform_message_id: str
    observed_at: float
    source_revision: int


def _source_id(scope: Scope, event_id: str) -> str:
    identity = "\0".join((scope.bot_id, scope.group_id, event_id)).encode("utf-8")
    return "src_" + hashlib.sha256(identity).hexdigest()


def archive_source_id(scope: Scope, event_id: str) -> str:
    """Return the stable identity for one exact source envelope."""

    if type(scope) is not Scope:
        raise OperationError("invalid_archive_scope")
    _identity(event_id)
    return _source_id(scope, event_id)


def _record(values: dict[str, object]) -> ArchiveSourceRecord:
    scope = Scope(bot_id=str(values["bot_id"]), group_id=str(values["group_id"]))
    source_kind = values["source_kind"]
    speaker_kind = values["speaker_kind"]
    if source_kind not in _SOURCE_KINDS or speaker_kind not in _SPEAKER_KINDS:
        raise OperationError("invalid_archive_source")
    digest = values["content_digest"]
    if digest is not None and not isinstance(digest, str):
        raise OperationError("invalid_content_digest")
    return ArchiveSourceRecord(
        source_id=str(values["source_id"]),
        scope=scope,
        event_id=str(values["origin_event_id"]),
        source_kind=cast(ArchiveSourceKind, source_kind),
        speaker_kind=cast(ArchiveSpeakerKind, speaker_kind),
        speaker_id=str(values["speaker_id"]),
        platform_message_id=str(values["platform_message_id"]),
        observed_at=cast(float, values["observed_at"]),
        ingested_at=cast(float, values["ingested_at"]),
        content_digest=digest,
        body_omitted=bool(values["body_omitted"]),
        source_revision=cast(int, values["source_revision"]),
        status=str(values["status"]),
        visibility_grant_id=str(values["visibility_grant_id"]),
        text_expires_at=(
            None
            if values.get("text_expires_at") is None
            else cast(float, values["text_expires_at"])
        ),
    )


def _status(
    values: Mapping[str, object | None], source_id: str, scope: Scope
) -> ArchiveSourceStatus:
    raw_source = values.get("source")
    raw_tombstone = values.get("tombstone")
    record = None if raw_source is None else _record(cast(dict[str, object], raw_source))
    if record is not None:
        if record.status not in _SOURCE_STATUSES - {"absent"}:
            raise OperationError("invalid_archive_source")
        if raw_tombstone is not None:
            tombstone = cast(dict[str, object], raw_tombstone)
            tombstone_revision = cast(int, tombstone["source_revision"])
            if record.source_revision != tombstone_revision:
                raise OperationError("invalid_archive_source")
            if record.status not in {"active", "revoked"}:
                raise OperationError("invalid_archive_source")
            return ArchiveSourceStatus(
                source_id=source_id,
                scope=scope,
                status="revoked",
                source_revision=record.source_revision,
                reason=str(tombstone["reason"]),
                revoked_at=float(cast(float, tombstone["revoked_at"])),
                actor=str(tombstone["actor"]),
                source=record if record.status == "revoked" else replace(record, status="revoked"),
            )
        if record.status == "revoked":
            raise OperationError("invalid_archive_source")
        return ArchiveSourceStatus(
            source_id=source_id,
            scope=scope,
            status=cast(Literal["active", "deleted", "expired", "quarantined"], record.status),
            source_revision=record.source_revision,
            reason="",
            revoked_at=None,
            actor="",
            source=record,
        )
    if raw_tombstone is not None:
        tombstone = cast(dict[str, object], raw_tombstone)
        return ArchiveSourceStatus(
            source_id=source_id,
            scope=scope,
            status="revoked",
            source_revision=cast(int, tombstone["source_revision"]),
            reason=str(tombstone["reason"]),
            revoked_at=float(cast(float, tombstone["revoked_at"])),
            actor=str(tombstone["actor"]),
            source=None,
        )
    return ArchiveSourceStatus(
        source_id=source_id,
        scope=scope,
        status="absent",
        source_revision=None,
        reason="",
        revoked_at=None,
        actor="",
        source=None,
    )


def _commit(values: dict[str, object]) -> BackfillCommit:
    return BackfillCommit(
        run_id=str(values["run_id"]),
        status=str(values["status"]),
        cursor_marker=str(values["last_committed_marker"])
        if "last_committed_marker" in values
        else str(values["cursor_marker"]),
        scanned_count=cast(int, values["scanned_count"]),
        emitted_count=cast(int, values["emitted_count"]),
        committed_count=cast(int, values["committed_count"]),
    )


def _cursor(values: dict[str, object]) -> ArchiveCursorRecord:
    return ArchiveCursorRecord(
        scanner=str(values["scanner"]),
        scope=Scope(bot_id=str(values["bot_id"]), group_id=str(values["group_id"])),
        scanner_version=str(values["scanner_version"]),
        params_hash=str(values["params_hash"]),
        last_committed_marker=str(values["last_committed_marker"]),
        last_observed_at=cast(float | None, values["last_observed_at"]),
        status=str(values["status"]),
        updated_at=cast(float, values["updated_at"]),
    )


class ArchiveService:
    """The only N6 caller that may submit source metadata to Store."""

    def __init__(
        self,
        store: Store,
        policy: Policy,
        *,
        clock: Callable[[], float] = time.time,
        text_spool: EncryptedTextSpool | None = None,
    ) -> None:
        self.store = store
        self.policy = policy
        self._clock = clock
        self.text_spool = text_spool

    def _now(self) -> float:
        return _timestamp(self._clock(), "invalid_archive_clock")

    def _authorize(
        self,
        db: Any,
        subject: str,
        scope: Scope,
        sources: Sequence[ArchiveSourceInput],
    ) -> None:
        _identity(subject, limit=64)
        read_subjects: set[str] = set()
        archive_subjects: set[str] = set()
        for source in sources:
            if source.source_kind != "human_message":
                raise OperationError("source_kind_forbidden")
            reader = source.speaker_id or subject
            if reader not in read_subjects:
                self.policy.check_transaction(
                    db, reader, source.scope, "message.read", "", "", False, False
                )
                read_subjects.add(reader)
            if reader not in archive_subjects:
                self.policy.check_transaction(
                    db, reader, source.scope, "memory.archive", "", "", False, False
                )
                archive_subjects.add(reader)
        if subject not in archive_subjects:
            self.policy.check_transaction(
                db, subject, scope, "memory.archive", "", "", False, False
            )

    @staticmethod
    def _stored_source(source: ArchiveSourceInput, ingested_at: float) -> dict[str, object]:
        return {
            "source_id": _source_id(source.scope, source.event_id),
            "bot_id": source.scope.bot_id,
            "group_id": source.scope.group_id,
            "origin_event_id": source.event_id,
            "platform_message_id": source.platform_message_id,
            "source_kind": source.source_kind,
            "speaker_kind": source.speaker_kind,
            "speaker_id": source.speaker_id,
            "observed_at": source.observed_at,
            "ingested_at": ingested_at,
            "content_digest": source.content_digest,
            "body_omitted": True,
            "source_revision": source.source_revision,
            "visibility_grant_id": source.visibility_grant_id,
            "text_expires_at": None,
        }

    async def archive_source(
        self, subject: str, source: ArchiveSourceInput
    ) -> ArchiveSourceRecord:
        ingested_at = self._now()
        stored = self._stored_source(source, ingested_at)

        def authorize(db: Any) -> None:
            self._authorize(db, subject, source.scope, (source,))

        values = await self.store.archive_source_commit(source=stored, authorize=authorize)
        return _record(values)

    @staticmethod
    def _model_destination(value: object) -> str:
        if (
            type(value) is not str
            or not value
            or value != value.strip()
            or len(value) > 128
            or any(ord(char) < 32 for char in value)
        ):
            raise OperationError("invalid_archive_model_destination")
        return value

    def _text_consent(
        self,
        db: Any,
        actor: str,
        scope: Scope,
        author: str,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        _identity(actor, limit=64)
        _identity(author, limit=64)
        if type(scope) is not Scope or scope.bot_id != self.policy.bot_id:
            raise OperationError("denied")
        for action in ("message.read", "memory.archive", "memory.learn"):
            self.policy.check_transaction(db, author, scope, action, "", "", False, False)
        for action in ("message.read", "memory.archive", "memory.learn"):
            self.policy.check_transaction(db, actor, scope, action, "", "", False, False)
        if provider is not None and model is not None:
            for subject in dict.fromkeys((author, actor)):
                self.policy.check_transaction(
                    db, subject, scope, "model.invoke", provider, model, True, False
                )

    @staticmethod
    def _text_values_match(row: Any, values: Mapping[str, object]) -> bool:
        return all(
            row[column] == values[column]
            for column in (
                "source_id",
                "bot_id",
                "group_id",
                "origin_event_id",
                "platform_message_id",
                "source_kind",
                "speaker_kind",
                "speaker_id",
                "observed_at",
                "content_digest",
                "body_omitted",
                "source_revision",
                "visibility_grant_id",
            )
        )

    def _capture_text_authorize(
        self,
        db: Any,
        actor: str,
        source: ArchiveSourceInput,
        values: Mapping[str, object],
    ) -> None:
        self._text_consent(db, actor, source.scope, source.speaker_id)
        source_id = cast(str, values["source_id"])
        tombstone = db.execute(
            "SELECT bot_id,group_id FROM archive_source_tombstones WHERE source_id=?",
            (source_id,),
        ).fetchone()
        if tombstone is not None:
            if tombstone["bot_id"] != source.scope.bot_id or tombstone["group_id"] != source.scope.group_id:
                raise OperationError("denied")
            raise OperationError("source_revoked")
        row = db.execute(
            "SELECT * FROM archive_sources WHERE source_id=?", (source_id,)
        ).fetchone()
        if row is None:
            return
        if row["bot_id"] != source.scope.bot_id or row["group_id"] != source.scope.group_id:
            raise OperationError("denied")
        if row["status"] != "active":
            raise OperationError("source_revoked")
        if not self._text_values_match(row, values):
            raise OperationError("archive_source_conflict")

    def _active_text_snapshot(
        self,
        db: Any,
        actor: str,
        source_id: str,
        scope: Scope,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> _ArchiveTextSnapshot:
        _identity(actor, limit=64)
        _source_identity(source_id)
        if type(scope) is not Scope or scope.bot_id != self.policy.bot_id:
            raise OperationError("denied")
        row = db.execute(
            "SELECT * FROM archive_sources WHERE source_id=?", (source_id,)
        ).fetchone()
        tombstone = db.execute(
            "SELECT bot_id,group_id FROM archive_source_tombstones WHERE source_id=?",
            (source_id,),
        ).fetchone()
        for candidate in (row, tombstone):
            if candidate is not None and (
                candidate["bot_id"] != scope.bot_id or candidate["group_id"] != scope.group_id
            ):
                raise OperationError("denied")
        if row is None:
            if tombstone is not None:
                raise OperationError("source_revoked")
            raise OperationError("archive_source_not_found")
        if tombstone is not None or row["status"] == "revoked":
            raise OperationError("source_revoked")
        if row["status"] != "active":
            raise OperationError("archive_source_unavailable")
        if row["source_kind"] != "human_message" or row["speaker_kind"] != "human":
            raise OperationError("source_kind_forbidden")
        author = _identity(str(row["speaker_id"]), limit=64)
        self._text_consent(
            db, actor, scope, author, provider=provider, model=model
        )
        return _ArchiveTextSnapshot(
            source_id=source_id,
            scope=scope,
            event_id=str(row["origin_event_id"]),
            source_kind=str(row["source_kind"]),
            speaker_kind=str(row["speaker_kind"]),
            speaker_id=author,
            platform_message_id=str(row["platform_message_id"]),
            observed_at=_timestamp(row["observed_at"]),
            source_revision=cast(int, row["source_revision"]),
        )

    def _spool(self) -> EncryptedTextSpool:
        if self.text_spool is None:
            raise OperationError("archive_text_spool_unavailable")
        return self.text_spool

    def _spool_read(self, source_id: str) -> str | None:
        try:
            return self._spool().read(source_id)
        except EncryptedTextSpoolError as exc:
            raise OperationError("archive_text_spool_unavailable") from exc

    def assert_human_source_transaction(
        self, db: StoreConnection, *, actor: str, scope: Scope, source_id: str,
        expected_revision: int, subject_id: str, event_id: str,
    ) -> ArchiveHumanSourcePointer:
        """Body-free exact human source gate shared by derived state owners."""
        snapshot = self._active_text_snapshot(db, actor, source_id, scope)
        if (type(expected_revision) is not int or expected_revision < 1
                or snapshot.source_revision != expected_revision
                or snapshot.speaker_id != subject_id or snapshot.event_id != event_id
                or subject_id == self.policy.bot_id):
            raise OperationError("archive_source_changed")
        row = db.execute("SELECT text_expires_at FROM archive_sources WHERE source_id=?",
                         (source_id,)).fetchone()
        assert row is not None
        if row["text_expires_at"] is not None and float(row["text_expires_at"]) <= self._now():
            raise OperationError("archive_source_expired")
        return ArchiveHumanSourcePointer(source_id, expected_revision, scope, subject_id, event_id)

    def assert_text_send_transaction(
        self,
        db: Any,
        subject: str,
        source_id: str,
        scope: Scope,
        *,
        provider: str,
        model: str,
        expected_revision: int,
    ) -> None:
        """Recheck a source inside the model intent's Store transaction."""
        snapshot = self._active_text_snapshot(
            db,
            subject,
            source_id,
            scope,
            provider=self._model_destination(provider),
            model=self._model_destination(model),
        )
        if snapshot.source_revision != expected_revision:
            raise OperationError("archive_source_changed")

    async def pending_extraction_sources(
        self,
        subject: str,
        *,
        provider: str,
        model: str,
        limit: int = 32,
        after_source_id: str | None = None,
    ) -> ArchiveExtractionBatch:
        """Recover a bounded, still-authorized batch of encrypted sources."""
        provider = self._model_destination(provider)
        model = self._model_destination(model)
        try:
            ids = self._spool().pending_source_ids(
                limit=limit, after_source_id=after_source_id
            )
        except EncryptedTextSpoolError as exc:
            raise OperationError("archive_text_spool_unavailable") from exc

        def inspect(db: Any) -> tuple[list[ArchiveSourceRecord], list[str]]:
            ready: list[ArchiveSourceRecord] = []
            cleanup: list[str] = []
            for source_id in ids:
                row = db.execute(
                    "SELECT * FROM archive_sources WHERE source_id=?", (source_id,)
                ).fetchone()
                if row is None or row["bot_id"] != self.policy.bot_id:
                    cleanup.append(source_id)
                    continue
                scope = Scope(bot_id=str(row["bot_id"]), group_id=str(row["group_id"]))
                try:
                    # Public Journal consent is an explicit local-only purpose.
                    # Check its original author's current source consent and TTL
                    # before reading; the prefix grants no permission or retention.
                    self.assert_human_source_transaction(
                        db, actor=str(row["speaker_id"]), scope=scope, source_id=source_id,
                        expected_revision=int(row["source_revision"]),
                        subject_id=str(row["speaker_id"]), event_id=str(row["origin_event_id"]),
                    )
                    local_text = self._spool_read(source_id)
                    # None can be an existing sealed extraction decision.
                    if local_text is not None and local_text.startswith("公开日志同意："):
                        continue
                    self._active_text_snapshot(
                        db, subject, source_id, scope, provider=provider, model=model
                    )
                except OperationError as exc:
                    if exc.code not in {
                        "denied", "source_revoked", "archive_source_unavailable", "archive_source_expired",
                    }:
                        raise
                    cleanup.append(source_id)
                    continue
                ready.append(_record(dict(row)))
            return ready, cleanup

        ready, cleanup = await self.store.transaction(inspect)
        for source_id in cleanup:
            try:
                self._spool().delete(source_id)
            except EncryptedTextSpoolError as exc:
                raise OperationError("archive_text_cleanup_failed") from exc
        return ArchiveExtractionBatch(tuple(ready), ids[-1] if ids else None)

    async def capture_text(
        self, subject: str, source: ArchiveSourceInput, text: str
    ) -> ArchiveSourceRecord:
        """Capture one explicitly authorized human message in the encrypted spool."""
        if type(source) is not ArchiveSourceInput:
            raise OperationError("invalid_archive_source")
        if source.source_kind != "human_message" or source.speaker_kind != "human":
            raise OperationError("source_kind_forbidden")
        _identity(subject, limit=64)
        if type(text) is not str:
            raise OperationError("invalid_archive_text")
        spool = self._spool()
        stored = self._stored_source(source, self._now())

        def inspect(db: Any) -> bool:
            self._capture_text_authorize(db, subject, source, stored)
            row = db.execute(
                "SELECT * FROM archive_sources WHERE source_id=?", (stored["source_id"],)
            ).fetchone()
            return row is not None

        exists = await self.store.transaction(inspect)
        existing_text = self._spool_read(cast(str, stored["source_id"]))
        created = False
        if exists:
            if existing_text is None:
                raise OperationError("archive_text_unavailable")
            if existing_text != text:
                raise OperationError("archive_text_conflict")
        elif existing_text is not None:
            if existing_text != text:
                raise OperationError("archive_text_conflict")
            text_expires_at = spool.expiry(cast(str, stored["source_id"]))
            if text_expires_at is None:
                raise OperationError("archive_text_spool_unavailable")
            stored["text_expires_at"] = text_expires_at
        else:
            try:
                stored["text_expires_at"] = spool.put(cast(str, stored["source_id"]), text)
                created = True
            except EncryptedTextSpoolError as exc:
                raced_text = self._spool_read(cast(str, stored["source_id"]))
                if raced_text != text:
                    if raced_text is None:
                        raise OperationError("archive_text_spool_unavailable") from exc
                    raise OperationError("archive_text_conflict") from exc
                text_expires_at = spool.expiry(cast(str, stored["source_id"]))
                if text_expires_at is None:
                    raise OperationError("archive_text_spool_unavailable") from exc
                stored["text_expires_at"] = text_expires_at

        def authorize(db: Any) -> None:
            self._capture_text_authorize(db, subject, source, stored)

        try:
            values = await self.store.archive_source_commit(
                source=stored, authorize=authorize
            )
            record = _record(values)
            if record.status != "active":
                raise OperationError("source_revoked")
            return record
        except BaseException:
            if created:
                try:
                    spool.delete(cast(str, stored["source_id"]))
                except EncryptedTextSpoolError as cleanup_error:
                    raise OperationError("archive_text_cleanup_failed") from cleanup_error
            raise

    async def read_local_text(self, subject: str, source_id: str, scope: Scope) -> str | None:
        """Read transient command evidence locally, without granting model send permission.

        Uses the same active source snapshot and original spool expiry as model
        reads; both author and caller need current read/archive/learn consent.
        """
        spool = self._spool()

        def authorize(db: Any) -> _ArchiveTextSnapshot:
            return self._active_text_snapshot(db, subject, source_id, scope)

        before = await self.store.transaction(authorize)
        try:
            text = spool.read(source_id)
        except EncryptedTextSpoolError as exc:
            raise OperationError("archive_text_spool_unavailable") from exc
        after = await self.store.transaction(authorize)
        if before != after:
            raise OperationError("archive_source_changed")
        return text

    async def read_text(
        self,
        subject: str,
        source_id: str,
        scope: Scope,
        *,
        provider: str,
        model: str,
    ) -> str | None:
        """Read text only while author, actor, model destination, and source remain authorized."""
        provider = self._model_destination(provider)
        model = self._model_destination(model)
        spool = self._spool()

        def authorize(db: Any) -> _ArchiveTextSnapshot:
            return self._active_text_snapshot(
                db, subject, source_id, scope, provider=provider, model=model
            )

        before = await self.store.transaction(authorize)
        try:
            text = spool.read(source_id)
        except EncryptedTextSpoolError as exc:
            raise OperationError("archive_text_spool_unavailable") from exc
        after = await self.store.transaction(authorize)
        if before != after:
            raise OperationError("archive_source_changed")
        return text

    async def read_extraction_decision(
        self,
        subject: str,
        source_id: str,
        scope: Scope,
        *,
        provider: str,
        model: str,
    ) -> str | None:
        """Recover one encrypted extraction decision under current source policy."""
        provider = self._model_destination(provider)
        model = self._model_destination(model)

        def authorize(db: Any) -> _ArchiveTextSnapshot:
            return self._active_text_snapshot(
                db, subject, source_id, scope, provider=provider, model=model
            )

        before = await self.store.transaction(authorize)
        try:
            decision = self._spool().read_decision(source_id)
        except EncryptedTextSpoolError as exc:
            raise OperationError("archive_text_spool_unavailable") from exc
        after = await self.store.transaction(authorize)
        if before != after:
            raise OperationError("archive_source_changed")
        return decision

    async def seal_extraction_decision(
        self,
        subject: str,
        source_id: str,
        scope: Scope,
        decision: str,
        *,
        provider: str,
        model: str,
    ) -> None:
        """Replace raw text with an encrypted decision at its original deadline."""
        provider = self._model_destination(provider)
        model = self._model_destination(model)

        def authorize(db: Any) -> _ArchiveTextSnapshot:
            return self._active_text_snapshot(
                db, subject, source_id, scope, provider=provider, model=model
            )

        before = await self.store.transaction(authorize)
        try:
            self._spool().seal_decision(source_id, decision)
        except EncryptedTextSpoolError as exc:
            raise OperationError("archive_text_spool_unavailable") from exc
        try:
            after = await self.store.transaction(authorize)
            if before != after:
                raise OperationError("archive_source_changed")
        except BaseException:
            try:
                self._spool().delete(source_id)
            except EncryptedTextSpoolError as exc:
                raise OperationError("archive_text_cleanup_failed") from exc
            raise

    async def delete_text(self, subject: str, source_id: str, scope: Scope) -> bool:
        """Delete one authorized source body; a failed filesystem delete is reported."""
        spool = self._spool()
        await self.store.transaction(
            lambda db: self._active_text_snapshot(db, subject, source_id, scope)
        )
        try:
            return spool.delete(source_id)
        except EncryptedTextSpoolError as exc:
            raise OperationError("archive_text_delete_failed") from exc

    async def delete_exhausted_raw_text(
        self,
        subject: str,
        source_id: str,
        scope: Scope,
        *,
        expected_revision: int,
    ) -> bool:
        """Delete authorized raw text at the expected revision, never a decision."""
        spool = self._spool()

        def authorize(db: Any) -> None:
            snapshot = self._active_text_snapshot(db, subject, source_id, scope)
            if snapshot.source_revision != expected_revision:
                raise OperationError("archive_source_changed")

        await self.store.transaction(authorize)
        try:
            return spool.delete_raw_text(source_id)
        except EncryptedTextSpoolError as exc:
            raise OperationError("archive_text_delete_failed") from exc

    def _check_policy_configure(self, db: Any, subject: str, scope: Scope) -> None:
        self.policy.check_transaction(
            db, subject, scope, "policy.configure", "", "", False, False
        )

    def _revoke_authorize(
        self,
        db: Any,
        subject: str,
        scope: Scope,
        source_id: str,
    ) -> None:
        _identity(subject, limit=64)
        if scope.bot_id != self.policy.bot_id:
            raise OperationError("denied")
        source_row = db.execute(
            "SELECT bot_id,group_id,speaker_id FROM archive_sources WHERE source_id=?",
            (source_id,),
        ).fetchone()
        tombstone_row = db.execute(
            "SELECT bot_id,group_id,speaker_id FROM archive_source_tombstones WHERE source_id=?",
            (source_id,),
        ).fetchone()
        for row in (source_row, tombstone_row):
            if row is not None and (
                row["bot_id"] != scope.bot_id or row["group_id"] != scope.group_id
            ):
                raise OperationError("denied")
        recorded_speaker = ""
        for row in (source_row, tombstone_row):
            if row is not None and row["speaker_id"]:
                recorded_speaker = str(row["speaker_id"])
                break
        # A source supplied only by an ingress caller is not proof of authorship.
        # The author bypass starts once the Store has a durable speaker binding.
        if recorded_speaker and recorded_speaker == subject:
            return
        self._check_policy_configure(db, subject, scope)

    async def revoke_source(
        self,
        subject: str,
        source_id: str,
        scope: Scope,
        reason: str,
        *,
        expected_revision: int | None = None,
    ) -> ArchiveSourceStatus:
        """Revoke one exact source, retaining a durable identity-only tombstone."""

        _identity(subject, limit=64)
        _source_identity(source_id)
        if type(scope) is not Scope:
            raise OperationError("invalid_archive_scope")
        _reason(reason)
        if type(expected_revision) not in {int, type(None)} or (
            expected_revision is not None and not 0 <= expected_revision <= 1_000_000
        ):
            raise OperationError("invalid_archive_source_revision")
        if scope.bot_id != self.policy.bot_id:
            raise OperationError("denied")
        now = self._now()

        def authorize(db: Any) -> None:
            self._revoke_authorize(db, subject, scope, source_id)

        values = await self.store.archive_source_revoke(
            source_id=source_id,
            bot_id=scope.bot_id,
            group_id=scope.group_id,
            actor=subject,
            reason=reason,
            now=now,
            expected_revision=expected_revision,
            authorize=authorize,
        )
        status = _status(values, source_id, scope)
        if self.text_spool is not None:
            try:
                self.text_spool.delete(source_id)
            except EncryptedTextSpoolError as exc:
                # The tombstone is already durable, so reads are denied. An
                # idempotent revoke retry can finish this filesystem cleanup.
                raise OperationError("archive_text_delete_failed") from exc
        return status

    def _status_authorize(self, db: Any, subject: str, scope: Scope, source_id: str) -> None:
        _identity(subject, limit=64)
        if scope.bot_id != self.policy.bot_id:
            raise OperationError("denied")
        source_row = db.execute(
            "SELECT bot_id,group_id,speaker_id FROM archive_sources WHERE source_id=?",
            (source_id,),
        ).fetchone()
        tombstone_row = db.execute(
            "SELECT bot_id,group_id,speaker_id FROM archive_source_tombstones WHERE source_id=?",
            (source_id,),
        ).fetchone()
        for row in (source_row, tombstone_row):
            if row is not None and (
                row["bot_id"] != scope.bot_id or row["group_id"] != scope.group_id
            ):
                raise OperationError("denied")
        recorded_speaker = ""
        for row in (source_row, tombstone_row):
            if row is not None and row["speaker_id"]:
                recorded_speaker = str(row["speaker_id"])
                break
        if recorded_speaker and recorded_speaker == subject:
            return
        try:
            self.policy.check_transaction(
                db, subject, scope, "memory.archive", "", "", False, False
            )
        except OperationError:
            self._check_policy_configure(db, subject, scope)

    async def source_status(
        self, subject: str, source_id: str, scope: Scope
    ) -> ArchiveSourceStatus:
        """Read one scoped source state without exposing body or digest data."""

        _identity(subject, limit=64)
        _source_identity(source_id)
        if type(scope) is not Scope:
            raise OperationError("invalid_archive_scope")
        if scope.bot_id != self.policy.bot_id:
            raise OperationError("denied")

        def authorize(db: Any) -> None:
            self._status_authorize(db, subject, scope, source_id)

        values = await self.store.archive_source_status_read(
            source_id=source_id,
            bot_id=scope.bot_id,
            group_id=scope.group_id,
            authorize=authorize,
        )
        return _status(values, source_id, scope)

    async def commit_page(
        self,
        request: BackfillRequest,
        items: Sequence[ArchiveBackfillItem],
        *,
        next_marker: str,
        high_water_confirmed: bool = False,
    ) -> BackfillCommit:
        return await self._commit_page(
            request,
            items,
            next_marker=next_marker,
            high_water_confirmed=high_water_confirmed,
            expected_cursor=None,
        )

    async def begin_rescan(
        self,
        request: BackfillRequest,
        expected_cursor: ArchiveCursorRecord,
    ) -> BackfillCommit:
        """Authorize and CAS one fixed-range rescan against its old cursor."""
        if (
            type(expected_cursor) is not ArchiveCursorRecord
            or expected_cursor.status != "needs_rescan"
            or expected_cursor.scanner != request.scanner
            or expected_cursor.scope != request.scope
        ):
            raise OperationError("archive_cursor_conflict")
        now = self._now()
        run = {
            "run_id": request.run_id,
            "scanner": request.scanner,
            "bot_id": request.scope.bot_id,
            "group_id": request.scope.group_id,
            "scanner_version": request.scanner_version,
            "params_hash": request.params_hash,
            "run_kind": "rescan",
            "from_marker": request.from_marker,
            "to_marker": request.to_marker,
        }
        expected = {
            "scanner_version": expected_cursor.scanner_version,
            "params_hash": expected_cursor.params_hash,
            "last_committed_marker": expected_cursor.last_committed_marker,
            "status": expected_cursor.status,
        }

        def authorize(db: Any) -> None:
            self.policy.check_transaction(
                db, request.subject, request.scope, "memory.archive", "", "", False, False
            )

        values = await self.store.archive_rescan_begin(
            run=run,
            expected_cursor=expected,
            now=now,
            authorize=authorize,
        )
        return _commit(values)

    async def commit_rescan_page(
        self,
        request: BackfillRequest,
        expected_cursor: ArchiveCursorRecord,
        items: Sequence[ArchiveBackfillItem],
        *,
        next_marker: str,
        high_water_confirmed: bool = False,
    ) -> BackfillCommit:
        return await self._commit_page(
            request,
            items,
            next_marker=next_marker,
            high_water_confirmed=high_water_confirmed,
            expected_cursor=expected_cursor,
        )

    async def _commit_page(
        self,
        request: BackfillRequest,
        items: Sequence[ArchiveBackfillItem],
        *,
        next_marker: str,
        high_water_confirmed: bool,
        expected_cursor: ArchiveCursorRecord | None,
    ) -> BackfillCommit:
        """Commit a scanner page; empty completion requires a Store-checked proof."""
        if len(items) > _MAX_PAGE_ITEMS:
            raise OperationError("archive_page_limit")
        _identity(next_marker)
        if type(high_water_confirmed) is not bool:
            raise OperationError("invalid_archive_page")
        normalized = tuple(items)
        markers = tuple(item.marker for item in normalized)
        if len(set(markers)) != len(markers):
            raise OperationError("archive_page_duplicate_marker")
        if normalized and next_marker != markers[-1]:
            raise OperationError("archive_marker_conflict")
        sources = tuple(item.source for item in normalized)
        if any(source.scope != request.scope for source in sources):
            raise OperationError("archive_scope_mismatch")
        if any(source.source_kind != "human_message" for source in sources):
            raise OperationError("source_kind_forbidden")
        ingested_at = self._now()
        stored_sources = tuple(self._stored_source(source, ingested_at) for source in sources)
        digest_material = (
            "\0".join((*markers, *(str(row["source_id"]) for row in stored_sources)))
            if normalized
            else f"empty\0{request.to_marker}"
        )
        page_digest = hashlib.sha256(digest_material.encode("utf-8")).hexdigest()
        run = {
            "run_id": request.run_id,
            "scanner": request.scanner,
            "bot_id": request.scope.bot_id,
            "group_id": request.scope.group_id,
            "scanner_version": request.scanner_version,
            "params_hash": request.params_hash,
            "run_kind": "incremental" if expected_cursor is None else "rescan",
            "from_marker": request.from_marker,
            "to_marker": request.to_marker,
        }

        def authorize(db: Any) -> None:
            self._authorize(db, request.subject, request.scope, sources)

        if expected_cursor is None:
            values = await self.store.archive_backfill_commit(
                run=run,
                sources=stored_sources,
                markers=markers,
                next_marker=next_marker,
                page_digest=page_digest,
                high_water_confirmed=high_water_confirmed,
                now=ingested_at,
                authorize=authorize,
            )
        else:
            if (
                type(expected_cursor) is not ArchiveCursorRecord
                or expected_cursor.status != "needs_rescan"
                or expected_cursor.scanner != request.scanner
                or expected_cursor.scope != request.scope
            ):
                raise OperationError("archive_cursor_conflict")
            values = await self.store.archive_rescan_page_commit(
                run=run,
                expected_cursor={
                    "scanner_version": expected_cursor.scanner_version,
                    "params_hash": expected_cursor.params_hash,
                    "last_committed_marker": expected_cursor.last_committed_marker,
                    "status": expected_cursor.status,
                },
                sources=stored_sources,
                markers=markers,
                next_marker=next_marker,
                page_digest=page_digest,
                high_water_confirmed=high_water_confirmed,
                now=ingested_at,
                authorize=authorize,
            )
        return _commit(values)

    async def cancel_run(self, subject: str, run_id: str, scope: Scope) -> BackfillCommit:
        _identity(subject, limit=64)
        _identity(run_id)
        if type(scope) is not Scope:
            raise OperationError("invalid_archive_scope")
        if scope.bot_id != self.policy.bot_id:
            raise OperationError("denied")

        def authorize(db: Any) -> None:
            self.policy.check_transaction(
                db, subject, scope, "memory.archive", "", "", False, False
            )

        values = await self.store.archive_cancel_run(
            run_id,
            bot_id=scope.bot_id,
            group_id=scope.group_id,
            authorize=authorize,
            now=self._now(),
        )
        return _commit(values)

    async def read_cursor(self, request: BackfillRequest) -> ArchiveCursorRecord | None:
        def authorize(db: Any) -> None:
            self.policy.check_transaction(
                db, request.subject, request.scope, "memory.archive", "", "", False, False
            )

        values = await self.store.archive_cursor_read(
            scanner=request.scanner,
            bot_id=request.scope.bot_id,
            group_id=request.scope.group_id,
            authorize=authorize,
        )
        return None if values is None else _cursor(values)


class ArchiveBackfillRunner:
    """Run a finite scan through a caller-supplied, authorized source provider.

    Policy is checked before the provider is read and again for every committed
    source page. This runner has no built-in database, platform, or network
    provider and never widens the fixed high-water marker in ``request``.
    """

    def __init__(
        self,
        archive: ArchiveService,
        source_provider: ArchiveSourceProvider,
        *,
        page_size: int,
        max_pages: int,
    ) -> None:
        if type(page_size) is not int or not 1 <= page_size <= _MAX_PAGE_ITEMS:
            raise OperationError("archive_page_limit")
        if type(max_pages) is not int or not 1 <= max_pages <= _MAX_BACKFILL_RUNNER_PAGES:
            raise OperationError("archive_page_limit")
        self.archive = archive
        self.source_provider = source_provider
        self.page_size = page_size
        self.max_pages = max_pages

    async def run(self, request: BackfillRequest) -> BackfillCommit:
        """Scan at most ``max_pages`` and resume from the persisted cursor."""

        cursor = await self.archive.read_cursor(request)
        if cursor is None:
            if request.from_marker:
                raise OperationError("archive_cursor_conflict")
            marker = ""
        else:
            if cursor.status == "needs_rescan":
                raise OperationError("archive_rescan_required")
            marker = cursor.last_committed_marker
        last_commit: BackfillCommit | None = None
        for _ in range(self.max_pages):
            page = await self.source_provider.fetch_page(
                scope=request.scope,
                after_marker=marker,
                through_marker=request.to_marker,
                limit=self.page_size,
            )
            if type(page) is not ArchiveBackfillPage or len(page.items) > self.page_size:
                raise OperationError("archive_page_limit")
            if page.high_water_confirmed and page.items:
                raise OperationError("archive_high_water_requires_empty_page")

            page_request = replace(request, from_marker=marker)
            if not page.items:
                # Empty-page confirmation needs a Store-owned atomic path so a
                # scanner drift can be recorded before this page is rejected.
                return await self.archive.commit_page(
                    page_request,
                    (),
                    next_marker=request.to_marker,
                    high_water_confirmed=page.high_water_confirmed,
                )

            next_marker = page.items[-1].marker
            if next_marker == marker:
                raise OperationError("archive_marker_no_progress")
            last_commit = await self.archive.commit_page(
                page_request,
                page.items,
                next_marker=next_marker,
            )
            if last_commit.status != "running":
                return last_commit
            marker = last_commit.cursor_marker

        if last_commit is None:
            raise OperationError("invalid_archive_run")
        return last_commit

    async def rescan(
        self,
        request: BackfillRequest,
        expected_cursor: ArchiveCursorRecord,
    ) -> BackfillCommit:
        """Resume only an explicitly bounded rescan of the current cursor."""
        checkpoint = await self.archive.begin_rescan(request, expected_cursor)
        if checkpoint.status == "committed":
            return checkpoint
        marker = checkpoint.cursor_marker
        last_commit: BackfillCommit | None = None
        for _ in range(self.max_pages):
            page = await self.source_provider.fetch_page(
                scope=request.scope,
                after_marker=marker,
                through_marker=request.to_marker,
                limit=self.page_size,
            )
            if type(page) is not ArchiveBackfillPage or len(page.items) > self.page_size:
                raise OperationError("archive_page_limit")
            if not page.items:
                return await self.archive.commit_rescan_page(
                    replace(request, from_marker=marker),
                    expected_cursor,
                    (),
                    next_marker=request.to_marker,
                    high_water_confirmed=page.high_water_confirmed,
                )

            next_marker = page.items[-1].marker
            if next_marker == marker:
                raise OperationError("archive_marker_no_progress")
            last_commit = await self.archive.commit_rescan_page(
                replace(request, from_marker=marker),
                expected_cursor,
                page.items,
                next_marker=next_marker,
                high_water_confirmed=page.high_water_confirmed,
            )
            if last_commit.status != "running":
                return last_commit
            marker = last_commit.cursor_marker

        if last_commit is None:
            raise OperationError("invalid_archive_run")
        return last_commit
