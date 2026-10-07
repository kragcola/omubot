"""Same-group derived interaction familiarity; no nickname or social reward truth."""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .archive import ArchiveHumanSourcePointer, ArchiveService
from .policy import Policy
from .store import Store, StoreConnection, StoreRow, request_digest
from .types import Event, OperationError, Scope, SendReceipt

_MAX_SCOPE_CONTRIBUTIONS = 2048
_CURRENT_SOURCE_ERRORS = frozenset({"denied", "source_revoked", "archive_source_not_found",
    "archive_source_unavailable", "archive_source_changed", "archive_source_expired"})


def _identity(value: str) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > 128:
        raise OperationError("invalid_affection_identity")
    if any(ord(char) < 32 for char in value):
        raise OperationError("invalid_affection_identity")
    return value


@dataclass(frozen=True, slots=True)
class AffectionContribution:
    source: ArchiveHumanSourcePointer
    source_digest: str
    action_id: str
    receipt_id: str
    day: str
    revision: int


@dataclass(frozen=True, slots=True)
class AffectionProjection:
    scope: Scope
    subject_id: str
    revision: int
    score: float
    tier: str
    contributions: tuple[AffectionContribution, ...]
    adjustment: AffectionAdjustment | None = None


@dataclass(frozen=True, slots=True)
class AffectionAdjustment:
    revision: int
    requested_score: float
    offset: float
    actor: str
    operation_id: str


