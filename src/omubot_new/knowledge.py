"""Explicitly reviewed, non-personal Markdown sources in the existing Store.

Documents are data, never instructions or consent on behalf of named people.
No filesystem discovery, raw-message archive, model, vector store or cache lives
here. Source/chunk revisions are checked again by the consumer at dispatch.
"""

from __future__ import annotations

import asyncio
import bisect
import hashlib
import json
import math
import re
import time
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Literal, cast

from pydantic import Field, field_validator, model_validator

from .policy import Policy
from .store import Store, StoreConnection, StoreRow
from .types import (
    KnowledgeVisibilityRef,
    OperationError,
    Scope,
    StrictModel,
    VisibilityGrant,
    VisibilityReceipt,
)

INDEX_VERSION = "markdown-ngram-v1"
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_SCOPE_BYTES = 4 * 1024 * 1024
MAX_SCOPE_SOURCES = 128
MAX_CHUNK_CHARS = 1600
MAX_CHUNKS = 256
_SOURCE_FIELDS = (
    "bot_id,group_id,source_id,uploader_id,source_label,title,content_hash,revision,content_revision,"
    "review_status,reviewed_content_revision,review_actor,status,apply_actor,upload_policy_revision,"
    "review_policy_revision,apply_policy_revision,index_version,created_at,updated_at"
)
_STOP_CHARS = frozenset("的了是在和与或及而就都也把被对给从到于这那哪么怎什")
_STOP_WORDS = frozenset({"a", "an", "the", "and", "or", "is", "are", "what", "how", "with"})


class MarkdownSourceInput(StrictModel):
    source_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    source_label: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=MAX_DOCUMENT_BYTES, repr=False)
    classification: Literal["non_personal_document"]
    format: Literal["markdown"] = "markdown"

    @field_validator("source_label", "title")
    @classmethod
    def one_line_label(cls, value: str) -> str:
        if value != value.strip() or any(ord(c) < 32 or c in "\x85\u2028\u2029" for c in value):
            raise ValueError("document labels must be exact nonblank single-line data")
        return value

    @field_validator("content")
    @classmethod
    def bounded_utf8(cls, value: str) -> str:
        if not value.strip() or len(value.encode("utf-8")) > MAX_DOCUMENT_BYTES or "\0" in value:
            raise ValueError("document must be bounded nonblank UTF-8")
        return value


@dataclass(frozen=True, slots=True)
class KnowledgePosition:
    ordinal: int
    start_char: int
    end_char: int
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class MarkdownChunk:
    title: str
    body: str
    position: KnowledgePosition


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    scope: Scope
    source_id: str
    uploader_id: str
    source_label: str
    title: str
    content_hash: str
    revision: int
    content_revision: int
    review_status: Literal["pending", "approved", "rejected"]
    reviewed_content_revision: int | None
    review_actor: str | None
    status: Literal["inactive", "active", "removed"]
    apply_actor: str | None
    upload_policy_revision: int
    review_policy_revision: int | None
    apply_policy_revision: int | None
    index_version: str
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    scope: Scope
    reader_id: str
    source_id: str
    source_revision: int
    content_revision: int
    source_hash: str
    source_label: str
    uploader_id: str
    chunk_id: str
    index_version: str
    title: str
    position: KnowledgePosition
    body: str
    score: float
    policy_revision: int

    def to_dict(self) -> dict[str, object]:
        return {
            "source_kind": "document",
            "bot_id": self.scope.bot_id,
            "group_id": self.scope.group_id,
            "reader_id": self.reader_id,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "content_revision": self.content_revision,
            "source_hash": self.source_hash,
            "source_label": self.source_label,
            "uploader_id": self.uploader_id,
            "chunk_id": self.chunk_id,
            "index_version": self.index_version,
            "title": self.title,
            "body": self.body,
            "score": self.score,
            "policy_revision": self.policy_revision,
            "position": {
                "ordinal": self.position.ordinal,
                "start_char": self.position.start_char,
                "end_char": self.position.end_char,
                "start_line": self.position.start_line,
                "end_line": self.position.end_line,
            },
        }


@dataclass(frozen=True, slots=True)
class SharedKnowledgeHit:
    """Keep original source provenance distinct from the target reader's visibility."""

    hit: KnowledgeHit
    target_scope: Scope
    reader_id: str
    receipt: VisibilityReceipt



