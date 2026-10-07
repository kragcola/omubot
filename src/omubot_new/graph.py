"""Manual, source-backed concept projections in the existing Store.

Personal self-fact edges have separate manual governance and are excluded from
concept projections. There is no global graph or source body cache.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Literal, cast

from pydantic import Field, field_validator

from .knowledge import KnowledgeChunkPointer, KnowledgeService
from .memory import MemoryFactPointer, MemoryService
from .policy import Policy
from .store import Store, StoreConnection, StoreRow
from .types import OperationError, Scope, StrictModel

GraphKind = Literal["relation", "alias"]
GraphStatus = Literal["inactive", "active", "ambiguous", "revoked"]
ReviewStatus = Literal["pending", "approved", "rejected"]
StopReason = Literal["exhausted", "depth", "node_budget", "edge_budget", "time_budget", "result_budget"]
_IDENTIFIER = r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$"
_TABLES: dict[GraphKind, str] = {"relation": "graph_relations", "alias": "graph_aliases"}
_STALE_SOURCE_CODES = frozenset(
    {
        "denied",
        "knowledge_source_not_found",
        "knowledge_source_removed",
        "knowledge_source_changed",
        "knowledge_chunk_changed",
        "memory_fact_not_found",
        "stale_memory_fact",
        "source_revoked",
        "source_not_found",
        "graph_memory_owner_required",
    }
)


class RelationInput(StrictModel):
    relation_id: str = Field(pattern=_IDENTIFIER)
    subject_id: str = Field(pattern=_IDENTIFIER)
    predicate: str = Field(pattern=_IDENTIFIER)
    target_id: str = Field(pattern=_IDENTIFIER)
    source: KnowledgeChunkPointer
    classification: Literal["non_personal_concept_relation"]


class SelfFactSource(StrictModel):
    """Human declarations bind to the actual fact, not an automatic sensitivity label."""

    kind: Literal["self_fact_relation"] = "self_fact_relation"
    fact: MemoryFactPointer
    declaration: Literal["self_non_sensitive_fact"]
    fact_classification: Literal["self_preference"]
    target_classification: Literal["non_personal_concept"]
    target_value_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class SelfFactRelationInput(StrictModel):
    relation_id: str = Field(pattern=_IDENTIFIER)
    source: MemoryFactPointer
    declaration: Literal["self_non_sensitive_fact"]
    fact_classification: Literal["self_preference"]
    target_id: str = Field(pattern=_IDENTIFIER)
    target_value: str = Field(min_length=1, max_length=256)
    target_classification: Literal["non_personal_concept"]


@dataclass(frozen=True, slots=True)
class SelfFactGraphRelation:
    scope: Scope
    relation_id: str
    revision: int
    subject_id: str
    predicate: str
    target_id: str
    source: SelfFactSource
    review_status: ReviewStatus
    status: GraphStatus


class AliasInput(StrictModel):
    alias_id: str = Field(pattern=_IDENTIFIER)
    entity_id: str = Field(pattern=_IDENTIFIER)
    alias: str = Field(min_length=1, max_length=128)
    source: KnowledgeChunkPointer
    classification: Literal["non_personal_concept_alias"]

    @field_validator("alias")
    @classmethod
    def literal_alias(cls, value: str) -> str:
        if value != value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("alias must be exact single-line data")
        return value


@dataclass(frozen=True, slots=True)
class GraphRelation:
    scope: Scope
    relation_id: str
    revision: int
    subject_id: str
    predicate: str
    target_id: str
    source: KnowledgeChunkPointer
    review_status: ReviewStatus
    status: GraphStatus


@dataclass(frozen=True, slots=True)
class GraphAlias:
    scope: Scope
    alias_id: str
    revision: int
    entity_id: str
    alias: str
    source: KnowledgeChunkPointer
    review_status: ReviewStatus
    status: GraphStatus


@dataclass(frozen=True, slots=True)
class AliasResolution:
    scope: Scope
    reader_id: str
    state: Literal["resolved", "ambiguous", "missing"]
    entity_id: str | None
    aliases: tuple[GraphAlias, ...]


@dataclass(frozen=True, slots=True)
class GraphObjectPage:
    items: tuple[GraphRelation | GraphAlias, ...]
    next_cursor: str | None


class GraphHealthCounts(StrictModel):
    """Counts of inspected rows; review state and graph state are independent."""

    scanned: int = Field(default=0, ge=0)
    pending: int = Field(default=0, ge=0)
    approved: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    inactive: int = Field(default=0, ge=0)
    active: int = Field(default=0, ge=0)
    ambiguous: int = Field(default=0, ge=0)
    revoked: int = Field(default=0, ge=0)
    current_primary_valid: int = Field(default=0, ge=0)
    current_primary_invalid: int = Field(default=0, ge=0)
    active_primary_valid: int = Field(default=0, ge=0)
    active_primary_invalid: int = Field(default=0, ge=0)


class GraphHealthSnapshot(StrictModel):
    """Body-free governance snapshot; partial counts are never population totals."""

    version: Literal["graph_health_v1"] = "graph_health_v1"
    state: Literal["complete", "partial"]
    scan_limit: Literal[2048] = 2048
    relations: GraphHealthCounts
    aliases: GraphHealthCounts


@dataclass(frozen=True, slots=True)
class GraphProjection:
    scope: Scope
    reader_id: str
    relations: tuple[GraphRelation | SelfFactGraphRelation, ...]
    paths: tuple[tuple[str, ...], ...]
    aliases: tuple[GraphAlias, ...]
    stop_reason: StopReason
    nodes_visited: int
    edges_examined: int

    @property
    def document_upload_subjects(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            item.source.uploader_id for item in (*self.relations, *self.aliases)
            if isinstance(item.source, KnowledgeChunkPointer)
        ))

    @property
    def personal_upload_subjects(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            item.source.fact.subject_id for item in self.relations
            if isinstance(item, SelfFactGraphRelation)
        ))

    @property
    def upload_subjects(self) -> tuple[str, ...]:
        if self.personal_upload_subjects:
            raise OperationError("graph_personal_consumer_required")
        return self.document_upload_subjects


def _scope(row: StoreRow) -> Scope:
    return Scope(bot_id=str(row["bot_id"]), group_id=str(row["group_id"]))


def _source(row: StoreRow) -> KnowledgeChunkPointer | SelfFactSource:
    payload = json.loads(str(row["pointer_json"]))
    if payload.get("kind") == "self_fact_relation":
        return SelfFactSource.model_validate_json(str(row["pointer_json"]))
    return KnowledgeChunkPointer.model_validate_json(str(row["pointer_json"]))


def _doc_source(row: StoreRow) -> KnowledgeChunkPointer:
    source = _source(row)
    if not isinstance(source, KnowledgeChunkPointer):
        raise OperationError("graph_personal_projection_disabled")
    return source


def _self_relation(row: StoreRow) -> SelfFactGraphRelation:
    source = _source(row)
    if not isinstance(source, SelfFactSource):
        raise OperationError("graph_self_fact_source_required")
    return SelfFactGraphRelation(
        _scope(row), str(row["object_id"]), int(row["revision"]), str(row["subject_id"]),
        str(row["predicate"]), str(row["target_id"]), source,
        cast(ReviewStatus, row["review_status"]), cast(GraphStatus, row["status"]),
    )


def _relation(row: StoreRow) -> GraphRelation:
    return GraphRelation(
        _scope(row),
        str(row["object_id"]),
        int(row["revision"]),
        str(row["subject_id"]),
        str(row["predicate"]),
        str(row["target_id"]),
        _doc_source(row),
        cast(ReviewStatus, row["review_status"]),
        cast(GraphStatus, row["status"]),
    )


def _alias(row: StoreRow) -> GraphAlias:
    return GraphAlias(
        _scope(row),
        str(row["object_id"]),
        int(row["revision"]),
        str(row["entity_id"]),
        str(row["alias_surface"]),
        _doc_source(row),
        cast(ReviewStatus, row["review_status"]),
        cast(GraphStatus, row["status"]),
    )


def _norm(alias: str) -> str:
    return " ".join(unicodedata.normalize("NFC", alias).casefold().split())


def _identifier(value: str) -> str:
    if type(value) is not str or re.fullmatch(_IDENTIFIER, value) is None:
        raise OperationError("invalid_graph_identifier")
    return value


class GraphService:
    def __init__(
        self,
        store: Store,
        policy: Policy,
        knowledge: KnowledgeService,
        *,
        clock: Callable[[], float] = time.monotonic,
        memory: MemoryService | None = None,
    ) -> None:
        if knowledge.store is not store or knowledge.policy is not policy:
            raise OperationError("graph_owner_mismatch")
        if memory is not None and (memory.store is not store or memory.policy is not policy):
            raise OperationError("graph_owner_mismatch")
        self.store, self.policy, self.knowledge, self.clock = store, policy, knowledge, clock
        self.memory = memory

    def _permission(self, db: StoreConnection, actor: str, scope: Scope, action: str) -> int:
        revision = self.policy.check_transaction(db, actor, scope, action, "", "", False, False)
        self.policy.check_transaction(db, actor, scope, "knowledge.retrieve", "", "", False, False)
        return revision

    def _current_source(
        self,
        db: StoreConnection,
        actor: str,
        scope: Scope,
        pointer: KnowledgeChunkPointer | SelfFactSource,
    ) -> None:
        if isinstance(pointer, SelfFactSource):
            self._memory().assert_fact_pointer_transaction(
                db, actor=actor, scope=scope, pointer=pointer.fact,
            )
        else:
            self.knowledge.assert_chunk_pointer_transaction(db, actor=actor, scope=scope, pointer=pointer)

    def _memory(self) -> MemoryService:
        if self.memory is None:
            raise OperationError("graph_memory_owner_required")
        return self.memory

    @staticmethod
    def _row(db: StoreConnection, kind: GraphKind, scope: Scope, object_id: str) -> StoreRow:
        row = db.execute(
            f"SELECT * FROM {_TABLES[kind]} WHERE bot_id=? AND group_id=? AND object_id=?",
            (scope.bot_id, scope.group_id, object_id),
        ).fetchone()
        if row is None:
            raise OperationError("graph_object_not_found")
        return row

    @staticmethod
    def _expected(row: StoreRow | None, revision: int) -> None:
        if type(revision) is not int or revision != (0 if row is None else row["revision"]):
            raise OperationError("graph_revision_conflict")

    @staticmethod
    def _audit(db: StoreConnection, actor: str, row: StoreRow, code: str) -> None:
        details = {"actor": actor, "bot_id": row["bot_id"], "group_id": row["group_id"]}
        if code.startswith("self_fact_"):
            source = _self_relation(row).source
            details.update({
                "source_hash": hashlib.sha256(str(row["pointer_json"]).encode()).hexdigest(),
                "fact_digest": source.fact.fact_digest,
                "payload_digest": str(row["payload_digest"]),
                "declaration": source.declaration,
                "fact_classification": source.fact_classification,
                "target_classification": source.target_classification,
            })
        db.execute(
            "INSERT INTO audit(kind,identity,revision,code,details) VALUES ('graph',?,?,?,?)",
            (
                row["object_id"],
                row["revision"],
                code,
                json.dumps(details),
            ),
        )

    def _self_permission(
        self, db: StoreConnection, actor: str, scope: Scope, action: str,
    ) -> int:
        revision = self.policy.check_transaction(db, actor, scope, action, "", "", False, False)
        self._memory().assert_retrieval_actor_transaction(db, actor, scope)
        return revision

    async def propose_self_fact_relation(
        self, value: SelfFactRelationInput, *, actor: str, scope: Scope,
        expected_revision: int = 0,
    ) -> SelfFactGraphRelation:
        frozen = SelfFactRelationInput.model_validate(value.model_dump())
        digest = hashlib.sha256(frozen.model_dump_json().encode()).hexdigest()

        def commit(db: StoreConnection) -> SelfFactGraphRelation:
            db.execute("BEGIN IMMEDIATE")
            revision = self._self_permission(db, actor, scope, "graph.learn")
            fact = self._memory().assert_fact_pointer_transaction(
                db, actor=actor, scope=scope, pointer=frozen.source,
            )
            # The human declares a preference; predicate names are not a classifier.
            if frozen.target_value != fact.value:
                raise OperationError("graph_target_value_mismatch")
            old = db.execute(
                "SELECT * FROM graph_relations WHERE bot_id=? AND group_id=? AND object_id=?",
                (scope.bot_id, scope.group_id, frozen.relation_id),
            ).fetchone()
            self._expected(old, expected_revision)
            if old is not None:
                if old["payload_digest"] != digest:
                    raise OperationError("graph_payload_conflict")
                return _self_relation(old)
            subject = frozen.source.self_entity_id
            if subject == frozen.target_id:
                raise OperationError("graph_entity_kind_conflict")
            for entity, kind in ((subject, "self_subject"), (frozen.target_id, "concept")):
                existing = db.execute(
                    "SELECT kind FROM graph_entities WHERE bot_id=? AND group_id=? AND entity_id=?",
                    (scope.bot_id, scope.group_id, entity),
                ).fetchone()
                if existing is not None and existing[0] != kind:
                    raise OperationError("graph_entity_kind_conflict")
                db.execute("INSERT INTO graph_entities VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                           (scope.bot_id, scope.group_id, entity, kind))
            source = SelfFactSource(
                fact=frozen.source, declaration=frozen.declaration,
                fact_classification=frozen.fact_classification,
                target_classification=frozen.target_classification,
                target_value_digest=hashlib.sha256(fact.value.encode()).hexdigest(),
            )
            now = time.time()
            db.execute(
                "INSERT INTO graph_relations(bot_id,group_id,object_id,subject_id,predicate,target_id,"
                "pointer_json,payload_digest,policy_revision,created_at,updated_at,"
                "revision,review_status,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'pending','inactive')",
                (scope.bot_id, scope.group_id, frozen.relation_id, subject, fact.predicate,
                 frozen.target_id, source.model_dump_json(), digest, revision, now, now),
            )
            row = self._row(db, "relation", scope, frozen.relation_id)
            self._audit(db, actor, row, "self_fact_proposed")
            return _self_relation(row)
        return await self.store.transaction(commit)

    async def read_self_fact_relation(
        self, relation_id: str, *, actor: str, scope: Scope,
    ) -> SelfFactGraphRelation:
        relation_id = _identifier(relation_id)
        def read(db: StoreConnection) -> SelfFactGraphRelation:
            db.execute("BEGIN")
            self._self_permission(db, actor, scope, "graph.review")
            item = _self_relation(self._row(db, "relation", scope, relation_id))
            self._current_source(db, actor, scope, item.source)
            return item
        return await self.store.transaction(read)

    async def review_self_fact_relation(
        self, relation_id: str, *, actor: str, scope: Scope, expected_revision: int,
        decision: Literal["approved", "rejected"], self_non_sensitive_fact: bool = False,
        non_personal_target: bool = False,
    ) -> SelfFactGraphRelation:
        if decision not in {"approved", "rejected"}:
            raise OperationError("invalid_graph_review")
        if decision == "approved" and (
            self_non_sensitive_fact is not True or non_personal_target is not True
        ):
            raise OperationError("graph_self_fact_confirmation_required")
        return await self._mutate_self_fact(
            relation_id, actor=actor, scope=scope, expected_revision=expected_revision,
            operation="review", decision=decision,
        )

    async def apply_self_fact_relation(
        self, relation_id: str, *, actor: str, scope: Scope, expected_revision: int,
    ) -> SelfFactGraphRelation:
        return await self._mutate_self_fact(
            relation_id, actor=actor, scope=scope, expected_revision=expected_revision, operation="apply",
        )

    async def revoke_self_fact_relation(
        self, relation_id: str, *, actor: str, scope: Scope, expected_revision: int,
    ) -> SelfFactGraphRelation:
        return await self._mutate_self_fact(
            relation_id, actor=actor, scope=scope, expected_revision=expected_revision, operation="remove",
        )

    async def _mutate_self_fact(
        self, relation_id: str, *, actor: str, scope: Scope, expected_revision: int,
        operation: Literal["review", "apply", "remove"], decision: str = "",
    ) -> SelfFactGraphRelation:
        relation_id = _identifier(relation_id)
        def commit(db: StoreConnection) -> SelfFactGraphRelation:
            db.execute("BEGIN IMMEDIATE")
            policy_revision = self._self_permission(db, actor, scope, "graph." + operation)
            row = self._row(db, "relation", scope, relation_id)
            item = _self_relation(row)
            self._expected(row, expected_revision)
            if item.status == "revoked":
                raise OperationError("graph_object_revoked")
            if operation != "remove":
                self._current_source(db, actor, scope, item.source)
            if operation == "apply" and (item.review_status != "approved" or not row["review_actor"]):
                raise OperationError("graph_review_required")
            status = "inactive" if operation == "review" else "active" if operation == "apply" else "revoked"
            review = decision if operation == "review" else item.review_status
            db.execute(
                "UPDATE graph_relations SET revision=revision+1,review_status=?,status=?,"
                "review_actor=CASE WHEN ?='review' THEN ? ELSE review_actor END,"
                "apply_actor=CASE WHEN ?='apply' THEN ? ELSE apply_actor END,policy_revision=?,updated_at=? "
                "WHERE bot_id=? AND group_id=? AND object_id=?",
                (review, status, operation, actor, operation, actor, policy_revision, time.time(),
                 scope.bot_id, scope.group_id, relation_id),
            )
            current = self._row(db, "relation", scope, relation_id)
            code = "self_fact_" + operation + (":" + decision if decision else "")
            if operation == "review" and decision == "approved":
                code += ":self_non_sensitive_fact:non_personal_target"
            self._audit(db, actor, current, code)
            return _self_relation(current)
        return await self.store.transaction(commit)

    async def propose_relation(
        self,
        value: RelationInput,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int = 0,
    ) -> GraphRelation:
        value = RelationInput.model_validate(value.model_dump())
        row = await self._propose(
            "relation",
            value,
            actor=actor,
            scope=scope,
            expected_revision=expected_revision,
        )
        return _relation(row)

    async def propose_alias(
        self,
        value: AliasInput,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int = 0,
    ) -> GraphAlias:
        value = AliasInput.model_validate(value.model_dump())
        row = await self._propose(
            "alias",
            value,
            actor=actor,
            scope=scope,
            expected_revision=expected_revision,
        )
        return _alias(row)

    async def propose_relations(
        self, values: Sequence[RelationInput], *, actor: str, scope: Scope,
        expected_revisions: Sequence[int] | None = None,
        preflight_transaction: Callable[[StoreConnection], None] | None = None,
    ) -> tuple[GraphRelation, ...]:
        """Commit at most two pending candidates together; review/apply remains manual."""
        if not 1 <= len(values) <= 2:
            raise OperationError("invalid_graph_batch")
        frozen = tuple(RelationInput.model_validate(value.model_dump()) for value in values)
        revisions = tuple(expected_revisions) if expected_revisions is not None else (0,) * len(frozen)
        if len(revisions) != len(frozen):
            raise OperationError("invalid_graph_batch")

        def commit(db: StoreConnection) -> tuple[GraphRelation, ...]:
            db.execute("BEGIN IMMEDIATE")
            if preflight_transaction is not None:
                preflight_transaction(db)
            return tuple(_relation(self._propose_transaction(
                db, "relation", value, actor=actor, scope=scope, expected_revision=revision,
            )) for value, revision in zip(frozen, revisions, strict=True))

        return await self.store.transaction(commit)

    async def _propose(
        self, kind: GraphKind, value: RelationInput | AliasInput, *,
        actor: str, scope: Scope, expected_revision: int,
    ) -> StoreRow:
        def commit(db: StoreConnection) -> StoreRow:
            db.execute("BEGIN IMMEDIATE")
            return self._propose_transaction(
                db, kind, value, actor=actor, scope=scope, expected_revision=expected_revision,
            )

        return await self.store.transaction(commit)

    def _propose_transaction(
        self, db: StoreConnection, kind: GraphKind, value: RelationInput | AliasInput, *,
        actor: str, scope: Scope, expected_revision: int,
    ) -> StoreRow:
        payload = value.model_dump_json()
        digest = hashlib.sha256(payload.encode()).hexdigest()
        object_id = value.relation_id if isinstance(value, RelationInput) else value.alias_id
        revision = self._permission(db, actor, scope, "graph.learn")
        self._current_source(db, actor, scope, value.source)
        old = db.execute(
            f"SELECT * FROM {_TABLES[kind]} WHERE bot_id=? AND group_id=? AND object_id=?",
            (scope.bot_id, scope.group_id, object_id),
        ).fetchone()
        self._expected(old, expected_revision)
        if old is not None:
            if old["payload_digest"] != digest:
                raise OperationError("graph_payload_conflict")
            return old
        now = time.time()
        common = (
            scope.bot_id,
            scope.group_id,
            object_id,
            value.source.model_dump_json(),
            digest,
            revision,
            now,
            now,
        )
        if isinstance(value, RelationInput):
            entities = (value.subject_id, value.target_id)
            db.execute(
                "INSERT INTO graph_relations(bot_id,group_id,object_id,subject_id,predicate,target_id,"
                "pointer_json,payload_digest,policy_revision,created_at,updated_at,"
                "revision,review_status,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'pending','inactive')",
                (*common[:3], value.subject_id, value.predicate, value.target_id, *common[3:]),
            )
        else:
            entities = (value.entity_id,)
            db.execute(
                "INSERT INTO graph_aliases(bot_id,group_id,object_id,entity_id,alias_surface,alias_norm,"
                "pointer_json,payload_digest,policy_revision,created_at,updated_at,"
                "revision,review_status,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'pending','inactive')",
                (*common[:3], value.entity_id, value.alias, _norm(value.alias), *common[3:]),
            )
        for entity_id in entities:
            existing = db.execute(
                "SELECT kind FROM graph_entities WHERE bot_id=? AND group_id=? AND entity_id=?",
                (scope.bot_id, scope.group_id, entity_id),
            ).fetchone()
            if existing is not None and existing[0] != "concept":
                raise OperationError("graph_entity_kind_conflict")
            db.execute(
                "INSERT INTO graph_entities VALUES (?,?,?,'concept') ON CONFLICT DO NOTHING",
                (scope.bot_id, scope.group_id, entity_id),
            )
        row = self._row(db, kind, scope, object_id)
        self._audit(db, actor, row, "proposed")
        return row

    async def review(
        self,
        kind: GraphKind,
        object_id: str,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int,
        decision: Literal["approved", "rejected"],
    ) -> GraphRelation | GraphAlias:
        if decision not in {"approved", "rejected"}:
            raise OperationError("invalid_graph_review")
        return await self._mutate(
            kind,
            object_id,
            actor=actor,
            scope=scope,
            expected_revision=expected_revision,
            operation="review",
            decision=decision,
        )

    async def apply(
        self,
        kind: GraphKind,
        object_id: str,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int,
    ) -> GraphRelation | GraphAlias:
        return await self._mutate(
            kind, object_id, actor=actor, scope=scope, expected_revision=expected_revision, operation="apply"
        )

    async def revoke(
        self,
        kind: GraphKind,
        object_id: str,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int,
    ) -> GraphRelation | GraphAlias:
        return await self._mutate(
            kind, object_id, actor=actor, scope=scope, expected_revision=expected_revision, operation="remove"
        )

    async def _mutate(
        self,
        kind: GraphKind,
        object_id: str,
        *,
        actor: str,
        scope: Scope,
        expected_revision: int,
        operation: Literal["review", "apply", "remove"],
        decision: str = "",
    ) -> GraphRelation | GraphAlias:
        if kind not in _TABLES:
            raise OperationError("invalid_graph_kind")
        object_id = _identifier(object_id)

        def commit(db: StoreConnection) -> StoreRow:
            db.execute("BEGIN IMMEDIATE")
            policy_revision = self._permission(db, actor, scope, "graph." + operation)
            row = self._row(db, kind, scope, object_id)
            if isinstance(_source(row), SelfFactSource):
                raise OperationError("graph_self_fact_api_required")
            self._expected(row, expected_revision)
            if operation != "remove":
                self._current_source(db, actor, scope, _source(row))
            if row["status"] == "revoked":
                raise OperationError("graph_object_revoked")
            status = "inactive" if operation == "review" else "active" if operation == "apply" else "revoked"
            review = decision if operation == "review" else str(row["review_status"])
            if operation == "apply":
                if review != "approved":
                    raise OperationError("graph_review_required")
                if kind == "alias":
                    conflicts = db.execute(
                        "SELECT * FROM graph_aliases WHERE bot_id=? AND group_id=? AND alias_norm=? "
                        "AND status IN ('active','ambiguous') AND entity_id<>? AND object_id<>?",
                        (scope.bot_id, scope.group_id, row["alias_norm"], row["entity_id"], object_id),
                    ).fetchall()
                    for conflict in conflicts:
                        try:
                            self._current_source(db, actor, scope, _source(conflict))
                        except OperationError as exc:
                            if exc.code in _STALE_SOURCE_CODES:
                                continue
                            raise
                        status = "ambiguous"
                        db.execute(
                            "UPDATE graph_aliases SET status='ambiguous',revision=revision+1,updated_at=? "
                            "WHERE bot_id=? AND group_id=? AND object_id=?",
                            (time.time(), scope.bot_id, scope.group_id, conflict["object_id"]),
                        )
                        self._audit(
                            db,
                            actor,
                            self._row(db, kind, scope, str(conflict["object_id"])),
                            "alias_ambiguous",
                        )
            db.execute(
                f"UPDATE {_TABLES[kind]} SET revision=revision+1,review_status=?,status=?,"
                "review_actor=CASE WHEN ?='review' THEN ? ELSE review_actor END,"
                "apply_actor=CASE WHEN ?='apply' THEN ? ELSE apply_actor END,policy_revision=?,updated_at=? "
                "WHERE bot_id=? AND group_id=? AND object_id=?",
                (
                    review,
                    status,
                    operation,
                    actor,
                    operation,
                    actor,
                    policy_revision,
                    time.time(),
                    scope.bot_id,
                    scope.group_id,
                    object_id,
                ),
            )
            current = self._row(db, kind, scope, object_id)
            self._audit(db, actor, current, operation + (":" + decision if decision else ""))
            return current

        row = await self.store.transaction(commit)
        return _relation(row) if kind == "relation" else _alias(row)

    async def list_objects(
        self, kind: GraphKind, *, actor: str, scope: Scope, limit: int = 32, after: str | None = None,
    ) -> GraphObjectPage:
        """Governance metadata remains readable when its primary source expires."""
        if kind not in _TABLES or type(limit) is not int or not 1 <= limit <= 64:
            raise OperationError("invalid_graph_page")
        if after is not None:
            after = _identifier(after)

        def read(db: StoreConnection) -> GraphObjectPage:
            self._permission(db, actor, scope, "graph.review")
            rows = db.execute(
                f"SELECT * FROM {_TABLES[kind]} WHERE bot_id=? AND group_id=? "
                "AND json_extract(pointer_json,'$.kind') IS NULL "
                "AND (? IS NULL OR object_id>?) ORDER BY object_id LIMIT ?",
                (scope.bot_id, scope.group_id, after, after, limit + 1),
            ).fetchall()
            convert = _relation if kind == "relation" else _alias
            return GraphObjectPage(
                tuple(convert(row) for row in rows[:limit]),
                str(rows[limit - 1]["object_id"]) if len(rows) > limit else None,
            )

        return await self.store.transaction(read)

    async def health_snapshot(self, *, actor: str, scope: Scope) -> GraphHealthSnapshot:
        """Inspect bounded current sources only on an explicit governance request.

        No population, repair or writes run here. Source checks use the same
        transaction as the scope/actor permission and object reads.
        """
        if type(scope) is not Scope:
            raise OperationError("unsupported_scope")

        def read(db: StoreConnection) -> GraphHealthSnapshot:
            db.execute("BEGIN")
            self._permission(db, actor, scope, "graph.review")
            rows = db.execute(
                "SELECT 'alias' AS kind,object_id,review_status,status,pointer_json FROM graph_aliases "
                "WHERE bot_id=? AND group_id=? UNION ALL "
                "SELECT 'relation' AS kind,object_id,review_status,status,pointer_json FROM graph_relations "
                "WHERE bot_id=? AND group_id=? ORDER BY kind,object_id LIMIT 2049",
                (scope.bot_id, scope.group_id, scope.bot_id, scope.group_id),
            ).fetchall()
            counts: dict[GraphKind, dict[str, int]] = {
                kind: {field: 0 for field in GraphHealthCounts.model_fields} for kind in _TABLES
            }
            for row in rows[:2048]:
                values = counts[cast(GraphKind, row["kind"])]
                values["scanned"] += 1
                values[str(row["review_status"])] += 1
                values[str(row["status"])] += 1
                quality = "valid"
                try:
                    self._current_source(db, actor, scope, _source(row))
                except OperationError as exc:
                    if exc.code not in _STALE_SOURCE_CODES:
                        raise
                    quality = "invalid"
                values["current_primary_" + quality] += 1
                if row["status"] == "active":
                    values["active_primary_" + quality] += 1
            return GraphHealthSnapshot(
                state="partial" if len(rows) > 2048 else "complete",
                relations=GraphHealthCounts(**counts["relation"]),
                aliases=GraphHealthCounts(**counts["alias"]),
            )

        return await self.store.transaction(read)

    async def resolve_alias(self, alias: str, *, actor: str, scope: Scope) -> AliasResolution:
        if not alias.strip() or len(alias) > 128:
            raise OperationError("invalid_graph_alias")

        def read(db: StoreConnection) -> AliasResolution:
            self._permission(db, actor, scope, "graph.retrieve")
            rows = db.execute(
                "SELECT * FROM graph_aliases WHERE bot_id=? AND group_id=? AND alias_norm=? "
                "AND review_status='approved' AND status IN ('active','ambiguous') "
                "ORDER BY object_id LIMIT 33",
                (scope.bot_id, scope.group_id, _norm(alias)),
            ).fetchall()
            aliases: list[GraphAlias] = []
            for row in rows[:32]:
                try:
                    self._current_source(db, actor, scope, _source(row))
                except OperationError as exc:
                    if exc.code in _STALE_SOURCE_CODES:
                        continue
                    raise
                aliases.append(_alias(row))
            entities = {item.entity_id for item in aliases}
            ambiguous = len(rows) > 32 or len(entities) > 1 or any(a.status == "ambiguous" for a in aliases)
            state = "ambiguous" if ambiguous else "resolved" if entities else "missing"
            return AliasResolution(
                scope, actor, state, next(iter(entities)) if state == "resolved" else None, tuple(aliases)
            )

        return await self.store.transaction(read)

    async def neighbors(self, entity_id: str, *, actor: str, scope: Scope, limit: int = 8) -> GraphProjection:
        return await self.walk((entity_id,), actor=actor, scope=scope, max_depth=1, limit=limit)

    async def query_projection(
        self, query: str, *, actor: str, scope: Scope, limit: int = 8,
    ) -> GraphProjection | None:
        """Exact scoped alias/identifier lookup; never enumerate a graph population.

        At most 32 lexical candidates yield four roots. Natural language entity
        extraction and personal names are deliberately outside this owner.
        """
        if type(query) is not str or len(query) > 4096:
            raise OperationError("invalid_graph_query")
        if type(limit) is not int or not 1 <= limit <= 8:
            raise OperationError("invalid_graph_budget")
        text = query.strip()
        candidates = tuple(dict.fromkeys((
            *((text,) if text and len(text) <= 128 else ()),
            *(match.group(0) for match in re.finditer(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}", text)),
        )))[:32]

        def read(db: StoreConnection) -> tuple[tuple[str, ...], tuple[GraphAlias, ...]]:
            self._permission(db, actor, scope, "graph.retrieve")
            roots: list[str] = []
            aliases: list[GraphAlias] = []
            for candidate in candidates:
                rows = db.execute(
                    "SELECT * FROM graph_aliases WHERE bot_id=? AND group_id=? AND alias_norm=? "
                    "AND review_status='approved' AND status IN ('active','ambiguous') "
                    "ORDER BY object_id LIMIT 33",
                    (scope.bot_id, scope.group_id, _norm(candidate)),
                ).fetchall()
                qualified: list[GraphAlias] = []
                for row in rows[:32]:
                    try:
                        self._current_source(db, actor, scope, _source(row))
                    except OperationError as exc:
                        if exc.code in _STALE_SOURCE_CODES:
                            continue
                        raise
                    qualified.append(_alias(row))
                entities = {item.entity_id for item in qualified}
                if len(rows) > 32 or len(entities) > 1 or any(a.status == "ambiguous" for a in qualified):
                    continue
                if qualified:
                    entity = qualified[0].entity_id
                    aliases.append(qualified[0])
                elif re.fullmatch(_IDENTIFIER, candidate):
                    # Exact concept key only. This index lookup does not expose
                    # unrelated objects or infer a person from a display name.
                    row = db.execute(
                        "SELECT * FROM graph_relations WHERE bot_id=? AND group_id=? AND subject_id=? "
                        "AND review_status='approved' AND status='active' ORDER BY object_id LIMIT 1",
                        (scope.bot_id, scope.group_id, candidate),
                    ).fetchone()
                    if row is None or isinstance(_source(row), SelfFactSource):
                        continue
                    try:
                        self._current_source(db, actor, scope, _source(row))
                    except OperationError as exc:
                        if exc.code in _STALE_SOURCE_CODES:
                            continue
                        raise
                    entity = candidate
                else:
                    continue
                if entity not in roots:
                    roots.append(entity)
                if len(roots) == 4:
                    break
            return tuple(roots), tuple({item.alias_id: item for item in aliases}.values())

        roots, aliases = await self.store.transaction(read)
        if not roots:
            return None
        projection = replace(
            await self.walk(roots, actor=actor, scope=scope, limit=limit), aliases=aliases,
        )
        await self.store.transaction(
            lambda db: self.assert_projection_transaction(db, actor=actor, scope=scope, frozen=projection)
        )
        return projection

    async def walk(
        self,
        seeds: Sequence[str],
        *,
        actor: str,
        scope: Scope,
        max_depth: int = 2,
        max_nodes: int = 32,
        max_edges: int = 64,
        deadline_ms: int = 50,
        limit: int = 8,
        alias_resolution: AliasResolution | None = None,
    ) -> GraphProjection:
        for value, bound in ((max_depth, 2), (max_nodes, 32), (max_edges, 64), (deadline_ms, 50), (limit, 8)):
            if type(value) is not int or not 1 <= value <= bound:
                raise OperationError("invalid_graph_budget")
        roots = tuple(dict.fromkeys(_identifier(seed) for seed in seeds))
        if not roots or len(roots) > max_nodes:
            raise OperationError("invalid_graph_seeds")

        def read(db: StoreConnection) -> GraphProjection:
            self._permission(db, actor, scope, "graph.retrieve")
            aliases = () if alias_resolution is None else alias_resolution.aliases
            if alias_resolution is not None:
                if (
                    alias_resolution.scope != scope
                    or alias_resolution.reader_id != actor
                    or alias_resolution.state != "resolved"
                    or alias_resolution.entity_id not in roots
                ):
                    raise OperationError("stale_graph_projection")
                self._assert_aliases(db, actor, scope, aliases)
            deadline = self.clock() + deadline_ms / 1000
            queue: deque[tuple[str, int, tuple[str, ...]]] = deque((seed, 0, ()) for seed in roots)
            visited = set(roots)
            retained: list[GraphRelation] = []
            paths: list[tuple[str, ...]] = []
            examined = 0
            stop: StopReason = "exhausted"
            while queue:
                if self.clock() >= deadline:
                    stop = "time_budget"
                    break
                entity, depth, path = queue.popleft()
                if depth == max_depth:
                    stop = "depth"
                    continue
                rows = db.execute(
                    "SELECT * FROM graph_relations WHERE bot_id=? AND group_id=? AND subject_id=? "
                    "AND review_status='approved' AND status='active' ORDER BY object_id LIMIT ?",
                    (scope.bot_id, scope.group_id, entity, max_edges - examined + 1),
                ).fetchall()
                for row in rows:
                    if self.clock() >= deadline:
                        stop = "time_budget"
                        break
                    if examined >= max_edges:
                        stop = "edge_budget"
                        break
                    examined += 1
                    if isinstance(_source(row), SelfFactSource):
                        continue
                    try:
                        self._current_source(db, actor, scope, _source(row))
                    except OperationError as exc:
                        if exc.code in _STALE_SOURCE_CODES:
                            continue
                        raise
                    item = _relation(row)
                    if item.target_id not in visited and len(visited) >= max_nodes:
                        stop = "node_budget"
                        break
                    if len(retained) >= limit:
                        stop = "result_budget"
                        break
                    retained.append(item)
                    next_path = (*path, item.relation_id)
                    paths.append(next_path)
                    if item.target_id not in visited:
                        visited.add(item.target_id)
                        queue.append((item.target_id, depth + 1, next_path))
                if stop in {"time_budget", "node_budget", "edge_budget", "result_budget"}:
                    break
            return GraphProjection(
                scope, actor, tuple(retained), tuple(paths), aliases, stop, len(visited), examined
            )

        return await self.store.transaction(read)

    async def project_self_facts(
        self, pointers: Sequence[MemoryFactPointer], *, actor: str, scope: Scope, limit: int = 8,
    ) -> GraphProjection:
        """Project only exact caller-selected primary facts; never search a person population."""
        if type(limit) is not int or not 1 <= limit <= 8 or not 1 <= len(pointers) <= 8:
            raise OperationError("invalid_graph_budget")
        frozen = tuple({pointer.model_dump_json(): MemoryFactPointer.model_validate(pointer.model_dump())
                        for pointer in pointers}.values())

        def read(db: StoreConnection) -> GraphProjection:
            db.execute("BEGIN")
            self._self_permission(db, actor, scope, "graph.retrieve")
            retained: list[SelfFactGraphRelation] = []
            examined = 0
            stop: StopReason = "exhausted"
            for pointer in frozen:
                self._memory().assert_fact_pointer_transaction(db, actor=actor, scope=scope, pointer=pointer)
                rows = db.execute(
                    "SELECT * FROM graph_relations WHERE bot_id=? AND group_id=? AND subject_id=? "
                    "AND review_status='approved' AND status='active' "
                    "AND json_extract(pointer_json,'$.kind')='self_fact_relation' "
                    "AND json_extract(pointer_json,'$.fact.fact_id')=? "
                    "AND json_extract(pointer_json,'$.fact.fact_revision')=? ORDER BY object_id LIMIT ?",
                    (scope.bot_id, scope.group_id, pointer.self_entity_id, pointer.fact_id,
                     pointer.fact_revision, limit - len(retained) + 1),
                ).fetchall()
                for row in rows:
                    examined += 1
                    item = _self_relation(row)
                    if item.source.fact != pointer:
                        continue
                    if len(retained) == limit:
                        stop = "result_budget"
                        break
                    retained.append(item)
                if stop == "result_budget":
                    break
            nodes = {node for item in retained for node in (item.subject_id, item.target_id)}
            return GraphProjection(scope, actor, tuple(retained),
                                   tuple((item.relation_id,) for item in retained), (), stop,
                                   len(nodes), examined)
        return await self.store.transaction(read)

    def _assert_aliases(
        self,
        db: StoreConnection,
        actor: str,
        scope: Scope,
        aliases: Sequence[GraphAlias],
    ) -> None:
        for item in aliases:
            current = _alias(self._row(db, "alias", scope, item.alias_id))
            if current != item or item.status != "active":
                raise OperationError("stale_graph_projection")
            self._current_source(db, actor, scope, item.source)

    def assert_projection_transaction(
        self,
        db: StoreConnection,
        *,
        actor: str,
        scope: Scope,
        frozen: GraphProjection,
    ) -> None:
        if any(isinstance(item, SelfFactGraphRelation) for item in frozen.relations):
            self._self_permission(db, actor, scope, "graph.retrieve")
        if frozen.aliases or any(isinstance(item, GraphRelation) for item in frozen.relations):
            self._permission(db, actor, scope, "graph.retrieve")
        if not frozen.relations and not frozen.aliases:
            self._permission(db, actor, scope, "graph.retrieve")
        if frozen.scope != scope or frozen.reader_id != actor:
            raise OperationError("stale_graph_projection")
        self._assert_aliases(db, actor, scope, frozen.aliases)
        for item in frozen.relations:
            row = self._row(db, "relation", scope, item.relation_id)
            current = _self_relation(row) if isinstance(item, SelfFactGraphRelation) else _relation(row)
            if current != item or item.status != "active" or item.review_status != "approved":
                raise OperationError("stale_graph_projection")
            self._current_source(db, actor, scope, item.source)