class AffectionService:
    """A bounded body-free ledger, deriving current views under source consent."""

    def __init__(self, store: Store, policy: Policy, archive: ArchiveService,
                 *, clock: Callable[[], float] = time.time) -> None:
        if archive.store is not store or archive.policy is not policy:
            raise OperationError("affection_owner_mismatch")
        self.store, self.policy, self.archive = store, policy, archive
        self._clock = clock

    def _scope(self, scope: Scope, subject_id: str) -> None:
        _identity(subject_id)
        if type(scope) is not Scope or scope.bot_id != self.policy.bot_id:
            raise OperationError("denied")
        if subject_id == self.policy.bot_id:
            raise OperationError("affection_person_required")

    def _source(self, db: StoreConnection, actor: str, scope: Scope,
                subject_id: str, event_id: str, source_id: str,
                source_revision: int) -> ArchiveHumanSourcePointer:
        return self.archive.assert_human_source_transaction(
            db, actor=actor, scope=scope, source_id=source_id,
            expected_revision=source_revision, subject_id=subject_id, event_id=event_id)

    def _contribution(self, db: StoreConnection, row: StoreRow, actor: str,
                      scope: Scope) -> AffectionContribution:
        pointer = self._source(db, actor, scope, str(row["subject_id"]), str(row["event_id"]),
                               str(row["source_id"]), int(row["source_revision"]))
        self.store.assert_successful_reply_transaction(
            db, bot_id=scope.bot_id, group_id=scope.group_id, subject_id=pointer.subject_id,
            event_id=pointer.event_id, source_digest=str(row["source_digest"]),
            action_id=str(row["action_id"]), receipt_id=str(row["receipt_id"]))
        return AffectionContribution(
            pointer, str(row["source_digest"]), str(row["action_id"]), str(row["receipt_id"]),
            str(row["day"]), int(row["revision"]))

    def _projection(self, db: StoreConnection, actor: str, scope: Scope,
                    subject_id: str) -> AffectionProjection:
        self.policy.check_transaction(db, actor, scope, "memory.retrieve", "", "", False, False)
        revision_row = db.execute("SELECT revision FROM affection_relations "
            "WHERE bot_id=? AND group_id=? AND subject_id=?",
            (scope.bot_id, scope.group_id, subject_id)).fetchone()
        rows = db.execute("SELECT * FROM affection_contributions "
            "WHERE bot_id=? AND group_id=? AND subject_id=? ORDER BY created_at,event_id LIMIT ?",
            (scope.bot_id, scope.group_id, subject_id, _MAX_SCOPE_CONTRIBUTIONS+1)).fetchall()
        if len(rows) > _MAX_SCOPE_CONTRIBUTIONS:
            raise OperationError("affection_scope_budget")
        days: Counter[str] = Counter()
        valid: list[AffectionContribution] = []
        for row in rows:
            try:
                contribution = self._contribution(db, row, actor, scope)
            except OperationError as exc:
                if exc.code in _CURRENT_SOURCE_ERRORS:
                    continue
                raise
            if days[contribution.day] >= 12:
                continue
            days[contribution.day] += 1
            valid.append(contribution)
        row = db.execute("SELECT * FROM affection_adjustments WHERE bot_id=? AND group_id=? "
                         "AND subject_id=?", (scope.bot_id, scope.group_id, subject_id)).fetchone()
        adjustment = (AffectionAdjustment(int(row["revision"]), float(row["requested_score"]),
            float(row["offset"]), str(row["actor"]), str(row["operation_id"])) if row else None)
        derived_score = min(100.0, round(len(valid)*0.8, 1))
        score = min(100.0, max(0.0, round(
            derived_score + (adjustment.offset if adjustment is not None else 0.0), 1)))
        tiers = ("陌生人", "熟人", "朋友", "好朋友", "重要的人")
        return AffectionProjection(scope, subject_id, int(revision_row[0]) if revision_row else 0,
                                   score, tiers[min(int(score//20), 4)], tuple(valid), adjustment)

    async def adjust_current(self, *, actor: str, scope: Scope, subject_id: str,
                             score: float, expected_revision: int,
                             operation_id: str) -> AffectionProjection:
        actor, operation_id = map(_identity, (actor, operation_id))
        self._scope(scope, subject_id)
        if (isinstance(score, bool) or not math.isfinite(score) or not 0 <= score <= 100
                or type(expected_revision) is not int or expected_revision < 0):
            raise OperationError("invalid_affection_adjustment")
        score = round(score, 1)
        digest = hashlib.sha256(json.dumps(
            [scope.key, actor, subject_id, score, expected_revision],
            separators=(",", ":")).encode()).hexdigest()

        def write(db: StoreConnection) -> AffectionProjection:
            policy_revision = self.policy.check_transaction(
                db, actor, scope, "affection.manage", "", "", False)
            current = self._projection(db, actor, scope, subject_id)
            previous = db.execute("SELECT details FROM audit WHERE kind='affection' AND identity=? "
                "AND code='admin_score_adjusted' ORDER BY id DESC LIMIT 1", (operation_id,)).fetchone()
            if previous is not None:
                if json.loads(previous["details"])["digest"] != digest:
                    raise OperationError("idempotency_conflict")
                return current
            if current.revision != expected_revision:
                raise OperationError("revision_conflict")
            derived = min(100.0, round(len(current.contributions)*0.8, 1))
            offset, revision = round(score-derived, 1), current.revision+1
            db.execute("INSERT INTO affection_relations VALUES (?,?,?,?) "
                "ON CONFLICT(bot_id,group_id,subject_id) DO UPDATE SET revision=excluded.revision",
                (scope.bot_id, scope.group_id, subject_id, revision))
            db.execute("INSERT INTO affection_adjustments VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(bot_id,group_id,subject_id) DO UPDATE SET "
                "revision=excluded.revision,offset=excluded.offset,requested_score=excluded.requested_score,"
                "actor=excluded.actor,operation_id=excluded.operation_id,digest=excluded.digest,"
                "policy_revision=excluded.policy_revision,updated_at=excluded.updated_at",
                (scope.bot_id, scope.group_id, subject_id, revision, offset, score,
                 actor, operation_id, digest, policy_revision, self._clock()))
            evidence = {
                "bot_id": scope.bot_id, "group_id": scope.group_id, "subject_id": subject_id,
                "actor": actor, "requested_score": score, "offset": offset,
                "policy_revision": policy_revision,
                "digest": digest,
            }
            db.execute("INSERT INTO audit(kind,identity,revision,code,details) VALUES (?,?,?,?,?)",
                       ("affection", operation_id, revision, "admin_score_adjusted",
                        json.dumps(evidence, separators=(",", ":"), sort_keys=True)))
            return self._projection(db, actor, scope, subject_id)

        return await self.store.transaction(write)

    async def record_successful_reply(
        self, *, actor: str, event: Event,
        source_id: str, source_revision: int, action_id: str, receipt: SendReceipt,
        expected_revision: int,
    ) -> AffectionContribution:
        scope = event.scope
        if not isinstance(scope, Scope):
            raise OperationError("unsupported_scope")
        subject_id = event.user_id
        actor, event_id, action_id = map(_identity, (actor, event.event_id, action_id))
        self._scope(scope, subject_id)
        source_digest = request_digest(event)
        _identity(receipt.message_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise OperationError("invalid_affection_revision")
        now = self._clock()
        if not math.isfinite(now):
            raise OperationError("invalid_affection_clock")
        day = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).date().isoformat()

        def write(db: StoreConnection) -> AffectionContribution:
            pointer = self._source(db, actor, scope, subject_id, event_id, source_id, source_revision)
            self.store.assert_successful_reply_transaction(db, bot_id=scope.bot_id,
                group_id=scope.group_id, subject_id=subject_id, event_id=event_id,
                source_digest=source_digest, action_id=action_id, receipt_id=receipt.message_id)
            policy_revision = self.policy.check_transaction(
                db, actor, scope, "memory.learn", "", "", False, False)
            existing = db.execute("SELECT * FROM affection_contributions WHERE bot_id=? "
                "AND group_id=? AND subject_id=? AND event_id=?",
                (scope.bot_id, scope.group_id, subject_id, event_id)).fetchone()
            if existing is not None:
                if (existing["source_id"] != source_id or existing["source_revision"] != source_revision
                        or existing["source_digest"] != source_digest):
                    raise OperationError("affection_contribution_conflict")
                return self._contribution(db, existing, actor, scope)
            count = db.execute("SELECT count(*) FROM affection_contributions WHERE bot_id=? AND group_id=?",
                               (scope.bot_id, scope.group_id)).fetchone()[0]
            if count >= _MAX_SCOPE_CONTRIBUTIONS:
                raise OperationError("affection_scope_budget")
            prior = db.execute("SELECT revision FROM affection_relations WHERE bot_id=? AND group_id=? "
                               "AND subject_id=?", (scope.bot_id, scope.group_id, subject_id)).fetchone()
            revision = int(prior[0]) if prior else 0
            if revision != expected_revision:
                raise OperationError("revision_conflict")
            if db.execute(
                "SELECT 1 FROM affection_contributions WHERE bot_id=? AND group_id=? AND action_id=?",
                (scope.bot_id, scope.group_id, action_id),
            ).fetchone():
                raise OperationError("affection_contribution_conflict")
            revision += 1
            db.execute("INSERT INTO affection_relations VALUES (?,?,?,?) "
                "ON CONFLICT(bot_id,group_id,subject_id) DO UPDATE SET revision=excluded.revision",
                (scope.bot_id, scope.group_id, subject_id, revision))
            db.execute("INSERT INTO affection_contributions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (scope.bot_id, scope.group_id, subject_id, event_id, source_id, source_revision,
                 source_digest, action_id, receipt.message_id, day, revision, actor, policy_revision, now))
            return AffectionContribution(pointer, source_digest, action_id, receipt.message_id, day, revision)

        return await self.store.transaction(write)

    async def read_current(self, *, actor: str, scope: Scope, subject_id: str) -> AffectionProjection:
        _identity(actor)
        self._scope(scope, subject_id)
        return await self.store.transaction(lambda db: self._projection(db, actor, scope, subject_id))

    def assert_projection_transaction(self, db: StoreConnection, *, actor: str, scope: Scope,
                                      projection: AffectionProjection) -> None:
        if projection.scope != scope or self._projection(
            db, actor, scope, projection.subject_id) != projection:
            raise OperationError("stale_affection_projection")