class KnowledgeChunkPointer(StrictModel):
    """A durable exact document span, without a stored raw-body copy or reader grant."""

    scope: Scope
    source_id: str = Field(min_length=1, max_length=128)
    source_revision: int = Field(ge=1)
    content_revision: int = Field(ge=1)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    uploader_id: str = Field(min_length=1, max_length=64)
    chunk_id: str = Field(min_length=1, max_length=128)
    index_version: str = Field(min_length=1, max_length=64)
    ordinal: int = Field(ge=0)
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    span_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def bounded_span(self) -> KnowledgeChunkPointer:
        if not 0 < self.end_char - self.start_char <= MAX_CHUNK_CHARS or self.end_line < self.start_line:
            raise ValueError("invalid document span")
        return self

    @classmethod
    def from_hit(cls, hit: KnowledgeHit) -> KnowledgeChunkPointer:
        return cls(
            scope=hit.scope, source_id=hit.source_id, source_revision=hit.source_revision,
            content_revision=hit.content_revision, source_hash=hit.source_hash,
            uploader_id=hit.uploader_id, chunk_id=hit.chunk_id, index_version=hit.index_version,
            ordinal=hit.position.ordinal, start_char=hit.position.start_char,
            end_char=hit.position.end_char, start_line=hit.position.start_line,
            end_line=hit.position.end_line, span_hash=hashlib.sha256(hit.body.encode()).hexdigest(),
        )


def tokenize(text: str) -> Counter[str]:
    terms: Counter[str] = Counter()
    for segment in re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", text.casefold()):
        if "\u3400" <= segment[0] <= "\u9fff":
            terms.update(c for c in segment if c not in _STOP_CHARS)
            terms.update(
                segment[i : i + 2]
                for i in range(len(segment) - 1)
                if segment[i] not in _STOP_CHARS and segment[i + 1] not in _STOP_CHARS
            )
        elif segment not in _STOP_WORDS:
            terms[segment] += 1
    return terms


def chunk_markdown(document: MarkdownSourceInput) -> Iterator[MarkdownChunk]:
    """Preserve exact source spans, bounded sections, and fenced code as data."""
    text = document.content
    lines = text.splitlines(keepends=True)
    starts: list[int] = []
    boundaries: list[tuple[int, str]] = [(0, document.title)]
    offset = 0
    fence: tuple[str, int] | None = None
    for line in lines:
        starts.append(offset)
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            run = marker.group(1)
            if fence is None:
                fence = run[0], len(run)
            elif run[0] == fence[0] and len(run) >= fence[1]:
                fence = None
        elif fence is None and (heading := re.match(r"^ {0,3}#{1,6}[ \t]+(.+?)\s*$", line)):
            title = f"{document.title} › {heading.group(1).strip()}"[:256]
            if offset == 0:
                boundaries[0] = (0, title)
            else:
                boundaries.append((offset, title))
        offset += len(line)
    ordinal = 0
    for index, (start, title) in enumerate(boundaries):
        section_end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        while start < section_end:
            end = min(start + MAX_CHUNK_CHARS, section_end)
            if end < section_end:
                newline = text.rfind("\n", start + MAX_CHUNK_CHARS // 2, end)
                if newline >= 0:
                    end = newline + 1
            body = text[start:end]
            if body.strip():
                if ordinal >= MAX_CHUNKS:
                    raise OperationError("knowledge_chunk_budget_exceeded")
                yield MarkdownChunk(
                    title,
                    body,
                    KnowledgePosition(
                        ordinal,
                        start,
                        end,
                        bisect.bisect_right(starts, start),
                        bisect.bisect_right(starts, end - 1),
                    ),
                )
                ordinal += 1
            start = end


def _source(row: StoreRow) -> KnowledgeSource:
    return KnowledgeSource(
        Scope(bot_id=str(row["bot_id"]), group_id=str(row["group_id"])),
        str(row["source_id"]),
        str(row["uploader_id"]),
        str(row["source_label"]),
        str(row["title"]),
        str(row["content_hash"]),
        int(row["revision"]),
        int(row["content_revision"]),
        cast(Literal["pending", "approved", "rejected"], row["review_status"]),
        row["reviewed_content_revision"],
        row["review_actor"],
        cast(Literal["inactive", "active", "removed"], row["status"]),
        row["apply_actor"],
        int(row["upload_policy_revision"]),
        row["review_policy_revision"],
        row["apply_policy_revision"],
        str(row["index_version"]),
        float(row["created_at"]),
        float(row["updated_at"]),
    )


def _chunk_id(scope: Scope, source_id: str, content_revision: int, ordinal: int) -> str:
    data = ["knowledge.chunk.v1", scope.bot_id, scope.group_id, source_id, content_revision, ordinal]
    return hashlib.sha256(json.dumps(data, ensure_ascii=True).encode()).hexdigest()


def _end_line(body: str, length: int, start_line: int) -> int:
    breaks = re.finditer(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]", body)
    return start_line + sum(match.end() < length for match in breaks)


class KnowledgeService:
    def __init__(self, store: Store, policy: Policy) -> None:
        self.store, self.policy = store, policy

    def _permission(self, db: StoreConnection, actor: str, scope: Scope, action: str) -> int:
        return self.policy.check_transaction(db, actor, scope, action, "", "", False, False)

    @staticmethod
    def _row(db: StoreConnection, scope: Scope, source_id: str) -> StoreRow:
        row = db.execute(
            "SELECT * FROM knowledge_sources WHERE bot_id=? AND group_id=? AND source_id=?",
            (scope.bot_id, scope.group_id, source_id),
        ).fetchone()
        if row is None:
            raise OperationError("knowledge_source_not_found")
        if row["status"] == "removed":
            raise OperationError("knowledge_source_removed")
        return row

    @staticmethod
    def _expected(row: StoreRow | None, revision: int) -> None:
        current = 0 if row is None else int(row["revision"])
        if type(revision) is not int or revision != current:
            raise OperationError("knowledge_revision_conflict")

    def _authority(self, db: StoreConnection, row: StoreRow, scope: Scope) -> None:
        self._permission(db, str(row["uploader_id"]), scope, "knowledge.import")

    @staticmethod
    def _audit(db: StoreConnection, row: StoreRow, actor: str, code: str) -> None:
        db.execute(
            "INSERT INTO audit(kind,identity,revision,code,details) VALUES ('knowledge',?,?,?,?)",
            (
                row["source_id"],
                row["revision"],
                code,
                json.dumps(
                    {
                        "bot_id": row["bot_id"],
                        "group_id": row["group_id"],
                        "actor": actor,
                        "content_hash": row["content_hash"],
                        "content_revision": row["content_revision"],
                    }
                ),
            ),
        )

    @staticmethod
    def _delete_chunks(db: StoreConnection, scope: Scope, source_id: str) -> None:
        # Store does not enable global foreign_keys; this owner deletes both
        # projections explicitly rather than silently relying on CASCADE.
        parameters = (scope.bot_id, scope.group_id, source_id)
        db.execute(
            "DELETE FROM knowledge_chunk_terms WHERE bot_id=? AND group_id=? AND chunk_id IN "
            "(SELECT chunk_id FROM knowledge_chunks WHERE bot_id=? AND group_id=? AND source_id=?)",
            (scope.bot_id, scope.group_id, *parameters),
        )
        db.execute("DELETE FROM knowledge_chunks WHERE bot_id=? AND group_id=? AND source_id=?", parameters)

    async def reindex(
        self,
        document: MarkdownSourceInput,
        *,
        scope: Scope,
        actor: str,
        expected_revision: int,
        _rebuild: bool = False,
    ) -> KnowledgeSource:
        content_hash = hashlib.sha256(document.content.encode()).hexdigest()

        def prepare(db: StoreConnection) -> tuple[KnowledgeSource | None, bool]:
            self._permission(db, actor, scope, "knowledge.import")
            row = db.execute(
                "SELECT * FROM knowledge_sources WHERE bot_id=? AND group_id=? AND source_id=?",
                (scope.bot_id, scope.group_id, document.source_id),
            ).fetchone()
            self._expected(row, expected_revision)
            if row is None:
                return None, False
            if row["status"] == "removed":
                raise OperationError("knowledge_source_removed")
            if row["uploader_id"] != actor:
                raise OperationError("denied")
            unchanged = (
                row["content_hash"] == content_hash
                and row["title"] == document.title
                and row["source_label"] == document.source_label
                and row["index_version"] == INDEX_VERSION
            )
            return _source(row), unchanged

        previous, unchanged = await self.store.transaction(prepare)
        if unchanged and not _rebuild:
            assert previous is not None
            return previous
        chunks: list[tuple[MarkdownChunk, Counter[str]]] = []
        # Cancellation before SQL commit drops only local staged objects. The
        # old persistent document remains available until one atomic commit.
        for chunk in chunk_markdown(document):
            await asyncio.sleep(0)
            chunks.append((chunk, tokenize(chunk.title + "\n" + chunk.body)))
        await asyncio.sleep(0)
        now = time.time()
        content_revision = 1 if previous is None else previous.content_revision + (not unchanged)

        def commit(db: StoreConnection) -> KnowledgeSource:
            db.execute("BEGIN IMMEDIATE")
            permission_revision = self._permission(db, actor, scope, "knowledge.import")
            row = db.execute(
                "SELECT * FROM knowledge_sources WHERE bot_id=? AND group_id=? AND source_id=?",
                (scope.bot_id, scope.group_id, document.source_id),
            ).fetchone()
            self._expected(row, expected_revision)
            if row is not None and (row["status"] == "removed" or row["uploader_id"] != actor):
                raise OperationError("denied")
            counts = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(body_bytes),0) FROM knowledge_sources "
                "WHERE bot_id=? AND group_id=? AND status<>'removed' AND source_id<>?",
                (scope.bot_id, scope.group_id, document.source_id),
            ).fetchone()
            body_bytes = len(document.content.encode())
            if counts[0] >= MAX_SCOPE_SOURCES or counts[1] + body_bytes > MAX_SCOPE_BYTES:
                raise OperationError("knowledge_source_budget_exceeded")
            if row is None:
                db.execute(
                    "INSERT INTO knowledge_sources(bot_id,group_id,source_id,uploader_id,source_label,title,"
                    "format,classification,body,body_bytes,content_hash,revision,content_revision,review_status,"
                    "status,upload_policy_revision,index_version,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,'markdown','non_personal_document',?,?,?,1,1,"
                    "'pending','inactive',?,?,?,?)",
                    (
                        scope.bot_id,
                        scope.group_id,
                        document.source_id,
                        actor,
                        document.source_label,
                        document.title,
                        document.content,
                        body_bytes,
                        content_hash,
                        permission_revision,
                        INDEX_VERSION,
                        now,
                        now,
                    ),
                )
            else:
                self._delete_chunks(db, scope, document.source_id)
                db.execute(
                    "UPDATE knowledge_sources SET source_label=?,title=?,body=?,body_bytes=?,content_hash=?,"
                    "revision=revision+1,content_revision=?,upload_policy_revision=?,"
                    "index_version=?,updated_at=? "
                    "WHERE bot_id=? AND group_id=? AND source_id=?",
                    (
                        document.source_label,
                        document.title,
                        document.content,
                        body_bytes,
                        content_hash,
                        content_revision,
                        permission_revision,
                        INDEX_VERSION,
                        now,
                        scope.bot_id,
                        scope.group_id,
                        document.source_id,
                    ),
                )
                if not unchanged:
                    db.execute(
                        "UPDATE knowledge_sources SET review_status='pending',reviewed_content_revision=NULL,"
                        "review_actor=NULL,review_policy_revision=NULL,status='inactive',apply_actor=NULL,"
                        "apply_policy_revision=NULL WHERE bot_id=? AND group_id=? AND source_id=?",
                        (scope.bot_id, scope.group_id, document.source_id),
                    )
            for chunk, terms in chunks:
                position = chunk.position
                chunk_id = _chunk_id(scope, document.source_id, content_revision, position.ordinal)
                db.execute(
                    "INSERT INTO knowledge_chunks(bot_id,group_id,chunk_id,source_id,source_revision,ordinal,"
                    "start_char,end_char,start_line,end_line,title,body,token_count) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        scope.bot_id,
                        scope.group_id,
                        chunk_id,
                        document.source_id,
                        content_revision,
                        position.ordinal,
                        position.start_char,
                        position.end_char,
                        position.start_line,
                        position.end_line,
                        chunk.title,
                        chunk.body,
                        terms.total(),
                    ),
                )
                db.executemany(
                    "INSERT INTO knowledge_chunk_terms(bot_id,group_id,term,chunk_id,frequency) "
                    "VALUES (?,?,?,?,?)",
                    [
                        (scope.bot_id, scope.group_id, term, chunk_id, frequency)
                        for term, frequency in terms.items()
                    ],
                )
            current = self._row(db, scope, document.source_id)
            self._audit(db, current, actor, "rebuilt" if _rebuild else "indexed")
            return _source(current)

        return await self.store.transaction(commit)

    async def rebuild(
        self,
        *,
        scope: Scope,
        actor: str,
        source_id: str,
        expected_revision: int,
    ) -> KnowledgeSource:
        def read(db: StoreConnection) -> MarkdownSourceInput:
            self._permission(db, actor, scope, "knowledge.import")
            row = self._row(db, scope, source_id)
            self._expected(row, expected_revision)
            if row["uploader_id"] != actor:
                raise OperationError("denied")
            return MarkdownSourceInput(
                source_id=source_id,
                source_label=str(row["source_label"]),
                title=str(row["title"]),
                content=str(row["body"]),
                classification="non_personal_document",
            )

        document = await self.store.transaction(read)
        return await self.reindex(
            document, scope=scope, actor=actor, expected_revision=expected_revision, _rebuild=True
        )

    async def list_sources(
        self, *, scope: Scope, actor: str, limit: int = 32, after: str | None = None,
    ) -> tuple[tuple[KnowledgeSource, ...], str | None]:
        if type(limit) is not int or not 1 <= limit <= 64:
            raise OperationError("invalid_knowledge_limit")
        if after is not None and (not after or len(after) > 128):
            raise OperationError("invalid_knowledge_cursor")

        def read(db: StoreConnection) -> tuple[tuple[KnowledgeSource, ...], str | None]:
            self._permission(db, actor, scope, "knowledge.review")
            rows = db.execute(
                f"SELECT {_SOURCE_FIELDS} FROM knowledge_sources "
                "WHERE bot_id=? AND group_id=? AND source_id>? ORDER BY source_id LIMIT ?",
                (scope.bot_id, scope.group_id, after or "", limit + 1),
            ).fetchall()
            items = tuple(_source(row) for row in rows[:limit])
            return items, items[-1].source_id if len(rows) > limit else None

        return await self.store.transaction(read)

    async def source(self, *, scope: Scope, actor: str, source_id: str) -> KnowledgeSource:
        def read(db: StoreConnection) -> KnowledgeSource:
            self._permission(db, actor, scope, "knowledge.review")
            return _source(self._row(db, scope, source_id))

        return await self.store.transaction(read)

    async def read_document(
        self,
        *,
        scope: Scope,
        actor: str,
        source_id: str,
        expected_revision: int,
    ) -> tuple[KnowledgeSource, MarkdownSourceInput]:
        """Explicit review preview; never expose an unauthorized stored body."""

        def read(db: StoreConnection) -> tuple[KnowledgeSource, MarkdownSourceInput]:
            self._permission(db, actor, scope, "knowledge.review")
            row = self._row(db, scope, source_id)
            self._expected(row, expected_revision)
            self._authority(db, row, scope)
            return _source(row), MarkdownSourceInput(
                source_id=source_id,
                source_label=str(row["source_label"]),
                title=str(row["title"]),
                content=str(row["body"]),
                classification="non_personal_document",
            )

        return await self.store.transaction(read)

    async def review(
        self,
        *,
        scope: Scope,
        actor: str,
        source_id: str,
        expected_revision: int,
        decision: Literal["approved", "rejected"],
        non_personal_document: bool = False,
    ) -> KnowledgeSource:
        if decision not in {"approved", "rejected"} or (
            decision == "approved" and non_personal_document is not True
        ):
            raise OperationError("knowledge_review_required")

        def commit(db: StoreConnection) -> KnowledgeSource:
            db.execute("BEGIN IMMEDIATE")
            revision = self._permission(db, actor, scope, "knowledge.review")
            row = self._row(db, scope, source_id)
            self._expected(row, expected_revision)
            self._authority(db, row, scope)
            if (
                row["review_status"] == decision
                and row["reviewed_content_revision"] == row["content_revision"]
            ):
                return _source(row)
            db.execute(
                "UPDATE knowledge_sources SET revision=revision+1,review_status=?,"
                "reviewed_content_revision=?,"
                "review_actor=?,review_policy_revision=?,status='inactive',apply_actor=NULL,apply_policy_revision=NULL,"
                "updated_at=? WHERE bot_id=? AND group_id=? AND source_id=?",
                (
                    decision,
                    row["content_revision"],
                    actor,
                    revision,
                    time.time(),
                    scope.bot_id,
                    scope.group_id,
                    source_id,
                ),
            )
            current = self._row(db, scope, source_id)
            self._audit(db, current, actor, decision)
            return _source(current)

        return await self.store.transaction(commit)

    async def set_active(
        self,
        *,
        scope: Scope,
        actor: str,
        source_id: str,
        expected_revision: int,
        active: bool,
    ) -> KnowledgeSource:
        if type(active) is not bool:
            raise OperationError("invalid_knowledge_activation")

        def commit(db: StoreConnection) -> KnowledgeSource:
            db.execute("BEGIN IMMEDIATE")
            revision = self._permission(db, actor, scope, "knowledge.apply")
            row = self._row(db, scope, source_id)
            self._expected(row, expected_revision)
            if active:
                self._authority(db, row, scope)
                if (
                    row["review_status"] != "approved"
                    or row["reviewed_content_revision"] != row["content_revision"]
                ):
                    raise OperationError("knowledge_review_required")
            status = "active" if active else "inactive"
            if row["status"] == status:
                return _source(row)
            db.execute(
                "UPDATE knowledge_sources SET revision=revision+1,status=?,apply_actor=?,"
                "apply_policy_revision=?,"
                "updated_at=? WHERE bot_id=? AND group_id=? AND source_id=?",
                (status, actor, revision, time.time(), scope.bot_id, scope.group_id, source_id),
            )
            current = self._row(db, scope, source_id)
            self._audit(db, current, actor, "activated" if active else "disabled")
            return _source(current)

        return await self.store.transaction(commit)

    async def remove(
        self,
        *,
        scope: Scope,
        actor: str,
        source_id: str,
        expected_revision: int,
    ) -> KnowledgeSource:
        def commit(db: StoreConnection) -> KnowledgeSource:
            db.execute("BEGIN IMMEDIATE")
            self._permission(db, actor, scope, "knowledge.remove")
            row = db.execute(
                "SELECT * FROM knowledge_sources WHERE bot_id=? AND group_id=? AND source_id=?",
                (scope.bot_id, scope.group_id, source_id),
            ).fetchone()
            self._expected(row, expected_revision)
            if row is None:
                raise OperationError("knowledge_source_not_found")
            if row["status"] == "removed":
                return _source(row)
            self._delete_chunks(db, scope, source_id)
            db.execute(
                "UPDATE knowledge_sources SET revision=revision+1,status='removed',body='',body_bytes=0,"
                "updated_at=? WHERE bot_id=? AND group_id=? AND source_id=?",
                (time.time(), scope.bot_id, scope.group_id, source_id),
            )
            current = db.execute(
                "SELECT * FROM knowledge_sources WHERE bot_id=? AND group_id=? AND source_id=?",
                (scope.bot_id, scope.group_id, source_id),
            ).fetchone()
            assert current is not None
            self._audit(db, current, actor, "removed")
            return _source(current)

        return await self.store.transaction(commit)

    def _active_sources(self, db: StoreConnection, scope: Scope) -> dict[str, StoreRow]:
        sources: dict[str, StoreRow] = {}
        for row in db.execute(
            f"SELECT {_SOURCE_FIELDS} FROM knowledge_sources WHERE bot_id=? AND group_id=? "
            "AND status='active' AND review_status='approved' AND reviewed_content_revision=content_revision "
            "AND index_version=?",
            (scope.bot_id, scope.group_id, INDEX_VERSION),
        ):
            try:
                self._authority(db, row, scope)
            except OperationError as exc:
                if exc.code != "denied":
                    raise
                continue
            sources[str(row["source_id"])] = row
        return sources

    @staticmethod
    def _query_terms(query: str, limit: int, body_budget: int) -> list[str]:
        if type(query) is not str or len(query) > 512 or not query.strip() or "\0" in query:
            raise OperationError("invalid_knowledge_query")
        if (
            type(limit) is not int
            or not 1 <= limit <= 20
            or type(body_budget) is not int
            or not 1 <= body_budget <= 8192
        ):
            raise OperationError("invalid_knowledge_budget")
        query_terms = sorted(tokenize(query))
        if len(query_terms) > 128:
            raise OperationError("knowledge_query_budget_exceeded")

        return query_terms

    def _search_transaction(
        self, db: StoreConnection, *, scope: Scope, actor: str,
        sources: dict[str, StoreRow], policy_revision: int, query_terms: list[str],
        limit: int, body_budget: int,
    ) -> tuple[KnowledgeHit, ...]:
        if not query_terms or not sources:
            return ()
        source_ids = sorted(sources)
        source_marks = ",".join("?" for _ in source_ids)
        term_marks = ",".join("?" for _ in query_terms)
        total, tokens = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(token_count),0) FROM knowledge_chunks "
            f"WHERE bot_id=? AND group_id=? AND source_id IN ({source_marks})",
            (scope.bot_id, scope.group_id, *source_ids),
        ).fetchone()
        average = tokens / max(total, 1)
        rows = db.execute(
            "SELECT t.chunk_id,t.term,t.frequency,c.token_count,c.source_id,c.ordinal "
            "FROM knowledge_chunk_terms AS t JOIN knowledge_chunks AS c "
            "ON c.bot_id=t.bot_id AND c.group_id=t.group_id AND c.chunk_id=t.chunk_id "
            f"WHERE t.bot_id=? AND t.group_id=? AND t.term IN ({term_marks}) "
            f"AND c.source_id IN ({source_marks})",
            (scope.bot_id, scope.group_id, *query_terms, *source_ids),
        ).fetchall()
        frequencies = Counter(str(row["term"]) for row in rows)
        scores: dict[str, float] = defaultdict(float)
        identifiers: dict[str, tuple[str, int]] = {}
        for row in rows:
            term, chunk_id = str(row["term"]), str(row["chunk_id"])
            frequency, length = int(row["frequency"]), int(row["token_count"])
            idf = math.log(1 + (total - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            normalization = 1.5 * (0.25 + 0.75 * length / max(average, 1))
            scores[chunk_id] += idf * frequency * 2.5 / (frequency + normalization)
            identifiers[chunk_id] = str(row["source_id"]), int(row["ordinal"])
        ranked = sorted(
            scores, key=lambda chunk_id: (-scores[chunk_id], *identifiers[chunk_id], chunk_id)
        )
        hits: list[KnowledgeHit] = []
        remaining = body_budget
        for chunk_id in ranked[:limit]:
            row = db.execute(
                "SELECT * FROM knowledge_chunks WHERE bot_id=? AND group_id=? AND chunk_id=?",
                (scope.bot_id, scope.group_id, chunk_id),
            ).fetchone()
            assert row is not None
            source = sources[str(row["source_id"])]
            full_body = str(row["body"])
            body = full_body[:remaining]
            if not body:
                break
            position = KnowledgePosition(
                int(row["ordinal"]),
                int(row["start_char"]),
                int(row["start_char"]) + len(body),
                int(row["start_line"]),
                _end_line(full_body, len(body), int(row["start_line"])),
            )
            hits.append(
                KnowledgeHit(
                    scope,
                    actor,
                    str(source["source_id"]),
                    int(source["revision"]),
                    int(source["content_revision"]),
                    str(source["content_hash"]),
                    str(source["source_label"]),
                    str(source["uploader_id"]),
                    chunk_id,
                    str(source["index_version"]),
                    str(row["title"]),
                    position,
                    body,
                    scores[chunk_id],
                    policy_revision,
                )
            )
            remaining -= len(body)
        return tuple(hits)

    async def search(
        self, query: str, *, scope: Scope, actor: str, limit: int = 5, body_budget: int = 4096,
    ) -> tuple[KnowledgeHit, ...]:
        query_terms = self._query_terms(query, limit, body_budget)

        def read(db: StoreConnection) -> tuple[KnowledgeHit, ...]:
            db.execute("BEGIN IMMEDIATE")
            revision = self._permission(db, actor, scope, "knowledge.retrieve")
            return self._search_transaction(
                db, scope=scope, actor=actor, sources=self._active_sources(db, scope),
                policy_revision=revision, query_terms=query_terms, limit=limit, body_budget=body_budget,
            )

        return await self.store.transaction(read)

    def _visibility_sources(
        self, db: StoreConnection, *, source_scope: Scope,
        object_refs: Sequence[KnowledgeVisibilityRef],
    ) -> dict[str, StoreRow]:
        sources: dict[str, StoreRow] = {}
        for ref in object_refs:
            row = self._row(db, source_scope, ref.source_id)
            self._authority(db, row, source_scope)
            if (
                row["classification"] != "non_personal_document"
                or row["status"] != "active" or row["review_status"] != "approved"
                or row["reviewed_content_revision"] != row["content_revision"]
                or row["revision"] != ref.source_revision
                or row["content_revision"] != ref.content_revision
                or row["index_version"] != ref.index_version or ref.index_version != INDEX_VERSION
            ):
                raise OperationError("knowledge_source_changed")
            sources[ref.source_id] = row
        return sources

    def assert_visibility_grant_transaction(self, db: StoreConnection, grant: VisibilityGrant) -> None:
        """Source qualification runs inside Policy's visibility mutation transaction."""
        if grant.material_type != "knowledge" or any(
            not isinstance(ref, KnowledgeVisibilityRef) for ref in grant.object_refs
        ):
            raise OperationError("invalid_knowledge_visibility")
        self._visibility_sources(
            db, source_scope=grant.source_scope,
            object_refs=cast(tuple[KnowledgeVisibilityRef, ...], grant.object_refs),
        )

    async def search_shared(
        self, query: str, *, actor: str, target_scope: Scope, receipt: VisibilityReceipt,
        limit: int = 5, body_budget: int = 4096,
    ) -> tuple[SharedKnowledgeHit, ...]:
        query_terms = self._query_terms(query, limit, body_budget)

        def read(db: StoreConnection) -> tuple[SharedKnowledgeHit, ...]:
            db.execute("BEGIN IMMEDIATE")
            self.policy.assert_visibility_receipt_transaction(
                db, actor=actor, target_scope=target_scope, receipt=receipt,
            )
            if receipt.material_type != "knowledge" or any(
                not isinstance(ref, KnowledgeVisibilityRef) for ref in receipt.object_refs
            ):
                raise OperationError("invalid_knowledge_visibility")
            sources = self._visibility_sources(
                db, source_scope=receipt.source_scope,
                object_refs=cast(tuple[KnowledgeVisibilityRef, ...], receipt.object_refs),
            )
            hits = self._search_transaction(
                db, scope=receipt.source_scope, actor=actor, sources=sources,
                policy_revision=receipt.policy_revision, query_terms=query_terms,
                limit=limit, body_budget=body_budget,
            )
            return tuple(SharedKnowledgeHit(hit, target_scope, actor, receipt) for hit in hits)

        return await self.store.transaction(read)

    def assert_shared_hits_transaction(
        self, db: StoreConnection, *, actor: str, target_scope: Scope,
        hits: Sequence[SharedKnowledgeHit],
    ) -> None:
        for shared in hits:
            self.policy.assert_visibility_receipt_transaction(
                db, actor=actor, target_scope=target_scope, receipt=shared.receipt,
            )
            hit = shared.hit
            if (shared.target_scope != target_scope or shared.reader_id != actor
                    or hit.reader_id != actor or hit.scope != shared.receipt.source_scope
                    or shared.receipt.material_type != "knowledge"
                    or KnowledgeVisibilityRef(
                        source_id=hit.source_id, source_revision=hit.source_revision,
                        content_revision=hit.content_revision, index_version=hit.index_version,
                    ) not in shared.receipt.object_refs):
                raise OperationError("stale_knowledge_visibility")
            self._assert_source_hits_transaction(db, scope=hit.scope, hits=(hit,))

    def assert_shared_chunk_pointer_transaction(
        self, db: StoreConnection, *, actor: str, target_scope: Scope,
        receipt: VisibilityReceipt, pointer: KnowledgeChunkPointer,
    ) -> MarkdownChunk:
        self.policy.assert_visibility_receipt_transaction(
            db, actor=actor, target_scope=target_scope, receipt=receipt,
        )
        if (pointer.scope != receipt.source_scope or receipt.material_type != "knowledge"
                or KnowledgeVisibilityRef(
                    source_id=pointer.source_id, source_revision=pointer.source_revision,
                    content_revision=pointer.content_revision, index_version=pointer.index_version,
                ) not in receipt.object_refs):
            raise OperationError("stale_knowledge_visibility")
        return self._assert_chunk_pointer_source_transaction(db, pointer=pointer)

    def assert_hits_transaction(
        self,
        db: StoreConnection,
        *,
        scope: Scope,
        actor: str,
        hits: Sequence[KnowledgeHit],
    ) -> None:
        """For the actual model-upload/send transaction, never trust a stale hit."""
        self._permission(db, actor, scope, "knowledge.retrieve")
        for hit in hits:
            if hit.scope != scope or hit.reader_id != actor:
                raise OperationError("denied")
        self._assert_source_hits_transaction(db, scope=scope, hits=hits)

    def _assert_source_hits_transaction(
        self, db: StoreConnection, *, scope: Scope, hits: Sequence[KnowledgeHit],
    ) -> None:
        for hit in hits:
            row = self._row(db, scope, hit.source_id)
            self._authority(db, row, scope)
            if (
                row["status"] != "active"
                or row["review_status"] != "approved"
                or row["reviewed_content_revision"] != row["content_revision"]
                or row["revision"] != hit.source_revision
                or row["content_revision"] != hit.content_revision
                or row["content_hash"] != hit.source_hash
                or row["index_version"] != hit.index_version
                or hit.index_version != INDEX_VERSION
                or row["uploader_id"] != hit.uploader_id
                or row["source_label"] != hit.source_label
            ):
                raise OperationError("knowledge_source_changed")
            chunk = db.execute(
                "SELECT * FROM knowledge_chunks WHERE bot_id=? AND group_id=? AND source_id=? AND chunk_id=?",
                (scope.bot_id, scope.group_id, hit.source_id, hit.chunk_id),
            ).fetchone()
            if chunk is None or (
                chunk["source_revision"] != hit.content_revision
                or chunk["ordinal"] != hit.position.ordinal
                or chunk["title"] != hit.title
                or chunk["start_char"] != hit.position.start_char
                or hit.position.end_char != hit.position.start_char + len(hit.body)
                or chunk["start_line"] != hit.position.start_line
                or hit.position.end_line
                != _end_line(
                    str(chunk["body"]),
                    len(hit.body),
                    int(chunk["start_line"]),
                )
                or str(chunk["body"])[: len(hit.body)] != hit.body
                or not hit.body
                or len(hit.body) > len(str(chunk["body"]))
            ):
                raise OperationError("knowledge_chunk_changed")

    def assert_chunk_pointer_transaction(
        self, db: StoreConnection, *, scope: Scope, actor: str, pointer: KnowledgeChunkPointer,
    ) -> MarkdownChunk:
        """Current source owner gate for derived projections, never a historical consent cache."""
        self._permission(db, actor, scope, "knowledge.retrieve")
        if pointer.scope != scope:
            raise OperationError("denied")
        return self._assert_chunk_pointer_source_transaction(db, pointer=pointer)

    def _assert_chunk_pointer_source_transaction(
        self, db: StoreConnection, *, pointer: KnowledgeChunkPointer,
    ) -> MarkdownChunk:
        scope = pointer.scope
        row = self._row(db, scope, pointer.source_id)
        self._authority(db, row, scope)
        if (
            row["status"] != "active" or row["review_status"] != "approved"
            or row["reviewed_content_revision"] != row["content_revision"]
            or row["revision"] != pointer.source_revision
            or row["content_revision"] != pointer.content_revision
            or row["content_hash"] != pointer.source_hash
            or row["uploader_id"] != pointer.uploader_id
            or row["index_version"] != pointer.index_version or pointer.index_version != INDEX_VERSION
        ):
            raise OperationError("knowledge_source_changed")
        chunk = db.execute(
            "SELECT * FROM knowledge_chunks WHERE bot_id=? AND group_id=? AND source_id=? AND chunk_id=?",
            (scope.bot_id, scope.group_id, pointer.source_id, pointer.chunk_id),
        ).fetchone()
        if chunk is None:
            raise OperationError("knowledge_chunk_changed")
        size = pointer.end_char - pointer.start_char
        body = str(chunk["body"])[:size]
        if (
            chunk["source_revision"] != pointer.content_revision or chunk["ordinal"] != pointer.ordinal
            or chunk["start_char"] != pointer.start_char or chunk["start_line"] != pointer.start_line
            or len(body) != size
            or _end_line(str(chunk["body"]), size, int(chunk["start_line"])) != pointer.end_line
            or hashlib.sha256(body.encode()).hexdigest() != pointer.span_hash
        ):
            raise OperationError("knowledge_chunk_changed")
        return MarkdownChunk(str(chunk["title"]), body, KnowledgePosition(
            pointer.ordinal, pointer.start_char, pointer.end_char, pointer.start_line, pointer.end_line,
        ))

    async def resolve_chunk_pointer(
        self, *, scope: Scope, actor: str, pointer: KnowledgeChunkPointer,
    ) -> MarkdownChunk:
        return await self.store.transaction(
            lambda db: self.assert_chunk_pointer_transaction(db, scope=scope, actor=actor, pointer=pointer)
        )

    async def resolve_hit(self, hit: KnowledgeHit, *, scope: Scope, actor: str) -> MarkdownChunk:
        """Current provenance pointer resolution for source-location consumers."""

        def read(db: StoreConnection) -> MarkdownChunk:
            db.execute("BEGIN IMMEDIATE")
            self.assert_hits_transaction(db, scope=scope, actor=actor, hits=(hit,))
            return MarkdownChunk(hit.title, hit.body, replace(hit.position))

        return await self.store.transaction(read)
